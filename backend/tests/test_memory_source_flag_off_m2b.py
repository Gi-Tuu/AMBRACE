# -*- coding: utf-8 -*-
"""M2 2b flag off：不签发来源、不触发权威写，旧路径保持。"""
import asyncio

from sqlalchemy import text


async def _seed():
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (7501,'u','n','x')"))
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,"
            "talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
            "VALUES (7511,7501,'c',0,0,1,0,0)"))
        await db.execute(text(
            "INSERT OR IGNORE INTO chat_sessions(id,user_id,character_id,is_active) VALUES (7521,7501,7511,1)"))
        await db.commit()


def test_flag_off_no_source_event(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", False)

    async def run():
        await _seed()
        msg_id, _ = await chat_store._persist_user_message(7521, 7501, 7511, "off", True)
        async with async_session_factory() as db:
            return (await db.execute(text(
                "SELECT COUNT(*) FROM memory_source_events WHERE source_row_id=:m"
            ), {"m": msg_id})).scalar_one()

    assert asyncio.run(run()) == 0


def test_flag_off_does_not_revoke_on_delete(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import source_binding
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", False)

    async def run():
        await _seed()
        async with async_session_factory() as db:
            msg = ChatMessage(session_id=7521, sender_type="user", content="x")
            db.add(msg); await db.flush()
            await db.commit()
            assert not source_binding.enabled()
            await source_binding.revoke_chat_message(db, msg, user_id=7501)
            return True

    assert asyncio.run(run()) is True
