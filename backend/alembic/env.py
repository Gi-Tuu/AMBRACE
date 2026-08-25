# -*- coding: utf-8 -*-
"""Alembic 迁移环境（env.py）。

AMBRACE 后端用异步引擎（sqlite+aiosqlite），Alembic 迁移与自动生成需要同步引擎；
此处从 app.config.settings.database_url 派生同步 URL（去掉 +aiosqlite），并导入全部
ORM 模型作为 target_metadata（单一事实源 = Base.metadata）。
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from alembic import context

# 确保 backend/ 目录在 sys.path，便于 import app.* （与 alembic.ini 的 prepend_sys_path=. 双保险）
this_dir = os.path.abspath(os.path.dirname(__file__))
backend_root = os.path.abspath(os.path.join(this_dir, ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from app.config import settings  # noqa: E402
from app.models._all import Base  # noqa: E402  # 导入全部模型，填充 Base.metadata（步7：models 按域分组后全量导入）

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """把异步 DB URL 转成 Alembic 可用的同步引擎 URL（SQLite 用内置 sqlite3 驱动）。"""
    url = settings.database_url
    # sqlite+aiosqlite:///... -> sqlite:///...
    if url.startswith("sqlite+aiosqlite"):
        url = url.replace("sqlite+aiosqlite", "sqlite", 1)
    # 若未来切换异步 DRIVER（如 postgresql+asyncpg），在此去掉 DRIVER 后缀即可
    elif url.startswith("postgresql+asyncpg"):
        url = url.replace("postgresql+asyncpg", "postgresql", 1)
    return url


def _ensure_sqlite_dir(url: str) -> None:
    """全新部署时 data/ 目录可能尚不存在，SQLite 会报 unable to open database file。"""
    if url.startswith("sqlite:///"):
        db_path = url[len("sqlite:///"):]
        if db_path and db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)


def run_migrations_offline() -> None:
    """离线模式（--sql）：只用 URL 生成 SQL，不连库。"""
    url = _sync_url()
    _ensure_sqlite_dir(url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=url.startswith("sqlite"),  # SQLite 部分 ALTER 需 batch 模式
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移 / 自动生成（同步引擎）。"""
    url = _sync_url()
    _ensure_sqlite_dir(url)
    connectable = create_engine(url, poolclass=pool.NullPool)

    # render_as_batch=True：SQLite 的 ALTER TABLE 受限（ADD COLUMN 可直改，改列/删列/改约束
    # 需 batch_alter_table 表重建），Alembic 自动降级为重写表。仅对 SQLite 开启。
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=url.startswith("sqlite"),
            # SQLite 不支持删除/修改列/约束，batch 模式会自动用"新表复制"策略重写
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
