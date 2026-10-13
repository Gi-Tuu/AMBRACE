# -*- coding: utf-8 -*-
"""A50 批14：``search_memories`` 里**最后两段**——RRF 融合与检索 trace 留痕——与同文件私有**函数**
``_fuse_route_relevance`` / ``_emit_search_trace`` 之间的行为边界守卫。

这两段是检索链的**尾段**：一段把三路召回的 rank 证据融合成 ``relevance_bonus`` 交给 ``_rerank``，
一段把 debug/精简两套 payload 落进 ``agent_task_logs``。抽出之后守卫钉的是**行为**不是行号：

- **RRF 段**：三路 rank 列表的装配**顺序**（dense 各路 → sparse 各路 → entity 一路，entity 空时不追加）、
  ``_BRRF_DEFAULT_K``／``_RRF_WEIGHT`` 两个常量按 kwarg 原值透传、跨路重复 id 照常各算一路、
  ``debug["rrf_top"]`` 按引用原地打戳（键序不变、Top10 体积上限）、异常时静默退化为**空 bonus**
  且只 warning 一次（不扩到主链路）
- **trace 段**：``_no_candidates``/``_dense_has``/``_sparse_has`` 三个入参到**五档 route** 的映射、
  flag 开→写透 debug（含 ``route`` 追加键、``steps_json`` 8000 字符硬上限）／flag 关→精简五键
  （``hit_ids`` 截 5、``hit_count``=候选池大小、``returned``=实际返回条数，两者**故意不等**）、
  ``trace_meta`` 三个 id 透传、trigger/status/latency 原值、落库抛异常只 warning 且**不吞掉**
  其前面的 debug 汇总（异常范围没被扩大）
- **caller**：``_latency_ms`` 仍在原行取（时间起点/取值时点不动）、``_final`` 投影与 ``return _final``
  留在 caller、trace helper 是**同步**调用（不加 await、不塞后台任务）、RRF 的返回值真的被
  ``_rerank`` 收到（e2e）、两个 helper 都是同步 def 且 keyword-only 零默认

临时库走 ``tests/_dbclone`` 克隆到 ``tmp_path``，语料全为合成字串；项目未装 pytest-asyncio，统一
``asyncio.run``。本文件**不打桩 helper 本体**（只替换其外部依赖：``_rrf``/``enqueue_task_log``），
**零模型调用**。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path

import pytest

from _dbclone import clone_engine, make_session_factory

import app.memory.service as svc
from app.agent import loop as _loop
from app.memory import retrieve as _retrieve
from app.memory.rrf import _BRRF_DEFAULT_K, _RRF_WEIGHT

pytestmark = pytest.mark.slow

CHAR_ID = 206
OTHER_CHAR_ID = 997
MAIN_Q = "合成检索词"
LONG_CONTENT = "合成超长记忆内容" + "填充" * 60   # >60 字，逼出 preview 裁剪

_FLAG_KEYS = ("memory_peak_cutoff", "recall_neighbor_block", "recall_diversify",
              "memory_trace_debug", "recall_entity_match", "memory_temporal_recall",
              "perception_source_tag", "perception_isolate", "status_memory_ttl",
              "memory_supersede")


@pytest.fixture(autouse=True)
def clean_flags():
    """所有 flag 在本文件内归零再按需拨开，结束后逐个复位（trace 默认开＝便于钉 flag 开路径）。"""
    saved = {k: _loop.AGENT_FLAGS.get(k) for k in _FLAG_KEYS}
    for k in _FLAG_KEYS:
        _loop.AGENT_FLAGS[k] = False
    _loop.AGENT_FLAGS["memory_trace_debug"] = True
    yield
    for k, v in saved.items():
        _loop.AGENT_FLAGS[k] = v


class _WarnLog:
    """记录 warning 的假 logger（断言「只 warning 一次」「不吞别的」）。"""

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

    def critical(self, *a, **k):
        pass


# ───────────────────────── 1) RRF 段：真 helper 直调（假 _rrf 记形态） ─────────────────────────

class _FakeRRF:
    """记录入参的假 ``_rrf``；``scores`` 可预置，``raise_on`` 可让融合整体炸掉。"""

    def __init__(self, scores=None, raise_on=None):
        self._scores = scores or {}
        self._raise_on = raise_on
        self.calls: list[dict] = []

    def reciprocal_rank_fusion(self, ranked_lists, k=None):
        self.calls.append({"fn": "fuse", "ranked_lists": [list(x) for x in ranked_lists], "k": k})
        if self._raise_on == "fuse":
            raise ValueError("合成 RRF 炸了")
        return dict(self._scores)

    def normalized_bonus(self, scores, weight=None):
        self.calls.append({"fn": "bonus", "scores": dict(scores), "weight": weight})
        if self._raise_on == "bonus":
            raise ValueError("合成 bonus 炸了")
        return {i: float(v) for i, v in scores.items()}

    _BRRF_DEFAULT_K = 60
    _RRF_WEIGHT = 30.0


def _dense(n, ids):
    return [{"id": i, "content": "合成向量条%d" % k, "distance": 0.1 * (k + 1)}
            for k, i in enumerate(ids)][:n]


def _fuse(*, dense_hits, sparse_hits, entity_ids, debug, fake_rrf):
    """直调真 helper（不打桩本体），把它交回的 relevance_bonus 拿回来。"""
    return _retrieve._fuse_route_relevance(
        dense_hits=dense_hits, sparse_hits=sparse_hits, _entity_ids=entity_ids,
        debug=debug, _rrf=fake_rrf,
    )


def test_三路rank列表按dense到sparse到entity装配():
    """路序是这段的命门：dense 各查询路 → sparse 各查询路 → entity 一路，
    且 dense 只取 id、sparse 取 (mid, sc) 元组的第一个分量。"""
    fake = _FakeRRF(scores={1: 0.5, 7: 0.2})
    debug: dict = {"query": MAIN_Q}
    bonus = _fuse(dense_hits=[_dense(2, [1, 2]), _dense(1, [5])],
                  sparse_hits=[[(7, 0.9), (8, 0.4)], [(9, 0.3)]],
                  entity_ids=[5, 7], debug=debug, fake_rrf=fake)

    assert fake.calls[0]["ranked_lists"] == [[1, 2], [5], [7, 8], [9], [5, 7]], "路序 dense→sparse→entity"
    assert bonus == {1: 0.5, 7: 0.2}
    assert fake.calls[1]["weight"] == 30.0 and fake.calls[1]["scores"] == {1: 0.5, 7: 0.2}


def test_entity为空时不追加第三路():
    """专名路没命中（None/[] 两种）⇒ `_ranked` 里不追加那一路，别塞一条空证据。"""
    for empty in (None, []):
        fake = _FakeRRF(scores={1: 0.4})
        _fuse(dense_hits=[_dense(1, [1])], sparse_hits=[], entity_ids=empty,
              debug={}, fake_rrf=fake)
        assert fake.calls[0]["ranked_lists"] == [[1]], "entity=%r 时不得多出一路" % (empty,)


def test_两个常量按原值透传给rrf():
    """k/weight 来自 `_rrf` 模块常量本身（不是就地写死的数字），helper 不得替换默认值。"""
    fake = _FakeRRF(scores={1: 0.3})
    _fuse(dense_hits=[_dense(1, [1])], sparse_hits=[], entity_ids=None, debug={}, fake_rrf=fake)
    assert fake.calls[0]["k"] == _BRRF_DEFAULT_K == 60
    assert fake.calls[1]["weight"] == _RRF_WEIGHT == 30.0


def test_跨路重复id各算一路证据():
    """同一个 id 同时出现在向量路与 sparse 路 ⇒ 两路 rank 证据都在（不是去重后再算）。"""
    fake = _FakeRRF(scores={7: 0.9})
    _fuse(dense_hits=[_dense(1, [7])], sparse_hits=[[(7, 0.5), (3, 0.2)]],
          entity_ids=None, debug={}, fake_rrf=fake)
    assert fake.calls[0]["ranked_lists"] == [[7], [7, 3]], "重复 id 必须出现在两条 rank 列表里"


def test_debug按引用原地打戳且键序不变(monkeypatch):
    """`debug` 是 caller 传进来的同一个 dict：`rrf_top` 追加在原有键之后（键序是 trace 契约）。"""
    monkeypatch.setattr(_retrieve, "_logger", _WarnLog())
    fake = _FakeRRF(scores={i: 1.0 / i for i in range(1, 15)})   # 14 个 id ⇒ 超 Top10
    debug: dict = {"query": MAIN_Q, "derived_queries": []}
    _fuse(dense_hits=[_dense(14, list(range(1, 15)))], sparse_hits=[],
          entity_ids=None, debug=debug, fake_rrf=fake)

    assert list(debug) == ["query", "derived_queries", "rrf_top"], "键序不变、rrf_top 追加在后"
    assert len(debug["rrf_top"]) == 10, "融合后 Top10 体积上限照旧"
    assert debug["rrf_top"][0] == 1, "1.0 分最高"


def test_rrf_top初始就是空列表而非缺键(monkeypatch):
    """`debug["rrf_top"]` 在 try **之前**就被初始化 ⇒ 融合炸了键也在（空值），不是缺键。"""
    monkeypatch.setattr(_retrieve, "_logger", _WarnLog())
    fake = _FakeRRF(raise_on="fuse")
    debug: dict = {"query": MAIN_Q}
    _fuse(dense_hits=[_dense(1, [1])], sparse_hits=[], entity_ids=None,
          debug=debug, fake_rrf=fake)
    assert debug["rrf_top"] == []


@pytest.mark.parametrize("bad", ["fuse", "bonus"])
def test_融合异常退化为空bonus且只warning一次(monkeypatch, bad):
    """异常静默退化为空 dict（主链路不受影响），warning 一次且文案逐字是原句。"""
    logger = _WarnLog()
    monkeypatch.setattr(_retrieve, "_logger", logger)
    debug: dict = {"query": MAIN_Q}
    out = _fuse(dense_hits=[_dense(2, [1, 2])], sparse_hits=[[(3, 0.5)]],
                entity_ids=[4], debug=debug, fake_rrf=_FakeRRF(raise_on=bad))

    assert out == {}, "异常时 relevance_bonus 必须是空 dict"
    assert len(logger.warnings) == 1
    assert logger.warnings[0][0] == "RRF fusion failed, degrade to pure merge: %s"


def test_真rrf模块的融合结果与bonus排序(monkeypatch):
    """不打桩 `_rrf` 本体：用真 `reciprocal_rank_fusion`/`normalized_bonus` 跑一遍，
    钉「多路命中的那条 bonus 更高」这条真实语义（不是只看调用形态）。"""
    monkeypatch.setattr(_retrieve, "_logger", _WarnLog())
    debug: dict = {}
    # id=1 同时排 dense 第 1、sparse 第 2；id=2 只排 sparse 第 1
    bonus = _fuse(dense_hits=[_dense(1, [1])], sparse_hits=[[(1, 0.4), (2, 0.9)]],
                  entity_ids=None, debug=debug, fake_rrf=svc._rrf)
    assert set(bonus) == {1, 2} and bonus[1] > bonus[2], bonus
    assert all(0.0 <= v <= _RRF_WEIGHT for v in bonus.values()), "bonus 落在 [0, _RRF_WEIGHT]"
    assert debug["rrf_top"][0] == 1


# ───────────────────────── 2) trace 段：真 helper 直调（假 enqueue 记落库） ─────────────────────────

def _trace_box(monkeypatch, *, raises=False):
    box: list[dict] = []

    def _enqueue(**kw):
        box.append(kw)
        if raises:
            raise RuntimeError("合成 trace 落库炸了")

    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _enqueue)
    return box


def _emit(monkeypatch, *, trace_debug=True, no_candidates=False, dense=True, sparse=True,
          candidate_count=7, results=None, final=None, debug=None, trace_meta=None,
          latency=13, raises=False):
    """直调真 trace helper（只把 enqueue_task_log 换成收集盒）。"""
    box = _trace_box(monkeypatch, raises=raises)
    logger = _WarnLog()
    monkeypatch.setattr(_retrieve, "_logger", logger)
    dbg = debug if debug is not None else {"query": MAIN_Q}
    rows = results if results is not None else [{"id": i, "content": "合成候选%d" % i} for i in (1, 2)]
    fin = final if final is not None else [{"id": r["id"], "content": r.get("content", "")} for r in rows]
    out = _retrieve._emit_search_trace(
        character_id=CHAR_ID, query=MAIN_Q, query_list=[MAIN_Q], results=rows, _final=fin,
        debug=dbg, candidate_count=candidate_count, limit=5, _trace_debug=trace_debug,
        _no_candidates=no_candidates, _dense_has=dense, _sparse_has=sparse,
        trace_meta=trace_meta, _latency_ms=latency,
    )
    return out, box, logger, dbg


@pytest.mark.parametrize("no_cand,dense,sparse,route", [
    (True, True, True, "keyword"),
    (True, False, False, "keyword"),
    (False, True, True, "hybrid"),
    (False, False, True, "sparse"),
    (False, True, False, "dense"),
    (False, False, False, "entity"),
])
def test_五档route映射(monkeypatch, no_cand, dense, sparse, route):
    """五档 route 由三个入参唯一决定（`_no_candidates` 优先于双路标记）。"""
    _out, box, _log, _dbg = _emit(monkeypatch, no_candidates=no_cand, dense=dense, sparse=sparse)
    assert box[0]["route"] == route
    assert box[0]["trigger"] == "memory_search" and box[0]["status"] == "ok"


def test_flag开时写透debug并追加route键(monkeypatch):
    """flag 开：`steps_json` 就是 debug 的 JSON，`route` 键**追加在末尾**（键序是契约）。"""
    dbg = {"query": MAIN_Q, "derived_queries": [], "rrf_top": [1]}
    _out, box, _log, _dbg = _emit(monkeypatch, trace_debug=True, debug=dbg)
    steps = json.loads(box[0]["steps_json"])
    assert list(steps) == ["query", "derived_queries", "rrf_top", "candidate_count", "hit_count",
                           "limit", "returned", "latency_ms", "route"], list(steps)
    assert steps["route"] == "hybrid"
    assert steps["hit_count"] == steps["candidate_count"] == 7
    assert steps["limit"] == 5 and steps["latency_ms"] == 13


def test_flag关时trace是精简五键(monkeypatch):
    """flag 关：payload 逐字回到旧形状（不写扩充 debug，也没有 route 键）。"""
    _out, box, _log, dbg = _emit(monkeypatch, trace_debug=False)
    steps = json.loads(box[0]["steps_json"])
    assert list(steps) == ["query", "queries", "hit_ids", "hit_count", "returned"], list(steps)
    assert steps["queries"] == 1
    assert steps["hit_count"] == 7 and steps["returned"] == 2, "候选池大小 vs 实际返回条数是两个数"
    assert dbg == {"query": MAIN_Q}, "flag 关时 debug 一个键都不许多写"


def test_debug汇总的键序与returned最终投影(monkeypatch):
    """`debug.update` 五键按 candidate_count→hit_count→limit→returned→latency_ms 顺序追加；
    `returned` 投影自 `_final`（不是 `results`），preview 裁 60 字。"""
    fin = [{"id": 11, "content": LONG_CONTENT}, {"id": 12, "content": "短"}]
    rows = [{"id": 11, "content": LONG_CONTENT}, {"id": 12, "content": "短"}]
    _out, _box, _log, dbg = _emit(monkeypatch, results=rows, final=fin, debug={"query": MAIN_Q})
    assert list(dbg) == ["query", "candidate_count", "hit_count", "limit", "returned",
                        "latency_ms", "route"], "五键汇总后紧跟 try 里的 route"
    assert [t["id"] for t in dbg["returned"]] == [11, 12]
    assert all(len(t["preview"]) <= 60 for t in dbg["returned"])
    assert dbg["returned"][1]["preview"] == "短"


def test_精简路径hit_ids只截前五条(monkeypatch):
    """精简 payload 的 `hit_ids` 体积上限 5 条（不是候选池全量）。"""
    rows = [{"id": i, "content": "合成候选%d" % i} for i in range(9)]
    fin = [{"id": i, "content": ""} for i in range(9)]
    _out, box, _log, _dbg = _emit(monkeypatch, results=rows, final=fin, trace_debug=False,
                                 candidate_count=9)
    steps = json.loads(box[0]["steps_json"])
    assert steps["hit_ids"] == ["0", "1", "2", "3", "4"]
    assert steps["hit_count"] == 9 and steps["returned"] == 9


def test_steps_json八千字符硬上限(monkeypatch):
    """flag 开时 debug 很大 ⇒ `steps_json` 被截到 8000 字符（落库面不膨胀）。"""
    big = {"query": MAIN_Q, "blob": "填" * 9000}
    _out, box, _log, _dbg = _emit(monkeypatch, debug=big)
    assert len(box[0]["steps_json"]) == 8000


def test_trace_meta三个id与latency透传(monkeypatch):
    """`trace_meta` 的 user_id/session_id/task_id 三个 id 逐个透传，缺省全 None；latency 原值。"""
    meta = {"user_id": 7, "session_id": "s-1", "task_id": "t-1"}
    _out, box, _log, _dbg = _emit(monkeypatch, trace_meta=meta, latency=42)
    assert (box[0]["user_id"], box[0]["session_id"], box[0]["task_id"]) == (7, "s-1", "t-1")
    assert box[0]["character_id"] == CHAR_ID and box[0]["latency_ms"] == 42
    _out, box2, _log, _dbg = _emit(monkeypatch, trace_meta=None)
    assert (box2[0]["user_id"], box2[0]["session_id"], box2[0]["task_id"]) == (None, None, None)


def test_落库异常只warning且debug汇总仍生效(monkeypatch):
    """落库抛异常 ⇒ warning 一次且**不抛出去**；异常范围没有扩大到它前面的 debug 汇总。"""
    _out, box, logger, dbg = _emit(monkeypatch, raises=True, debug={"query": MAIN_Q})
    assert len(box) == 1 and len(logger.warnings) == 1
    assert logger.warnings[0][0] == "Memory search trace failed: %s"
    assert list(dbg) == ["query", "candidate_count", "hit_count", "limit", "returned",
                        "latency_ms", "route"], "trace 炸了也不能把 debug 汇总一起吞掉"


def test_显式返回None(monkeypatch):
    """helper 显式返回 None（调用点是裸表达式语句，不参与任何返回值运输）。"""
    out, _box, _log, _dbg = _emit(monkeypatch)
    assert out is None


# ───────────────────────── 3) e2e：真 caller（临时库） ─────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时文件库（_dbclone 克隆到 tmp_path）＋外部依赖全打桩。"""
    engine = clone_engine(tmp_path / "a50b14_rrftrace.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b14_u1", nickname="批14用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=1, name="批14角色"))
            db.add(AICharacter(id=OTHER_CHAR_ID, user_id=1, name="批14他角色"))
            await db.commit()

    asyncio.run(_seed_parents())

    async def _embed(_text):
        return [0.0] * 8

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        return []

    async def _sparse(character_id, query, top_k=5):
        return []

    async def _entity(character_id, query):
        return [], {"terms": [], "reasons": []}

    trace_box = _trace_box(monkeypatch)
    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _vector)
    monkeypatch.setattr(svc, "bm25_search", _sparse)
    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    monkeypatch.setattr(_retrieve, "_logger", _WarnLog())

    yield {"factory": factory, "trace": trace_box}

    asyncio.run(engine.dispose())


def _add(factory, content, *, importance=50.0, char=CHAR_ID):
    from datetime import timedelta

    from app.models.memory import Memory
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=char, memory_type="event", content=content,
                       source="chat", importance=importance,
                       created_at=now_naive_utc() - timedelta(minutes=1))
            db.add(m)
            await db.commit()
            return m.id

    return asyncio.run(_run())


def test_caller把rrf返回值真的喂给rerank(env, monkeypatch):
    """e2e 口径：RRF helper 的返回值（不是旧空 dict）真的到了 `_rerank` 的 relevance_bonus 参数；
    且向量路与 sparse 路重复的那条 bonus 更高。"""
    ids = [_add(env["factory"], "合成e2e条%d" % i, importance=90.0 - i) for i in range(3)]
    seen: list[dict] = []

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        return [{"id": ids[0], "content": "合成e2e条0", "type": "event", "importance": 90.0,
                 "distance": 0.1}, {"id": ids[1], "content": "合成e2e条1", "type": "event",
                                    "importance": 89.0, "distance": 0.2}]

    async def _sparse(character_id, query, top_k=5):
        return [(ids[0], 0.9), (ids[2], 0.4)]

    async def _spy_rerank(results, character_id, hit_count=None, relevance_bonus=None,
                          return_debug=False, _keep_score=False):
        seen.append({"bonus": dict(relevance_bonus or {}), "hit_count": dict(hit_count or {})})
        rows = [dict(r) for r in results]
        for r in rows:
            r["_score"] = 1.0
        if return_debug:
            return rows, {"db_pool": len(rows), "rerank_top": []}
        return rows

    monkeypatch.setattr(svc, "vector_search", _vector)
    monkeypatch.setattr(svc, "bm25_search", _sparse)
    monkeypatch.setattr(_retrieve, "_rerank", _spy_rerank)

    asyncio.run(_retrieve.search_memories(CHAR_ID, MAIN_Q, limit=5))

    assert seen, "_rerank 必须被调过"
    bonus = seen[0]["bonus"]
    assert set(bonus) == set(ids), bonus
    assert bonus[ids[0]] > bonus[ids[2]], "向量+sparse 双路命中的那条 bonus 必须更高"


def test_caller落库的trace与出口逐条对应(env, monkeypatch):
    """e2e 口径：走真 caller 时 trace 的 returned 与出口逐条一致，hit_count 是候选池大小
    （limit 更小时两者不等，这正是旧读端要分开的两个数）。"""
    ids = [_add(env["factory"], "合成trace条%d" % i, importance=90.0 - i) for i in range(3)]

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        return [{"id": i, "content": "合成trace条%d" % k, "type": "event", "importance": 90.0 - k,
                 "distance": 0.1} for k, i in enumerate(ids)]

    monkeypatch.setattr(svc, "vector_search", _vector)
    out = asyncio.run(_retrieve.search_memories(CHAR_ID, MAIN_Q, limit=2))

    got = [r["id"] for r in out]
    assert len(got) == 2
    row = env["trace"][-1]
    assert row["route"] == "dense" and row["trigger"] == "memory_search"
    steps = json.loads(row["steps_json"])
    assert [t["id"] for t in steps["returned"]] == got, "trace 的 returned 与出口逐条一致"
    assert steps["candidate_count"] == steps["hit_count"] == 3, "候选池 3 条"
    assert steps["limit"] == 2


def test_兜底空池时route是keyword(env, monkeypatch):
    """三路皆空 ⇒ LIKE 兜底出候选（query 必须是内容子串），trace route 记 ``keyword``。"""
    mid = _add(env["factory"], "合成兜底词命中条")

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        return []

    monkeypatch.setattr(svc, "vector_search", _vector)
    out = asyncio.run(_retrieve.search_memories(CHAR_ID, "合成兜底词", limit=5))

    assert [r["id"] for r in out] == [mid]
    assert env["trace"][-1]["route"] == "keyword"


# ───────────────────────── 4) 形状类守卫（AST，不跑 IO） ─────────────────────────

def _module_node(name):
    src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("找不到模块级函数 " + name)


def _caller_node(name, node=None):
    node = node or _module_node(name)
    return node


@pytest.mark.parametrize("name,kw", [
    ("_fuse_route_relevance", ("dense_hits", "sparse_hits", "_entity_ids", "debug", "_rrf")),
    ("_emit_search_trace", ("character_id", "query", "query_list", "results", "_final", "debug",
                            "candidate_count", "limit", "_trace_debug", "_no_candidates",
                            "_dense_has", "_sparse_has", "trace_meta", "_latency_ms")),
])
def test_两个helper都是同文件同步def且keyword_only零默认(name, kw):
    """必须是**同步** def（不是协程：不加 await、不改调度）、keyword-only、零默认参数
    （零默认＝不把依赖藏进默认值打桩面，调用方必须显式传）。"""
    n = _module_node(name)
    assert isinstance(n, ast.FunctionDef), "必须是同步 def，本批明确禁止新增 await"
    assert n.args.kwonlyargs and not n.args.args
    assert tuple(a.arg for a in n.args.kwonlyargs) == kw
    assert not any(n.args.kw_defaults)


def test_两个helper体内没有模型调用也没有IO():
    """零 LLM、零 await：`chat_completion` 之类模型入口与 await/async with 都不许出现。"""
    for name in ("_fuse_route_relevance", "_emit_search_trace"):
        body = ast.unparse(_module_node(name))
        assert "chat_completion" not in body and "await " not in body
        assert "async with" not in body
    # trace 段的落库入口仍只有 enqueue_task_log 一处，且局部 import 仍在 try 内
    trace = ast.unparse(_module_node("_emit_search_trace"))
    assert trace.count("enqueue_task_log(") == 1


def test_caller两个位置各只有一条调用且传参全按名():
    """调用点形态：RRF 是赋值（收返回值），trace 是裸表达式（返回 None）；实参全部按名且是原名。"""
    caller = _module_node("search_memories")
    calls = {}
    for n in ast.walk(caller):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in (
                "_fuse_route_relevance", "_emit_search_trace"):
            calls.setdefault(n.func.id, []).append(n)
    assert sorted(calls) == ["_emit_search_trace", "_fuse_route_relevance"]
    for name, group in calls.items():
        assert len(group) == 1, name + " 在 caller 里必须恰好一处调用（不得重复/多处调用）"
        call = group[0]
        assert not call.args, "实参必须全按名（保持可读顺序契约）"
        assert all(isinstance(k.value, ast.Name) and k.arg == k.value.id for k in call.keywords), name


def test_rrf的rrf实参由caller显式传入():
    """`_rrf` 是 caller 顶部的局部 import ⇒ 必须显式传参（import 时点不搬家）。"""
    caller = ast.unparse(_module_node("search_memories"))
    assert "_rrf=_rrf" in caller
    assert "_fuse_route_relevance(" in caller


def test_时点与最终投影仍留在caller():
    """`_latency_ms` 取值紧邻 trace 调用之前、`_final` 投影与 `return _final` 都在 caller 尾部。"""
    src = inspect.getsource(_retrieve.search_memories)
    lines = [ln.strip() for ln in src.splitlines()]
    i_call = next(i for i, ln in enumerate(lines) if ln.startswith("_emit_search_trace("))
    prev = [ln for ln in lines[:i_call] if ln and not ln.startswith("#")][-1]
    assert prev == "_latency_ms = int((time.monotonic() - _t0) * 1000)", prev
    assert lines[-1] == "return _final"
    assert "_final = [" in src, "最终投影留在 caller"


def test_caller里顺序是rrf早于合并trace晚于投影():
    """路序：RRF 融合在候选合并之前；trace 落库在 scene 过滤与最终投影之后。"""
    src = inspect.getsource(_retrieve.search_memories)
    assert src.index("_fuse_route_relevance(") < src.index("_merge_route_candidates(")
    assert src.index("_scene_filter(") < src.index("_emit_search_trace(")
    assert src.index("_final = [") < src.index("_emit_search_trace(")


def test_公共签名未变():
    """十个参数（含 #72 的 scene/exclude_sources/group_id 与末尾 user_id）逐字不动。"""
    params = list(inspect.signature(_retrieve.search_memories).parameters)
    assert params == ["character_id", "query", "limit", "queries", "trace_meta", "time_range",
                      "scene", "exclude_sources", "group_id", "user_id"]


def test_棘轮基线与本批实测一致():
    """``search_memories`` 本批实测 197 行/CC 13；两个 helper 都在 150/35 以内；
    `_rerank` 147/52 与其余热点一字未动。"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_hot_function_ratchet import _cc_of  # noqa: E402

    src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    nodes = {n.name: n for n in ast.parse(src).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    got = {name: ((n.end_lineno or n.lineno) - n.lineno + 1, _cc_of(n)) for name, n in nodes.items()}
    assert got["search_memories"] == (197, 13), got["search_memories"]
    assert got["_fuse_route_relevance"] == (39, 7)
    assert got["_emit_search_trace"] == (81, 16)
    assert got["_rerank"] == (147, 52), "_rerank 本批必须一字未动"
    for name, (ln, cc) in got.items():
        if name in ("_fuse_route_relevance", "_emit_search_trace"):
            assert ln <= 150 and cc <= 35, (name, ln, cc)


def test_报告与旧锚点迁移面():
    """两处旧源码锚点已随迁到 trace helper（test_experience_fixes::test_bug2），caller 侧补了
    「显式调用」与「落库动作不在 caller 裸写」两条钉，防止锚点失去约束面。"""
    src = inspect.getsource(_retrieve._emit_search_trace)
    assert '"hit_count": candidate_count' in src
    assert '"returned": len(results)' in src
    caller = inspect.getsource(_retrieve.search_memories)
    assert "_emit_search_trace(" in caller
    names = {n.id for n in ast.walk(_module_node("search_memories")) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(_module_node("search_memories")) if isinstance(n, ast.Attribute)}
    assert "enqueue_task_log" not in names | attrs, "caller 里不得再裸写落库动作（注释不算）"