# -*- coding: utf-8 -*-
"""T5 插件独立 metadata 隔离与幂等建表（2026-09-10）。

覆盖：
1. 加载两渠道插件后，其表只在 plugin_metadata、不在主 Base.metadata；
2. 插件表零外键（跨 metadata 不允许 FK）；
3. ensure_plugin_tables 幂等（重复调用不报错、返回同组表）；
4. 只 create_all 主 metadata 的干净库里不含插件表。
"""
import asyncio

from sqlalchemy import create_engine, inspect

from app.models.base import Base
from app.plugins import registry
from app.plugins.plugin_base import PluginBase, plugin_metadata

# 直接复用内核同源常量（registry.EXAMPLE_DIR = 项目根/plugins/examples），
# 不要手写 parents[N] 拼路径——测试位于 backend/tests/，手写极易错一级。
PLUGIN_ROOT = registry.EXAMPLE_DIR
DOUYIN = {"douyin_accounts", "douyin_posts", "douyin_comments",
          "douyin_pending", "douyin_viewed_notes"}
WECHAT = {"wechat_ilink_bindings", "wechat_ilink_messages"}


def test_plugin_base_uses_isolated_metadata():
    """PluginBase.metadata 就是 plugin_metadata，且不等于主 Base.metadata。"""
    assert PluginBase.metadata is plugin_metadata
    assert plugin_metadata is not Base.metadata


def test_plugin_tables_not_in_main_metadata_after_load():
    """加载两渠道插件后，其表只在 plugin_metadata，不在主 Base.metadata。"""
    registry.load_plugin_dir(PLUGIN_ROOT / "douyin_mcp")
    registry.load_plugin_dir(PLUGIN_ROOT / "wechat_ilink")
    main_tables = set(Base.metadata.tables)
    plug_tables = set(plugin_metadata.tables)
    assert DOUYIN.isdisjoint(main_tables), f"渠道表泄漏进主 metadata: {DOUYIN & main_tables}"
    assert WECHAT.isdisjoint(main_tables), f"渠道表泄漏进主 metadata: {WECHAT & main_tables}"
    assert DOUYIN <= plug_tables and WECHAT <= plug_tables
    assert set(plug_tables) == DOUYIN | WECHAT  # 加载两渠道后插件 metadata 恰这 7 张


def test_plugin_tables_have_no_foreign_key():
    """插件表零外键（跨 metadata 不允许 FK；现状契约，防回归）。"""
    registry.load_plugin_dir(PLUGIN_ROOT / "douyin_mcp")
    registry.load_plugin_dir(PLUGIN_ROOT / "wechat_ilink")
    assert plugin_metadata.tables, "插件表未注册，用例前提不成立"
    for name, tbl in plugin_metadata.tables.items():
        assert list(tbl.foreign_keys) == [], f"{name} 出现外键，违反插件表无 FK 约定"


def test_ensure_plugin_tables_idempotent():
    """ensure_plugin_tables 重复调用不报错且表齐全（checkfirst 幂等）。"""
    registry.load_plugin_dir(PLUGIN_ROOT / "douyin_mcp")
    registry.load_plugin_dir(PLUGIN_ROOT / "wechat_ilink")
    r1 = asyncio.run(registry.ensure_plugin_tables())
    r2 = asyncio.run(registry.ensure_plugin_tables())
    assert DOUYIN <= set(r1) and WECHAT <= set(r1)
    assert set(r2) == set(r1)  # 第二次仍幂等返回同一组表、不抛错


def test_main_metadata_create_all_does_not_build_plugin_tables(tmp_path):
    """只对主 metadata create_all 的干净库里【不】含插件表（证明主建表不再顺带建插件表）。"""
    import app.models  # noqa: F401  确保主模型已注册

    eng = create_engine(f"sqlite:///{(tmp_path / 'main_only.db').as_posix()}")
    try:
        Base.metadata.create_all(eng)
        have = set(inspect(eng).get_table_names())
    finally:
        eng.dispose()
    assert "ai_characters" in have and "memories" in have
    assert DOUYIN.isdisjoint(have) and WECHAT.isdisjoint(have)


def test_sdk_plugin_base_exposes_same_class():
    """sdk.plugin_base() 与直接 import 的 PluginBase 是同一个类。"""
    from app.plugins import sdk

    assert sdk.plugin_base() is PluginBase
