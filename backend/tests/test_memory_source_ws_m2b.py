# -*- coding: utf-8 -*-
"""M2 2b 真实 WebSocket 入口：签名 token、真实路由、同事务来源、失败回滚。"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import text

from app.api import chat as chat_api
from app.auth.config import create_token


async def _seed(u=9301, c=9311, s=9321):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"), {"u": u, "n": f"u{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": c, "u": u})
        await db.execute(text("INSERT OR IGNORE INTO chat_sessions(id,user_id,character_id,is_active) VALUES (:s,:u,:c,1)"),
                         {"s": s, "u": u, "c": c})
        await db.commit()


def _client():
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _fake_chunked(store, fail=None):
    async def fake(*, session_id, user_id, character_id, content, **kw):
        mid, _ = await store._persist_user_message(session_id, user_id, character_id, content, save_user_message=True)
        return {"chunks": [], "memories_updated": False,
                "user_message": {"id": mid, "session_id": session_id,
                                 "sender_type": "user", "content": content}}
    return fake


async def _counts(session_id):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        msgs = (await db.execute(text("SELECT COUNT(*) FROM chat_messages WHERE session_id=:s"), {"s": session_id})).scalar_one()
        srcs = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE session_id=:s"), {"s": session_id})).scalar_one()
    return msgs, srcs


def _recv_user(ws):
    for _ in range(6):
        data = ws.receive_json()
        if data.get("type") == "user_message":
            return data
    raise AssertionError("no user_message event")


def test_ws_user_message_signs_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_chunked", _fake_chunked(chat_store))
    asyncio.run(_seed())
    token = create_token(9301)
    with _client().websocket_connect(f"/api/v1/chat/ws/9321?token={token}") as ws:
        ws.send_json({"type": "chat_message", "character_id": 9311, "content": "WS 路由测试"})
        data = _recv_user(ws)
        mid = data["data"]["id"]
    async def check():
        async with async_session_factory() as db:
            uid = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"), {"m": mid})).scalar_one()
            ev = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE event_uid=:u"), {"u": uid})).scalar_one() if uid else 0
        return uid, ev
    uid, ev = asyncio.run(check())
    assert uid and ev == 1


def test_ws_sign_failure_rolls_back(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.memory import source_binding

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_chunked", _fake_chunked(chat_store))
    calls = []

    async def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("ws_sign_failure")

    monkeypatch.setattr(source_binding, "sign_chat_message", boom)
    asyncio.run(_seed(9302, 9312, 9322))
    token = create_token(9302)
    with _client().websocket_connect(f"/api/v1/chat/ws/9322?token={token}") as ws:
        ws.send_json({"type": "chat_message", "character_id": 9312, "content": "应回滚"})
        # 端点捕获异常后发 typing=false / 关闭；读到任意消息即停
        for _ in range(6):
            try:
                data = ws.receive_json()
            except WebSocketDisconnect:
                break
            if data.get("type") == "error":
                break
    assert calls == [1]
    assert asyncio.run(_counts(9322)) == (0, 0)


def test_ws_wrong_owner_denied(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_chunked", _fake_chunked(chat_store))
    asyncio.run(_seed(9303, 9313, 9323))
    asyncio.run(_seed(9304, 9314, 9324))
    token = create_token(9303)
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client().websocket_connect(f"/api/v1/chat/ws/9324?token={token}") as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_ws_invalid_token_denied(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    asyncio.run(_seed(9305, 9315, 9325))
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client().websocket_connect("/api/v1/chat/ws/9325?token=bad") as ws:
            ws.receive_json()
    assert exc.value.code == 4401
