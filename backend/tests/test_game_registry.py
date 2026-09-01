# -*- coding: utf-8 -*-
"""X1 游戏开放注册口测试：register_game_type 校验/来源过滤/卸载清理 + coin_flip 示例包加载与引擎冒烟"""
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1].parent

import pytest

from app.games import registry as gr
from app.games.base import GameEngine


# ── 注册口校验 ──


def test_builtin_six_registered():
    assert set(gr._GAME_SOURCE.values()) >= {"builtin"}
    for gt in ("undercover", "truth_or_dare", "twenty_q", "werewolf", "liars_bar", "turtle_soup"):
        assert gt in gr._REGISTRY and gr._GAME_SOURCE[gt] == "builtin"
        assert gr.engine_for(gt) is not None


def test_register_duplicate_raises():
    with pytest.raises(ValueError, match="already registered"):
        gr.register_game_type("undercover", gr.engine_for("twenty_q"), {
            "name": "重复", "player_mode": "dual", "min_players": 2,
            "max_players": 2, "needs_gm": False, "description": "x"})


def test_register_invalid_meta_raises():
    with pytest.raises(ValueError, match="meta"):
        gr.register_game_type("bad_meta", gr.engine_for("twenty_q"), {"name": "缺字段"})
    with pytest.raises(ValueError, match="player_mode"):
        gr.register_game_type("bad_mode", gr.engine_for("twenty_q"), {
            "name": "x", "player_mode": "solo", "min_players": 1,
            "max_players": 2, "needs_gm": False, "description": "x"})
    with pytest.raises(ValueError, match="game_type"):
        gr.register_game_type("Bad-Type!", gr.engine_for("twenty_q"), {
            "name": "x", "player_mode": "dual", "min_players": 1,
            "max_players": 2, "needs_gm": False, "description": "x"})
    with pytest.raises(ValueError, match="GameEngine"):
        gr.register_game_type("bad_cls", object, {
            "name": "x", "player_mode": "dual", "min_players": 1,
            "max_players": 2, "needs_gm": False, "description": "x"})


def test_plugin_source_enable_filter(monkeypatch):
    """插件来源游戏：插件启用→可用；停用→engine_for 拒绝且 list_games 隐藏"""

    class _Dummy(GameEngine):
        game_type = "dummy_pack"

    import app.plugins.registry as plugin_registry
    gr.register_game_type("dummy_pack", _Dummy, {
        "name": "假包", "player_mode": "dual", "min_players": 2,
        "max_players": 2, "needs_gm": False, "description": "测试"}, source="some_plugin")
    try:
        monkeypatch.setattr(plugin_registry, "_enabled", {"some_plugin": False})
        with pytest.raises(ValueError, match="unknown game_type"):
            gr.engine_for("dummy_pack")
        assert all(g["game_type"] != "dummy_pack" for g in gr.list_games())

        monkeypatch.setattr(plugin_registry, "_enabled", {"some_plugin": True})
        assert gr.engine_for("dummy_pack") is _Dummy
        assert any(g["game_type"] == "dummy_pack" for g in gr.list_games())
    finally:
        gr.unregister_games_for_source("some_plugin")
    assert "dummy_pack" not in gr._REGISTRY


def test_unregister_games_not_in():
    class _Dummy(GameEngine):
        game_type = "stale_pack"

    # 注册表是全局的：先清掉其他用例/插件同步留下的插件来源注册（如示例包自注册），保证断言确定
    gr.unregister_games_not_in(set())
    gr.register_game_type("stale_pack", _Dummy, {
        "name": "残留", "player_mode": "dual", "min_players": 2,
        "max_players": 2, "needs_gm": False, "description": "x"}, source="gone_plugin")
    removed = gr.unregister_games_not_in({"other_plugin"})
    assert removed == ["stale_pack"]
    assert "stale_pack" not in gr._REGISTRY
    # builtin 不受影响
    assert "undercover" in gr._REGISTRY


# ── coin_flip 示例包：真实加载 + 引擎冒烟 ──


class _Player:
    def __init__(self, seat, player_type="ai", character_id=None):
        self.id = seat + 1
        self.seat = seat
        self.player_type = player_type
        self.character_id = character_id
        self.role = ""
        self.alive = True
        self.score = 0
        self.is_spectator = False
        self.private_json = "{}"


class _Session:
    def __init__(self):
        self.id = 1
        self.game_type = "coin_flip"
        self.round = 0
        self.phase = ""
        self.status = "created"
        self.state_json = "{}"
        self.winner_side = None
        self.finished_at = None


def test_coin_flip_example_plugin_loads_and_registers(monkeypatch):
    """示例包经真实 load_plugin_dir 加载 → main.py 执行 sdk.register_game → 注册成功"""
    import app.plugins.registry as plugin_registry

    info = plugin_registry.load_plugin_dir(REPO_ROOT / "plugins" / "examples" / "coin_flip")
    assert info is not None and info["name"] == "coin_flip"
    assert "coin_flip" in gr._REGISTRY and gr._GAME_SOURCE["coin_flip"] == "coin_flip"
    try:
        monkeypatch.setattr(plugin_registry, "_enabled", {"coin_flip": True})
        assert any(g["game_type"] == "coin_flip" for g in gr.list_games())
        engine = gr.engine_for("coin_flip")(_Session())
        assert isinstance(engine, GameEngine)
    finally:
        gr.unregister_games_for_source("coin_flip")
        plugin_registry._loaded.pop("coin_flip", None)
        sys.modules.pop("ai_plugin_coin_flip", None)


def test_coin_flip_engine_smoke():
    """引擎功能冒烟：加载示例包 → 喊边→抛币→出胜者（结果随机但流程确定完整）"""
    import app.plugins.registry as plugin_registry

    assert plugin_registry.load_plugin_dir(REPO_ROOT / "plugins" / "examples" / "coin_flip") is not None
    engine = gr._REGISTRY["coin_flip"](_Session())
    engine.players = [_Player(0, "user"), _Player(1, "ai")]
    for p in engine.players:
        engine._set_meta(p.seat, name=("用户" if p.player_type == "user" else f"角色{p.seat}"))
    events = asyncio.run(engine.setup())
    assert engine.state["stage"] == "call" and events

    # 喊错误边被拒
    bad = asyncio.run(engine.apply_action(0, "call", {"side": "edge"}))
    assert bad.ok is False

    res = asyncio.run(engine.apply_action(0, "call", {"side": "heads"}))
    assert res.ok and res.event["payload"]["side"] == "heads"

    wins = asyncio.run(engine.advance())
    assert wins and wins[0]["event_type"] == "win"
    assert engine.state["stage"] == "done"
    assert asyncio.run(engine.check_winner()) in ("0", "1")
    # 视图与 AI 上下文无异常
    assert engine.view_for(0).public_state["stage"] == "done"
    ctx = engine.build_ai_prompt(1)
    assert ctx.game_type == "coin_flip"
    assert engine.expected_action(1) == "skip"
