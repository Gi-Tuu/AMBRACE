# -*- coding: utf-8 -*-
"""B-WS 回归测试（2026-09-01 审查）：聊天 WS 连接池身份守卫。

事故：连接池"无条件覆盖注册 + 无条件 pop"——弱网下旧半开连接晚死时，
其注销逻辑会把新活连接挤出池，服务端推送静默失效。
断言：_unregister_ws 只允许"当前连接"注销自己；旧连接注销不误删新连接。
"""
from fastapi import WebSocket

from app.api.chat import _unregister_ws, connected_clients


class _FakeWs:
    """最小假连接（身份比较用，不需要真实收发）。"""

    def __init__(self, sid: str):
        self.id = sid


def test_unregister_removes_only_itself():
    key = 777
    old_ws = _FakeWs("old")
    new_ws = _FakeWs("new")
    connected_clients[key] = old_ws
    try:
        # 旧连接注销时字典里已是新连接 → 不误删
        connected_clients[key] = new_ws
        _unregister_ws(key, old_ws)
        assert connected_clients.get(key) is new_ws, "旧连接注销不得误删新连接"

        # 新连接注销自己 → 正常移除
        _unregister_ws(key, new_ws)
        assert key not in connected_clients
    finally:
        connected_clients.pop(key, None)


def test_unregister_noop_when_absent():
    key = 778
    ws = _FakeWs("solo")
    # 字典里没有它 → 注销是 no-op（不抛、不误删他人）
    connected_clients[key] = _FakeWs("other")
    try:
        _unregister_ws(key, ws)
        assert connected_clients.get(key) is not None
    finally:
        connected_clients.pop(key, None)


def test_register_replaces_and_unregister_keeps_new():
    """模拟弱网时序：A 半开 → B 重连覆盖 → A 晚死注销 → B 必须仍在池中。"""
    key = 779
    a, b = WebSocket(scope={"type": "websocket"}, receive=None, send=None), _FakeWs("b")
    # 用假对象代替真实 WebSocket 注册（身份语义与真实一致）
    a = _FakeWs("a")
    connected_clients[key] = a
    try:
        # 重连：B 覆盖（生产代码此时会主动 close A）
        connected_clients[key] = b
        # A 稍后才断开 → 注销自己
        _unregister_ws(key, a)
        assert connected_clients.get(key) is b, "新活连接必须保留"
    finally:
        connected_clients.pop(key, None)
