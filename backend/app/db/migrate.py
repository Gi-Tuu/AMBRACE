# -*- coding: utf-8 -*-
"""Alembic 迁移集成：启动时把当前数据库对齐到版本链 head。

设计决策（渐进式，避免把 90+ 条手工迁移重写为版本链的高风险）：
- ``init_db()``（app/db/database.py）仍是幂等兼容层：首次建库（create_all）+ 增量补丁
  （PRAGMA 幂等 ALTER）继续生效，保证存量与全新部署的 schema 都到当前模型状态，**不动**。
- 本模块只负责「版本记账」：把当前库的 alembic_version 对齐到 head。
  存量库（无 alembic_version 或为空）→ stamp head（标记为已迁移，不重复执行迁移）；
  已有版本但落后于 head → alembic upgrade head（应用后续 schema 变更）。
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
        # 全新库（init_db 刚建好）或从未版本化 → 标记为已迁移到 head。
        command.stamp(cfg, head)
        return f"stamped:{head}"
    if known is None:
        # 存量库带一个本版本链之外的版本号（历史遗留/被移除的 alembic 试验）：
        # init_db 已把 schema 对齐到当前模型；用 purge=True 清掉旧历史后重新 stamp 到 head，
        # 绕开 alembic 解析「孤儿版本号」时的失败。
        command.stamp(cfg, head, purge=True)
        return f"re-stamped:{cur}->{head}"
    # 已知旧版本，且落后于 head → 执行升级（应用后续 schema 变更）。
    command.upgrade(cfg, head)
    return f"upgraded:{cur}->{head}"


async def ensure_alembic_revision() -> str:
    """启动时对齐数据库版本链（在线程池中执行，不阻塞事件循环）。

    幂等：库已在 head 时为 no-op；存量库无版本表时 stamp head；落后时 upgrade。
    返回动作描述字符串，调用方（main.py lifespan）可记录日志。
    """
    return await asyncio.to_thread(_ensure_alembic_revision_sync)


def is_migration_available() -> bool:
    """判断 Alembic 是否就绪（alembic.ini 与脚本目录存在）。供 main.py 防御性判断。"""
    return _ALEMBIC_INI.exists() and (_ALEMBIC_DIR / "env.py").exists()
