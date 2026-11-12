# -*- coding: utf-8 -*-
"""M2 2b 真实 WebSocket 入口：签名 token、真实路由、同事务来源、失败回滚。"""
import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import text

from app.api import chat as chat_api
from app.auth.config import create_token


async def _seed(u=9301, c=9311, s=9321):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text("INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"), {"u": u, "n": f"u{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": c, "u": u})
        await db.execute(text("INSERT OR IGNORE INTO chat_sessions(id,user_id,character_id,is_active) VALUES (:s,:u,:c,1)"),
                         {"s": s, "u": u, "c": c})
        await db.commit()


def _client():
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _fake_chunked(store, fail=None):
    async def fake(*, session_id, user_id, character_id, content, **kw):
        mid, _ = await store._persist_user_message(session_id, user_id, character_id, content, save_user_message=True)
        return {"chunks": [], "memories_updated": False,
                "user_message": {"id": mid, "session_id": session_id,
                                 "sender_type": "user", "content": content}}
    return fake


async def _counts(session_id):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        msgs = (await db.execute(text("SELECT COUNT(*) FROM chat_messages WHERE session_id=:s"), {"s": session_id})).scalar_one()
        srcs = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE session_id=:s"), {"s": session_id})).scalar_one()
    return msgs, srcs


def _recv_user(ws):
    for _ in range(6):
        data = ws.receive_json()
        if data.get("type") == "user_message":
            return data
    raise AssertionError("no user_message event")


def test_ws_user_message_signs_source(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_chunked", _fake_chunked(chat_store))
    asyncio.run(_seed())
    token = create_token(9301)
    with _client().websocket_connect(f"/api/v1/chat/ws/9321?token={token}") as ws:
        ws.send_json({"type": "chat_message", "character_id": 9311, "content": "WS 路由测试"})
        data = _recv_user(ws)
        mid = data["data"]["id"]
    async def check():
        async with async_session_factory() as db:
            uid = (await db.execute(text("SELECT source_event_uid FROM chat_messages WHERE id=:m"), {"m": mid})).scalar_one()
            ev = (await db.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE event_uid=:u"), {"u": uid})).scalar_one() if uid else 0
        return uid, ev
    uid, ev = asyncio.run(check())
    assert uid and ev == 1


def test_ws_sign_failure_rolls_back(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.memory import source_binding

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_chunked", _fake_chunked(chat_store))
    calls = []

    async def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("ws_sign_failure")

    monkeypatch.setattr(source_binding, "sign_chat_message", boom)
    asyncio.run(_seed(9302, 9312, 9322))
    # 增量口径（2026-11-08 盘点统一）：共享沙箱防固定 ID 残留撞假红
    _before_9322 = asyncio.run(_counts(9322))[:2]
    token = create_token(9302)
    with _client().websocket_connect(f"/api/v1/chat/ws/9322?token={token}") as ws:
        ws.send_json({"type": "chat_message", "character_id": 9312, "content": "应回滚"})
        # 端点捕获异常后发 typing=false / 关闭；读到任意消息即停
        for _ in range(6):
            try:
                data = ws.receive_json()
            except WebSocketDisconnect:
                break
            if data.get("type") == "error":
                break
    assert calls == [1]
    # 增量口径（2026-11-08 盘点统一）：共享沙箱防固定 ID 残留撞假红
    assert asyncio.run(_counts(9322))[:2] == _before_9322


def test_ws_wrong_owner_denied(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_chunked", _fake_chunked(chat_store))
    asyncio.run(_seed(9303, 9313, 9323))
    asyncio.run(_seed(9304, 9314, 9324))
    token = create_token(9303)
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client().websocket_connect(f"/api/v1/chat/ws/9324?token={token}") as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_ws_invalid_token_denied(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    asyncio.run(_seed(9305, 9315, 9325))
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client().websocket_connect("/api/v1/chat/ws/9325?token=bad") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def _recv_user_batch(ws, n):
    """batch 分支每条消息回传一个 user_message（另有 typing 事件穿插），收集 n 个。"""
    out = []
    for _ in range(24):
        data = ws.receive_json()
        if data.get("type") == "user_message":
            out.append(data["data"])
            if len(out) == n:
                return out
    raise AssertionError(f"user_message 只收到 {len(out)}/{n}")


def test_ws_batch_messages_sign_sources(monkeypatch):
    """batch_messages 连发组逐条签名（2026-11-07 补接：生产连发组 5/90 未签的缺口）。"""
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    asyncio.run(_seed(9306, 9316, 9326))
    token = create_token(9306)
    with _client().websocket_connect(f"/api/v1/chat/ws/9326?token={token}") as ws:
        ws.send_json({"type": "batch_messages", "character_id": 9316,
                      "messages": ["连发一", "连发二", "连发三"]})
        infos = _recv_user_batch(ws, 3)

    async def check():
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            signed = 0
            for i in infos:
                u = (await db.execute(text(
                    "SELECT source_event_uid FROM chat_messages WHERE id=:m"),
                    {"m": i["id"]})).scalar_one()
                if u:
                    signed += 1
            evs = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_source_events WHERE session_id=:s"),
                {"s": 9326})).scalar_one()
        return signed, evs

    signed, evs = asyncio.run(check())
    assert signed == 3, f"连发组 3 条必须全部签名，实际 {signed}"
    assert evs == 3


def test_ws_batch_sign_failure_rolls_back(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import source_binding

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []

    async def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("batch_sign_failure")

    monkeypatch.setattr(source_binding, "sign_chat_message", boom)
    asyncio.run(_seed(9307, 9317, 9327))
    # 增量口径（2026-11-08 盘点统一）：同上，防共享沙箱残留撞假红
    _before_9327 = asyncio.run(_counts(9327))[:2]
    token = create_token(9307)
    with _client().websocket_connect(f"/api/v1/chat/ws/9327?token={token}") as ws:
        ws.send_json({"type": "batch_messages", "character_id": 9317,
                      "messages": ["应回滚一", "应回滚二"]})
        # 签名失败 ⇒ 整组事务回滚；端点捕获后发 error/typing 或断开
        for _ in range(8):
            try:
                data = ws.receive_json()
            except WebSocketDisconnect:
                break
            if data.get("type") in ("error",):
                break
    assert calls == [1]
    assert asyncio.run(_counts(9327))[:2] == _before_9327


def test_ws_user_level_override_blocks_signing(monkeypatch):
    """用户级灰度（2026-11-07）：user_runtime_flags 显式 OFF ⇒ 即使全局开也不签（resolve_flag 链）。"""
    from app.flags.agent_flags import AGENT_FLAGS
    from app.application import chat_store
    from app.db.database import async_session_factory
    from app.models.config import UserRuntimeFlag

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setattr(chat_api, "send_and_receive_chunked", _fake_chunked(chat_store))
    asyncio.run(_seed(9308, 9318, 9328))

    async def _override():
        from sqlalchemy import delete as _delete
        async with async_session_factory() as db:
            await db.execute(_delete(UserRuntimeFlag).where(
                UserRuntimeFlag.user_id == 9308,
                UserRuntimeFlag.key == "memory_source_proof_v1"))
            db.add(UserRuntimeFlag(user_id=9308, key="memory_source_proof_v1", enabled=False))
            await db.commit()
            from app.application.flag_service import resolve_flag
            print("[dbg] resolve:", await resolve_flag("memory_source_proof_v1", 9308))
    asyncio.run(_override())

    token = create_token(9308)
    from app.memory import source_binding as _sb
    from app.application import chat_store as _cs
    from app.application import flag_service as _fs
    _orig_rflag = _fs.resolve_flag

    async def _spy_rflag(key, user_id=None):
        try:
            v = await _orig_rflag(key, user_id=user_id)
            print(f"[dbg] resolve_flag({key}, {user_id}) -> {v}", flush=True)
            return v
        except Exception as e:
            print(f"[dbg] resolve_flag RAISED {e!r}", flush=True)
            raise
    _fs.resolve_flag = _spy_rflag
    _ef_calls = []
    _orig_ef = _sb.enabled_for

    async def _spy_ef(uid):
        v = await _orig_ef(uid)
        _ef_calls.append((uid, v))
        print(f"[dbg] enabled_for({uid}) -> {v}", flush=True)
        return v
    _sb.enabled_for = _spy_ef
    # 关键：chat_store 里的 enabled_for 是**函数内 from-import**，函数对象持有的引用在调用时
    # 才解析模块属性 ⇒ 替换 _sb.enabled_for 即生效；这里再钉一道自检。
    with _client().websocket_connect(f"/api/v1/chat/ws/9328?token={token}") as ws:
        ws.send_json({"type": "chat_message", "character_id": 9318, "content": "用户级关闭"})
        data = _recv_user(ws)
    _sb.enabled_for = _orig_ef
    print("[dbg] ef_calls:", _ef_calls, flush=True)
    mid = data["data"]["id"]

    async def check():
        async with async_session_factory() as db:
            return (await db.execute(text(
                "SELECT source_event_uid FROM chat_messages WHERE id=:m"), {"m": mid})).scalar_one()
    assert asyncio.run(check()) is None, "用户级 OFF 必须不签名"
