# -*- coding: utf-8 -*-
"""#72 PR-B 群共享记忆读写原语测试（append_group_event / recall_group_longterm / trim_group_summary_pointers）。

语义按方案 §4.1；flag（group_cognition_v2）彼时默认关——写入类用例显式 monkeypatch
group_cognition_on=True 以验证写入路径（不定义/改动 AGENT_FLAGS，本例不属 PR-C）。：
- append_group_event：一轮群聊 → 只写 1 条 group_memories；
- recall_group_longterm：按 group 取回，跨天倒序取然后正序展示；
- trim_group_summary_pointers：每角色每群只留最近 keep 条 group_summary 指针，超出软删。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data。）
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.group_memory as gm
from app.models.memory import Memory


@pytest.fixture()
def gmem_db(monkeypatch):
    """临时库 + 把 group_memory 的 async_session_factory 指向临时工厂。"""
    import app.models  # noqa: F401
    from app.models.base import Base
    import app.db.database as db_mod
    import app.memory.service as memsvc

    tmp = tempfile.mkdtemp(prefix="gmem_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}",
                                 poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(gm, "async_session_factory", factory)

    async def _seed_group():
        from app.models.chat import ChatGroup
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="tester", nickname="测试"))
            g = ChatGroup(id=1, user_id=1, name="家庭群聊")
            db.add(g)
            await db.commit()
    asyncio.run(_seed_group())
    yield factory
    asyncio.run(engine.dispose())


def test_append_group_event_一轮只写1条(gmem_db, monkeypatch):
    factory = gmem_db
    monkeypatch.setattr(gm, "group_cognition_on", lambda: True)

    async def _run():
        await gm.append_group_event(
            group_id=1, user_id=1, round_id="r1",
            user_content="明天一起做饭",
            replies=[{"character_id": 11, "content": "好呀"}, {"character_id": 12, "content": "我带菜"}],
            name_map={11: "小阳", 12: "小冰"},
        )
        async with factory() as db:
            rows = (await db.execute(select(gm.GroupMemory))).scalars().all()
            return rows

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0].group_id == 1 and rows[0].user_id == 1
    assert rows[0].round_id == "r1"
    assert "用户：明天一起做饭" in rows[0].content
    assert "小阳：好呀" in rows[0].content and "小冰：我带菜" in rows[0].content


def test_append_group_event_开关关不写(gmem_db, monkeypatch):
    factory = gmem_db
    monkeypatch.setattr(gm, "group_cognition_on", lambda: False)

    async def _run():
        await gm.append_group_event(group_id=1, user_id=1, round_id=None,
                                    user_content="hi", replies=[], name_map={})
        async with factory() as db:
            rows = (await db.execute(select(gm.GroupMemory))).scalars().all()
            return rows

    assert asyncio.run(_run()) == []


def test_recall_group_longterm_按群取回(gmem_db, monkeypatch):
    monkeypatch.setattr(gm, "group_cognition_on", lambda: True)

    async def _run():
        await gm.append_group_event(group_id=1, user_id=1, round_id="r1",
                                    user_content="第一轮话题", replies=[], name_map={})
        await gm.append_group_event(group_id=1, user_id=1, round_id="r2",
                                    user_content="第二轮话题", replies=[], name_map={})
        fetched = await gm.recall_group_longterm(group_id=1)
        # 非本群：group 2 取不到
        empty = await gm.recall_group_longterm(group_id=2)
        return fetched, empty

    fetched, empty = asyncio.run(_run())
    assert len(fetched) == 2
    assert any("第一轮话题" in s for s in fetched)
    assert any("第二轮话题" in s for s in fetched)
    assert all(s.startswith("[") for s in fetched)   # 带日期前缀
    assert empty == []


def test_trim_group_summary_pointers_每群只留3条(gmem_db):
    factory = gmem_db

    async def _seed():
        async with factory() as db:
            for i in range(5):
                db.add(Memory(
                    user_id=1, character_id=1, memory_type="event",
                    content=f"群摘要指针{i}", importance=35.0,
                    source="group", sub_type="group_summary", group_id=1,
                ))
            await db.commit()
    asyncio.run(_seed())

    async def _trim():
        async with factory() as db:
            await gm.trim_group_summary_pointers(db, character_id=1, group_id=1, keep=3)
            await db.commit()
        async with factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.character_id == 1,
                    Memory.source == "group",
                    Memory.sub_type == "group_summary",
                    Memory.group_id == 1,
                ).order_by(Memory.id.asc())
            )).scalars().all()
            return rows

    rows = asyncio.run(_trim())
    assert len(rows) == 5
    archived = [r for r in rows if r.is_archived]
    active = [r for r in rows if not r.is_archived]
    # 只留最近 3 条：最旧的 2 条软删（is_archived=True），其余 active
    assert len(archived) == 2
    assert len(active) == 3
    assert [r.content for r in active] == ["群摘要指针2", "群摘要指针3", "群摘要指针4"]
