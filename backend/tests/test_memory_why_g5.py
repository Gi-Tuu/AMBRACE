# -*- coding: utf-8 -*-
"""RC2 G5 合同：「为什么记住」来源链端点（只读）。

- 有 source_event_uid 的记忆 ⇒ 来源事件＋签名消息摘录＋回执数；
- 无（老记忆）⇒ has_source=False、note=no_source_event，不伪造来源；
- 跨租户/不存在 ⇒ 404 同形；只读（无 commit）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from starlette.testclient import TestClient

from app.api import memories as mem_api
from app.api.memory_why import router as why_router
from app.auth.config import create_token


def _client():
    app = FastAPI()
    app.include_router(mem_api.router)
    app.include_router(why_router)
    return TestClient(app)


def _auth(uid=9551):
    return {"Authorization": f"Bearer {create_token(uid)}"}


async def _seed(uid=9551, cid=9561, with_event=True, receipt=False):
    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    from app.models.chat import ChatMessage, ChatSession
    from app.models.memory import Memory, MemorySourceEvent, MemoryWriteReceipt
    from app.models.user import User
    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"),
            {"u": uid, "n": f"g5_{uid}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": cid, "u": uid})
        await db.commit()
        m = Memory(user_id=uid, character_id=cid, memory_type="event",
                   content="G5 测试记忆内容", source="chat")
        db.add(m)
        await db.flush()
        mid = m.id
        if with_event:
            sess = ChatSession(user_id=uid, character_id=cid, title="g5", is_active=True,
                              created_at=datetime(2026, 11, 7, 12, 0, 0))
            db.add(sess)
            await db.flush()
            msg = ChatMessage(session_id=sess.id, sender_type="user",
                              content="签名来源消息正文", created_at=datetime(2026, 11, 7, 12, 0, 0))
            db.add(msg)
            await db.flush()
            import hashlib as _hl
            _sha = _hl.sha256("签名来源消息正文".encode("utf-8")).hexdigest()
            db.add(MemorySourceEvent(
                event_uid="mse-g5-test-0001", kind="chat_message",
                source_row_id=msg.id, generation=1, seq=990001,
                owner_user_id=uid, character_id=cid, session_id=sess.id,
                event_at=datetime(2026, 11, 7, 12, 0, 0), content_sha256=_sha))
            await db.flush()   # 事件先落，memories.source_event_uid 的 UPDATE 才有 FK 目标
            m.source_event_uid = "mse-g5-test-0001"
            db.add(ChatMessage(id=999001, session_id=sess.id, sender_type="user",
                               content="占位（非来源行）"))
        if receipt:
            db.add(MemoryWriteReceipt(character_id=cid, memory_id=mid,
                                      action="create", reason="g5 测试"))
        await db.commit()
        return mid


def test_why_returns_full_chain(monkeypatch):
    """有来源 ⇒ 事件＋消息摘录＋回执数全链返回。"""
    from app.db.database import async_session_factory
    monkeypatch.setattr(mem_api.tenant_scope_ids, "__defaults__", (), raising=False)

    async def run():
        mid = await _seed(with_event=True, receipt=True)
        client = _client()
        r = client.get(f"/api/v1/memories/{mid}/why", headers=_auth())
        return mid, r

    # tenant_scope_ids 在临时库需真实用户行——_seed 已建；直接走 API
    mid, r = asyncio.run(run())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok" and data["has_source"] is True
    assert data["source_event_uid"] == "mse-g5-test-0001"
    assert len(data["sources"]) == 1
    src = data["sources"][0]
    assert src["kind"] == "chat_message"
    assert "签名来源消息正文" in (src["message_excerpt"] or "")
    assert data["receipt_count"] == 1


def test_why_no_source_event(monkeypatch):
    """老记忆（无 uid）⇒ has_source=False、note=no_source_event，不伪造。"""
    async def run():
        mid = await _seed(uid=9552, cid=9562, with_event=False)
        client = _client()
        return client.get(f"/api/v1/memories/{mid}/why", headers=_auth(9552))

    r = asyncio.run(run())
    assert r.status_code == 200
    data = r.json()
    assert data["has_source"] is False and data["note"] == "no_source_event"
    assert data["sources"] == []


def test_why_cross_tenant_404():
    """跨租户/不存在 ⇒ 404 同形。"""
    client = _client()
    r = client.get("/api/v1/memories/999999/why", headers=_auth(9553))
    assert r.status_code == 404


def test_why_readonly_no_commit():
    """端点全程只读：无任何写语句（源码棘轮——G5 端点体内不许出现 commit）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "memory_why.py").read_text(encoding="utf-8")
    assert ".commit()" not in src, "G5 端点必须只读"
    assert "insert(" not in src and "UPDATE " not in src.upper().replace("SELECT", "")
