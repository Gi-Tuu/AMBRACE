# -*- coding: utf-8 -*-
"""2a 真实内核：forget→reallow→新来源→单次 grant→active 正例与旧来源/重复 grant 反例。"""
import asyncio

from sqlalchemy import text


async def _seed(u=9401, c=9411, s=9421):
    from test_memory_source_queue_m2b import _seed_and_message
    return await _seed_and_message(u, c, s)


def test_reallow_grant_active_kernel_positive(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import source_events as se
    from app.memory.service import save_memory
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg1 = await _seed(9401, 9411, 9421)
        mem = await save_memory(user_id=9401, character_id=9411, memory_type="event",
                                content="synthetic reallow content", source="chat", source_id=msg1)
        async with async_session_factory() as db:
            await db.run_sync(lambda sd: se.forget_memory(
                sd.connection(), memory_id=mem.id, owner_user_id=9401, character_id=9411))
            await db.commit()
            seq = (await db.execute(text("SELECT seq FROM source_seq WHERE id=1"))).scalar_one()
            token = await db.run_sync(lambda sd: se.reallow_memory(
                sd.connection(), memory_id=mem.id, owner_user_id=9401,
                character_id=9411, confirm_seq=int(seq)))
            await db.commit()
            state = (await db.execute(text("SELECT state FROM memory_tombstones WHERE memory_id=:m"),
                                      {"m": mem.id})).scalar_one()
            assert state == "reallowed"
            mid, _ = await chat_store._persist_user_message(9421, 9401, 9411, "source two")
            ev = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"),
                                   {"m": mid})).scalar_one()
            result = await db.run_sync(lambda sd: se.write_authoritative_memory(
                sd.connection(), event_uid=ev, memory_id=mem.id,
                owner_user_id=9401, character_id=9411,
                actor_kind="authenticated_user", actor_user_id=9401,
                action="update", idempotency_key=f"reallow:{ev}",
                memory_values={"content": "synthetic new authorized content"},
                grant_token=token))
            await db.commit()
            mem_content = (await db.execute(text("SELECT content FROM memories WHERE id=:m"),
                                            {"m": mem.id})).scalar_one()
            used = (await db.execute(text(
                "SELECT used FROM memory_reallow_grants WHERE memory_id=:m"), {"m": mem.id})).scalar_one()
            state2 = (await db.execute(text("SELECT state FROM memory_tombstones WHERE memory_id=:m"),
                                       {"m": mem.id})).scalar_one()
        return result, mem_content, used, state2, token, ev, mem.id

    result, content, used, state2, token, new_ev, memory_id = asyncio.run(run())
    assert result == "accepted"
    assert content == "synthetic new authorized content"
    assert used == 1 and state2 == "reallowed_active"


def test_reallow_old_source_and_reused_grant_refused(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import source_events as se
    from app.memory.service import save_memory
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg1 = await _seed(9402, 9412, 9422)
        mem = await save_memory(user_id=9402, character_id=9412, memory_type="event",
                                content="synthetic reallow negative", source="chat", source_id=msg1)
        async with async_session_factory() as db:
            old_ev = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"),
                                       {"m": msg1})).scalar_one()
            await db.run_sync(lambda sd: se.forget_memory(
                sd.connection(), memory_id=mem.id, owner_user_id=9402, character_id=9412))
            await db.commit()
            seq = (await db.execute(text("SELECT seq FROM source_seq WHERE id=1"))).scalar_one()
            token = await db.run_sync(lambda sd: se.reallow_memory(
                sd.connection(), memory_id=mem.id, owner_user_id=9402,
                character_id=9412, confirm_seq=int(seq)))
            await db.commit()
            mid2, _ = await chat_store._persist_user_message(9422, 9402, 9412, "source two")
            ev2 = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"),
                                    {"m": mid2})).scalar_one()
            old_result = await db.run_sync(lambda sd: se.write_authoritative_memory(
                sd.connection(), event_uid=old_ev, memory_id=mem.id,
                owner_user_id=9402, character_id=9412,
                actor_kind="authenticated_user", actor_user_id=9402,
                action="update", idempotency_key=f"old:{old_ev}",
                memory_values={"content": "old source should fail"}, grant_token=token))
            await db.commit()
            first = await db.run_sync(lambda sd: se.write_authoritative_memory(
                sd.connection(), event_uid=ev2, memory_id=mem.id,
                owner_user_id=9402, character_id=9412,
                actor_kind="authenticated_user", actor_user_id=9402,
                action="update", idempotency_key=f"ok:{ev2}",
                memory_values={"content": "authorized new"}, grant_token=token))
            await db.commit()
            mid3, _ = await chat_store._persist_user_message(9422, 9402, 9412, "source three")
            ev3 = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"),
                                    {"m": mid3})).scalar_one()
            reuse = await db.run_sync(lambda sd: se.write_authoritative_memory(
                sd.connection(), event_uid=ev3, memory_id=mem.id,
                owner_user_id=9402, character_id=9412,
                actor_kind="authenticated_user", actor_user_id=9402,
                action="update", idempotency_key=f"reuse:{ev3}",
                memory_values={"content": "reuse should fail"}, grant_token=token))
            await db.commit()
        return old_result, first, reuse

    old_result, first, reuse = asyncio.run(run())
    assert old_result == "refused_stale_source"
    assert first == "accepted"
    assert reuse == "refused_bad_grant"


