# -*- coding: utf-8 -*-
"""P1 测试：反思驱动（ai_reflection 注入主动消息，flag 控制）"""
import asyncio

from app.agent import loop
from app.scheduling import message_generator as mg


def test_load_recent_reflection_固化常开_恒尝试加载(monkeypatch):
    """agent_reflection_inject 已固化常开（2026-09-17 用户拍板）：不再有 flag 短路，恒尝试加载最近复盘。"""
    from app.agent import loop as _loop
    assert "agent_reflection_inject" not in _loop.AGENT_FLAGS
    # 故障静默仍保留：DB 异常返回空串（验证已无 flag 短路、真正走到 DB）
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    r = asyncio.run(mg._load_recent_reflection(11))
    assert r == ""


def test_load_recent_reflection_无角色返回空():
    r = asyncio.run(mg._load_recent_reflection(None))
    assert r == ""


def test_load_recent_reflection_异常静默(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    r = asyncio.run(mg._load_recent_reflection(11))
    assert r == ""  # 故障静默（固化常开后仍无 flag 短路）
