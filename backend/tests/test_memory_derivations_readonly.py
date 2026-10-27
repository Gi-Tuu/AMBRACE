# -*- coding: utf-8 -*-
"""RC2 G2：派生关系接口的**只读**边界合同。

不是源码断言：执行所有 GET（含分页/坏数据/404），前后对核心表做整表快照比较；
并把向量写函数替换成故障哨兵——一旦被调用即抛错，证明请求链未触发写入。
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import memories as memories_api
from app.auth.deps import get_current_user_id
from app.db import database as db_mod
from app.db import vector_store as vs
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.user import User

_SNAPSHOT_SQL = {
    "memories": "SELECT id,user_id,character_id,memory_type,title,content,status,is_archived,"
                "review_count,last_reinforce_at,next_review_at,derived_from_ids,updated_at "
                "FROM memories ORDER BY id",
    "flags": "SELECT * FROM runtime_flags ORDER BY key",
    "receipts": "SELECT * FROM memory_write_receipts ORDER BY id",
}


@pytest.fixture()
def ro_db(monkeypatch, tmp_path):
    engine = clone_engine(str(tmp_path / "g2ro.db"))
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)

    def _boom(*a, **k):
        raise AssertionError("派生关系只读接口不得调用向量写函数")

    for name in ("add_memory", "upsert_memory_vector", "delete_memory_vector",
                 "delete_memory_vectors_by_character", "delete_memory_vectors_by_user",
                 "mark_memory_vector_status"):
        if hasattr(vs, name):
            monkeypatch.setattr(vs, name, _boom)
    yield factory
    asyncio.run(engine.dispose())


async def _snapshot(factory):
    out = {}
    async with factory() as db:
        for name, sql in _SNAPSHOT_SQL.items():
            rows = (await db.execute(text(sql))).all()
            out[name] = json.dumps([[str(v) for v in row] for row in rows], ensure_ascii=False)
    return out


async def _seed(factory):
    async with factory() as db:
        db.add_all([User(id=1, username="ro_u1", nickname="甲"),
                    AICharacter(id=10, user_id=1, name="c10")])
        await db.commit()
        db.add_all([
            Memory(id=100, user_id=1, character_id=10, memory_type="event",
                   content="当前", derived_from_ids="[101, 102, 999]"),
            Memory(id=101, user_id=1, character_id=10, memory_type="event", content="父一"),
            Memory(id=102, user_id=1, character_id=10, memory_type="event", content="父二"),
            Memory(id=103, user_id=1, character_id=10, memory_type="event",
                   content="子", derived_from_ids="[100]"),
            Memory(id=104, user_id=1, character_id=10, memory_type="event",
                   content="坏", derived_from_ids="not-json"),
            Memory(id=105, user_id=1, character_id=10, memory_type="event",
                   content="字符串成员", derived_from_ids='["100"]'),
        ])
        await db.commit()


def test_所有GET前后整表快照一致且向量写未被调用(ro_db):
    asyncio.run(_seed(ro_db))
    before = asyncio.run(_snapshot(ro_db))

    app = FastAPI()
    app.include_router(memories_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1
    client = TestClient(app)

    responses = [
        client.get("/api/v1/memories/100/derivations"),
        client.get("/api/v1/memories/100/derivations", params={"limit": 1}),
        client.get("/api/v1/memories/100/derivations", params={"limit": 1, "children_cursor": "1"}),
        client.get("/api/v1/memories/104/derivations"),
        client.get("/api/v1/memories/9999/derivations"),
        client.get("/api/v1/memories/100/derivations", params={"parents_cursor": "bad"}),
    ]
    assert responses[0].status_code == 200
    assert responses[1].status_code == 200
    assert responses[2].status_code == 200
    assert responses[3].status_code == 200          # 坏 JSON 只读不修
    assert responses[4].status_code == 404
    assert responses[5].status_code == 400

    after = asyncio.run(_snapshot(ro_db))
    assert after == before, "派生关系 GET 不得改动记忆/旗标/回执"


def test_连续分页读取不改变数据(ro_db):
    asyncio.run(_seed(ro_db))
    before = asyncio.run(_snapshot(ro_db))
    app = FastAPI()
    app.include_router(memories_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1
    client = TestClient(app)
    cursor = None
    for _ in range(5):
        params = {"limit": 1}
        if cursor:
            params["children_cursor"] = cursor
        r = client.get("/api/v1/memories/100/derivations", params=params)
        assert r.status_code == 200
        cursor = r.json()["children"].get("next_cursor")
        if not cursor:
            break
    assert asyncio.run(_snapshot(ro_db)) == before
