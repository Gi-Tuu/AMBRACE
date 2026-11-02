import asyncio
from sqlalchemy import text
from test_memory_source_routes_m2b import _seed, _client, _auth, _fake_send


def test_http_sign_failure_reaches_injection_with_valid_owner(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.api import chat as chat_api
    from app.application import chat_store
    from app.memory import source_binding
    from app.db.database import async_session_factory
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive", _fake_send(chat_store))
    calls = []
    async def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic_sign_failure")
    monkeypatch.setattr(source_binding, "sign_chat_message", fail)
    asyncio.run(_seed(9102, 9112, 9122))
    response = _client().post("/api/v1/chat/send", headers=_auth(9102), json={"session_id": 9122, "content": "synthetic failure"})
    assert calls == [1], "injection was never reached"
    assert response.status_code == 500
    async def rows():
        async with async_session_factory() as db:
            messages = (await db.execute(text("SELECT COUNT(*) FROM chat_messages WHERE session_id=9122"))).scalar_one()
            sources = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE session_id=9122"))).scalar_one()
            return messages, sources
    assert asyncio.run(rows()) == (0, 0)
