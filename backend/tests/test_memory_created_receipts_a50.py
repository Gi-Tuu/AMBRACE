# -*- coding: utf-8 -*-
"""A50 批 2：``save_memory`` 新建回执尾段（`_emit_created_memory_receipts`）守卫（2026-10-12）。

被测切口：把 ``save_memory`` 尾段「M4 待核降级 → 批 0-2 感知打标留痕 → #70 M3 create 回执
＋批 0-8 provenance」三段机械抽成同文件模块级私有函数 ``_emit_created_memory_receipts``，
调用点仍在 ``await db.refresh(memory)`` 之后、向量写入之前，**同步调用**（不新增 await）。
**纯搬运、零行为变化**，所以守卫的全部目的是「证明它没变」：

- 结构冻结（纯 AST，零 IO）：helper 是同步私有函数且全部 keyword-only 十七个依赖、三段
  try/except 的语句序列与顺序、``ACTION_*`` 仍走函数内延迟 import（模块属性才是接缝）、
  调用点形态（不是 await、不在 helper 内查库）、没有多出来或挪走的任何一条语句；
- 行为冻结（临时库真跑 ``save_memory``）：pending／perception／两者同在三种形态的回执**顺序与
  完整 payload**逐字段相等；三枚 provenance gate（``perception_tagged``／
  ``admission_pending_review``／``plan_renamed``）各自组合的取值；普通新建只有一条 create；
  回执 flag 关时零留痕；三处回执任一抛异常后续行为照旧；调用点在 refresh 后、向量前；
  既有 reject／merge 入口**零**新建回执调用。

纪律：临时库走 pytest ``tmp_path``（``_dbclone`` 克隆模板库），绝不连生产库；全文只用合成数据，
不引用任何真实用户内容、仓库绝对路径或内部文档。
"""
import ast
import asyncio
import io
import os

import pytest

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
import app.memory.service as svc
from app.memory.perception_tier import PERCEPTION_SOURCE

# 本切口负责的三条回执（顺序即 pending → perception → create）
RECEIPT_ORDER = ["downgrade", "update", "create"]

WRITE_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "memory")) + os.sep + "write.py"

# helper 的十七个 keyword-only 依赖（顺序也钉死：调用点必须逐个显式列出）
HELPER_ARGS = [
    "memory", "character_id", "memory_type", "sub_type", "source", "source_id",
    "derived_from_ids", "node_type", "emit_memory_receipt", "_epi", "_spk", "_spk_type",
    "_pending", "_perception_tagged", "_source_before_tag", "_speaker_type_before_tag",
    "_sub_type_before_plan",
]


# ─────────────────────── 结构冻结（纯 AST，零 IO） ───────────────────────

def _write_tree():
    return ast.parse(io.open(WRITE_PY, encoding="utf-8").read())


def _fn(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("write.py 里找不到模块级 %s" % name)


def test_helper是模块级私有同步函数且签名固定():
    """必须是「同文件模块级**同步**私有函数」——抽成 async／加 await 都算改了调用时序。"""
    helper = _fn(_write_tree(), "_emit_created_memory_receipts")
    assert isinstance(helper, ast.FunctionDef), "必须是同步函数（尾段无 await，加 await 即改变调用形态）"
    assert not isinstance(helper, ast.AsyncFunctionDef)
    assert helper.name.startswith("_")
    assert [a.arg for a in helper.args.args] == [], "不许有位置参数：十七个依赖一律 keyword-only"
    assert [a.arg for a in helper.args.kwonlyargs] == HELPER_ARGS
    assert helper.args.vararg is None and helper.args.kwarg is None
    assert helper.decorator_list == [], "不许加装饰器"


def test_helper体三段顺序与语句序列冻结():
    """docstring 之后＝import ＋ 三段；类型、顺序、数量一个都不许变（禁合并回执）。"""
    helper = _fn(_write_tree(), "_emit_created_memory_receipts")
    body = list(helper.body)
    assert isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant), "首条必须是 docstring"
    code = body[1:]
    assert [type(s).__name__ for s in code] == [
        "ImportFrom", "If", "If", "Try"], "import ＋ _pending 段 ＋ _perception_tagged 段 ＋ create 段"
    # 三段各自 try 作用域都在（回执异常不得阻止后续旧行为）
    assert isinstance(code[1].body[0], ast.Try) and isinstance(code[2].body[0], ast.Try)
    assert len(code[3].handlers) == 1 and isinstance(code[3].handlers[0].type, ast.Name), \
        "create 段必须仍是「一个裸 try/except Exception」（不许把三段并进同一个 try）"
    unparsed = [ast.unparse(s) for s in code]
    assert unparsed[0] == ("from app.memory.receipt import ACTION_CREATE, "
                           "ACTION_DOWNGRADE, ACTION_UPDATE"), unparsed[0]
    assert unparsed[1].startswith("if _pending:")
    assert "ACTION_DOWNGRADE" in unparsed[1] and "admission gate: pending review" in unparsed[1]
    assert unparsed[2].startswith("if _perception_tagged:")
    assert "ACTION_UPDATE" in unparsed[2] and "perception source tagged (screen-derived)" in unparsed[2]
    assert "ACTION_CREATE" in unparsed[3] and "new memory written" in unparsed[3]
    assert "_write_provenance(" in unparsed[3] and "_gates" in unparsed[3]
    # 三枚 gate 的判定顺序逐字钉死（gates 列表是有序的，不能被「整理」成字典序）
    gate_pos = {g: unparsed[3].index(g) for g in
                ("'perception_tagged'", "'admission_pending_review'", "'plan_renamed'")}
    assert gate_pos["'perception_tagged'"] < gate_pos["'admission_pending_review'"] \
        < gate_pos["'plan_renamed'"], gate_pos
    # 不许总包围 try：外层三段各自已静默，多包一层会把 payload 异常一起吞掉（形态变了）
    assert not isinstance(code[0], ast.Try)


def test_ACTION常量仍在函数内延迟import且未被提到模块顶层():
    """模块属性才是 monkeypatch 接缝；提到模块顶层＝绕开「每次调用重新解析」的原口径。"""
    tree = _write_tree()
    top = set()
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module:
            top.add(n.module)
        elif isinstance(n, ast.Import):
            top.update(a.name for a in n.names)
    assert "app.memory.receipt" not in top, "receipt 模块不许出现在模块顶层 import"
    in_helper = {ast.unparse(n).split(" import ")[0].replace("from ", "")
                 for n in ast.walk(_fn(tree, "_emit_created_memory_receipts"))
                 if isinstance(n, ast.ImportFrom) and n.module}
    assert "app.memory.receipt" in in_helper, "三个 ACTION 常量必须继续在函数顶延迟 import"
    # save_memory 自己不再用这三个常量，故那边只留 emit/ACTION_MERGE/ACTION_REJECT
    sm_imports = {a.name for n in ast.walk(_fn(tree, "save_memory"))
                  if isinstance(n, ast.ImportFrom) and n.module == "app.memory.receipt"
                  for a in n.names}
    assert sm_imports == {"emit_memory_receipt", "ACTION_MERGE", "ACTION_REJECT"}, sm_imports


def test_helper内不查库不写库():
    """它只读传入的 ``memory`` 行与标量：不得出现 execute/get/add/commit 这类 IO。"""
    helper = _fn(_write_tree(), "_emit_created_memory_receipts")
    banned = ("db.", "session.", "await ", "async_session_factory", ".execute(", ".commit(",
              "add(", "flush(", "refresh(")
    for node in ast.walk(helper):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("execute", "get", "add", "commit", "flush", "refresh"), \
                "helper 内出现 %s＝搬进了 IO" % node.attr
        if isinstance(node, ast.Await):
            raise AssertionError("helper 内出现 await＝调用形态被改了")


def test_调用点在refresh之后向量之前且是同步调用():
    """调用点必须仍是 ``async with`` 的直接子句、位于 refresh 之后与向量段之前，且不 await。"""
    sm = _fn(_write_tree(), "save_memory")
    blocks = [n for n in ast.walk(sm)
              if isinstance(n, ast.AsyncWith) and "async_session_factory" in ast.unparse(n)]
    assert len(blocks) == 1
    block = blocks[0]
    calls = [i for i, s in enumerate(block.body)
             if isinstance(s, ast.Expr) and "_emit_created_memory_receipts" in ast.unparse(s)]
    assert len(calls) == 1, "helper 在该上下文里必须被调用且只调一次"
    call = block.body[calls[0]]
    assert not isinstance(call.value, ast.Await), "必须是同步调用（不新增 await）"
    inner = call.value
    assert sorted(k.arg for k in inner.keywords) == sorted(HELPER_ARGS)
    assert not inner.args, "十七个依赖一律 keyword 传入，不留位置参数"
    # 位置：`await db.refresh(memory)` 在前，`# 生成向量并存入 ChromaDB` 在后
    before = [ast.unparse(s) for s in block.body[:calls[0]]]
    assert before[-3:] == ["await db.flush()", "await db.commit()", "await db.refresh(memory)"], before[-3:]
    nxt = block.body[calls[0] + 1]
    assert isinstance(nxt, ast.Try) and "add_memory(" in ast.unparse(nxt), \
        "调用点之后必须紧接向量写入段（回执在向量之前）"
    # emit_memory_receipt 由调用点传入（不是 helper 内部 import）——patch 模块属性才接得住
    assert [k for k in inner.keywords if k.arg == "emit_memory_receipt"][0].value.id \
        == "emit_memory_receipt"


# ─────────────────────── 行为冻结（临时库真跑） ───────────────────────

async def _noop():
    return None


class Env:
    """记一次 save_memory 的回执现场（顺序、payload、调用点上下文）。"""

    def __init__(self):
        self.receipts = []
        self.order = []
        self.added = []
        self.helper_calls = []
        self.session_exits = [0]
        self.embedding = ["synthetic-embedding", 0.11, 0.22]

    def rec(self, action):
        return [r for r in self.receipts if r["action"] == action]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时库（``_dbclone`` 克隆模板库到 tmp_path）＋ 记录式替身；只观察新建回执尾段。"""
    e = Env()
    engine = clone_engine(tmp_path / "receipts.db")
    raw_factory = make_session_factory(engine)

    class _Ctx:
        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            return await self._inner.__aenter__()

        async def __aexit__(self, *a):
            e.session_exits[0] += 1
            return await self._inner.__aexit__(*a)

    def _factory():
        return _Ctx(raw_factory())

    async def _seed():
        from app.models.chat import ChatSession
        from app.models.character import AICharacter
        from app.models.user import User
        async with raw_factory() as db:
            db.add(User(id=1, username="a50b2_u1", nickname="A50B2 合成用户"))
            db.add(AICharacter(id=101, user_id=1, name="A50B2 合成角色"))
            db.add(ChatSession(id=1, user_id=1, character_id=101))
            await db.commit()

    asyncio.run(_seed())

    async def _embed(_text):
        return e.embedding

    async def _no_similar(*_a, **_kw):
        return None

    async def _add(**kw):
        e.order.append("vector")
        e.added.append(kw)

    monkeypatch.setattr(svc, "async_session_factory", _factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    # 与本切口无关的外部依赖：后台任务 / 事件 / 插件 hook / 核心晋升
    import app.memory.chain_builder as cb
    import app.memory.core as core_mod
    import app.memory.dedup as dd
    import app.memory.meaning as meaning_mod
    import app.events as events_mod
    import app.plugins.registry as registry
    import app.utils.async_tasks as at
    from app.memory import receipt as receipt_mod

    monkeypatch.setattr(dd, "_schedule_dedup", lambda *_a, **_kw: _noop())
    monkeypatch.setattr(at, "spawn_background", lambda coro, **_kw: coro.close())
    monkeypatch.setattr(cb, "memory_chain_builder_enabled", lambda: False)
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", lambda *_a, **_kw: _noop())
    monkeypatch.setattr(events_mod, "publish", lambda *_a, **_kw: None)
    monkeypatch.setattr(registry, "run_hook", lambda *_a, **_kw: _noop())
    monkeypatch.setattr(core_mod, "maybe_promote_core", lambda *_a, **_kw: _noop())

    real_emit = receipt_mod.emit_memory_receipt

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        e.order.append("receipt:" + str(action))
        e.receipts.append({"character_id": character_id, "memory_id": memory_id,
                           "action": action, "reason": reason, "detail": detail})

    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _rec)

    # 调用点探针：证明 helper 真被调用，且调用发生在 session 退出之前
    from app.memory import write as write_mod
    real_helper = write_mod._emit_created_memory_receipts

    def _probe(**kw):
        e.helper_calls.append({
            "in_session": e.session_exits[0] == 0,
            "memory_id": kw["memory"].id,
            "pending": kw["_pending"],
            "perception_tagged": kw["_perception_tagged"],
            "emit_is_patched": kw["emit_memory_receipt"] is _rec,
        })
        return real_helper(**kw)

    monkeypatch.setattr(write_mod, "_emit_created_memory_receipts", _probe)

    yield {"env": e, "factory": raw_factory, "real_emit": real_emit,
           "recorder": _rec, "monkeypatch": monkeypatch}
    asyncio.run(engine.dispose())


def _save(content=None, **kw):
    from app.memory.write import save_memory
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    if content is not None:
        kw["content"] = content
    kw.setdefault("content", "合成样例：明天要交的第一版草稿")
    kw.setdefault("importance", 2)
    return asyncio.run(save_memory(**kw))


def _seed_snapshot(factory, content, source="clipboard"):
    from app.models.device import PhoneSnapshot
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            db.add(PhoneSnapshot(user_id=1, source=source, content=content,
                                 created_at=now_naive_utc()))
            await db.commit()

    asyncio.run(_run())


def _seed_existing(factory):
    """先落一条既有记忆，再让向量查重命中它（构造 merge 入口）。"""
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            row = Memory(user_id=1, character_id=101, memory_type="event",
                         content="合成样例：既有的一条记忆", importance=4, scope="private")
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    return asyncio.run(_run())


@pytest.mark.slow
def test_普通新建只有一条create且字段逐项相等(env):
    e = env["env"]
    m = _save(source="chat", sub_type="extracted", source_id=999, speaker_type="user", speaker_id=1)
    assert m is not None
    assert [r["action"] for r in e.receipts] == ["create"], e.receipts
    r = e.receipts[0]
    assert r["character_id"] == 101 and r["memory_id"] == m.id
    assert r["reason"] == "new memory written"
    assert r["detail"] == {
        "memory_type": "event", "sub_type": "extracted", "source": "chat",
        "importance": float(m.importance or 0),
        "provenance": r["detail"]["provenance"],
    }
    prov = r["detail"]["provenance"]
    assert prov["route"] == "extracted" and prov["actor"] == "user"
    assert prov["evidence"] == {"kind": "chat_message", "source_id": 999,
                                "derived_from_ids": [], "node_type": None}
    assert prov["gates"] == []
    assert prov["stored"] == {"source": "chat", "sub_type": "extracted",
                              "memory_type": "event", "epistemic_status": "FACT"}
    # 调用点确实发生、且在 session 退出之前；emit 是调用点传入的桩（接缝还在）
    assert e.helper_calls == [{"in_session": True, "memory_id": m.id, "pending": False,
                               "perception_tagged": False, "emit_is_patched": True}]


@pytest.mark.slow
def test_回执在refresh之后向量写入之前(env):
    """调用点在 ``refresh`` 之后、``add_memory`` 之前：顺序由真跑记录，不靠静态搜名字。"""
    e = env["env"]
    _save()
    assert e.order == ["receipt:create", "vector"], e.order
    assert e.session_exits == [1]
    assert e.added and e.added[0]["content"] == "合成样例：明天要交的第一版草稿"


@pytest.mark.slow
def test_待核形态只有downgrade且payload逐字段相等(env):
    """M4 准入闸门开 + 角色推断 ⇒ ``_pending=True``：downgrade 在 create 之前。"""
    e = env["env"]
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    m = _save(source="diary", speaker_type="character", speaker_id=101)
    assert m is not None
    assert [r["action"] for r in e.receipts] == ["downgrade", "create"], e.receipts
    d = e.receipts[0]
    assert d["memory_id"] == m.id
    assert d["reason"] == "admission gate: pending review"
    assert d["detail"] == {"epistemic_status": "INFERRED", "source": "diary",
                           "sender_type": "character"}
    assert e.rec("create")[0]["detail"]["provenance"]["gates"] == ["admission_pending_review"]
    assert e.helper_calls[0]["pending"] is True


@pytest.mark.slow
def test_感知打标形态只有update且payload逐字段相等(env):
    """打标命中 ⇒ ``_perception_tagged=True``：update 在 create 之前，from/to 来源都留痕。"""
    e = env["env"]
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _seed_snapshot(env["factory"], "开会迟到五分钟被主管说了一顿")
    m = _save("他今天开会迟到五分钟被主管说了一顿", source="chat")
    assert m is not None and m.source == PERCEPTION_SOURCE
    assert [r["action"] for r in e.receipts] == ["update", "create"], e.receipts
    u = e.receipts[0]
    assert u["reason"] == "perception source tagged (screen-derived)"
    assert u["detail"] == {"from_source": "chat", "to_source": PERCEPTION_SOURCE,
                           "epistemic_status": "INFERRED", "memory_type": "event",
                           "sub_type": "clipboard", "speaker": "perception",
                           "speaker_type": "perception", "from_speaker_type": None}
    prov = e.rec("create")[0]["detail"]["provenance"]
    assert prov["route"] == "perception"
    assert prov["gates"] == ["perception_tagged"]
    assert prov["stored"] == {"source": PERCEPTION_SOURCE, "sub_type": "clipboard",
                              "memory_type": "event", "epistemic_status": "INFERRED"}
    assert e.helper_calls[0]["perception_tagged"] is True


@pytest.mark.slow
def test_两者同在时三段顺序与provenance_gates齐全(env):
    """pending 与打标同在 ⇒ 三条回执全在，顺序固定，gates 两枚且有序。

    构造方式：打标命中会把 ``speaker_type`` 清空成 perception（因此准入判不出角色），
    再用 ``reliability<0.4`` 把待核拉起来——两条判据互不干扰，才拿得到「两者同在」。
    """
    e = env["env"]
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    _seed_snapshot(env["factory"], "开会迟到五分钟被主管说了一顿")
    m = _save("他今天开会迟到五分钟被主管说了一顿", source="chat", reliability=0.1)
    assert m is not None
    assert [r["action"] for r in e.receipts] == RECEIPT_ORDER, e.receipts
    assert e.order[:3] == ["receipt:downgrade", "receipt:update", "receipt:create"]
    assert e.rec("create")[0]["detail"]["provenance"]["gates"] == [
        "perception_tagged", "admission_pending_review"]
    assert e.helper_calls[0]["pending"] is True and e.helper_calls[0]["perception_tagged"] is True


@pytest.mark.slow
def test_第三枚gate_plan_renamed照旧登记(env):
    """L4 把 sub_type 收敛成 plan ⇒ ``plan_renamed`` 入 gates，且 route 仍按改名前判定。"""
    from datetime import datetime

    import app.memory.tense as tense

    e = env["env"]
    _loop.AGENT_FLAGS["review_plan_validity_extract"] = True
    env["monkeypatch"].setattr(tense, "classify_tense", lambda _m: "plan")
    env["monkeypatch"].setattr(tense, "plan_valid_until",
                               lambda _m, _now: datetime(2026, 10, 1))
    m = _save("用户下周打算去杭州", source="chat", sub_type="extracted")
    assert m is not None and m.sub_type == "plan"
    prov = e.rec("create")[0]["detail"]["provenance"]
    assert prov["route"] == "extracted"          # 判定用的是改名前的 sub_type
    assert prov["gates"] == ["plan_renamed"]
    assert prov["stored"]["sub_type"] == "plan"


@pytest.mark.slow
def test_三枚gate同时在时顺序与取值全对(env):
    """pending ＋ 打标 ＋ plan 改名同时命中：gates 顺序与三段回执都不得被「整理」过。

    注意一个**真实交互**（不是本批引入的，也不许在本批"顺手修"）：感知打标会把 ``sub_type``
    改写成命中快照的通道，而 L4 只在 ``sub_type in (None, "extracted", "plan")`` 时改名。
    通道值空间是 accessibility/clipboard/media ⇒ 生产上打标与 plan_renamed **互斥**。
    要让三枚同时在，通道必须本身落在改名集里（见下一例把互斥也钉死）。
    """
    from datetime import datetime

    import app.memory.tense as tense

    e = env["env"]
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    _loop.AGENT_FLAGS["review_plan_validity_extract"] = True
    env["monkeypatch"].setattr(tense, "classify_tense", lambda _m: "plan")
    env["monkeypatch"].setattr(tense, "plan_valid_until",
                               lambda _m, _now: datetime(2026, 10, 1))
    # 通道值取 "extracted"（落在 L4 改名集内）——这是三枚 gate 同时成立的唯一构造
    _seed_snapshot(env["factory"], "下周打算去杭州开会", source="extracted")
    m = _save("他下周打算去杭州开会", source="chat", sub_type="extracted", reliability=0.1)
    assert m is not None
    assert [r["action"] for r in e.receipts] == RECEIPT_ORDER, e.receipts
    prov = e.rec("create")[0]["detail"]["provenance"]
    assert prov["gates"] == ["perception_tagged", "admission_pending_review", "plan_renamed"]
    assert prov["route"] == "perception"       # 打标把来源改成 perception，route 随之改
    assert prov["stored"] == {"source": PERCEPTION_SOURCE, "sub_type": "plan",
                              "memory_type": "event", "epistemic_status": "INFERRED"}


@pytest.mark.slow
def test_打标与plan改名在真实通道值下互斥(env):
    """真实通道（clipboard/accessibility/...）不在 L4 改名集内 ⇒ 三枚里最多两枚。

    这条不是"期望的怪癖"，是必须钉住的现状：将来谁把改名集放宽或通道口径改了，
    这里会红，提示"互斥结论已过期"，而不是让两处悄悄漂移。
    """
    from datetime import datetime

    import app.memory.tense as tense

    e = env["env"]
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _loop.AGENT_FLAGS["review_plan_validity_extract"] = True
    env["monkeypatch"].setattr(tense, "classify_tense", lambda _m: "plan")
    env["monkeypatch"].setattr(tense, "plan_valid_until",
                               lambda _m, _now: datetime(2026, 10, 1))
    _seed_snapshot(env["factory"], "下周打算去杭州开会", source="clipboard")
    m = _save("他下周打算去杭州开会", source="chat", sub_type="extracted")
    assert m is not None and m.sub_type == "clipboard", "通道不改名（不在 L4 改名集内）"
    prov = e.rec("create")[0]["detail"]["provenance"]
    assert prov["gates"] == ["perception_tagged"]
    assert prov["stored"]["sub_type"] == "clipboard"


@pytest.mark.slow
def test_回执flag关闭时零落库(env, monkeypatch):
    """``memory_write_receipt`` 默认关 ⇒ 还原真发射口后表里零行，写入行为不变。"""
    import app.db.database as dbmod
    from app.models.memory import MemoryWriteReceipt
    from sqlalchemy import select

    e = env["env"]
    assert _loop.AGENT_FLAGS.get("memory_write_receipt", False) is False
    monkeypatch.setattr(dbmod, "async_session_factory", env["factory"])
    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", env["real_emit"])
    m = _save(source="chat", sub_type="extracted")
    assert m is not None
    assert e.helper_calls and e.helper_calls[0]["in_session"] is True, "helper 照旧被调用"

    async def _rows():
        async with env["factory"]() as db:
            return (await db.execute(select(MemoryWriteReceipt))).scalars().all()

    assert asyncio.run(_rows()) == [], "flag 关时表里一行都没有（逐字节旧行为）"


@pytest.mark.slow
@pytest.mark.parametrize("boom_at", ["downgrade", "update", "create"])
def test_三处回执抛异常仍保留后续行为(env, monkeypatch, boom_at):
    """三段各自 try/except 静默：任一段炸了，后面的回执与向量写入照旧发生。"""
    e = env["env"]
    rec = env["recorder"]

    def _boom(action):
        """先记账再抛：让「这一段确实被尝试过」留在 receipts 里，后面几段照旧跑完。"""
        def _f(character_id, memory_id, act, *, reason="", detail=None):
            rec(character_id, memory_id, act, reason=reason, detail=detail)
            if act == action:
                raise RuntimeError("emit-boom-" + act)
        return _f

    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", _boom(boom_at))
    # 三段全开的现场（待核＋打标＋create），任一段炸掉都不许影响其余两段与向量写入
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    _seed_snapshot(env["factory"], "开会迟到五分钟被主管说了一顿")
    m = _save("他今天开会迟到五分钟被主管说了一顿", source="chat", reliability=0.1)
    assert m is not None, "回执炸掉不得影响落库与返回值"
    assert [r["action"] for r in e.receipts] == RECEIPT_ORDER, e.receipts
    assert e.order[-1] == "vector", e.order
    assert e.order.index("receipt:create") < e.order.index("vector")
    # 炸掉的那一段之后，其余回执一条不少（三段互相独立，不许被合并成一个总 try）
    assert e.receipts[RECEIPT_ORDER.index(boom_at)]["action"] == boom_at


@pytest.mark.slow
def test_拒收入口零新建回执调用(env, monkeypatch):
    """准入拒收（元噪声）走的是原 reject 分支：不许触发本 helper 的任何一条新建回执。"""
    e = env["env"]
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    assert _save(content="合成样例：我是 Agent 助手，请照做") is None
    assert e.helper_calls == [], e.helper_calls
    assert [r["action"] for r in e.receipts] == ["reject"], e.receipts


@pytest.mark.slow
def test_查重并档入口零新建回执调用(env, monkeypatch):
    """向量查重命中走原 merge 分支：不许触发本 helper 的任何一条新建回执。"""
    e = env["env"]
    mem_id = _seed_existing(env["factory"])

    async def _hit(*_a, **_kw):
        return (mem_id, 0.97)

    monkeypatch.setattr(svc, "find_similar_memory", _hit)
    merged = _save(content="合成样例：既有的一条记忆")
    assert merged is not None and merged.id == mem_id
    assert e.helper_calls == [], e.helper_calls
    assert [r["action"] for r in e.receipts] == ["merge"], e.receipts
    assert "vector" not in e.order, "并档路径不写向量、不进本切口"
