# -*- coding: utf-8 -*-
"""A1 向量库「账号归属」单测（2026-09-19，派单 §三.6）。

不连真实 Chroma、不碰 backend/data：以假 collection monkeypatch
``app.db.vector_store.get_or_create_collection``，owner 查询以 monkeypatch
``_character_owner_of`` 注入（参考 tests/test_chain_builder.py 的沙箱写法）。

覆盖：
- where 构造：flag 关=旧行为（与 _char_where 逐字节一致）；flag 开=$and[char_clause,
  user_id $in scope]；user_ids 缺省=角色 owner；owner 查不到=fail-open（不加子句）。
- 写入路径：写 user_id；user_id=None 回退 owner；显式 user_id 优先；owner 查不到不写键且不抛。
- 开 flag 时「缺 user_id 键」的向量不被召回（预期副作用，必须先回填再开 flag）。
- owner 进程内缓存（上限 512）。
"""
import asyncio

import pytest

import app.db.vector_store as _vs


# ---------------- 假 Chroma ----------------

def _match(meta: dict, where: dict | None) -> bool:
    """最小 Chroma where 解释器：$and / 等值 / $in（缺键视为不匹配，与 Chroma 语义一致）。"""
    if where is None:
        return True
    if "$and" in where:
        return all(_match(meta, c) for c in where["$and"])
    for key, cond in where.items():
        if isinstance(cond, dict):
            if "$in" not in cond or meta.get(key) not in cond["$in"]:
                return False
        elif meta.get(key) != cond:
            return False
    return True


class _FakeCollection:
    """假 collection：记录最近一次 where，query/get 按 where 过滤。"""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.wheres: list = []

    def seed(self, doc_id, metadata, embedding=None, document=""):
        self.items[str(doc_id)] = {
            "embedding": embedding if embedding is not None else [0.0],
            "document": document or "",
            "metadata": dict(metadata),
        }

    def add(self, ids, embeddings, documents, metadatas):
        for i, doc_id in enumerate(ids):
            self.seed(doc_id, metadatas[i], embeddings[i], documents[i])

    def upsert(self, ids, embeddings, documents, metadatas):
        self.add(ids, embeddings, documents, metadatas)

    def update(self, ids, metadatas):
        for i, doc_id in enumerate(ids):
            self.items[str(doc_id)]["metadata"] = dict(metadatas[i])

    def query(self, query_embeddings, n_results, where=None):
        self.wheres.append(where)
        hits = [(k, v) for k, v in self.items.items() if _match(v["metadata"], where)][:n_results]
        return {
            "ids": [[k for k, _ in hits]],
            "documents": [[v["document"] for _, v in hits]],
            "metadatas": [[v["metadata"] for _, v in hits]],
            "distances": [[0.05 for _ in hits]],
        }

    def get(self, ids=None, where=None, include=None):
        self.wheres.append(where)
        keys = list(self.items)
        if ids is not None:
            keep = set(ids)
            keys = [k for k in keys if k in keep]
        elif where is not None:
            keys = [k for k in keys if _match(self.items[k]["metadata"], where)]
        return {
            "ids": keys,
            "metadatas": [self.items[k]["metadata"] for k in keys],
            "embeddings": [self.items[k]["embedding"] for k in keys],
        }


@pytest.fixture()
def fake(monkeypatch):
    """假 collection + owner 映射（测试内直接 owners[char_id] = user_id 注入）。"""
    col = _FakeCollection()
    owners: dict[int, int] = {}

    async def _fake_collection():
        return col

    async def _owner(character_id):
        return owners.get(character_id)

    monkeypatch.setattr(_vs, "get_or_create_collection", _fake_collection)
    monkeypatch.setattr(_vs, "_character_owner_of", _owner)
    monkeypatch.setattr(_vs, "_OWNER_CACHE", {})
    return col, owners


def _flag_on(monkeypatch, value=True):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "vector_user_scope", value)


def _meta(char_id, **extra):
    m = {"memory_id": 1, "character_id": char_id, "memory_type": "event",
         "importance": 1, "status": "active"}
    m.update(extra)
    return m


# ---------------- where 构造 ----------------

def test_flag关_char_where逐字节一致():
    """flag 关：_char_where_scoped 必须与旧 _char_where 结果逐字节一致（连 where 都不变）。"""
    for supersede in (False, True):
        assert _vs._char_where_scoped(5, supersede, None, None) == _vs._char_where(5, supersede)
    assert _vs._char_where_scoped(5, False, "active", None) == _vs._char_where(5, False, "active")
    assert _vs._char_where(5, False) == {"character_id": 5}   # 既有断言继续全绿


def test_flag开_scope_where结构():
    assert _vs._char_where_scoped(5, False, None, [3]) == {
        "$and": [{"character_id": 5}, {"user_id": {"$in": [3]}}]}
    assert _vs._char_where_scoped(5, True, None, [4, 3]) == {
        "$and": [
            {"$and": [{"character_id": 5}, {"status": {"$in": ["active", "stale"]}}]},
            {"user_id": {"$in": [3, 4]}},
        ]}


def test_flag开_scope为空不加子句():
    # scope 解析不出（owner 查不到）：不追加 user_id 子句（fail-open，保召回）
    assert _vs._char_where_scoped(5, False, None, []) == {"character_id": 5}


# ---------------- 读路径 where ----------------

def test_读路径_flag关_where旧行为(fake, monkeypatch):
    col, _owners = fake
    _flag_on(monkeypatch, False)
    col.seed(1, _meta(5))
    out = asyncio.run(_vs.search_memories(5, [0.0]))
    assert col.wheres[-1] == {"character_id": 5}
    assert [m["id"] for m in out] == [1]


def test_读路径_flag开_缺省回退角色owner(fake, monkeypatch):
    col, owners = fake
    owners[5] = 3
    _flag_on(monkeypatch)
    col.seed(1, _meta(5, user_id=3))
    col.seed(2, _meta(5, user_id=4))
    out = asyncio.run(_vs.search_memories(5, [0.0]))
    assert col.wheres[-1] == {"$and": [{"character_id": 5}, {"user_id": {"$in": [3]}}]}
    assert [m["id"] for m in out] == [1]   # 只召回本账号


def test_读路径_flag开_显式user_ids并集去重排序(fake, monkeypatch):
    col, _owners = fake
    _flag_on(monkeypatch)
    col.seed(1, _meta(5, user_id=3))
    col.seed(2, _meta(5, user_id=7))
    out = asyncio.run(_vs.search_memories(5, [0.0], user_ids=[7, 3, 3]))
    assert col.wheres[-1]["$and"][1] == {"user_id": {"$in": [3, 7]}}
    assert sorted(m["id"] for m in out) == [1, 2]


def test_读路径_flag开_owner查不到failopen(fake, monkeypatch):
    # owners 未注入 → owner None → scope 空 → 不加 user_id 子句
    col, _owners = fake
    _flag_on(monkeypatch)
    col.seed(1, _meta(5))   # 无 user_id 键
    out = asyncio.run(_vs.search_memories(5, [0.0]))
    assert col.wheres[-1] == {"character_id": 5}
    assert [m["id"] for m in out] == [1]


def test_flag开_缺user_id键的向量不被召回(fake, monkeypatch):
    """预期副作用（断言并注释）：开 scope 过滤后，metadata 缺 user_id 的向量整批漏掉。

    这正是上线顺序「先回填 user_id、再开 vector_user_scope」的原因；本断言固化该副作用。
    """
    col, owners = fake
    owners[5] = 3
    _flag_on(monkeypatch)
    col.seed(1, _meta(5))              # 缺 user_id（未回填）→ 开 flag 后不可召回
    col.seed(2, _meta(5, user_id=3))   # 已回填 → 正常召回
    out = asyncio.run(_vs.search_memories(5, [0.0]))
    assert [m["id"] for m in out] == [2]


def test_get_all_vectors_flag关_旧where(fake, monkeypatch):
    col, _owners = fake
    _flag_on(monkeypatch, False)
    col.seed(1, _meta(5), embedding=[0.1])
    col.seed(2, _meta(5, user_id=4), embedding=[0.2])
    out = asyncio.run(_vs.get_all_vectors_by_character(5))
    assert col.wheres[-1] == {"character_id": 5}
    assert set(out) == {1, 2}


def test_get_all_vectors_flag开_按scope过滤(fake, monkeypatch):
    col, owners = fake
    owners[5] = 3
    _flag_on(monkeypatch)
    col.seed(1, _meta(5, user_id=3), embedding=[0.1])
    col.seed(2, _meta(5, user_id=4), embedding=[0.2])
    out = asyncio.run(_vs.get_all_vectors_by_character(5))
    assert col.wheres[-1] == {"$and": [{"character_id": 5}, {"user_id": {"$in": [3]}}]}
    assert set(out) == {1}


def test_find_similar_flag开_按scope过滤(fake, monkeypatch):
    col, owners = fake
    owners[5] = 3
    _flag_on(monkeypatch)
    col.seed(1, _meta(5, user_id=3), embedding=[1.0])
    col.seed(2, _meta(5, user_id=4), embedding=[1.0])
    got = asyncio.run(_vs.find_similar_memory(5, [1.0], limit=5, min_similarity=0.5))
    assert got == (1, pytest.approx(0.95))
    # current_facts_active_only 默认 True → 内层带 active 状态子句，外层再包 scope
    assert col.wheres[-1] == _vs._char_where_scoped(5, False, "active", [3])


# ---------------- 写入路径 ----------------

def test_写入_add回退owner写user_id(fake, monkeypatch):
    col, owners = fake
    owners[5] = 3
    asyncio.run(_vs.add_memory(11, 5, "event", "hello", [0.1]))
    assert col.items["11"]["metadata"]["user_id"] == 3
    assert col.items["11"]["metadata"]["status"] == "active"


def test_写入_显式user_id优先于owner(fake, monkeypatch):
    col, owners = fake
    owners[5] = 3
    asyncio.run(_vs.add_memory(11, 5, "event", "hello", [0.1], user_id=9))
    assert col.items["11"]["metadata"]["user_id"] == 9


def test_写入_owner查不到不写键且不抛(fake, monkeypatch):
    col, _owners = fake   # 未注入 owner
    asyncio.run(_vs.add_memory(11, 5, "event", "hello", [0.1]))
    meta = col.items["11"]["metadata"]
    assert "user_id" not in meta
    assert meta["status"] == "active"   # 其余键照旧


def test_写入_upsert写user_id(fake, monkeypatch):
    col, owners = fake
    owners[5] = 3
    asyncio.run(_vs.upsert_memory_vector(11, 5, "event", "hello", [0.1]))
    assert col.items["11"]["metadata"]["user_id"] == 3


# ---------------- owner 进程内缓存 ----------------

def test_owner缓存只查一次(monkeypatch):
    monkeypatch.setattr(_vs, "_OWNER_CACHE", {})
    calls = []

    class _Res:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, stmt):
            calls.append(stmt)
            return _Res(3)

    from app.db import database as _db
    monkeypatch.setattr(_db, "async_session_factory", lambda: _Session())

    async def _main():
        return await _vs._character_owner_of(5), await _vs._character_owner_of(5)

    assert asyncio.run(_main()) == (3, 3)
    assert len(calls) == 1   # 第二次命中缓存，未再打库


def test_owner缓存上限淘汰最旧(monkeypatch):
    monkeypatch.setattr(_vs, "_OWNER_CACHE", {})
    for i in range(_vs._OWNER_CACHE_MAX):
        _vs._remember_owner(i, i)
    assert len(_vs._OWNER_CACHE) == _vs._OWNER_CACHE_MAX
    _vs._remember_owner(99999, 99999)
    assert len(_vs._OWNER_CACHE) == _vs._OWNER_CACHE_MAX
    assert 0 not in _vs._OWNER_CACHE and 99999 in _vs._OWNER_CACHE
