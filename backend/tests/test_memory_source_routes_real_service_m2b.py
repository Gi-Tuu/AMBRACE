# -*- coding: utf-8 -*-
"""M2 2b 真实生成服务内部链：只替身 agent.ainvoke（模型边界），不替换 send_and_receive*。"""
import asyncio

from fastapi import FastAPI
from starlette.testclient import TestClient
from sqlalchemy import text

from app.api import chat as chat_api
from app.auth.config import create_token


async def _seed(u=9501, c=9511, s=9521):
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


class _BackgroundCollector:
    """收集真实服务 spawn_background 的任务，在 TestClient 同一 portal loop 中排空。"""

    def __init__(self):
        self.tasks = []

    def __call__(self, coro):
        import asyncio
        self.tasks.append(asyncio.create_task(coro))

    async def drain(self):
        import asyncio
        for _ in range(4):
            pending = [t for t in self.tasks if not t.done()]
            if not pending:
                return
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=10)
            except Exception:
                for t in pending:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                return


def _client():
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _auth(uid=9501):
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _fake_agent(monkeypatch, reply="合成回复"):
    from app.application import chat_service

    async def ainvoke(state):
        return {
            **state,
            "ai_response": reply,
            "raw_response": reply,
            "should_update_memory": False,
            "emotional_state": "",
            "streamed": False,
            "actions": [],
        }

    monkeypatch.setattr(chat_service.agent, "ainvoke", ainvoke)


async def _counts(session_id):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        msgs = (await db.execute(text("SELECT COUNT(*) FROM chat_messages WHERE session_id=:s"), {"s": session_id})).scalar_one()
        srcs = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE session_id=:s"), {"s": session_id})).scalar_one()
        uid = (await db.execute(text(
            "SELECT source_event_uid FROM chat_messages WHERE session_id=:s AND sender_type='user' LIMIT 1"), {"s": session_id})).scalar_one_or_none()
    return msgs, srcs, uid


def test_real_service_http_signs_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    _fake_agent(monkeypatch)
    from app.application import chat_service
    collector = _BackgroundCollector()
    monkeypatch.setattr(chat_service, "spawn_background", collector)
    asyncio.run(_seed(9501, 9511, 9521))
    client = _client()
    with client:
        r = client.post("/api/v1/chat/send", headers=_auth(9501),
                        json={"session_id": 9521, "content": "真实服务 HTTP"})
        client.portal.call(collector.drain)
    assert r.status_code == 200, r.text
    msgs, srcs, uid = asyncio.run(_counts(9521))
    assert uid and srcs >= 1


def test_real_service_http_sign_failure_rolls_back(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import source_binding
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    _fake_agent(monkeypatch)
    calls = []

    async def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("sign_failure")

    monkeypatch.setattr(source_binding, "sign_chat_message", boom)
    from app.application import chat_service
    collector = _BackgroundCollector()
    monkeypatch.setattr(chat_service, "spawn_background", collector)
    asyncio.run(_seed(9502, 9512, 9522))
    client = _client()
    with client:
        r = client.post("/api/v1/chat/send", headers=_auth(9502),
                        json={"session_id": 9522, "content": "真实服务失败"})
        client.portal.call(collector.drain)
    assert calls == [1]
    assert r.status_code == 500
    assert asyncio.run(_counts(9522))[:2] == (0, 0)


def test_real_service_sse_signs_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    _fake_agent(monkeypatch)
    from app.application import chat_service
    collector = _BackgroundCollector()
    monkeypatch.setattr(chat_service, "spawn_background", collector)
    asyncio.run(_seed(9503, 9513, 9523))
    client = _client()
    with client:
        r = client.post("/api/v1/chat/sessions/9523/messages/stream", headers=_auth(9503),
                        json={"content": "真实服务 SSE"})
        client.portal.call(collector.drain)
    assert r.status_code == 200
    msgs, srcs, uid = asyncio.run(_counts(9523))
    assert uid and srcs >= 1


def test_real_service_ws_signs_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    _fake_agent(monkeypatch)
    from app.application import chat_service
    collector = _BackgroundCollector()
    monkeypatch.setattr(chat_service, "spawn_background", collector)
    asyncio.run(_seed(9504, 9514, 9524))
    token = create_token(9504)
    client = _client()
    with client:
        with client.websocket_connect(f"/api/v1/chat/ws/9524?token={token}") as ws:
            ws.send_json({"type": "chat_message", "character_id": 9514, "content": "真实服务 WS"})
            user = None
            for _ in range(6):
                data = ws.receive_json()
                if data.get("type") == "user_message":
                    user = data["data"]
                    break
            assert user is not None
            mid = user["id"]
        client.portal.call(collector.drain)
    async def check():
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            uid = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"), {"m": mid})).scalar_one()
            ev = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE event_uid=:u"), {"u": uid})).scalar_one() if uid else 0
        return uid, ev
    uid, ev = asyncio.run(check())
    assert uid and ev == 1
