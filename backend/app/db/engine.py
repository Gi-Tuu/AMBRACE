"""DB 引擎（F1 拆分，2026-08-31）：引擎创建与 SQLite 目录准备；会话工厂见 session.py。"""
"""数据库连接与会话管理（原 database.py 头部）"""
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

# 首次部署时 backend/data/sqlite/ 尚不存在，SQLite 会报 unable to open database file
if settings.database_url.startswith("sqlite"):
    _db_file = settings.database_url.split("///", 1)[-1].split("?", 1)[0]
    if _db_file and _db_file != ":memory:":
        os.makedirs(Path(_db_file).resolve().parent, exist_ok=True)

# 创建异步引擎
engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,          # SQLite 不需要连接池
    connect_args={"check_same_thread": False, "timeout": 10},
)

