# -*- coding: utf-8 -*-
"""RC2 M3/M4 合同：记忆生命周期只读投影端点。

- flag 关 ⇒ 404（未开放的读面不暴露）；
- flag 开 ⇒ version/superseded_by/valid_to/来源事件数/回执/派生计数；
- 跨租户/不存在 ⇒ 404 同形；只读（无写操作）。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from starlette.testclient import TestClient

from app.api import memories as mem_api
from app.api.memory_lifecycle import router as lifecycle_router  # noqa: F401 —— 注册路由
from app.auth.config import create_token


def _client():
    app = FastAPI()
    app.include_router(mem_api.router)
    return TestClient(app)


def _auth(uid=9571):
    return {"Authorization": f"Bearer {create_token(uid)}"}


async def _seed(uid=9571, cid=9572, with_event=True, receipt=False, superseded=False):
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage, ChatSession
    from app.models.memory import Memory, MemorySourceEvent, MemoryWriteReceipt
    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"),
            {"u": uid, "n": f"lc_{uid}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": cid, "u": uid})
        await db.commit()
        m = Memory(user_id=uid, character_id=cid, memory_type="event",
                   content="生命周期测试记忆", source="chat", version=2)
        if superseded:
            m.status = "superseded"
            m.superseded_by = 999
            m.valid_to = datetime(2026, 11, 13, 1, 0, 0)
        db.add(m)
        await db.flush()
        mid = m.id
        if with_event:
            sess = ChatSession(user_id=uid, character_id=cid, title="lc", is_active=True,
                               created_at=datetime(2026, 11, 13, 1, 0, 0))
            db.add(sess)
            await db.flush()
            msg = ChatMessage(session_id=sess.id, sender_type="user",
                              content="生命周期来源消息", created_at=datetime(2026, 11, 13, 1, 0, 0))
            db.add(msg)
            await db.flush()
            import hashlib as _hl
            _sha = _hl.sha256("生命周期来源消息".encode("utf-8")).hexdigest()
            db.add(MemorySourceEvent(
                event_uid=f"mse-lc-{mid}", kind="chat_message",
                source_row_id=msg.id, generation=1, seq=990100 + mid,
                owner_user_id=uid, character_id=cid, session_id=sess.id,
                event_at=datetime(2026, 11, 13, 1, 0, 0), content_sha256=_sha))
            await db.flush()
            m.source_event_uid = f"mse-lc-{mid}"
        if receipt:
            from sqlalchemy import delete as _delete
            await db.execute(_delete(MemoryWriteReceipt).where(MemoryWriteReceipt.memory_id == mid))
            db.add(MemoryWriteReceipt(character_id=cid, memory_id=mid,
                                      action="update", reason="content edited"))
        await db.commit()
        return mid


def test_flag_off_returns_404(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS

    mid = pytest.importorskip("asyncio").run(_seed(uid=9573, cid=9574))
    monkeypatch.setitem(AGENT_FLAGS, "memory_lifecycle_view_v1", False)
    r = _client().get(f"/api/v1/memories/{mid}/lifecycle", headers=_auth(9573))
    assert r.status_code == 404, r.text


def test_flag_on_returns_lifecycle_projection(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS

    mid = pytest.importorskip("asyncio").run(_seed(uid=9575, cid=9576, with_event=True, receipt=True))
    monkeypatch.setitem(AGENT_FLAGS, "memory_lifecycle_view_v1", True)
    r = _client().get(f"/api/v1/memories/{mid}/lifecycle", headers=_auth(9575))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["memory_id"] == mid
    assert j["version"] == 2
    assert j["source_event_uid"] == f"mse-lc-{mid}"
    assert j["source_event_count"] == 1
    assert j["revision_count"] >= 1
    assert any(x["action"] == "update" for x in j["receipts"])
    assert j["superseded_by"] is None


def test_superseded_state_visible(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS

    mid = pytest.importorskip("asyncio").run(
        _seed(uid=9577, cid=9578, with_event=False, superseded=True))
    monkeypatch.setitem(AGENT_FLAGS, "memory_lifecycle_view_v1", True)
    r = _client().get(f"/api/v1/memories/{mid}/lifecycle", headers=_auth(9577))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["superseded_by"] == 999
    assert j["valid_to"] is not None


def test_cross_tenant_404(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS

    mid = pytest.importorskip("asyncio").run(_seed(uid=9579, cid=9580))
    monkeypatch.setitem(AGENT_FLAGS, "memory_lifecycle_view_v1", True)
    r = _client().get(f"/api/v1/memories/{mid}/lifecycle", headers=_auth(9581))
    assert r.status_code == 404, r.text


def test_route_is_readonly_ratchet():
    """端点只读：源码里不得出现 commit/flush/delete/update 写操作。"""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "app/api/memory_lifecycle.py").read_text(
        encoding="utf-8")
    for banned in ("db.commit()", ".flush()", "delete(", "add("):
        assert banned not in src, banned
