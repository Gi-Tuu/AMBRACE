# -*- coding: utf-8 -*-
"""M2 2b binding：用户文本消息与来源 UID 同事务、身份/内容漂移拒绝。"""
import asyncio

import pytest
from sqlalchemy import text


async def _seed(user_id=7001, char_id=7101, session_id=7201):
    from app.db.database import async_session_factory
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


async def _persist(user_id, char_id, session_id, content="hello m2b"):
    from app.application import chat_store
    return await chat_store._persist_user_message(
        session_id, user_id, char_id, content, save_user_message=True)


def test_user_message_and_source_event_same_transaction(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage
    from app.models.memory import MemorySourceEvent

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        await _seed()
        msg_id, _ = await _persist(7001, 7101, 7201)
        async with async_session_factory() as db:
            msg = await db.get(ChatMessage, msg_id)
            ev = await db.get(MemorySourceEvent, msg.source_event_uid)
            return msg, ev

    msg, ev = asyncio.run(run())
    assert msg.source_event_uid and ev is not None
    assert (ev.owner_user_id, ev.character_id) == (7001, 7101)
    assert ev.session_id == 7201
    assert ev.source_row_id == msg.id


def test_flag_off_emits_no_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", False)

    async def run():
        await _seed(7002, 7102, 7202)
        msg_id, _ = await _persist(7002, 7102, 7202, "off")
        async with async_session_factory() as db:
            msg = await db.get(ChatMessage, msg_id)
            count = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_source_events WHERE kind='chat_message' "
                "AND source_row_id=:m"), {"m": msg_id})).scalar_one()
            return msg, count

    msg, count = asyncio.run(run())
    assert msg.source_event_uid is None and count == 0


def test_wrong_authenticated_user_rolls_back_message(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        await _seed(7003, 7103, 7203)
        with pytest.raises(ValueError):
            await _persist(9999, 7103, 7203, "forged")
        async with async_session_factory() as db:
            return (await db.execute(text(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id=7203"))).scalar_one()

    assert asyncio.run(run()) == 0


def test_binding_rejects_content_or_generation_drift(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage
    from app.memory import source_binding

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        await _seed(7004, 7104, 7204)
        msg_id, _ = await _persist(7004, 7104, 7204, "original")
        async with async_session_factory() as db:
            msg = await db.get(ChatMessage, msg_id)
            assert await source_binding.resolve_chat_binding(
                db, user_id=7004, character_id=7104, source="chat",
                source_id=msg_id, expected_uid=msg.source_event_uid) is not None
            await db.execute(text("UPDATE chat_messages SET content='changed' WHERE id=:m"),
                             {"m": msg_id})
            await db.commit()
            return await source_binding.resolve_chat_binding(
                db, user_id=7004, character_id=7104, source="chat",
                source_id=msg_id, expected_uid=msg.source_event_uid)

    assert asyncio.run(run()) is None


def test_delete_route_revokes_source_in_transaction(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage
    from app.models.memory import MemorySourceEvent
    from app.api.chat import delete_message

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        await _seed(7005, 7105, 7205)
        msg_id, _ = await _persist(7005, 7105, 7205, "delete me")
        async with async_session_factory() as db:
            msg = await db.get(ChatMessage, msg_id)
            uid = msg.source_event_uid
            await delete_message(msg_id, db=db, user_id=7005, lang="zh")
        async with async_session_factory() as db:
            gone = await db.get(ChatMessage, msg_id) is None
            ev = await db.get(MemorySourceEvent, uid)
            return gone, ev.deleted

    assert asyncio.run(run()) == (True, True)


def test_delete_revoke_failure_rolls_back_message(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage
    from app.models.memory import MemorySourceEvent
    from app.api.chat import delete_message
    from app.memory import source_binding

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def boom(*a, **k):
        raise RuntimeError("revoke_injected")

    async def run():
        await _seed(7006, 7106, 7206)
        msg_id, _ = await _persist(7006, 7106, 7206, "delete rollback")
        async with async_session_factory() as db:
            msg = await db.get(ChatMessage, msg_id)
            uid = msg.source_event_uid
        monkeypatch.setattr(source_binding, "revoke_chat_message", boom)
        async with async_session_factory() as db:
            try:
                await delete_message(msg_id, db=db, user_id=7006, lang="zh")
            except RuntimeError:
                pass
            else:
                raise AssertionError("injected revoke failure did not raise")
        async with async_session_factory() as db:
            still = await db.get(ChatMessage, msg_id) is not None
            ev = await db.get(MemorySourceEvent, uid)
            return still, ev.deleted

    assert asyncio.run(run()) == (True, False)
