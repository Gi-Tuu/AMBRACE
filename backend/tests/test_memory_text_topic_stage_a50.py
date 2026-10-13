# -*- coding: utf-8 -*-
"""A50 批 8：``save_memory`` 写入前**字符级查重 ＋ 24h 同主题合并**段（``_text_topic_dedup_on_write``）守卫（2026-10-12）。

被测切口：把 ``save_memory`` 里「2) 字符级查重（``>= 0.72``／最近 30 条）」与「3) 24h 同主题合并
（严格 ``> 0.6``／同 memory_type／24h 内 50 条）」两段共 8 条连续语句（93 行 / CC 22）整体机械抽成
同文件模块级私有协程 ``_text_topic_dedup_on_write``；调用点仍在向量语义查重（批 7 那段）早退之后。

**判据与副作用零行为变化**，唯一改写是返回值运输契约：命中返回被并那条原 ``Memory``、未命中返回
``None``，调用点 ``is not None`` 早退。这两段既不读也不改 ``embedding``，故**不运输它**。

守卫分两层：
- 结构冻结（纯 AST，零 IO、不依赖 git／docs）：helper 形态与十五个 keyword-only 形参、语句序列恰为
  ``[docstring, 原块 8 条, 运输 Return]``、两条原 ``return m``／``return _m`` 逐字仍在、阈值
  ``>= 0.72`` 与严格 ``> 0.6`` 逐字仍在、两条 SQL 的 ``is_archived``／``order_by``／``limit(30)``／
  ``limit(50)``／24h 边界与**两处状态子句差异**（字符档现行闸 vs 主题档怀旧面）、``SequenceMatcher``
  局部导入位置照旧、块内两处 ``obs_event`` 局部 import 未上提、15 个顶部局部导入依赖由调用点显式
  kwarg 传入、模块全局仍晚绑定、每档恰 1 处 ``commit`` 且排在「日志→obs→回执」之前、两段**无 try**、
  调用点是「await + ``is not None`` 早退」且外层三闸与 ``embedding = None`` 初值仍留调用方、
  ``save_memory`` 公共签名二十一个形参逐字未变；
- 行为冻结（临时库真跑 ``save_memory``，合成数据）：字符命中优先于主题命中、主题命中返回被并那条、
  两档各自的置顶／锁定保护、归档行被 SQL 挡掉、跨源禁令（感知 ↔ 非感知）不并、
  **现行闸差异**（同一批 stale 行，闸开＝字符档看不见、主题档看得见；闸关＝字符档能并）、
  阈值边界（比例恰为 0.72 命中、恰为 0.6 不命中）、24h 边界（25 小时前不并、1 小时前并）、
  两条查询的 ``ORDER BY``／``LIMIT``（从真实 SQL 文本验）、命中早退不进入新建路径、
  回执抛异常照旧向上抛且 commit 已发生。

**变异自检（只在本测试进程内做，绝不碰工作区字节）**：两发 —— ① 把模块属性
``_cross_source_merge_blocked`` 猴补成恒 ``False``（＝撤掉跨源禁令）；② 在内存里编译一份
``> 0.6`` → ``>= 0.6`` 的 write.py 副本（``exec`` 到临时模块，工作区文件不动）。两发都必须让上面
对应的行为守卫判红，即证明这些守卫真的有牙。

纪律：临时库走 pytest ``tmp_path``（``_dbclone`` 克隆模板库），绝不连生产库；全文只用合成数据，
不引用任何真实用户内容、作者机器路径或内部文档；嵌入／向量查重／向量写入全部打桩，不发生真实推理。
"""
import ast
import asyncio
import io
import os
import types
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
import app.memory.service as svc

WRITE_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"

HELPER = "_text_topic_dedup_on_write"
# 十五个依赖一律 keyword-only：块内读的 12 个 ＋ 由调用点顶 import 传入的 3 个（timedelta/回执对）
PARAMS = ["db", "content", "character_id", "memory_type", "source", "importance", "sub_type",
          "source_id", "speaker_type", "epistemic_status", "derived_from_ids", "node_type",
          "timedelta", "emit_memory_receipt", "ACTION_MERGE"]
# 必须继续按模块全局晚绑定（monkeypatch 接缝在模块属性上）；提成默认参数就会绕开接缝
DEFERRED = ["select", "Memory", "current_facts_status_clause", "_write_dedup_active_only_on",
            "_retrievable_status_clause", "_cross_source_merge_blocked", "_normalize_importance",
            "DECAY_MAX_PCT", "REINFORCE_FACTOR_WRITE", "_apply_reinforce", "_merge_derived",
            "_merge_receipt_detail", "_now_naive", "_logger"]
# save_memory 的公共签名（二十一个形参，逐字冻结）
SAVE_SIG = ["user_id", "character_id", "memory_type", "content", "title", "importance",
            "sub_type", "source", "related_memory_id", "source_id", "group_id", "scope",
            "skip_dedup", "speaker_id", "speaker_type", "epistemic_status", "reliability",
            "chain_id", "parent_id", "node_type", "derived_from_ids"]

# 合成语料（不对应任何真实用户内容）；相似度均已用 difflib 实测
BODY = "阳台上的薄荷又长高了一截"                 # 新写入正文
OLD_CHAR = "阳台上的薄荷又长高了一大截"              # 与 BODY 0.96 → 字符档命中
OLD_TOPIC = "阳台薄荷长高了一大截今天浇水了"          # 与 BODY 0.6667 → 字符档不中、主题档中
UNRELATED = "厨房的水槽彻底修好了当天再没漏过水"       # 与 BODY 远低于 0.6 → 两档都不中
# 阈值边界：共享前缀 + 两侧互不相同的填充 ⇒ 比例**恰好** 0.72 / 0.6（2*M/总长，M 与总长都可手算）
EXACT_072_A = "甲" * 36 + "x" * 14                 # 2*36/100 = 0.72（字符档 >= 0.72 ⇒ 命中）
EXACT_072_B = "甲" * 36 + "y" * 14
EXACT_06_A = "乙" * 30 + "x" * 20                  # 2*30/100 = 0.6（主题档严格 > 0.6 ⇒ 不命中）
EXACT_06_B = "乙" * 30 + "y" * 20

FLAGS = ("memory_admission_gate", "perception_source_tag", "memory_write_receipt",
         "review_plan_validity_extract", "actor_semantics_shadow", "write_dedup_active_only",
         "memory_supersede", "current_facts_active_only")


# ───────────────────────── 结构冻结（纯 AST，零 IO） ─────────────────────────

def _tree():
    return ast.parse(io.open(WRITE_PY, encoding="utf-8").read())


def _fn(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("write.py 里找不到 %s" % name)


def _flat(fn_):
    """摊平 save_memory 的 async with 一层，让调用点与原块在同一层比。"""
    out = []
    for s in fn_.body:
        if isinstance(s, ast.AsyncWith):
            out.extend(s.body)
        else:
            out.append(s)
    return out


def _guard(flat):
    """写入前查重那一层的 If（外层三闸），返回它本身。"""
    for s in flat:
        if isinstance(s, ast.If) and ast.unparse(s.test).startswith("content and content.strip()"):
            return s
    raise AssertionError("save_memory 里找不到写入前查重的外层三闸")


def _src_lines():
    return io.open(WRITE_PY, encoding="utf-8").read().split("\n")


COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)


def _ordered(node):
    """按源码顺序摊平某个节点内的**简单语句**（跳过复合语句本身，只取其叶子），用来验先后次序。"""
    leaves = [s for s in ast.walk(node)
              if isinstance(s, ast.stmt) and not isinstance(s, COMPOUND)]
    return sorted(leaves, key=lambda s: (s.lineno, s.col_offset))


def test_helper是模块级私有协程且十五个形参固定():
    h = _fn(_tree(), HELPER)
    assert isinstance(h, ast.AsyncFunctionDef), "必须 async（块内两个 await：两次 db.execute）"
    assert h.name.startswith("_"), "私有协程，名字带下划线前缀"
    assert h.args.args == [] and h.args.kwonlyargs, "十五个依赖一律 keyword-only"
    assert [a.arg for a in h.args.kwonlyargs] == PARAMS
    assert h.args.vararg is None and h.args.kwarg is None
    assert not h.decorator_list
    assert isinstance(h.returns, ast.BinOp), "返回契约写死成 Memory | None（与批2～批7 同款，不靠口头约定）"
    assert ast.unparse(h.returns) == "Memory | None"


def test_helper体语句序列是docstring_原块8条_运输Return():
    h = _fn(_tree(), HELPER)
    body = list(h.body)
    kinds = [type(s).__name__ for s in body]
    assert kinds[0] == "Expr" and isinstance(body[0].value, ast.Constant), "首条是 docstring"
    assert kinds[1:] == ["Assign", "ImportFrom", "Assign", "Assign", "Assign",
                         "For", "Assign", "For", "Return"], kinds
    assert len(body) == 10, "docstring + 原块 8 条 + 运输 Return，共 %d" % len(body)
    # 首条必须是「字符档状态子句」赋值，次条是随块搬过来的 difflib 局部导入（位置照旧）
    assert ast.unparse(body[1]).startswith("_dedup_clause = current_facts_status_clause() if ")
    assert ast.unparse(body[2]) == "from difflib import SequenceMatcher"


def test_两条原命中Return逐字保留且未命中出口唯一():
    h = _fn(_tree(), HELPER)
    assert ast.unparse(h.body[-1]) == "return None", ast.unparse(h.body[-1])
    char_rets = [ast.unparse(s) for s in ast.walk(h.body[6]) if isinstance(s, ast.Return)]
    topic_rets = [ast.unparse(s) for s in ast.walk(h.body[8]) if isinstance(s, ast.Return)]
    assert char_rets == ["return m"], char_rets
    assert topic_rets == ["return _m"], "主题档那条必须逐字保留（含 _m 旧名，不许顺手改名）"


def test_阈值仍是大于等于072与严格大于06():
    h = _fn(_tree(), HELPER)
    char_if = [s for s in ast.walk(h.body[6])
               if isinstance(s, ast.If) and "SequenceMatcher" in ast.unparse(s.test)][0]
    topic_if = [s for s in ast.walk(h.body[8])
                if isinstance(s, ast.If) and "SequenceMatcher" in ast.unparse(s.test)][0]
    assert ast.unparse(char_if.test) == "SequenceMatcher(None, a, b).ratio() >= 0.72", ast.unparse(char_if.test)
    assert ast.unparse(topic_if.test) == "SequenceMatcher(None, _a, b).ratio() > 0.6", ast.unparse(topic_if.test)


def test_两条查询的过滤_order_limit_照旧():
    h = _fn(_tree(), HELPER)
    char_q = ast.unparse(h.body[3])          # recent_result = await db.execute(...)
    topic_q = ast.unparse(h.body[7])         # merge_rows = (await db.execute(...)).scalars().all()
    assert ".order_by(Memory.created_at.desc())" in char_q
    assert ".limit(30)" in char_q
    assert "Memory.is_archived == False" in char_q and "_dedup_clause" in char_q
    assert "memory_type" not in char_q, "字符档不按 memory_type 过滤（原样）"
    assert "created_at >=" not in char_q, "字符档没有 24h 窗口（原样）"
    assert ".order_by(Memory.created_at.desc())" in topic_q
    assert ".limit(50)" in topic_q
    assert "Memory.is_archived == False" in topic_q and "Memory.memory_type == memory_type" in topic_q
    assert "Memory.created_at >= _now_naive() - timedelta(hours=24)" in topic_q
    # 状态子句在**调用点已算好**：字符档用变量 `_dedup_clause`，主题档直接写怀旧面函数（原样）
    assert "current_facts_status_clause" not in char_q, "字符档的闸门只许在 _dedup_clause 那一行出现一次"
    assert "current_facts_status_clause" not in topic_q


def test_字符档用_dedup_clause主题档恒用_retrievable_clause():
    """两处状态子句的**差异**照旧保留（不许"顺手统一"）：字符档受现行闸门控，主题档恒怀旧面。"""
    h = _fn(_tree(), HELPER)
    assert ast.unparse(h.body[1]) == (
        "_dedup_clause = current_facts_status_clause() if _write_dedup_active_only_on() "
        "else _retrievable_status_clause()")
    topic_q = ast.unparse(h.body[7])
    assert "_retrievable_status_clause()" in topic_q
    assert "current_facts_status_clause" not in topic_q, "主题档不得改成现行闸（原样保留差异）"
    helper_src = "\n".join(_src_lines()[_fn(_tree(), HELPER).lineno - 1:_fn(_tree(), HELPER).end_lineno])
    assert "#70-C" in helper_src, "行内注释随块"


def test_顶部三个局部导入由调用点传入_其余十四项仍晚绑定():
    h = _fn(_tree(), HELPER)
    names = [a.arg for a in h.args.kwonlyargs]
    for late in DEFERRED:
        assert late not in names, "%s 必须继续按模块全局晚绑定，不许提成形参" % late
    # 顶部局部导入（timedelta / 回执对）**没有**被提成模块级 import：仍只在 save_memory 顶局部 import
    tree = _tree()
    top_imported = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                top_imported.add(a.asname or a.name.split(".")[0])
    for nm in ("timedelta", "emit_memory_receipt", "ACTION_MERGE"):
        assert nm not in top_imported, "%s 不许上提成模块级 import（会改依赖解析时点）" % nm
    # helper 体里 SequenceMatcher 仍是局部名（不是模块级导入）
    assert "_dedup_clause" and isinstance(h.body[2], ast.ImportFrom)


def test_块内两处obs_event局部import未上提且未合并():
    h = _fn(_tree(), HELPER)
    imports = [s for s in ast.walk(h)
               if isinstance(s, ast.ImportFrom) and s.module == "app.memory.observability"]
    assert len(imports) == 2, "两处 obs_event 局部 import 必须各自保留（不许上提/合并）"
    # 两处都必须待在各自命中分支里（不是块顶）
    for imp in imports:
        assert imp not in h.body and imp not in h.body[6].body, "obs_event import 必须在命中分支内部"
        assert ast.unparse(imp) == "from app.memory.observability import obs_event"


def test_每档恰一处commit且排在日志_obs_回执之前():
    h = _fn(_tree(), HELPER)
    for loop_idx, hit_log in ((6, "text-hit"), (8, "topic-hit")):
        unparsed = [ast.unparse(s) for s in _ordered(h.body[loop_idx])]
        commits = [i for i, t in enumerate(unparsed) if t == "await db.commit()"]
        assert len(commits) == 1, "每个 For 里必须恰好一处 commit：%s" % unparsed
        c = commits[0]
        log_i = next(i for i, t in enumerate(unparsed) if hit_log in t and "_logger.info" in t)
        obs_i = next(i for i, t in enumerate(unparsed) if "obs_event(" in t)
        rec_i = next(i for i, t in enumerate(unparsed) if "emit_memory_receipt(" in t)
        assert c < log_i < obs_i < rec_i, "commit→日志→obs→回执 的先后不能变：%s" % unparsed


def test_两段没有try_异常照旧向上抛():
    h = _fn(_tree(), HELPER)
    assert not [s for s in ast.walk(h) if isinstance(s, ast.Try)], "不许新增 try/吞异常"
    assert not any(isinstance(s, ast.ExceptHandler) for s in ast.walk(h))


def test_调用点是await_单值_is_not_None早退_三闸与embedding初值留调用方():
    flat = _flat(_fn(_tree(), "save_memory"))
    g = _guard(flat)
    assert ast.unparse(g.test) == "content and content.strip() and (not skip_dedup)"
    idx = flat.index(g)
    assert ast.unparse(flat[idx - 1]) == "embedding = None", "外层 embedding 初值仍留调用方"
    kinds = [type(s).__name__ for s in g.body]
    assert kinds == ["Assign", "If", "Assign", "If"], kinds
    assert ast.unparse(g.body[0]).startswith("_vector_hit, embedding = await _vector_dedup_on_write(")
    assert ast.unparse(g.body[1]) == "if _vector_hit is not None:\n    return _vector_hit"
    assert ast.unparse(g.body[2]).startswith("_text_hit = await _text_topic_dedup_on_write(")
    assert ast.unparse(g.body[3]) == "if _text_hit is not None:\n    return _text_hit"
    kw = [k.arg for k in g.body[2].value.value.keywords]
    assert kw == PARAMS, "调用点必须把十五个依赖逐个显式传入：%s" % kw
    # 早退之后就是新建路径（helper 之后不再有任何查重语句）
    assert isinstance(flat[idx + 1], ast.Assign), "helper 之后直接进入新建准备阶段"


def test_save_memory公共签名与helper落位未变():
    tree = _tree()
    sm = _fn(tree, "save_memory")
    assert [a.arg for a in sm.args.args] == SAVE_SIG
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert names.index(HELPER) == names.index("_vector_dedup_on_write") + 1, "紧邻批 7 那段之后"
    assert names.index("_emit_created_memory_receipts") == names.index(HELPER) + 1


# ───────────────────────── 行为冻结（临时库 + 合成数据） ─────────────────────────

class Env:
    def __init__(self):
        self.order = []                # commit / obs / receipt 的先后
        self.obs = []
        self.receipts = []
        self.sql = []                  # 真实执行的 SQL 文本
        self.text_calls = []           # 本切口 helper 的实参（kwargs）
        self.prepare_calls = []        # 新建准备阶段（早退时必须为空）
        self.add_calls = []            # 向量写入
        self.promote_calls = []        # 核心晋升
        self.hooks = []
        self.receipt_raises = False


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
    """临时库（_dbclone 克隆模板库到 tmp_path）＋ 记录式替身；种子行由用例自己插。"""
    e = Env()
    engine = clone_engine(tmp_path / "texttopicstage.db")
    factory = make_session_factory(engine)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(_conn, _cur, statement, parameters, _ctx, _many):
        e.sql.append((statement, list(parameters or [])))

    async def _seed_base():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50b8_u1", nickname="A50 批8 合成用户"))
            db.add(AICharacter(id=101, user_id=1, name="A50 批8 合成角色"))
            await db.commit()
    asyncio.run(_seed_base())

    # —— 服务层四件一律打桩：不真推理、不真写向量 ——
    async def _embed(_text):
        return [0.0] * 8

    async def _similar(*_a, **_kw):
        return None                      # 向量段永不命中 ⇒ 只测本切口两段

    async def _add(**kw):
        e.add_calls.append(kw)
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _similar)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    # —— 与本切口无关的副作用全静默 ——
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
        if e.receipt_raises:
            raise RuntimeError("receipt-boom")
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
    monkeypatch.setattr(core_mod, "maybe_promote_core", lambda *a, **k: e.promote_calls.append(a) or None)
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _rec)
    monkeypatch.setattr(obs_mod, "obs_event", _obs)

    # —— 本切口调用点与早退下游的探针 ——
    real_helper = write_mod._text_topic_dedup_on_write

    async def _probe(**kw):
        out = await real_helper(**kw)
        e.text_calls.append(dict(kw))
        return out

    real_prepare = write_mod._prepare_created_memory

    async def _probe_prepare(**kw):
        e.prepare_calls.append(dict(kw))
        return await real_prepare(**kw)

    real_hooks = write_mod._run_created_memory_hooks

    async def _probe_hooks(**kw):
        e.hooks.append(dict(kw))
        return await real_hooks(**kw)

    monkeypatch.setattr(write_mod, "_text_topic_dedup_on_write", _probe)
    monkeypatch.setattr(write_mod, "_prepare_created_memory", _probe_prepare)
    monkeypatch.setattr(write_mod, "_run_created_memory_hooks", _probe_hooks)

    # —— 会话探针：记 commit 次序 ——
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
    e.engine = engine
    e.factory = factory
    yield e
    asyncio.run(engine.dispose())


def _seed(env, *, content, mid, memory_type="event", source="chat", status="active",
          created_hours_ago=1.0, archived=False, pinned=False, locked=False, importance=40.0):
    """插一条合成旧记忆行（created_at 显式给定，24h 边界才可验）。"""
    from app.models.memory import Memory

    async def _run():
        from app.memory.service import _now_naive
        async with env.factory() as db:
            db.add(Memory(id=mid, user_id=1, character_id=101, memory_type=memory_type,
                          content=content, source=source, status=status,
                          created_at=_now_naive() - timedelta(hours=created_hours_ago),
                          is_archived=archived, is_pinned=pinned, is_locked=locked,
                          importance=importance, strength_days=1.0, review_count=0,
                          derived_from_ids="[]"))
            await db.commit()
    asyncio.run(_run())


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


def _dedup_sql(env):
    return [(s, p) for s, p in env.sql if "FROM memories" in s and "ORDER BY" in s]


def test_字符命中优先_返回被并那条且不新建(env):
    _seed(env, content=OLD_CHAR, mid=1)
    _seed(env, content=OLD_TOPIC, mid=2)
    out = _save(derived_from_ids=[7])
    assert out is not None and out.id == 1, "字符档（>=0.72）先于主题档命中，必须并第 1 条"
    assert len(_rows()) == 2, "命中不许再新建一行"
    assert [o["kind"] for o in env.obs] == ["text_dedup"], env.obs
    assert env.receipts[-1]["reason"] == "write-time text dedup"
    assert env.receipts[-1]["detail"]["kind"] == "text_dedup"
    assert env.receipts[-1]["detail"]["into"]["memory_id"] == 1
    assert env.order[-3:] == ["commit", "obs", "receipt"], env.order[-6:]
    row = _rows()[0]
    assert row.derived_from_ids == "[7]" and row.importance > 40.0 and row.strength_days > 1.0


def test_主题命中_字符档不中时由24h同主题档接住(env):
    _seed(env, content=OLD_TOPIC, mid=3)
    out = _save()
    assert out is not None and out.id == 3, "0.6667 字符档不中(>=0.72)、主题档中(>0.6)"
    assert [o["kind"] for o in env.obs] == ["merge"] and env.obs[0]["detail"]["sim"] == 0.667
    assert env.receipts[-1]["reason"] == "write-time topic merge 24h"
    assert env.receipts[-1]["detail"]["sim"] == 0.667, "extra 被平铺进 detail（原样）"


def test_两档都不中时照旧新建(env):
    _seed(env, content=UNRELATED, mid=4)
    out = _save()
    assert out is not None and out.id == 5, "无命中必须新建（拿到新 id）"
    assert sorted(r.id for r in _rows()) == [4, 5]
    assert env.obs == [], "没命中不许打 dedup obs"


@pytest.mark.parametrize("protect", ["pinned", "locked"])
def test_两档各自的置顶锁定保护(protect, env):
    _seed(env, content=OLD_CHAR, mid=6, **{protect: True})
    out = _save()
    assert out is not None and out.id == 7, "%s 的行两档都跳过 ⇒ 走新建" % protect
    assert next(r for r in _rows() if r.id == 6).review_count == 0, "被保护行不许被强化"


def test_归档行被SQL挡掉(env):
    _seed(env, content=OLD_CHAR, mid=8, archived=True)
    out = _save()
    assert out is not None and out.id == 9, "归档行不参与两档查重"
    assert len(_dedup_sql(env)) == 2, "两档查询照发（各带 is_archived 过滤）"


def test_跨源禁令_感知与非感知不互并(env):
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _seed(env, content=OLD_CHAR, mid=10, source="perception")
    out = _save(source="chat")
    assert out is not None and out.id == 11, "感知行不得并进 chat 行（禁令 3）"
    assert env.obs == []


def test_同来源照旧可并_跨源禁令不是一刀切(env):
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _seed(env, content=OLD_CHAR, mid=12, source="perception")
    out = _save(source="perception")
    assert out is not None and out.id == 12, "两边同为感知 ⇒ 照旧合并"
    assert [o["kind"] for o in env.obs] == ["text_dedup"]


def test_现行闸差异_闸开时字符档看不见stale但主题档看得见(env):
    """同一批 stale 行：字符档走现行闸（恒 active）看不见它，主题档恒怀旧面看得见 ⇒ 判成主题命中。"""
    _loop.AGENT_FLAGS["write_dedup_active_only"] = True
    _loop.AGENT_FLAGS["memory_supersede"] = True
    _loop.AGENT_FLAGS["current_facts_active_only"] = True
    _seed(env, content=OLD_CHAR, mid=14, status="stale")
    out = _save()
    assert out is not None and out.id == 14, "主题档仍能看见 stale 行"
    assert [o["kind"] for o in env.obs] == ["merge"], "stale 行在字符档不可见 ⇒ 只能走主题档"


def test_现行闸差异_闸关时字符档沿用怀旧面照旧并stale(env):
    _loop.AGENT_FLAGS["write_dedup_active_only"] = False
    _loop.AGENT_FLAGS["memory_supersede"] = True
    _loop.AGENT_FLAGS["current_facts_active_only"] = True
    _seed(env, content=OLD_CHAR, mid=15, status="stale")
    out = _save()
    assert out is not None and out.id == 15
    assert [o["kind"] for o in env.obs] == ["text_dedup"], "闸关＝怀旧面（active+stale）⇒ 字符档并它"


def test_阈值边界_比例恰为072命中字符档(env):
    from difflib import SequenceMatcher
    assert SequenceMatcher(None, EXACT_072_A, EXACT_072_B).ratio() == 0.72, "语料自证：恰为 0.72"
    _seed(env, content=EXACT_072_A, mid=16)
    out = _save(content=EXACT_072_B)
    assert out is not None and out.id == 16, ">= 0.72 含等号 ⇒ 命中"
    assert [o["kind"] for o in env.obs] == ["text_dedup"]


def test_阈值边界_比例恰为06不命中主题档(env):
    from difflib import SequenceMatcher
    assert SequenceMatcher(None, EXACT_06_A, EXACT_06_B).ratio() == 0.6, "语料自证：恰为 0.6"
    _seed(env, content=EXACT_06_A, mid=17)
    out = _save(content=EXACT_06_B)
    assert out is not None and out.id == 18, "主题档是严格 > 0.6，等于不命中 ⇒ 新建"
    assert env.obs == []


def test_24h边界_25小时前不并(env):
    _seed(env, content=OLD_TOPIC, mid=19, created_hours_ago=25.0)
    out = _save()
    assert out is not None and out.id == 20, "超过 24h 不并（窗口外）"
    assert env.obs == []


def test_24h边界_1小时前照并(env):
    _seed(env, content=OLD_TOPIC, mid=21, created_hours_ago=1.0)
    out = _save()
    assert out is not None and out.id == 21
    assert [o["kind"] for o in env.obs] == ["merge"]


def test_主题档只并同memory_type(env):
    _seed(env, content=OLD_TOPIC, mid=22, memory_type="preference")
    out = _save(memory_type="event")
    assert out is not None and out.id == 23, "主题档按 memory_type 过滤，异类型不并"
    assert env.obs == []


def test_两条查询的order与limit从真实SQL验(env):
    _save()
    sqls = _dedup_sql(env)
    assert len(sqls) == 2, sqls
    (q1, p1), (q2, p2) = sqls
    # LIMIT 是参数化下发的（SQLAlchemy 渲染成 "LIMIT ? OFFSET ?"），值要从绑定参数里取
    assert "ORDER BY memories.created_at DESC" in q1 and p1[-2:] == [30, 0], (q1, p1)
    assert "ORDER BY memories.created_at DESC" in q2 and p2[-2:] == [50, 0], (q2, p2)
    assert "is_archived = 0" in q1 and "is_archived = 0" in q2
    # select(Memory) 会把整行列名铺进 SELECT 段，所以「有没有按类型过滤」只看 WHERE 段
    w1, w2 = q1.split("WHERE")[1], q2.split("WHERE")[1]
    assert "memories.memory_type" not in w1, "字符档不按类型过滤"
    assert "memories.memory_type" in w2 and "memories.created_at >=" in w2


def test_命中早退不进入新建路径(env):
    _seed(env, content=OLD_CHAR, mid=24)
    out = _save()
    assert out.id == 24
    assert env.prepare_calls == [] and env.add_calls == [] and env.promote_calls == []
    assert env.hooks == [], "早退后不得跑向量写入/晋升/写后副动作"


def test_两段都不命中才进入新建(env):
    _seed(env, content=UNRELATED, mid=25)
    out = _save()
    assert out.id == 26
    assert len(env.text_calls) == 1 and set(env.text_calls[0]) == set(PARAMS), env.text_calls
    assert env.prepare_calls and env.add_calls, "无命中照旧走新建全链"


def test_回执异常照旧向上抛且commit已发生(env):
    _seed(env, content=OLD_CHAR, mid=27)
    env.receipt_raises = True
    with pytest.raises(RuntimeError, match="receipt-boom"):
        _save()
    assert "commit" in env.order and env.order.index("commit") < len(env.order) - 1
    assert env.order[-1] == "receipt", "receipt 是最后一步（抛在它里面）"


def test_被并那条被强化并抬importance(env):
    _seed(env, content=OLD_CHAR, mid=28, importance=20.0)
    out = _save(importance=9)
    assert out.id == 28
    row = _rows()[0]
    # importance=9 → _normalize_importance 给 90.0，抬升后再叠加强化，精确值随强化口径浮动
    assert row.importance >= 90.0 and row.review_count == 1 and row.strength_days > 1.0


# ───────────────────────── 变异自检（只在本进程内，绝不碰工作区字节） ─────────────────────────

def _mutant(pairs):
    """在内存里编译一份改过字面量的 write.py 副本（工作区文件一个字节都不动）。"""
    src = io.open(WRITE_PY, encoding="utf-8").read()
    for old, new in pairs:
        assert src.count(old) == 1, "变异锚点 %r 命中 %d 次（必须唯一）" % (old, src.count(old))
        src = src.replace(old, new)
    mod = types.ModuleType("write_mutant_a50b8")
    mod.__file__ = WRITE_PY
    exec(compile(src, WRITE_PY, "exec"), mod.__dict__)
    return mod


def _save_with(mod, **kw):
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("importance", 3)
    kw.setdefault("content", BODY)
    kw.setdefault("source", "chat")
    return asyncio.run(mod.save_memory(**kw))


def test_变异一_撤掉跨源禁令会让跨源守卫判红(env, monkeypatch):
    """把 ``_cross_source_merge_blocked`` 猴补成恒 False（工作区字节不动）→ 本应并了。"""
    import app.memory.write as wm
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _seed(env, content=OLD_CHAR, mid=30, source="perception")
    monkeypatch.setattr(wm, "_cross_source_merge_blocked", lambda *_a, **_kw: False)
    out = _save_with(wm)
    assert out is not None and out.id == 30, "撤掉禁令后确实会并（⇒ 跨源守卫有牙，真源一旦被改这里就红）"
    # 对照：同场景在未变异源码下必须**不**并（上面 test_跨源禁令_感知与非感知不互并 钉的就是它）


def test_变异二_主题阈值改大于等于会让阈值守卫判红(env):
    """``> 0.6`` → ``>= 0.6`` 的内存副本：恰为 0.6 的语料从"不并"变成"并"。"""
    from difflib import SequenceMatcher
    assert SequenceMatcher(None, EXACT_06_A, EXACT_06_B).ratio() == 0.6
    mutant = _mutant([("ratio() > 0.6", "ratio() >= 0.6")])
    _seed(env, content=EXACT_06_A, mid=31)
    out = _save_with(mutant, content=EXACT_06_B)
    assert out is not None and out.id == 31, "阈值改成 >= 后恰 0.6 也并（⇒ 阈值守卫有牙）"


def test_变异三_24h窗口改48h会让24h边界守卫判红(env):
    """``timedelta(hours=24)`` → ``timedelta(hours=48)`` 的内存副本：25 小时前那行会从"不并"变"并"。"""
    mutant = _mutant([("timedelta(hours=24),\n", "timedelta(hours=48),\n")])
    _seed(env, content=OLD_TOPIC, mid=32, created_hours_ago=25.0)
    out = _save_with(mutant)
    assert out is not None and out.id == 32, "窗口放宽到 48h 后 25 小时前那行也并（⇒ 24h 守卫有牙）"


def test_未变异源码在三发变异语料上仍是不并(env):
    """对照组：真源码在 0.6 语料与 25h 语料上必须新建，证明上面三发的红来自变异而非语料本身。"""
    _seed(env, content=EXACT_06_A, mid=33)
    out1 = _save(content=EXACT_06_B)
    _seed(env, content=OLD_TOPIC, mid=40, created_hours_ago=25.0)
    out2 = _save()
    assert (out1.id, out2.id) == (34, 41), (out1.id, out2.id, env.obs)