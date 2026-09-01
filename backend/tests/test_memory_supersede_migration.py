# -*- coding: utf-8 -*-
"""#70 方案C：迁移可逆 + 存量 status 回填 + 管理/调试接口（归属校验）测试。

- test_migration_upgrade_downgrade：临时库跑 alembic upgrade head → 列/索引/表存在；downgrade 可逆删除。
- test_backfill：缺 status 的向量 metadata 补 "active"。
- test_api_supersede_restore：管理/调试接口归属校验与调用成功/失败。
"""
import asyncio
import os
import sqlite3
import tempfile

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def mig_db(monkeypatch):
    """临时库：把 settings.database_url 指向临时 DB，跑真实 alembic 迁移链。"""
    import app.config as cfg
    tmp = tempfile.mkdtemp(prefix="memory_supersede_mig_")
    db_path = os.path.join(tmp, "mig.db")
    monkeypatch.setattr(cfg.settings, "database_url", "sqlite+aiosqlite:///" + db_path)
    yield db_path


def test_migration_upgrade_downgrade(mig_db):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    db_path = mig_db
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))

    command.upgrade(cfg, "head")
    eng = create_engine("sqlite:///" + db_path)
    insp = inspect(eng)
    cols = {c["name"] for c in insp.get_columns("memories")}
    idx = {i["name"] for i in insp.get_indexes("memories")}
    for c in ("status", "superseded_by", "valid_from", "valid_to", "derived_from_ids"):
        assert c in cols, f"upgrade 后缺列 {c}"
    assert "idx_memories_char_status" in idx
    assert insp.has_table("memory_archive")
    eng.dispose()

    command.downgrade(cfg, "a6b7c8d9e0f1")  # 只回退本 revision
    eng2 = create_engine("sqlite:///" + db_path)
    insp2 = inspect(eng2)
    cols2 = {c["name"] for c in insp2.get_columns("memories")}
    idx2 = {i["name"] for i in insp2.get_indexes("memories")}
    for c in ("status", "superseded_by", "valid_from", "valid_to", "derived_from_ids"):
        assert c not in cols2, f"downgrade 后列 {c} 未删"
    assert "idx_memories_char_status" not in idx2
    assert not insp2.has_table("memory_archive")
    eng2.dispose()


# ---------------- backfill：缺 status 补 active ----------------

def test_backfill(monkeypatch):
    import importlib.util
    import app.config as cfg

    tmp = tempfile.mkdtemp(prefix="memory_supersede_bf_")
    db_path = os.path.join(tmp, "bf.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, character_id INTEGER)")
    conn.execute("INSERT INTO memories (character_id) VALUES (1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cfg.settings, "database_url", "sqlite+aiosqlite:///" + db_path)

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script_path = os.path.join(project_root, "scripts", "backfill_memory_status.py")
    spec = importlib.util.spec_from_file_location("backfill_memory_status", script_path)
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)

    class _FakeCol:
        def __init__(self):
            self.updates = []
        def get(self, ids=None, include=None, where=None):
            # 只返回缺 status 的一条（模拟存量向量）
            metas = [{"memory_id": 1, "character_id": 1}]
            return {"ids": ["1"], "metadatas": metas}
        def update(self, ids=None, metadatas=None):
            self.updates.append((list(ids or []), list(metadatas or [])))

    fake = _FakeCol()

    async def _get_collection():
        return fake
    async def _get_vecs(cid):
        return {1: [0.1, 0.2]}

    monkeypatch.setattr(bf, "get_or_create_collection", _get_collection)
    monkeypatch.setattr(bf, "get_all_vectors_by_character", _get_vecs)
    fixed = asyncio.run(bf.backfill_characters())
    assert fixed == 1
    assert len(fake.updates) == 1
    ids, metas = fake.updates[0]
    assert ids == ["1"]
    assert metas[0]["status"] == "active"   # 缺 status → 补 active


# ---------------- 管理/调试接口：归属校验 + 成功/失败 ----------------

@pytest.fixture()
def api_db(monkeypatch):
    import app.db.database as db_mod
    import app.memory.service as memsvc
    import app.memory.supersede as sup
    import app.db.vector_store as vs
    import app.memory.bm25_index as bm25

    tmp = tempfile.mkdtemp(prefix="memory_supersede_api_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(sup, "async_session_factory", factory)
    monkeypatch.setattr(vs, "mark_memory_vector_status", _noop)
    monkeypatch.setattr(vs, "delete_memory_vector", _noop)
    monkeypatch.setattr(bm25, "invalidate", lambda *a, **k: None)
    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)
    monkeypatch.setattr(memsvc, "bm25_invalidate", lambda *a, **k: None)
    yield factory, engine
    asyncio.run(engine.dispose())


def test_api_supersede_restore(api_db):
    factory, _ = api_db
    from app.api.memories import supersede_memory_api, restore_memory_api

    async def _seed(user_id=1, **kw):
        async with factory() as db:
            m = Memory(user_id=user_id, character_id=1, memory_type="preference",
                       content="用户喜欢薄荷糖", importance=40, **kw)
            db.add(m)
            await db.commit()
            await db.refresh(m)
            return m

    async def _main():
        # 归属校验：用户 2 不能取代用户 1 的记忆
        m_owned = await _seed(user_id=1)
        try:
            await supersede_memory_api(memory_id=m_owned.id, data={"new_id": None},
                                       user_id=2, lang="zh")
            owned_denied = False
        except HTTPException:
            owned_denied = True
        # 成功：用户 1 取代自己的记忆
        ok = await supersede_memory_api(memory_id=m_owned.id, data={"new_id": None, "reason": "debug"},
                                        user_id=1, lang="zh")
        # 重复取代 → 失败（400）
        try:
            await supersede_memory_api(memory_id=m_owned.id, data={"new_id": None},
                                       user_id=1, lang="zh")
            repeat_failed = False
        except HTTPException:
            repeat_failed = True
        # 恢复：回滚 supersede
        r = await restore_memory_api(memory_id=m_owned.id, user_id=1, lang="zh")
        # 恢复后再次取代应成功
        ok2 = await supersede_memory_api(memory_id=m_owned.id, data={"new_id": None},
                                         user_id=1, lang="zh")
        async with factory() as db:
            row = await db.get(Memory, m_owned.id)
        return owned_denied, ok, repeat_failed, r, ok2, row.status

    owned_denied, ok, repeat_failed, r, ok2, status = asyncio.run(_main())
    assert owned_denied is True           # 非归属 → 404
    assert ok["status"] == "ok" and ok["superseded"] is True
    assert repeat_failed is True          # 已 superseded 再取代 → 400
    assert r["status"] == "ok" and r["restored"] is True
    assert ok2["status"] == "ok"
    assert status == "superseded"
