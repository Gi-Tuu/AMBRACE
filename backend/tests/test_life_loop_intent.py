# -*- coding: utf-8 -*-
"""聊天→生活意图提取测试（chat_intent.py，零 LLM 本地规则）。

覆盖（2026-08-26，v3.3.6 CI 修复）：
- 正则意图提取（非即时）："想去公园" → go_out / "散散步" → walk / "想吃火锅" → eat
- 显式指令（this_turn）："你现在去睡觉" → sleep / priority=3 / horizon=this_turn
- 24h 去重：已有 pending 同动作类型不重复写
- 节流：同角色 5 分钟内二次调用不提取
- 即时指令立即触发 run_character_tick（priority>=3）

v3.3.6 修复：统一使用单个事件循环（fixture 持有 loop），避免每次 asyncio.run 新建/关闭
loop 与 aiosqlite 连接 worker 线程跨 loop 冲突（CI Linux 全量下偶发写库失败被静默吞掉）。
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.life.chat_intent as chat_intent
from app.models.life import LifeChatIntent

OWNER = 100


@pytest.fixture()
def intent_db(monkeypatch):
    """临时 SQLite 文件库 + 单个事件循环：monkeypatch chat_intent.async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="life_loop_intent_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    try:
        loop.run_until_complete(_init())
        monkeypatch.setattr("app.life.chat_intent.async_session_factory", factory)
        monkeypatch.setattr(chat_intent, "_throttle", {})
        yield factory, loop
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


async def _count(db, action_type: str | None = None) -> int:
    q = select(LifeChatIntent)
    if action_type is not None:
        q = q.where(LifeChatIntent.action_type == action_type)
    rows = (await db.execute(q)).scalars().all()
    return len(rows)


# ─────────── 正则提取（非即时） ───────────

def test_提取_想去公园_go_out(intent_db):
    factory, loop = intent_db

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "我想去公园逛逛")
        async with factory() as db:
            rows = (await db.execute(select(LifeChatIntent))).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.character_id == 1 and r.user_id == OWNER
        assert r.action_type == "go_out"
        assert r.horizon == "this_week"
        assert r.priority == 2

    loop.run_until_complete(_case())


def test_提取_散散步_walk(intent_db):
    factory, loop = intent_db

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "我想出去散散步")
        async with factory() as db:
            rows = (await db.execute(select(LifeChatIntent))).scalars().all()
        assert len(rows) == 1 and rows[0].action_type == "walk"

    loop.run_until_complete(_case())


def test_提取_想吃火锅_eat(intent_db):
    factory, loop = intent_db

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "好饿，想吃火锅")
        async with factory() as db:
            rows = (await db.execute(select(LifeChatIntent))).scalars().all()
        assert len(rows) == 1 and rows[0].action_type == "eat"

    loop.run_until_complete(_case())


def test_提取_太短或超长_忽略(intent_db):
    factory, loop = intent_db

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "去")
        await chat_intent.extract_life_intent(1, OWNER, "好" * 101)
        async with factory() as db:
            rows = (await db.execute(select(LifeChatIntent))).scalars().all()
        assert rows == []

    loop.run_until_complete(_case())


# ─────────── 显式指令（this_turn） ───────────

def test_即时指令_去睡觉_sleep_this_turn(intent_db):
    factory, loop = intent_db

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "你现在去睡觉")
        async with factory() as db:
            rows = (await db.execute(select(LifeChatIntent))).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.action_type == "sleep"
        assert r.horizon == "this_turn"
        assert r.priority == 3

    loop.run_until_complete(_case())


# ─────────── 24h 去重 ───────────

def test_去重24h_dedup(intent_db):
    factory, loop = intent_db

    async def _case():
        async with factory() as db:
            db.add(LifeChatIntent(character_id=1, user_id=OWNER, action_type="go_out", status="pending"))
            await db.commit()
        await chat_intent.extract_life_intent(1, OWNER, "我想去公园逛逛")
        async with factory() as db:
            assert await _count(db, "go_out") == 1

    loop.run_until_complete(_case())


# ─────────── 节流 ───────────

def test_节流_5分钟内不重复(intent_db):
    factory, loop = intent_db

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "我想去公园逛逛")
        await chat_intent.extract_life_intent(1, OWNER, "我想去海边走走")
        async with factory() as db:
            assert await _count(db, "go_out") == 1  # 第二次被节流

    loop.run_until_complete(_case())


# ─────────── 即时指令触发 run_character_tick ───────────

def test_即时指令立即触发run_character_tick(intent_db, monkeypatch):
    factory, loop = intent_db
    calls = []

    async def _fake_run(character_id, user_id):
        calls.append((character_id, user_id))

    monkeypatch.setattr("app.life.life_loop.run_character_tick", _fake_run)

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "你现在去睡觉")
        await asyncio.sleep(0.1)  # 让 ensure_future 任务跑完
        async with factory() as db:
            rows = (await db.execute(select(LifeChatIntent))).scalars().all()
        assert len(rows) == 1 and rows[0].action_type == "sleep"

    loop.run_until_complete(_case())
    assert (1, OWNER) in calls


def test_非即时_不触发run_character_tick(intent_db, monkeypatch):
    factory, loop = intent_db
    calls = []

    async def _fake_run(character_id, user_id):
        calls.append((character_id, user_id))

    monkeypatch.setattr("app.life.life_loop.run_character_tick", _fake_run)

    async def _case():
        await chat_intent.extract_life_intent(1, OWNER, "我想去公园逛逛")
        async with factory() as db:
            rows = (await db.execute(select(LifeChatIntent))).scalars().all()
        assert len(rows) == 1

    loop.run_until_complete(_case())
    assert calls == []