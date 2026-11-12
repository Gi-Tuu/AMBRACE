# -*- coding: utf-8 -*-
"""M2 2b 权威写：真实 save_memory → Memory+receipt 原子；flag 关兼容。"""
import asyncio

import pytest
from sqlalchemy import text


async def _seed(user_id=7301, char_id=7311, session_id=7321, content="source text m2b"):
    from app.db.database import async_session_factory
    from app.application import chat_store
    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) "
            "VALUES (:u,:n,'n','x')"), {"u": user_id, "n": f"m2b_{user_id}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,"
            "talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
            "VALUES (:c,:u,'c',0,0,1,0,0)"), {"c": char_id, "u": user_id})
        await db.execute(text(
            "INSERT OR IGNORE INTO chat_sessions(id,user_id,character_id,is_active) "
            "VALUES (:s,:u,:c,1)"), {"s": session_id, "u": user_id, "c": char_id})
        await db.commit()
    msg_id, _ = await chat_store._persist_user_message(
        session_id, user_id, char_id, content, save_user_message=True)
    return msg_id


def test_save_memory_authoritative_receipt(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import write as w
    from app.db.database import async_session_factory
    from app.models.memory import Memory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg_id = await _seed()
        mem = await w.save_memory(
            user_id=7301, character_id=7311, memory_type="event",
            content="一起记住的合成内容", source="chat", source_id=msg_id)
        async with async_session_factory() as db:
            row = await db.get(Memory, mem.id) if mem else None
            # 批 1A 后普通回执默认也写 → 本用例只看「权威回执」（其判据本就是 authoritative）
            receipts = (await db.execute(text(
                "SELECT * FROM memory_write_receipts WHERE memory_id=:m AND authoritative=1"),
                {"m": mem.id if mem else -1})).mappings().all()
        return mem, row, receipts

    mem, row, receipts = asyncio.run(run())
    assert mem is not None and row is not None
    assert row.source_event_uid and row.source_event_uid == row.source_event_uid
    assert len(receipts) == 1
    assert receipts[0]["authoritative"] == 1
    assert receipts[0]["source_event_uid"] == row.source_event_uid


def test_missing_source_is_rejected(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import write as w
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        await _seed(7302, 7312, 7322)
        with pytest.raises(RuntimeError):
            await w.save_memory(user_id=7302, character_id=7312,
                                memory_type="event", content="x",
                                source="chat", source_id=9999999)
        async with async_session_factory() as db:
            return (await db.execute(text(
                "SELECT COUNT(*) FROM memory_write_receipts WHERE owner_user_id=7302"
            ))).scalar_one()

    assert asyncio.run(run()) == 0


def test_flag_off_keeps_legacy_path(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import write as w
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", False)
    # 批 1A 后普通回执默认也写 → 本用例要「零回执」，显式关
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", False)
    # 批 1A 后 admission 默认开 → 角色来源会被降级 INFERRED；本用例判旧 FACT 路径，显式关
    monkeypatch.setitem(AGENT_FLAGS, "memory_admission_gate", False)

    async def run():
        msg_id = await _seed(7303, 7313, 7323, "legacy off")
        from datetime import datetime as _dt
        t0 = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        mem = await w.save_memory(
            user_id=7303, character_id=7313, memory_type="event",
            content="legacy only content", source="chat", source_id=msg_id)
        async with async_session_factory() as db:
            n = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_write_receipts WHERE memory_id=:m "
                "AND created_at >= :t0"),
                {"m": mem.id if mem else -1, "t0": t0})).scalar_one()
            uid = (await db.execute(text(
                "SELECT source_event_uid FROM memories WHERE id=:m"),
                {"m": mem.id if mem else -1})).scalar_one()
        return mem, n, uid

    mem, n, uid = asyncio.run(run())
    assert mem is not None and n == 0 and uid is None
