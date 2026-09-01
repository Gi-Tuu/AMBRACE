"""数据库连接与会话管理（F1 门面，2026-08-31）。

实现已拆分：engine.py（引擎）/ session.py（会话工厂）/ init_db.py（建库与轻量迁移）。
本文件保留旧 import 兼容（`from app.db.database import ...`），并**定义** get_db 与
unit_of_work——函数体在本模块命名空间查找 async_session_factory，保持既有测试/运维
`monkeypatch.setattr(app.db.database, "async_session_factory", ...)` 接缝有效（勿移走定义）。
"""
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import engine  # noqa: F401
from app.db.session import async_session_factory  # noqa: F401
from app.db.init_db import _migrate_ai_character_loop_flags, init_db  # noqa: F401


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def unit_of_work():
    """轻量事务边界（F1 新增）：统一提交/回滚，替代各处手写 commit/rollback。

    用法：
        async with unit_of_work() as db:
            db.add(...)
    退出时自动 commit；异常自动 rollback 并抛出。
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
