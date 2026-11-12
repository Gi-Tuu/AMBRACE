# -*- coding: utf-8 -*-
"""A50 批11 守卫：``search_memories`` 的「三路候选合并＋稀疏回填＋计数」块（S6–S9）
机械抽成同文件私有协程 ``_merge_route_candidates`` 之后的行为不变式。

两类判据，**都不是**「把 helper 整个 mock 掉再数调用次数」：

- **行为类**（真临时库 + 真 helper + 真 caller，只把外部依赖打桩）：``hit_count`` **先计数后去重**
  （多路重叠命中的加成不被合并吞掉）、合并顺序 dense→sparse→entity 且**原 dict 对象引用**不变、
  同分时 dense 在前、稀疏只对**新 id** 回填（重叠 id 不进 IN 列表）、回填四键 schema 与
  ``importance`` 默认 0、回填三道过滤（归档/working_state/状态子句）、回填失败只留 warning
  且不冒泡、会话在 helper 内退出（资源随 async with 归还）、``_no_candidates`` 与候选计数是
  **关键词兜底之前**的快照（兜底捞到东西 route 仍是 keyword）、候选计数＝``len(hit_count)``。
- **形状类**（AST 源码断言，不跑 IO）：helper 必须是 ``retrieve.py`` 里的私有协程、caller 只有
  一处调用且按原名字解包六元组、``results``/``hit_count`` 的初始化留在 caller（helper 不另造
  第二份集合）、``async_session_factory`` 由 caller 显式传入且 helper 内零重导入、
  ``select``/``Memory``/``_retrievable_status_clause``/``_logger`` 仍按**裸全局名晚绑定**、
  零 LLM/推理/召回代码节点、关键词兜底仍排在调用点之后。

纪律：临时库走 ``tests/_dbclone``（禁止连生产库）；项目未装 pytest-asyncio，统一 ``asyncio.run``。
本文件**不读生产库、不连队列、不读真实数据**，语料全部是合成字串。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event as sa_event

from _dbclone import clone_engine, make_session_factory

import app.memory.service as svc
from app.agent import loop as _loop
from app.memory import retrieve as _retrieve
from app.models.memory import Memory

pytestmark = pytest.mark.slow

CHAR_ID = 102
MAIN_Q = "主查询乙"
LIKE_Q = "合成关键词兜底词"

_FLAG_KEYS = ("recall_entity_match", "recall_recency_bonus", "recall_neighbor_block",
              "memory_peak_cutoff", "memory_temporal_recall", "perception_source_tag",
              "perception_isolate", "status_memory_ttl", "memory_supersede")


# ─────────────────────────────── flag 隔离 ───────────────────────────────

@pytest.fixture(autouse=True)
def clean_flags():
    """本文件任何用例都不依赖本机 runtime_flags 现值：前后复位（默认全关＋trace 开）。"""
    saved = {k: _loop.AGENT_FLAGS.get(k) for k in _FLAG_KEYS}
    saved_trace = _loop.AGENT_FLAGS.get("memory_trace_debug")

    def _reset():
        for k in _FLAG_KEYS:
            _loop.AGENT_FLAGS[k] = False
        _loop.AGENT_FLAGS["memory_trace_debug"] = True

    _reset()
    yield
    for k, v in saved.items():
        _loop.AGENT_FLAGS[k] = v
    _loop.AGENT_FLAGS["memory_trace_debug"] = saved_trace


class _WarnLog:
    """只收 warning 的假 logger（回填失败那条 warning 必须能被断言到）。"""

    def __init__(self):
        self.warnings: list[tuple] = []

    def warning(self, msg, *a):
        self.warnings.append((msg, a))

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


class _SpySession:
    """记录每次 ``execute`` 传入的**语句对象**并转发其余调用（保持 async with 语义与会话关闭）。"""

    def __init__(self, inner, stmts, log):
        self._inner, self._stmts, self._log = inner, stmts, log

    async def __aenter__(self):
        await self._inner.__aenter__()
        self._log.append("enter")
        return self

    async def __aexit__(self, *exc):
        self._log.append("exit")
        return await self._inner.__aexit__(*exc)

    async def execute(self, stmt, *a, **k):
        self._stmts.append(stmt)
        return await self._inner.execute(stmt, *a, **k)


def _spy_factory(factory):
    """包一层工厂；返回的 callable 带 ``.box``：``stmts`` 收语句对象、``log`` 收 enter/exit、
    ``sessions`` 收真实会话（用于验会话已关闭）。"""
    box = {"stmts": [], "log": [], "sessions": []}

    def _make():
        s = factory()
        box["sessions"].append(s)
        return _SpySession(s, box["stmts"], box["log"])

    _make.box = box
    return _make


def _where_sql(stmt) -> str:
    return str(stmt).partition("WHERE")[2]


def _is_backfill(stmt) -> bool:
    """回填查询的判别：WHERE 里同时出现 ``is_archived`` 与 ``memory_type !=``
    （rerank 那条只有前者；列清单在 WHERE 之前，不参与判别）。"""
    tail = _where_sql(stmt)
    return "memories.is_archived" in tail and "memories.memory_type" in tail


def _in_call_ids(stmt):
    """取出该查询 ``memories.id IN (...)`` 的字面 id 列表（编译参数里那一段）。"""
    for value in stmt.compile().params.values():
        if isinstance(value, (list, tuple)) and value and all(isinstance(v, int) for v in value):
            return list(value)
    return None


# ─────────────────────────────── 临时库夹具 ───────────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时文件库（_dbclone 克隆到 tmp_path）＋外部依赖全打桩，桩都记调用轨迹。"""
    engine = clone_engine(tmp_path / "a50b11_merge.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b11_u1", nickname="批11用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=1, name="批11角色"))
            await db.commit()

    asyncio.run(_seed_parents())

    calls: dict[str, list] = {"text_embedding": [], "vector_search": [], "bm25_search": [],
                              "cached_embedding": [], "entity_route": [], "trace": []}
    trace_box: list[dict] = []
    logger = _WarnLog()

    async def _embed(text):
        calls["text_embedding"].append(text)
        return [0.0] * 8

    async def _cached(character_id, query):
        calls["cached_embedding"].append({"character_id": character_id, "query": query})
        return [0.0] * 8

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        calls["vector_search"].append({"character_id": character_id, "limit": limit})
        return []

    async def _sparse(character_id, query, top_k=5):
        calls["bm25_search"].append({"character_id": character_id, "query": query, "top_k": top_k})
        return []

    async def _entity(character_id, query):
        calls["entity_route"].append({"character_id": character_id, "query": query})
        return [], {"terms": [], "reasons": []}

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _vector)
    monkeypatch.setattr(svc, "bm25_search", _sparse)
    monkeypatch.setattr(_retrieve, "get_cached_embedding", _cached)
    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    monkeypatch.setattr(_retrieve, "_logger", logger)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", lambda *a, **k: [])
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: trace_box.append(kw))

    yield {"engine": engine, "factory": factory, "calls": calls, "trace": trace_box, "logger": logger}

    asyncio.run(engine.dispose())


def _add(factory, content, *, importance=50.0, char=CHAR_ID, mtype="event", created_at=None, **kw):
    """落一条合成记忆（精确控制属性；正文全是合成字串）。"""
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=char, memory_type=mtype, content=content,
                       source="chat", importance=importance,
                       created_at=created_at or (now_naive_utc() - timedelta(minutes=1)), **kw)
            db.add(m)
            await db.commit()
            return m.id

    return asyncio.run(_run())


def _dense_row(mid, distance=0.1, content="合成向量条"):
    return {"id": mid, "content": content, "type": "event", "importance": 50.0, "distance": distance}


def _entity_row(mid, content="合成专名条"):
    return {"id": mid, "content": content, "type": "event", "importance": 50.0}


def _merge(factory, *, dense=None, sparse=None, entity=None):
    """直调真 helper：自带 caller 侧那两个「按原对象传入」的容器。

    返回 ``(helper返回的六元组, caller那份results, caller那份hit_count)``，便于同时验
    「返回的仍是同两个对象」。
    """
    results: list[dict] = []
    hit_count: dict[int, int] = {}

    async def _run():
        return await _retrieve._merge_route_candidates(
            dense_hits=dense if dense is not None else [],
            sparse_hits=sparse if sparse is not None else [],
            entity_rows=entity if entity is not None else [],
            results=results,
            hit_count=hit_count,
            async_session_factory=factory,
        )

    return asyncio.run(_run()), results, hit_count


def _search(query=MAIN_Q, **kw):
    return asyncio.run(_retrieve.search_memories(CHAR_ID, query, **kw))


def _ids(rows):
    return [r["id"] for r in rows]


# ───────────── 1) 运输契约：同对象传回 / 计数口径 / 合并顺序 ─────────────

def test_helper把caller那两个容器原样带回(env):
    """``results``/``hit_count`` 是 caller 初始化后按原对象传入的 ⇒ 返回的就是同两个对象，
    不是拷贝；候选计数恒等于 ``len(hit_count)``（含只在专名路出现过的 id）。"""
    factory = env["factory"]
    dense_id = _add(factory, "合成稠密条甲")
    ent_id = _add(factory, "合成专名条乙")
    got, results, hit_count = _merge(
        factory, dense=[[_dense_row(dense_id)]], entity=[_entity_row(ent_id)])

    assert len(got) == 6, "必须回六元组（results/hit_count/候选数/双路判据/无候选快照）"
    ret_results, ret_hit_count, candidate_count, dense_has, sparse_has, no_candidates = got
    assert ret_results is results, "results 必须原样带回（不许 helper 里另造一份再返回）"
    assert ret_hit_count is hit_count, "hit_count 必须原样带回"
    assert candidate_count == len(hit_count) == 2
    assert (dense_has, sparse_has, no_candidates) == (True, False, False)


def test_三路皆空时无候选快照为真(env):
    """三路皆空 ⇒ ``_no_candidates`` 为 True、双路判据皆 False、候选数 0（不开任何会话）。"""
    factory = env["factory"]
    spy = _spy_factory(factory)
    got, results, hit_count = _merge(spy, dense=[], sparse=[], entity=[])
    assert got[0] is results and results == [] and got[1] is hit_count and hit_count == {}
    assert got[2] == 0 and got[3] is False and got[4] is False and got[5] is True
    assert spy.box["sessions"] == [], "没有稀疏新 id ⇒ helper 一条查询都不该发"


def test_合并顺序dense到sparse到entity且保留原行对象(env):
    """合并序＝dense 返回序 → sparse 新 id → entity 返回序；进池的仍是**原 dict 对象**
    （向量/专名行的字段不会被重查覆盖）。"""
    factory = env["factory"]
    d1 = _add(factory, "合成稠密条一")
    d2 = _add(factory, "合成稠密条二")
    s1 = _add(factory, "合成稀疏条一", importance=40.0)
    e1 = _add(factory, "合成专名条一", importance=30.0)
    d_row_1, d_row_2 = _dense_row(d1, content="向量原文一"), _dense_row(d2, content="向量原文二")
    e_row = _entity_row(e1, content="专名原文一")

    (results, hit_count, count, _dh, _sh, _noc), _r, _h = _merge(
        factory, dense=[[d_row_1], [d_row_2]], sparse=[[(s1, 2.0)]], entity=[e_row])
    assert [r["id"] for r in results] == [d1, d2, s1, e1]
    assert count == 4 == len(hit_count)
    assert results[0] is d_row_1 and results[1] is d_row_2, "向量行必须原对象入池（不得被回填覆盖）"
    assert results[3] is e_row, "专名行原对象入池"
    sparse_row = [r for r in results if r["id"] == s1][0]
    assert set(sparse_row) == {"id", "content", "type", "importance"}, "回填行固定四键"
    assert sparse_row["content"] == "合成稀疏条一"


# ───────────── 2) hit_count：先计数后去重（多路加成不被合并吞掉） ─────────────

def test_重复命中先计数后去重(env):
    """同一 id 在多路/多查询里反复出现：``hit_count`` 每出现一次加一（**先加后判重**），
    而 ``results`` 只进一条。"""
    factory = env["factory"]
    dup = _add(factory, "合成重复命中条")
    other = _add(factory, "合成只出现一次条", importance=30.0)

    (results, hit_count, count, _dh, _sh, _noc), _r, _h = _merge(
        factory,
        dense=[[_dense_row(dup), _dense_row(dup)], [_dense_row(dup)]],
        sparse=[[(dup, 3.0), (other, 1.0)]],
        entity=[_entity_row(dup), _entity_row(dup)],
    )
    assert hit_count[dup] == 6, "稠密3次＋稀疏1次＋专名2次＝6（去重不得吞计数）"
    assert hit_count[other] == 1
    assert [r["id"] for r in results] == [dup, other]
    assert count == 2 == len(hit_count)


def test_同分时dense排在稀疏前面(env, monkeypatch):
    """同 importance、同创建时间、同 RRF rank（两路都排第 1）⇒ 分数完全相等，
    稳定排序下**先合并进来的 dense 行在前**（同分 tie-break ＝合并序，没被搬运打乱）。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    same_at = now_naive_utc() - timedelta(minutes=30)
    d_id = _add(factory, "合成同分稠密条", importance=55.0, created_at=same_at)
    s_id = _add(factory, "合成同分稀疏条", importance=55.0, created_at=same_at)

    async def _vec(character_id, query_embedding, limit=5, **_kw):
        return [_dense_row(d_id)]

    async def _sp(character_id, query, top_k=5):
        return [(s_id, 1.0)]

    monkeypatch.setattr(svc, "vector_search", _vec)
    monkeypatch.setattr(svc, "bm25_search", _sp)
    assert _ids(_search()) == [d_id, s_id]


# ───────────── 3) 稀疏回填：只查新 id / 四键 schema / 三道过滤 ─────────────

def test_重叠id不再回填只查新id(env):
    """与向量路重叠的 id **不进 IN 列表**（它已带全量字段）；只有稀疏独有的新 id 才回填。"""
    factory = env["factory"]
    overlap = _add(factory, "合成两路重叠条")
    fresh = _add(factory, "合成仅稀疏新条", importance=41.0)
    spy = _spy_factory(factory)

    (results, hit_count, _c, _dh, _sh, _noc), _r, _h = _merge(
        spy, dense=[[_dense_row(overlap, content="向量原文")]], sparse=[[(overlap, 2.0), (fresh, 1.5)]])
    backfills = [s for s in spy.box["stmts"] if _is_backfill(s)]
    assert len(backfills) == 1, "只有一条回填查询（实际 %d 条）" % len(backfills)
    assert _in_call_ids(backfills[0]) == [fresh], "IN 列表只能是稀疏新 id，重叠 id 不该再查"
    assert hit_count[overlap] == 2 and hit_count[fresh] == 1
    assert results[0]["content"] == "向量原文", "重叠行保持向量路那份内容，未被回填覆盖"
    assert results[1]["content"] == "合成仅稀疏新条"


def test_回填四键schema与importance是浮点(env):
    """回填行固定四键；``importance`` 走 ``float(...)``（库里 0.0 与 77.5 都原样带回，
    类型恒为 float）。

    诚实边界：``memories.importance`` 列是 **NOT NULL**，``float(x or 0)`` 里 ``or 0``
    那一支在本列上不可达 ⇒ 本例只用真实可达的 0.0 钉「不会被带回成 None/整数」。
    """
    factory = env["factory"]
    has_val = _add(factory, "合成回填有重要度", importance=77.5)
    zero_val = _add(factory, "合成回填零重要度", importance=0.0)

    (results, _hit_count, _c, _dh, _sh, _noc), _r, _h = _merge(
        factory, sparse=[[(has_val, 2.0), (zero_val, 1.0)]])
    assert sorted(r["id"] for r in results) == sorted([has_val, zero_val]), "两条都回填"
    by_id = {r["id"]: r for r in results}
    assert set(by_id[has_val]) == {"id", "content", "type", "importance"}
    assert by_id[has_val]["importance"] == 77.5
    assert by_id[zero_val]["importance"] == 0.0 and isinstance(by_id[zero_val]["importance"], float)
    assert isinstance(by_id[has_val]["importance"], float)


def test_回填过滤归档与工作记忆与状态子句(env):
    """回填 WHERE 三件套逐字保留：``is_archived == False`` ＋ ``memory_type != working_state``
    ＋ ``_retrievable_status_clause()``（flag 关＝永真、开＝{active, stale}）。"""
    factory = env["factory"]
    ok_id = _add(factory, "合成回填正常条")
    arch_id = _add(factory, "合成回填已归档条", is_archived=True)
    ws_id = _add(factory, "合成回填工作记忆条", mtype="working_state")
    sup_id = _add(factory, "合成回填被取代条", status="superseded")
    sparse = [[(ok_id, 4.0), (arch_id, 3.0), (ws_id, 2.0), (sup_id, 1.0)]]

    (off_results, off_hits, _c, _dh, _sh, _noc), _r, _h = _merge(factory, sparse=sparse)
    assert sorted(r["id"] for r in off_results) == sorted([ok_id, sup_id]), \
        "flag 关：归档与工作记忆被挡，被取代条仍可见"

    _loop.AGENT_FLAGS["memory_supersede"] = True
    (on_results, on_hits, _c2, _dh2, _sh2, _noc2), _r2, _h2 = _merge(factory, sparse=sparse)
    assert [r["id"] for r in on_results] == [ok_id], "flag 开：状态子句只放行 {active, stale}"
    assert set(on_hits) == {ok_id, arch_id, ws_id, sup_id}, "过滤只影响入池，hit_count 仍按命中计"


def test_回填失败只warning不冒泡且其余命中保留(env, monkeypatch):
    """回填那段 try/except 与 warning 文案逐字保留：DB 炸了只记一条 warning，
    稠密命中照常入池、检索照常返回（不许抛给调用方，也不许吞掉整块）。"""
    factory = env["factory"]
    dense_id = _add(factory, "合成回填失败时稠密存活条", importance=60.0)
    ghost = 999001   # 库里不存在，但照样进 IN 列表

    async def _vec(character_id, query_embedding, limit=5, **_kw):
        return [_dense_row(dense_id)]

    async def _sp(character_id, query, top_k=5):
        return [(ghost, 1.0)]

    class _BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_a, **_k):
            raise RuntimeError("回填库炸了")

    made = {"n": 0}

    def _factory():
        """第一个会话炸（回填是本轮第一处开会话的地方），之后转真工厂供 _rerank 用。"""
        made["n"] += 1
        return _BoomSession() if made["n"] == 1 else factory()

    monkeypatch.setattr(svc, "async_session_factory", _factory)
    monkeypatch.setattr(svc, "vector_search", _vec)
    monkeypatch.setattr(svc, "bm25_search", _sp)
    assert _ids(_search()) == [dense_id]
    warns = [w for w in env["logger"].warnings if "BM25 sparse hit enrich failed" in w[0]]
    assert len(warns) == 1, "必须且只能留一条回填失败 warning（实际 %d 条）" % len(warns)


def test_回填会话在helper内退出不跨会话持有(env):
    """``async with async_session_factory()`` 的作用域仍在 helper 内：进出成对，
    helper 返回时连接已 checkin/close 归还（临时库是 NullPool ⇒ 直接断连），
    没有把会话挪到 helper 之外跨函数持有。"""
    factory = env["factory"]
    fresh = _add(factory, "合成会话退出条")
    spy = _spy_factory(factory)
    events: list[str] = []
    pool = env["engine"].sync_engine.pool
    handlers = []
    for name in ("connect", "checkout", "checkin", "close"):
        h = (lambda *a, _n=name: events.append(_n))
        handlers.append((name, h))
        sa_event.listen(pool, name, h)
    try:
        (_got, _hc, _c, _dh, _sh, _noc), _r, _h = _merge(spy, sparse=[[(fresh, 1.0)]])
    finally:
        for name, h in handlers:
            sa_event.remove(pool, name, h)

    assert spy.box["log"] == ["enter", "exit"], "必须整段进出成对（会话作用域没被挪走/漏掉）"
    assert len(spy.box["sessions"]) == 1, "helper 只开一个会话"
    assert events[:2] == ["connect", "checkout"] and "checkin" in events and events[-1] == "close", \
        "会话没归还＝连接仍被占着（实际事件 %r）" % (events,)


# ───────────── 4) 快照口径：_no_candidates / 候选计数取在兜底之前 ─────────────

def test_无候选快照取在关键词兜底之前(env):
    """三路皆空但 LIKE 兜底捞到东西：``_no_candidates`` 仍是 True（route=keyword），
    候选数被改写成兜底结果条数——**不能**跟着兜底结果翻成「有候选」。"""
    factory = env["factory"]
    kw1 = _add(factory, "合成兜底语料一 " + LIKE_Q, importance=70.0)
    kw2 = _add(factory, "合成兜底语料二 " + LIKE_Q, importance=65.0)

    assert _ids(_search(LIKE_Q)) == [kw1, kw2]
    tr = env["trace"][-1]
    assert tr["route"] == "keyword", "兜底捞到东西也仍是 keyword（_no_candidates 是兜底前快照）"
    steps = json.loads(tr["steps_json"])
    assert steps["candidate_count"] == 2 == steps["hit_count"], "候选数＝兜底结果条数"
    assert {r["id"] for r in steps["returned"]} == {kw1, kw2}


def test_仅稀疏命中时route为sparse且候选数等于命中数(env, monkeypatch):
    """只有稀疏路出候选：``_dense_has`` False／``_sparse_has`` True ⇒ route=sparse，
    候选计数＝三路合并去重后的命中数（含回填补不回来的 id）。"""
    factory = env["factory"]
    s1 = _add(factory, "合成仅稀疏条一", importance=40.0)
    ghost = 999002   # 库里没有 ⇒ 回填取不到，但仍是命中（候选池口径按命中数算）

    async def _sp(character_id, query, top_k=5):
        return [(s1, 2.0), (ghost, 1.0)]

    monkeypatch.setattr(svc, "bm25_search", _sp)
    assert _ids(_search()) == [s1]
    tr = env["trace"][-1]
    assert tr["route"] == "sparse"
    steps = json.loads(tr["steps_json"])
    assert steps["candidate_count"] == 2, "候选计数＝len(hit_count)（命中口径，非入池口径）"
    assert steps["hit_count"] == steps["candidate_count"]


def test_专名路独占候选时route为entity且命中计数进池(env, monkeypatch):
    """双路皆空、只有专名路出候选 ⇒ route=entity（flag 关时到不了该分支，既有取值集合不变）。"""
    factory = env["factory"]
    e1 = _add(factory, "合成专名独占条", importance=35.0)
    row = _entity_row(e1, content="合成专名独占条")

    async def _entity(character_id, query):
        return [row], {"terms": ["合成"], "reasons": ["stub"]}

    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    _loop.AGENT_FLAGS["recall_entity_match"] = True
    assert _ids(_search("zzz查不到任何东西")) == [e1]
    assert env["trace"][-1]["route"] == "entity"
    assert json.loads(env["trace"][-1]["steps_json"])["candidate_count"] == 1


def test_entity重叠只加计数不换行也不补查(env, monkeypatch):
    """专名行与向量行同 id：只加一路命中计数，**行不被替换**；无稀疏新 id ⇒ 一条回填查询都不发。"""
    factory = env["factory"]
    mid = _add(factory, "合成实体重叠条")
    dense_row = _dense_row(mid, content="向量原文甲")
    entity_row = _entity_row(mid, content="专名原文甲")

    async def _vec(character_id, query_embedding, limit=5, **_kw):
        return [dense_row]

    async def _entity(character_id, query):
        return [entity_row], {"terms": ["重叠"], "reasons": ["stub"]}

    spy = _spy_factory(factory)
    monkeypatch.setattr(svc, "async_session_factory", spy)
    monkeypatch.setattr(svc, "vector_search", _vec)
    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    _loop.AGENT_FLAGS["recall_entity_match"] = True
    out = _search()
    assert _ids(out) == [mid]
    assert out[0]["content"] == "合成实体重叠条", "重叠合并后正文必须回填 SQLite 现行值"
    assert [s for s in spy.box["stmts"] if _is_backfill(s)] == [], "无稀疏新 id ⇒ 不该发回填查询"
    assert json.loads(env["trace"][-1]["steps_json"])["candidate_count"] == 1


# ───────────── 5) 形状类守卫（AST，不跑 IO） ─────────────

def _module_node(name):
    src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("retrieve.py 里找不到 %s" % name)


def test_helper是同文件私有协程且caller只有一处调用():
    helper, caller = _module_node("_merge_route_candidates"), _module_node("search_memories")
    assert isinstance(helper, ast.AsyncFunctionDef), "必须是协程（块内含 await 会话回填）"
    assert helper.name.startswith("_"), "必须是私有名"
    calls = [n for n in ast.walk(caller)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "_merge_route_candidates"]
    assert len(calls) == 1, "caller 内必须只有一处调用（实际 %d 处）" % len(calls)
    call = calls[0]
    awaits = [n for n in ast.walk(caller) if isinstance(n, ast.Await) and n.value is call]
    assert len(awaits) == 1, "调用必须被 await"
    assert [kw.arg for kw in call.keywords] == [
        "dense_hits", "sparse_hits", "entity_rows", "results", "hit_count",
        "async_session_factory"], "六个实参必须显式按名传入"
    assign = next(s for s in caller.body
                  if isinstance(s, ast.Assign) and "_merge_route_candidates" in ast.unparse(s.value))
    targets = [t.id for t in assign.targets[0].elts]
    assert targets == ["results", "hit_count", "candidate_count", "_dense_has",
                       "_sparse_has", "_no_candidates"], "必须按原名字解包六个返回值"
    rets = [s for s in helper.body if isinstance(s, ast.Return)]
    assert len(rets) == 1 and ast.unparse(rets[0].value) == \
        "(results, hit_count, candidate_count, _dense_has, _sparse_has, _no_candidates)", \
        "helper 只多一条 return 运输"


def test_helper入参是keywordonly且两个容器由caller初始化():
    helper, caller = _module_node("_merge_route_candidates"), _module_node("search_memories")
    assert helper.args.args == [] and helper.args.defaults == []
    assert [a.arg for a in helper.args.kwonlyargs] == [
        "dense_hits", "sparse_hits", "entity_rows", "results", "hit_count",
        "async_session_factory"]
    # 初始化必须留在 caller：helper 内不得再出现第二份空容器
    caller_src = ast.unparse(caller)
    assert "results: list[dict] = []" in caller_src and "hit_count: dict[int, int] = {}" in caller_src
    helper_src = ast.unparse(helper)
    for bad in ("results: list[dict] = []", "hit_count: dict[int, int] = {}"):
        assert bad not in helper_src, "helper 内不该再初始化 %r" % bad
    assert "_seen_ids: set[int] = set()" in helper_src, "去重集合按原样留在 helper 内"


def test_helper零重导入且依赖按裸全局名晚绑定():
    helper = _module_node("_merge_route_candidates")
    src = ast.get_source_segment(Path(_retrieve.__file__).read_text(encoding="utf-8"), helper)
    assert "from app.memory.service import" not in src, "helper 不得自己重导入 service 符号"
    assert "import app.memory.service" not in src
    imports = [n for n in ast.walk(helper) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imports == [], "原块没有 import，helper 也不该新增（逐字搬运，不增不减）"
    loaded = {n.id for n in ast.walk(helper) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    for name in ("select", "Memory", "_retrievable_status_clause", "_logger"):
        assert name in loaded, "%s 必须继续按裸全局名晚绑定" % name
    code = [ast.unparse(n) for n in ast.walk(helper)
            if isinstance(n, (ast.Call, ast.Import, ast.ImportFrom, ast.Name, ast.Attribute))]
    for bad in ("chat_completion", "llm_client", "encode", "inference", "vector_search", "bm25_search"):
        assert not [c for c in code if bad in c], "helper 代码里出现 %r：合并块不许调模型或再召回" % bad


def test_关键词兜底与缺快照仍排在调用点之后():
    caller = _module_node("search_memories")
    body = list(caller.body)
    call_i = next(i for i, s in enumerate(body)
                  if isinstance(s, ast.Assign) and "_merge_route_candidates" in ast.unparse(s.value))
    fb_i = next(i for i, s in enumerate(body)
                if isinstance(s, ast.If) and ast.unparse(s.test) == "not results")
    assert fb_i > call_i, "关键词兜底必须在合并调用之后（_no_candidates 快照在它之前）"
    # 文本类断言走**原始源码切片**（unparse 会重排 import 括号等格式，不是原样）
    src = ast.get_source_segment(Path(_retrieve.__file__).read_text(encoding="utf-8"), caller)
    fallback = _module_node("_keyword_fallback")
    fallback_src = ast.get_source_segment(Path(_retrieve.__file__).read_text(encoding="utf-8"), fallback)
    assert "candidate_count = len(results)" in fallback_src, "批12迁宿主，候选数覆盖仍保留"
    assert "_like_escape(query)" in fallback_src and "order_by" in fallback_src, "转义＋排序仍在兜底"
    assert "async with async_session_factory() as db:" in fallback_src, "会话作用域随段不变"
    assert ast.unparse(body[fb_i].body[0]).startswith("results, candidate_count = await _keyword_fallback(")
    # 批14融合随段搬出，caller局部导入仍在并按原时点显式传入。
    assert "from app.memory.service import (" in src
    fuse = _module_node("_fuse_route_relevance")
    fuse_src = ast.get_source_segment(Path(_retrieve.__file__).read_text(encoding="utf-8"), fuse)
    assert "_rrf.reciprocal_rank_fusion" in fuse_src
    fuse_calls = [n for n in ast.walk(caller) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name) and n.func.id == "_fuse_route_relevance"]
    assert len(fuse_calls) == 1
    assert any(k.arg == "_rrf" and ast.unparse(k.value) == "_rrf" for k in fuse_calls[0].keywords)


def test_签名与公共出口未变():
    """helper 抽走不改 ``search_memories`` 的签名（service 垫片与全部调用方零感知）。"""
    sig = inspect.signature(_retrieve.search_memories)
    assert list(sig.parameters) == ["character_id", "query", "limit", "queries", "trace_meta",
                                    "time_range", "scene", "exclude_sources", "group_id", "user_id"]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in sig.parameters.values())
    assert inspect.iscoroutinefunction(_retrieve.search_memories)
