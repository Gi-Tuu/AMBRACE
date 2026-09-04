# -*- coding: utf-8 -*-
"""#72 PR-B 检索场景（scene）过滤测试。

覆盖：
- _scene_filter 纯函数表驱动：scene=None 原样返回（零行为变化）；dm 过滤群逐条但保留群摘要指针；
  group 过滤其它群 gid；exclude_sources 生效；
- search_memories 默认参数（scene/exclude_sources/group_id None）行为与旧路径一致（不触发过滤）；
  传 scene='dm' 时群逐条被剔除、群摘要指针与普通私聊保留。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data。）
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.memory.retrieve import _scene_filter, search_memories


# ---------------- _scene_filter 纯函数（表驱动） ----------------

def _row(i, source=None, sub_type=None, group_id=None):
    return {"id": i, "source": source, "sub_type": sub_type, "group_id": group_id}


_ROWS = [
    _row(1, "group", "group", 1),            # 群逐条
    _row(2, "group", "group_summary", 1),    # 群摘要指针
    _row(3, "game", "game", None),           # 游戏逐条
    _row(4, "game", "game_summary", None),   # 游戏摘要指针
    _row(5, "chat", "chat", None),           # 普通私聊
    _row(6, "group", "group", 2),            # 其它群 逐条
    _row(7, "group", "group_summary", 2),    # 其它群 摘要指针
]


def _ids(rows):
    return [r["id"] for r in rows]


def test_scene_filter_scene_none_原样返回():
    out = _scene_filter(list(_ROWS), None, None, None)
    assert out == _ROWS and out is not _ROWS  # 值相等且非同一对象（每次都新建列表）


def test_scene_filter_dm_过滤群逐条保留群摘要():
    out = _scene_filter(list(_ROWS), "dm", None, None)
    ids = _ids(out)
    assert 1 not in ids          # 群逐条不进私聊
    assert 6 not in ids          # 其它群逐条同样不进私聊
    assert 2 in ids              # 群摘要指针保留（"知道发生过"）
    assert 7 in ids
    assert 3 in ids and 4 in ids  # game 不靠 dm 过滤（由 exclude_sources 决定）
    assert 5 in ids


def test_scene_filter_group_过滤其它群gid():
    out = _scene_filter(list(_ROWS), "group", None, 1)
    ids = _ids(out)
    assert 1 in ids and 2 in ids       # 本群保留
    assert 6 not in ids and 7 not in ids  # 其它群剔除
    assert 3 in ids and 4 in ids and 5 in ids  # 非 group 不受群过滤


def test_scene_filter_exclude_sources_生效():
    out = _scene_filter(list(_ROWS), None, {"game"}, None)
    ids = _ids(out)
    assert 3 not in ids and 4 not in ids  # game 全部剔除
    assert 1 in ids and 2 in ids and 5 in ids and 6 in ids and 7 in ids


def test_scene_filter_dm_加_exclude_游戏摘要也剔除():
    out = _scene_filter(list(_ROWS), "dm", {"game"}, None)
    ids = _ids(out)
    assert 1 not in ids and 6 not in ids   # 群逐条剔除
    assert 3 not in ids and 4 not in ids   # game 剔除（含游戏摘要）
    assert 2 in ids and 7 in ids and 5 in ids  # 群摘要 + 普通私聊保留


# ---------------- search_memories 默认参数与旧路径一致 / scene=dm 生效 ----------------

async def _noop(*a, **k):
    return None


@pytest.fixture()
def scene_db(monkeypatch):
    """临时库 + 检索原语桩：让 search_memories 走到 _rerank（回填 source/sub_type/group_id）。"""
    import app.models  # noqa: F401
    from app.models.base import Base
    import app.db.database as db_mod
    import app.memory.service as memsvc

    tmp = tempfile.mkdtemp(prefix="scene_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}",
                                 poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)

    async def _seed():
        from app.models.memory import Memory
        async with factory() as db:
            m1 = Memory(user_id=1, character_id=1, memory_type="event",
                        content="群聊流水A", importance=50.0,
                        source="group", sub_type="group", group_id=1)
            m2 = Memory(user_id=1, character_id=1, memory_type="event",
                        content="群摘要B", importance=50.0,
                        source="group", sub_type="group_summary", group_id=1)
            m3 = Memory(user_id=1, character_id=1, memory_type="event",
                        content="普通私聊C", importance=50.0,
                        source="chat", sub_type="chat", group_id=None)
            db.add_all([m1, m2, m3])
            await db.commit()
            for m in (m1, m2, m3):
                await db.refresh(m)
            return m1.id, m2.id, m3.id

    ids = asyncio.run(_seed())

    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)
    yield factory, ids
    asyncio.run(engine.dispose())


def test_search_memories_默认参数与旧路径一致(scene_db, monkeypatch):
    factory, (id1, id2, id3) = scene_db
    import app.memory.service as memsvc

    async def _fake_embed(c):
        return [0.1, 0.2]

    async def _fake_vector(character_id, query_embedding, limit=5):
        return [
            {"id": id1, "content": "群聊流水A", "type": "event", "importance": 50.0},
            {"id": id2, "content": "群摘要B", "type": "event", "importance": 50.0},
            {"id": id3, "content": "普通私聊C", "type": "event", "importance": 50.0},
        ]

    async def _fake_bm25(cid, q, top_k=5):
        return []

    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "vector_search", _fake_vector)
    monkeypatch.setattr(memsvc, "bm25_search", _fake_bm25)

    # 默认参数：scene/exclude_sources/group_id 全 None → 不过滤，群逐条保留
    async def _run():
        return await search_memories(character_id=1, query="x", limit=5)
    hits = asyncio.run(_run())
    ids = [h["id"] for h in hits]
    assert id1 in ids and id2 in ids and id3 in ids


def test_search_memories_scene_dm_过滤群逐条(scene_db, monkeypatch):
    factory, (id1, id2, id3) = scene_db
    import app.memory.service as memsvc

    async def _fake_embed(c):
        return [0.1, 0.2]

    async def _fake_vector(character_id, query_embedding, limit=5):
        return [
            {"id": id1, "content": "群聊流水A", "type": "event", "importance": 50.0},
            {"id": id2, "content": "群摘要B", "type": "event", "importance": 50.0},
            {"id": id3, "content": "普通私聊C", "type": "event", "importance": 50.0},
        ]

    async def _fake_bm25(cid, q, top_k=5):
        return []

    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "vector_search", _fake_vector)
    monkeypatch.setattr(memsvc, "bm25_search", _fake_bm25)

    async def _run():
        return await search_memories(character_id=1, query="x", limit=5, scene="dm")
    hits = asyncio.run(_run())
    ids = [h["id"] for h in hits]
    assert id1 not in ids      # 群逐条不进私聊
    assert id2 in ids          # 群摘要指针保留
    assert id3 in ids
