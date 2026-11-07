# -*- coding: utf-8 -*-
"""M3b 合同：动机分 tick 快照（复用 M3a 的 `tick_cache` 与 `proactive_tick_cache` flag）。

覆盖：flag 关逐次计算、tick 内同角色只算一次、不同角色分开、跨 tick 重算、无 tick 上下文不缓存、
三处调用点都经过包装函数（接线棘轮）。
"""
import asyncio
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def _patch_uncached(monkeypatch):
    from app.scheduling import arbiter

    calls = []

    async def fake(character_id: int) -> float:
        calls.append(character_id)
        return float(character_id) * 10.0

    monkeypatch.setattr(arbiter, "_compute_motivation_uncached", fake)
    return calls


def test_flag_off_computes_every_time(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import arbiter, tick_cache

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, False)
    calls = _patch_uncached(monkeypatch)

    async def run():
        with tick_cache.tick():
            await arbiter._compute_motivation(7)
            await arbiter._compute_motivation(7)

    asyncio.run(run())
    assert calls == [7, 7], "flag 关必须逐次计算（历史行为）"


def test_flag_on_snapshot_within_tick(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import arbiter, tick_cache

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, True)
    calls = _patch_uncached(monkeypatch)

    async def run():
        with tick_cache.tick():
            a = await arbiter._compute_motivation(7)
            b = await arbiter._compute_motivation(7)
            c = await arbiter._compute_motivation(8)
            d = await arbiter._compute_motivation(8)
            s = tick_cache.stats()
        return a, b, c, d, s

    a, b, c, d, stats = asyncio.run(run())
    assert calls == [7, 8], f"同 tick 内每个角色只算一次，实际 {calls}"
    assert a == b == 70.0 and c == d == 80.0
    assert stats.get("motivation:7#hits") == 1 and stats.get("motivation:8#hits") == 1


def test_new_tick_recomputes(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import arbiter, tick_cache

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, True)
    calls = _patch_uncached(monkeypatch)

    async def run():
        with tick_cache.tick():
            await arbiter._compute_motivation(7)
        with tick_cache.tick():
            await arbiter._compute_motivation(7)

    asyncio.run(run())
    assert calls == [7, 7], "跨 tick 必须重算（不得复用上一拍分数）"


def test_without_tick_context_no_cache(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import arbiter, tick_cache

    monkeypatch.setitem(AGENT_FLAGS, tick_cache.FLAG, True)
    calls = _patch_uncached(monkeypatch)

    async def run():
        await arbiter._compute_motivation(7)
        await arbiter._compute_motivation(7)

    asyncio.run(run())
    assert calls == [7, 7], "无 tick 上下文不得缓存"


def test_call_sites_go_through_wrapper():
    src = (APP / "scheduling" / "arbiter.py").read_text(encoding="utf-8")
    assert "async def _compute_motivation_uncached(" in src
    assert 'tick_cache.cached(\n        "motivation:%d"' in src or "motivation:%d" in src
    # 三个调用点都调包装函数（不是 uncached）
    assert src.count("await _compute_motivation(char_id)") == 1
    assert "_compute_motivation_uncached(" in src
    mot = (APP / "scheduling" / "sources" / "motivation.py").read_text(encoding="utf-8")
    assert mot.count("arbiter._compute_motivation(") == 2, "关系门与让位路径都走包装"
