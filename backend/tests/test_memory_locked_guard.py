# -*- coding: utf-8 -*-
"""P0 记忆护栏测试（2026-08-18，审查 M-P0-1 / M-P0-2）：
- 锁定（is_locked）记忆参与全量去重（向量对 / 字符回退路径）不被软删；
- 写前查重（向量 / 字符命中）命中锁定记忆不改写其 importance、不触发强化；
- 24h 同主题合并路径对锁定记忆依旧跳过（回归，不破坏既有正确行为）；
- memories 高频查询索引迁移幂等（临时库跑 init_db 两次，不触碰 backend/data）。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库参照 test_plugin_bridge 风格）
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.dedup as memdedup
import app.memory.service as memsvc
from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch 记忆模块的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="memory_locked_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(memdedup, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)  # 避免真实 ChromaDB 调用
    monkeypatch.setattr(memdedup, "_schedule_dedup", _noop)     # save_memory 尾部异步去重不触发
    yield factory
    asyncio.run(engine.dispose())


async def _seed(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


async def _get(factory, mid):
    async with factory() as db:
        return await db.get(Memory, mid)


def _base_kw(**over):
    kw = dict(
        user_id=1, character_id=1, memory_type="event",
        content="x", importance=50.0, is_locked=False,
        strength_days=5.0,
    )
    kw.update(over)
    return kw


# ---------------- M-P0-1：全量去重（dedup.deduplicate_memories）----------------

def test_全量去重_向量对_锁定记忆保留_非锁定重复被删(mem_db, monkeypatch):
    async def _main():
        locked = await _seed(mem_db, **_base_kw(
            content="用户今天爬山", importance=50.0, is_locked=True))
        dup = await _seed(mem_db, **_base_kw(
            content="用户今天爬山", importance=90.0, is_locked=False))

        async def _fake_vectors(cid):
            return {locked.id: [1.0, 0.0], dup.id: [1.0, 0.0]}
        monkeypatch.setattr(memdedup, "get_all_vectors_by_character", _fake_vectors)

        deleted = await memdedup.deduplicate_memories(1)
        return deleted, await _get(mem_db, locked.id), await _get(mem_db, dup.id)

    deleted, l, d = asyncio.run(_main())
    assert deleted == 1
    assert l.is_archived is False  # 锁定记忆不被软删
    assert d.is_archived is True   # 非锁定重复被删


def test_全量去重_向量对_双方都锁定_都不删(mem_db, monkeypatch):
    async def _main():
        a = await _seed(mem_db, **_base_kw(
            content="用户今天爬山", importance=50.0, is_locked=True))
        b = await _seed(mem_db, **_base_kw(
            content="用户今天爬山", importance=90.0, is_locked=True))

        async def _fake_vectors(cid):
            return {a.id: [1.0, 0.0], b.id: [1.0, 0.0]}
        monkeypatch.setattr(memdedup, "get_all_vectors_by_character", _fake_vectors)

        deleted = await memdedup.deduplicate_memories(1)
        return deleted, await _get(mem_db, a.id), await _get(mem_db, b.id)

    deleted, a, b = asyncio.run(_main())
    assert deleted == 0
    assert a.is_archived is False and b.is_archived is False


def test_全量去重_字符回退_锁定记忆保留_非锁定重复被删(mem_db, monkeypatch):
    async def _main():
        locked = await _seed(mem_db, **_base_kw(
            content="今天和用户一起去爬山了", importance=50.0, is_locked=True))
        dup = await _seed(mem_db, **_base_kw(
            content="今天和用户一起去爬山了", importance=90.0, is_locked=False))

        async def _fake_vectors(cid):
            return {}  # 无向量 → 走字符级回退
        monkeypatch.setattr(memdedup, "get_all_vectors_by_character", _fake_vectors)

        deleted = await memdedup.deduplicate_memories(1)
        return deleted, await _get(mem_db, locked.id), await _get(mem_db, dup.id)

    deleted, l, d = asyncio.run(_main())
    assert deleted == 1
    assert l.is_archived is False  # 锁定记忆不被软删
    assert d.is_archived is True   # 非锁定重复被删


# ---------------- M-P0-1：写前查重（service.save_memory）----------------

def test_写前查重_向量命中锁定记忆_不改写importance且新建(mem_db, monkeypatch):
    async def _main():
        locked = await _seed(mem_db, **_base_kw(
            memory_type="preference", content="用户喜欢吃辣的火锅",
            importance=70.0, is_locked=True))

        async def _fake_embed(c):
            return [0.1, 0.2]

        async def _fake_find(cid, embedding, limit, min_similarity):
            return (locked.id, 0.95)

        monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
        monkeypatch.setattr(memsvc, "find_similar_memory", _fake_find)
        import app.memory.meaning as meaning_mod
        monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)

        result = await memsvc.save_memory(
            user_id=1, character_id=1, memory_type="preference",
            content="用户喜欢吃辣的火锅", importance=4,
        )
        after = await _get(mem_db, locked.id)
        return result, after

    result, after = asyncio.run(_main())
    assert result.id != after.id        # 命中锁定记忆 → 跳过 → 新建一条
    assert result.importance == 80.0    # 新记忆正常按 4 星落库
    assert after.importance == 70.0     # 锁定记忆 importance 未被改写
    assert after.strength_days == 5.0   # 未触发强化（S 不变）


def test_写前查重_字符命中锁定记忆_不改写importance且新建(mem_db, monkeypatch):
    async def _main():
        locked = await _seed(mem_db, **_base_kw(
            memory_type="preference", content="用户喜欢吃辣的火锅",
            importance=70.0, is_locked=True))

        async def _boom_embed(c):
            raise RuntimeError("embed unavailable")

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        import app.memory.meaning as meaning_mod
        monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)

        # importance=5 → new_pct=100 > 70，若命中会改写；锁定护栏必须拦下
        result = await memsvc.save_memory(
            user_id=1, character_id=1, memory_type="preference",
            content="用户喜欢吃辣的火锅", importance=5,
        )
        after = await _get(mem_db, locked.id)
        return result, after

    result, after = asyncio.run(_main())
    assert result.id != after.id        # 字符命中锁定记忆 → 跳过 → 新建
    assert after.importance == 70.0     # 锁定记忆 importance 未被改写
    assert after.strength_days == 5.0   # 未触发强化（S 不变）


def test_24h合并_锁定记忆跳过_新建而非合并(mem_db, monkeypatch):
    async def _main():
        locked = await _seed(mem_db, **_base_kw(
            memory_type="preference", content="用户昨天说想养一只橘猫",
            importance=70.0, is_locked=True))

        async def _boom_embed(c):
            raise RuntimeError("embed unavailable")

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        import app.memory.meaning as meaning_mod
        monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)

        # 字符相似 0.706：字符查重(≥0.72)不命中、24h 合并(>0.6)命中 → 仅合并路径能拦到它
        result = await memsvc.save_memory(
            user_id=1, character_id=1, memory_type="preference",
            content="用户想养橘猫", importance=5,
        )
        after = await _get(mem_db, locked.id)
        return result, after

    result, after = asyncio.run(_main())
    assert result.id != after.id                          # 合并路径对 locked 依旧跳过 → 新建
    assert after.importance == 70.0                       # 锁定记忆未被合并改写
    assert after.content == "用户昨天说想养一只橘猫"       # 内容未被合并改写


# ---------------- M-P0-2：索引迁移（database.init_db）----------------

def test_init_db_记忆索引迁移幂等(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="memory_idx_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)

    import app.db.database as dbmod
    monkeypatch.setattr(dbmod, "engine", engine)

    async def _indexes():
        async with engine.begin() as conn:
            rows = (await conn.execute(sa_text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='memories' ORDER BY name"
            ))).scalars().all()
        return list(rows)

    async def _run():
        await dbmod.init_db()
        return await _indexes()

    idx1 = asyncio.run(_run())
    assert "idx_memories_next_review" in idx1
    assert "idx_memories_char_archived" in idx1
    assert "idx_memories_char_created" in idx1

    idx2 = asyncio.run(_run())  # 幂等：再跑一次不报错、不产生重复索引
    assert idx2 == idx1
    assert idx2.count("idx_memories_next_review") == 1
    assert idx2.count("idx_memories_char_archived") == 1
    assert idx2.count("idx_memories_char_created") == 1

    asyncio.run(engine.dispose())
