# -*- coding: utf-8 -*-
"""引擎平局语义（has_draw_semantics）口径测试（2026-09-19 复检 P3-③）。

只读复核结论：6 款内置引擎中**只有 twenty_q / turtle_soup 不存在任何平局结局**
（check_winner 白名单只允许 guesser/thinker、终局由单值 phase_result 二选一、gm 模板无 draw 键）；
其余四款（werewolf / undercover / liars_bar / truth_or_dare）都有显式 return "draw" 路径，必须保持 True。

本文件锁定两件事：
1. 6 款引擎的类属性取值矩阵（防后续改动把默认 True 误改成 False，或给无平局引擎留 True）；
2. 分流口径：has_draw_semantics=False 时护栏末级止血走 abort（_abort_game）而非记平局（_settle_game("draw")）。

纯内存 + 桩，不连库、不调 LLM。
"""
import asyncio

from app.api import games as games_api
from app.games.liars_bar import LiarsBarEngine
from app.games.truth_or_dare import TruthOrDareEngine
from app.games.turtle_soup import TurtleSoupEngine
from app.games.twenty_q import TwentyQEngine
from app.games.undercover import UndercoverEngine
from app.games.werewolf import WerewolfEngine

# 期望矩阵：False=本引擎无平局语义（护栏止血走 abort）；True=支持平局（维持 settle(draw)）
EXPECTED = {
    "twenty_q": False,
    "turtle_soup": False,
    "werewolf": True,
    "undercover": True,
    "liars_bar": True,
    "truth_or_dare": True,
}

CLASSES = {
    "twenty_q": TwentyQEngine,
    "turtle_soup": TurtleSoupEngine,
    "werewolf": WerewolfEngine,
    "undercover": UndercoverEngine,
    "liars_bar": LiarsBarEngine,
    "truth_or_dare": TruthOrDareEngine,
}


def test_class_attr_matrix():
    """6 款内置引擎：只有 twenty_q / turtle_soup 为 False，其余四款必须 True。"""
    for game_type, cls in CLASSES.items():
        assert getattr(cls, "has_draw_semantics") is EXPECTED[game_type], game_type
        # 类属性（非实例覆盖）：构造后取值一致，且 isinstance 视角同样可见
        assert cls(None).has_draw_semantics is EXPECTED[game_type], game_type

    no_draw = sorted(gt for gt, want in EXPECTED.items() if not want)
    assert no_draw == ["turtle_soup", "twenty_q"]


def test_registry_builtins_covered():
    """注册表里的内置引擎都被上表覆盖（新增引擎漏配会在这里失败）。"""
    from app.games.registry import _GAME_META

    builtin = {gt for gt in _GAME_META if gt in EXPECTED}
    for gt in builtin:
        assert getattr(CLASSES[gt], "has_draw_semantics") is EXPECTED[gt], gt
    assert builtin == set(EXPECTED), f"内置引擎集变化，需同步 EXPECTED: {sorted(_GAME_META)}"


def test_guard_stop_routes_by_flag(monkeypatch):
    """分流口径：False → _abort_game（无胜负终止）；True → _settle_game("draw")。"""
    calls: list[tuple] = []

    async def _fake_settle(db, session, engine, winner):
        calls.append(("settle", winner))

    async def _fake_abort(db, session, engine, reason):
        calls.append(("abort", reason))

    monkeypatch.setattr(games_api, "_settle_game", _fake_settle)
    monkeypatch.setattr(games_api, "_abort_game", _fake_abort)

    async def _run():
        # 无平局语义引擎：绝不伪造平局
        await games_api._guard_stop(None, None, TwentyQEngine(None), "guard_abort_draw")
        await games_api._guard_stop(None, None, TurtleSoupEngine(None), "guard_abort_draw")
        # 有平局语义引擎：维持原口径
        await games_api._guard_stop(None, None, WerewolfEngine(None), "guard_abort_draw")

    asyncio.run(_run())

    assert calls == [
        ("abort", "guard_abort_draw"),
        ("abort", "guard_abort_draw"),
        ("settle", "draw"),
    ]
