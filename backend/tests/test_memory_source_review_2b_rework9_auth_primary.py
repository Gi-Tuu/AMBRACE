import asyncio
from test_memory_source_routes_m2b import _seed, _client, _auth, _fake_send


def test_wrong_owner_stops_before_sign_injection(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.api import chat as chat_api
    from app.application import chat_store
    from app.memory import source_binding
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive", _fake_send(chat_store))
    calls = []
    async def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic_sign_failure")
    monkeypatch.setattr(source_binding, "sign_chat_message", fail)
    asyncio.run(_seed(9201, 9211, 9221))
    asyncio.run(_seed(9202, 9212, 9222))
    response = _client().post("/api/v1/chat/send", headers=_auth(9201), json={"session_id": 9222, "content": "synthetic"})
    assert response.status_code == 404
    assert calls == []
