# -*- coding: utf-8 -*-
# 用户级通知 WebSocket 连接池（#55 后台保活）+ 通知 WS 端点鉴权测试
import asyncio
import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from _dbclone import clone_engine, make_session_factory

from app.api import system as system_api
from app.ws import notify_manager


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def test_register_push_unregister():
    async def run():
        ws = _FakeWs()
        await notify_manager.register(1, ws)
        assert notify_manager.connection_count(1) == 1
        ok = await notify_manager.push_to_user(1, {"type": "ai_response"})
        assert ok is True
        assert ws.sent == [{"type": "ai_response"}]
        await notify_manager.unregister(1, ws)
        assert notify_manager.connection_count(1) == 0
        return True

    assert asyncio.run(run()) is True


def test_push_offline_returns_false():
    async def run():
        return await notify_manager.push_to_user(999, {"type": "ai_response"})

    assert asyncio.run(run()) is False


def test_push_skips_dead_socket():
    class _Dead:
        def __init__(self):
            self.sent = []

        async def send_json(self, data):
            raise RuntimeError("closed")

    async def run():
        dead = _Dead()
        alive = _FakeWs()
        await notify_manager.register(1, dead)
        await notify_manager.register(1, alive)
        ok = await notify_manager.push_to_user(1, {"type": "x"})
        assert ok is True
        assert alive.sent == [{"type": "x"}]
        assert notify_manager.connection_count(1) == 1
        return True

    assert asyncio.run(run()) is True


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    return TestClient(app)


@pytest.fixture
def notify_ws_db(monkeypatch, tmp_path):
    """用例自足的临时库（模板库克隆，NullPool ⇒ 跨事件循环安全）＋ 播种 users(id=1, 未禁用)。

    P2-7（2026-09-28）给通知 WS 加了「账号存在且未禁用」的库判据后，本文件的 good-token 用例
    原先默认「会话共享库里恰好有 id=1 用户」：**单跑必红**（新进程的沙箱库没有这行）、全量/CI
    下则取决于同一 worker 前序用例是否建过 id=1 —— py3.13 全量档实测就是它把 CI 打红
    （WebSocketDisconnect code=4403）。这里照 test_liveness_endpoint.py 的做法：把端点用的
    会话工厂 patch 到本用例私有库，只播本用例需要的那一行，不依赖执行顺序、不碰 backend/data。
    """
    from app.models.user import User

    db_path = os.path.join(str(tmp_path), "t.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _seed():
        async with factory() as db:
            db.add(User(id=1, username="notify_user", nickname="通知用户", password_hash="x"))
            await db.commit()

    asyncio.run(_seed())
    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def test_ws_bad_token_closed():
    client = _make_client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/system/notifications/ws?token=bad"):
            pass


def test_ws_good_token_connected_and_pong(notify_ws_db):
    """合法 token 能建连并回 pong。

    依赖 notify_ws_db：端点要按 users 表判「账号存在且未禁用」（P2-7），本用例自带 id=1 那行，
    不再靠「会话共享库里恰好有 id=1」这种执行顺序前提。
    """
    from app.auth.config import create_token

    token = create_token(1)
    client = _make_client()
    with client.websocket_connect(f"/api/v1/system/notifications/ws?token={token}") as ws:
        data = ws.receive_json()
        assert data["type"] == "connected"
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
