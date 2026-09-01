# -*- coding: utf-8 -*-
"""记忆链条绑定（chain）后端增量测试（2026-08-20）：
- save_memory 传 chain 字段（chain_id/parent_id/node_type）落库且 version 默认 0；
- PATCH /api/v1/memories/{id}/content 改内容 → version+1 且向量重算覆盖（created_at 保留）；
- DELETE /api/v1/memories/{id}/tree?cascade=false 返回直接子列表不删；cascade=true 经 purge_memory **物理删**热行（#70-C BUG-3：非 is_archived 软删）。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data）
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.dedup as memdedup
import app.memory.service as memsvc
from app.api.memories import remove_memory_tree, update_memory_content
from app.db import database as db_mod
from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch 相关模块的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="memory_chain_test_")
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
    # #70-C BUG-3：purge_memory 物理删热行；须让 supersede 也用临时库（否则跨用例残留
    # 的 supersede.async_session_factory 会指向前一个用例已 dispose 的 engine，导致 deleted 计数错乱）
    import app.memory.supersede as sup
    monkeypatch.setattr(sup, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)  # 避免真实 ChromaDB 调用
    # #70-C R-3：purge_memory 函数内从 vector_store 导入 delete_memory_vector（拿模块属性，
    # 非 memsvc 引用），须直接 patch vector_store，否则 cascade 用例会误触真实 Chroma
    import app.db.vector_store as _vs
    monkeypatch.setattr(_vs, "delete_memory_vector", _noop)
    monkeypatch.setattr(memdedup, "_schedule_dedup", _noop)     # save_memory 尾部异步去重不触发
    monkeypatch.setattr(db_mod, "async_session_factory", factory)  # API/_get_owned_memory 走临时库
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


# ---------------- save_memory：chain 字段落库 ----------------

def test_save_memory_chain字段落库(mem_db, monkeypatch):
    """传 chain_id/parent_id/node_type 时写入对应列，version 默认 0"""
    async def _fake_embed(c):
        return [0.1, 0.2]
    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "add_memory", _noop)
    import app.memory.meaning as meaning_mod
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)

    async def _main():
        m = await memsvc.save_memory(
            user_id=1, character_id=1, memory_type="user_info",
            content="用户喜欢喝美式咖啡", importance=3,
            chain_id="chain-1", parent_id=123, node_type="root",
            skip_dedup=True,
        )
        async with mem_db() as db:
            row = await db.get(Memory, m.id)
        return m, row

    m, row = asyncio.run(_main())
    assert row is not None
    assert row.chain_id == "chain-1"
    assert row.parent_id == 123
    assert row.node_type == "root"
    assert row.version == 0


def test_save_memory_不传chain字段_默认None(mem_db, monkeypatch):
    """向后兼容：不传 chain 参数时 new 列均为 None，version 默认 0"""
    async def _fake_embed(c):
        return [0.3, 0.4]
    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "add_memory", _noop)
    import app.memory.meaning as meaning_mod
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)

    async def _main():
        m = await memsvc.save_memory(
            user_id=1, character_id=1, memory_type="user_info",
            content="用户喜欢喝茶", importance=2, skip_dedup=True,
        )
        async with mem_db() as db:
            row = await db.get(Memory, m.id)
        return row

    row = asyncio.run(_main())
    assert row.chain_id is None
    assert row.parent_id is None
    assert row.node_type is None
    assert row.version == 0


# ---------------- PATCH /{memory_id}/content：version+1 且向量重算 ----------------

def test_content_update_version加1_且向量重算(mem_db, monkeypatch):
    """改内容：version 0→1，向量用新内容重算并覆盖（created_at 保留）"""
    calls = {}

    async def _fake_embed(c):
        calls["text"] = c
        return [0.5, 0.6]

    async def _fake_upsert(memory_id, character_id, memory_type, content, embedding, importance=1):
        calls["upsert"] = (memory_id, character_id, memory_type, content, embedding, importance)

    monkeypatch.setattr("app.memory.embedding.text_embedding", _fake_embed)
    monkeypatch.setattr("app.db.vector_store.upsert_memory_vector", _fake_upsert)

    async def _main():
        m = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                        content="原始内容", importance=50.0)
        orig_created = m.created_at
        resp = await update_memory_content(memory_id=m.id, data={"content": "改写后的内容"}, user_id=1, lang="zh")
        row = await _get(mem_db, m.id)
        return resp, row, orig_created

    resp, row, orig_created = asyncio.run(_main())
    assert resp == {"status": "ok", "version": 1}
    assert row.content == "改写后的内容"
    assert row.version == 1
    assert (row.created_at or orig_created).replace(tzinfo=None) == orig_created.replace(tzinfo=None)  # created_at 保留
    assert calls["text"] == "改写后的内容"
    up = calls.get("upsert")
    assert up is not None
    assert up[0] == row.id
    assert up[1] == row.character_id
    assert up[2] == row.memory_type
    assert up[3] == "改写后的内容"
    assert up[4] == [0.5, 0.6]


def test_content_update_空内容400(mem_db, monkeypatch):
    from fastapi import HTTPException

    async def _main():
        m = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                        content="原始内容", importance=50.0)
        with pytest.raises(HTTPException) as ei:
            await update_memory_content(memory_id=m.id, data={"content": "   "}, user_id=1, lang="zh")
        return ei.value.status_code

    assert asyncio.run(_main()) == 400


def test_content_update_非本人404(mem_db, monkeypatch):
    from fastapi import HTTPException

    async def _main():
        m = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                        content="原始内容", importance=50.0)
        with pytest.raises(HTTPException) as ei:
            await update_memory_content(memory_id=m.id, data={"content": "x"}, user_id=999, lang="zh")
        return ei.value.status_code

    assert asyncio.run(_main()) == 404


# ---------------- DELETE /{memory_id}/tree：级联软删 ----------------

def test_tree_cascade_false_返回直接子列表不删(mem_db, monkeypatch):
    """cascade=false（默认）：仅返回 parent_id==root 的直接子节点列表，不软删"""
    async def _main():
        root = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                           content="根节点", importance=50.0, chain_id="c1", node_type="root")
        c1 = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                         content="子节点1", importance=50.0, chain_id="c1", parent_id=root.id, node_type="leaf")
        c2 = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                         content="子节点2", importance=50.0, chain_id="c1", parent_id=root.id, node_type="leaf")
        other = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                            content="无关节点", importance=50.0, chain_id="c1", parent_id=999, node_type="leaf")
        resp = await remove_memory_tree(memory_id=root.id, cascade=False, user_id=1, lang="zh")
        return resp, await _get(mem_db, root.id), await _get(mem_db, c1.id), await _get(mem_db, c2.id), await _get(mem_db, other.id)

    resp, root, c1, c2, other = asyncio.run(_main())
    assert resp["status"] == "ok"
    assert resp["cascade"] is False
    assert {c["id"] for c in resp["children"]} == {c1.id, c2.id}      # 仅直接子节点
    assert {c["content"] for c in resp["children"]} == {"子节点1", "子节点2"}
    assert root.is_archived is False   # 不删除
    assert c1.is_archived is False
    assert other.is_archived is False  # 非直接子节点不进列表、不删


def test_tree_cascade_true_软删根与子(mem_db, monkeypatch):
    """cascade=true：根 + 直接子逐个软删，is_archived=True，不物理删除"""
    async def _main():
        root = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                           content="根节点", importance=50.0, chain_id="c1", node_type="root")
        c1 = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                         content="子节点1", importance=50.0, chain_id="c1", parent_id=root.id, node_type="leaf")
        c2 = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                         content="子节点2", importance=50.0, chain_id="c1", parent_id=root.id, node_type="leaf")
        resp = await remove_memory_tree(memory_id=root.id, cascade=True, user_id=1, lang="zh")
        return resp, await _get(mem_db, root.id), await _get(mem_db, c1.id), await _get(mem_db, c2.id)

    resp, root, c1, c2 = asyncio.run(_main())
    assert resp == {"status": "ok", "cascade": True, "deleted": 3}
    # #70-C BUG-3：remove_memory_tree 走 purge_memory → 物理删热行（非 is_archived 软删）
    assert root is None
    assert c1 is None
    assert c2 is None


def test_tree_cascade_true_无子节点_只删根(mem_db, monkeypatch):
    async def _main():
        root = await _seed(mem_db, user_id=1, character_id=1, memory_type="event",
                           content="孤立根", importance=50.0, chain_id="c2", node_type="root")
        resp = await remove_memory_tree(memory_id=root.id, cascade=True, user_id=1, lang="zh")
        return resp, await _get(mem_db, root.id)

    resp, root = asyncio.run(_main())
    assert resp == {"status": "ok", "cascade": True, "deleted": 1}
    # #70-C BUG-3：remove_memory_tree 走 purge_memory → 物理删热行（非 is_archived 软删）
    assert root is None
