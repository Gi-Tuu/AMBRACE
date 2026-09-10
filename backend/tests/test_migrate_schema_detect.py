# -*- coding: utf-8 -*-
"""migrate.py「当前 schema」自动判别（§6.3，2026-09-09）单测。"""
from sqlalchemy import create_engine, inspect, text

from app.db import migrate


def _main_orm_metadata():
    """主 ORM 表子集（权威来源 = app.models._all 聚合的模型类），不含渠道/插件表。

    渠道插件(douyin_mcp/wechat_ilink)的模型随 main.py 加载注册进全局 Base.metadata，
    若直接 Base.metadata.create_all 会把插件表(douyin_*/wechat_ilink_*)一起建、导致
    `_schema_is_current_auto` 误判「当前 schema」。此处只取主 ORM 表子集，使断言与运行顺序
    无关。注：§5.2（全局 Base.metadata 快照-恢复 autouse）已实测证伪并回退——插件兄弟模块
    以裸顶层名进 sys.modules、插件表挂全局 metadata，测试侧无法干净重建；插件表与主
    metadata 的彻底隔离已由生产侧「插件独立 metadata」（T5，2026-09-10）承担：插件表改挂
    app.plugins.plugin_base.plugin_metadata，主 Base.metadata 天然不再含插件表。本过滤在
    T5 后已等价为全量主 metadata，保留仅为运行顺序无关的稳健性。
    """
    import app.models._all as allpkg
    from sqlalchemy import MetaData

    from app.models.base import Base

    main_names = {
        obj.__table__.name
        for obj in (getattr(allpkg, n) for n in dir(allpkg))
        if isinstance(obj, type) and hasattr(obj, "__table__")
        and getattr(obj.__table__, "metadata", None) is Base.metadata
    }
    meta = MetaData()
    for name, tbl in list(Base.metadata.tables.items()):
        if name in main_names:
            tbl.to_metadata(meta)
    return meta


def test_migration_chain_tables_covers_orm_and_plugin_tables():
    """版本链建表全集：覆盖主 ORM 表 + 插件/渠道表，且非空。"""
    cfg = migrate._alembic_config()
    need = migrate._migration_chain_tables(cfg)
    assert len(need) > 50
    # wechat_ilink_bindings 由微信桥插件自有迁移创建（不在本 alembic 版本链），不在此集合
    for t in ("ai_characters", "memories", "api_configs",
              "chat_messages", "douyin_accounts"):
        assert t in need


def test_schema_is_current_auto_empty_db_false(tmp_path):
    """空库 → False（保守走 upgrade，而不是 stamp）。"""
    db = tmp_path / "empty.db"
    sync_url = f"sqlite:///{db}"
    cfg = migrate._alembic_config()
    assert migrate._schema_is_current_auto(sync_url, cfg) is False


def test_schema_is_current_auto_partial_false(tmp_path):
    """只建少数表 → False（need ⊆ have 不成立）。"""
    db = tmp_path / "partial.db"
    sync_url = f"sqlite:///{db.as_posix()}"
    eng = create_engine(sync_url)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE ai_characters (id INTEGER PRIMARY KEY)"))
    eng.dispose()
    cfg = migrate._alembic_config()
    assert migrate._schema_is_current_auto(sync_url, cfg) is False


def test_schema_is_current_auto_create_all_without_plugin_tables_false(tmp_path):
    """仅 create_all 主 ORM（缺 douyin 等非 ORM 插件表）→ False：判落后走 upgrade 补齐。

    这正是 §6.3 自动判别的价值：非 ORM 表（只在版本链/插件迁移建）缺失时不会被误判
    「当前 schema」而 stamp（旧人工哨兵需记得枚举，漏了会永久缺表）。
    """
    import app.models  # noqa: F401

    db = tmp_path / "full.db"
    sync_url = f"sqlite:///{db.as_posix()}"
    eng = create_engine(sync_url)
    # 只建主 ORM 表子集（不含插件/渠道表），保证断言与运行顺序无关
    _main_orm_metadata().create_all(eng)
    eng.dispose()
    cfg = migrate._alembic_config()
    assert migrate._schema_is_current_auto(sync_url, cfg) is False


def test_manual_sentinel_ignores_plugin_tables(tmp_path):
    """T5（2026-09-10）：人工哨兵不再把插件表（wechat_ilink_* / douyin_*）当作主 schema 必备。

    主 ORM 建齐、仅缺插件表的库，_schema_is_current（人工哨兵）须判「当前」——否则未装载
    渠道的部署会被反复判落后去 upgrade，而版本链并不建 wechat_ilink_*，造成无意义重放。
    """
    import app.models  # noqa: F401

    db = tmp_path / "no_plugin.db"
    sync_url = f"sqlite:///{db.as_posix()}"
    eng = create_engine(sync_url)
    # 只建主 ORM 表（T5 后 Base.metadata 已天然不含插件表，无需再过滤子集）
    _main_orm_metadata().create_all(eng)
    have = set(inspect(eng).get_table_names())
    eng.dispose()
    assert "channel_bindings" in have           # 主表哨兵所在表必须建到
    assert "wechat_ilink_bindings" not in have  # 插件表缺席正是本用例场景
    assert "douyin_accounts" not in have
    for table, _col in migrate._CURRENT_SCHEMA_SENTINELS:
        assert not table.startswith(("wechat_ilink_", "douyin_")), \
            f"插件表 {table} 不应再出现在主 schema 哨兵中"
    assert migrate._schema_is_current(sync_url) is True
