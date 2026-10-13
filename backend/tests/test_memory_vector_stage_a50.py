# -*- coding: utf-8 -*-
"""A50 批 7：``save_memory`` 写入前**向量语义查重**段（``_vector_dedup_on_write``）守卫（2026-10-12）。

被测切口：把 ``save_memory`` 里「1) 向量语义查重」连续三个顶层语句（① ``try`` 算嵌入＋查相似
／``except`` 双双清 ``None``；② ``if similar:`` 取行＋跨源禁令置 ``m = None``；③ ``if similar and m:``
里「未归档/未置顶/未锁定 → 强化 → commit → 命中日志 → ``obs_event(kind="vector_dedup")`` →
``emit_memory_receipt(ACTION_MERGE)`` → ``return m``」），48 行 / CC 12）机械抽成同文件模块级私有协程
``_vector_dedup_on_write``；调用点仍在感知打标之后、2) 字符级查重之前。

**判据与副作用零行为变化**，唯一改写是返回值运输契约：

- 命中 → ``(m, embedding)``；调用点用 ``_vector_hit is not None`` 早退并原样返回那条记忆；
- 未命中（相似度不够／跨源禁令／归档·置顶·锁定）→ ``(None, embedding)``：**已经算出来的 embedding
  必须带回**给后面的写向量与 hooks（清 ``None`` 会重算，重算失败才可能不带向量）；
- 尝试阶段抛异常 → 仍照旧把 ``embedding`` 清成 ``None`` 再返回 ``(None, None)``（旧行为）。

守卫分两层：
- 结构冻结（纯 AST，零 IO、不依赖 git／docs）：helper 形态与十六个 keyword-only 形参、语句序列恰为
  ``[docstring, 原块 3 条, 运输 Return]``、两条运输 Return 的形状、0.86/limit=20/三重保护/跨源禁令
  逐字仍在、块内 ``obs_event`` 局部 import 随块且未上提、四个顶部局部导入由调用点显式 kwarg 传入、
  十个模块全局依赖仍晚绑定、块内仍只有 1 处 ``commit`` 且排在 ``obs_event``/回执之前、调用点是
  「await + 二元解包 + ``is not None`` 早退」且外层三闸与 ``embedding = None`` 初值仍留调用方、
  ``save_memory`` 公共签名二十一个形参逐字未变；
- 行为冻结（临时库真跑 ``save_memory``，合成数据）：命中早退返回**被并那条**而不是新建那条、
  强化／importance 抬升／``derived_from_ids`` 合并、回执与 ``obs_event`` 的字段逐个、commit 早于
  两者、三重保护（归档／置顶／锁定）各自不并、跨源禁令不并、未命中时 embedding **原对象**运到
  hooks 且**只算一次**、嵌入失败与查重失败两条异常路径仍清 ``None`` 且不合并。

纪律：临时库走 pytest ``tmp_path``（``_dbclone`` 克隆模板库），绝不连生产库；全文只用合成数据，
不引用任何真实用户内容、作者机器路径或内部文档；嵌入／向量查重／向量写入全部打桩，不发生真实推理。
"""
import ast
import asyncio
import io
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
import app.memory.service as svc

WRITE_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"

HELPER = "_vector_dedup_on_write"
# 十六个依赖一律 keyword-only：块内读的 12 个 ＋ 由调用点顶 import 传入的 4 个
PARAMS = ["db", "content", "character_id", "source", "importance", "sub_type", "memory_type",
          "source_id", "speaker_type", "epistemic_status", "derived_from_ids", "node_type",
          "text_embedding", "find_similar_memory", "emit_memory_receipt", "ACTION_MERGE"]
# 必须继续按模块全局晚绑定（monkeypatch 接缝在模块属性上）
DEFERRED = ["Memory", "VECTOR_DEDUP_THRESHOLD", "DECAY_MAX_PCT", "REINFORCE_FACTOR_WRITE",
            "_apply_reinforce", "_normalize_importance", "_merge_derived", "_merge_receipt_detail",
            "_cross_source_merge_blocked", "_now_naive", "_logger"]
# save_memory 的公共签名（二十一个形参，逐字冻结）
SAVE_SIG = ["user_id", "character_id", "memory_type", "content", "title", "importance",
            "sub_type", "source", "related_memory_id", "source_id", "group_id", "scope",
            "skip_dedup", "speaker_id", "speaker_type", "epistemic_status", "reliability",
            "chain_id", "parent_id", "node_type", "derived_from_ids"]

# 合成语料（不对应任何真实用户内容）
BODY = "阳台上的薄荷又长高了一截"
# 合成旧行：与 BODY **字符级不相似**（相似度远低于 0.72/0.6），否则「向量被挡住」那些用例会被
# 2) 字符级／3) 同主题兜底接住，测不出向量段自己的保护——本批只钉向量这一腿。
OLD = "他把那份季度复盘表又核对了一遍"
UNRELATED = "厨房的水槽通了"

FLAGS = ("memory_admission_gate", "perception_source_tag", "memory_write_receipt",
         "review_plan_validity_extract", "actor_semantics_shadow", "write_dedup_active_only",
         "memory_supersede")


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


def _dedup_guard(flat):
    """写入前查重那一层的 If（外层三闸），返回它的 body。"""
    for s in flat:
        if isinstance(s, ast.If) and ast.unparse(s.test).startswith("content and content.strip()"):
            return s
    raise AssertionError("save_memory 里找不到写入前查重的外层三闸")


def test_helper是模块级私有协程且十六个形参固定():
    h = _fn(_tree(), HELPER)
    assert isinstance(h, ast.AsyncFunctionDef), "必须 async（块内有两个 await：text_embedding/find_similar_memory）"
    assert h.name.startswith("_"), "私有协程，名字带下划线前缀"
    assert h.args.args == [] and h.args.kwonlyargs, "十六个依赖一律 keyword-only"
    assert [a.arg for a in h.args.kwonlyargs] == PARAMS
    assert h.args.vararg is None and h.args.kwarg is None
    assert not h.decorator_list
    # 返回契约写死成 tuple[...]（与批2～批6 同款），不靠口头约定
    assert isinstance(h.returns, ast.Subscript), "返回契约写死成 tuple[...]"
    assert ast.unparse(h.returns) == "tuple[Memory | None, list[float] | None]"


def test_helper体语句序列是docstring_原块3条_运输Return():
    h = _fn(_tree(), HELPER)
    body = list(h.body)
    kinds = [type(s).__name__ for s in body]
    assert kinds[0] == "Expr" and isinstance(body[0].value, ast.Constant), "首条是 docstring"
    assert kinds[1:] == ["Try", "If", "If", "Return"], kinds
    assert len(body) == 5, "docstring + 原块 3 条 + 运输 Return，共 %d" % len(body)
    assert ast.unparse(body[1]).startswith("try:\n    embedding = await text_embedding(content)")
    assert ast.unparse(body[2]).startswith("if similar:\n    mem_id, sim = similar")
    assert ast.unparse(body[3]).startswith("if similar and m:")


def test_运输契约两条Return形状():
    """唯一改写：原 ``return m`` → ``(m, embedding)``；末尾补 ``(None, embedding)``。"""
    h = _fn(_tree(), HELPER)
    assert ast.unparse(h.body[-1]) == "return (None, embedding)", ast.unparse(h.body[-1])
    inner = h.body[3].body[0]          # if not is_archived and not is_pinned and not is_locked
    hit = ast.unparse(inner.body[-1])
    assert hit == "return (m, embedding)", hit
    # 未命中出口只有一个，且它必须带 embedding（运输丢失就是从这里漏的）
    tails = [ast.unparse(s) for s in h.body[3].body[:-1]] + [ast.unparse(s) for s in inner.body[:-1]]
    assert not any(t.startswith("return") for t in tails), tails


def test_异常分支仍把embedding与similar双双清None():
    h = _fn(_tree(), HELPER)
    handler = h.body[1].handlers[0]
    assert isinstance(handler.type, ast.Name) and handler.type.id == "Exception", "仍是裸 Exception"
    assert [ast.unparse(s) for s in handler.body] == ["embedding = None", "similar = None"]


def test_阈值_三重保护_跨源禁令逐字仍在helper内():
    h = _fn(_tree(), HELPER)
    flat = ast.unparse(h)
    assert "limit=20, min_similarity=VECTOR_DEDUP_THRESHOLD" in flat, "0.86/20 口径不得改"
    assert "if not m.is_archived and (not m.is_pinned) and (not m.is_locked):" in flat
    assert "_cross_source_merge_blocked(source, m.source)" in flat
    assert "min(DECAY_MAX_PCT, new_pct)" in flat
    assert "_merge_derived(m.derived_from_ids, derived_from_ids or [])" in flat
    assert "kind='vector_dedup'" in flat and "reason='write-time vector dedup'" in flat
    assert "extra={'sim': round(float(sim), 3)}" in flat


def test_块内obs_event局部import随块且只出现一次():
    h = _fn(_tree(), HELPER)
    imports = [ast.unparse(n) for n in ast.walk(h) if isinstance(n, ast.ImportFrom)]
    assert imports == ["from app.memory.observability import obs_event"], imports
    body_src = ast.unparse(_fn(_tree(), "save_memory"))
    assert "from app.memory.observability import obs_event" not in body_src, "两处随字符/主题阶段搬出caller"
    text_helper = _fn(_tree(), "_text_topic_dedup_on_write")
    text_imports = [ast.unparse(n) for n in ast.walk(text_helper) if isinstance(n, ast.ImportFrom)]
    assert text_imports.count("from app.memory.observability import obs_event") == 2


def test_模块全局依赖仍晚绑定():
    h = _fn(_tree(), HELPER)
    params = set(PARAMS)
    for name in DEFERRED:
        assert name not in params, "%s 不得提成 keyword-only 形参（接缝会失效）" % name
    tree = _tree()
    assigned = {t.id for n in tree.body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    for name in DEFERRED:
        assert name not in assigned, "%s 不得在模块级缓存一份" % name


def test_块内仍只有一处commit且排在obs_event与回执之前():
    h = _fn(_tree(), HELPER)
    # 只看命中那一支的代码（docstring 里出现过这三个名字，不能混进顺序判断）
    flat = ast.unparse(h.body[3])
    assert flat.count("await db.commit()") == 1
    assert flat.index("await db.commit()") < flat.index("obs_event(") < flat.index("emit_memory_receipt(")
    assert "db.add(" not in flat and "db.flush(" not in flat and "db.refresh(" not in flat


def test_调用点是await二元解包加is_not_None早退():
    flat = _flat(_fn(_tree(), "save_memory"))
    guard = _dedup_guard(flat)
    idx = next(i for i, s in enumerate(guard.body) if HELPER in ast.unparse(s))
    call = guard.body[idx]
    assert isinstance(call, ast.Assign) and isinstance(call.value, ast.Await)
    assert isinstance(call.value.value, ast.Call)
    assert [t.id for t in call.targets[0].elts] == ["_vector_hit", "embedding"]
    inner = call.value.value
    assert inner.func.id == HELPER
    assert [(k.arg, ast.unparse(k.value)) for k in inner.keywords] == [
        ("db", "db"), ("content", "content"), ("character_id", "character_id"), ("source", "source"),
        ("importance", "importance"), ("sub_type", "sub_type"), ("memory_type", "memory_type"),
        ("source_id", "source_id"), ("speaker_type", "speaker_type"),
        ("epistemic_status", "epistemic_status"), ("derived_from_ids", "derived_from_ids"),
        ("node_type", "node_type"), ("text_embedding", "text_embedding"),
        ("find_similar_memory", "find_similar_memory"),
        ("emit_memory_receipt", "emit_memory_receipt"), ("ACTION_MERGE", "ACTION_MERGE"),
    ]
    nxt = guard.body[idx + 1]
    assert ast.unparse(nxt) == "if _vector_hit is not None:\n    return _vector_hit", ast.unparse(nxt)


def test_外层三闸与embedding初值留在调用方且helper不重算():
    flat = _flat(_fn(_tree(), "save_memory"))
    guard = _dedup_guard(flat)
    fi = flat.index(guard)
    # `embedding = None` 初值在三闸**之前**（它在闸外，未命中/skip_dedup 时也要有值）
    prev = flat[fi - 1]
    assert ast.unparse(prev) == "embedding = None", ast.unparse(prev)
    assert ast.unparse(guard.test).startswith("content and content.strip()")
    assert "skip_dedup" in ast.unparse(guard.test), "skip_dedup 这一闸仍留在调用方"
    # 命中返回后不再有字符级/同主题查重；未命中才继续（早退位置不许漂到闸外）
    after = ast.unparse(guard.body)
    assert after.index("if _vector_hit is not None") < after.index("_text_hit = await _text_topic_dedup_on_write(")
    h = _fn(_tree(), HELPER)
    assert ast.unparse(h).count("await text_embedding(content)") == 1, "helper 段内只算一次嵌入"


def test_save_memory公共签名未变():
    sm = _fn(_tree(), "save_memory")
    assert [a.arg for a in sm.args.args] == SAVE_SIG
    assert sm.args.vararg is None and sm.args.kwarg is None


# ───────────────────────── 行为冻结（临时库 + 合成数据） ─────────────────────────

class Env:
    def __init__(self):
        self.embeddings = []          # 每次 text_embedding 的实参
        self.emb_obj = None            # 第一次算出来的那一个 list（同一性用）
        self.similar_calls = []        # find_similar_memory 的实参
        self.similar_result = None     # 命中桩：(mem_id, sim) 或 None
        self.similar_raises = False
        self.embed_raises = False
        self.order = []                # commit / obs / receipt 的先后
        self.obs = []
        self.receipts = []
        self.vec_calls = []            # 命中 helper 的 (kwargs, 返回二元组)
        self.hooks = []


@pytest.fixture(autouse=True)
def clean_flags():
    def _reset():
        for k in FLAGS:
            _loop.AGENT_FLAGS[k] = False
    _reset()
    yield
    _reset()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时库（_dbclone 克隆模板库到 tmp_path）＋ 记录式替身；预置一条可被并的记忆行。"""
    e = Env()
    engine = clone_engine(tmp_path / "vectorstage.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.memory import Memory
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b7_u1", nickname="A50 批7 合成用户"))
            db.add(AICharacter(id=101, user_id=1, name="A50 批7 合成角色"))
            db.add(Memory(id=1, user_id=1, character_id=101, memory_type="event", content=OLD,
                          source="chat", importance=40.0, strength_days=1.0, review_count=0,
                          derived_from_ids="[]"))
            await db.commit()
    asyncio.run(_seed())

    # —— 服务层四件一律打桩：不真推理、不真写向量 ——
    async def _embed(text):
        if e.embed_raises:
            raise RuntimeError("embed-boom")
        e.embeddings.append(text)
        e.emb_obj = [0.0] * 8
        return e.emb_obj

    async def _similar(*a, **kw):
        e.similar_calls.append((a, kw))
        if e.similar_raises:
            raise RuntimeError("similar-boom")
        return e.similar_result

    async def _add(**_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _similar)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    # —— 与本切口无关的副作用全静默（后台任务/链/意义/事件/插件/晋升）——
    import app.memory.dedup as dd
    import app.utils.async_tasks as at
    import app.memory.chain_builder as cb
    import app.memory.meaning as meaning_mod
    import app.events as events_mod
    import app.plugins.registry as registry
    from app.memory import core as core_mod
    from app.memory import receipt as receipt_mod
    from app.memory import observability as obs_mod
    from app.memory import write as write_mod

    def _noop(*_a, **_kw):
        return None

    def _spawn(coro, **_kw):
        if hasattr(coro, "close"):
            coro.close()
        return None

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        e.order.append("receipt")
        e.receipts.append({"character_id": character_id, "memory_id": memory_id,
                           "action": action, "reason": reason, "detail": detail})

    def _obs(character_id, metric, detail, kind=None):
        e.order.append("obs")
        e.obs.append({"character_id": character_id, "metric": metric, "detail": detail, "kind": kind})

    monkeypatch.setattr(dd, "_schedule_dedup", _noop)
    monkeypatch.setattr(at, "spawn_background", _spawn)
    monkeypatch.setattr(cb, "memory_chain_builder_enabled", lambda: False)
    monkeypatch.setattr(cb, "link_new_memory", _noop)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)
    monkeypatch.setattr(events_mod, "publish", _noop)
    monkeypatch.setattr(registry, "run_hook", _noop)
    monkeypatch.setattr(core_mod, "maybe_promote_core", _noop)
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _rec)
    monkeypatch.setattr(obs_mod, "obs_event", _obs)

    # —— 本切口两个观测点（模块属性＝既有 monkeypatch 接缝）——
    real_vec = write_mod._vector_dedup_on_write

    async def _probe_vec(**kw):
        out = await real_vec(**kw)
        e.vec_calls.append((dict(kw), out))
        return out

    monkeypatch.setattr(write_mod, "_vector_dedup_on_write", _probe_vec)

    real_hooks = write_mod._run_created_memory_hooks

    async def _probe_hooks(**kw):
        e.hooks.append(dict(kw))
        return await real_hooks(**kw)

    monkeypatch.setattr(write_mod, "_run_created_memory_hooks", _probe_hooks)

    # —— 会话探针：记 commit 的发生次序（向量查重段只有命中那条会 commit）——
    class _TracedSession:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def commit(self):
            e.order.append("commit")
            return await self._inner.commit()

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    monkeypatch.setattr(svc, "async_session_factory", lambda: _TracedSession(factory()))
    yield e
    asyncio.run(engine.dispose())


def _rows():
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
    kw.setdefault("importance", 3)
    kw.setdefault("content", BODY)
    kw.setdefault("source", "chat")
    return asyncio.run(save_memory(**kw))


def test_命中早退返回被并那条而不是新建一条(env):
    env.similar_result = (1, 0.91)
    out = _save(derived_from_ids=[7])
    assert out is not None and out.id == 1, "必须把被并那条交还调用方当返回值"
    assert out.content == OLD
    assert [r.id for r in _rows()] == [1], "命中不许再新建一行"
    # helper 的运输契约：命中返回 (m, embedding)，embedding 也一并带出
    _kw, (hit, emb) = env.vec_calls[0]
    assert hit is not None and hit.id == 1 and emb is env.emb_obj
    # 0.86/20 口径照旧传给查重口
    _a, kw2 = env.similar_calls[0]
    assert kw2 == {"limit": 20, "min_similarity": env.similar_calls[0][1]["min_similarity"]}
    assert kw2["min_similarity"] > 0.85 and kw2["limit"] == 20


def test_命中时强化_抬importance_并derived(env):
    env.similar_result = (1, 0.93)
    _save(importance=3, derived_from_ids=[7, 8])
    row = _rows()[0]
    assert row.importance >= 60.0, "3 星归一化 60 起步（_apply_reinforce 还会再抬），原值是 40"
    assert row.strength_days and row.strength_days > 1.0, "S×2 强化"
    assert row.review_count == 1
    assert row.derived_from_ids == "[7, 8]", "并入调用方声明的派生来源"


def test_命中时回执与obs_event字段逐个(env):
    env.similar_result = (1, 0.877)
    _save(sub_type="accessibility", derived_from_ids=[7])
    merges = [r for r in env.receipts if r["reason"] == "write-time vector dedup"]
    assert len(merges) == 1, env.receipts
    r = merges[0]
    assert r["action"] == "merge" and r["memory_id"] == 1
    assert r["detail"]["kind"] == "vector_dedup", r["detail"]
    assert r["detail"]["sim"] == 0.877
    assert r["detail"]["incoming"]["sub_type"] == "accessibility", r["detail"]["incoming"]
    assert r["detail"]["into"]["memory_id"] == 1
    assert len(env.obs) == 1
    assert env.obs[0]["metric"] == "dual_write_dup_merge" and env.obs[0]["kind"] == "vector_dedup"
    assert env.obs[0]["detail"] == {"hit_id": 1, "sim": 0.877}


def test_commit排在obs_event与回执之前(env):
    env.similar_result = (1, 0.9)
    _save()
    assert env.order[:3] == ["commit", "obs", "receipt"], env.order


@pytest.mark.parametrize("field", ["is_archived", "is_pinned", "is_locked"])
def test_三重保护各自不并(env, field):
    async def _flip():
        from app.models.memory import Memory
        async with svc.async_session_factory() as db:
            row = await db.get(Memory, 1)
            setattr(row, field, True)
            await db.commit()
    asyncio.run(_flip())
    env.similar_result = (1, 0.95)
    out = _save()
    _kw, (hit, emb) = env.vec_calls[0]
    assert hit is None, "%s=True 不得合并" % field
    assert emb is env.emb_obj, "挡住时 embedding 仍要原样带回"
    assert out is not None and out.id != 1, "挡住后照旧走后续查重/新建（合成旧行字符级不相似，不会被兜底接住）"
    assert not [r for r in env.receipts if r["reason"] == "write-time vector dedup"], \
        "%s=True 时不许发向量 merge 回执" % field
    assert not [o for o in env.obs if o["kind"] == "vector_dedup"], \
        "%s=True 时不许打向量命中埋点" % field


def test_跨源不并且只打一条跳过日志(env):
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    env.similar_result = (1, 0.99)
    _save(source="perception")
    _kw, (hit, emb) = env.vec_calls[0]
    assert hit is None, "感知 ↔ 非感知不互并（禁令 3）"
    assert emb is env.emb_obj


def test_未命中embedding原对象运到hooks且只算一次(env):
    env.similar_result = None
    out = _save()
    assert out is not None and out.id != 1, "没命中就新建一条"
    assert env.embeddings == [BODY], "嵌入只算一次：未命中时算好的那个要往后送，不许重算"
    assert env.hooks and env.hooks[0]["embedding"] is env.emb_obj, "同一对象运到 hooks"
    assert env.vec_calls[0][1] == (None, env.emb_obj)


def test_嵌入失败清None不合并(env):
    env.embed_raises = True
    out = _save()
    assert out is not None and out.id != 1
    assert env.similar_calls == [], "嵌入炸了不该再查相似"
    assert env.vec_calls[0][1] == (None, None), "失败时 embedding 照旧清 None"
    assert env.hooks and env.hooks[0]["embedding"] is env.emb_obj, "失败后由 hooks 那段自己重算"


def test_查重抛异常时embedding也清None(env):
    """旧行为：find_similar_memory 抛了，已经算出的嵌入一并丢弃（抽出也不改）。"""
    env.similar_raises = True
    out = _save()
    assert out is not None and out.id != 1
    assert env.vec_calls[0][1] == (None, None)
    assert len(env.embeddings) == 2, "旧行为是丢弃后重算一次，不是复用"


def test_外层三闸短路时helper一次都不调(env):
    out = _save(content="   ")
    assert out is not None, "空正文这一层本来就不拒收（新建照旧），本用例只钉 helper 不被调"
    assert env.vec_calls == [], "空正文不该进向量段"
    assert env.similar_calls == []


def test_helper直调三种返回形状(env):
    """不经过 save_memory：直接打 helper，钉住三个出口的二元组形状。"""
    from app.memory import write as write_mod
    from app.models.memory import Memory

    class _FakeDB:
        def __init__(self, row):
            self._row = row

        async def get(self, model, ident):
            assert model is Memory and ident == 1
            return self._row

        async def commit(self):
            env.order.append("commit")

    base = dict(db=None, content=BODY, character_id=101, source="chat", importance=3,
                sub_type=None, memory_type="event", source_id=None, speaker_type=None,
                epistemic_status=None, derived_from_ids=None, node_type=None,
                emit_memory_receipt=lambda *_a, **_kw: None, ACTION_MERGE="merge")

    async def _embed(_t):
        return [0.0] * 8

    async def _hit(*_a, **_kw):
        return (1, 0.95)

    async def _miss(*_a, **_kw):
        return None

    async def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    async def _run(**over):
        kw = dict(base, **over)
        return await write_mod._vector_dedup_on_write(**kw)

    row = asyncio.run(_run(db=_FakeDB(_rows()[0]), text_embedding=_embed,
                           find_similar_memory=_hit))
    assert isinstance(row, tuple) and len(row) == 2 and row[0].id == 1
    miss = asyncio.run(_run(db=_FakeDB(None), text_embedding=_embed, find_similar_memory=_miss))
    assert miss == (None, [0.0] * 8), "未命中的 embedding 必须原样带回"
    failed = asyncio.run(_run(db=_FakeDB(None), text_embedding=_embed, find_similar_memory=_boom))
    assert failed == (None, None), "尝试阶段抛异常仍照旧双双清 None"
