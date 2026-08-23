# -*- coding: utf-8 -*-
"""P1 测试：反思驱动（ai_reflection 注入主动消息，flag 控制）"""
import asyncio

from app.agent import loop
from app.scheduler import message_generator as mg


def test_load_recent_reflection_flag关不查库(monkeypatch):
    def _boom():
        raise AssertionError("flag 关不应查 DB")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    loop.AGENT_FLAGS["agent_reflection_inject"] = False
    try:
        r = asyncio.run(mg._load_recent_reflection(11))
    finally:
        loop.AGENT_FLAGS["agent_reflection_inject"] = True
    assert r == ""


def test_load_recent_reflection_无角色返回空():
    r = asyncio.run(mg._load_recent_reflection(None))
    assert r == ""


def test_load_recent_reflection_异常静默(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    loop.AGENT_FLAGS["agent_reflection_inject"] = True
    try:
        r = asyncio.run(mg._load_recent_reflection(11))
    finally:
        loop.AGENT_FLAGS["agent_reflection_inject"] = True
    assert r == ""
