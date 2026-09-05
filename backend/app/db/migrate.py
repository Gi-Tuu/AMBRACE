# -*- coding: utf-8 -*-
"""Alembic 迁移集成：启动时把当前数据库对齐到版本链 head。

设计决策（渐进式，避免把 90+ 条手工迁移重写为版本链的高风险）：
- ``init_db()``（app/db/database.py）仍是幂等兼容层：首次建库（create_all）+ 增量补丁
  （PRAGMA 幂等 ALTER）继续生效，保证存量与全新部署的 schema 都到当前模型状态，**不动**。
- 本模块负责「版本记账」：把当前库的 alembic_version 对齐到 head。3.8 收尾（渐进版，
  2026-09-03）把「无版本」分支细分为三类，避免远古库被直接 stamp 后缺列：
  - **全新空库（无任何表）** → stamp head（不重放，版本记账）；
  - **非空老库（有表无版本，schema 落后）** → alembic upgrade head 整链重放：版本链已具备
    has_table / has_index / has_column 全守卫 + bootstrap 补列迁移，可对已存在表安全重放，
    补齐远古库缺的列（修复「只 stamp 不重放」造成的缺列漂移）；
  - **当前 schema 库（有表无版本，但列已在 init_db/create_all 建到当前模型）** → stamp head
    （不重放；用「是否已含链上新增列」判别，避免每会话重放 16 条迁移）。
  - 已知旧版本且落后于 head → alembic upgrade head（不变）；
  - 孤儿版本（版本号不在本链）→ stamp head --purge（清旧历史再标，理由见函数内）。
- 使用同步引擎（源自 settings.database_url，去掉 +aiosqlite）执行，避免与异步引擎混用；
  Alembic 迁移脚本本身跑在同步引擎上（SQLite 用内置 sqlite3 驱动）。
- 未来新增 schema 变更：改模型 → ``alembic revision --autogenerate`` 生成修订 → 入版本链；
  init_db 不要再新增手工 ALTER。
"""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.pool import NullPool

from app.config import settings

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"
_ALEMBIC_DIR = _BACKEND_ROOT / "alembic"

# 「当前 schema」判别哨兵：列『只由版本链 add_column 引入、init_db 从不添加』的 28 列。
# 全新/当前库（current create_all）含全部这些列；远古库（pre-alembic 旧库）缺其中若干。
# 命中全部 → 判定为当前 schema → stamp（不重放）；缺任一 → 判定为落后 → upgrade head。
_CURRENT_SCHEMA_SENTINELS: list[tuple[str, str]] = [
    ("users", "parent_id"),
    ("memories", "group_id"),
    ("memories", "status"),
    ("memories", "superseded_by"),
    ("memories", "derived_from_ids"),
    ("memories", "valid_from"),
    ("memories", "valid_to"),
    ("ai_characters", "talkativeness"),
    ("ai_characters", "talkativeness_locked"),
    ("ai_characters", "user_llm_config_id"),
    ("chat_group_members", "muted"),
    ("chat_group_messages", "game_session_id"),
    ("chat_group_messages", "msg_type"),
    ("life_states", "current_room"),
    ("life_states", "location"),
    ("life_states", "location_updated_at"),
    ("llm_usage", "config_id"),
    ("llm_usage", "group_owner_id"),
    ("lorebook_entries", "cooldown_rounds"),
    ("lorebook_entries", "inclusion_group"),
    ("lorebook_entries", "is_regex"),
    ("lorebook_entries", "probability"),
    ("lorebook_entries", "sticky_rounds"),
    ("plugins", "source"),
    ("plugins", "source_url"),
    ("plugins", "sha256"),
    ("plugins", "consented_permissions"),
    ("plugins", "consented_at"),
    # ── 一机多主 / 渠道绑定 per-账号化（2026-09-05，28→31）──
    # 插件自有表：表不存在=渠道插件未装载（全新/未装渠道）→ 判落后走 upgrade，
    # a7b8c9d0e1f2 迁移段以 _has_table 守卫自动跳过，安全。
    ("channel_bindings", "tenant_id"),
    ("wechat_ilink_bindings", "tenant_id"),
    ("douyin_accounts", "tenant_id"),
]


def _alembic_config() -> Config:
    """构造 Alembic Config，脚本位置指向 backend/alembic。"""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return cfg


def _sync_url() -> str:
    """把异步 DB URL 转为 Alembic 同步引擎 URL。"""
    url = settings.database_url
    if url.startswith("sqlite+aiosqlite"):
        return url.replace("sqlite+aiosqlite", "sqlite", 1)
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql", 1)
    return url


def _current_rev(sync_url: str) -> str | None:
    """读取当前库的 alembic_version；无版本表则返回 None。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        if not sa_inspect(engine).has_table("alembic_version"):
            return None
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
            return row[0] if row else None
    finally:
        engine.dispose()


def _has_any_table(sync_url: str) -> bool:
    """判断库中是否存在任何用户表（排除 alembic_version 自身）。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        tables = sa_inspect(engine).get_table_names()
        return any(t != "alembic_version" for t in tables)
    finally:
        engine.dispose()


def _schema_is_current(sync_url: str) -> bool:
    """判别库是否已是当前 schema（链上新增的 31 列全部存在）。

    仅当「每一张相关表都存在且含对应列」才返回 True；任一表缺失/列缺失 → False（判为落后库）。
    保守取向：宁可判为「落后」去 upgrade head（正确且幂等），不误判为「当前」去 stamp。
    """
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        insp = sa_inspect(engine)
        for table, column in _CURRENT_SCHEMA_SENTINELS:
            try:
                if not insp.has_table(table):
                    return False
                col_names = {c["name"] for c in insp.get_columns(table)}
                if column not in col_names:
                    return False
            except Exception:
                # 表被锁 / 无法反射 → 安全取向：判为未确认当前 → 走 upgrade（保守）。
                return False
        return True
    finally:
        engine.dispose()


def _ensure_alembic_revision_sync() -> str:
    """同步执行对齐，返回动作描述（stamped / re-stamped / upgraded / already_at_head）。"""
    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    sync_url = _sync_url()
    cur = _current_rev(sync_url)

    if cur == head:
        return "already_at_head"

    # 判断当前版本号是否在本版本链中（已知 revision）。存量库可能带一个历史遗留/被移除的
    # alembic 试验版本号（例如本机真实库的 78d3405d8b58），该版本不在本链中 → 当作 orphaned。
    known = None
    if cur is not None:
        try:
            known = script.revision_map.get_revision(cur)
        except Exception:
            known = None

    if cur is None:
        # 无 alembic_version：细分三类。
        if not _has_any_table(sync_url):
            # 全新空库：无任何表 → 仅标记为已迁移到 head（不重放）。
            command.stamp(cfg, head)
            return f"stamped:{head}"
        if _schema_is_current(sync_url):
            # 有表、无版本，且 schema 已是当前（init_db/create_all 已建到当前模型）→ 标记，不重放。
            command.stamp(cfg, head)
            return f"stamped:{head}"
        # 非空老库：schema 落后（缺链上新增列）→ 整链重放补齐（守卫幂等，安全）。
        command.upgrade(cfg, head)
        return f"upgraded:->{head}"

    if known is None:
        # 存量库带一个本版本链之外的版本号（历史遗留/被移除的 alembic 试验）：
        # init_db 已把 schema 对齐到当前模型；用 purge=True 清掉旧历史后重新 stamp 到 head，
        # 绕开 alembic 解析「孤儿版本号」时的失败。理由：该版本号不在本链，upgrade 无法定位
        # 其迁移路径；而 schema 已由 init_db 幂等层对齐，故清旧历史按当前 head 记账最稳。
        command.stamp(cfg, head, purge=True)
        return f"re-stamped:{cur}->{head}"

    # 已知旧版本，且落后于 head → 执行升级（应用后续 schema 变更）。
    command.upgrade(cfg, head)
    return f"upgraded:{cur}->{head}"


async def ensure_alembic_revision() -> str:
    """启动时对齐数据库版本链（在线程池中执行，不阻塞事件循环）。

    幂等：库已在 head 时为 no-op；无版本时按「空库/当前库/落后老库」分流 stamp 或 upgrade。
    返回动作描述字符串，调用方（main.py lifespan）可记录日志。
    """
    return await asyncio.to_thread(_ensure_alembic_revision_sync)


def is_migration_available() -> bool:
    """判断 Alembic 是否就绪（alembic.ini 与脚本目录存在）。供 main.py 防御性判断。"""
    return _ALEMBIC_INI.exists() and (_ALEMBIC_DIR / "env.py").exists()
