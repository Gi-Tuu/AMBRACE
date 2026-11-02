# -*- coding: utf-8 -*-
"""M2 2b 真实 HTTP/SSE 聊天入口：来源签发同事务、失败回滚、关保存不重复。"""
import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from sqlalchemy import text

from app.api import chat as chat_api
from app.auth.config import create_token


async def _seed(u=9001, c=9011, s=9021):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"), {"u": u, "n": f"u{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
            "VALUES (:c,:u,'c',0,0,1,0,0)"), {"c": c, "u": u})
        await db.execute(text("INSERT OR IGNORE INTO chat_sessions(id,user_id,character_id,is_active) VALUES (:s,:u,:c,1)"), {"s": s, "u": u, "c": c})
        await db.commit()


def _client():
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _auth(uid=9001):
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _fake_send(store):
    async def fake(*, session_id, user_id, character_id, content, **kw):
        save = kw.get("save_user_message", True)
        if not save:
            return
        mid, _ = await store._persist_user_message(session_id, user_id, character_id, content, save_user_message=True)
        return {"chunks": [], "memories_updated": False,
                "user_message": {"id": mid, "session_id": session_id,
                                 "sender_type": "user", "content": content}}
    return fake


def _fake_stream(store):
    async def fake(session_id, user_id, character_id, content, **kw):
        save = kw.get("save_user_message", True)
        if not save:
            return
        mid, _ = await store._persist_user_message(session_id, user_id, character_id, content, save_user_message=True)
        sink = kw.get("sink")
        if sink:
            await sink("user_message", {"id": mid, "session_id": session_id,
                                        "sender_type": "user", "content": content})
            await sink("done", {"message": {"id": 1, "content": "ok"}})
    return fake


async def _rows(message_id=None):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        if message_id is None:
            return (await db.execute(text(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id=9021"))).scalar_one()
        msg = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"),
                                {"m": message_id})).first()
        ev = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE source_uid IS NULL"))).scalar_one() if False else None
    return msg


def test_http_send_signs_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive", _fake_send(chat_store))

    async def run():
        await _seed()
    asyncio.run(run())
    r = _client().post("/api/v1/chat/send", headers=_auth(),
                       json={"session_id": 9021, "content": "真实路由测试"})
    assert r.status_code == 200, r.text
    mid = r.json()["user_message"]["id"]
    async def check():
        async with async_session_factory() as db:
            uid = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"), {"m": mid})).scalar_one()
            ev = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE event_uid=:u"), {"u": uid})).scalar_one() if uid else 0
        return uid, ev
    uid, ev = asyncio.run(check())
    assert uid and ev == 1


def test_http_send_sign_failure_rolls_back(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.memory import source_binding
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive", _fake_send(chat_store))
    calls = []

    async def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("sign_failure")

    monkeypatch.setattr(source_binding, "sign_chat_message", boom)
    asyncio.run(_seed(9002, 9012, 9022))
    r = _client().post("/api/v1/chat/send", headers=_auth(9002),
                       json={"session_id": 9022, "content": "应回滚"})
    assert calls == [1], "sign failure injection was not reached"
    assert r.status_code == 500

    async def check():
        async with async_session_factory() as db:
            msgs = (await db.execute(text("SELECT COUNT(*) FROM chat_messages WHERE session_id=9022"))).scalar_one()
            srcs = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE session_id=9022"))).scalar_one()
        return msgs, srcs

    assert asyncio.run(check()) == (0, 0)


def test_sse_send_signs_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_stream", _fake_stream(chat_store))
    asyncio.run(_seed(9003, 9013, 9023))
    r = _client().post("/api/v1/chat/sessions/9023/messages/stream", headers=_auth(9003),
                       json={"content": "SSE 路由测试"})
    assert r.status_code == 200
    assert "user_message" in r.text
    async def check():
        async with async_session_factory() as db:
            return (await db.execute(text(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id=9023 AND source_event_uid IS NOT NULL"))).scalar_one()
    assert asyncio.run(check()) == 1


def test_sse_save_user_message_false_no_sign(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_stream", _fake_stream(chat_store))
    asyncio.run(_seed(9004, 9014, 9024))
    r = _client().post("/api/v1/chat/sessions/9024/messages/stream", headers=_auth(9004),
                       json={"content": "不保存", "save_user_message": False})
    assert r.status_code == 200
    async def check():
        async with async_session_factory() as db:
            return (await db.execute(text("SELECT COUNT(*) FROM chat_messages WHERE session_id=9024"))).scalar_one()
    assert asyncio.run(check()) == 0



