# -*- coding: utf-8 -*-
"""M2 2b 切片 2（a2）：感知来源锚定「本轮注入批次」合同（2026-11-04）。

覆盖：正例（同轮消息＝批次 id、事件幂等复用、权威 receipt、来源锚点写入）、
反例（无快照/正文不重合/会话越权/flag 关零行为变化）。
"""
import asyncio

import pytest
from sqlalchemy import text


async def _seed(uid=9601, cid=9611, sid=9621):
    """用户/角色/会话 + 一条用户消息；返回 message_id。"""
    from test_memory_source_queue_m2b import _seed_and_message
    return await _seed_and_message(uid, cid, sid)


async def _add_snapshot(user_id: int, content: str, source: str = "accessibility") -> None:
    from app.db.database import async_session_factory
    from app.utils.timeutil import now_naive_utc

    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT INTO phone_snapshots(user_id, source, content, created_at) "
            "VALUES (:u, :s, :c, :t)"),
            {"u": user_id, "s": source, "c": content, "t": now_naive_utc()})
        await db.commit()


def test_perception_bind_uses_turn_message_as_batch(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    uid, cid, sid = 9601, 9611, 9621
    snap_text = "微信：张三 明天下午三点开会 记得带材料"

    async def run():
        msg = await _seed(uid, cid, sid)
        await _add_snapshot(uid, snap_text)
        mem = await save_memory(user_id=uid, character_id=cid, memory_type="event",
                                content="明天下午三点要开会，记得带材料", importance=2,
                                source="perception", source_id=msg)
        async with async_session_factory() as db:
            ev = (await db.execute(text(
                "SELECT source_event_uid FROM memories WHERE id=:m"),
                {"m": mem.id})).scalar_one()
            kind = (await db.execute(text(
                "SELECT kind FROM memory_source_events WHERE event_uid=:e"),
                {"e": ev})).scalar_one()
            receipts = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_write_receipts WHERE memory_id=:m "
                "AND authoritative=1 AND source_event_uid=:e"),
                {"m": mem.id, "e": ev})).scalar_one()
            events = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_source_events WHERE kind='perception_batch' "
                "AND owner_user_id=:u"), {"u": uid})).scalar_one()
        return mem, ev, kind, receipts, events, msg

    mem, ev, kind, receipts, events, msg = asyncio.run(run())
    assert mem is not None
    assert ev and ev.startswith("mse-")
    assert kind == "perception_batch"
    assert receipts == 1, "必须落权威 receipt"
    assert events == 1, "每轮只应签发一条批次事件"


def test_perception_event_reused_within_same_turn(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    uid, cid, sid = 9602, 9612, 9622

    async def run():
        msg = await _seed(uid, cid, sid)
        await _add_snapshot(uid, "相册：昨天拍的公园照片 有湖和长椅")
        m1 = await save_memory(user_id=uid, character_id=cid, memory_type="event",
                               content="昨天拍的公园照片很好看", source="perception", source_id=msg)
        m2 = await save_memory(user_id=uid, character_id=cid, memory_type="event",
                               content="有湖和长椅，还有一座小桥", source="perception", source_id=msg)
        async with async_session_factory() as db:
            ev1 = (await db.execute(text("SELECT source_event_uid FROM memories WHERE id=:m"),
                                    {"m": m1.id})).scalar_one()
            ev2 = (await db.execute(text("SELECT source_event_uid FROM memories WHERE id=:m"),
                                    {"m": m2.id})).scalar_one()
            total = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_source_events WHERE kind='perception_batch' "
                "AND owner_user_id=:u"), {"u": uid})).scalar_one()
        return ev1, ev2, total

    ev1, ev2, total = asyncio.run(run())
    assert ev1 == ev2, "同一轮的多次派生应复用同一条批次事件"
    assert total == 1


def test_perception_refused_without_matching_snapshot(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    uid, cid, sid = 9603, 9613, 9623

    async def run():
        msg = await _seed(uid, cid, sid)
        await _add_snapshot(uid, "剪贴板：一段完全无关的购物清单")
        try:
            await save_memory(user_id=uid, character_id=cid, memory_type="event",
                              content="明天下午三点要开会", source="perception", source_id=msg)
        except RuntimeError as exc:
            return str(exc)
        return None

    assert asyncio.run(run()) == "source_binding_missing"


def test_perception_refused_association_with_foreign_session(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg = await _seed(9604, 9614, 9624)
        await _add_snapshot(9604, "微信：明天下午三点开会")
        # 用别人的 body 去挂别人的会话 ⇒ 归属复核必须拒绝
        try:
            await save_memory(user_id=9605, character_id=9614, memory_type="event",
                              content="明天下午三点开会", source="perception", source_id=msg)
        except RuntimeError as exc:
            return str(exc)
        return None

    assert asyncio.run(run()) == "source_binding_missing"


def test_perception_flag_off_keeps_legacy_behavior(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", False)

    async def run():
        msg = await _seed(9606, 9616, 9626)
        await _add_snapshot(9606, "微信：明天下午三点开会")
        mem = await save_memory(user_id=9606, character_id=9616, memory_type="event",
                                content="明天下午三点开会", source="perception", source_id=msg)
        async with async_session_factory() as db:
            ev = (await db.execute(text("SELECT source_event_uid FROM memories WHERE id=:m"),
                                   {"m": mem.id})).scalar_one()
            total = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_source_events WHERE kind='perception_batch' "
                "AND owner_user_id=:u"), {"u": 9606})).scalar_one()
        return mem, ev, total

    mem, ev, total = asyncio.run(run())
    assert mem is not None and ev is None, "flag 关时不得绑定来源"
    assert total == 0, "flag 关时不得签发事件"
