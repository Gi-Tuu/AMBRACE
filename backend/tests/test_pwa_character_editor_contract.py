# -*- coding: utf-8 -*-
"""PWA M1a-5 角色创建/编辑 · **真实路由字段契约**（沙箱集成，2026-10-18）。

用真实 `app.api.characters.router` 的 POST / GET / PUT + 真实 SQLAlchemy 会话
（conftest 已把 DATABASE_URL 指向本会话临时沙箱），**不 import app.main**。
口径：十字段写入/读回、未提交字段保持、文本清空空串/数值清空 null、bool 真切换；
名称按 **Unicode 码点** ≤100（不是 UTF-16 码元）；talk 0–100、timezone −12..14。
开场白只手填：**不调用 generate-greeting**（被守卫打断即失败）。
错误状态码以现行 Pydantic/路由契约为准（非法入参 ⇒ 4xx），不为旧工单的粗写 400 去改业务。
"""
from __future__ import annotations

import asyncio

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from starlette.testclient import TestClient

from app.api.characters import router as characters_router
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.utils.errors import register_exception_handlers

USER = "pwa_cec_u"
PASSWORD = "PwaCec123"
FIELDS = ("name", "bio", "personality", "chat_style", "greeting_message", "gender",
          "appearance", "talkativeness", "talkativeness_locked", "timezone_offset")
_IDS: dict[str, int] = {}


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(characters_router)
    return app


async def _seed() -> None:
    async with async_session_factory() as db:
        row = (await db.execute(
            text("SELECT id FROM users WHERE username=:u"), {"u": USER})).first()
        if row:
            _IDS["user"] = int(row[0])
            return
        u = await db.execute(
            text("INSERT INTO users (username, nickname, password_hash, is_admin) "
                 "VALUES (:u, :u, :p, 1)"),
            {"u": USER, "p": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()})
        await db.commit()
        _IDS["user"] = int(u.lastrowid)


async def _counts() -> dict[str, int]:
    async with async_session_factory() as db:
        out = {}
        for t in ("ai_characters", "chat_sessions", "chat_messages", "llm_usage"):
            out[t] = int((await db.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar_one())
        return out


async def _row(cid: int) -> dict:
    async with async_session_factory() as db:
        r = (await db.execute(text("SELECT * FROM ai_characters WHERE id=:c"), {"c": cid})).mappings().first()
        return dict(r) if r else {}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    asyncio.run(_seed())
    return _IDS


@pytest.fixture(scope="module")
def client():
    with TestClient(_app()) as session:
        yield session


@pytest.fixture(autouse=True)
def external_tripwire(monkeypatch):
    """真实出口及已加载的早绑定引用均拦截；吞掉异常也会在收尾失败。"""
    import sys
    import httpx
    import app.agent.llm_client as llm_client
    from app.application import tts_service
    from app.memory import embedding

    calls = []

    def boom(*args, **kwargs):
        calls.append(True)
        raise AssertionError("角色管理不得调用模型、嵌入、语音或外网")

    targets = [llm_client.chat_completion, llm_client.chat_completion_stream,
               embedding.text_embedding, embedding._embed_sync, tts_service.synthesize]
    for module_name, module in list(sys.modules.items()):
        if module_name.startswith("app.") and module is not None:
            for name, value in list(vars(module).items()):
                if any(value is target for target in targets):
                    monkeypatch.setattr(module, name, boom)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom)
    yield calls
    assert not calls, "流程触发了禁止的出口（包括被业务吞掉的异常）"


@pytest.fixture
def no_greeting(monkeypatch):
    """禁止 create/update 触发 generate-greeting（它会调 LLM）。"""
    import app.application.characters as svc

    def _boom(*_a, **_kw):
        raise AssertionError("创建/编辑不得调用 generate-greeting")

    monkeypatch.setattr(svc, "generate_greeting", _boom)
    return True


def _auth(client: TestClient) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _payload(**kw) -> dict:
    base = {"name": "契约角色", "bio": "住在城南", "personality": "温柔",
            "chat_style": "自然", "greeting_message": "你来啦（手填）",
            "gender": "female", "appearance": "短发",
            "talkativeness": 60, "talkativeness_locked": False, "timezone_offset": 8}
    base.update(kw)
    return base


def test_创建201_十字段可读回且与库一致(client, _seeded, no_greeting):
    h = _auth(client)
    before = asyncio.run(_counts())
    r = client.post("/api/v1/characters", json=_payload(), headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    cid = int(body["id"])
    assert int(body["user_id"]) == _seeded["user"], "owner 必须是当前账号"
    for k in FIELDS:
        assert k in body, f"响应缺字段 {k}"

    g = client.get(f"/api/v1/characters/{cid}", headers=h)
    assert g.status_code == 200, g.text
    got = g.json()
    expect = _payload()
    for k in FIELDS:
        assert got[k] == expect[k], f"{k}: 响应={got[k]!r} 期望={expect[k]!r}"

    row = asyncio.run(_row(cid))
    for k in FIELDS:  # 十字段逐项落库核对（不只 name/bio/数值）
        assert row.get(k) == expect[k], f"库字段 {k}: {row.get(k)!r} ≠ {expect[k]!r}"

    after = asyncio.run(_counts())
    for t in ("chat_sessions", "chat_messages", "llm_usage"):
        assert after[t] == before[t], f"创建不应改动 {t}"
    assert after["ai_characters"] == before["ai_characters"] + 1


def test_PUT省略保持_文本清空空串_数值清空null_bool真切换(client, _seeded, no_greeting):
    h = _auth(client)
    cid = int(client.post("/api/v1/characters", json=_payload(), headers=h).json()["id"])

    # ① 只提交 bio='' ⇒ 其余九项保持
    r = client.put(f"/api/v1/characters/{cid}", json={"bio": ""}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bio"] == ""
    for k in FIELDS:
        if k != "bio":
            assert body[k] == _payload()[k], f"未提交字段 {k} 被覆盖：{body[k]!r}"
    row = asyncio.run(_row(cid))
    assert (row["bio"] or "") == ""
    for k in FIELDS:
        want = "" if k == "bio" else _payload()[k]
        assert (row.get(k) or "") == (want or ""), f"库字段 {k} 被误改：{row.get(k)!r} ≠ {want!r}"

    mid = asyncio.run(_counts())
    # ② 数值清空 ⇒ null
    r = client.put(f"/api/v1/characters/{cid}",
                   json={"talkativeness": None, "timezone_offset": None}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["talkativeness"] is None and r.json()["timezone_offset"] is None
    row = asyncio.run(_row(cid))
    assert row["talkativeness"] is None and row["timezone_offset"] is None

    # ③ bool 真切换 True/False 都不丢
    assert client.put(f"/api/v1/characters/{cid}",
                      json={"talkativeness_locked": True}, headers=h).json()["talkativeness_locked"] is True
    assert asyncio.run(_row(cid))["talkativeness_locked"]
    assert client.put(f"/api/v1/characters/{cid}",
                      json={"talkativeness_locked": False}, headers=h).json()["talkativeness_locked"] is False
    assert not asyncio.run(_row(cid))["talkativeness_locked"]
    post = asyncio.run(_counts())
    for t in ("chat_sessions", "chat_messages", "llm_usage"):
        assert post[t] == mid[t], f"编辑不应改动 {t}：{mid[t]}→{post[t]}"


def test_名称按Unicode码点_100通过101拒绝(client, _seeded, no_greeting):
    h = _auth(client)
    ok = client.post("/api/v1/characters", json={"name": "\U0001F600" * 100}, headers=h)
    assert ok.status_code == 201, f"100 码点应通过：{ok.text[:200]}"
    bad = client.post("/api/v1/characters", json={"name": "\U0001F600" * 101}, headers=h)
    assert bad.status_code in (400, 422), f"101 码点应被拒，实际 {bad.status_code}"


def test_名称_纯空白名POST与PUT均被拒_非空原文保留(client, _seeded, no_greeting):
    """schema 已补「纯空白名」校验：普通空格/换行制表/全角空格都要拒；
    非空但带两侧空白的名字**按原文保存**（不擅自 trim）。"""
    h = _auth(client)
    counts = asyncio.run(_counts())
    for blank in ("   ", "\n\t", "\u3000\u3000"):
        r = client.post("/api/v1/characters", json={"name": blank}, headers=h)
        assert r.status_code == 422, f"POST 空白名 {blank!r} 应 422，实际 {r.status_code}"

    assert asyncio.run(_counts()) == counts, "拒绝创建不得新增任何行"
    cid = int(client.post("/api/v1/characters", json=_payload(), headers=h).json()["id"])
    before = asyncio.run(_row(cid))
    counts = asyncio.run(_counts())
    bad = client.put(f"/api/v1/characters/{cid}", json={"name": "   "}, headers=h)
    assert bad.status_code == 422, f"PUT 空白名应 422，实际 {bad.status_code}"
    assert asyncio.run(_row(cid)) == before, "被拒的 PUT 不得改动任何字段"
    assert asyncio.run(_counts()) == counts, "拒绝修改不得新增任何行"

    padded = "  留白名  "
    ok = client.put(f"/api/v1/characters/{cid}", json={"name": padded}, headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["name"] == padded, "非空名称必须按原文保留（不擅自 trim）"
    assert asyncio.run(_row(cid))["name"] == padded


def test_数值边界_talk与timezone(client, _seeded, no_greeting):
    h = _auth(client)
    for v in (0, 100):
        assert client.post("/api/v1/characters",
                           json={"name": f"talk{v}", "talkativeness": v}, headers=h).status_code == 201
    for v in (-1, 101):
        r = client.post("/api/v1/characters", json={"name": f"talkBad{v}", "talkativeness": v}, headers=h)
        assert r.status_code in (400, 422), f"talk={v} 应被拒，实际 {r.status_code}"
    for v in (-12, 14):
        assert client.post("/api/v1/characters",
                           json={"name": f"tz{v}", "timezone_offset": v}, headers=h).status_code == 201
    for v in (-13, 15):
        r = client.post("/api/v1/characters", json={"name": f"tzBad{v}", "timezone_offset": v}, headers=h)
        assert r.status_code in (400, 422), f"timezone={v} 应被拒，实际 {r.status_code}"


def test_非法调用不改数据(client, _seeded, no_greeting):
    h = _auth(client)
    cid = int(client.post("/api/v1/characters", json=_payload(), headers=h).json()["id"])
    before = asyncio.run(_row(cid))
    client.post("/api/v1/characters", json={"name": "x" * 101}, headers=h)
    client.put(f"/api/v1/characters/{cid}", json={"talkativeness": 999}, headers=h)
    client.put(f"/api/v1/characters/{cid}", json={"name": ""}, headers=h)
    after = asyncio.run(_row(cid))
    for k in FIELDS:
        assert after.get(k) == before.get(k), f"非法调用改动了 {k}: {before.get(k)!r}→{after.get(k)!r}"
