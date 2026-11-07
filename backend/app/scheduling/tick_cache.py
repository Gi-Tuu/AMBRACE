# -*- coding: utf-8 -*-
"""M3a：每 tick 一次的角色集合缓存（flag `proactive_tick_cache` 默认关）。

背景（B 路核查）：`arbiter.get_active_characters()` 每个 tick 被 ≥5 次调用，每次都是全量联表。
做法：在 `triggers.get_active_characters` 外面套一层「tick 内缓存」，由 `arbiter.run_tick` 开 tick 上下文；
flag 关 / 不在 tick 上下文 ⇒ 逐次直查（历史行为逐字节不变）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

FLAG = "proactive_tick_cache"
_CACHE: ContextVar[dict | None] = ContextVar("proactive_tick_cache", default=None)


def enabled() -> bool:
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(FLAG, False))
    except Exception:  # noqa: BLE001 - 取数失败按旧行为（不缓存）
        return False


@contextmanager
def tick():
    """开一个 tick 上下文：期间同任务的重复查询走缓存。"""
    token = _CACHE.set({})
    try:
        yield
    finally:
        _CACHE.reset(token)


def stats() -> dict:
    """观测用：当前 tick 内缓存命中统计（无 tick 上下文时返回空）。"""
    cache = _CACHE.get()
    return dict(cache or {})


async def cached(key: str, loader):
    """按 key 取 tick 内缓存；flag 关或无 tick 上下文时直接 loader()。"""
    cache = _CACHE.get()
    if not enabled() or cache is None:
        return await loader()
    if key in cache:
        cache[f"{key}#hits"] = int(cache.get(f"{key}#hits", 0)) + 1
        return cache[key]
    value = await loader()
    cache[key] = value
    return value
