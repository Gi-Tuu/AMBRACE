"""DB 会话（F1 拆分，2026-08-31）：异步会话工厂。

注意：get_db / unit_of_work 的**定义**保留在 app/db/database.py——它们的函数体在定义模块的
命名空间里查找 async_session_factory，测试与运维以 `monkeypatch.setattr(app.db.database,
"async_session_factory", ...)` 作为既有 patch 接缝；定义若移出该模块，patch 将静默失效。
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.engine import engine  # noqa: F401  # 门面兼容：旧 import app.db.database.engine 经此中转

# 异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
