# -*- coding: utf-8 -*-
"""A50 批13 守卫：``search_memories`` 里「排序成形」与「插件扩展」**两段各自**机械抽成
同文件私有协程 ``_rank_and_shape`` / ``_apply_search_plugins`` 之后的行为不变式。

两段**不合并成一套新判断**（合并就等于偷偷改语义），所以判据按两段分开写：

- **排序成形**：peak flag 的局部 ``try/except``（读不到按关）、``_trace_debug or _peak_on``
  决定走 debug 还是非 debug 那条 ``_rerank`` 路径、``debug.update`` 按引用、peak 分支
  **先 peak_cutoff 再类型均衡**、两条路径都要 ``_score`` 清干净、``_diversify`` 恒真、
  邻居**只填本轮空缺槽位**且与 peak 开关**互斥**、``neighbor_block`` 打戳只在真新增时写、
  返回的是传进去的同一个 ``results`` 对象。
- **插件扩展**：``run_hook_collect`` 的局部 import 仍在 ``try`` 内、ctx 五键与**两处** user_id
  （ctx 一个＋显式 kwarg 一个）、``callsite`` 字面量、ctx 里 ``list(results)`` 是**拷贝**、
  非 list/dict 或无 id 的候选丢弃、重复 id 不重复追加、四键净化（``str``/``str``/``float``）、
  **原候选切片在前 extra 在后**、``max(0, limit - len(_extra))`` 截尾、``except: pass`` 吞异常
  后返回**当时**的 results、空候选也照样走 hook。
- **顺序类**（AST + 端到端）：caller 仍是 rank → plugin → scene → 投影；插件看到的集合是
  **排序后、scene 过滤前**（e2e 里 group 来源的行出现在 hook ctx、但不出现在 dm 出口）。

纪律：临时库走 ``tests/_dbclone``（禁止连生产库）；项目未装 pytest-asyncio，统一 ``asyncio.run``。
本文件**不读生产库、不连队列、不读真实数据**，语料全部是合成字串。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from _dbclone import clone_engine, make_session_factory

import app.memory.service as svc
from app.agent import loop as _loop
from app.memory import retrieve as _retrieve
from app.models.memory import Memory

pytestmark = pytest.mark.slow

CHAR_ID = 205
OTHER_CHAR_ID = 998
MAIN_Q = "主查询己"
GROUP_SRC_CONTENT = "合成群来源流水"

_FLAG_KEYS = ("memory_peak_cutoff", "recall_neighbor_block", "recall_diversify",
              "memory_trace_debug", "recall_entity_match", "memory_temporal_recall",
              "perception_source_tag", "perception_isolate", "status_memory_ttl",
              "memory_supersede")


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


# ───────────────────────────── 排序成形：假 _rerank / 假邻居 ─────────────────────────────

class _FakeRerank:
    """记录调用形态的假 ``_rerank``：按 ``_scores`` 给每行打 ``_score`` 并按分数降序返回。

    真 ``_rerank`` 的 ``_keep_score`` 语义照抄：True ⇒ 保留 ``_score``，False ⇒ 弹掉。
    """

    def __init__(self, scores: dict[int, float], types: dict[int, str] | None = None):
        self.scores = scores
        self.types = types or {}
        self.calls: list[dict] = []

    async def __call__(self, results, character_id, hit_count=None, relevance_bonus=None,
                       return_debug=False, _keep_score=False):
        self.calls.append({"n": len(results), "character_id": character_id,
                           "hit_count": dict(hit_count or {}),
                           "relevance_bonus": dict(relevance_bonus or {}),
                           "return_debug": return_debug, "_keep_score": _keep_score})
        out = []
        for r in results:
            row = dict(r)
            row["type"] = self.types.get(r["id"], row.get("type", "event"))
            row["_score"] = float(self.scores.get(r["id"], 0.0))
            out.append(row)
        out.sort(key=lambda x: x["_score"], reverse=True)
        if not _keep_score:
            for row in out:
                row.pop("_score", None)
        if return_debug:
            return out, {"db_pool": len(out),
                         "rerank_top": [{"id": r["id"], "score": r.get("_score", 0.0)} for r in out[:10]]}
        return out


def _row(mid, *, content="合成候选条", mtype="event", importance=50.0):
    return {"id": mid, "content": content, "type": mtype, "importance": importance}


def _rank(results, *, limit=5, debug=None, trace_debug=True, hit_count=None, bonus=None,
          character_id=CHAR_ID):
    """直调真 helper（不打桩 helper 本体），返回它交回来的 list。"""
    return asyncio.run(_retrieve._rank_and_shape(
        results=results,
        character_id=character_id,
        hit_count=hit_count if hit_count is not None else {},
        relevance_bonus=bonus if bonus is not None else {},
        limit=limit,
        debug=debug if debug is not None else {},
        _trace_debug=trace_debug,
    ))


def _plug(results, *, query=MAIN_Q, limit=5, user_id=None, character_id=CHAR_ID):
    return asyncio.run(_retrieve._apply_search_plugins(
        results=results, character_id=character_id, query=query, limit=limit, user_id=user_id))


class _HookSpy:
    """替换 ``app.plugins.registry.run_hook_collect``：记录 ctx/kwargs，返回预置 items。"""

    def __init__(self, items=None, raises=False):
        self.items = items if items is not None else []
        self.raises = raises
        self.seen: list[dict] = []

    async def __call__(self, hook, ctx, **kwargs):
        self.seen.append({"hook": hook, "ctx": ctx, "kwargs": dict(kwargs)})
        if self.raises:
            raise RuntimeError("合成插件炸了")
        return self.items

    @property
    def last(self):
        return self.seen[-1]


# ───────────────── 1) 排序成形：flag 各路 / 评分移除 / 同分顺序 ─────────────────

def test_trace开走debug路径且debug按引用更新(monkeypatch):
    """``_trace_debug or _peak_on`` 为真 ⇒ 走 ``return_debug=True`` 那条路径，
    ``debug.update`` 打进去的键是**同一个 dict 对象**（键插入顺序不变）。"""
    fake = _FakeRerank({1: 30.0, 2: 20.0})
    monkeypatch.setattr(_retrieve, "_rerank", fake)
    debug: dict = {"query": MAIN_Q}
    out = _rank([_row(1), _row(2)], debug=debug)

    assert fake.calls == [{"n": 2, "character_id": CHAR_ID, "hit_count": {}, "relevance_bonus": {},
                           "return_debug": True, "_keep_score": False}], "必须走 debug 路径"
    assert list(debug) == ["query", "db_pool", "rerank_top"], "debug 按引用原地追加，顺序在原键之后"
    assert debug["db_pool"] == 2 and [t["id"] for t in debug["rerank_top"]] == [1, 2]
    assert [r["id"] for r in out] == [1, 2]


def test_trace关且peak关走非debug路径且不多写键(monkeypatch):
    """两把都关 ⇒ 走非 debug ``_rerank``（``return_debug=False``/``_keep_score=False``），
    ``debug`` 一个键都不许多写（trace 逐字节不变）。"""
    fake = _FakeRerank({1: 30.0, 2: 20.0})
    monkeypatch.setattr(_retrieve, "_rerank", fake)
    debug: dict = {"query": MAIN_Q}
    out = _rank([_row(1), _row(2)], debug=debug, trace_debug=False)

    assert fake.calls[0]["return_debug"] is False and fake.calls[0]["_keep_score"] is False
    assert debug == {"query": MAIN_Q}, "非 debug 路径不许写任何新键"
    assert [r["id"] for r in out] == [1, 2]


def test_trace关但peak开仍走debug路径且保留分数后被清掉(monkeypatch):
    """``_trace_debug or _peak_on`` 是**或**：trace 关、peak 开 ⇒ 仍旧走 debug 路径且
    ``_keep_score=True``；peak 分支随后必须把 ``_score`` 弹干净（对外形状与旧路径一致）。"""
    _loop.AGENT_FLAGS["memory_peak_cutoff"] = True
    fake = _FakeRerank({1: 40.0, 2: 30.0, 3: 5.0})
    monkeypatch.setattr(_retrieve, "_rerank", fake)
    debug: dict = {}
    out = _rank([_row(1), _row(2), _row(3)], debug=debug, trace_debug=False)

    assert fake.calls[0]["return_debug"] is True and fake.calls[0]["_keep_score"] is True
    assert [r["id"] for r in out] == [1, 2], "peak 地板 18 分：5 分那条被自然收敛掉"
    assert all("_score" not in r for r in out), "peak 分支必须逐条 pop _score"
    assert "rerank_top" in debug, "peak 开时 debug 路径仍会并入 debug（键照打）"


def test_peak_flag读取异常按关处理(monkeypatch):
    """读 flag 抛异常 ⇒ 那一行 ``except`` 兜住按关处理：走非 debug 路径、peak 分支不执行。"""

    class _BoomFlags(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("flag 表炸了")

    monkeypatch.setattr("app.flags.agent_flags.AGENT_FLAGS", _BoomFlags())
    fake = _FakeRerank({1: 30.0})
    monkeypatch.setattr(_retrieve, "_rerank", fake)
    debug: dict = {}
    out = _rank([_row(1)], debug=debug, trace_debug=False)

    assert fake.calls[0]["return_debug"] is False, "读不到 flag ⇒ 按 peak 关处理"
    assert [r["id"] for r in out] == [1] and debug == {}


def test_peak先截后均衡且同类型不占满出口(monkeypatch):
    """``_diversify`` 恒真 ⇒ 两条路径都过 ``_diversify_by_type``；同类型每档只取 2 条
    （3 条 event＋1 条 emotion、limit=3 ⇒ 第三条 event 被类型均衡挤掉）。"""
    types = {1: "event", 2: "event", 3: "event", 4: "emotion"}
    fake = _FakeRerank({1: 40.0, 2: 39.0, 3: 38.0, 4: 37.0}, types)
    monkeypatch.setattr(_retrieve, "_rerank", fake)
    out = _rank([_row(i) for i in (1, 2, 3, 4)], limit=3)

    assert [r["id"] for r in out] == [1, 2, 4], "同类型第 3 条必须让位（per_type_cap=2）"

    _loop.AGENT_FLAGS["memory_peak_cutoff"] = True
    peak_out = _rank([_row(i) for i in (1, 2, 3, 4)], limit=3)
    assert [r["id"] for r in peak_out] == [1, 2, 4], "peak 分支也是先截后均衡，口径一致"


def test_空结果不进排序段也不改debug(monkeypatch):
    """helper 只搬运，不补判据：传进来空 list 就原样交回（caller 的 ``if results:`` 判定仍在外）。"""
    fake = _FakeRerank({})
    monkeypatch.setattr(_retrieve, "_rerank", fake)
    debug: dict = {}
    original: list = []
    out = _rank(original, debug=debug)

    assert out == [] and fake.calls, "空候选仍会走 _rerank（那是 _rerank 自己的空早退）"
    assert debug["db_pool"] == 0 and debug["rerank_top"] == []


# ───────────────── 2) 排序成形：邻居只填空缺槽位，且与 peak 互斥 ─────────────────

def _nb_stub(rows):
    seen: list[dict] = []

    async def _expand(character_id, ranked, slots):
        seen.append({"character_id": character_id, "slots": slots,
                     "n_ranked": len(ranked), "ids": [r["id"] for r in ranked]})
        return [dict(r) for r in rows[:slots]]

    _expand.seen = seen
    return _expand


def test_邻居只填本轮空缺槽位并打戳(monkeypatch):
    """limit=5、已有 3 条 ⇒ 只向邻居要 ``limit - len(results)``＝2 条；``neighbor_block`` 键
    记的是**排序后前 2 条**（NEIGHBOR_ANCHOR_MAX）的 id 与实际新增条数，且 trace 关时不打戳。"""
    fake = _FakeRerank({1: 30.0, 2: 20.0, 3: 10.0})
    monkeypatch.setattr(_retrieve, "_rerank", fake)
    expand = _nb_stub([_row(90), _row(91), _row(92)])
    monkeypatch.setattr(_retrieve, "_expand_neighbor_blocks", expand)
    _loop.AGENT_FLAGS["recall_neighbor_block"] = True

    debug: dict = {}
    out = _rank([_row(1), _row(2), _row(3)], limit=5, debug=debug)
    assert expand.seen[0]["slots"] == 2, "只补空缺槽位（不许挤掉已有条）"
    assert [r["id"] for r in out] == [1, 2, 3, 90, 91], "邻居排在直接命中之后"
    assert debug["neighbor_block"] == {"anchors": [1, 2], "added": 2}

    debug2: dict = {}
    _rank([_row(1), _row(2), _row(3)], limit=5, debug=debug2, trace_debug=False)
    assert "neighbor_block" not in debug2, "trace 关 ⇒ 不许写 neighbor_block 键"


def test_邻居不占位时一条都不补(monkeypatch):
    """本轮已满（条数 ≥ limit）⇒ 邻居那段整段不执行（连查询都不发）。"""
    monkeypatch.setattr(_retrieve, "_rerank", _FakeRerank({1: 30.0, 2: 20.0}))
    expand = _nb_stub([_row(90)])
    monkeypatch.setattr(_retrieve, "_expand_neighbor_blocks", expand)
    _loop.AGENT_FLAGS["recall_neighbor_block"] = True

    debug: dict = {}
    out = _rank([_row(1), _row(2)], limit=2, debug=debug)
    assert expand.seen == [] and [r["id"] for r in out] == [1, 2]
    assert "neighbor_block" not in debug


def test_peak开时邻居互斥(monkeypatch):
    """``_peak_on`` 为真 ⇒ 邻居那段条件里的 ``not _peak_on`` 不成立，整段跳过
    （peak 的语义就是「弱相关就少注入」，拿邻居补空缺会与之相冲）。"""
    _loop.AGENT_FLAGS["memory_peak_cutoff"] = True
    _loop.AGENT_FLAGS["recall_neighbor_block"] = True
    monkeypatch.setattr(_retrieve, "_rerank", _FakeRerank({1: 40.0}))
    expand = _nb_stub([_row(90)])
    monkeypatch.setattr(_retrieve, "_expand_neighbor_blocks", expand)

    debug: dict = {}
    out = _rank([_row(1)], limit=5, debug=debug)
    assert expand.seen == [], "peak 开 ⇒ 邻居函数一次都不许被调用"
    assert [r["id"] for r in out] == [1] and "neighbor_block" not in debug


def test_邻居闸关时整段不执行(monkeypatch):
    """``recall_neighbor_block`` 关（默认关）⇒ 零查询、零打戳。"""
    monkeypatch.setattr(_retrieve, "_rerank", _FakeRerank({1: 40.0}))
    expand = _nb_stub([_row(90)])
    monkeypatch.setattr(_retrieve, "_expand_neighbor_blocks", expand)

    debug: dict = {}
    out = _rank([_row(1)], limit=5, debug=debug)
    assert expand.seen == [] and "neighbor_block" not in debug and [r["id"] for r in out] == [1]


# ───────────────── 3) 插件扩展：ctx 透传 / 顺序 / 净化 / 截尾 / fail-open ─────────────────

def test_ctx五键与两处user_id以及callsite字面量(monkeypatch):
    """ctx 必须是五键（含 user_id）、**显式 kwarg** 也带同一个 user_id、callsite 字面量逐字不变；
    ctx 里的 results 是 ``list(results)`` **拷贝**（hook 改它不许影响 caller 手里那份）。"""
    spy = _HookSpy(items=[])
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)
    original = [_row(1), _row(2)]

    out = _plug(original, user_id=77)

    rec = spy.last
    assert rec["hook"] == "memory_search"
    assert rec["kwargs"] == {"user_id": 77, "callsite": "memory/retrieve.py:memory_search"}
    ctx = rec["ctx"]
    assert set(ctx) == {"character_id", "query", "results", "limit", "user_id"}, "ctx 五键逐字不变"
    assert ctx["character_id"] == CHAR_ID and ctx["query"] == MAIN_Q and ctx["limit"] == 5
    assert ctx["user_id"] == 77, "ctx 里的 user_id 必须与显式 kwarg 同一个"
    assert ctx["results"] == original and ctx["results"] is not original, "ctx 里必须是拷贝"
    ctx["results"].append({"id": 999})
    assert [r["id"] for r in original] == [1, 2], "hook 改 ctx 不许回写 caller 那份"
    assert [r["id"] for r in out] == [1, 2], "无追加 ⇒ 原样交回"


def test_user_id缺省时两处都是None(monkeypatch):
    """调用点拿不到调用者 ⇒ ctx 与显式 kwarg 都必须是 None（不猜、不造 0）。"""
    spy = _HookSpy(items=[])
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)
    _plug([_row(1)])
    assert spy.last["kwargs"]["user_id"] is None and spy.last["ctx"]["user_id"] is None


def test_原候选在前extra在后且四键净化(monkeypatch):
    """追加语义：**原候选切片在前、插件 extra 在后**（原结果让位给插件追加）；
    只保留四个键并做 ``str``/``str``/``float`` 净化；非 dict/非 list/无 id 的候选丢弃。"""
    items = [{"result": [
        {"id": 5, "content": "插件条甲", "type": "note", "importance": "12.5", "多余字段": "丢掉"},
        {"id": 6, "content": None, "type": None, "importance": None},
        {"id": 2, "content": "与已有重复", "importance": 9},
        {"content": "没有 id 的丢弃"},
        "不是 dict 丢弃",
    ]}, {"result": "不是 list 丢弃"}, {}]
    spy = _HookSpy(items=items)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)
    original = [_row(1), _row(2), _row(3)]

    out = _plug(original, limit=10)

    assert [r["id"] for r in out] == [1, 2, 3, 5, 6], "重复 id（2）被跳过，坏候选被丢弃"
    assert set(out[3]) == {"id", "content", "type", "importance"}, "插件条固定四键（未知字段丢弃）"
    assert out[3]["importance"] == 12.5 and isinstance(out[3]["importance"], float)
    assert out[4] == {"id": 6, "content": "", "type": "plugin", "importance": 0.0}, "None 走默认值净化"


def test_追加超过limit时原候选被截尾(monkeypatch):
    """``results[:max(0, limit - len(_extra))] + _extra``：extra(4) 比 limit(2) 还多 ⇒
    max(0, 2-4)=0 ⇒ 原候选一个不剩，extra 整段照原样接在后面（不截 extra 自己）。"""
    spy = _HookSpy(items=[{"result": [{"id": i} for i in range(10, 14)]}])
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)
    out = _plug([_row(1), _row(2), _row(3)], limit=2)
    assert [r["id"] for r in out] == [10, 11, 12, 13], "原候选全让位，extra 保持原序原条数"


def test_空候选也照样走hook(monkeypatch):
    """三路皆空时不许跳过插件：``results=[]`` 仍发一次 hook，插件追加照旧生效。"""
    spy = _HookSpy(items=[{"result": [{"id": 8, "content": "空候选时的插件条"}]}])
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)
    out = _plug([], limit=5)
    assert len(spy.seen) == 1
    assert [r["id"] for r in out] == [8]


def test_hook异常吞掉且返回当时results(monkeypatch):
    """``except Exception: pass`` 逐字保留：hook 抛异常时零 warning、主链路照旧返回当时的结果。"""
    spy = _HookSpy(raises=True)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)
    logger = _WarnLog()
    monkeypatch.setattr(_retrieve, "_logger", logger)
    out = _plug([_row(1), _row(2)], limit=5)
    assert [r["id"] for r in out] == [1, 2]
    assert logger.warnings == [], "异常隔离段不该留 warning（静默是原语义）"


def test_hook返回空或无result时不改结果(monkeypatch):
    """``_hook_items`` 为空、或每项都没有 list 型 ``result`` ⇒ 一条都不追加。"""
    for items in ([], [{"result": None}], [{}]):
        spy = _HookSpy(items=items)
        monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)
        out = _plug([_row(1), _row(2)])
        assert [r["id"] for r in out] == [1, 2], "items=%r 时结果不许变" % (items,)


# ───────────────── 4) 端到端：rank → plugin → scene → 投影 的顺序 ─────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时文件库（_dbclone 克隆到 tmp_path）＋外部依赖全打桩。"""
    engine = clone_engine(tmp_path / "a50b13_rankplugin.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b13_u1", nickname="批13用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=1, name="批13角色"))
            db.add(AICharacter(id=OTHER_CHAR_ID, user_id=1, name="批13他角色"))
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

    trace_box: list[dict] = []
    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _vector)
    monkeypatch.setattr(svc, "bm25_search", _sparse)
    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    monkeypatch.setattr(_retrieve, "_logger", _WarnLog())
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: trace_box.append(kw))

    yield {"engine": engine, "factory": factory, "trace": trace_box}

    asyncio.run(engine.dispose())


def _add(factory, content, *, importance=50.0, char=CHAR_ID, mtype="event", source="chat",
         sub_type=None, **kw):
    from app.utils.timeutil import now_naive_utc
    from datetime import timedelta

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=char, memory_type=mtype, content=content,
                       source=source, sub_type=sub_type, importance=importance,
                       created_at=now_naive_utc() - timedelta(minutes=1), **kw)
            db.add(m)
            await db.commit()
            return m.id

    return asyncio.run(_run())


def test_插件看到排序后scene过滤前的集合(env, monkeypatch):
    """e2e 钉顺序：向量路出的**群来源**行出现在 hook ctx（插件在 scene 过滤之前跑），
    但 dm 出口不含它（scene 过滤在插件之后）；插件追加的条没有 source，所以照旧过关。"""
    factory = env["factory"]
    group_id_row = _add(factory, GROUP_SRC_CONTENT, source="group", importance=70.0)
    dm_id = _add(factory, "合成私聊来源条", importance=60.0)

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        return [{"id": group_id_row, "content": GROUP_SRC_CONTENT, "type": "event",
                 "importance": 70.0, "distance": 0.1},
                {"id": dm_id, "content": "合成私聊来源条", "type": "event",
                 "importance": 60.0, "distance": 0.2}]

    monkeypatch.setattr(svc, "vector_search", _vector)
    spy = _HookSpy(items=[{"result": [{"id": 4242, "content": "插件追加条", "importance": 5}]}])
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)

    out = asyncio.run(_retrieve.search_memories(CHAR_ID, MAIN_Q, scene="dm", user_id=77))

    seen_ids = [r["id"] for r in spy.last["ctx"]["results"]]
    assert seen_ids[:2] == [group_id_row, dm_id], "插件看到的集合 = 排序后、scene 过滤前"
    assert spy.last["kwargs"]["user_id"] == 77
    got = [r["id"] for r in out]
    assert group_id_row not in got, "dm 出口必须已过 scene 过滤"
    assert dm_id in got and 4242 in got
    assert [r["type"] for r in out if r["id"] == 4242] == ["plugin"]


def test_走caller时排序与插件都只跑一轮(env, monkeypatch):
    """e2e 口径：出口条数 = 类型均衡后的 limit 截断＋插件追加，trace 的 returned 与之逐条对应。"""
    import json

    factory = env["factory"]
    ids = [_add(factory, "合成e2e条%d" % i, importance=90.0 - i) for i in range(3)]

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        return [{"id": i, "content": "合成e2e条%d" % k, "type": "event",
                 "importance": 90.0 - k, "distance": 0.1} for k, i in enumerate(ids)]

    monkeypatch.setattr(svc, "vector_search", _vector)
    spy = _HookSpy(items=[{"result": [{"id": 5555, "content": "插件追加条"}]}])
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", spy)

    out = asyncio.run(_retrieve.search_memories(CHAR_ID, MAIN_Q, limit=2))

    got = [r["id"] for r in out]
    assert got == ids[:1] + [5555], "limit=2 且 extra 1 条 ⇒ 原候选被截到 1 条，再把 extra 接在后面"
    steps = json.loads(env["trace"][-1]["steps_json"])
    assert [t["id"] for t in steps["returned"]] == got, "trace 的 returned 与出口逐条一致"
    assert len(spy.seen) == 1, "整轮只发一次 hook"


# ───────────────── 5) 形状类守卫（AST，不跑 IO） ─────────────────

def _module_node(name):
    src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("retrieve.py 里找不到 %s" % name)


@pytest.mark.parametrize("helper_name,caller_kw", [
    ("_rank_and_shape", ["results", "character_id", "hit_count", "relevance_bonus", "limit",
                         "debug", "_trace_debug"]),
    ("_apply_search_plugins", ["results", "character_id", "query", "limit", "user_id"]),
])
def test_两个helper是同文件私有协程且caller各只有一处调用(helper_name, caller_kw):
    helper, caller = _module_node(helper_name), _module_node("search_memories")
    assert isinstance(helper, ast.AsyncFunctionDef), "必须是协程"
    assert helper.name.startswith("_"), "必须是私有名"
    assert helper.args.args == [] and helper.args.defaults == [], "入参必须全 keyword-only 零默认"
    assert [a.arg for a in helper.args.kwonlyargs] == caller_kw, "入参名与顺序逐字符合契约"

    calls = [n for n in ast.walk(caller)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == helper_name]
    assert len(calls) == 1, "caller 内必须只有一处调用（实际 %d 处）" % len(calls)
    call = calls[0]
    assert len([n for n in ast.walk(caller) if isinstance(n, ast.Await) and n.value is call]) == 1, \
        "调用必须被 await"
    assert [kw.arg for kw in call.keywords] == caller_kw, "全部实参必须显式按名传入"
    returns = [s for s in ast.walk(helper) if isinstance(s, ast.Return)]
    assert len(returns) == 1 and ast.unparse(returns[0]) == "return results", "helper 只多一条 return 运输"


def test_caller仍保留if_results判定且顺序是rank到plugin到scene到投影():
    """``if results:`` 判定留在 caller、分支体只剩一条 await 调用；调用顺序 rank → plugin → scene
    → 投影（不许把 scene 提到 plugin 之前）。"""
    caller = _module_node("search_memories")
    body = list(caller.body)
    rank_if = next(s for s in body if isinstance(s, ast.If) and ast.unparse(s.test) == "results")
    assert len(rank_if.body) == 1 and "_rank_and_shape" in ast.unparse(rank_if.body[0])
    idx = {n: i for i, n in enumerate(body)}
    plug_stmt = next(n for n in body
                     if isinstance(n, ast.Assign) and "await _apply_search_plugins(" in ast.unparse(n))
    scene_if = next(s for s in body if isinstance(s, ast.If) and ast.unparse(s.test)
                    == "scene is not None or exclude_sources or group_id is not None")
    assert idx[rank_if] < idx[plug_stmt] < idx[scene_if], "rank → plugin → scene"
    final = next(n for n in body if isinstance(n, ast.Assign)
                 and ast.unparse(n.targets[0]) == "_final")
    assert idx[plug_stmt] < idx[scene_if] < idx[final], "投影永远在最后"


def test_两helper依赖按裸全局名晚绑定且零LLM节点():
    """两个 helper 都不得自己 import service 符号；``_rerank``/``peak_cutoff``/
    ``_diversify_by_type``/``_expand_neighbor_blocks``/``_neighbor_block_on`` 仍按裸全局名
    晚绑定（不缓存进默认参数），且两段里都不许出现模型调用。"""
    file_src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    for name in ("_rank_and_shape", "_apply_search_plugins"):
        helper = _module_node(name)
        src = ast.get_source_segment(file_src, helper)
        assert "from app.memory.service import" not in src, "%s 不得自己重导入 service 符号" % name
        code = [ast.unparse(n) for n in ast.walk(helper)
                if isinstance(n, (ast.Call, ast.Import, ast.ImportFrom, ast.Name, ast.Attribute))]
        for bad in ("chat_completion", "llm_client", "encode", "inference",
                    "vector_search", "bm25_search", "text_embedding"):
            assert not [c for c in code if bad in c], "%s 里出现 %r" % (name, bad)
    rank_loaded = {n.id for n in ast.walk(_module_node("_rank_and_shape"))
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    for dep in ("_rerank", "peak_cutoff", "_diversify_by_type", "_expand_neighbor_blocks",
                "_neighbor_block_on", "NEIGHBOR_ANCHOR_MAX"):
        assert dep in rank_loaded, "排序段 %s 必须继续按裸全局名晚绑定" % dep


def test_两helper的try结构原样保留():
    """排序段只有一个 flag 读取的 try（读不到按关）；插件段整段仍是一个 try＋``except: pass``，
    且 ``run_hook_collect`` 的局部 import 仍在 try **内**（不许提到 try 外面）。"""
    rank_node, plug_node = _module_node("_rank_and_shape"), _module_node("_apply_search_plugins")
    rank_tries = [n for n in ast.walk(rank_node) if isinstance(n, ast.Try)]
    plug_tries = [n for n in ast.walk(plug_node) if isinstance(n, ast.Try)]
    assert len(rank_tries) == 1 and len(plug_tries) == 1, "两段各自只许有一个 try"
    rank_try, plug_try = rank_tries[0], plug_tries[0]

    assert len(rank_try.handlers) == 1 and len(rank_try.body) == 2, "排序段那个 try 只包 flag 读取两行"
    assert "memory_peak_cutoff" in ast.unparse(rank_try.body[1])
    assert [type(x).__name__ for x in rank_try.body] == ["ImportFrom", "Assign"]
    assert ast.unparse(rank_try.handlers[0]).startswith("except Exception")

    imports = [n for n in ast.walk(plug_try) if isinstance(n, ast.ImportFrom)
               and "run_hook_collect" in ast.unparse(n)]
    assert len(imports) == 1, "run_hook_collect 的局部 import 必须在 try 内（不许提到外面）"
    assert imports[0] in plug_try.body, "那个 import 必须直接属于 try 的 body"
    assert len(plug_try.handlers) == 1 and plug_try.orelse == [] and plug_try.finalbody == []
    assert [type(x).__name__ for x in plug_try.handlers[0].body] == ["Pass"], "异常分支仍只 pass"


def test_签名与公共出口未变():
    """抽走两段不改 ``search_memories`` 的签名（service 垫片与全部调用方零感知）。"""
    sig = inspect.signature(_retrieve.search_memories)
    assert list(sig.parameters) == ["character_id", "query", "limit", "queries", "trace_meta",
                                    "time_range", "scene", "exclude_sources", "group_id", "user_id"]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in sig.parameters.values())
    assert inspect.iscoroutinefunction(_retrieve.search_memories)