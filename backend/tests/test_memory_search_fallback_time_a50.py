# -*- coding: utf-8 -*-
"""A50 批12 守卫：``search_memories`` 里「关键词兜底」与「时间召回」**两段各自**机械抽成
同文件私有协程 ``_keyword_fallback`` / ``_append_temporal_hits`` 之后的行为不变式。

两路**不合并成一套新判断**（合并就等于偷偷改语义），所以判据也按两路分开写：

- **关键词兜底**：三路皆空才兜底（有候选时一条 LIKE 查询都不发）、LIKE 通配符按 ``_like_escape``
  转义（``%``/``_`` 不当通配符）、三道过滤（归档/working_state/状态子句）、固定八键 schema、
  ``importance``/``created_at`` 降序 + ``limit*2``、候选计数被覆盖成兜底条数、``route`` 仍是
  keyword（``_no_candidates`` 是兜底前快照）、**DB 异常照旧冒泡**（原段没有 try）、会话在 helper 内退出。
- **时间路**：区间 ``[t_start, t_end)`` 半开、重叠 id 不重复、**追加在原候选之后**、返回的就是
  caller 传进来的同一个 list 对象且 ``debug`` 按引用、flag 关时零查询、flag 读取失败按关、
  只真新增才写 ``time_route`` 键、候选计数不随本路增加、查询失败照旧抛。
- **形状类**（AST 源码断言，不跑 IO）：两个 helper 各自是同文件私有协程、caller 各只有一处
  调用且被 await、四个/七个入参全 keyword-only 零默认、caller 仍保留 ``if not results:`` 与
  ``if time_range is not None:`` 两个判定、依赖按裸全局名晚绑定、零 LLM 节点、公共签名未变。

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

CHAR_ID = 204
OTHER_CHAR_ID = 999
MAIN_Q = "主查询戊"

_FLAG_KEYS = ("recall_entity_match", "recall_recency_bonus", "recall_neighbor_block",
              "memory_peak_cutoff", "memory_temporal_recall", "perception_source_tag",
              "perception_isolate", "status_memory_ttl", "memory_supersede")


# ─────────────────────────────── flag 隔离 ───────────────────────────────

@pytest.fixture(autouse=True)
def clean_flags():
    """本文件任何用例都不依赖本机 runtime_flags 现值：前后复位（默认全关＋trace 开）。

    注意本机 ``memory_temporal_recall`` 注册默认值是 True，所以**必须**由本夹具按用例拨，
    否则「flag 关时零查询」那条会被现值污染成假绿。
    """
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
    """只收 warning 的假 logger（这两段原实现都没有 except，warning 主要用于「不许静默」的对照）。"""

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
    """包一层工厂；返回的 callable 带 ``.box``：``stmts`` 收语句对象、``log`` 收 enter/exit。"""
    box = {"stmts": [], "log": []}

    def _make():
        return _SpySession(factory(), box["stmts"], box["log"])

    _make.box = box
    return _make


def _sql(stmt) -> str:
    return str(stmt)


def _like_queries(stmts) -> list:
    """关键词兜底那条查询的判别：SQL 文本里出现 ``content LIKE``（_rerank/回填都没有）。"""
    return [s for s in stmts if "memories.content LIKE" in _sql(s)]


def _temporal_queries(stmts) -> list:
    """时间路那条查询的判别：出现 ``memories.created_at >=``（其它路都不带这个下界）。"""
    return [s for s in stmts if "memories.created_at >=" in _sql(s)]


# ─────────────────────────────── 临时库夹具 ───────────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时文件库（_dbclone 克隆到 tmp_path）＋外部依赖全打桩。"""
    engine = clone_engine(tmp_path / "a50b12_kwtime.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b12_u1", nickname="批12用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=1, name="批12角色"))
            db.add(AICharacter(id=OTHER_CHAR_ID, user_id=1, name="批12他角色"))
            await db.commit()

    asyncio.run(_seed_parents())

    calls: dict[str, list] = {"text_embedding": [], "vector_search": [], "bm25_search": []}
    trace_box: list[dict] = []
    logger = _WarnLog()

    async def _embed(text):
        calls["text_embedding"].append(text)
        return [0.0] * 8

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        calls["vector_search"].append({"character_id": character_id, "limit": limit})
        return []

    async def _sparse(character_id, query, top_k=5):
        calls["bm25_search"].append({"character_id": character_id, "query": query, "top_k": top_k})
        return []

    async def _entity(character_id, query):
        return [], {"terms": [], "reasons": []}

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _vector)
    monkeypatch.setattr(svc, "bm25_search", _sparse)
    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    monkeypatch.setattr(_retrieve, "_logger", logger)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", lambda *a, **k: [])
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: trace_box.append(kw))

    yield {"engine": engine, "factory": factory, "calls": calls, "trace": trace_box,
           "logger": logger}

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


def _dense_row(mid, content="合成向量条"):
    return {"id": mid, "content": content, "type": "event", "importance": 50.0, "distance": 0.1}


def _fallback(factory, *, query=MAIN_Q, limit=5, character_id=CHAR_ID):
    """直调真 helper（不走 caller），返回 ``(results, candidate_count)``。"""
    return asyncio.run(_retrieve._keyword_fallback(
        character_id=character_id, query=query, limit=limit,
        async_session_factory=factory,
    ))


def _temporal(factory, *, results, debug, time_range, limit=5, trace_debug=True,
              character_id=CHAR_ID):
    """直调真 helper，返回它交回来的那个 list。"""
    return asyncio.run(_retrieve._append_temporal_hits(
        results=results, debug=debug, character_id=character_id, time_range=time_range,
        limit=limit, _trace_debug=trace_debug, async_session_factory=factory,
    ))


def _search(query=MAIN_Q, **kw):
    return asyncio.run(_retrieve.search_memories(CHAR_ID, query, **kw))


def _temporal_on():
    """拨开时间路闸（本机该 flag 注册默认值是 True，夹具复位成 False ⇒ 必须由用例显式拨开）。"""
    _loop.AGENT_FLAGS["memory_temporal_recall"] = True


def _ids(rows):
    return [r["id"] for r in rows]


class _BoomSession:
    """execute 一定炸的会话：这两段原实现都**没有** try/except，异常必须照旧冒泡。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_a, **_k):
        raise RuntimeError("合成库炸了")


def _pool_events(engine):
    """挂 pool 事件监听，返回 (events, handlers)；用完必须 detach。"""
    events: list[str] = []
    handlers = []
    pool = engine.sync_engine.pool
    for name in ("connect", "checkout", "checkin", "close"):
        h = (lambda *a, _n=name: events.append(_n))
        handlers.append((name, h))
        sa_event.listen(pool, name, h)
    return events, handlers


def _detach(engine, handlers):
    pool = engine.sync_engine.pool
    for name, h in handlers:
        sa_event.remove(pool, name, h)


# ───────────────── 1) 关键词兜底：只在无候选时触发 / 转义 / 过滤 / 排序 ─────────────────

def test_三路皆空才走兜底且路由仍是keyword(env, monkeypatch):
    """三路皆空 ⇒ 恰好一条 LIKE 查询、命中语料被捞出、``route`` 仍是 keyword
    （``_no_candidates`` 是兜底**前**快照），候选计数被改写成兜底条数。"""
    factory = env["factory"]
    hit = _add(factory, "合成兜底语料甲 " + MAIN_Q, importance=70.0)
    spy = _spy_factory(factory)
    monkeypatch.setattr(svc, "async_session_factory", spy)

    out = _search(MAIN_Q)

    assert _ids(out) == [hit]
    assert len(_like_queries(spy.box["stmts"])) == 1, "兜底只该发一条 LIKE 查询"
    tr = env["trace"][-1]
    assert tr["route"] == "keyword", "兜底捞到东西也仍是 keyword（快照在兜底之前）"
    steps = json.loads(tr["steps_json"])
    assert steps["candidate_count"] == 1 == steps["hit_count"], "候选计数被覆盖成兜底条数"


def test_有候选时一条like查询都不发(env, monkeypatch):
    """向量路出了候选 ⇒ ``if not results:`` 不成立 ⇒ 兜底整段不执行（LIKE 查询零条）。"""
    factory = env["factory"]
    mid = _add(factory, "合成有候选条 " + MAIN_Q, importance=60.0)
    _add(factory, "合成会被like捞到的干扰条", importance=99.0)

    async def _vec(character_id, query_embedding, limit=5, **_kw):
        return [_dense_row(mid, content="合成有候选条 " + MAIN_Q)]

    spy = _spy_factory(factory)
    monkeypatch.setattr(svc, "async_session_factory", spy)
    monkeypatch.setattr(svc, "vector_search", _vec)
    out = _search(MAIN_Q)

    assert _ids(out) == [mid], "有候选时不许混入兜底结果（99 分那条也不该出现）"
    assert _like_queries(spy.box["stmts"]) == [], "有候选 ⇒ LIKE 查询零条"
    assert json.loads(env["trace"][-1]["steps_json"])["candidate_count"] == 1


def test_like百分号与下划线不当通配符(env):
    """``_like_escape`` 仍在兜底那条 SQL 上：搜「50%」只命中字面「50%」，搜「a_b」只命中字面「a_b」——
    被通配符放行的「5012」「axb」两条都不许出现。"""
    factory = env["factory"]
    lit_pct = _add(factory, "合成比例50%的记录", importance=50.0)
    wild_pct = _add(factory, "合成编号5012的记录", importance=50.0)
    lit_us = _add(factory, "合成代号a_b的记录", importance=50.0)
    wild_us = _add(factory, "合成代号axb的记录", importance=50.0)

    got_pct, count_pct = _fallback(factory, query="50%")
    assert _ids(got_pct) == [lit_pct], "% 必须被转义（否则会连 5012 一起捞进来）"
    assert count_pct == len(got_pct) == 1

    got_us, count_us = _fallback(factory, query="a_b")
    assert _ids(got_us) == [lit_us], "_ 必须被转义（否则会连 axb 一起捞进来）"
    assert count_us == 1
    assert wild_pct not in _ids(got_pct) and wild_us not in _ids(got_us)


def test_兜底八键schema与过滤归档工作记忆状态(env):
    """八键 schema 逐字保留；WHERE 三件套不动：归档/working_state 被挡、superseded 在 flag 关时放行、
    ``memory_supersede`` 开时只放行 active/stale。"""
    factory = env["factory"]
    ok_id = _add(factory, "合成兜底正常条 " + MAIN_Q)
    arch_id = _add(factory, "合成兜底已归档条 " + MAIN_Q, is_archived=True)
    ws_id = _add(factory, "合成兜底工作记忆条 " + MAIN_Q, mtype="working_state")
    sup_id = _add(factory, "合成兜底被取代条 " + MAIN_Q, status="superseded")

    off, _c = _fallback(factory)
    assert sorted(_ids(off)) == sorted([ok_id, sup_id]), "flag 关：归档/工作记忆被挡，被取代条可见"
    assert set(off[0]) == {"id", "content", "type", "importance", "created_at",
                           "epistemic_status", "speaker_id", "speaker_type"}, "兜底行固定八键"

    _loop.AGENT_FLAGS["memory_supersede"] = True
    on, _c2 = _fallback(factory)
    assert _ids(on) == [ok_id], "flag 开：状态子句只放行 active/stale"
    assert arch_id not in _ids(on) and ws_id not in _ids(on)


def test_兜底按重要度与时间降序且截断为limit乘二(env):
    """``order_by(importance.desc(), created_at.desc())`` ＋ ``limit(limit*2)`` 逐字保留：
    5 条同词语料、limit=2 ⇒ 只回来 4 条，且正是按 (importance desc, created_at desc) 的前 4。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    base = now_naive_utc() - timedelta(hours=2)
    seeded = []
    for i, (imp, mins) in enumerate([(10.0, 10), (90.0, 90), (90.0, 5), (50.0, 60), (10.0, 120)]):
        seeded.append((_add(factory, "合成排序语料%d %s" % (i, MAIN_Q), importance=imp,
                            created_at=base - timedelta(minutes=mins)), imp, mins))
    want = [mid for mid, _imp, _m in sorted(seeded, key=lambda x: (-x[1], x[2]))][:4]

    got, count = _fallback(factory, limit=2)
    assert _ids(got) == want, "顺序/截断口径必须是 (importance desc, created_at desc) + limit*2"
    assert len(got) == 4 == count, "limit=2 ⇒ 上限 4（limit*2），候选计数同步"


def test_兜底库异常照旧冒泡不留warning(env):
    """原段**没有** try：DB 炸了异常必须冒给调用方（不许新加静默兜底，也不许只留 warning）。"""
    with pytest.raises(RuntimeError):
        _fallback(_BoomSession, query=MAIN_Q)
    assert env["logger"].warnings == [], "这段不许有任何 warning（说明被顺手包了 except）"


def test_兜底会话在helper内退出不跨会话持有(env):
    """``async with`` 作用域仍在 helper 内：进出成对、连接 checkin/close 归还，没有跨会话持有。"""
    factory = env["factory"]
    _add(factory, "合成兜底会话条 " + MAIN_Q)
    spy = _spy_factory(factory)
    events, handlers = _pool_events(env["engine"])
    try:
        _fallback(spy)
    finally:
        _detach(env["engine"], handlers)

    assert spy.box["log"] == ["enter", "exit"], "必须整段进出成对"
    assert len(_like_queries(spy.box["stmts"])) == 1
    assert events[:2] == ["connect", "checkout"] and "checkin" in events and events[-1] == "close", \
        "会话没归还＝连接仍被占着（实际事件 %r）" % (events,)


def test_兜底只捞本角色且空结果仍原语义(env):
    """``character_id`` 条件逐字保留（别的角色的同词语料不许被捞进来）；一条没捞到 ⇒ 返回空 list
    且候选计数 0（caller 走的是「重绑成空 list」这一支，不是保留旧对象）。"""
    factory = env["factory"]
    _add(factory, "合成他角色语料 " + MAIN_Q, char=OTHER_CHAR_ID)
    empty, count = _fallback(factory)
    assert empty == [] and count == 0
    mine = _add(factory, "合成本角色语料 " + MAIN_Q)
    got, count2 = _fallback(factory)
    assert _ids(got) == [mine] and count2 == 1


# ───────────────── 2) 时间路：半开区间 / 去重 / 追加位 / 引用语义 / 闸 ─────────────────

def test_时间区间左闭右开(env):
    """``created_at >= t_start`` 且 ``created_at < t_end``：右端那条**不在**区间内（半开）。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    _temporal_on()
    anchor = now_naive_utc() - timedelta(days=1)
    t_start, t_end = anchor, anchor + timedelta(hours=1)
    at_start = _add(factory, "合成区间左端条", created_at=t_start)
    inside = _add(factory, "合成区间内条", created_at=t_start + timedelta(minutes=1))
    at_end = _add(factory, "合成区间右端条", created_at=t_end)
    after = _add(factory, "合成区间之后条", created_at=t_end + timedelta(minutes=1))

    ret = _temporal(factory, results=[], debug={}, time_range=(t_start, t_end))
    assert sorted(_ids(ret)) == sorted([at_start, inside]), "右端与之后两条都不该进来"
    assert at_end not in _ids(ret) and after not in _ids(ret)


def test_时间路返回同一个list且在原候选之后追加(env):
    """helper 返回的就是 caller 传进来的**那个对象**（不是拷贝）；新增条目排在原候选之后、
    重叠 id 不重复；``debug`` 按引用打戳。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    _temporal_on()
    anchor = now_naive_utc() - timedelta(days=1)
    t_start, t_end = anchor, anchor + timedelta(hours=1)
    already = _add(factory, "合成已在候选里的条", importance=80.0,
                   created_at=t_start + timedelta(minutes=2))
    fresh_low = _add(factory, "合成时间路新条低分", importance=10.0,
                     created_at=t_start + timedelta(minutes=3))
    fresh_high = _add(factory, "合成时间路新条高分", importance=90.0,
                      created_at=t_start + timedelta(minutes=4))

    original = [{"id": already, "content": "原候选行", "type": "event", "importance": 80.0}]
    debug: dict = {}
    ret = _temporal(factory, results=original, debug=debug, time_range=(t_start, t_end))

    assert ret is original, "必须原样交回同一个 list 对象（不许 helper 内另造一份）"
    assert original[0] is not None and _ids(ret) == [already, fresh_high, fresh_low], \
        "重叠 id 不重复追加，新条按 SQL 的 (importance desc, created_at desc) 跟在原候选之后"
    assert ret[0]["content"] == "原候选行", "已有行不被时间路改写"
    assert debug["time_route"] == {"range": [str(t_start), str(t_end)], "added": 2}


def test_时间路flag关时零查询且原样返回(env):
    """flag 关（默认关）⇒ **一条查询都不发**，返回原 list，debug 不多写任何键。"""
    factory = env["factory"]
    _add(factory, "合成时间路闸关条")
    spy = _spy_factory(factory)
    original = [{"id": 1, "content": "原候选", "type": "event", "importance": 1.0}]
    debug: dict = {}

    ret = _temporal(spy, results=original, debug=debug, time_range=("2000-01-01", "2000-01-02"))

    assert _temporal_queries(spy.box["stmts"]) == [], "flag 关 ⇒ 时间查询零条"
    assert ret is original and _ids(ret) == [1]
    assert debug == {}, "flag 关 ⇒ trace 逐字节不变"


def test_时间路flag读取失败按关处理(env, monkeypatch):
    """读 flag 抛异常（import 或 get 炸了）⇒ 那一行 ``except`` 兜住按关处理，零查询、不冒泡。"""
    factory = env["factory"]
    _add(factory, "合成flag读炸条")

    class _BoomFlags(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("flag 表炸了")

    monkeypatch.setattr("app.flags.agent_flags.AGENT_FLAGS", _BoomFlags())
    spy = _spy_factory(factory)
    debug: dict = {}
    original = [{"id": 7, "content": "原候选", "type": "event", "importance": 1.0}]
    ret = _temporal(spy, results=original, debug=debug, time_range=("2000-01-01", "2000-01-02"))

    assert _temporal_queries(spy.box["stmts"]) == [], "读不到 flag ⇒ 零查询"
    assert ret is original and debug == {}


def test_时间路只真新增才写time_route(env, monkeypatch):
    """``_added`` 为 0（全部重叠）时不写 ``time_route``；trace 关时也不写——两档都必须静默。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    _temporal_on()
    anchor = now_naive_utc() - timedelta(days=1)
    t_start, t_end = anchor, anchor + timedelta(hours=1)
    mid = _add(factory, "合成全重叠条", created_at=t_start + timedelta(minutes=1))

    debug: dict = {}
    _temporal(factory, results=[{"id": mid, "content": "原候选", "type": "event", "importance": 1.0}],
              debug=debug, time_range=(t_start, t_end))
    assert "time_route" not in debug, "没有新增 ⇒ 不许写 time_route 键"

    debug2: dict = {}
    _temporal(factory, results=[], debug=debug2, time_range=(t_start, t_end),
              trace_debug=False)
    assert "time_route" not in debug2, "_trace_debug 关 ⇒ 不许写 time_route 键"


def test_时间路过滤归档与工作记忆(env, monkeypatch):
    """时间路那条 SQL 的过滤逐字保留：归档/working_state 被挡；superseded 在 flag 关时放行、
    ``memory_supersede`` 开时只放行 active/stale。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    _temporal_on()
    anchor = now_naive_utc() - timedelta(days=1)
    t_start, t_end = anchor, anchor + timedelta(hours=1)
    at = t_start + timedelta(minutes=1)
    ok_id = _add(factory, "合成时间路正常条", created_at=at)
    arch_id = _add(factory, "合成时间路归档条", created_at=at, is_archived=True)
    ws_id = _add(factory, "合成时间路工作记忆条", created_at=at, mtype="working_state")
    sup_id = _add(factory, "合成时间路被取代条", created_at=at, status="superseded")

    off = _temporal(factory, results=[], debug={}, time_range=(t_start, t_end))
    assert sorted(_ids(off)) == sorted([ok_id, sup_id])
    assert arch_id not in _ids(off) and ws_id not in _ids(off)

    _loop.AGENT_FLAGS["memory_supersede"] = True
    on = _temporal(factory, results=[], debug={}, time_range=(t_start, t_end))
    assert _ids(on) == [ok_id]


def test_时间路limit截断取limit(env):
    """时间路那条 SQL 的 ``limit(limit)`` 逐字保留（兜底是 limit*2，两路各是各的，不许互串）。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    _temporal_on()
    anchor = now_naive_utc() - timedelta(days=1)
    t_start, t_end = anchor, anchor + timedelta(hours=1)
    ids = [_add(factory, "合成时间路截断条%d" % i, importance=90.0 - i,
                created_at=t_start + timedelta(minutes=1 + i)) for i in range(4)]
    ret = _temporal(factory, results=[], debug={}, time_range=(t_start, t_end), limit=2)
    assert _ids(ret) == ids[:2], "limit=2 ⇒ 只取前 2（不是 limit*2）"


def test_时间路查询异常照旧冒泡(env):
    """原段没有 try：时间查询炸了异常必须冒泡（不许静默跳过时间路）。"""
    _temporal_on()
    with pytest.raises(RuntimeError):
        _temporal(_BoomSession, results=[], debug={}, time_range=("2000-01-01", "2000-01-02"))
    assert env["logger"].warnings == []


def test_时间路会话在helper内退出(env):
    """时间路的 ``async with`` 作用域仍在 helper 内：进出成对、连接归还。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    anchor = now_naive_utc() - timedelta(days=1)
    _loop.AGENT_FLAGS["memory_temporal_recall"] = True
    _add(factory, "合成时间路会话条", created_at=anchor + timedelta(minutes=1))
    spy = _spy_factory(factory)
    events, handlers = _pool_events(env["engine"])
    try:
        _temporal(spy, results=[], debug={}, time_range=(anchor, anchor + timedelta(hours=1)))
    finally:
        _detach(env["engine"], handlers)

    assert spy.box["log"] == ["enter", "exit"]
    assert len(_temporal_queries(spy.box["stmts"])) == 1
    assert events[:2] == ["connect", "checkout"] and "checkin" in events and events[-1] == "close"


def test_走caller时时间路不抬候选计数(env, monkeypatch):
    """经 caller 走真链路：向量路 1 条 ＋ 时间路新增 2 条 ⇒ 返回 3 条，而 trace 的
    ``candidate_count`` 仍是**合并后**的 1（本路不进候选计数口径），``time_route`` 键只写新增数。"""
    from app.utils.timeutil import now_naive_utc

    factory = env["factory"]
    anchor = now_naive_utc() - timedelta(days=1)
    t_start, t_end = anchor, anchor + timedelta(hours=1)
    d_id = _add(factory, "合成caller向量条", importance=60.0)
    _add(factory, "合成caller时间路条一", importance=30.0, created_at=t_start + timedelta(minutes=1))
    _add(factory, "合成caller时间路条二", importance=20.0, created_at=t_start + timedelta(minutes=2))

    async def _vec(character_id, query_embedding, limit=5, **_kw):
        return [_dense_row(d_id)]

    monkeypatch.setattr(svc, "vector_search", _vec)
    _loop.AGENT_FLAGS["memory_temporal_recall"] = True
    out = _search(MAIN_Q, time_range=(t_start, t_end))

    got_ids = _ids(out)
    assert len(out) == 3 and d_id in got_ids, "向量那条＋时间路两条都进出口"
    tr = env["trace"][-1]
    assert tr["route"] == "dense", "有向量候选 ⇒ route 不受时间路影响"
    steps = json.loads(tr["steps_json"])
    assert steps["candidate_count"] == 1 == steps["hit_count"], "候选计数不随时间路增加"
    assert steps["time_route"]["added"] == 2
    assert len(steps["time_route"]["range"]) == 2


# ───────────────── 3) 形状类守卫（AST，不跑 IO） ─────────────────

def _module_node(name):
    src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("retrieve.py 里找不到 %s" % name)


@pytest.mark.parametrize("helper_name,caller_kw", [
    ("_keyword_fallback", ["character_id", "query", "limit", "async_session_factory"]),
    ("_append_temporal_hits", ["results", "debug", "character_id", "time_range", "limit",
                               "_trace_debug", "async_session_factory"]),
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
    rets = [s for s in helper.body if isinstance(s, ast.Return)]
    assert len(rets) == 1, "helper 只多一条 return 运输"


def test_caller仍保留两个判定且两个helper各走各的():
    """「有没有候选」与「有没有时间区间」两个判定都必须留在 caller（不许被 helper 吞掉变成新判断），
    两个分支各自只留一条 await 调用、彼此不相连。"""
    caller = _module_node("search_memories")
    body = list(caller.body)
    fb = next(s for s in body if isinstance(s, ast.If) and ast.unparse(s.test) == "not results")
    tp = next(s for s in body
              if isinstance(s, ast.If) and ast.unparse(s.test) == "time_range is not None")
    assert isinstance(fb.body[0], ast.Assign) and "_keyword_fallback" in ast.unparse(fb.body[0])
    assert isinstance(tp.body[0], ast.Assign) and "_append_temporal_hits" in ast.unparse(tp.body[0])
    assert "_append_temporal_hits" not in ast.unparse(fb), "两路不许互相调用（保持两段独立）"
    assert "_keyword_fallback" not in ast.unparse(tp), "两路不许互相调用（保持两段独立）"
    # 兜底在前、时间路在后（与原顺序一致）
    assert body.index(fb) < body.index(tp), "兜底必须排在时间路之前（原顺序）"


def test_两helper零LLM节点且依赖按裸全局名晚绑定():
    """两段都是纯 SQL/搬运：不得出现任何模型调用节点；``select``/``Memory``/
    ``_like_escape``/``_retrievable_status_clause`` 仍按裸全局名晚绑定（模块内改名会被立刻发现）。"""
    file_src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    for name in ("_keyword_fallback", "_append_temporal_hits"):
        helper = _module_node(name)
        src = ast.get_source_segment(file_src, helper)
        assert "from app.memory.service import" not in src, "%s 不得自己重导入 service 符号" % name
        code = [ast.unparse(n) for n in ast.walk(helper)
                if isinstance(n, (ast.Call, ast.Import, ast.ImportFrom, ast.Name, ast.Attribute))]
        for bad in ("chat_completion", "llm_client", "encode", "inference",
                    "vector_search", "bm25_search", "text_embedding"):
            assert not [c for c in code if bad in c], "%s 里出现 %r" % (name, bad)
    kw_loaded = {n.id for n in ast.walk(_module_node("_keyword_fallback"))
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    for dep in ("select", "Memory", "_like_escape", "_retrievable_status_clause"):
        assert dep in kw_loaded, "兜底段 %s 必须继续按裸全局名晚绑定" % dep
    tp_loaded = {n.id for n in ast.walk(_module_node("_append_temporal_hits"))
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    for dep in ("select", "Memory", "_retrievable_status_clause"):
        assert dep in tp_loaded, "时间段 %s 必须继续按裸全局名晚绑定" % dep


def test_两helper都没有新加的except包裹():
    """兜底段原实现**没有** try；时间段只有 flag 读取那一个 try/except（原样搬来）。
    所以：兜底段 try 数必须为 0，时间段恰好 1 个且只包着 flag 读取那几行。"""
    kw_src = ast.unparse(_module_node("_keyword_fallback"))
    tp_src = ast.unparse(_module_node("_append_temporal_hits"))
    assert "try:" not in kw_src, "兜底段不许新增 try/except（DB 异常要照旧冒泡）"
    assert tp_src.count("try:") == 1, "时间段只保留 flag 读取那一个 try"
    assert "memory_temporal_recall" in tp_src.split("try:")[1].split("except")[0], \
        "那个 try 必须仍然只包 flag 读取"


def test_签名与公共出口未变():
    """抽走两段不改 ``search_memories`` 的签名（service 垫片与全部调用方零感知）。"""
    sig = inspect.signature(_retrieve.search_memories)
    assert list(sig.parameters) == ["character_id", "query", "limit", "queries", "trace_meta",
                                    "time_range", "scene", "exclude_sources", "group_id", "user_id"]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in sig.parameters.values())
    assert inspect.iscoroutinefunction(_retrieve.search_memories)
