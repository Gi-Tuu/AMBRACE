# -*- coding: utf-8 -*-
"""插件 hook 超时门禁测试（Phase A，2026-08-16）：超时中断并忽略返回值，不阻断主链路"""
import asyncio
import time

from app.plugins import registry


def _fake_entry(name: str, hooks: dict):
    registry._loaded[name] = {"info": {"name": name}, "hooks": hooks, "actions": {}, "router": None}
    registry._enabled[name] = True


def test_hook_异步超时门禁():
    async def slow_hook(ctx):
        await asyncio.sleep(30)
        return "late"

    _fake_entry("_slow_test", {"context_inject": [slow_hook]})
    try:
        t0 = time.monotonic()
        asyncio.run(registry.run_hook("context_inject", {}, timeout=0.05))
        assert time.monotonic() - t0 < 2, "异步 hook 超时应快速返回"
    finally:
        registry._loaded.pop("_slow_test", None)
        registry._enabled.pop("_slow_test", None)


def test_hook_异步超时_收集忽略返回值():
    async def slow_hook(ctx):
        await asyncio.sleep(30)
        return "late"

    _fake_entry("_slow_collect", {"proactive_candidate": [slow_hook]})
    try:
        t0 = time.monotonic()
        results = asyncio.run(registry.run_hook_collect("proactive_candidate", {}, timeout=0.05))
        assert results == [], "超时的 hook 返回值应被忽略"
        assert time.monotonic() - t0 < 2
    finally:
        registry._loaded.pop("_slow_collect", None)
        registry._enabled.pop("_slow_collect", None)


def test_hook_同步超时门禁():
    def slow_sync(ctx):
        time.sleep(0.3)
        return {"late": True}

    _fake_entry("_slow_sync", {"context_inject": [slow_sync]})
    try:
        t0 = time.monotonic()
        results = asyncio.run(registry.run_hook_collect("context_inject", {}, timeout=0.05))
        assert results == [], "同步 hook 超时返回值应被忽略"
        assert time.monotonic() - t0 < 2, "同步 hook 丢线程池执行，主流程不应被拖住"
    finally:
        registry._loaded.pop("_slow_sync", None)
        registry._enabled.pop("_slow_sync", None)
