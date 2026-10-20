# -*- coding: utf-8 -*-
"""PWA M1a-4 文本附件 · **隔离与真实写入边界**（沙箱集成，2026-10-17）。

本文件只钉三件「防越界」的事（不重复 contract 文件的字段级断言）：
1. **上传目录隔离**：conftest 的 DB 隔离**不**自动隔离上传目录 —— 必须猴补
   ``app.api.chat.UPLOAD_DIR`` 与 ``app.application.upload_service.UPLOAD_DIR`` 到本例
   ``tmp_path``；断言实际落盘只在本例 tmp_path，且**绝不**在 ``backend/data/uploads`` 下
   生成本次用例的会话目录（生产上传目录零写入）。
2. **DB 行对账**：上传只应新增「1 条 user 文件消息 + 1 条 chat.message_sent 事件」，
   会话表与其它会话的消息行一行不动（只出现预期写）。
3. **跨家庭不串 + 外部调用零**：乙家上传不动甲家任何行；LLM/TTS/embedding 入口
   「调用即失败」计数为 0。
"""
from __future__ import annotations

import asyncio
import itertools
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

PASSWORD = "PwaAttIso123"
_seq = itertools.count(1000)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return app


async def _mk_user(db, nickname: str) -> User:
    u = User(
        username=f"pwa_iso_{next(_seq):04d}",
        nickname=nickname,
        password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode(),
        is_admin=True,
    )
    db.add(u)
    await db.flush()
    return u


async def _seed_scene() -> dict:
    async with async_session_factory() as db:
        a1 = await _mk_user(db, "隔离甲")
        b1 = await _mk_user(db, "隔离乙")
        await db.flush()
        ch_a = AICharacter(user_id=a1.id, name="甲角色")
        ch_b = AICharacter(user_id=b1.id, name="乙角色")
        db.add_all([ch_a, ch_b])
        await db.flush()
        s_a = ChatSession(user_id=a1.id, character_id=ch_a.id, created_at=datetime(2026, 2, 1, 9))
        s_b = ChatSession(user_id=b1.id, character_id=ch_b.id, created_at=datetime(2026, 2, 2, 9))
        db.add_all([s_a, s_b])
        await db.flush()
        # 甲会话预置 2 条消息（越权哨兵：上传走乙时不得被动过）
        db.add_all([
            ChatMessage(session_id=s_a.id, sender_type="ai", content="甲-AI", created_at=datetime(2026, 2, 1, 9, 1)),
            ChatMessage(session_id=s_a.id, sender_type="user", content="甲-USER", created_at=datetime(2026, 2, 1, 9, 2)),
        ])
        await db.flush()
        out = dict(
            ua1=a1.id, ub1=b1.id, a1_username=a1.username, b1_username=b1.username,
            ch_a=ch_a.id, ch_b=ch_b.id, s_a=s_a.id, s_b=s_b.id,
        )
        await db.commit()
    return out


# ── DB 探测器 ────────────────────────────────────────────────────────────────
async def _counts() -> dict:
    async with async_session_factory() as db:
        return {
            "sessions": int((await db.execute(select(func.count()).select_from(ChatSession))).scalar_one()),
            "messages": int((await db.execute(select(func.count()).select_from(ChatMessage))).scalar_one()),
            "events": int((await db.execute(select(func.count()).select_from(DomainEvent))).scalar_one()),
        }


async def _sid_rows(session_id: int) -> int:
    async with async_session_factory() as db:
        return int((await db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)
        )).scalar_one())


async def _contents(session_id: int) -> list[str]:
    async with async_session_factory() as db:
        return list((await db.execute(
            select(ChatMessage.content).where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
        )).scalars().all())


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
    return TestClient(_app(), raise_server_exceptions=False)


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    d = tmp_path / "uploads"
    d.mkdir()
    monkeypatch.setattr("app.api.chat.UPLOAD_DIR", d)
    monkeypatch.setattr("app.application.upload_service.UPLOAD_DIR", d)
    return d


@pytest.fixture
def gen_stub(monkeypatch):
    calls: list[dict] = []

    async def _stub(**kw):
        calls.append(kw)
        return {"chunks": [{"id": 9100, "session_id": kw["session_id"], "sender_type": "ai",
                            "content": "ok", "created_at": "2026-10-17 03:00:01"}]}

    monkeypatch.setattr("app.api.chat.send_and_receive_chunked", _stub)
    return calls


@pytest.fixture
def tripwire(monkeypatch):
    calls = {"llm": 0, "tts": 0, "embed": 0}

    def _boom(name):
        def _f(*_a, **_kw):
            calls[name] += 1
            raise AssertionError(f"不得触发真实 {name} 调用")
        return _f

    import app.agent.llm_client as llm_client
    import app.agent.nodes as nodes
    import app.application.tts_service as tts
    import app.memory.embedding as emb

    for mod, name in ((llm_client, "chat_completion"), (llm_client, "chat_completion_stream"),
                      (nodes, "chat_completion"), (nodes, "chat_completion_stream")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _boom("llm"), raising=False)
    if hasattr(tts, "synthesize"):
        monkeypatch.setattr(tts, "synthesize", _boom("tts"), raising=False)
    monkeypatch.setattr(emb, "text_embedding", _boom("embed"), raising=False)
    return calls


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _upload(client, auth, sid: int, name: str, data: bytes, expect: int = 200):
    r = client.post(f"/api/v1/chat/sessions/{sid}/file", headers=auth,
                    files={"file": (name, data, "text/plain")})
    assert r.status_code == expect, r.text
    return r


# ── 1. 上传目录隔离：只写 tmp_path；生产上传目录零写入 ───────────────────────
def test_上传目录隔离_生产零写入(client, scene, upload_dir, gen_stub):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    _upload(client, auth, sid, "one.txt", b"one")
    _upload(client, auth, sid, "two.md", b"two")

    files = [p for p in upload_dir.rglob("*") if p.is_file()]
    assert len(files) == 2
    assert all(p.as_posix().startswith(upload_dir.as_posix()) for p in files)
    # 生产上传目录绝不出现本用例（唯一 sid）的会话目录
    assert not (settings.PROJECT_ROOT / "data" / "uploads" / "files" / str(sid)).exists()


# ── 2. DB 行对账：只出现预期写 ──────────────────────────────────────────────
def test_DB行对账只出现预期写(client, scene, upload_dir, gen_stub):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    before = asyncio.run(_counts())
    before_a_rows = asyncio.run(_sid_rows(sid))
    before_a_contents = asyncio.run(_contents(sid))

    _upload(client, auth, sid, "count.txt", b"hello")

    after = asyncio.run(_counts())
    assert after["sessions"] == before["sessions"], "上传不得新建/删除会话"
    assert after["messages"] == before["messages"] + 1, "只应新增 1 条 user 文件消息"
    assert after["events"] == before["events"] + 1, "只应新增 1 条文件事件"
    rows = asyncio.run(_contents(sid))
    assert rows[:len(before_a_contents)] == before_a_contents, "既有消息一行未动"
    assert len(rows) == before_a_rows + 1 and rows[-1] == "count.txt"


# ── 3. 跨家庭不串：乙家上传不动甲家 ─────────────────────────────────────────
def test_跨家庭上传不串(client, scene, upload_dir, gen_stub):
    auth_b = _login(client, scene["b1_username"])
    sid_b = scene["s_b"]
    before_a = asyncio.run(_contents(scene["s_a"]))
    before_a_rows = asyncio.run(_sid_rows(scene["s_a"]))

    r = _upload(client, auth_b, sid_b, "b.txt", b"b-data")
    assert r.json()["file_message"]["session_id"] == sid_b
    # 乙家自己会话 +1
    assert asyncio.run(_sid_rows(sid_b)) == 1
    # 甲家会话行数与内容一行未动
    assert asyncio.run(_sid_rows(scene["s_a"])) == before_a_rows
    assert asyncio.run(_contents(scene["s_a"])) == before_a
    # 甲家看不到乙家会话（404）
    auth_a = _login(client, scene["a1_username"])
    _upload(client, auth_a, sid_b, "x.txt", b"x", expect=404)


# ── 4. 两次上传各自独立（消息不串、同会话两条不同文件消息）──────────────────
def test_两次上传各自独立(client, scene, upload_dir, gen_stub):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    r1 = _upload(client, auth, sid, "first.txt", b"first")
    r2 = _upload(client, auth, sid, "second.txt", b"second")
    rows = [m for m in asyncio.run(_contents(sid)) if m.endswith(".txt")]
    assert rows == ["first.txt", "second.txt"]
    assert r1.json()["file_message"]["id"] != r2.json()["file_message"]["id"]
    assert len(gen_stub) == 2


# ── 5. 失败不污染：非法扩展名后 DB/目录/事件一行不加 ────────────────────────
def test_失败不污染(client, scene, upload_dir, gen_stub, tripwire):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_a"]
    before = asyncio.run(_counts())
    _upload(client, auth, sid, "bad.exe", b"MZ", expect=400)
    after = asyncio.run(_counts())
    assert after == before
    assert not any(p.is_file() for p in upload_dir.rglob("*"))
    assert gen_stub == [] and tripwire == {"llm": 0, "tts": 0, "embed": 0}


# ── 6. 外部调用零（成功路径亦不触发 LLM/TTS/embedding）──────────────────────
def test_成功路径外部调用零(client, scene, upload_dir, gen_stub, tripwire):
    auth = _login(client, scene["a1_username"])
    _upload(client, auth, scene["s_a"], "ext.txt", "中文内容".encode("utf-8"))
    assert tripwire == {"llm": 0, "tts": 0, "embed": 0}, tripwire
    # 摘要只取文本，不进模型（桩已挡在 send_and_receive_chunked 之外）
    assert gen_stub[-1]["content"].count("中文内容") == 1
