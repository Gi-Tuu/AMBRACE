# -*- coding: utf-8 -*-
"""2026-09-21：插件加载失败**不得毒化注册表**（Windows xdist 串味根因的回归）。

真实事故链（第二十八棒 CI：Windows 快测档 10 例红）：
1. 示例游戏包 ``coin_flip`` 在同一进程里被**第二次**加载时，其 main.py 的 ``sdk.register_game``
   抛 ``ValueError('game_type already registered: coin_flip')``；
2. 旧实现 ``registry.load_plugin_dir`` 在 exec main.py **之前**已写入占位条目（``info={}``），
   异常时直接 ``return None``、**不清占位** → 该条目没有 "name" 字段；
3. 之后任何 ``registry.list_plugins()`` 都在 ``out.sort(key=lambda x: x["name"])`` 抛
   ``KeyError('name')``；``claimed_categories()`` 的 ``except`` 把它吞成**空集**；
4. 空 claims = 插件策略包"没接管" → ``PluginSource.collect`` 不下发 roster → 示例策略包
   不产出候选 → ``test_proactive_strategy_{pack,x6b,x6c}`` 那 10 例在同一 worker 里稳定红。
   单进程档恰好在那次污染**之前**跑完这批用例，所以此前一直没暴露；xdist 改了分配顺序才显形。

本文件三条用例：①加载失败不留占位、list_plugins 仍可用；②同来源重复注册游戏 = 覆盖（消掉触发器）；
③重复加载游戏包之后策略类别 claims 仍非空（端到端锁死事故链路）。
"""
import json

import pytest

from app.plugins import registry

PACK = "proactive_strategy_special"   # 示例策略包（config.strategy_category = "special"）
GAME_PACK = "coin_flip"               # 示例游戏包（重载会走 register_game_type）
BAD = "badpkg_stub"                   # 临时构造的「加载必失败」插件


@pytest.fixture()
def clean_registry():
    """本文件会真的加载示例插件：前后把涉及名字的注册表状态清干净，不污染同 worker 的其他用例。"""
    from app.games.registry import unregister_games_for_source
    from app.scheduling.sources import strategy as _strategy

    def _cleanup():
        for name in (PACK, GAME_PACK, BAD):
            registry._loaded.pop(name, None)
            registry._enabled.pop(name, None)
        unregister_games_for_source(GAME_PACK)
        _strategy._REGISTRY.pop("special", None)

    _cleanup()
    yield
    _cleanup()


def _write_broken_plugin(tmp_path):
    """造一个 manifest 合法、main.py 必抛的插件目录（复现「exec 期失败」）。"""
    dst = tmp_path / BAD
    dst.mkdir()
    manifest = json.loads((registry.EXAMPLE_DIR / GAME_PACK / "manifest.json").read_text(encoding="utf-8"))
    manifest["name"] = BAD
    manifest["hooks"] = []
    (dst / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (dst / "main.py").write_text("raise RuntimeError('boom: 模拟加载失败')\n", encoding="utf-8")
    return dst


def test_failed_load_leaves_no_placeholder(tmp_path, clean_registry):
    """① 加载失败 → 不留占位条目，且 list_plugins() 不受影响（旧实现在这里 KeyError）。"""
    dst = _write_broken_plugin(tmp_path)

    assert registry.load_plugin_dir(dst) is None, "main.py 抛错时应判定加载失败"
    assert BAD not in registry._loaded, "失败加载不得留下占位条目（info 为空）"
    assert BAD not in registry._enabled

    names = [p["name"] for p in registry.list_plugins()]  # 旧实现：KeyError('name')
    assert BAD not in names


def test_duplicate_game_load_is_idempotent(clean_registry):
    """② 同来源重复注册游戏 = 覆盖（不再是 ValueError）——消掉事故的触发器。"""
    first = registry.load_plugin_dir(registry.EXAMPLE_DIR / GAME_PACK)
    second = registry.load_plugin_dir(registry.EXAMPLE_DIR / GAME_PACK)

    assert first, "首次加载示例游戏包应成功"
    assert second, "同来源重复加载应成功（=覆盖），不应是冲突"
    assert GAME_PACK in registry._loaded
    assert [p["name"] for p in registry.list_plugins()].count(GAME_PACK) == 1


def test_claims_survive_duplicate_game_load(clean_registry):
    """③ 端到端：重复加载游戏包之后，插件策略包的 claims 仍非空（事故链路的最终断言）。"""
    from app.scheduling.sources.strategy import claimed_categories

    assert registry.load_plugin_dir(registry.EXAMPLE_DIR / PACK) is not None
    registry._enabled[PACK] = True
    assert "special" in claimed_categories(), "正向前提：包已加载且启用 → 应声明接管 special"

    assert registry.load_plugin_dir(registry.EXAMPLE_DIR / GAME_PACK) is not None
    assert registry.load_plugin_dir(registry.EXAMPLE_DIR / GAME_PACK) is not None  # ← 历史事故点

    assert "special" in claimed_categories(), "重复加载游戏包不得把策略包打成「未接管」"
    assert any(p["name"] == PACK for p in registry.list_plugins())
