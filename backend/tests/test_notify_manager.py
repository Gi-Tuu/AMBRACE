# -*- coding: utf-8 -*-
# 用户级通知 WebSocket 连接池（#55 后台保活）+ 通知 WS 端点鉴权测试
import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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


def test_ws_bad_token_closed():
    client = _make_client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/system/notifications/ws?token=bad"):
            pass


def test_ws_good_token_connected_and_pong():
    from app.auth.config import create_token

    token = create_token(1)
    client = _make_client()
    with client.websocket_connect(f"/api/v1/system/notifications/ws?token={token}") as ws:
        data = ws.receive_json()
        assert data["type"] == "connected"
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
