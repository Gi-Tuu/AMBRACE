# -*- coding: utf-8 -*-
"""A50 批 4：``save_memory`` 新建记忆**准备阶段**（``_prepare_created_memory``）守卫（2026-10-12）。

被测切口：把 ``save_memory`` 中后段 ``pct = _normalize_importance(importance)`` 起、到 L4 计划有效期
那段 ``try/except`` 末尾为止的**连续块**（14 条语句 / 86 行），机械抽成同文件模块级私有协程
``_prepare_created_memory``；调用点仍在写入前查重之后、``db.add(memory)`` 之前。
**纯搬运、零行为变化**，所以守卫的全部目的就是「证明它没变」：

- 结构冻结（纯 AST，零 IO、不依赖 git／docs）：helper 的形态与二十三个 keyword-only 形参、
  语句序列恰为 ``[docstring, 原块 14 条, Return]``、**无总包围 try**、四段 except 结构
  （两段 ``as e`` 带回落、两段静默 ``pass``）逐字保留、返回七元同序、调用点是「await + 七元解包」
  且落在原块的同一序号位置、其后紧接 ``db.add(memory)``、十一个模块全局依赖仍晚绑定（未被提成
  形参或模块级缓存）、块内仍只有那一个 ``await``、块内仍无 ``db.add/flush/commit/refresh``；
- 行为冻结（临时库真跑 ``save_memory``，合成数据）：显式／默认 speaker 两条归属路径、感知来源
  收口、准入闸门开／关／待核／两处异常回落、epistemic 缺省来源、影子埋点开／关／异常静默、
  importance 与衰减时间的调用次数与顺序、派生列表 JSON、plan 标注开／非 plan／flag 关／标注抛错、
  准备完成后 ``db.add`` 之前无落库、后续回执与 Chroma 顺序不变、台词先拒时 helper 一次都不跑、
  reject/merge 入口根本不进本段。

纪律：临时库走 pytest ``tmp_path``（``_dbclone`` 克隆模板库），绝不连生产库；全文只用合成数据，
不引用任何真实用户内容、仓库绝对路径或内部文档；嵌入／向量查重／向量写入全部打桩，不发生真实推理。
"""
import ast
import asyncio
import io
import json as _json
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
import app.memory.service as svc

WRITE_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"

HELPER = "_prepare_created_memory"
# 二十三个依赖一律 keyword-only：save_memory 的 20 个形参（除 skip_dedup）＋ _src_msg_sender ＋ db ＋ timedelta
PARAMS = ["db", "timedelta", "user_id", "character_id", "memory_type", "content", "title",
          "importance", "sub_type", "source", "related_memory_id", "source_id", "group_id",
          "scope", "speaker_id", "speaker_type", "epistemic_status", "reliability", "chain_id",
          "parent_id", "node_type", "derived_from_ids", "_src_msg_sender"]
RETURNED = ["memory", "pct", "_spk_type", "_spk", "_epi", "_pending", "_sub_type_before_plan"]
# 段内新建的八个局部：返回七个 ＋ 只活在段内的 _spk_id
LOCALS8 = set(RETURNED) | {"_spk_id"}
# 十一个依赖必须继续按模块全局晚绑定（monkeypatch 接缝在模块属性上）
DEFERRED = ["Memory", "json", "_admission_gate_on", "_is_perception_source",
            "perception_actor_column", "_resolve_admission_sender", "admit_memory",
            "_normalize_importance", "_initial_strength", "_now_naive", "_logger"]

# 合成记忆正文（不对应任何真实用户内容）
BODY_USER = "阳台上的薄荷又长高了一截"
BODY_PLAN = "下周三之前要把体检报告取回来"


# ───────────────────────── 结构冻结（纯 AST，零 IO） ─────────────────────────

def _tree():
    return ast.parse(io.open(WRITE_PY, encoding="utf-8").read())


def _fn(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("write.py 里找不到 %s" % name)


def _flat(fn):
    """摊平 save_memory 的 async with 一层，让调用点与原块在同一层比。"""
    out = []
    for s in fn.body:
        if isinstance(s, ast.AsyncWith):
            out.extend(s.body)
        else:
            out.append(s)
    return out


def _call_node(tree):
    for s in _flat(_fn(tree, "save_memory")):
        if isinstance(s, ast.Assign) and HELPER in ast.unparse(s):
            return s
    raise AssertionError("save_memory 里找不到 %s 的调用点" % HELPER)


def test_helper是模块级私有协程且二十三个形参固定():
    helper = _fn(_tree(), HELPER)
    assert isinstance(helper, ast.AsyncFunctionDef), "必须 async（段内有 await _resolve_admission_sender）"
    assert helper.name.startswith("_"), "私有协程，名字带下划线前缀"
    assert helper.args.args == [] and helper.args.kwonlyargs, "二十三个依赖一律 keyword-only"
    assert [a.arg for a in helper.args.kwonlyargs] == PARAMS
    assert helper.args.vararg is None and helper.args.kwarg is None
    assert not helper.decorator_list


def test_helper体语句序列是docstring_原块14条_Return():
    """没有总包围 try：原四段 try/except 保持原样，外层不许再包一层（那会改变异常边界）。"""
    helper = _fn(_tree(), HELPER)
    body = list(helper.body)
    kinds = [type(s).__name__ for s in body]
    assert kinds[0] == "Expr" and isinstance(body[0].value, ast.Constant), "首条是 docstring"
    assert kinds[-1] == "Return", kinds[-1]
    assert len(body) == 16, "docstring + 原块 14 条 + return，%d" % len(body)
    # 原块首条 pct、末条是 plan 那段 try
    assert ast.unparse(body[1]) == "pct = _normalize_importance(importance)"
    assert isinstance(body[-2], ast.Try) and isinstance(body[-2].handlers[0].type, ast.Name)
    assert body[-2].handlers[0].type.id == "Exception"


def test_四段except结构逐字保留():
    """准入两段各自回落 + warning，影子与 plan 两段完全静默——既有语义，不得顺手修。"""
    helper = _fn(_tree(), HELPER)
    hs = [h for s in helper.body[1:-1] for h in ast.walk(s) if isinstance(h, ast.ExceptHandler)]
    assert len(hs) == 4, [ast.unparse(h) for h in hs]
    assert all(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in hs)
    named = [h for h in hs if h.name]
    silent = [h for h in hs if h.name is None]
    assert sorted(h.name for h in named) == ["e", "e"]
    # 静默两段各自只有一个 pass；带回落的两段必须先赋值再打 warning
    assert all([type(x).__name__ for x in h.body] == ["Pass"] for h in silent)
    for h in named:
        kinds = [type(x).__name__ for x in h.body]
        assert kinds[-1] == "Expr" and kinds[0].startswith("Assign"), kinds


def test_返回七元同序且不含_spk_id():
    helper = _fn(_tree(), HELPER)
    ret = helper.body[-1]
    assert isinstance(ret.value, ast.Tuple)
    assert [e.id for e in ret.value.elts] == RETURNED
    assert "_spk_id" not in RETURNED, "_spk_id 只活在段内（Memory 列 + 影子埋点）"


def test_除八个段内局部外不新增任何赋值():
    helper = _fn(_tree(), HELPER)
    bound = {n.id for n in ast.walk(helper)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    assert bound == LOCALS8, sorted(bound)
    params = {a.arg for a in helper.args.kwonlyargs}
    assert not (bound & params), "helper 不得改写任何形参：%s" % sorted(bound & params)


def test_调用点是await加七元解包且落在原块的位置():
    tree = _tree()
    call = _call_node(tree)
    assert isinstance(call.value, ast.Await), "调用点必须 await"
    inner = call.value.value
    assert isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
    assert inner.func.id == HELPER
    assert inner.args == [] and [k.arg for k in inner.keywords] == PARAMS, \
        "二十三个依赖一律 keyword 传入、不留位置参数"
    tgt = call.targets[0]
    assert isinstance(tgt, ast.Tuple) and [e.id for e in tgt.elts] == RETURNED, \
        "解包顺序必须与 helper 返回顺序一致"


def test_调用点位置不得漂移():
    tree = _tree()
    flat = _flat(_fn(tree, "save_memory"))
    idx = flat.index(_call_node(tree))
    assert ast.unparse(flat[idx - 1]) == "embedding = None"
    assert "persist_candidate" in ast.unparse(flat[idx + 1])
    assert "await persist_candidate" in ast.unparse(flat[idx + 2])
    assert "if not created" in ast.unparse(flat[idx + 3])



def test_十一个依赖仍是模块全局晚绑定():
    """不得提成默认参数／模块级缓存：测试 monkeypatch 与直接取用的正是 app.memory.write 上这些属性。"""
    tree = _tree()
    helper = _fn(tree, HELPER)
    used = {n.id for n in ast.walk(helper) if isinstance(n, ast.Name)}
    assert set(DEFERRED) <= used, "helper 里少了这些依赖：%s" % sorted(set(DEFERRED) - used)
    assert not (set(DEFERRED) & set(PARAMS)), "这些依赖不得变成形参（会提前绑定）"
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                assert not (isinstance(t, ast.Name) and t.id in DEFERRED), \
                    "模块顶层不许缓存 %s" % t.id


def test_块内挂起点与IO时点不变():
    helper = _fn(_tree(), HELPER)
    awaits = [ast.unparse(n.value) for s in helper.body[1:-1] for n in ast.walk(s)
              if isinstance(n, ast.Await)]
    assert len(awaits) == 1, awaits
    assert awaits[0] == "_resolve_admission_sender(source, source_id, speaker_type, " \
                        "_src_msg_sender, db)", awaits[0]
    txt = "\n".join(ast.unparse(s) for s in helper.body[1:-1])
    for banned in ("db.add(", "db.flush(", "db.commit(", "db.refresh("):
        assert banned not in txt, "块内不许出现 %s（事务时点必须留在调用方）" % banned


def test_段内局部import四件仍在helper内():
    """四件局部 import 不得上提：测试 patch 的正是 app.memory.tense / actor_shadow / agent_flags。"""
    helper = _fn(_tree(), HELPER)
    txt = "\n".join(ast.unparse(s) for s in helper.body[1:-1])
    for frag in ("from app.flags.agent_flags import AGENT_FLAGS",
                 "from app.memory.actor_shadow import trace_actor_write",
                 "from app.memory.tense import classify_tense, plan_valid_until"):
        assert frag in txt, "helper 内少了局部 import：%s" % frag
    assert txt.count("from app.flags.agent_flags import AGENT_FLAGS") == 2, "两处都在"


# ───────────────────────── 行为冻结（临时库真跑 save_memory） ─────────────────────────

class Env:
    def __init__(self):
        self.calls = []        # helper 每次调用的实参（kwargs）与返回值
        self.resolver = []     # _resolve_admission_sender 的调用次数
        self.shadows = []      # trace_actor_write 的调用次数
        self.receipts = []
        self.added = []        # db.add 的实参（落库对象）
        self.promoted = []
        self.times = []        # _now_naive / _initial_strength 的调用顺序
        self.notes = []

    @property
    def last(self):
        return self.calls[-1]

    @property
    def out(self):
        return self.calls[-1]["out"]

    @property
    def creates(self):
        return [r for r in self.receipts if r["action"] == "create"]


@pytest.fixture(autouse=True)
def clean_flags():
    """相关 flag 前后复位（本文件任何用例都不依赖本机 runtime_flags 现值）。"""
    def _reset():
        _loop.AGENT_FLAGS["memory_admission_gate"] = False
        _loop.AGENT_FLAGS["actor_semantics_shadow"] = False
        _loop.AGENT_FLAGS["review_plan_validity_extract"] = False
        _loop.AGENT_FLAGS["perception_source_tag"] = False
        _loop.AGENT_FLAGS["memory_write_receipt"] = False
        _loop.AGENT_FLAGS["write_dedup_active_only"] = False
    _reset()
    yield
    _reset()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时库（_dbclone 克隆模板库到 tmp_path）＋ 记录式替身，只观察准备阶段及其下游。"""
    e = Env()
    engine = clone_engine(tmp_path / "prepare.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b4_u1", nickname="A50 批4 合成用户"))
            db.add(AICharacter(id=101, user_id=1, name="A50 批4 合成角色"))
            await db.commit()

    asyncio.run(_seed())

    # —— 服务层四件（嵌入/向量查重/向量写入/BM25 失效），一律打桩不真推理 ——
    async def _embed(_text):
        return [0.0] * 8

    async def _no_similar(*_a, **_kw):
        return None

    async def _add(**_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    # —— 与本切口无关的外部副作用：后台任务/链/意义/事件/插件/晋升/回执 ——
    import app.memory.dedup as dd
    import app.utils.async_tasks as at
    import app.memory.chain_builder as cb
    import app.memory.meaning as meaning_mod
    import app.memory.core as core_mod
    import app.events as events_mod
    import app.plugins.registry as registry
    from app.memory import receipt as receipt_mod

    def _noop(*_a, **_kw):
        return None

    def _spawn(coro, **_kw):
        if hasattr(coro, "close"):
            coro.close()
        return None

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        e.receipts.append({"character_id": character_id, "memory_id": memory_id,
                           "action": action, "reason": reason, "detail": detail})

    def _promote(memory_id, pct, sub_type, memory_type):
        e.promoted.append((memory_id, pct, sub_type, memory_type))

    monkeypatch.setattr(dd, "_schedule_dedup", _noop)
    monkeypatch.setattr(at, "spawn_background", _spawn)
    monkeypatch.setattr(cb, "memory_chain_builder_enabled", lambda: False)
    monkeypatch.setattr(cb, "link_new_memory", _noop)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)
    monkeypatch.setattr(events_mod, "publish", _noop)
    monkeypatch.setattr(registry, "run_hook", _noop)
    monkeypatch.setattr(core_mod, "maybe_promote_core", _promote)
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _rec)

    # —— 准备阶段的观测点（都是模块属性＝既有 monkeypatch 接缝）——
    from app.memory import write as write_mod

    real_helper = write_mod._prepare_created_memory

    async def _probe(**kw):
        out = await real_helper(**kw)
        e.calls.append({"kwargs": kw, "out": out})
        return out

    monkeypatch.setattr(write_mod, "_prepare_created_memory", _probe)

    real_resolve = write_mod._resolve_admission_sender

    async def _count_resolve(*a, **kw):
        e.resolver.append((a, kw))
        return await real_resolve(*a, **kw)

    monkeypatch.setattr(write_mod, "_resolve_admission_sender", _count_resolve)

    import app.memory.actor_shadow as shadow_mod

    real_shadow = shadow_mod.trace_actor_write

    def _count_shadow(**kw):
        e.shadows.append(kw)
        return real_shadow(**kw)

    monkeypatch.setattr(shadow_mod, "trace_actor_write", _count_shadow)

    # db.add 观测（确认「准备完成后才落库」，且落的就是那个临时 Memory）
    class _TracedSession:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def add(self, obj):
            e.added.append(obj)
            return self._inner.add(obj)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    monkeypatch.setattr(svc, "async_session_factory", lambda: _TracedSession(factory()))

    # 时间／强度调用次数与顺序（helper 内部的三次 _now_naive 与两次 _initial_strength）
    real_now = write_mod._now_naive
    real_strength = write_mod._initial_strength
    real_norm = write_mod._normalize_importance

    def _now():
        e.times.append("now")
        return real_now()

    def _strength(t):
        e.times.append("strength:%s" % t)
        return real_strength(t)

    def _norm(v):
        e.times.append("norm:%s" % v)
        return real_norm(v)

    monkeypatch.setattr(write_mod, "_now_naive", _now)
    monkeypatch.setattr(write_mod, "_initial_strength", _strength)
    monkeypatch.setattr(write_mod, "_normalize_importance", _norm)

    yield e
    asyncio.run(engine.dispose())


def _rows(env):
    from app.models.memory import Memory

    async def _run():
        async with svc.async_session_factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()

    return asyncio.run(_run())


def _save(**kw):
    from app.memory.write import save_memory
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("importance", 4)
    kw.setdefault("content", BODY_USER)
    kw.setdefault("source", "chat")
    return asyncio.run(save_memory(**kw))


# ── 归属与认知状态 ───────────────────────────────────────────────────────────

@pytest.mark.slow
def test_默认speaker归用户且落库前已收口(env):
    m = _save()
    row = _rows(env)[0]
    assert m.id == row.id
    memory, pct, spk_type, spk, epi, pending, sub_before = env.out
    assert memory is row or memory.id == row.id, "返回的就是落库那同一个对象"
    assert spk_type == "user" and row.speaker_type == "user"
    assert row.speaker_id == 1
    assert spk == "user"
    assert epi == "FACT" and row.epistemic_status == "FACT"
    assert pending is False
    assert pct == 80.0 and float(row.importance) == 80.0
    assert sub_before is None
    assert env.resolver == [], "闸门关 ⇒ _resolve_admission_sender 一次都不调"
    assert env.shadows == [], "影子 flag 关 ⇒ 不埋点"


@pytest.mark.slow
def test_显式speaker优先于默认归属(env):
    _save(speaker_type="character", speaker_id=101)
    row = _rows(env)[0]
    assert row.speaker_type == "character" and row.speaker_id == 101
    assert env.out[3] == "character", "_spk 留痕跟着实际归属"


@pytest.mark.slow
def test_感知来源不被套默认归属(env):
    """感知派生条：即使没显式 speaker 也不套「默认＝用户陈述」，落成 perception 且 id 留空。"""
    _save(source="perception")
    row = _rows(env)[0]
    assert row.speaker_type == "perception"
    assert row.speaker_id is None
    assert env.out[2] == "perception"


@pytest.mark.slow
def test_epistemic缺省按来源推断(env):
    _save(source="search")
    assert _rows(env)[0].epistemic_status == "UNVERIFIED", "非白名单来源缺省 UNVERIFIED"
    _save(source="diary", content="另一条：楼下的快递还没取")
    assert _rows(env)[1].epistemic_status == "FACT", "白名单来源缺省 FACT"
    _save(content="第三条：阳台的薄荷开花了", source="life")
    assert _rows(env)[2].epistemic_status == "FACT"
    _save(content="第四条：路由器该换了", source="tool")
    assert _rows(env)[3].epistemic_status == "UNVERIFIED"


# ── 准入闸门 ────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_准入闸门开_角色发言进待核(env, monkeypatch):
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    _save(speaker_type="character", speaker_id=101, reliability=0.9)
    row = _rows(env)[0]
    assert env.resolver, "闸门开 ⇒ 要走一次归属解析"
    assert row.epistemic_status == "INFERRED"
    assert env.out[5] is True, "_pending 传给回执段"
    assert [r for r in env.receipts if r["action"] == "downgrade"], "待核必须写 downgrade 回执"
    assert env.promoted == [], "待核记忆不参与核心晋升"


@pytest.mark.slow
def test_准入闸门开_可靠度低进待核(env):
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    _save(reliability=0.2)
    row = _rows(env)[0]
    assert row.epistemic_status == "UNVERIFIED" and env.out[5] is True
    assert row.reliability_score == 0.2


@pytest.mark.slow
def test_准入解析异常回落用户且不中断(env, monkeypatch):
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    from app.memory import write as write_mod

    async def _boom(*a, **kw):
        raise RuntimeError("resolve-boom")

    monkeypatch.setattr(write_mod, "_resolve_admission_sender", _boom)
    m = _save()
    row = _rows(env)[0]
    assert m is not None, "异常不得把这条记忆丢掉"
    assert env.out[3] == "user", "解析异常回落 _spk='user'"
    assert row.epistemic_status == "FACT", "裁决照旧（用户陈述 → FACT）"


@pytest.mark.slow
def test_裁决异常回落显式epistemic且不待核(env, monkeypatch):
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    from app.memory import write as write_mod

    def _boom(*a, **kw):
        raise RuntimeError("verdict-boom")

    monkeypatch.setattr(write_mod, "admit_memory", _boom)
    m = _save(epistemic_status="PLANNED")
    row = _rows(env)[0]
    assert m is not None
    assert row.epistemic_status == "PLANNED", "裁决异常回落显式标注"
    assert env.out[5] is False, "_pending 回落 False"


# ── 影子埋点 ────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_影子埋点开_只判定不改值(env):
    _loop.AGENT_FLAGS["actor_semantics_shadow"] = True
    _save(speaker_type=None, speaker_id=None)
    row = _rows(env)[0]
    assert len(env.shadows) == 1, env.shadows
    kw = env.shadows[0]
    assert kw["actual_speaker_type"] == "user" and kw["actual_speaker_id"] == 1
    assert row.speaker_type == "user", "影子不得改写落库值"
    assert row.epistemic_status == "FACT"


@pytest.mark.slow
def test_影子埋点异常完全静默(env, monkeypatch):
    _loop.AGENT_FLAGS["actor_semantics_shadow"] = True
    import app.memory.actor_shadow as shadow_mod

    def _boom(**kw):
        raise RuntimeError("shadow-boom")

    monkeypatch.setattr(shadow_mod, "trace_actor_write", _boom)
    m = _save()
    row = _rows(env)[0]
    assert m is not None
    assert row.speaker_type == "user" and row.epistemic_status == "FACT"


# ── Memory 构造（importance / 衰减 / 派生列表） ──────────────────────────────

@pytest.mark.slow
def test_衰减起点与强度调用次数顺序(env):
    _save(memory_type="event", importance=3)
    row = _rows(env)[0]
    assert row.decay_base_at is not None and row.last_reinforce_at is not None
    assert row.next_review_at is not None and row.next_review_at > row.decay_base_at
    # 只取准备段那一段（写入前查重的 24h 合并查询本身也会调一次 _now_naive，不在本切口内）
    i = env.times.index("norm:3")
    seg = env.times[i:]
    assert seg == ["norm:3", "now", "strength:event", "now", "now", "strength:event"], seg


@pytest.mark.slow
def test_派生列表序列化成JSON(env):
    _save(derived_from_ids=[7, 9])
    row = _rows(env)[0]
    assert _json.loads(row.derived_from_ids) == [7, 9]
    _save(content="另一条：厨房的水槽通了", derived_from_ids=None)
    assert _json.loads(_rows(env)[1].derived_from_ids) == []


# ── L4 计划标注 ────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_plan标注开_改sub_type并写有效期(env):
    _loop.AGENT_FLAGS["review_plan_validity_extract"] = True
    _save(content=BODY_PLAN, memory_type="event")
    row = _rows(env)[0]
    assert row.sub_type == "plan"
    assert row.valid_to is not None
    assert env.out[6] is None, "_sub_type_before_plan 是标注前的值（回执据此判 plan_renamed）"
    gate = env.creates[0]["detail"]["provenance"]["gates"]
    assert "plan_renamed" in gate


@pytest.mark.slow
def test_plan标注开_显式sub_type不被覆盖(env):
    _loop.AGENT_FLAGS["review_plan_validity_extract"] = True
    _save(content=BODY_PLAN, sub_type="status")
    row = _rows(env)[0]
    assert row.sub_type == "status", "显式 sub_type 不覆盖"
    assert row.valid_to is None
    assert env.out[6] == "status"


@pytest.mark.slow
def test_plan标注关零行为(env):
    _save(content=BODY_PLAN)
    row = _rows(env)[0]
    assert row.sub_type is None and row.valid_to is None


@pytest.mark.slow
def test_plan标注异常静默且不改sub_type(env, monkeypatch):
    _loop.AGENT_FLAGS["review_plan_validity_extract"] = True
    import app.memory.tense as tense_mod

    def _boom(_m):
        raise RuntimeError("tense-boom")

    monkeypatch.setattr(tense_mod, "classify_tense", _boom)
    m = _save(content=BODY_PLAN)
    row = _rows(env)[0]
    assert m is not None
    assert row.sub_type is None and row.valid_to is None


# ── 接线顺序 ────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_准备完成后才db_add且落的是同一个对象(env):
    _save()
    row = _rows(env)[0]
    assert len(env.added) == 1 and env.added[0].id == row.id
    assert env.calls[0]["out"][0].id == row.id, "db.add 的正是 helper 返回的那个 Memory"
    # add 之后才是回执与 Chroma（顺序照旧）
    assert env.added[0].id == env.creates[0]["memory_id"]


@pytest.mark.slow
def test_台词先拒时准备段一次都不跑(env):
    m = _save(source="chat", content="（摸了摸她的头）别难过")
    assert m is None
    assert env.calls == [], "台词拦截在准备段之前，helper 一次都不该跑"
    assert [r for r in env.receipts if r["action"] == "reject"]
    assert _rows(env) == []


@pytest.mark.slow
def test_元信息拦截不进准备段(env):
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    m = _save(content="我是你的 Agent 助手，请按流程执行这段记忆重构")
    assert m is None
    assert env.calls == []
    assert [r for r in env.receipts if r["action"] == "reject"]


@pytest.mark.slow
def test_同证据重放经准入准备但不触发merge(env):
    first = _save(source="chat", source_id=991)
    again = _save(source="chat", source_id=991, importance=5)
    assert again.id == first.id
    assert len(env.calls) == 2
    assert not [r for r in env.receipts if r["action"] == "merge"]
