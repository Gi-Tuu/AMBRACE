# -*- coding: utf-8 -*-
"""A50 批 3：``save_memory`` 感知来源标注阶段（``_tag_perception_source``）守卫（2026-10-12）。

被测切口：把 ``save_memory`` 中段 ``if _perception_tag_on():`` 这一个完整 If 节点（含原 try/except）
机械抽成同文件模块级私有协程 ``_tag_perception_source``，调用点仍在「台词过滤 + 逐字拦截」之后、
写入前查重之前。**纯搬运、零行为变化**，所以守卫的全部目的就是「证明它没变」：

- 结构冻结（纯 AST，零 IO、不依赖 git／docs）：helper 的形态与十四个形参、语句序列恰为
  ``[docstring, If, Return]``、无总包围 try、``If`` 条件仍是 ``_perception_tag_on()``、
  调用点是「await + 六元解包」且落在原 If 的同一个序号位置、初始化（``_perception_tagged`` 与四个
  ``*_before_tag``）仍留在调用点、七个依赖仍按模块全局晚绑定（没有被提成默认参数或模块级缓存）；
- 行为冻结（临时库真跑 ``save_memory``，合成数据）：flag 关时**连快照都不查**、空语料不启用判据、
  标签残留命中／长词重合命中／未命中、通道写进 ``sub_type``（通道为空则原值保持）、显式 speaker 两列
  被撤销并由 ``perception_actor_column`` 收口成 ``perception``、查询异常 fail-open、
  匹配／通道／日志三处异常下的**部分改写与既有回退边界**（含「except 不撤 ``epistemic_status`` 与
  ``_perception_tagged``」这条既有怪癖）、台词先拒时 helper 一次都不跑、打标后查重收到新 ``source``、
  来源备份仍供后续回执与 provenance 使用。

纪律：临时库走 pytest ``tmp_path``（``_dbclone`` 克隆模板库），绝不连生产库；全文只用合成数据，
不引用任何真实用户内容、仓库绝对路径或内部文档；嵌入与向量查重全部打桩，不发生真实推理调用。
"""
import ast
import asyncio
import io
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
import app.memory.service as svc

WRITE_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"

HELPER = "_tag_perception_source"
PARAMS = ["db", "user_id", "character_id", "content", "source", "epistemic_status", "sub_type",
          "speaker_type", "speaker_id", "_perception_tagged", "_source_before_tag",
          "_sub_type_before_tag", "_speaker_type_before_tag", "_speaker_id_before_tag"]
RETURNED = ["source", "epistemic_status", "_perception_tagged", "sub_type",
            "speaker_type", "speaker_id"]
# 七个依赖必须继续按模块全局晚绑定（monkeypatch 接缝在模块属性上）
DEFERRED = ["_perception_tag_on", "_recent_snapshots", "has_snapshot_tag", "snapshot_overlap",
            "_matched_snapshot_channel", "PERCEPTION_SOURCE", "_logger"]

# 合成语料（不对应任何真实用户内容）
SNAP_A = "第三季度预算缩减三成已经确认"          # accessibility 通道
SNAP_B = "他答应周末去机场接人"                 # clipboard 通道
MEM_HIT = "第三季度预算缩减三成这件事要跟进"       # 与 SNAP_A 连续重合 ≥6 字 ⇒ 主判据命中
MEM_MISS = "阳台上的薄荷长得很好"               # 与两条快照都无长词重合
MEM_TAG = "[屏幕 3分钟前] 阳台上的薄荷长得很好"   # 只有标签残留命中


# ───────────────────────── 结构冻结（纯 AST，零 IO） ─────────────────────────

def _tree():
    return ast.parse(io.open(WRITE_PY, encoding="utf-8").read())


def _fn(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("write.py 里找不到 %s" % name)


def _flat(fn):
    """摊平 save_memory 的 async with 一层，让调用点与原 If 在同一层比。"""
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


def test_helper是模块级私有协程且十四个形参固定():
    helper = _fn(_tree(), HELPER)
    assert isinstance(helper, ast.AsyncFunctionDef), "必须 async（段内有 await _recent_snapshots）"
    assert helper.name.startswith("_"), "私有协程，名字带下划线前缀"
    assert helper.args.args == [] and helper.args.kwonlyargs, "十四个依赖一律 keyword-only"
    assert [a.arg for a in helper.args.kwonlyargs] == PARAMS
    assert helper.args.vararg is None and helper.args.kwarg is None
    assert not helper.decorator_list


def test_helper体语句序列恰为docstring_If_Return():
    """没有总包围 try：原 If 内的 try 保持原样，外层不许再包一层（那会改变异常边界）。"""
    body = list(_fn(_tree(), HELPER).body)
    assert isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant), "首条是 docstring"
    assert [type(s).__name__ for s in body[1:]] == ["If", "Return"], \
        [type(s).__name__ for s in body[1:]]
    node = body[1]
    assert ast.unparse(node.test) == "_perception_tag_on()", "总闸条件逐字不变"
    assert [type(s).__name__ for s in node.body] == ["Try"], "If 内仍是那一个 try"
    handler = node.body[0].handlers[0]
    assert isinstance(handler.type, ast.Name) and handler.type.id == "Exception", "except Exception 不变"
    assert ast.unparse(body[2]) == "return (%s)" % ", ".join(RETURNED)


def test_除六个改写量外不新增任何赋值():
    """helper 只允许改写六个形参（外加三个段内临时量）——不得引入隐式局部状态。"""
    helper = _fn(_tree(), HELPER)
    bound = {n.id for n in ast.walk(helper)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    tmps = {"_corpus", "_texts", "_channel", "_s", "t"}
    assert bound <= set(PARAMS) | tmps, "越界赋值：%s" % sorted(bound - set(PARAMS) - tmps)
    assert bound & set(PARAMS) == set(RETURNED), "被改写的形参必须恰好是返回的那六个"


def test_调用点是await加六元解包且落在原If的位置():
    tree = _tree()
    call = _call_node(tree)
    assert isinstance(call.value, ast.Await), "调用点必须 await"
    inner = call.value.value
    assert isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
    assert inner.func.id == HELPER
    assert inner.args == [] and [k.arg for k in inner.keywords] == PARAMS, \
        "十四个依赖一律 keyword 传入、不留位置参数"
    tgt = call.targets[0]
    assert isinstance(tgt, ast.Tuple) and [e.id for e in tgt.elts] == RETURNED, \
        "解包顺序必须与 helper 返回顺序一致"

    flat = _flat(_fn(tree, "save_memory"))
    idx = flat.index(call)
    before = [ast.unparse(s) for s in flat[idx - 5:idx]]
    assert before == [
        "_perception_tagged = False",
        "_source_before_tag = source",
        "_sub_type_before_tag = sub_type",
        "_speaker_type_before_tag = speaker_type",
        "_speaker_id_before_tag = speaker_id",
    ], "五个初始化必须仍在调用点、且紧邻其上：%s" % before
    assert ast.unparse(flat[idx + 1]) == "embedding = None", "调用点之后紧接写入前查重"
    # 位置不得前移：台词拦截分支必须在它之前。A50 批 6 起那段（元信息闸门／台词原文／逐字 AI 台词）
    # 整体搬进 `_prefilter_memory_write`，故这里锚「过滤段调用点」而不是原 If 的文本——顺序保证不变。
    hits = [i for i, s in enumerate(flat) if "_prefilter_memory_write" in ast.unparse(s)]
    assert hits and max(hits) < idx, "打标调用点仍排在台词过滤之后"


def test_七个依赖仍是模块全局晚绑定():
    """不得提成默认参数／模块级缓存：测试 monkeypatch 的正是 app.memory.write 上的这些属性。"""
    tree = _tree()
    helper = _fn(tree, HELPER)
    used = {n.id for n in ast.walk(helper) if isinstance(n, ast.Name)}
    assert set(DEFERRED) <= used, "helper 里少了这些依赖：%s" % sorted(set(DEFERRED) - used)
    assert not (set(DEFERRED) & set(PARAMS)), "这些依赖不得变成形参（会提前绑定）"
    # 模块顶层不得出现对这些名字的缓存赋值
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                assert not (isinstance(t, ast.Name) and t.id in DEFERRED), \
                    "模块顶层不许缓存 %s" % t.id
    # 反向钉：save_memory 自己不再直接调快照查询（那段整体搬走了）
    sm = _fn(tree, "save_memory")
    assert not any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_recent_snapshots"
                   for n in ast.walk(sm)), "save_memory 里不该再有 _recent_snapshots 调用"


# ───────────────────────── 行为冻结（临时库真跑 save_memory） ─────────────────────────

class Env:
    def __init__(self):
        self.calls = []          # helper 每次调用的实参（kwargs）与返回值
        self.snapshots = []      # _recent_snapshots 的调用次数
        self.receipts = []
        self.rows = []
        self.notes = []

    @property
    def last(self):
        return self.calls[-1]

    @property
    def updates(self):
        return [r for r in self.receipts if r["action"] == "update"]

    @property
    def creates(self):
        return [r for r in self.receipts if r["action"] == "create"]


@pytest.fixture(autouse=True)
def clean_flags():
    """相关 flag 前后复位（本文件任何用例都不依赖本机 runtime_flags 现值）。"""
    def _reset():
        _loop.AGENT_FLAGS["perception_source_tag"] = False
        _loop.AGENT_FLAGS["memory_write_receipt"] = False
        _loop.AGENT_FLAGS["memory_admission_gate"] = False
    _reset()
    yield
    _reset()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时库（_dbclone 克隆模板库到 tmp_path）＋ 记录式替身，只观察标注阶段及其下游。"""
    e = Env()
    engine = clone_engine(tmp_path / "perception.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="a50p_u1", nickname="A50 批3 合成用户"))
            db.add(AICharacter(id=101, user_id=1, name="A50 批3 合成角色"))
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

    monkeypatch.setattr(dd, "_schedule_dedup", _noop)
    monkeypatch.setattr(at, "spawn_background", _spawn)
    monkeypatch.setattr(cb, "memory_chain_builder_enabled", lambda: False)
    monkeypatch.setattr(cb, "link_new_memory", _noop)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)
    monkeypatch.setattr(events_mod, "publish", _noop)
    monkeypatch.setattr(registry, "run_hook", _noop)
    monkeypatch.setattr(core_mod, "maybe_promote_core", _noop)
    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _rec)

    # —— 标注阶段的两个观测点（都是模块属性＝既有 monkeypatch 接缝）——
    from app.memory import write as write_mod

    real_helper = write_mod._tag_perception_source

    async def _probe(**kw):
        out = await real_helper(**kw)
        e.calls.append({"kwargs": kw, "out": out})
        return out

    monkeypatch.setattr(write_mod, "_tag_perception_source", _probe)

    real_snapshots = write_mod._recent_snapshots

    async def _count_snapshots(*a, **kw):
        e.snapshots.append((a, kw))
        return await real_snapshots(*a, **kw)

    monkeypatch.setattr(write_mod, "_recent_snapshots", _count_snapshots)

    yield e
    asyncio.run(engine.dispose())


def _flag(on=True):
    _loop.AGENT_FLAGS["perception_source_tag"] = on


def _add_snapshots(env, rows):
    """落真实快照行：(通道, 正文, 几分钟前)；走临时库，窗口口径由 _recent_snapshots 自己判。"""
    from app.models.device import PhoneSnapshot
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with svc.async_session_factory() as db:
            for src, text, mins in rows:
                db.add(PhoneSnapshot(user_id=1, source=src, content=text,
                                     created_at=now_naive_utc() - timedelta(minutes=mins)))
            await db.commit()

    asyncio.run(_run())


def _rows(env):
    from app.models.memory import Memory

    async def _run():
        async with svc.async_session_factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()

    return asyncio.run(_run())


def _seed_memory(env, content, *, source="chat", sub_type=None):
    """先落一条既有记忆（绕开打标段），返回它的 id。"""
    from app.models.memory import Memory

    async def _run():
        async with svc.async_session_factory() as db:
            row = Memory(user_id=1, character_id=101, memory_type="event", content=content,
                         source=source, sub_type=sub_type, importance=50.0)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    return asyncio.run(_run())


def _save(**kw):
    from app.memory.write import save_memory
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("importance", 4)
    kw.setdefault("content", MEM_HIT)
    kw.setdefault("source", "chat")
    return asyncio.run(save_memory(**kw))


def _flag_on_corpus(env, rows=(("accessibility", SNAP_A, 1), ("clipboard", SNAP_B, 2))):
    _flag(True)
    _add_snapshots(env, rows)


def _real_logger():
    from app.memory import write as write_mod
    return write_mod._logger


class _BoomLogger:
    """只让指定那条日志抛异常的替身（其余照旧走真 logger，避免误伤无关路径）。"""

    def __init__(self, real, info_kw=None, warn_kw=None):
        self._real, self._info_kw, self._warn_kw = real, info_kw, warn_kw

    def info(self, msg, *a, **kw):
        if self._info_kw and self._info_kw in str(msg):
            raise RuntimeError("log-boom")
        return self._real.info(msg, *a, **kw)

    def warning(self, msg, *a, **kw):
        if self._warn_kw and self._warn_kw in str(msg):
            raise RuntimeError("warn-boom")
        return self._real.warning(msg, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ── 总闸 ────────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_flag关_整段跳过且连快照都不查(env):
    """flag 关 ⇒ 逐字节旧行为：标签判据与快照查询都不发生，六元原样返回。"""
    _add_snapshots(env, [("accessibility", SNAP_A, 1)])
    m = _save(content=MEM_HIT, sub_type="slot", speaker_type="user", speaker_id=7)
    assert env.snapshots == [], "总闸关着就不许查快照（省一次库查询）"
    assert len(env.calls) == 1 and env.calls[0]["out"] == (
        "chat", None, False, "slot", "user", 7), env.calls
    assert m.source == "chat" and m.epistemic_status == "FACT"
    assert not env.updates and [r["action"] for r in env.receipts] == ["create"]
    # 非感知条照旧默认归属用户（两列没被撤）
    assert m.speaker_type == "user" and m.speaker_id == 7


@pytest.mark.slow
def test_flag开_空语料_不启用判据(env):
    """本轮没有任何感知注入 ⇒ 判据不启用、不报错，四列与认知状态原样。"""
    _flag(True)
    m = _save(content=MEM_HIT, sub_type="slot")
    assert len(env.snapshots) == 1, "语料空只是不启用判据，查过快照这件事照旧"
    assert env.last["out"][:3] == ("chat", None, False)
    assert m.source == "chat" and m.epistemic_status == "FACT" and m.sub_type == "slot"


@pytest.mark.slow
def test_flag开_窗口外语料不参与打标(env):
    """超出 30 分钟窗的快照不进语料（窗口口径由 _recent_snapshots 自己判，不在本次切口内）。"""
    _flag_on_corpus(env, rows=(("accessibility", SNAP_A, 45),))
    m = _save(content=MEM_HIT)
    assert m.source == "chat" and m.epistemic_status == "FACT"


# ── 两路判据 ────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_flag开_长词重合命中_六元全改写(env):
    """主判据命中：source/epistemic/tagged 三项改写，speaker 两列显式撤销。"""
    _flag_on_corpus(env)
    m = _save(content=MEM_HIT, sub_type="slot", speaker_type="user", speaker_id=7)
    src, epi, tagged, sub, spk_t, spk_i = env.last["out"]
    assert (src, epi, tagged) == ("perception", "INFERRED", True)
    assert (sub, spk_t, spk_i) == ("accessibility", None, None), (sub, spk_t, spk_i)
    assert m.source == "perception" and m.epistemic_status == "INFERRED"
    # 清空之后由 perception_actor_column 收口成可 SQL 查询的 perception 列
    assert m.speaker_type == "perception" and m.speaker_id is None
    assert m.sub_type == "accessibility", "命中那条快照的通道写进 sub_type"
    assert len(_rows(env)) == 1


@pytest.mark.slow
def test_flag开_标签残留命中(env):
    """辅助判据（快照时间标签残留）命中：正文与快照零重合也应标出。"""
    _flag_on_corpus(env)
    m = _save(content=MEM_TAG)
    assert m.source == "perception" and m.epistemic_status == "INFERRED"


@pytest.mark.slow
def test_flag开_未命中_四列与认知状态原样(env):
    """既无标签残留又无长词重合 ⇒ 一个字段都不动（用户真说过的话不受影响）。"""
    _flag_on_corpus(env)
    m = _save(content=MEM_MISS, sub_type="slot", speaker_type="user", speaker_id=7)
    assert env.last["out"] == ("chat", None, False, "slot", "user", 7)
    assert m.source == "chat" and m.sub_type == "slot" and m.speaker_type == "user"
    assert not env.updates


@pytest.mark.slow
@pytest.mark.parametrize("corpus,expect_sub", [
    ([("accessibility", SNAP_A, 1)], "accessibility"),      # 命中的那条快照的通道
    ([("clipboard", SNAP_B, 1), ("accessibility", SNAP_A, 2)], "accessibility"),
    ([(None, SNAP_A, 1)], "slot"),                          # 通道为空 ⇒ 保持调用点原值
])
def test_通道按命中那条快照写入_sub_type(env, monkeypatch, corpus, expect_sub):
    """M2：sub_type 记通道——只在打标这一刻写；通道为空时保持调用点给的值。"""
    _flag(True)

    async def _corpus(_db, _user_id):
        return [(s, t) for s, t, _m in corpus]

    monkeypatch.setattr("app.memory.write._recent_snapshots", _corpus)
    m = _save(content=MEM_HIT, sub_type="slot")
    assert env.last["out"][3] == expect_sub, env.last["out"]
    assert m.source == "perception"
    assert m.sub_type == expect_sub, "sub_type 落在调用点给的 slot 或命中的通道上"


# ── 异常边界（既有行为，抽出不得顺手修） ──────────────────────────────────────

@pytest.mark.slow
def test_快照查询异常_fail_open不丢记忆(env, monkeypatch):
    """查询炸掉：记忆照旧落库（来源不改）、不向上抛。"""
    _flag(True)

    async def _boom(*_a, **_kw):
        raise RuntimeError("snapshot query blew up")

    monkeypatch.setattr("app.memory.write._recent_snapshots", _boom)
    m = _save(content=MEM_HIT, sub_type="slot", speaker_type="user", speaker_id=7)
    assert env.last["out"] == ("chat", None, False, "slot", "user", 7)
    assert m is not None and m.source == "chat" and m.epistemic_status == "FACT"
    assert len(_rows(env)) == 1


@pytest.mark.slow
@pytest.mark.parametrize("boom_at", ["tag", "overlap"])
def test_判据阶段异常_一个字段都没改写(env, monkeypatch, boom_at):
    """判据在**任何改写之前**求值：标签/重合判据炸了 ⇒ 六元原样返回（打标没发生）。"""
    _flag(True)

    async def _corpus(_db, _user_id):
        return [("accessibility", SNAP_A)]

    monkeypatch.setattr("app.memory.write._recent_snapshots", _corpus)
    target = ("app.memory.write.has_snapshot_tag" if boom_at == "tag"
              else "app.memory.write.snapshot_overlap")
    monkeypatch.setattr(target, lambda *a, **kw: (_ for _ in ()).throw(RuntimeError(boom_at + "-boom")))

    m = _save(content=MEM_HIT, sub_type="slot", speaker_type="user", speaker_id=7)
    assert env.last["out"] == ("chat", None, False, "slot", "user", 7), env.last["out"]
    assert m.source == "chat" and m.epistemic_status == "FACT" and m.speaker_type == "user"
    assert not env.updates and len(_rows(env)) == 1


@pytest.mark.slow
def test_通道阶段异常_只撤四列_epistemic与tagged保留(env, monkeypatch):
    """既有怪癖：改写途中抛错时 except 只撤 source/sub_type/speaker 两列，**不撤**
    epistemic_status 与 _perception_tagged——后者停在 True，并照旧写出 update 回执。原样钉死。"""
    _flag(True)

    async def _corpus(_db, _user_id):
        return [("accessibility", SNAP_A)]

    monkeypatch.setattr("app.memory.write._recent_snapshots", _corpus)
    monkeypatch.setattr("app.memory.write._matched_snapshot_channel",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("channel-boom")))

    m = _save(content=MEM_HIT, sub_type="slot", speaker_type="user", speaker_id=7)
    src, epi, tagged, sub, spk_t, spk_i = env.last["out"]
    assert (src, sub, spk_t, spk_i) == ("chat", "slot", "user", 7), "四列必须被撤回"
    assert (epi, tagged) == ("INFERRED", True), "既有怪癖：这两项不撤"
    assert m.source == "chat" and m.epistemic_status == "INFERRED" and m.speaker_type == "user"
    assert len(env.updates) == 1, "tagged 为真 ⇒ 仍写 update 回执（既有行为）"
    assert len(_rows(env)) == 1


@pytest.mark.slow
def test_成功打标的日志抛异常_走同一条fail_open(env, monkeypatch):
    """打标成功那行 INFO 抛错也照旧 fail-open（它在 try 内 ⇒ 被同一个 except 接住）。"""
    _flag(True)

    async def _corpus(_db, _user_id):
        return [("clipboard", SNAP_A)]

    monkeypatch.setattr("app.memory.write._recent_snapshots", _corpus)

    real_logger = _real_logger()
    monkeypatch.setattr("app.memory.write._logger", _BoomLogger(
        real_logger, info_kw="Memory tagged perception"))
    m = _save(content=MEM_HIT, sub_type="slot")
    assert env.last["out"][:4] == ("chat", "INFERRED", True, "slot"), env.last["out"]
    assert m is not None and m.source == "chat" and len(_rows(env)) == 1


@pytest.mark.slow
def test_fail_open那行日志自身抛错_异常照旧上抛(env, monkeypatch):
    """日志抛错的边界照旧：except 里的 warning 再炸，没有外层兜底 ⇒ 冒到调用方
    （不许为了「更稳」顺手加总包围 try——那会把原本该冒的错吞掉）。"""
    _flag(True)

    async def _boom(*_a, **_kw):
        raise RuntimeError("snapshot query blew up")

    monkeypatch.setattr("app.memory.write._recent_snapshots", _boom)
    monkeypatch.setattr("app.memory.write._logger", _BoomLogger(
        _real_logger(), warn_kw="perception tagging failed"))
    with pytest.raises(RuntimeError, match="warn-boom"):
        _save(content=MEM_HIT)


# ── 与上下游的接线（顺序不得变） ──────────────────────────────────────────────

@pytest.mark.slow
def test_台词先拒时标注helper一次都不跑(env, monkeypatch):
    """打标位置刻意排在台词过滤之后：台词被判原文丢弃时，整段标注（含查快照）都不许跑。"""
    import app.memory.dialogue_filter as df

    monkeypatch.setattr(df, "looks_like_raw_dialogue", lambda _t: True)
    m = _save(content=MEM_HIT, source="chat")
    assert m is None
    assert env.calls == [] and env.snapshots == []


@pytest.mark.slow
def test_打标后写入前查重收到新source(env, monkeypatch):
    """查重与禁合并看到的是改写后的 source（不是旧 chat），且感知条不并进既有 chat 行。"""
    _flag_on_corpus(env)
    seeded_id = _seed_memory(env, MEM_HIT)

    async def _hit(*_a, **_kw):
        return (seeded_id, 0.97)

    monkeypatch.setattr(svc, "find_similar_memory", _hit)
    m = _save(content=MEM_HIT)
    assert m.id != seeded_id
    rows = _rows(env)
    assert len(rows) == 2 and [r.source for r in rows] == ["chat", "perception"]
    assert m.source == "perception"
    assert not [r for r in env.receipts if r["action"] == "merge"]


@pytest.mark.slow
def test_来源备份仍供后续回执与provenance使用(env):
    """打标前的来源快照（_source_before_tag）必须仍在：update 回执与 create 回执的 gates 都靠它。"""
    _flag_on_corpus(env)
    m = _save(content=MEM_HIT, sub_type="slot")
    upd = env.updates
    assert len(upd) == 1 and upd[0]["memory_id"] == m.id
    assert upd[0]["reason"] == "perception source tagged (screen-derived)"
    assert upd[0]["detail"]["from_source"] == "chat"
    assert upd[0]["detail"]["to_source"] == "perception"
    assert upd[0]["detail"]["from_speaker_type"] is None
    assert upd[0]["detail"]["speaker_type"] == "perception"
    assert upd[0]["detail"]["sub_type"] == "accessibility"
    create = env.creates[-1]
    assert create["detail"]["provenance"]["gates"] == ["perception_tagged"]
    assert create["detail"]["provenance"]["route"] == "perception"
    assert create["detail"]["provenance"]["stored"]["source"] == "perception"