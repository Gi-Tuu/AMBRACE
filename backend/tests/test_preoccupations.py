# -*- coding: utf-8 -*-
"""#63 机制5：心事微澜单测（创建/去重/上限、衰减归档、安慰减重、破冰归档、mood 惩罚边界）。"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.life import preoccupations as preo
from app.models.memory import Memory


@pytest.fixture
def preo_db():
    tmp = tempfile.mkdtemp(prefix="preo_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield factory
    asyncio.run(engine.dispose())


async def _add_memory(db, *, user_id=1, character_id=101, content="心事A", importance=60.0, archived=False):
    m = Memory(
        user_id=user_id, character_id=character_id, memory_type="insight",
        sub_type="preoccupation", source="state_trigger", content=content,
        importance=importance, epistemic_status="FACT",
        speaker_type="character", speaker_id=character_id, scope="private",
        is_archived=archived,
    )
    db.add(m)
    await db.commit()
    return m


def test_mood_baseline_penalty_empty_zero():
    assert preo.mood_baseline_penalty([]) == 0.0


def test_mood_baseline_penalty_bounds():
    class _M:
        def __init__(self, importance):
            self.importance = importance
    assert preo.mood_baseline_penalty([_M(100)]) == -5.0
    assert preo.mood_baseline_penalty([_M(10)]) == 0.0
    assert -5.0 <= preo.mood_baseline_penalty([_M(70)]) <= 0.0
    # 多心事取最高权重
    assert preo.mood_baseline_penalty([_M(30), _M(90)]) == preo.mood_baseline_penalty([_M(90)])


def test_create_preoccupation_dedup_and_cap(preo_db):
    async def _run():
        async with preo_db() as db:
            assert await preo.create_preoccupation(db, user_id=1, character_id=101, content="心事A", weight=60) is True
            # 同内容去重
            assert await preo.create_preoccupation(db, user_id=1, character_id=101, content="心事A", weight=80) is False
            assert await preo.create_preoccupation(db, user_id=1, character_id=101, content="心事B", weight=60) is True
            assert await preo.create_preoccupation(db, user_id=1, character_id=101, content="心事C", weight=60) is True
            # 上限 3
            assert await preo.create_preoccupation(db, user_id=1, character_id=101, content="心事D", weight=60) is False
            active = await preo.list_active_preoccupations(db, 101)
            assert len(active) == 3
    asyncio.run(_run())


def test_decay_preoccupations_archives_at_zero(preo_db, monkeypatch):
    async def _run():
        async with preo_db() as db:
            await _add_memory(db, content="心事A", importance=10.0)
            await _add_memory(db, content="心事B", importance=100.0)
            prev = await preo.list_active_preoccupations(db, 101)
            assert len(prev) == 2
            n = await preo.decay_preoccupations(db)
            await db.commit()
            assert n == 2
            active = await preo.list_active_preoccupations(db, 101)
            # 100-15~25 仍在；10-15~25 归零归档
            assert len(active) == 1
    asyncio.run(_run())


def test_soften_by_comfort_words(preo_db):
    async def _run():
        async with preo_db() as db:
            await _add_memory(db, content="心事A", importance=30.0)
            await _add_memory(db, content="心事B", importance=90.0)
            # 无安慰词 → 不处理
            assert await preo.soften_by_comfort_words(db, user_id=1, character_id=101, content="今天吃什么") is False
            # 有安慰词 → 最高权重心事(90)减重
            assert await preo.soften_by_comfort_words(db, user_id=1, character_id=101, content="别难过，抱抱你") is True
            await db.commit()
            active = await preo.list_active_preoccupations(db, 101)
            assert len(active) == 2
            assert active[0].importance < 90.0  # 最高权重的减了
    asyncio.run(_run())


def test_resolve_cold_war_preoccupations(preo_db):
    async def _run():
        async with preo_db() as db:
            await _add_memory(db, content="刚刚和你冷战了，心里有点难受", importance=85.0)
            await _add_memory(db, content="有点吃醋", importance=75.0)
            n = await preo.resolve_cold_war_preoccupations(db, user_id=1, character_id=101)
            await db.commit()
            assert n == 1  # 只归档冷战心事
            remain = await preo.list_active_preoccupations(db, 101)
            assert len(remain) == 1
            assert "吃醋" in remain[0].content
    asyncio.run(_run())


def test_create_preoccupation_for_rule_mapping(preo_db):
    async def _run():
        async with preo_db() as db:
            assert await preo.create_preoccupation_for_rule(db, user_id=1, character_id=101, rule_key="anger_mood_low") is True
            assert await preo.create_preoccupation_for_rule(db, user_id=1, character_id=101, rule_key="not_emotion") is False
    asyncio.run(_run())


def test_has_comfort_word_gua_boundary():
    """P3-1：单字「乖」加边界——多字词与独立「乖」命中，好乖/乖巧/这猫好乖不误触发。"""
    assert preo.has_comfort_word("别难过，抱抱你") is True
    assert preo.has_comfort_word("乖啦，不哭") is True
    assert preo.has_comfort_word("乖，别哭了") is True
    assert preo.has_comfort_word("乖") is True
    assert preo.has_comfort_word("今天吃什么") is False
    # 误匹配修复
    assert preo.has_comfort_word("这猫好乖") is False
    assert preo.has_comfort_word("好乖") is False
    assert preo.has_comfort_word("你家的猫真乖巧") is False


def test_soften_not_triggered_by_gua_describing(preo_db):
    """P3-1：好乖/这猫好乖 描述用法不应触安慰减重。"""
    async def _run():
        async with preo_db() as db:
            await _add_memory(db, content="心情低落", importance=60.0)
            assert await preo.soften_by_comfort_words(db, user_id=1, character_id=101, content="这猫好乖") is False
            await db.commit()
            active = await preo.list_active_preoccupations(db, 101)
            assert len(active) == 1
            assert active[0].importance == 60.0  # 未被减重
    asyncio.run(_run())
