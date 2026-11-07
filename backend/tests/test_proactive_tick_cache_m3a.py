# -*- coding: utf-8 -*-
"""M3a 合同：active 角色集合的每 tick 缓存（flag `proactive_tick_cache` 默认关）。

覆盖：flag 关逐次直查、flag 开 tick 内只查一次、跨 tick 重新加载、无 tick 上下文不缓存、
arbiter.run_tick 确实开 tick 上下文（源码棘轮）。
"""
import asyncio
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def _patch_loader(monkeypatch):
    from app.scheduling import triggers

    calls = {"n": 0}

    async def fake():
        calls["n"] += 1
        return [{"character_id": 1, "user_id": 1, "name": "c"}]

    monkeypatch.setattr(triggers, "_load_active_characters", fake)
    return calls


def test_flag_off_queries_every_time(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import tick_cache, triggers

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, False)
    calls = _patch_loader(monkeypatch)

    async def run():
        with tick_cache.tick():
            await triggers.get_active_characters()
            await triggers.get_active_characters()

    asyncio.run(run())
    assert calls["n"] == 2, "flag 关必须逐次直查（历史行为）"


def test_flag_on_caches_within_tick(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import tick_cache, triggers

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, True)
    calls = _patch_loader(monkeypatch)

    async def run():
        with tick_cache.tick():
            a = await triggers.get_active_characters()
            b = await triggers.get_active_characters()
            s = tick_cache.stats()
        return a, b, s

    a, b, stats = asyncio.run(run())
    assert calls["n"] == 1, f"同一 tick 内应只查一次，实际 {calls['n']}"
    assert a == b and a[0]["character_id"] == 1
    assert stats.get("active_characters#hits") == 1


def test_flag_on_new_tick_reloads(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import tick_cache, triggers

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, True)
    calls = _patch_loader(monkeypatch)

    async def run():
        with tick_cache.tick():
            await triggers.get_active_characters()
        with tick_cache.tick():
            await triggers.get_active_characters()

    asyncio.run(run())
    assert calls["n"] == 2, "新 tick 必须重新加载（不能跨 tick 复用旧集合）"


def test_flag_on_without_tick_context_does_not_cache(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import tick_cache, triggers

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, True)
    calls = _patch_loader(monkeypatch)

    async def run():
        await triggers.get_active_characters()
        await triggers.get_active_characters()

    asyncio.run(run())
    assert calls["n"] == 2, "无 tick 上下文时不得缓存（避免跨 tick 脏读）"


def test_run_tick_wraps_inner_with_tick_context():
    src = (APP / "scheduling" / "arbiter.py").read_text(encoding="utf-8")
    i = src.index("async def run_tick(")
    window = src[i:i + 400]
    assert "tick_cache.tick()" in window, "run_tick 必须开 tick 上下文"
    assert "_run_tick_inner()" in window
    trg = (APP / "scheduling" / "triggers.py").read_text(encoding="utf-8")
    assert 'tick_cache.cached("active_characters"' in trg, "查询点必须走缓存包装"
    assert "async def _load_active_characters(" in trg
