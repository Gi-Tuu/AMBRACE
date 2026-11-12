# -*- coding: utf-8 -*-
"""M2 2b 切片 1 合同：墓碑记忆的在线一段式重新授权（2026-11-04 用户拍板 A）。

覆盖：正例（reallow→确认事件→权威写回→active、grant 单次消费、权威 receipt、内容不变）、
幂等（二次调用不重复发 grant）、反例（未墓碑/未开闸/非本人/API 缺 confirm）。
"""
import asyncio

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from starlette.testclient import TestClient

from app.api import memories as memories_api
from app.auth.config import create_token


async def _seed(u=9501, c=9511, s=9521):
    from test_memory_source_queue_m2b import _seed_and_message
    return await _seed_and_message(u, c, s)


async def _tombstoned_memory(u=9501, c=9511, s=9521, content="synthetic reauth content"):
    """种一条 chat 来源记忆并 forget 成墓碑；返回 (memory_id, msg_id)。"""
    from app.memory import source_events as se
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    msg = await _seed(u, c, s)
    mem = await save_memory(user_id=u, character_id=c, memory_type="event",
                            content=content, source="chat", source_id=msg)
    async with async_session_factory() as db:
        await db.run_sync(lambda sd: se.forget_memory(
            sd.connection(), memory_id=mem.id, owner_user_id=u, character_id=c))
        await db.commit()
    return mem.id, msg


def _client():
    app = FastAPI()
    app.include_router(memories_api.router)
    return TestClient(app)


def _auth(uid):
    return {"Authorization": f"Bearer {create_token(uid)}"}


def test_reauthorize_positive_atomic(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.recovery import reauthorize_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    u, c = 9501, 9511

    async def run():
        memory_id, msg = await _tombstoned_memory(u, c, 9521, "synthetic reauth content")
        async with async_session_factory() as db:
            result = await reauthorize_memory(db, memory_id=memory_id, owner_user_id=u)
            await db.commit()
            state = (await db.execute(text(
                "SELECT state FROM memory_tombstones WHERE memory_id=:m"),
                {"m": memory_id})).scalar_one()
            content = (await db.execute(text(
                "SELECT content FROM memories WHERE id=:m"), {"m": memory_id})).scalar_one()
            used = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_reallow_grants WHERE memory_id=:m AND used=1"),
                {"m": memory_id})).scalar_one()
            receipts = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_write_receipts WHERE memory_id=:m "
                "AND authoritative=1 AND source_event_uid=:e"),
                {"m": memory_id, "e": result["source_event_uid"]})).scalar_one()
            kind = (await db.execute(text(
                "SELECT kind FROM memory_source_events WHERE event_uid=:e"),
                {"e": result["source_event_uid"]})).scalar_one()
            new_uid = (await db.execute(text(
                "SELECT source_event_uid FROM memories WHERE id=:m"),
                {"m": memory_id})).scalar_one()
        return result, state, content, used, receipts, kind, new_uid, msg

    result, state, content, used, receipts, kind, new_uid, msg = asyncio.run(run())
    assert result["state"] == "reallowed_active"
    assert state == "reallowed_active"
    assert content == "synthetic reauth content", "恢复不得改写业务内容"
    assert used == 1, "grant 必须恰好消费一次"
    assert receipts == 1, "必须落一条权威 receipt"
    assert kind == "user_reauthorization"
    assert new_uid == result["source_event_uid"], "记忆的来源锚点应换成确认事件"


def test_reauthorize_is_idempotent(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.recovery import reauthorize_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        memory_id, _ = await _tombstoned_memory(9502, 9512, 9522, "idem content")
        async with async_session_factory() as db:
            first = await reauthorize_memory(db, memory_id=memory_id, owner_user_id=9502)
            await db.commit()
            second = await reauthorize_memory(db, memory_id=memory_id, owner_user_id=9502)
            await db.commit()
            grants = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_reallow_grants WHERE memory_id=:m"),
                {"m": memory_id})).scalar_one()
        return first, second, grants

    first, second, grants = asyncio.run(run())
    assert first["state"] == "reallowed_active" and not first.get("idempotent")
    assert second.get("idempotent") is True
    assert grants == 1, "二次调用不得再签发 grant"


def test_reauthorize_refuses_not_tombstoned(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.recovery import RecoveryError, reauthorize_memory
    from app.memory.service import save_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        msg = await _seed(9503, 9513, 9523)
        mem = await save_memory(user_id=9503, character_id=9513, memory_type="event",
                                content="not tombstoned", source="chat", source_id=msg)
        async with async_session_factory() as db:
            try:
                await reauthorize_memory(db, memory_id=mem.id, owner_user_id=9503)
            except RecoveryError as exc:
                await db.rollback()
                return exc.code
        return None

    assert asyncio.run(run()) == "not_tombstoned"


def test_reauthorize_refuses_flag_off(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.recovery import RecoveryError, reauthorize_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", False)

    async def run():
        memory_id, _ = await _tombstoned_memory(9504, 9514, 9524, "flag off content")
        async with async_session_factory() as db:
            try:
                await reauthorize_memory(db, memory_id=memory_id, owner_user_id=9504)
            except RecoveryError as exc:
                await db.rollback()
                return exc.code
        return None

    # 注意：save_memory 在 flag 关时不绑定来源，故该记忆无来源；恢复前先看闸门是否先拒
    assert asyncio.run(run()) == "source_proof_disabled"


def test_reauthorize_refuses_other_user(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.recovery import RecoveryError, reauthorize_memory
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def run():
        memory_id, _ = await _tombstoned_memory(9505, 9515, 9525, "owner content")
        async with async_session_factory() as db:
            try:
                await reauthorize_memory(db, memory_id=memory_id, owner_user_id=9999)
            except RecoveryError as exc:
                await db.rollback()
                return exc.code
        return None

    assert asyncio.run(run()) == "memory_not_found"


def test_reauthorize_api_contract(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def seed():
        return await _tombstoned_memory(9506, 9516, 9526, "api content")

    memory_id, _ = asyncio.run(seed())
    client = _client()

    # 缺 confirm ⇒ 400
    r = client.post(f"/api/v1/memories/{memory_id}/reauthorize", json={}, headers=_auth(9506))
    assert r.status_code == 400
    # 非本人 ⇒ 404（不泄露存在性）
    r = client.post(f"/api/v1/memories/{memory_id}/reauthorize",
                    json={"confirm": True}, headers=_auth(9507))
    assert r.status_code == 404
    # 正常 ⇒ 200 + 状态
    r = client.post(f"/api/v1/memories/{memory_id}/reauthorize",
                    json={"confirm": True}, headers=_auth(9506))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["state"] == "reallowed_active"
    assert body["source_event_uid"].startswith("mse-")
    # 二次调用 ⇒ 幂等成功
    r2 = client.post(f"/api/v1/memories/{memory_id}/reauthorize",
                     json={"confirm": True}, headers=_auth(9506))
    assert r2.status_code == 200 and r2.json().get("idempotent") is True


def test_reauthorize_api_disabled_when_flag_off(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    memory_id, _ = asyncio.run(_tombstoned_memory(9508, 9518, 9528, "disabled content"))
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", False)
    r = _client().post(f"/api/v1/memories/{memory_id}/reauthorize",
                       json={"confirm": True}, headers=_auth(9508))
    assert r.status_code == 400
    assert "未开启" in r.json()["detail"], r.text
