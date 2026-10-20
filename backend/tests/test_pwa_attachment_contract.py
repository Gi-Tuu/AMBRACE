# -*- coding: utf-8 -*-
"""PWA M1a-4 文本附件 · **真实文件路由契约**（沙箱集成，2026-10-17）。

契约权威＝``backend/app/api/chat.py::upload_chat_file``：
- ``POST /api/v1/chat/sessions/{sid}/file``，multipart 字段名 **``file``**，
  语言头 **``lang``**（**不是** X-Lang；本单如实登记这一既有口径，不顺手改生产源码）；
- 需身份 + owned-session 校验（非本人会话一律 404）；
- 先 ``save_file`` → 读文本前 8192 字节 UTF-8 解码截 6000 字符作摘要 →
  持久化 user 文件消息（content=原文件名，extra_meta.file={name,url,size,type,summary}）→
  追加事件 → ``send_and_receive_chunked(...)`` 一次 → 返回普通 JSON ``{file_message, chunks}``。

口径（对本单验收 §7「真实后端路由＋临时库/临时文件」）：
- 只用 conftest 沙箱库 + pytest ``tmp_path``；**猴补** ``app.api.chat.UPLOAD_DIR`` 与
  ``app.application.upload_service.UPLOAD_DIR`` 到 ``tmp_path``（conftest 的 DB 隔离
  **不**自动隔离上传目录）；实际落盘路径必须在本例 tmp_path 内；
- 把路由实际调用的 ``send_and_receive_chunked`` 换成**可记录参数/返回合成 chunks** 的桩；
  LLM/TTS/embedding 入口钉成「调用即失败」并断言 0 真实调用；
- 返回合成 chunks ≠ 真实 AI 生成，断言只覆盖「路由契约」，不冒充真实 AI 落库。
"""
from __future__ import annotations

import asyncio
import itertools
import json
from datetime import datetime

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import select, func
from starlette.testclient import TestClient

from app.api.chat import router as chat_router
from app.auth.router import router as auth_router
from app.config import settings
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.chat import ChatMessage, ChatSession
from app.models.domain_event import DomainEvent
from app.models.user import User
from app.utils.errors import register_exception_handlers

PASSWORD = "PwaAttach123"
_seq = itertools.count(1)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return app


async def _mk_user(db, nickname: str, *, is_admin: bool) -> User:
    u = User(
        username=f"pwa_att_{next(_seq):03d}",
        nickname=nickname,
        password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode(),
        is_admin=is_admin,
    )
    db.add(u)
    await db.flush()
    return u


async def _seed_scene() -> dict:
    """甲家（主账号 a1）＋乙家（主账号 b1）；各一个角色 + 一个活跃会话。"""
    async with async_session_factory() as db:
        a1 = await _mk_user(db, "附件甲", is_admin=True)
        b1 = await _mk_user(db, "附件乙", is_admin=True)
        await db.flush()
        ch_a = AICharacter(user_id=a1.id, name="附件角色", greeting_message="你好，发个文件吧")
        ch_b = AICharacter(user_id=b1.id, name="外家角色")
        db.add_all([ch_a, ch_b])
        await db.flush()
        s_a = ChatSession(user_id=a1.id, character_id=ch_a.id, created_at=datetime(2026, 1, 2, 9))
        s_b = ChatSession(user_id=b1.id, character_id=ch_b.id, created_at=datetime(2026, 1, 3, 9))
        db.add_all([s_a, s_b])
        await db.flush()
        scene = dict(
            ua1=a1.id, ub1=b1.id, a1_username=a1.username, b1_username=b1.username,
            ch_a=ch_a.id, ch_b=ch_b.id, s_a=s_a.id, s_b=s_b.id,
        )
        await db.commit()
    return scene


# ── DB 探测器 ────────────────────────────────────────────────────────────────
async def _msg_count(session_id: int) -> int:
    async with async_session_factory() as db:
        return int((await db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)
        )).scalar_one())


async def _last_user_file_msg(session_id: int) -> ChatMessage | None:
    async with async_session_factory() as db:
        return (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.sender_type == "user")
            .order_by(ChatMessage.id.desc()).limit(1)
        )).scalars().first()


async def _file_events(entity_id: int) -> list[int]:
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(DomainEvent.entity_id).where(
                DomainEvent.event_type == "chat.message_sent",
                DomainEvent.entity_id == entity_id,
            )
        )).scalars().all()
        return [int(i) for i in rows]


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _enable_domain_events():
    from app.agent.loop import AGENT_FLAGS
    had = "domain_event_log_enabled" in AGENT_FLAGS
    prev = AGENT_FLAGS.get("domain_event_log_enabled")
    AGENT_FLAGS["domain_event_log_enabled"] = True
    yield
    if had:
        AGENT_FLAGS["domain_event_log_enabled"] = prev
    else:
        AGENT_FLAGS.pop("domain_event_log_enabled", None)


@pytest.fixture
def scene():
    return asyncio.run(_seed_scene())


@pytest.fixture(scope="module")
def client():
    # raise_server_exceptions=False：让「生成桩失败」用例能拿到 500 响应（断言客户端报错 ≠ 未写入）
    return TestClient(_app(), raise_server_exceptions=False)


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """把上传目录钉到本例 tmp_path（**两个真实持有者都猴补**）。"""
    d = tmp_path / "uploads"
    d.mkdir()
    monkeypatch.setattr("app.api.chat.UPLOAD_DIR", d)
    monkeypatch.setattr("app.application.upload_service.UPLOAD_DIR", d)
    return d


@pytest.fixture
def tripwire(monkeypatch):
    """LLM / TTS / embedding 入口钉成「调用即失败」，返回计数供断言 0 真实调用。"""
    calls = {"llm": 0, "tts": 0, "embed": 0}

    def _boom(name):
        def _f(*_a, **_kw):
            calls[name] += 1
            raise AssertionError(f"文件上传契约不得触发真实 {name} 调用")
        return _f

    import app.agent.llm_client as llm_client
    import app.agent.nodes as nodes
    import app.application.tts_service as tts_service
    import app.memory.embedding as emb

    for mod, name in ((llm_client, "chat_completion"), (llm_client, "chat_completion_stream"),
                      (nodes, "chat_completion"), (nodes, "chat_completion_stream")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _boom("llm"), raising=False)
    if hasattr(tts_service, "synthesize"):
        monkeypatch.setattr(tts_service, "synthesize", _boom("tts"), raising=False)
    monkeypatch.setattr(emb, "text_embedding", _boom("embed"), raising=False)
    return calls


@pytest.fixture
def gen_stub(monkeypatch):
    """把路由实际调用的 send_and_receive_chunked 换成可记录参数、返回合成 chunks 的桩。"""
    calls: list[dict] = []

    async def _stub(**kw):
        calls.append(kw)
        return {"chunks": [{
            "id": 9001, "session_id": kw["session_id"], "sender_type": "ai",
            "content": "（合成）已收到你的文件", "created_at": "2026-10-17 03:00:01",
            "extra_meta": json.dumps({"tools": ["文档问答"]}, ensure_ascii=False),
        }]}

    monkeypatch.setattr("app.api.chat.send_and_receive_chunked", _stub)
    return calls


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _upload(client, auth, sid: int, name: str, data: bytes, *, lang: str | None = None, expect: int = 200):
    headers = dict(auth)
    if lang is not None:
        headers["lang"] = lang
    r = client.post(f"/api/v1/chat/sessions/{sid}/file", headers=headers,
                    files={"file": (name, data, "text/plain")})
    assert r.status_code == expect, r.text
    return r


# ── 1. 成功：字段/摘要/桩参数/落库/事件/临时目录 ──────────────────────────────
def test_上传文本成功_字段与摘要与桩参数(client, scene, upload_dir, gen_stub, tripwire):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    body = "第一行内容\n第二行内容 more text".encode("utf-8")
    expect_summary = body[:8192].decode("utf-8", errors="ignore").strip()[:6000]

    before = asyncio.run(_msg_count(sid))
    r = _upload(client, auth, sid, "报告草案.md", body)
    data = r.json()

    # 响应契约
    fm = data["file_message"]
    assert set(fm.keys()) == {"id", "session_id", "sender_type", "content", "file_url", "created_at"}
    assert fm["session_id"] == sid and fm["sender_type"] == "user"
    assert fm["content"] == "报告草案.md"
    assert fm["file_url"].startswith(f"/uploads/files/{sid}/") and fm["file_url"].endswith(".md")
    assert isinstance(data["chunks"], list) and data["chunks"][0]["id"] == 9001

    # 真落库 + 元数据/摘要边界
    row = asyncio.run(_last_user_file_msg(sid))
    assert row is not None and row.id == fm["id"] and row.content == "报告草案.md"
    meta = json.loads(row.extra_meta)["file"]
    assert meta["name"] == "报告草案.md"
    assert meta["url"] == fm["file_url"]
    assert meta["type"] == "md"
    assert meta["summary"] == expect_summary and len(meta["summary"]) <= 6000
    assert asyncio.run(_msg_count(sid)) == before + 1
    # 文件事件真追加、绑该消息
    assert asyncio.run(_file_events(row.id)) == [row.id]

    # 桩：参数如实（内容含文件名 + 摘要；不重复存 user 消息；route/extr capabilities/lang）
    kw = gen_stub[-1]
    assert kw["session_id"] == sid and kw["user_id"] == scene["ua1"] and kw["character_id"] == scene["ch_a"]
    assert kw["save_user_message"] is False
    assert kw["route"] == "file"
    assert kw["extra_capabilities"] == ["文档问答"]
    assert kw["lang"] == "zh"
    assert "《报告草案.md》" in kw["content"] and expect_summary in kw["content"]

    # 上传目录隔离：正好 1 个文件、且在本例 tmp_path 内
    written = list(upload_dir.rglob("*"))
    files = [p for p in written if p.is_file()]
    assert len(files) == 1, written
    assert files[0].as_posix().startswith(upload_dir.as_posix())
    assert not (settings.PROJECT_ROOT / "data" / "uploads" / "files" / str(sid)).exists()
    assert tripwire == {"llm": 0, "tts": 0, "embed": 0}


# ── 2. lang 头真实名称 = lang ────────────────────────────────────────────────
def test_lang头透传到生成桩(client, scene, upload_dir, gen_stub, tripwire):
    auth = _login(client, scene["a1_username"])
    _upload(client, auth, scene["s_a"], "note.txt", b"hello", lang="en")
    assert gen_stub[-1]["lang"] == "en"
    assert tripwire["llm"] == 0


# ── 3. 非法扩展名：400 且 0 文件/0 消息/桩 0 调用 ─────────────────────────────
def test_非法扩展名400零写入(client, scene, upload_dir, gen_stub, tripwire):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    before = asyncio.run(_msg_count(sid))
    r = _upload(client, auth, sid, "evil.py", b"print(1)", expect=400)
    assert "detail" in r.json()
    assert list(upload_dir.rglob("*")) == [] or not any(p.is_file() for p in upload_dir.rglob("*"))
    assert asyncio.run(_msg_count(sid)) == before
    assert gen_stub == [] and tripwire["llm"] == 0


# ── 4. 超上限 20MiB+1：400 且 0 文件/0 消息 ──────────────────────────────────
def test_超上限400零写入(client, scene, upload_dir, gen_stub):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    before = asyncio.run(_msg_count(sid))
    over = b"a" * (20 * 1024 * 1024 + 1)
    r = _upload(client, auth, sid, "big.txt", over, expect=400)
    assert "20" in str(r.json().get("detail", "")) or r.json().get("detail")
    assert not any(p.is_file() for p in upload_dir.rglob("*"))
    assert asyncio.run(_msg_count(sid)) == before
    assert gen_stub == []


# ── 5. 恰好 20MiB：边界通过 ─────────────────────────────────────────────────
def test_恰好上限20MiB通过(client, scene, upload_dir, gen_stub):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    exact = b"b" * (20 * 1024 * 1024)
    r = _upload(client, auth, sid, "exact.txt", exact)
    assert r.json()["file_message"]["content"] == "exact.txt"
    files = [p for p in upload_dir.rglob("*") if p.is_file()]
    assert len(files) == 1
    # 摘要 = 前 6000 个 'b'
    meta = json.loads(asyncio.run(_last_user_file_msg(sid)).extra_meta)["file"]
    assert meta["summary"] == "b" * 6000


# ── 6. 坏 multipart（缺 file 字段）：422 ────────────────────────────────────
def test_缺file字段422(client, scene, upload_dir, gen_stub, tripwire):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/file", headers=auth,
                    data={"not_file": "x"})
    assert r.status_code == 422
    assert gen_stub == []


# ── 7. 文件名边界：中文名保留、超 120 字符截断 ──────────────────────────────
def test_文件名边界_UTF8与120截断(client, scene, upload_dir, gen_stub):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    long_name = "长" * 200 + ".txt"
    r = _upload(client, auth, sid, long_name, "内容".encode("utf-8"))
    fname = r.json()["file_message"]["content"]
    assert fname == ("长" * 200 + ".txt")[:120]
    assert len(fname) == 120
    row = asyncio.run(_last_user_file_msg(sid))
    assert row.content == fname
    # 桩提供的文件名同口径
    assert fname in gen_stub[-1]["content"]


# ── 8. 摘要边界：先按 8192 字节读、再 UTF-8 解码、再截 6000 字符 ─────────────
def test_摘要边界_6000上限与8192字节读取(client, scene, upload_dir, gen_stub):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]

    # (a) ASCII 7000 字符（7000 字节，未触 8192 字节读取上限）⇒ 6000 字符上限生效
    _upload(client, auth, sid, "ascii.csv", ("c" * 7000).encode("utf-8"))
    m1 = json.loads(asyncio.run(_last_user_file_msg(sid)).extra_meta)["file"]
    assert m1["summary"] == "c" * 6000 and len(m1["summary"]) == 6000

    # (b) CJK 7000 字（21000 字节）⇒ 8192 字节读取**先**截断；截在多字节字符中间由
    #     errors="ignore" 安全丢弃，不得崩、不得多出半个字符
    cjk = "甲" * 7000
    _upload(client, auth, sid, "cjk.csv", cjk.encode("utf-8"))
    m2 = json.loads(asyncio.run(_last_user_file_msg(sid)).extra_meta)["file"]
    expected = cjk.encode("utf-8")[:8192].decode("utf-8", errors="ignore").strip()[:6000]
    assert m2["summary"] == expected
    assert expected == "甲" * 2730 and len(m2["summary"]) == 2730  # 8192//3
    assert "甲" * 2730 in gen_stub[-1]["content"]


# ── 9. 生成桩失败：客户端 500，但**文件与消息已真落库**（报错 ≠ 未写入）───────
def test_生成桩失败仍已落库(client, scene, upload_dir, monkeypatch):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    before = asyncio.run(_msg_count(sid))

    async def _boom(**_kw):
        raise RuntimeError("合成生成失败（模拟服务端在保存后出错）")

    monkeypatch.setattr("app.api.chat.send_and_receive_chunked", _boom)
    r = client.post(f"/api/v1/chat/sessions/{sid}/file", headers=auth,
                    files={"file": ("ok.txt", b"data", "text/plain")})
    assert r.status_code == 500
    # 关键边界：客户端报错不代表服务端没写入
    assert asyncio.run(_msg_count(sid)) == before + 1
    assert asyncio.run(_last_user_file_msg(sid)).content == "ok.txt"
    files = [p for p in upload_dir.rglob("*") if p.is_file()]
    assert len(files) == 1


# ── 10. 空 chunks：如实返回空，不虚构回复 ───────────────────────────────────
def test_空chunks不虚构(client, scene, upload_dir, monkeypatch):
    auth = _login(client, scene["a1_username"])

    async def _empty(**_kw):
        return {"chunks": []}

    monkeypatch.setattr("app.api.chat.send_and_receive_chunked", _empty)
    r = _upload(client, auth, scene["s_a"], "empty.txt", b"hi")
    assert r.json()["chunks"] == []


# ── 11. 归属校验：跨账号 / 不存在 session → 404；未认证 → 401 ─────────────────
def test_归属400_404与未认证401(client, scene, upload_dir, gen_stub, tripwire):
    auth_b = _login(client, scene["b1_username"])
    auth_a = _login(client, scene["a1_username"])

    # 乙账号上传到甲的会话 → 404（与「不存在」同观感）
    _upload(client, auth_b, scene["s_a"], "x.txt", b"x", expect=404)
    # 不存在的会话 → 404
    _upload(client, auth_a, 999999, "x.txt", b"x", expect=404)
    # 未认证 → 401
    r = client.post(f"/api/v1/chat/sessions/{scene['s_a']}/file",
                    files={"file": ("x.txt", b"x", "text/plain")})
    assert r.status_code == 401
    # 全程 0 文件写入、0 桩调用
    assert not any(p.is_file() for p in upload_dir.rglob("*"))
    assert gen_stub == [] and tripwire["llm"] == 0
