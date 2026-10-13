# -*- coding: utf-8 -*-
"""A50 批10 守卫：``search_memories`` 的三路召回块（三闭包＋三路 gather＋命中打戳）
机械抽成同文件私有协程 ``_recall_three_routes`` 之后的行为不变式。

两类判据，**都不是**「把 helper 整个 mock 掉再数调用次数」：

- **行为类**（真临时库 + 真 helper + 真 caller，只把外部依赖打桩）：三路闭包各跑一次且**真并发**
  （三路互等同一道闸：串行执行必然超时）、三路返回与 debug 打戳（含键插入顺序与体积上限）、
  合并顺序 dense→sparse→entity、主查询不吃派生缓存、最多 4 路、稠密/稀疏各自异常退化为空、
  专名路异常照旧冒泡、flag 关时专名路一条查询都不发、裸全局名晚绑定接缝、ONNX 调用次数不增。
- **形状类**（AST 源码断言，不跑 IO）：helper 必须是 ``retrieve.py`` 里的私有协程、caller 只有
  一处调用且按原名字解包、helper 内**零** service 重导入（三个符号由 caller 显式传入）、
  **零** LLM/ONNX 直调、``gather`` 仍是「外层 3 参 ＋ 内层各一次 gather」、``query_list`` 口径不变。

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

from _dbclone import clone_engine, make_session_factory

import app.memory.service as svc
from app.agent import loop as _loop
from app.memory import retrieve as _retrieve
from app.models.memory import Memory

pytestmark = pytest.mark.slow

CHAR_ID = 101
MAIN_Q = "主查询甲"


# ─────────────────────────────── flag 隔离 ───────────────────────────────

@pytest.fixture(autouse=True)
def clean_flags():
    """本文件任何用例都不依赖本机 runtime_flags 现值：前后复位。"""
    keys = ("recall_entity_match", "recall_recency_bonus", "recall_neighbor_block",
            "memory_peak_cutoff", "memory_temporal_recall", "perception_source_tag",
            "perception_isolate")
    saved = {k: _loop.AGENT_FLAGS.get(k) for k in keys}
    saved_trace = _loop.AGENT_FLAGS.get("memory_trace_debug")

    def _reset():
        for k in keys:
            _loop.AGENT_FLAGS[k] = False
        _loop.AGENT_FLAGS["memory_trace_debug"] = True

    _reset()
    yield
    for k, v in saved.items():
        _loop.AGENT_FLAGS[k] = v
    _loop.AGENT_FLAGS["memory_trace_debug"] = saved_trace


class _Silent:
    def __getattr__(self, _name):
        return lambda *a, **k: None


# ─────────────────────────────── 临时库夹具 ───────────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时文件库（_dbclone 克隆到 tmp_path）＋外部依赖全打桩，桩都记调用轨迹。"""
    engine = clone_engine(tmp_path / "a50b10_routes.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b10_u1", nickname="批10用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=1, name="批10角色"))
            await db.commit()

    asyncio.run(_seed_parents())

    calls: dict[str, list] = {"text_embedding": [], "vector_search": [], "bm25_search": [],
                              "cached_embedding": [], "entity_route": [], "trace": []}
    trace_box: list[dict] = []

    async def _embed(text):
        calls["text_embedding"].append(text)
        return [0.0] * 8

    async def _vector(character_id, query_embedding, limit=5, **_kw):
        calls["vector_search"].append({"character_id": character_id, "limit": limit})
        return []

    async def _sparse(character_id, query, top_k=5):
        calls["bm25_search"].append({"character_id": character_id, "query": query, "top_k": top_k})
        return []

    async def _cached(character_id, query):
        calls["cached_embedding"].append({"character_id": character_id, "query": query})
        return [0.0] * 8

    async def _entity(character_id, query):
        calls["entity_route"].append({"character_id": character_id, "query": query})
        return [], {"terms": [], "reasons": []}

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _vector)
    monkeypatch.setattr(svc, "bm25_search", _sparse)
    monkeypatch.setattr(_retrieve, "get_cached_embedding", _cached)
    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    monkeypatch.setattr(_retrieve, "_logger", _Silent())
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", lambda *a, **k: [])
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: trace_box.append(kw))

    yield {"factory": factory, "calls": calls, "trace": trace_box}

    asyncio.run(engine.dispose())


def _add(factory, content, *, importance=50.0, char=CHAR_ID, mtype="event", **kw):
    """落一条合成记忆（精确控制属性；正文全是合成字串）。"""
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=char, memory_type=mtype, content=content,
                       source="chat", importance=importance,
                       created_at=now_naive_utc() - timedelta(minutes=1), **kw)
            db.add(m)
            await db.commit()
            return m.id

    return asyncio.run(_run())


def _search(query=MAIN_Q, **kw):
    return asyncio.run(_retrieve.search_memories(CHAR_ID, query, **kw))


def _ids(rows):
    return [r["id"] for r in rows]


def _dense_row(mid, distance=0.1):
    return {"id": mid, "content": "合成向量条", "type": "event",
            "importance": 50.0, "distance": distance}


# ───────────────────── 1) 三路闭包：各跑一次且真并发 ─────────────────────

def test_三路闭包各跑一次(env):
    """稠密/稀疏/专名三路各发一次查询（不是「跑了几次」的口头承诺，是真实调用轨迹）。"""
    _add(env["factory"], "合成语料甲")
    _loop.AGENT_FLAGS["recall_entity_match"] = True
    _search()
    assert len(env["calls"]["vector_search"]) == 1
    assert len(env["calls"]["bm25_search"]) == 1
    assert len(env["calls"]["entity_route"]) == 1
    assert env["calls"]["entity_route"][0] == {"character_id": CHAR_ID, "query": MAIN_Q}


def test_三路真并发而非串行(env, monkeypatch):
    """三路必须**同时**在飞：每路进桩后等同一道闸，闸要三路都到才开。

    串行执行时第一路永远等不到另外两路 ⇒ ``wait_for`` 超时 ⇒ 当场红（不是靠耗时阈值猜的）。
    """
    async def _main():
        arrived: list[str] = []
        gate = asyncio.Event()

        async def _wait(tag):
            arrived.append(tag)
            if len(arrived) >= 3:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)

        async def _vec(*a, **k):
            await _wait("dense")
            return []

        async def _sparse(*a, **k):
            await _wait("sparse")
            return []

        async def _entity(*a, **k):
            await _wait("entity")
            return [], {"terms": [], "reasons": []}

        monkeypatch.setattr(svc, "vector_search", _vec)
        monkeypatch.setattr(svc, "bm25_search", _sparse)
        monkeypatch.setattr(_retrieve, "_entity_route", _entity)
        _loop.AGENT_FLAGS["recall_entity_match"] = True
        await asyncio.wait_for(_retrieve.search_memories(CHAR_ID, MAIN_Q), timeout=5)
        return arrived

    assert sorted(asyncio.run(_main())) == ["dense", "entity", "sparse"]


# ───────────────────── 2) 运输契约与 debug 打戳 ─────────────────────

def test_helper返回值与caller解包一致(env, monkeypatch):
    """helper 直接调用的五元组，与 caller 经 ``search_memories`` 走一圈后的三路结果逐项一致。"""
    factory, calls = env["factory"], env["calls"]
    dense_id = _add(factory, "合成稠密条")
    sparse_id = _add(factory, "合成稀疏条", importance=40.0)
    entity_id = _add(factory, "合成专名条", importance=30.0)

    async def _fake_vector(character_id, query_embedding, limit=5, **_kw):
        calls["vector_search"].append({"character_id": character_id, "limit": limit})
        return [_dense_row(dense_id)]

    async def _fake_sparse(character_id, query, top_k=5):
        calls["bm25_search"].append({"character_id": character_id, "query": query, "top_k": top_k})
        return [(sparse_id, 2.5)]

    async def _fake_entity(character_id, query):
        calls["entity_route"].append({"character_id": character_id, "query": query})
        return ([{"id": entity_id, "content": "合成专名条", "type": "event", "importance": 30.0}],
                {"terms": ["甲", "乙", "丙", "丁", "戊"], "reasons": ["r%d" % i for i in range(9)]})

    monkeypatch.setattr(svc, "vector_search", _fake_vector)
    monkeypatch.setattr(svc, "bm25_search", _fake_sparse)
    monkeypatch.setattr(_retrieve, "_entity_route", _fake_entity)
    _loop.AGENT_FLAGS["recall_entity_match"] = True

    async def _main():
        helper_debug = {"query": MAIN_Q, "derived_queries": []}
        got = await _retrieve._recall_three_routes(
            character_id=CHAR_ID, query=MAIN_Q, query_list=[MAIN_Q], limit=5, debug=helper_debug,
            text_embedding=svc.text_embedding, vector_search=svc.vector_search,
            bm25_search=svc.bm25_search)
        hits = await _retrieve.search_memories(CHAR_ID, MAIN_Q, limit=5)
        return got, helper_debug, hits, env["trace"][-1]

    (dense_hits, sparse_hits, entity_rows, entity_meta, entity_ids), helper_debug, hits, tr = asyncio.run(_main())

    # 五元组形状与内容
    assert [h["id"] for h in dense_hits[0]] == [dense_id]
    assert sparse_hits[0] == [(sparse_id, 2.5)]
    assert [r["id"] for r in entity_rows] == [entity_id]
    assert entity_ids == [entity_id]
    assert entity_meta["terms"] == ["甲", "乙", "丙", "丁", "戊"]        # 元信息照常运输（后文不用也不丢）
    # caller 走完一圈后，三路集合与 helper 直调一致（合并顺序 dense→sparse→entity）
    assert set(_ids(hits)) == {dense_id, sparse_id, entity_id}
    assert _ids(hits)[0] == dense_id
    # debug 原地打戳：caller 那个对象按引用被 helper 改过
    assert helper_debug["dense_hits"] == [dense_id]
    # trace 键序：前四键固定，entity 两键紧跟其后且顺序固定（保 steps_json 字节序）
    keys = list(json.loads(tr["steps_json"]).keys())
    assert keys[:4] == ["query", "derived_queries", "dense_hits", "sparse_hits"]
    assert keys[4:6] == ["entity_hits", "entity_route"]
    # 体积上限：每路 id ≤5、terms ≤4、reasons ≤ ENTITY_REASON_TRACE_MAX
    steps = json.loads(tr["steps_json"])
    assert steps["entity_hits"] == [entity_id]
    assert len(steps["entity_route"]["terms"]) == 4
    assert len(steps["entity_route"]["reasons"]) == _retrieve.ENTITY_REASON_TRACE_MAX
    assert len(steps["dense_hits"]) <= 5 and len(steps["sparse_hits"]) <= 5


def test_各路命中体积上限仍钳在5(env, monkeypatch):
    """八条命中进三路 ⇒ debug 里每路仍只留 5 个 id（钳制没跟着搬运丢）。"""
    ids = [_add(env["factory"], "合成批%d条" % i) for i in range(8)]

    async def _fake_vector(character_id, query_embedding, limit=5, **_kw):
        return [_dense_row(m) for m in ids]

    async def _fake_sparse(character_id, query, top_k=5):
        return [(m, 1.0) for m in ids]

    monkeypatch.setattr(svc, "vector_search", _fake_vector)
    monkeypatch.setattr(svc, "bm25_search", _fake_sparse)
    _search()
    steps = json.loads(env["trace"][-1]["steps_json"])
    assert steps["dense_hits"] == ids[:5]
    assert steps["sparse_hits"] == ids[:5]


# ───────────────────── 3) 取料口径：主查询不吃缓存 / 最多 4 路 ─────────────────────

def test_主查询走text_embedding派生查询走缓存(env):
    """X-3 口径：主查询恒走 ``text_embedding``；只有派生查询吃进程内缓存（且不多一次 ONNX）。"""
    _add(env["factory"], "合成语料乙")
    _search(MAIN_Q, queries=["派生一", "派生二", "派生三", "派生四"])
    assert env["calls"]["text_embedding"] == [MAIN_Q]
    assert [c["query"] for c in env["calls"]["cached_embedding"]] == ["派生一", "派生二", "派生三"]
    assert all(c["character_id"] == CHAR_ID for c in env["calls"]["cached_embedding"])


def test_最多四路且取量不变(env):
    """``query_list = [query] + queries[:3]`` ⇒ 最多 4 路；向量取 limit*2、BM25 取 max(limit*2,5)。"""
    _add(env["factory"], "合成语料丙")
    _search(MAIN_Q, queries=["d%d" % i for i in range(9)], limit=3)
    assert len(env["calls"]["vector_search"]) == 4
    assert len(env["calls"]["bm25_search"]) == 4
    assert [c["query"] for c in env["calls"]["bm25_search"]] == [MAIN_Q, "d0", "d1", "d2"]
    assert {c["limit"] for c in env["calls"]["vector_search"]} == {6}          # limit*2
    assert {c["top_k"] for c in env["calls"]["bm25_search"]} == {6}             # max(limit*2, 5)
    assert {c["character_id"] for c in env["calls"]["vector_search"]} == {CHAR_ID}


def test_小limit时BM25地板为5(env):
    """limit=1 ⇒ 向量取 2、BM25 仍取地板 5（两处取量公式各自独立，没被合并）。"""
    _add(env["factory"], "合成语料丁")
    _search(MAIN_Q, limit=1)
    assert {c["limit"] for c in env["calls"]["vector_search"]} == {2}
    assert {c["top_k"] for c in env["calls"]["bm25_search"]} == {5}


def test_ONNX调用次数等于路数不增(env):
    """一次检索的嵌入调用总数＝1 主查询 ＋ N 派生查询；不多调也不漏调。"""
    _add(env["factory"], "合成语料戊")
    _search(MAIN_Q, queries=["p1", "p2"])
    assert len(env["calls"]["text_embedding"]) + len(env["calls"]["cached_embedding"]) == 3


# ───────────────────── 4) 异常边界 ─────────────────────

def test_稠密路异常退化为空且不影响稀疏路(env, monkeypatch):
    """向量桩抛异常 ⇒ 该路返回 []（不冒泡），稀疏路命中照常出现在结果里。"""
    sparse_id = _add(env["factory"], "合成稀疏存活条", importance=60.0)

    async def _boom(*a, **k):
        raise RuntimeError("向量库炸了")

    async def _sparse(character_id, query, top_k=5):
        return [(sparse_id, 3.0)]

    monkeypatch.setattr(svc, "vector_search", _boom)
    monkeypatch.setattr(svc, "bm25_search", _sparse)
    assert _ids(_search()) == [sparse_id]


def test_稠密路全炸时结果为空不抛异常(env, monkeypatch):
    """两路都炸且关键词兜底也捞不到 ⇒ 返回空列表，不抛异常（异常边界跟着搬对了）。"""
    _add(env["factory"], "合成兜底无关语料")

    async def _boom(*a, **k):
        raise RuntimeError("炸")

    monkeypatch.setattr(svc, "vector_search", _boom)
    monkeypatch.setattr(svc, "bm25_search", _boom)
    assert _search("zzz完全查不到的词") == []


def test_稀疏路异常退化为空且不影响稠密路(env, monkeypatch):
    """BM25 桩抛异常 ⇒ 该路返回 []，稠密路命中照常出现。"""
    dense_id = _add(env["factory"], "合成稠密存活条", importance=60.0)

    async def _vec(character_id, query_embedding, limit=5, **_kw):
        return [_dense_row(dense_id)]

    async def _boom(*a, **k):
        raise RuntimeError("索引炸了")

    monkeypatch.setattr(svc, "vector_search", _vec)
    monkeypatch.setattr(svc, "bm25_search", _boom)
    assert _ids(_search()) == [dense_id]


def test_专名路异常照旧冒泡(env, monkeypatch):
    """专名路**无** try 边界：``_entity_route`` 抛错必须经 gather 冒泡到调用方（不许被吞）。"""
    _add(env["factory"], "合成语料己")

    async def _boom(character_id, query):
        raise RuntimeError("专名路炸了")

    monkeypatch.setattr(_retrieve, "_entity_route", _boom)
    _loop.AGENT_FLAGS["recall_entity_match"] = True
    with pytest.raises(RuntimeError, match="专名路炸了"):
        _search()


# ───────────────────── 5) flag 关＝零查询 / 晚绑定接缝 ─────────────────────

def test_专名flag关时零查询且不打戳(env, monkeypatch):
    """flag 关：专名路一条查询都不发，debug 里也不出现 entity_hits / entity_route 两键。"""
    _add(env["factory"], "合成语料庚")

    async def _boom(character_id, query):
        raise AssertionError("flag 关时不应进入专名第三路")

    monkeypatch.setattr(_retrieve, "_entity_route", _boom)
    _search()
    assert env["calls"]["entity_route"] == []
    steps = json.loads(env["trace"][-1]["steps_json"])
    assert "entity_hits" not in steps and "entity_route" not in steps
    assert "dense_hits" in steps and "sparse_hits" in steps


def test_检索模块裸全局名晚绑定接缝仍在(env, monkeypatch):
    """helper 留在 retrieve.py 内按裸全局名取 ``_entity_match_on``/``_entity_route``/
    ``get_cached_embedding``：运行期替换模块属性立即生效（测试打桩面接缝未被搬断）。"""
    factory, calls = env["factory"], env["calls"]
    hit = _add(factory, "合成晚绑定命中条")
    seen = {"entity": 0, "cached": 0}

    def _match_on():
        return True

    async def _entity(character_id, query):
        seen["entity"] += 1
        return [{"id": hit, "content": "合成晚绑定命中条", "type": "event", "importance": 50.0}], \
            {"terms": ["晚"], "reasons": ["stub"]}

    async def _cached(character_id, query):
        seen["cached"] += 1
        calls["cached_embedding"].append({"character_id": character_id, "query": query})
        return [0.0] * 8

    monkeypatch.setattr(_retrieve, "_entity_match_on", _match_on)
    monkeypatch.setattr(_retrieve, "_entity_route", _entity)
    monkeypatch.setattr(_retrieve, "get_cached_embedding", _cached)

    got = _ids(_search(MAIN_Q, queries=["派生X"]))
    assert seen["entity"] == 1 and seen["cached"] == 1
    assert [c["query"] for c in calls["cached_embedding"]] == ["派生X"]
    assert hit in got


# ───────────────────── 6) 形状类守卫（AST，不跑 IO） ─────────────────────

def _module_node(name):
    src = Path(_retrieve.__file__).read_text(encoding="utf-8")
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("retrieve.py 里找不到 %s" % name)


def test_helper是同文件私有协程且caller只有一处调用():
    helper, caller = _module_node("_recall_three_routes"), _module_node("search_memories")
    assert isinstance(helper, ast.AsyncFunctionDef), "_recall_three_routes 必须是协程（要与 gather 语义一致）"
    assert helper.name.startswith("_"), "必须是私有名"
    calls = [n for n in ast.walk(caller)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "_recall_three_routes"]
    assert len(calls) == 1, "caller 内必须只有一处调用（实际 %d 处）" % len(calls)
    call = calls[0]
    awaits = [n for n in ast.walk(caller)
              if isinstance(n, ast.Await) and n.value is call]
    assert len(awaits) == 1, "调用必须被 await（不等价于 gather 语义）"
    assert [kw.arg for kw in call.keywords] == [
        "character_id", "query", "query_list", "limit", "debug",
        "text_embedding", "vector_search", "bm25_search"], "八个实参必须显式按名传入"
    assign = next(s for s in caller.body
                  if isinstance(s, ast.Assign) and "_recall_three_routes" in ast.unparse(s.value))
    targets = [t.id for t in assign.targets[0].elts]
    assert targets == ["dense_hits", "sparse_hits", "entity_rows", "entity_meta", "_entity_ids"], \
        "必须按原名字解包五个返回值"
    ret = [s for s in helper.body if isinstance(s, ast.Return)]
    assert len(ret) == 1 and ast.unparse(ret[0].value) == \
        "(dense_hits, sparse_hits, entity_rows, entity_meta, _entity_ids)", "helper 只多一条 return 运输"


def test_helper三个service符号是keywordonly入参且零重导入():
    helper = _module_node("_recall_three_routes")
    kwonly = [a.arg for a in helper.args.kwonlyargs]
    assert helper.args.args == [] and helper.args.defaults == []
    assert kwonly == ["character_id", "query", "query_list", "limit", "debug",
                      "text_embedding", "vector_search", "bm25_search"]
    src = ast.get_source_segment(Path(_retrieve.__file__).read_text(encoding="utf-8"), helper)
    assert "from app.memory.service import" not in src, "helper 不得自己重导入 service 符号"
    assert "import app.memory.service" not in src
    # LLM/推理直调只查**代码节点**（原注释里本就写着「免 ONNX 推理」，不能按文本子串判）
    code = [ast.unparse(n) for n in ast.walk(helper)
            if isinstance(n, (ast.Call, ast.Import, ast.ImportFrom, ast.Name, ast.Attribute))]
    for bad in ("chat_completion", "llm_client", "encode", "inference"):
        assert not [c for c in code if bad in c], "helper 代码里出现 %r：召回判定路径不许调模型" % bad
    imports = sorted(ast.unparse(n) for n in ast.walk(helper) if isinstance(n, (ast.Import, ast.ImportFrom)))
    assert imports == ["from app.flags.agent_flags import AGENT_FLAGS as _af",
                       "import asyncio as _asyncio"], "helper 只允许原块里那两次局部导入（逐字搬运，不增不减）"


def test_gather仍是外层三参加内层各一次():
    helper = _module_node("_recall_three_routes")
    gathers = [n for n in ast.walk(helper)
               if isinstance(n, ast.Call) and ast.unparse(n.func) == "_asyncio.gather"]
    assert len(gathers) == 3, "应是外层一次 + 内层两次（实际 %d 次）" % len(gathers)
    outer = [g for g in gathers if len(g.args) == 3]
    assert len(outer) == 1, "外层 gather 必须仍是三个参数"
    inner = [g for g in gathers if g is not outer[0]]
    assert len(inner) == 2 and all(len(g.args) == 1 for g in inner)
    assert "return_exceptions" not in {kw.arg for kw in outer[0].keywords}, "不许改成吞异常的形状"
    # 外层三个位置：内层 dense gather / 内层 sparse gather / 专名路
    third = ast.unparse(outer[0].args[2])
    assert third.endswith("()") and "_entity_one" in third


def test_caller_query_list与局部导入口径未变():
    caller = _module_node("search_memories")
    src = ast.get_source_segment(Path(_retrieve.__file__).read_text(encoding="utf-8"), caller)
    assert "query_list = [query] + (list(queries or [])[:3])" in src
    assert "from app.memory.service import (" in src, "caller 仍保留原局部导入（打桩面）"
    # 批12查询随段搬，factory仍按caller局部导入原时点传给三个IO helper。
    assert "async_session_factory" in src
    fuse = _module_node("_fuse_route_relevance")
    fuse_src = ast.get_source_segment(Path(_retrieve.__file__).read_text(encoding="utf-8"), fuse)
    assert "_rrf.reciprocal_rank_fusion" in fuse_src
    fuse_calls = [n for n in ast.walk(caller) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name) and n.func.id == "_fuse_route_relevance"]
    assert len(fuse_calls) == 1
    assert any(k.arg == "_rrf" and ast.unparse(k.value) == "_rrf" for k in fuse_calls[0].keywords)
    calls = [n for n in ast.walk(caller) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id in {"_merge_route_candidates", "_keyword_fallback", "_append_temporal_hits"}]
    assert len(calls) == 3
    for call in calls:
        values = [k.value for k in call.keywords if k.arg == "async_session_factory"]
        assert len(values) == 1 and ast.unparse(values[0]) == "async_session_factory"


def test_签名与公共出口未变():
    """helper 抽走不改 search_memories 的签名（service 垫片与全部调用方零感知）。"""
    sig = inspect.signature(_retrieve.search_memories)
    assert list(sig.parameters) == ["character_id", "query", "limit", "queries", "trace_meta",
                                    "time_range", "scene", "exclude_sources", "group_id", "user_id"]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in sig.parameters.values())
