# -*- coding: utf-8 -*-
"""P1-1：老角色「认知循环 / 记忆 v2.1」开关迁移测试。

存量库列已存在但值为 0（早期 DEFAULT 0 迁移期留下）→ UPDATE 置 1；
新库新列（列缺失首次加列）→ ALTER ADD COLUMN ... DEFAULT 1，存量行回填 1。
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import database
from app.models.character import AICharacter


@pytest.fixture
def mig_db():
    tmp = tempfile.mkdtemp(prefix="char_loop_mig_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield engine, factory
    asyncio.run(engine.dispose())


def test_migrate_updates_existing_zero_rows(mig_db):
    """列已存在且值为 0 的存量行 → UPDATE 置为 1。"""
    engine, factory = mig_db

    async def _run():
        # 构造存量行：早期 DEFAULT 0 迁移期留下值为 0（保持产品「全量开启」默认应迁移为 1）
        async with factory() as db:
            db.add(AICharacter(user_id=1, name="旧角色", cognitive_loop_enabled=False, memory_v2_enabled=False))
            await db.commit()
        async with engine.begin() as conn:
            await database._migrate_ai_character_loop_flags(conn)
        async with factory() as db:
            row = (await db.execute(
                sa_text("SELECT cognitive_loop_enabled, memory_v2_enabled FROM ai_characters WHERE id = 1")
            )).one()
            assert row[0] == 1
            assert row[1] == 1
    asyncio.run(_run())


def test_migrate_skips_when_columns_missing(mig_db):
    """列缺失（远古库首次启动）→ 跳过且不写哨兵、不崩（加列已由 bootstrap 迁移承接，3.8 收敛）。

    哨兵不写是关键：bootstrap 补列后的下次启动仍会执行存量 0→1 迁移。
    """
    # 再造一个旧表：去掉两个开关列（模拟老 schema；不用 create_all 以免自动带新列）
    tmp = tempfile.mkdtemp(prefix="char_loop_old_")
    db_path = os.path.join(tmp, "old.db")
    old_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)

    async def _run():
        async with old_engine.begin() as conn:
            await conn.execute(sa_text(
                "CREATE TABLE ai_characters ("
                "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name VARCHAR(100) NOT NULL)"
            ))
            await conn.execute(sa_text("INSERT INTO ai_characters (user_id, name) VALUES (1, '旧角色')"))
            await conn.execute(sa_text("CREATE TABLE runtime_flags (key VARCHAR PRIMARY KEY, enabled BOOLEAN, updated_at DATETIME)"))
        async with old_engine.begin() as conn:
            # 不抛异常即通过（列缺失 → 跳过）
            await database._migrate_ai_character_loop_flags(conn)
        async with old_engine.begin() as conn:
            cols = {r[1] for r in (await conn.execute(sa_text("PRAGMA table_info(ai_characters)"))).fetchall()}
            assert "cognitive_loop_enabled" not in cols and "memory_v2_enabled" not in cols
            sent = (await conn.execute(sa_text(
                "SELECT 1 FROM runtime_flags WHERE key='migration_life_v2_flags_20260827'"
            ))).fetchone()
            assert sent is None, "列未就绪时不得写哨兵（否则 bootstrap 补列后迁移被永久跳过）"
    asyncio.run(_run())
    asyncio.run(old_engine.dispose())


def test_migrate_sentinel_preserves_manual_off(mig_db):
    """迁移完成后用户显式关闭的开关，再次 init/migrate 不会被重置（一次性哨兵）。"""
    engine, factory = mig_db

    async def _run():
        async with factory() as db:
            db.add(AICharacter(user_id=1, name="旧角色", cognitive_loop_enabled=False, memory_v2_enabled=False))
            await db.commit()
        # 第一次迁移：存量 0 → 1 + 写入哨兵
        async with engine.begin() as conn:
            await database._migrate_ai_character_loop_flags(conn)
        # 模拟用户显式关闭认知循环
        async with factory() as db:
            await db.execute(sa_text("UPDATE ai_characters SET cognitive_loop_enabled = 0 WHERE id = 1"))
            await db.commit()
        # 第二次迁移：哨兵已存在 → 不覆盖用户显式关闭
        async with engine.begin() as conn:
            await database._migrate_ai_character_loop_flags(conn)
        async with factory() as db:
            row = (await db.execute(
                sa_text("SELECT cognitive_loop_enabled, memory_v2_enabled FROM ai_characters WHERE id = 1")
            )).one()
            assert row[0] == 0  # 用户显式关闭保留
            assert row[1] == 1
    asyncio.run(_run())
