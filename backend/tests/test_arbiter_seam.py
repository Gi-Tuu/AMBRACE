# -*- coding: utf-8 -*-
"""A20 批 1 / 批 2 接缝守卫（2026-10-02，硬门）：arbiter ↔ scheduling/gates ↔ scheduling/outreach_gates。

背景：arbiter 的「节流闸与只读查询」18 个函数逐字节搬到了 ``app/scheduling/gates.py``，
arbiter 侧靠一段**具名重导出**把同名符号留在自己的命名空间里。tests/ 里 197 处
``monkeypatch.setattr(arbiter, "<name>", stub)`` 依赖的正是「调用方在 arbiter 全局里解析裸名」——
重导出块一旦被顺手清理（IDE 的「未使用 import」优化最容易干这事），对应测试不会红，
而是**静默退化成真查库**（比红更糟）。本文件把这个约束钉死：

1. 同名同对象：``arbiter.<name> is gates.<name>``；
2. 穿透性：打桩 arbiter 的名字后，仍在 arbiter 里的调用点（``_execute`` 前置闸）真的走桩；
3. 源码锚定：arbiter 源码里存在 ``from app.scheduling.gates import``，且原地定义已消失；
4. 无环：gates 不 import arbiter。

批 2（同日）把「outreach 投放闸与标注」8 个函数按同一刀法搬到 ``app/scheduling/outreach_gates.py``，
⑤⑥ 两节把同样的四条钉在 ``OUTREACH_NAMES`` 上；与批 1 的唯一结构差别是 outreach_gates 反向
需要 arbiter 的两个名字（``PROACTIVE_OUTREACH_TYPES`` / ``_OUTREACH_SEND_TRACE``），
因此额外钉住「该导入必须位于全部函数定义之后」（否则任一模块先被导入就会 ImportError），
以及 R4 的代价：被打桩的裸名到底在哪个命名空间解析（⑥ 最后一例＝只打 arbiter 会被绕过）。

批 3a（同日）把 ``_execute`` 开头的**跨类型前置闸**搬到 ``app/scheduling/executors/guards.py``。
搬过去的闸函数**不能**在新模块里 import（解析点一挪，② 那批桩就静默失效），改为由 arbiter 的
``_gates()`` **在调用时刻现取**、经 ``GateBundle`` 注入。⑦ 节钉的就是这条新接缝：现取语义
（含「不是 import 期焊死」）、Stage 1 全类型 / Stage 2 只前台类型、timer 不经过闸、guards 不得
import 闸函数（无环 + 桩不失效），以及 R4 的代价：run_tick 改传 bundle 后 ``_execute`` 的桩签名
必须跟到位——跟不到不会红在断言上，而是 TypeError 被 run_tick 的 try 吞成 ``ok=False``（假失败）。

批 3b（同日）把 ``_execute`` 的 **timer 分支**与三个 timer 专用 helper 搬到 ``app/scheduling/executors/timer.py``，
并把**会话工厂**也纳入 GateBundle（同一条理由：13 处 ``setattr(arbiter, "async_session_factory", …)``）。
⑧ 节钉的是这条新接缝：三个 helper 同名同对象、``session_factory`` 现取、timer.py 不得 import 闸函数与会话工厂、
函数内 import 不得上提顶层、timer 只剩薄调用且返回值透传、限额命中不查库，以及**命门语义**——
每小时限额命中只 return False（不移除事件、不 mark_fired，承诺留 pending 等下轮）。
（零 DB、零网络：所有被调名字一律打桩，纯内存断言，不打 slow 标记。）
"""
import ast
import asyncio
import dataclasses
import inspect
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app.scheduling import arbiter, gates, outreach_gates
from app.scheduling.executors import GateBundle, guards

# 本批搬家的 18 个名字（与任务书逐字一致，一个不多一个不少）
GATE_NAMES = (
    "has_user_said_sleep",
    "get_hourly_active_count",
    "get_last_proactive_time",
    "get_motivation_approved_count",
    "_cn_hour_now",
    "get_daily_sent_count",
    "get_session_daily_sent_count",
    "get_session_last_sent_at",
    "get_recent_proactive_messages",
    "unreplied_cooldown_active",
    "get_dnd_window",
    "is_dnd_now",
    "is_user_active",
    "get_hours_since_last_user_message",
    "inactive_char_skip",
    "has_pending_timer",
    "has_pending_storyline",
    "get_active_characters",
)

_ARBITER_PY = Path(arbiter.__file__).resolve()
_GATES_PY = Path(gates.__file__).resolve()


def _arbiter_src() -> str:
    return _ARBITER_PY.read_text(encoding="utf-8")


def _gates_src() -> str:
    return _GATES_PY.read_text(encoding="utf-8")


# ───────────────────────── ① 同名同对象 ─────────────────────────
def test_18个名字在两个模块里是同一个对象():
    missing = [n for n in GATE_NAMES if not hasattr(arbiter, n) or not hasattr(gates, n)]
    assert missing == [], f"重导出缺名字：{missing}"
    drifted = [n for n in GATE_NAMES if getattr(arbiter, n) is not getattr(gates, n)]
    assert drifted == [], (
        f"以下名字在 arbiter 里被重新定义/替换了（打桩将只影响其中一个命名空间）：{drifted}"
    )


def test_搬走的函数确实是gates里的原生定义():
    # arbiter 只是重导出，不是自己又写了一份（防止两份实现漂移）
    for name in GATE_NAMES:
        func = getattr(gates, name)
        qual = getattr(func, "__qualname__", name)
        assert getattr(arbiter, name).__module__ == "app.scheduling.gates", f"{name} 原生定义不在 gates"
        assert qual == name, f"{name} 的 __qualname__ 异常：{qual}"


# ───────────────────────── ② 穿透性 ─────────────────────────
def _const(value):
    async def _stub(*_a, **_kw):
        return value
    return _stub


def _recorder(sink, value):
    async def _stub(*a, **_kw):
        sink.append(a)
        return value
    return _stub


def test_穿透_is_dnd_now_在execute前置闸走桩(monkeypatch):
    """``_execute`` 里裸名调 is_dnd_now ⇒ 打桩 arbiter.is_dnd_now 必须生效（且一次都不查库）。"""
    calls: list = []
    monkeypatch.setattr(arbiter, "is_dnd_now", _recorder(calls, True))
    item = {"type": "greeting", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False
    # 第二个入参是北京时间 datetime（不钉死时刻），只验「谁被调用、调了几次」
    assert len(calls) == 1 and calls[0][0] == 7, f"打桩没被 arbiter 里的调用点走到：{calls}"


def test_穿透_has_user_said_sleep_在execute前置闸走桩(monkeypatch):
    calls: list = []
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _recorder(calls, True))
    item = {"type": "greeting", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False
    assert calls == [(7, 3)], f"打桩没被 arbiter 里的调用点走到：{calls}"


def test_穿透_get_hourly_active_count_在timer闸走桩(monkeypatch):
    """timer 分支第一道就是每小时限额：桩返回巨值 ⇒ 立刻 False，不进 DB。"""
    calls: list = []
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _recorder(calls, 10 ** 6))
    item = {"type": "timer", "event": types.SimpleNamespace(character_id=7)}
    assert asyncio.run(arbiter._execute(item)) is False
    assert calls == [(7,)], f"打桩没被 arbiter 里的调用点走到：{calls}"


def test_穿透_is_user_active_在execute前置闸走桩(monkeypatch):
    calls: list = []
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    monkeypatch.setattr(arbiter, "is_user_active", _recorder(calls, True))
    item = {"type": "rhythm", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False
    assert calls == [(7, 3)], f"打桩没被 arbiter 里的调用点走到：{calls}"


# ───────────────────────── ③ 源码锚定 ─────────────────────────
def test_arbiter源码里存在gates具名重导出块():
    src = _arbiter_src()
    assert "from app.scheduling.gates import" in src, "arbiter 的具名重导出块被删了（测试会静默退化成真查库）"


def test_重导出块至少含5个本批名字():
    src = _arbiter_src()
    block = re.search(r"from app\.scheduling\.gates import \(([^)]*)\)", src, re.S)
    assert block, "重导出块形态异常（不是 from … import ( … ) 形式）"
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", block.group(1)))
    hit = names & set(GATE_NAMES)
    assert len(hit) >= 5, f"重导出块里的本批名字过少：{sorted(hit)}"


def test_arbiter里不再有这18个函数的原地定义():
    src = _arbiter_src()
    still_defined = [
        n for n in GATE_NAMES
        if re.search(r"^(async )?def %s\b" % re.escape(n), src, re.M)
    ]
    assert still_defined == [], f"arbiter 里仍有原地定义（与 gates 双份实现会漂移）：{still_defined}"


def test_gates源码保留逐字节搬来的定义():
    src = _gates_src()
    missing = [n for n in GATE_NAMES if not re.search(r"^(async )?def %s\b" % re.escape(n), src, re.M)]
    assert missing == [], f"gates 里缺定义：{missing}"


# ───────────────────────── ④ 无环 ─────────────────────────
def test_gates不依赖arbiter():
    src = _gates_src()
    for forbidden in ("import arbiter", "from app.scheduling import arbiter",
                      "from app.scheduling.arbiter", "app.scheduling.arbiter"):
        assert forbidden not in src, f"gates 源码里出现 {forbidden}（搬家不许成环）"


def test_gates模块对象里没有回指arbiter的名字():
    # 运行时兜底：即便有人用字符串拼接绕过 grep，gates 的命名空间里也不该出现 arbiter
    assert not hasattr(gates, "arbiter"), "gates 命名空间里出现了 arbiter（成环）"


# ═══════════════════ ⑤ A20 批 2：outreach 投放闸与标注下沉 outreach_gates ═══════════════════

# 本批搬家的 8 个名字（与任务书逐字一致，一个不多一个不少）
OUTREACH_NAMES = (
    "_outreach_enabled",
    "_collect_outreach_materials",
    "_get_recent_outreach_intents",
    "_shadow_drive_note",
    "_annotate_outreach_plan",
    "_mark_gate",
    "_user_active_hours",
    "_pacing_gate",
)

_OG_PY = Path(outreach_gates.__file__).resolve()


def _og_src() -> str:
    return _OG_PY.read_text(encoding="utf-8")


def test_批2的8个名字在两个模块里是同一个对象():
    missing = [n for n in OUTREACH_NAMES if not hasattr(arbiter, n) or not hasattr(outreach_gates, n)]
    assert missing == [], f"重导出缺名字：{missing}"
    drifted = [n for n in OUTREACH_NAMES if getattr(arbiter, n) is not getattr(outreach_gates, n)]
    assert drifted == [], (
        f"以下名字在 arbiter 里被重新定义/替换了（打桩将只影响其中一个命名空间）：{drifted}"
    )


def test_批2搬走的函数确实是outreach_gates里的原生定义():
    for name in OUTREACH_NAMES:
        func = getattr(outreach_gates, name)
        assert func.__module__ == "app.scheduling.outreach_gates", f"{name} 原生定义不在 outreach_gates"
        assert getattr(func, "__qualname__", name) == name, f"{name} 的 __qualname__ 异常"


def test_arbiter源码里存在outreach_gates具名重导出块():
    src = _arbiter_src()
    assert "from app.scheduling.outreach_gates import" in src, (
        "批 2 的具名重导出块被删了（测试会静默退化成真查库）"
    )
    block = re.search(r"from app\.scheduling\.outreach_gates import \(([^)]*)\)", src, re.S)
    assert block, "重导出块形态异常（不是 from … import ( … ) 形式）"
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", block.group(1)))
    hit = names & set(OUTREACH_NAMES)
    assert hit == set(OUTREACH_NAMES), f"重导出块名字不完整：缺 {sorted(set(OUTREACH_NAMES) - hit)}"


def test_arbiter里不再有批2这8个函数的原地定义():
    src = _arbiter_src()
    still_defined = [
        n for n in OUTREACH_NAMES
        if re.search(r"^(async )?def %s\b" % re.escape(n), src, re.M)
    ]
    assert still_defined == [], f"arbiter 里仍有原地定义（与 outreach_gates 双份实现会漂移）：{still_defined}"


def test_outreach_gates源码保留逐字节搬来的定义():
    src = _og_src()
    missing = [n for n in OUTREACH_NAMES if not re.search(r"^(async )?def %s\b" % re.escape(n), src, re.M)]
    assert missing == [], f"outreach_gates 里缺定义：{missing}"


def test_批2共享状态仍是同一个对象():
    # _annotate_outreach_plan 写、arbiter 发送侧 pop 取走：一旦两侧各自持有一份 dict，观测三键会静默丢失
    assert outreach_gates._OUTREACH_SEND_TRACE is arbiter._OUTREACH_SEND_TRACE
    assert outreach_gates.PROACTIVE_OUTREACH_TYPES is arbiter.PROACTIVE_OUTREACH_TYPES
    outreach_gates._OUTREACH_SEND_TRACE[10 ** 9] = {"intent": "x"}
    try:
        assert arbiter._OUTREACH_SEND_TRACE.get(10 ** 9) == {"intent": "x"}, "两个命名空间各自持有了 dict"
    finally:
        arbiter._OUTREACH_SEND_TRACE.pop(10 ** 9, None)
    assert arbiter._OUTREACH_SEND_TRACE == {}


def test_outreach_gates对arbiter的导入必须排在全部函数定义之后():
    """批 2 与批 1 的唯一结构差别：outreach_gates 反向要 arbiter 的两个名字。

    arbiter 具名重导出要 import 本模块 ⇒ 本模块若在函数定义**之前** import arbiter，
    「先导入 outreach_gates」那条路会因 arbiter 拿不到尚未定义的 8 个函数而 ImportError。
    """
    src = _og_src()
    imports = [m.start() for m in re.finditer(r"^\s*from app\.scheduling\.arbiter import", src, re.M)]
    assert imports, "outreach_gates 没有将 PROACTIVE_OUTREACH_TYPES / _OUTREACH_SEND_TRACE 回填"
    last_def = max(src.index(f"def {n}(") for n in OUTREACH_NAMES)
    assert min(imports) > last_def, "对 arbiter 的导入被挪到函数定义之前了（会成环炸导入）"
    # 真跑一次「先导入 outreach_gates」的顺序（子进程隔离，sys.modules 干净）
    import subprocess
    import sys
    probe = subprocess.run(
        [sys.executable, "-c",
         "import app.scheduling.outreach_gates as g, app.scheduling.arbiter as a; "
         "assert g._pacing_gate is a._pacing_gate and g._OUTREACH_SEND_TRACE is a._OUTREACH_SEND_TRACE"],
        cwd=str(_ARBITER_PY.parents[2]), capture_output=True, text=True,
    )
    assert probe.returncode == 0, f"先导入 outreach_gates 这条路炸了：{probe.stderr[-400:]}"


# ───────────────────────── ⑥ 穿透性（批 2） ─────────────────────────
def test_批2穿透_pacing_gate_在execute三闸处走桩(monkeypatch):
    """``_execute`` 里裸名调 ``_pacing_gate`` ⇒ 打桩 arbiter 必须生效，且命中后真走 ``_mark_gate`` 留痕。"""
    calls: list = []
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(False))
    monkeypatch.setattr(arbiter, "is_user_active", _const(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(0))
    monkeypatch.setattr(arbiter, "_pacing_gate", _recorder(calls, "hour"))
    item = {"type": "greeting", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False
    assert len(calls) == 1 and calls[0][0] is item and calls[0][1] == "greeting" and calls[0][2] == 7, (
        f"打桩没被 arbiter 里的调用点走到：{calls}"
    )
    assert item.get("_gate") == "hour", "arbiter 侧 _mark_gate 重导出失效（留痕链路断）"


def test_批2穿透_annotate_outreach_plan_在run_tick汇总层走桩(monkeypatch):
    """``run_tick`` 里裸名调 ``_outreach_enabled`` / ``_annotate_outreach_plan`` ⇒ 打桩必须生效。"""
    calls: list = []

    class _Src:
        name = "rhythm"

        async def collect(self, ctx):
            return [{"type": "greeting", "priority": 1,
                     "candidate": {"character_id": 7, "user_id": 3, "idle_minutes": 5}}]

    async def _noop(*_a, **_k):
        return None

    async def _motivation(_cid):
        return 0.0

    async def _enabled(*_a, **_k):
        return True

    # A20 批 3a R4：run_tick 改为 `_execute(item, _gates())`（规格 ③）⇒ 桩签名须跟到位
    async def _execute(_item, _g=None):
        return True

    async def _annotate(item, char_id, mats_cache, recent_cache, char_has_unfinished=False):
        calls.append((item["type"], char_id, char_has_unfinished))

    monkeypatch.setattr("app.domain.relationship.decay.run_relationship_decay", _noop)
    monkeypatch.setattr(arbiter, "all_sources", lambda: [_Src()])
    monkeypatch.setattr(arbiter, "_compute_motivation", _motivation)
    monkeypatch.setattr(arbiter, "_outreach_enabled", _enabled)
    monkeypatch.setattr(arbiter, "_annotate_outreach_plan", _annotate)
    monkeypatch.setattr(arbiter, "_execute", _execute)
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _noop)
    monkeypatch.setattr(arbiter, "_trace_scheduler_task", _noop)
    assert asyncio.run(arbiter.run_tick()) == ["greeting(char=7)"]
    assert calls == [("greeting", 7, False)], f"打桩没被 run_tick 的汇总层走到：{calls}"


def test_批2裸名解析点已在outreach_gates_只打arbiter会被绕过(monkeypatch):
    """R4 的可执行说明书：搬走的函数体在 **outreach_gates** 解析 ``async_session_factory``。

    arbiter 侧故意打成「一调用就炸」的桩 ⇒ 若哪天解析点回到 arbiter（有人把函数搬回去），
    本例会以 AssertionError 变红，提醒同步回退测试桩。
    """
    def _boom_factory():
        raise AssertionError("解析点已回到 arbiter（本批桩迁移需重新核对）")

    calls: list = []

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return ["日常问候 [outreach=check_in]", "无意图"]

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, *_a, **_k):
            calls.append("execute")
            return _Rows()

    monkeypatch.setattr(arbiter, "async_session_factory", _boom_factory)
    monkeypatch.setattr(outreach_gates, "async_session_factory", lambda: _Sess())
    out = asyncio.run(arbiter._get_recent_outreach_intents(7, limit=2))
    assert calls == ["execute"], f"outreach_gates 侧的桩没被用到：{calls}"
    assert out == ["check_in"], f"反查逻辑未走通（异常被 fail-open 吞掉了）：{out}"


# ═══════════════ ⑦ A20 批 3a：前置闸下沉 executors/guards + GateBundle 现取注入 ═══════════════

# 本单经 bundle 注入的 6 个闸函数：(GateBundle 字段名, arbiter 里的符号名)
GUARD_FIELDS = (
    ("is_dnd_now", "is_dnd_now"),
    ("has_user_said_sleep", "has_user_said_sleep"),
    ("is_user_active", "is_user_active"),
    ("hourly_active", "get_hourly_active_count"),
    ("pacing_gate", "_pacing_gate"),
    ("mark_gate", "_mark_gate"),
)

_GUARDS_PY = Path(guards.__file__).resolve()


def _guards_src() -> str:
    return _GUARDS_PY.read_text(encoding="utf-8")


# ───────── ⑦-1 现取语义（本单的命门） ─────────
def test_批3_gates_是调用时刻现取而非import期焊死(monkeypatch):
    """``_gates()`` 每次调用都重新解析 arbiter 全局的裸名 ⇒ 桩生效、且 bundle 不是单例。

    若有人把它改成 import 期取一次（模块级 ``_BUNDLE = GateBundle(...)``），本例立刻红：
    桩会被焊死在旧函数对象上，57 处 ``setattr(arbiter, …)`` 当场静默失效。
    """
    async def _stub_a(*_a, **_kw):
        return False

    async def _stub_b(*_a, **_kw):
        return False

    monkeypatch.setattr(arbiter, "is_dnd_now", _stub_a)
    b1 = arbiter._gates()
    assert b1.is_dnd_now is _stub_a, "_gates() 没拿到 arbiter 命名空间上的桩"

    monkeypatch.setattr(arbiter, "is_dnd_now", _stub_b)
    b2 = arbiter._gates()
    assert b2.is_dnd_now is _stub_b, "bundle 在 import 期就被焊死了（第二次现取拿到旧对象）"
    assert b1 is not b2, "两次 _gates() 复用了同一个 bundle 对象（现取语义丢失）"

    # 字段名与 arbiter 符号一一对应（机械核对，防止错位注入）
    for field, name in GUARD_FIELDS:
        assert getattr(b2, field) is getattr(arbiter, name), f"{field} 与 arbiter.{name} 错位"


def test_批3_GateBundle字段与任务书逐字一致且frozen():
    fields = tuple(f.name for f in dataclasses.fields(GateBundle))
    # 批 3b 追加 1 个非闸函数字段（B3B_FIELDS=session_factory），批 4b 再追加 1 个
    # （B4B_FIELDS=app_day_start），其余仍是批 3a 的 6 个闸字段
    assert fields == tuple(f for f, _ in GUARD_FIELDS) + B3B_FIELDS + B4B_FIELDS, \
        f"GateBundle 字段漂移：{fields}"
    assert GateBundle.__dataclass_params__.frozen, "GateBundle 必须 frozen（防运行期被改写）"
    b = arbiter._gates()
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.is_dnd_now = None  # type: ignore[misc]


def test_批3_run_tick调用点显式传现取bundle():
    """规格 ③：run_tick 也走 ``_gates()``（默认参数只是兼容入口，不是主路径）。"""
    src = _arbiter_src()
    assert "ok = await _execute(item, _gates())" in src, "run_tick 没把现取 bundle 传下去"
    assert re.search(r"^def _gates\(\) -> GateBundle:", src, re.M), "_gates() 定义丢失"
    assert "async def _execute(item: dict, g: GateBundle | None = None) -> bool:" in src
    assert "_g = g or _gates()" in src
    assert "blocked = await pre_gates(item, etype, _g)" in src, "_execute 没把 bundle 交给 pre_gates"


# ───────── ⑦-2 穿透：Stage 1 经 bundle 走桩 ─────────
def test_批3穿透_pre_gates经bundle走桩并在Stage1短路(monkeypatch):
    """guards 里的 ``g.is_dnd_now`` 来自 arbiter 现取 ⇒ 打桩 arbiter 必须生效并 return False。"""
    dnd_calls: list = []
    later: list = []
    monkeypatch.setattr(arbiter, "is_dnd_now", _recorder(dnd_calls, True))
    for field, name in GUARD_FIELDS[1:]:
        monkeypatch.setattr(arbiter, name, _recorder(later, False))
    item = {"type": "greeting", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False
    assert len(dnd_calls) == 1 and dnd_calls[0][0] == 7, f"打桩没被 guards 里的调用点走到：{dnd_calls}"
    assert later == [], f"Stage 1 已拦下，后面的闸不该再跑：{later}"


def test_批3_pre_gates放行返回None而非False(monkeypatch):
    """放行必须是 ``None``：``_execute`` 用 ``is not None`` 判定，写成真值判断会把放行当拦截。"""
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(False))
    monkeypatch.setattr(arbiter, "is_user_active", _const(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(0))
    monkeypatch.setattr(arbiter, "_pacing_gate", _const(None))
    calls: list = []
    monkeypatch.setattr(arbiter, "_mark_gate", lambda *_a: calls.append("mark"))
    item = {"type": "greeting", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(guards.pre_gates(item, "greeting", arbiter._gates())) is None
    assert calls == [], "放行却留痕了"

    async def _review(*a, **_kw):
        calls.append(("review", a))
        return True

    monkeypatch.setattr("app.scheduling.memory_review.run_memory_review", _review)
    item2 = {"type": "memory_review", "candidate": {"character_id": 7, "user_id": 3, "memory_id": 11}}
    assert asyncio.run(arbiter._execute(item2, arbiter._gates())) is True
    assert calls == [("review", (7, 3, 11))], f"放行后没走到 etype 分支：{calls}"


# ───────── ⑦-3 Stage 2 只对前台类型跑 ─────────
def test_批3_stage2只对前台类型跑(monkeypatch):
    """ai_social / group_active / pet_visit 是后台行为：Stage 1（免打扰）照跑，三闸不跑。

    原实现里这三类在各自分支就 return 了，用户活跃/每小时限额/outreach 三闸碰不到它们。
    """
    stage1: list = []
    stage2: list = []
    monkeypatch.setattr(arbiter, "is_dnd_now", _recorder(stage1, False))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _recorder(stage2, False))
    monkeypatch.setattr(arbiter, "is_user_active", _recorder(stage2, False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _recorder(stage2, 0))
    monkeypatch.setattr(arbiter, "_pacing_gate", _recorder(stage2, None))

    for etype, cand, runner in (
        ("ai_social", {"character_id": 7, "character_b_id": 8, "user_id": 3},
         "app.scheduling.ai_social.run_ai_social"),
        ("group_active", {"character_id": 7, "group_id": 2, "user_id": 3},
         "app.scheduling.group_active.run_group_active"),
        ("pet_visit", {"character_id": 7, "ai_pet_id": 9, "user_id": 3},
         "app.scheduling.pet_care.run_pet_visit"),
    ):
        stage1.clear()
        stage2.clear()
        monkeypatch.setattr(runner, _const(True))
        ok = asyncio.run(arbiter._execute({"type": etype, "candidate": cand}))
        assert ok is True, f"{etype} 没走到自己的执行分支"
        assert len(stage1) == 1, f"{etype} 连 Stage 1 免打扰都没跑：{stage1}"
        assert stage2 == [], f"{etype} 是后台类型，不该碰 Stage 2 三闸：{stage2}"


def test_批3_前台类型跑满Stage2三闸(monkeypatch):
    """前台类型（greeting）三道闸按序各跑一次，入参与搬前一致；mark_gate 用真实现验留痕。"""
    calls: list = []
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(False))
    monkeypatch.setattr(arbiter, "is_user_active", _recorder(calls, False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _recorder(calls, 0))
    monkeypatch.setattr(arbiter, "_pacing_gate", _recorder(calls, "hour"))
    item = {"type": "greeting",
            "candidate": {"character_id": 7, "user_id": 3, "trigger_reason": "闲置 2000 分钟"}}
    assert asyncio.run(arbiter._execute(item)) is False
    assert calls == [(7, 3), (7,), (item, "greeting", 7, item["candidate"])], calls
    assert item.get("_gate") == "hour", "三闸命中后没经 bundle.mark_gate 留痕"
    assert "[gate=hour]" in item["candidate"]["trigger_reason"], item["candidate"]["trigger_reason"]


# ───────── ⑦-4 timer 不经过 pre_gates ─────────
def test_批3_timer_不经pre_gates(monkeypatch):
    """timer 在 pre_gates 之前就分支返回：6 个闸一次都不该被碰（原实现 Stage 1 带 etype != timer 守卫）。"""
    touched: list = []

    async def _boom_async(*a, **_kw):
        touched.append(a)
        raise AssertionError("timer 不该走前置闸（免打扰/睡眠/三闸都在其后）")

    def _boom_sync(*a, **_kw):
        touched.append(a)
        raise AssertionError("timer 不该调 _mark_gate")

    for field, name in GUARD_FIELDS[:-1]:
        monkeypatch.setattr(arbiter, name, _boom_async)
    monkeypatch.setattr(arbiter, "_mark_gate", _boom_sync)
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(10 ** 6))  # timer 自身的每小时限额
    item = {"type": "timer", "event": types.SimpleNamespace(character_id=7)}
    assert asyncio.run(arbiter._execute(item)) is False
    assert touched == [], f"timer 被塞进 pre_gates 了：{touched}"


# ───────── ⑦-5 guards 侧的约束（无环 + 桩不失效） ─────────
def test_批3_guards不得import闸函数只能经bundle取():
    """闸函数一旦在 guards 里 import，解析点就搬到 guards ⇒ arbiter 上的 57 处桩静默失效。"""
    src = _guards_src()
    import_lines = [ln for ln in src.splitlines() if ln.lstrip().startswith(("import ", "from "))]
    for _field, name in GUARD_FIELDS:
        hit = [ln for ln in import_lines if re.search(r"\b%s\b" % re.escape(name), ln)]
        assert hit == [], f"guards 直接 import 了闸函数 {name}（arbiter 桩会静默失效）：{hit}"
    assert "from app.scheduling.gates import" not in src
    assert "from app.scheduling.outreach_gates import" not in src
    # 正向锚定：6 个字段在函数体里确实是以 g.<field>(…) 形式调用的
    for field, _name in GUARD_FIELDS:
        assert f"g.{field}(" in src, f"guards 里没经 bundle 调用 {field}"


def test_批3_guards不依赖arbiter且logger名沿用旧名():
    src = _guards_src()
    for forbidden in ("import arbiter", "from app.scheduling import arbiter",
                      "from app.scheduling.arbiter", "app.scheduling.arbiter"):
        assert forbidden not in src, f"guards 源码里出现 {forbidden}（搬家不许成环）"
    assert 'get_logger("scheduler.arbiter")' in src, "logger 名必须沿用 scheduler.arbiter（D-1）"


def test_批3_前置闸的三段日志文案逐字节未变():
    """台账/告警按这三行关键字排障，改一个字就算行为变化。"""
    src = _guards_src()
    for line in (
        '_logger.info("Proactive %s char=%s skipped: dnd", etype, _cid)',
        '_logger.info("Proactive %s char=%d skipped: user said sleep after 21:00",',
        '_logger.warning("Sleep flag check failed: %s", e)',
    ):
        assert line in src, f"日志文案漂移：{line}"


def test_批3_先导入executors再导入arbiter不炸():
    """包的重导出方向：arbiter → executors（单向）。反向一旦成环，导入顺序就会炸。"""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import app.scheduling.executors as e, app.scheduling.arbiter as a; "
         "assert e.pre_gates is a.pre_gates and e.GateBundle is a.GateBundle"],
        cwd=str(_ARBITER_PY.parents[2]), capture_output=True, text=True,
    )
    assert probe.returncode == 0, f"先导入 executors 这条路炸了：{probe.stderr[-400:]}"


# ═══════════ ⑧ A20 批 3b：timer 分支下沉 executors/timer（依赖经 bundle 现取） ═══════════
#
# 批 3b 把 ``_execute`` 的 timer 分支（136 行）与三个 timer 专用 helper 逐字节搬到
# ``app/scheduling/executors/timer.py``。与前几批唯一的口径差别：本单把**会话工厂**也纳入了
# GateBundle（tests/ 有 13 处 ``setattr(arbiter, "async_session_factory", …)``，其中
# test_proactive_channel_guard_matrix 的 timer 用例真靠它喂假库）——一旦 timer.py 自己 import，
# 那条用例不会红，而是**静默查真库**。⑧-1 钉重导出与原生定义归属，⑧-2 钉 session_factory 现取，
# ⑧-3 钉依赖纪律（不得 import 闸函数/会话工厂 + 函数内 import 不得上提），⑧-4 钉执行入口
# （timer 走 run_timer 且返回值透传、仍不碰跨类型前置闸、限额命中经 bundle 走桩不查库）。

# 批 3b 追加到 GateBundle 的字段（非闸函数，而是被 tests/ 打桩的依赖）
B3B_FIELDS = ("session_factory",)

# 本批搬家的 3 个 timer 专用 helper（与任务书逐字一致，一个不多一个不少）
TIMER_HELPERS = ("_build_timer_hint", "_build_timer_hint_legacy", "_timer_current_anchor")

# timer 分支里必须留在函数体内的 import（tests/ 打的是各自模块；上提还会引入 scheduler 环）
TIMER_LOCAL_IMPORTS = (
    "app.agent.llm_client",
    "app.scheduling.promise_service",
    "app.scheduling.proactive_topic_guard",
    "app.scheduling.promise_parser",
    "app.scheduling import scheduler",
    "app.life.life_state",
    "app.memory.current_state",
)

from app.scheduling.executors import context as exec_context  # noqa: E402
from app.scheduling.executors import timer as exec_timer  # noqa: E402

_TIMER_PY = Path(exec_timer.__file__).resolve()


def _timer_src() -> str:
    return _TIMER_PY.read_text(encoding="utf-8")


def _import_lines(src: str) -> list[str]:
    return [ln for ln in src.splitlines()
            if ln.lstrip().startswith(("import ", "from ")) and not ln.lstrip().startswith("#")]


def _sentinel_factory():  # pragma: no cover - 只做身份哨兵
    raise AssertionError("哨兵工厂不该被调用")


# ───────── ⑧-1 同名同对象 ─────────
def test_批3b三个helper在arbiter与timer里是同一个对象():
    missing = [n for n in TIMER_HELPERS if not hasattr(arbiter, n) or not hasattr(exec_timer, n)]
    assert missing == [], f"重导出缺名字：{missing}"
    drifted = [n for n in TIMER_HELPERS if getattr(arbiter, n) is not getattr(exec_timer, n)]
    assert drifted == [], (
        f"以下名字在 arbiter 里被重新定义/替换了（两份实现会漂移）：{drifted}"
    )
    for name in TIMER_HELPERS:
        func = getattr(exec_timer, name)
        assert func.__module__ == "app.scheduling.executors.timer", f"{name} 原生定义不在 timer"
        assert getattr(func, "__qualname__", name) == name, f"{name} 的 __qualname__ 异常"


def test_批3b_agent_flag_on下沉context后arbiter名字仍指同一实现():
    # _agent_flag_on 实测零打桩，但按名保留（tests/ 之外也可能按名引用）
    assert arbiter._agent_flag_on is exec_context.agent_flag_on


def test_批3b_arbiter源码里存在timer具名重导出块且原地定义已消失():
    src = _arbiter_src()
    assert "from app.scheduling.executors.timer import" in src, (
        "批 3b 的具名重导出块被删了（按名引用的用例会拿不到名字）"
    )
    block = re.search(r"from app\.scheduling\.executors\.timer import \(([^)]*)\)", src, re.S)
    assert block, "重导出块形态异常（不是 from … import ( … ) 形式）"
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", block.group(1)))
    hit = names & set(TIMER_HELPERS)
    assert hit == set(TIMER_HELPERS), f"重导出块名字不完整：缺 {sorted(set(TIMER_HELPERS) - hit)}"
    still_defined = [n for n in TIMER_HELPERS if re.search(r"^(async )?def %s\b" % re.escape(n), src, re.M)]
    assert still_defined == [], f"arbiter 里仍有原地定义（双份实现会漂移）：{still_defined}"
    assert not re.search(r"^def _agent_flag_on\b", src, re.M), "arbiter 里仍有 _agent_flag_on 原地定义"
    # timer.py 保留逐字节搬来的定义
    tsrc = _timer_src()
    missing = [n for n in TIMER_HELPERS if not re.search(r"^(async )?def %s\b" % re.escape(n), tsrc, re.M)]
    assert missing == [], f"timer 里缺定义：{missing}"


# ───────── ⑧-2 session_factory 现取（本单新增的接缝） ─────────
def test_批3b_session_factory是调用时刻现取而非import期焊死(monkeypatch):
    b1 = arbiter._gates()
    assert b1.session_factory is arbiter.async_session_factory, "_gates() 没现取 arbiter 的会话工厂"

    monkeypatch.setattr(arbiter, "async_session_factory", _sentinel_factory)
    b2 = arbiter._gates()
    assert b2.session_factory is _sentinel_factory, "bundle 在 import 期就被焊死了（桩拿不到）"
    assert b1.session_factory is not b2.session_factory
    assert b1 is not b2, "两次 _gates() 复用了同一个 bundle 对象（现取语义丢失）"


def test_批3b_GateBundle新增字段仍在末尾且frozen不受影响():
    fields = tuple(f.name for f in dataclasses.fields(GateBundle))
    # 批 4b 又在末尾追加了 app_day_start（想念每日配额取应用日界），session_factory 仍在批 3b 的位置
    assert fields[-2] == "session_factory", f"新字段位置漂移：{fields}"
    with pytest.raises(dataclasses.FrozenInstanceError):
        arbiter._gates().session_factory = None  # type: ignore[misc]


# ───────── ⑧-3 timer.py 的依赖纪律（本单命门） ─────────
def test_批3b_timer不得import闸函数与会话工厂只能经bundle取():
    """``get_hourly_active_count``(8 处桩) 与 ``async_session_factory``(13 处桩) 一旦被 timer import，
    arbiter 上的桩当场静默失效——test_timer_anchor_reaches_llm_payload 会退化成真查库。"""
    src = _timer_src()
    imports = _import_lines(src)
    for name in ("get_hourly_active_count", "async_session_factory"):
        hit = [ln for ln in imports if re.search(r"\b%s\b" % re.escape(name), ln)]
        assert hit == [], f"timer 直接 import 了 {name}（arbiter 桩会静默失效）：{hit}"
    # 正向锚定：两处依赖确实以 g.<field>(…) 形式调用
    assert "await g.hourly_active(char_id)" in src
    assert src.count("g.session_factory()") == 2, "timer 分支的取库点没全部走 bundle"
    assert "await _timer_current_anchor(event, g.session_factory)" in src, "锚 helper 没经 bundle 拿工厂"
    assert "async_session_factory" not in _helper_src(src)


def _helper_src(src: str) -> str:
    """切出 ``_timer_current_anchor`` 函数体（helper 内部只认显式传入的 session_factory）。"""
    i = src.index("async def _timer_current_anchor")
    return src[i:]


def test_批3b_函数内import原样留在函数体内不得上提顶层():
    src = _timer_src()
    top = _import_lines(src)
    for frag in TIMER_LOCAL_IMPORTS:
        lifted = [ln for ln in top if frag in ln and not ln.startswith((" ", "\t"))]
        assert lifted == [], f"{frag} 被提到模块顶层了（桩失效 + 可能成环）：{lifted}"
        assert any(frag in ln for ln in top if ln.startswith(("    ", "\t"))), f"{frag} 的函数内 import 丢了"


def test_批3b_timer不依赖arbiter且logger名沿用旧名():
    src = _timer_src()
    for forbidden in ("import arbiter", "from app.scheduling import arbiter",
                      "from app.scheduling.arbiter"):
        assert forbidden not in src, f"timer 源码里出现 {forbidden}（搬家不许成环）"
    assert 'get_logger("scheduler.arbiter")' in src, "logger 名必须沿用 scheduler.arbiter（D-1）"


def test_批3b_timer命门语义与日志文案逐字节未变():
    """台账/告警按这些行检索；**限额命中不移除事件、不 mark_fired** 是本单最容易改坏的一条。"""
    src = _timer_src()
    for line in (
        '_logger.info("Timer event char=%d skipped: hourly limit", char_id)',
        '_logger.info("Timer ready event %d skipped: user already reported result", event.id)',
        '_logger.info("Timer %d suppressed by topic guard: %s", event.id, _reason)',
        '_logger.info("Timer %d skipped via __SKIP__", event.id)',
        'content = "我回来啦！"',
        'if _render_fix_on:',
        'message_type="timer"',
    ):
        assert line in src, f"文案/语义漂移：{line}"
    seg = src[src.index("skipped: hourly limit"):src.index("# 生成兑现消息")]
    assert "await mark_fired(" not in seg, "限额命中段出现了 mark_fired（承诺会被静默吞掉）"
    assert "return False" in seg, "限额命中必须 return False（保留 pending 等下轮）"
    assert "from app.scheduling.executors.timer import (  # noqa: F401" in _arbiter_src()


# ───────── ⑧-4 执行入口：timer 走 run_timer，且仍不碰跨类型前置闸 ─────────
def test_批3b_execute的timer分支只剩薄调用并走run_timer入口(monkeypatch):
    calls: list = []

    async def _spy(item, g):
        calls.append((item, g))
        return True

    monkeypatch.setattr(exec_timer, "run_timer", _spy)
    ev = object()
    item = {"type": "timer", "event": ev}
    assert asyncio.run(arbiter._execute(item)) is True, "返回值没透传"
    assert len(calls) == 1, f"run_timer 没被调用（入口被绕过）：{calls}"
    assert calls[0][0] is item, f"入口没拿到原 item：{calls[0][0]}"
    assert isinstance(calls[0][1], GateBundle), "入口没拿到 GateBundle"
    assert calls[0][1].session_factory is arbiter.async_session_factory

    src = _arbiter_src()
    seg = src[src.index('if etype == "timer":'):]
    seg = seg[:seg.index("# ── 跨类型前置闸")]
    body = [ln for ln in seg.splitlines()[1:] if ln.strip()]
    assert body == ["        from app.scheduling.executors.timer import run_timer",
                    "        return await run_timer(item, _g)"], f"timer 分支没薄干净：{body}"


def test_批3b_timer仍不经跨类型前置闸(monkeypatch):
    """与 ⑦-4 互补：那条走的是「限额命中」早退，这条走的是新入口 run_timer。"""
    async def _boom(*_a, **_kw):
        raise AssertionError("timer 不该走跨类型前置闸（免打扰/睡眠/三闸都在其后）")

    async def _spy(_item, _g):
        return True

    monkeypatch.setattr(exec_timer, "run_timer", _spy)
    for _field, name in GUARD_FIELDS:
        monkeypatch.setattr(arbiter, name, _boom)
    assert asyncio.run(arbiter._execute({"type": "timer", "event": object()})) is True


def test_批3b_限额命中经bundle走桩且不进DB(monkeypatch):
    """run_timer 真跑：限额命中 ⇒ g.hourly_active 走 arbiter 桩、会话工厂一次都不碰。"""
    calls: list = []
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _recorder(calls, 10 ** 6))
    monkeypatch.setattr(arbiter, "async_session_factory", _sentinel_factory)
    item = {"type": "timer", "event": types.SimpleNamespace(character_id=7)}
    assert asyncio.run(exec_timer.run_timer(item, arbiter._gates())) is False
    assert calls == [(7,)], f"限额闸没经 bundle 走桩：{calls}"


def test_批3b_先导入executors再导入arbiter不炸():
    """批 3b 后 executors/__init__ 多了 timer 的重导出，导入方向仍单向。"""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import app.scheduling.executors as e, app.scheduling.arbiter as a; "
         "import app.scheduling.executors.timer as t; "
         "assert e.run_timer is t.run_timer and a._build_timer_hint is t._build_timer_hint "
         "and a._agent_flag_on is e.agent_flag_on"],
        cwd=str(_ARBITER_PY.parents[2]), capture_output=True, text=True,
    )
    assert probe.returncode == 0, f"先导入 executors 这条路炸了：{probe.stderr[-400:]}"


# ═══════ ⑨ A20 批 4a：5 组 etype 分支下沉 executors/ ＋ registry/dispatch 分派表 ═══════
#
# 批 4a 把 ``_execute`` 的 18 个 etype 键（16 条分支）逐字节搬到 ``executors/{social,memory,story,
# special,moment}.py``，并建立方案 §2.3 的分派表（``registry.HANDLERS`` ＋ ``@handler`` ＋
# ``dispatch()``）。arbiter 侧只在 ``pre_gates`` 之后插一次调用：命中⇒透传 bool，未接管⇒返回
# ``None`` 并继续走它自己剩下的梯子（outreach 五类 / plugin，批 4b 再搬）。
#
# 本单最容易坏的四条：
# ① **键集互不相交**（分派化等价性的前提）⇒ 已搬的 etype 在 arbiter 梯子里不得再留分支头；
# ② **未命中必须返回 ``None`` 而非 ``False``** ⇒ 否则未搬分支永远进不去（且与旧梯子末尾的
#    ``return False`` 语义撞车），而 handler 真返回 ``False`` 时又必须直接透传、不许回落；
# ③ **handler 不得自己 import 会话工厂**（special 那组取库只能经 ``g.session_factory``）⇒
#    21 处 ``setattr(arbiter, "async_session_factory", …)`` 会静默绕过打桩去查真库；
# ④ ``BACKGROUND_TYPES`` 只能有一份（guards 的 Stage 2 豁免与 @handler 分组同源）。

B4A_ETYPES = (
    # social（后台行为：只过 Stage 1，三闸豁免）
    "ai_social", "group_active", "pet_visit",
    # memory
    "memory_review", "memory_review_contextual", "emotion_care", "pet_remind", "ai_care", "ai_adopt",
    # story
    "life_regression", "state_trigger", "unfinished_topic", "prospective_intent",
    # special（三键共用一个 handler）
    "birthday", "holiday", "anniversary",
    # moment
    "moment_publish", "moment_comment",
)

from app.scheduling.executors import dispatch as exec_dispatch  # noqa: E402
from app.scheduling.executors import memory as exec_memory  # noqa: E402
from app.scheduling.executors import moment as exec_moment  # noqa: E402
from app.scheduling.executors import registry as exec_registry  # noqa: E402
from app.scheduling.executors import social as exec_social  # noqa: E402
from app.scheduling.executors import special as exec_special  # noqa: E402
from app.scheduling.executors import story as exec_story  # noqa: E402

B4A_MODULES = {
    "social": exec_social, "memory": exec_memory, "story": exec_story,
    "special": exec_special, "moment": exec_moment,
}

# 搬前就是「分支体内局部 import」的名字（tests/ 打的是各自模块属性，上提顶层＝桩失效 + 可能成环）
B4A_LOCAL_IMPORTS = {
    "social": ("app.scheduling.ai_social", "app.scheduling.group_active", "app.scheduling.pet_care"),
    "memory": ("app.scheduling.memory_review", "app.agent.internal_runner", "app.scheduling.pet_care"),
    "story": ("app.scheduling.state_triggers",),
    "special": ("app.scheduling.message_generator", "app.scheduling import scheduler"),
    "moment": ("app.scheduling.moment_publisher", "app.application.moment_service"),
}

# 本批从 arbiter 梯子里删掉的分支头（逐字照搬删除前的写法，钉住「不再回来」）
B4A_GONE_BRANCHES = (
    'if etype == "ai_social":', 'if etype == "group_active":', 'if etype == "pet_visit":',
    'if etype == "memory_review":', 'if etype == "memory_review_contextual":',
    'if etype == "emotion_care":', 'if etype == "pet_remind":', 'if etype == "ai_care":',
    'if etype == "ai_adopt":', 'if etype == "life_regression":', 'if etype == "state_trigger":',
    'if etype == "unfinished_topic":', 'if etype == "prospective_intent":',
    'if etype in ("birthday", "holiday", "anniversary"):',
    'elif etype == "moment_publish":', 'elif etype == "moment_comment":',
)


def _b4a_src(mod) -> str:
    return Path(mod.__file__).resolve().read_text(encoding="utf-8")


def _b4a_open_gates(monkeypatch):
    """把六只闸全部放行（本批只验分派，不重复验闸）。"""
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(False))
    monkeypatch.setattr(arbiter, "is_user_active", _const(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(0))
    monkeypatch.setattr(arbiter, "_pacing_gate", _const(None))


# ───────── ⑨-1 注册表覆盖 ＋ 未接管判定 ─────────
def test_批4a_注册表覆盖本批全部etype且原生定义都在executors里():
    missing = sorted(set(B4A_ETYPES) - set(exec_registry.HANDLERS))
    assert missing == [], f"分派表未覆盖：{missing}"
    homes = {mod.__name__ for mod in B4A_MODULES.values()}
    for etype in B4A_ETYPES:
        fn = exec_registry.HANDLERS[etype]
        assert fn.__module__ in homes, f"{etype} 的原生定义不在本批五个模块：{fn.__module__}"
        assert inspect.iscoroutinefunction(fn), f"{etype} 的 handler 必须是协程"
    # 后台三键：guards 只豁免闸，真正执行仍靠 handler 接管
    assert set(guards.BACKGROUND_TYPES) <= set(exec_registry.HANDLERS)


def test_批4a_dispatch对未接管键返回None而非False():
    """未命中 ⇒ ``None``（交回 arbiter 梯子）。谁哪天把它改成 ``False``，未注册 etype 当场全灭。

    批 4b 后 outreach 五类与 plugin 已被 handler 接管（见 ⑩-1），这里只留**仍未注册**的键。
    """
    # 包属性必须是**模块**：__init__ 里写成 from …dispatch import dispatch 会把它遮蔽成函数
    assert inspect.ismodule(exec_dispatch), "executors.dispatch 被遮蔽成函数了（按模块路径取桩会失效）"
    g = arbiter._gates()
    for etype in ("rhythm", "timer", "nope"):
        out = asyncio.run(exec_dispatch.dispatch({"type": etype}, etype, {"character_id": 7}, 7, g))
        assert out is None, f"{etype} 被判成已接管（实际仍由 arbiter 处理）：{out!r}"


def test_批4a_命中分派时入参一致且返回值透传(monkeypatch):
    seen: list = []
    ret = {"v": True}

    async def _spy(item, candidate, char_id, g):
        seen.append((item, candidate, char_id, g))
        return ret["v"]

    _b4a_open_gates(monkeypatch)
    monkeypatch.setitem(exec_registry.HANDLERS, "pet_remind", _spy)
    cand = {"character_id": 7, "user_id": 3, "pet_id": 5}
    for value in (True, False):
        # False 也必须直接返回：回落梯子＝同一次事件被两个分支各执行一遍（批 4b 后的头号隐患）
        ret["v"] = value
        assert asyncio.run(arbiter._execute({"type": "pet_remind", "candidate": cand})) is value, (
            f"handler 返回 {value} 没被透传"
        )
    assert len(seen) == 2
    item, passed_cand, char_id, g = seen[0]
    assert passed_cand is item["candidate"] is cand, "handler 拿到的 candidate 不是 item 里那份"
    assert char_id == 7, "char_id 口径变了（原梯子取 candidate['character_id']）"
    assert isinstance(g, GateBundle) and g.session_factory is arbiter.async_session_factory


# ───────── ⑨-2 未接管时 arbiter 仍自己处理 ─────────
def test_批4a后批4b_dispatch未接管时arbiter只剩兜底False(monkeypatch):
    """规格：批 4b 把 outreach/plugin 搬走后，本例口径**反转**（原来钉的是「必须回落到 arbiter
    自己的 outreach 分支」）。现在 dispatch 返回 ``None`` ⇒ ``_execute`` 只能给兜底 ``False``，
    一次闸都不许多跑，更不许在 arbiter 里再留一份 outreach 实现（双份会漂移）。"""
    seen: list = []

    async def _not_handled(*_a, **_kw):
        return None

    async def _inactive(char_id, *_a, **_kw):
        seen.append(char_id)
        return True

    monkeypatch.setattr(exec_dispatch, "dispatch", _not_handled)
    _b4a_open_gates(monkeypatch)
    monkeypatch.setattr(arbiter, "inactive_char_skip", _inactive)
    item = {"type": "greeting", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False
    assert seen == [], "arbiter 里还留着 outreach 分支（批 4b 已下沉 executors/outreach）"


def test_批4a_arbiter源码只剩一次分派调用且已搬分支不再回来():
    src = _arbiter_src()
    assert "from app.scheduling.executors.dispatch import dispatch as _dispatch_exec" in src
    assert "_handled = await _dispatch_exec(item, etype, candidate, char_id, _g)" in src
    assert "if _handled is not None:" in src
    assert src.count("_dispatch_exec(") == 1, "分派调用点应只有一处"
    gone = [h for h in B4A_GONE_BRANCHES if h in src]
    assert gone == [], f"已搬分支又回到 arbiter 梯子里（与 handler 双份实现会漂移）：{gone}"
    # 批 4b：outreach 五类与 plugin 也下沉了，这两组分支头同样不得留在 arbiter
    gone_b4b = [h for h in B4B_GONE_BRANCHES if h in src]
    assert gone_b4b == [], f"批 4b 已搬走的分支回到 arbiter：{gone_b4b}"
    # 分派必须排在 pre_gates 之后（否则后台三类的闸豁免会失效）
    assert src.index("blocked = await pre_gates(item, etype, _g)") < src.index("_dispatch_exec(")


# ───────── ⑨-3 依赖纪律（本单命门） ─────────
def test_批4a_五个新模块不得import会话工厂special只能经bundle取():
    for name, mod in B4A_MODULES.items():
        src = _b4a_src(mod)
        bad = [ln for ln in _import_lines(src) if "async_session_factory" in ln]
        assert bad == [], f"{name} 把会话工厂 import 进来了（arbiter 桩会静默失效）：{bad}"
        assert "async_session_factory(" not in src, f"{name} 直接调会话工厂，没经 bundle"
        assert "from app.db.database import" not in src, f"{name} 直接从 app.db.database 取依赖"
        for _field, gate in GUARD_FIELDS:
            hit = [ln for ln in _import_lines(src) if re.search(r"\b%s\b" % re.escape(gate), ln)]
            assert hit == [], f"{name} 直接 import 了闸函数 {gate}：{hit}"
    assert _b4a_src(exec_special).count("async with g.session_factory() as db:") == 1, (
        "节日分支的失败留痕没经 bundle 取库"
    )


def test_批4a_分支体内的局部import原样留在函数体内不得上提顶层():
    for name, frags in B4A_LOCAL_IMPORTS.items():
        lines = _b4a_src(B4A_MODULES[name]).splitlines()
        for frag in frags:
            hits = [ln for ln in lines if frag in ln and re.match(r"(from|import)\b", ln.lstrip())]
            assert hits, f"{name} 丢了 {frag} 的函数内 import"
            lifted = [ln for ln in hits if not ln.startswith((" ", "\t"))]
            assert lifted == [], f"{name} 把 {frag} 提到了模块顶层（桩失效 + 可能成环）：{lifted}"


def test_批4a_新模块不依赖arbiter且有日志的执行器沿用旧logger名():
    for name, mod in B4A_MODULES.items():
        src = _b4a_src(mod)
        for forbidden in ("import arbiter", "from app.scheduling import arbiter",
                          "from app.scheduling.arbiter"):
            assert forbidden not in src, f"{name} 源码里出现 {forbidden}（搬家不许成环）"
    for name in ("special", "moment"):
        assert 'get_logger("scheduler.arbiter")' in _b4a_src(B4A_MODULES[name]), (
            f"{name} 的 logger 名必须沿用 scheduler.arbiter（D-1）"
        )


def test_批4a_story顶层import的三个执行体与arbiter仍同指一份():
    """搬前它们就是 arbiter 的顶层 import（``arbiter.py:15-17``）：arbiter 侧按名保留，不得漂成两份。"""
    for name in ("run_life_regression", "run_unfinished_topic", "run_prospective_due"):
        assert getattr(exec_story, name) is getattr(arbiter, name), f"{name} 两份实现漂移"
        assert getattr(arbiter, name).__module__.startswith("app.scheduling."), f"{name} 原生定义跑偏"


# ───────── ⑨-4 BACKGROUND_TYPES 单一来源 ─────────
def test_批4a_BACKGROUND_TYPES单一来源由registry持有():
    assert guards.BACKGROUND_TYPES is exec_registry.BACKGROUND_TYPES, "guards 仍持有自己的副本"
    assert exec_registry.BACKGROUND_TYPES == ("ai_social", "group_active", "pet_visit")
    assert not re.search(r"^BACKGROUND_TYPES\s*=", _guards_src(), re.M), "guards 里又出现了本地定义"
    assert all(exec_registry.HANDLERS[t].__module__ == exec_social.__name__
               for t in guards.BACKGROUND_TYPES), "后台三类的 handler 不在 social.py"


# ───────── ⑨-5 重复登记保护 ─────────
def test_批4a_同一etype二次登记直接抛且不污染注册表():
    before = dict(exec_registry.HANDLERS)

    async def _dup(item, candidate, char_id, g):
        return False

    with pytest.raises(ValueError):
        exec_registry.handler("ai_social")(_dup)
    with pytest.raises(ValueError):
        exec_registry.handler("holiday", "birthday")(_dup)   # 多键里任一键撞车也要抛
    assert dict(exec_registry.HANDLERS) == before, "抛之前已经把键写进去了（注册表被污染）"


# ═══════ ⑩ A20 批 4b：outreach 五类 + plugin 下沉（arbiter 收口到「闸 → 分派 → 兜底」） ═══════
#
# 批 4b 把 ``_execute`` 剩下的最后两组分支（outreach 五类 5 键 + plugin）逐字节搬进
# ``executors/{outreach,plugin}.py``，并给 GateBundle 补第 8 个字段 ``app_day_start``。
# 本单最容易坏的四条：
# ① 键集仍互不相交（24 键，含本批 6 键）；只有**未注册**的 etype 才落到末尾兜底 ``False``；
# ② 被 tests/ 打桩的依赖各归其位：会话工厂与应用日界经 ``GateBundle`` 现取；而批 1 那五个节流闸
#    与 ``_OUTREACH_SEND_TRACE`` 的桩**仍在 arbiter 侧** ⇒ outreach.py 只能按 ``arbiter.<name>``
#    模块属性调用（在 outreach 里 import 到本地命名空间 = 当场绕过打桩去查真库，比红更糟）；
# ③ ``_plugin_proactive_runtime`` 原生定义落在 plugin.py，arbiter 侧具名重导出
#    （tests/{test_phase_e,test_d2_df,test_proactive_strategy_pack} 按 arbiter 该名直接调用）；
# ④ ``_execute`` 末尾不得再塞回分支（防双份实现漂移）。
#
# ⑩-7 两条是行为证据（真分派、不打桩 dispatch），② 那口径一改就红，不会静默变绿。

B4B_ETYPES = ("greeting", "proactive_chat", "goodnight", "status_update", "motivation", "plugin")

# 批 4b 追加到 GateBundle 的字段（非闸函数，而是被 tests/ 打桩的依赖）
B4B_FIELDS = ("app_day_start",)

# 本批从 arbiter 梯子里删掉的分支头（逐字照搬删除前的写法，钉住「不再回来」）
B4B_GONE_BRANCHES = (
    'if etype == "plugin":',
    'if etype in ("greeting", "proactive_chat", "goodnight", "status_update", "motivation"):',
)

# 批 1 下沉 gates、但桩仍打在 arbiter 上的名字（本批 outreach 分支体用到的那些）
B4B_ARBITER_ATTRS = (
    "inactive_char_skip", "get_motivation_approved_count", "get_last_proactive_time",
    "unreplied_cooldown_active", "get_recent_proactive_messages",
)

from app.scheduling.executors import outreach as exec_outreach  # noqa: E402
from app.scheduling.executors import plugin as exec_plugin  # noqa: E402

_B4B_MODULES = {"outreach": exec_outreach, "plugin": exec_plugin}


def _b4b_code(mod) -> str:
    """模块源码去掉**模块级 docstring**（docstring 里会把 ``from app.db.database import`` 这类禁止
    写法当反例写出来，扫描依赖纪律时只应看代码；函数内 docstring 保留，里面没有 import）。"""
    src = Path(mod.__file__).resolve().read_text(encoding="utf-8")
    first = ast.parse(src).body[0]
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        lines = src.splitlines(keepends=True)
        return "".join(lines[: first.lineno - 1] + lines[first.end_lineno:])
    return src


# ───────── ⑩-1 注册表覆盖（键集 24）＋ 未注册仍返回 None ─────────
def test_批4b_注册表覆盖outreach五类与plugin且共24键():
    missing = sorted(set(B4B_ETYPES) - set(exec_registry.HANDLERS))
    assert missing == [], f"分派表未覆盖：{missing}"
    homes = {exec_outreach.__name__, exec_plugin.__name__}
    for etype in B4B_ETYPES:
        fn = exec_registry.HANDLERS[etype]
        assert fn.__module__ in homes, f"{etype} 的原生定义不在本批两个模块：{fn.__module__}"
        assert inspect.iscoroutinefunction(fn), f"{etype} 的 handler 必须是协程"
    # 五类共用一个 handler（原分支就是一个 ``etype in (…)` 条件），plugin 独占一个
    assert len({exec_registry.HANDLERS[e] for e in B4B_ETYPES[:5]}) == 1, "五类被拆成多份实现会漂移"
    assert exec_registry.HANDLERS["plugin"] is exec_plugin.run_plugin_exec
    assert len(exec_registry.HANDLERS) == 24, f"键集漂移：{sorted(exec_registry.HANDLERS)}"
    # 未注册 etype ⇒ 仍是 None（arbiter 兜底 False），不能因为本批搬完就被改成 False
    g = arbiter._gates()
    out = asyncio.run(exec_dispatch.dispatch({"type": "nope"}, "nope", {"character_id": 7}, 7, g))
    assert out is None, f"未知 etype 没返回 None：{out!r}"


# ───────── ⑩-2 本单新增接缝：app_day_start 现取 ─────────
def test_批4b_app_day_start是调用时刻现取而非import期焊死(monkeypatch):
    b1 = arbiter._gates()
    assert b1.app_day_start is arbiter.app_day_start_utc, "_gates() 没现取 arbiter 的应用日界"

    monkeypatch.setattr(arbiter, "app_day_start_utc", lambda: "SENTINEL_DAY_START")
    b2 = arbiter._gates()
    assert b2.app_day_start() == "SENTINEL_DAY_START", "bundle 在 import 期就被焊死了（桩拿不到）"
    assert b1.app_day_start is not b2.app_day_start
    assert b1 is not b2, "两次 _gates() 复用了同一个 bundle 对象（现取语义丢失）"


# ───────── ⑩-3 两个新模块的依赖纪律（本单命门之一） ─────────
def test_批4b_两个新模块不得import会话工厂与应用日界只能经bundle取():
    for name, mod in _B4B_MODULES.items():
        code = _b4b_code(mod)
        imports = _import_lines(code)
        assert "from app.db.database import" not in code, f"{name} 直接从 app.db.database 取依赖"
        assert "from app.utils.timeutil import app_day_start_utc" not in code, (
            f"{name} 把应用日界 import 进来了（arbiter 桩会静默失效）"
        )
        for dep in ("async_session_factory", "app_day_start_utc"):
            hit = [ln for ln in imports if re.search(r"\b%s\b" % re.escape(dep), ln)]
            assert hit == [], f"{name} import 了被桩依赖 {dep}：{hit}"
        assert code.count("g.session_factory()") == 1, f"{name} 取库点没全经 bundle"
    # 正向锚定：日界只在想念每日配额那一行用一次，plugin 侧完全不碰
    assert _b4b_code(exec_outreach).count("g.app_day_start()") == 1, "想念每日配额没经 bundle 取日界"
    assert "g.app_day_start()" not in _b4b_code(exec_plugin)


# ───────── ⑩-4 outreach 的闸函数只按 arbiter 模块属性调用 ─────────
def test_批4b_outreach调用闸函数只走arbiter模块属性不做具名import():
    """这批的闸函数（批 1 已下沉 ``gates``）桩**仍在 arbiter 上**，与批 2 那八个名字相反：
    在 outreach 里具名 import、或改到 ``gates`` 侧解析，``setattr(arbiter, …)`` 当场静默失效。"""
    code = _b4b_code(exec_outreach)
    imports = _import_lines(code)
    assert "from app.scheduling.outreach_gates import" not in code
    assert "from app.scheduling.gates import" not in code
    for name in B4B_ARBITER_ATTRS + ("_OUTREACH_SEND_TRACE",):
        hit = [ln for ln in imports if re.search(r"\b%s\b" % re.escape(name), ln)]
        assert hit == [], f"outreach 具名 import 了闸函数 {name}（arbiter 桩会静默失效）：{hit}"
        assert f"arbiter.{name}" in code, f"outreach 没按 arbiter.{name} 模块属性调用"
    # arbiter 只能函数体内 import（提到顶层会在 import 期成环：arbiter → executors → outreach → arbiter）
    arb = [ln for ln in imports if re.search(r"\barbiter\b", ln)]
    assert arb, "outreach 没有经 arbiter 取闸函数"
    lifted = [ln for ln in arb if not ln.startswith((" ", "\t"))]
    assert lifted == [], f"arbiter import 被提到模块顶层：{lifted}"
    # 分支体内的局部 import 原样留在函数体内（同批 4a 的口径）
    for frag in ("app.scheduling.message_generator", "app.scheduling.user_rhythm",
                 "app.agent.topic_tracker"):
        hits = [ln for ln in imports if frag in ln]
        assert hits, f"outreach 丢了 {frag} 的函数内 import"
        assert all(ln.startswith((" ", "\t")) for ln in hits), f"{frag} 被提到顶层：{hits}"


# ───────── ⑩-5 plugin：同名同对象 ＋ arbiter 只剩重导出 ─────────
def test_批4b_plugin执行体同名同对象且arbiter只剩具名重导出():
    assert arbiter._plugin_proactive_runtime is exec_plugin._plugin_proactive_runtime, (
        "两份实现漂移（tests/ 按 arbiter 该名调用会测到旧逻辑）"
    )
    assert getattr(exec_plugin, "_plugin_proactive_runtime").__module__ == exec_plugin.__name__
    src = _arbiter_src()
    assert "from app.scheduling.executors.plugin import _plugin_proactive_runtime" in src
    assert not re.search(r"^async def _plugin_proactive_runtime", src, re.M), (
        "arbiter 里仍有原地定义（双份实现会漂移）"
    )
    psrc = _b4b_code(exec_plugin)
    assert re.search(r"^async def _plugin_proactive_runtime", psrc, re.M), "plugin 里缺原生定义"
    # 分支里调用的是本模块裸名（不回指 arbiter，避免双向依赖）
    assert "return await _plugin_proactive_runtime(char_id, candidate, session_id, hint)" in psrc
    # logger 名沿用 scheduler.arbiter（D-1）
    for name, mod in _B4B_MODULES.items():
        assert 'get_logger("scheduler.arbiter")' in Path(mod.__file__).read_text(encoding="utf-8"), name


# ───────── ⑩-6 _execute 末尾只剩兜底 return False ─────────
def test_批4b_execute末尾只剩兜底returnFalse():
    """防将来把分支塞回 arbiter：dispatch 之后最多 3 行（if / return _handled / return False）。"""
    src = _arbiter_src()
    i = src.index("_handled = await _dispatch_exec(")
    j = src.index("\nasync def ", i)                 # _execute 之后的下一个顶层定义
    tail = [ln.strip() for ln in src[i:j].splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert tail[-1] == "return False", f"兜底 return False 不在末尾：{tail}"
    assert len(tail) - 1 <= 3, f"dispatch 之后塞回了分支（{len(tail) - 1} 行）：{tail}"
    assert not any("if etype" in ln for ln in tail), f"尾部又出现 etype 判定：{tail}"


# ───────── ⑩-7 行为证据：真分派下桩仍走 arbiter / bundle ─────────
def test_批4b穿透_greeting经真分派走arbiter闸桩(monkeypatch):
    """不打桩 dispatch，让 greeting 真走一遍「前置闸 → dispatch → outreach handler」：
    ``arbiter.inactive_char_skip`` 的桩必须被 handler 走到（零 DB、零 LLM）。"""
    seen: list = []

    async def _inactive(char_id, *_a, **_kw):
        seen.append(char_id)
        return True          # 配额让位＝分支第一个早退

    _b4a_open_gates(monkeypatch)
    monkeypatch.setattr(arbiter, "inactive_char_skip", _inactive)
    item = {"type": "greeting", "candidate": {"character_id": 7, "user_id": 3}}
    assert asyncio.run(arbiter._execute(item)) is False
    assert seen == [7], "handler 没按 arbiter.<name> 取闸函数（解析点搬走＝桩静默失效）"


def test_批4b穿透_plugin取库经bundle现取arbiter桩(monkeypatch):
    """plugin 旧裸生成分支取角色名走 bundle：``setattr(arbiter, "async_session_factory", 哨兵)``
    必须被用到（哨兵直接抛 ⇒ 一旦被改成模块自 import，本例会退化成真查库而静默变绿）。"""
    calls: list = []

    def _boom():
        calls.append("factory")
        raise RuntimeError("db down")

    async def _flag_off(*_a, **_kw):
        return False

    _b4a_open_gates(monkeypatch)
    monkeypatch.setattr(arbiter, "async_session_factory", _boom)
    monkeypatch.setattr("app.application.flag_service.resolve_flag", _flag_off)
    item = {"type": "plugin", "candidate": {"character_id": 7, "user_id": 3, "session_id": 9,
                                            "hint": "你关注的频道更新了"}}
    with pytest.raises(RuntimeError):
        asyncio.run(arbiter._execute(item))
    assert calls == ["factory"], "plugin 分支的取库点没经 bundle 现取 arbiter 桩"


# ═══════════ ⑪ A20 批 5 第一刀：会话与落库下沉 application/chat_store ═══════════
#
# 批 5 把 chat_service 的**会话解析/创建、用户消息落库、未读与已读维护** 6 个函数逐字节搬到
# ``app/application/chat_store.py``。与 arbiter 那八批唯一的口径差别：本批**不用 GateBundle**
# （chat_service 的打桩面实测 22 名 / 86 处，bundle 字段会爆炸），改用**模块属性回指**——搬出去的
# 模块在函数体内 ``from app.application import chat_service as _cs``，再按 ``_cs.<name>`` 现取。
# 于是那 86 处桩（含 19 处字符串路径桩 ``"app.application.chat_service.get_latest_session_id"``）
# **一行都不用迁移**；代价全落在纪律上：chat_store 顶层不许具名 import 这四个被桩名字，
# ``import chat_service`` 也不许提到顶层（成环）。⑪ 节钉的就是这两侧的约束。
from app.application import chat_service  # noqa: E402
from app.application import chat_store  # noqa: E402

CS_STORE_NAMES = (
    "get_latest_session_id",
    "create_session",
    "_persist_user_message",
    "get_owned_session",
    "get_unread_counts",
    "mark_session_read",
)
# 桩仍在 chat_service 侧、必须经 ``_cs`` 现取的名字（三名依赖 + 组内两处兄弟互调）
B5C1_CS_ATTRS = ("async_session_factory", "spawn_background", "append_domain_event",
                 "get_latest_session_id", "get_owned_session")

_CHAT_SERVICE_PY = Path(chat_service.__file__).resolve()
_CHAT_STORE_PY = Path(chat_store.__file__).resolve()
_BACKEND_PY = _CHAT_STORE_PY.parents[2]


def _b5c1_code(mod) -> str:
    """同 ``_b4b_code``：去掉模块级 docstring（本单 docstring 把禁止写法当反例写了进去）。"""
    src = Path(mod.__file__).resolve().read_text(encoding="utf-8")
    first = ast.parse(src).body[0]
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        lines = src.splitlines(keepends=True)
        del lines[first.lineno - 1: first.end_lineno]
        return "".join(lines)
    return src


class _B5C1Ts:
    def isoformat(self):
        return "2026-10-02T03:04:05"


class _B5C1Res:
    def __init__(self, sess):
        self._sess = sess

    def scalar_one_or_none(self):
        return self._sess

    def all(self):
        return [(11,), (12,)]


# ───────── ⑪-1 同名同对象 ＋ chat_service 只剩重导出 ─────────
def test_批5一_六个名字在chat_service与chat_store里是同一个对象():
    missing = [n for n in CS_STORE_NAMES if not hasattr(chat_service, n) or not hasattr(chat_store, n)]
    assert missing == [], f"重导出缺名字：{missing}"
    drifted = [n for n in CS_STORE_NAMES if getattr(chat_service, n) is not getattr(chat_store, n)]
    assert drifted == [], f"两侧不是同一对象（两份实现会漂移）：{drifted}"
    for name in CS_STORE_NAMES:
        assert getattr(chat_store, name).__module__ == chat_store.__name__, name
        assert not re.search(r"^(?:async )?def %s\b" % re.escape(name),
                             _CHAT_SERVICE_PY.read_text(encoding="utf-8"), re.M), (
            f"chat_service 里仍有 {name} 的原地定义")
    # 重导出块本身也在锚定范围内：IDE 的「未使用 import」清理最容易干这件事
    assert "from app.application.chat_store import" in _CHAT_SERVICE_PY.read_text(encoding="utf-8"), (
        "chat_service 的具名重导出块不见了（6 个调用方与字符串路径桩会当场静默失效）"
    )


# ───────── ⑪-2 字符串路径打桩仍然穿透 ─────────
def test_批5一_字符串路径桩穿透到chat_store里的兄弟调用(monkeypatch):
    """本单命门：tests/ 有 19 处 ``setattr("app.application.chat_service.get_latest_session_id", …)``，
    而唯一调它的 ``create_session`` 已经搬到 chat_store —— 只有让它经 ``_cs`` 现取才继续生效。"""
    seen: list = []

    async def _sid(user_id, character_id):
        seen.append((user_id, character_id))
        return 4242

    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    out = asyncio.run(chat_store.create_session(7, 3))
    assert seen == [(7, 3)], "create_session 没经 _cs 取兄弟（字符串路径桩静默失效）"
    assert out == {"id": 4242, "character_id": 3, "greeting": None}, "复用分支的返回值一字未改"


def test_批5一_兄弟调用的解析点在chat_service而非chat_store自身(monkeypatch):
    """代价面（R4 的镜像）：两侧同名都打桩时，经 ``_cs`` 的调用只认 chat_service 那一份。
    记下来防止将来有人「顺手」把回指改成本地裸名——那不会红，只会让上面那 19 处桩失效。"""
    hits: list = []

    async def _via_cs(*_a, **_kw):
        hits.append("cs")
        return 111

    async def _via_store(*_a, **_kw):
        hits.append("store")
        return 222

    monkeypatch.setattr(chat_service, "get_latest_session_id", _via_cs)
    monkeypatch.setattr(chat_store, "get_latest_session_id", _via_store)
    out = asyncio.run(chat_store.create_session(1, 2))
    assert hits == ["cs"], f"兄弟调用的解析点跑偏：{hits}"
    assert out["id"] == 111


# ───────── ⑪-3 会话工厂：调用时刻现取 chat_service 桩 ─────────
def test_批5一_会话工厂现取chat_service桩而非本模块真工厂(monkeypatch):
    calls: list = []

    def _boom():
        calls.append("factory")
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(chat_store.get_latest_session_id(None, 7))
    assert calls == ["factory"], "取库点没回指 chat_service（会静默退化成真查库）"


def test_批5一_persist跳过落库时不取库不发事件(monkeypatch):
    """``save_user_message=False`` 的早退分支（15 处桩依赖的行为之一）：搬家后仍旧零副作用。"""
    def _boom():
        raise AssertionError("跳过落库不该取库")

    async def _evt(*_a, **_kw):
        raise AssertionError("跳过落库不该发事件")

    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    monkeypatch.setattr(chat_service, "append_domain_event", _evt)
    out = asyncio.run(chat_store._persist_user_message(
        9, 2, 3, "hi", save_user_message=False))
    assert out == (None, None)


def test_批5一_persist的取库与事件与后台任务三个桩名全经chat_service回指(monkeypatch):
    """一条链上钉死三个被桩名字：``async_session_factory`` / ``append_domain_event`` /
    ``spawn_background``，顺带钉住返回值形状与 P-fix（刷新 chat_sessions.updated_at）。"""
    seen: list = []
    sess = types.SimpleNamespace(updated_at=None)

    class _DB:
        def add(self, _obj):
            pass

        async def flush(self):
            pass

        async def commit(self):
            seen.append("commit")

        async def execute(self, *_a, **_kw):
            return _B5C1Res(sess)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    def _factory():
        seen.append("factory")
        return _DB()

    async def _evt(etype, _domain, _eid, **kw):
        seen.append(("event", etype, kw.get("entity_type"), kw.get("entity_id")))

    def _spawn(coro):
        seen.append("spawn")
        coro.close()

    class _Msg:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.id = 77
            self.created_at = _B5C1Ts()

    monkeypatch.setattr(chat_service, "async_session_factory", _factory)
    monkeypatch.setattr(chat_service, "append_domain_event", _evt)
    monkeypatch.setattr(chat_service, "spawn_background", _spawn)
    monkeypatch.setattr(chat_store, "ChatMessage", _Msg)
    mid, info = asyncio.run(chat_store._persist_user_message(
        9, 2, 3, "第一次见面", shared_memory=True))
    assert mid == 77
    assert info == {"id": 77, "session_id": 9, "sender_type": "user",
                    "content": "第一次见面", "created_at": "2026-10-02T03:04:05",
                    "extra_meta": None}, "落库返回形状漂移"
    assert seen[0] == "factory", "取库没走 chat_service 桩"
    assert sess.updated_at is not None, "P-fix 刷新 updated_at 丢了"
    assert seen[-2:] == [("event", chat_store._ET.CHAT_MESSAGE_SENT.value, "chat_message", 77),
                         "spawn"], f"事件/后台任务没回指 chat_service：{seen}"


# ───────── ⑪-4 mark_session_read：归属查询与事件都回指 ─────────
def test_批5一_mark_session_read的归属查询与事件均经chat_service现取(monkeypatch):
    """``db`` 由调用方传入 ⇒ 零假库也能验：兄弟调用 ``get_owned_session`` 与
    ``append_domain_event`` 都必须在 chat_service 命名空间解析；顺带钉住 F-14 联动
    （同角色每个活跃会话各落一条已读事件）。"""
    seen: list = []

    class _DB:
        async def execute(self, *_a, **_kw):
            return _B5C1Res(None)

        async def commit(self):
            pass

    async def _owned(_db, session_id, user_id):
        seen.append(("owned", session_id, user_id))
        return types.SimpleNamespace(user_id=user_id, character_id=5)

    async def _evt(etype, _domain, _eid, **_kw):
        seen.append(("event", etype))

    monkeypatch.setattr(chat_service, "get_owned_session", _owned)
    monkeypatch.setattr(chat_service, "append_domain_event", _evt)
    assert asyncio.run(chat_store.mark_session_read(_DB(), 11, 3)) is True
    assert seen[0] == ("owned", 11, 3), "兄弟调用没经 _cs 取 get_owned_session"
    assert [x for x in seen if x[0] == "event"] == [
        ("event", chat_store._ET.CHAT_SESSION_READ.value)] * 2, "联动已读事件条数漂移"


# ───────── ⑪-5 chat_store 的依赖纪律（源码锚定） ─────────
def test_批5一_chat_store顶层不import_chat_service也不具名import被桩四名():
    code = _b5c1_code(chat_store)
    imports = _import_lines(code)
    # ① 被桩名字一律不许具名 import（解析点一挪，86 处桩当场静默失效）
    for name in B5C1_CS_ATTRS:
        hit = [ln for ln in imports if re.search(r"\b%s\b" % re.escape(name), ln)]
        assert hit == [], f"chat_store 具名 import 了被桩名字 {name}：{hit}"
    for frag in ("from app.db.database import", "from app.utils.async_tasks import",
                 "from app.events.store import"):
        assert frag not in code, f"chat_store 直接从原模块取依赖：{frag}"
    # ② chat_service 只能函数体内 import（提到顶层 ⇒ import 期成环）
    cs = [ln for ln in imports if re.search(r"\bchat_service\b", ln)]
    assert cs, "chat_store 没经 chat_service 现取被桩依赖"
    lifted = [ln for ln in cs if not ln.startswith((" ", "\t"))]
    assert lifted == [], f"chat_service import 被提到模块顶层：{lifted}"
    # ③ 正向锚定：四个回指点一个都不能少
    for name in ("async_session_factory", "append_domain_event", "spawn_background"):
        assert "_cs.%s" % name in code, f"缺 _cs.{name} 回指"
    assert code.count("_cs.async_session_factory") == 4, "取库点数量与本刀实测不符"
    assert "_cs.get_latest_session_id(" in code and "_cs.get_owned_session(" in code


# ───────── ⑪-6 外部调用方仍可 import（两种导入顺序） ─────────
def test_批5一_六个外部调用方仍能import这些名字():
    snippet = (
        "import app.application.%s as m; "
        "import app.agent.runtime, app.voice.gateway, app.api.privacy; "
        "import app.application.moment_service, app.api.images, app.application.emotion_care_ports; "
        "import app.api.chat; "
        "from app.application.chat_service import (get_latest_session_id, create_session, "
        "get_owned_session, get_unread_counts, _persist_user_message, mark_session_read); "
        "import app.application.chat_service as cs, app.application.chat_store as st; "
        "assert all(getattr(cs, n) is getattr(st, n) for n in %r)"
    )
    for first in ("chat_service", "chat_store"):
        probe = subprocess.run(
            [sys.executable, "-c", snippet % (first, list(CS_STORE_NAMES))],
            cwd=str(_BACKEND_PY), capture_output=True, text=True,
        )
        assert probe.returncode == 0, f"先导入 {first} 这条路炸了：{probe.stderr[-500:]}"


# ═══════════ ⑫ A20 批 5 第二刀：回合结算下沉 application/chat_settlement ═══════════
#
# 批 5 第二刀把 chat_service 的**关系温度 / 状态与自述 / 关系驱力 / 念头池的回合结算** 8 个函数
# 逐字节搬到 ``app/application/chat_settlement.py``。刀法与 ⑪ 一致（模块属性回指 ⇒ 桩零迁移）：
# 本组被 tests 打桩的三名 —— ``async_session_factory``（实测 9 处 setattr）/ ``spawn_background``
# （3 处）/ ``SELF_STATEMENT_MAX_LEN``（chat_service 的模块级常量，两处写入点都在搬走的函数里）——
# 一律在函数体内 ``from app.application import chat_service as _cs`` 后按 ``_cs.<name>`` 现取。
#
# 与 ⑪ 的两处结构差别（⑫-5／⑫-6 分别钉住）：
# ① **本组实测零兄弟互调**（8 个函数互相之间没有调用点，只有 ``_settle_thought_pool_turn`` 的
#    docstring 拿 ``_settle_relational_drive`` 当口径参照）⇒ 这一刀没有 ``_cs.<兄弟函数>`` 回指，
#    调用图一字未动：``_trigger_state_eval`` 等仍由留在 chat_service 的 ``_run_post_processing`` 调用。
# ② ``_bump_relationship`` 函数体内**自带** ``from app.db.database import async_session_factory``
#    （搬之前就是局部 import、遮蔽模块级同名），逐字节保留 ⇒ chat_service 的桩本来就碰不到它。
#    这是既有语义、不是本单引入，⑫-5 把它钉成显式契约：将来谁「顺手回指」＝行为改动，会红在这里。
#
# 任务书 ⑫-2 要求对 ``agent`` 也打一条穿透桩，但本组 8 个函数无一引用 chat_service 的模块级 ``agent``
# （只有 ``from app.agent.llm_client import …`` 这类原模块 import），故第三条改为回指面里真实存在的
# ``SELF_STATEMENT_MAX_LEN``（常量回指，⑫-4），并在 ⑫-6 源码断言里钉住「无 ``_cs.agent``」。
from app.application import chat_settlement  # noqa: E402

CS_SETTLE_NAMES = (
    "_save_bio_update",
    "_generate_initial_bio",
    "_save_status_update",
    "_trigger_state_eval",
    "_bump_relationship",
    "_settle_relational_drive",
    "_release_drive_on_reply",
    "_settle_thought_pool_turn",
)
# 桩仍在 chat_service 侧、必须经 ``_cs`` 现取的三名（两名依赖 + 一个模块级常量）
B5C2_CS_ATTRS = ("async_session_factory", "spawn_background", "SELF_STATEMENT_MAX_LEN")


class _B5C2Res:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _B5C2DB:
    """最小假库：execute 回预置行、flush/commit 记账（形状沿用 ⑪ 的假库）"""

    def __init__(self, row, sink):
        self._row = row
        self._sink = sink

    async def execute(self, *_a, **_kw):
        return _B5C2Res(self._row)

    async def flush(self):
        self._sink.append("flush")

    async def commit(self):
        self._sink.append("commit")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


# ───────── ⑫-1 同名同对象 ＋ chat_service 只剩重导出 ─────────
def test_批5二_八个名字在chat_service与chat_settlement里是同一个对象():
    missing = [n for n in CS_SETTLE_NAMES
               if not hasattr(chat_service, n) or not hasattr(chat_settlement, n)]
    assert missing == [], f"重导出缺名字：{missing}"
    drifted = [n for n in CS_SETTLE_NAMES if getattr(chat_service, n) is not getattr(chat_settlement, n)]
    assert drifted == [], f"两侧不是同一对象（两份实现会漂移）：{drifted}"
    src = _CHAT_SERVICE_PY.read_text(encoding="utf-8")
    for name in CS_SETTLE_NAMES:
        assert getattr(chat_settlement, name).__module__ == chat_settlement.__name__, name
        assert not re.search(r"^(?:async )?def %s\b" % re.escape(name), src, re.M), (
            f"chat_service 里仍有 {name} 的原地定义")
    assert "from app.application.chat_settlement import" in src, (
        "chat_service 的具名重导出块不见了（_run_post_processing 与 tests 的 8 处原地桩会静默失效）"
    )


# ───────── ⑫-2 会话工厂：9 处桩一个都不许迁 ─────────
def test_批5二_会话工厂现取chat_service桩而非本模块真工厂(monkeypatch):
    calls: list = []

    def _boom():
        calls.append("factory")
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    assert asyncio.run(chat_settlement._save_bio_update(7, "自述正文", 3)) is None
    assert calls == ["factory"], "取库点没回指 chat_service（会静默退化成真查库）"


def test_批5二_空文本早退分支不取库(monkeypatch):
    """``if not bio_text: return`` 的既有早退：搬家后仍旧零副作用（9 处桩依赖的行为之一）。"""
    def _boom():
        raise AssertionError("空自述不该取库")

    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    assert asyncio.run(chat_settlement._save_bio_update(7, "", 3)) is None


# ───────── ⑫-3 后台任务提交点也回指 ─────────
def test_批5二_spawn_background现取chat_service桩(monkeypatch):
    """``_trigger_state_eval`` 是 8 个函数里唯一的后台任务提交点（3 处桩依赖）。"""
    coros: list = []

    def _spawn(coro):
        coros.append(coro)

    def _no_db():
        raise AssertionError("状态评估触发不该取库")

    monkeypatch.setattr(chat_service, "spawn_background", _spawn)
    monkeypatch.setattr(chat_service, "async_session_factory", _no_db)
    try:
        assert chat_settlement._trigger_state_eval(7, 3, "用户话", "AI话", "在吃饭") is None
        assert len(coros) == 1, "后台任务提交点没回指 chat_service"
        assert coros[0].cr_code.co_name == "update_character_states"
    finally:
        for c in coros:
            c.close()


# ───────── ⑫-4 常量回指：长度上限改在 chat_service 侧即生效 ─────────
def test_批5二_自述长度上限现取chat_service常量(monkeypatch):
    seen: list = []
    char = types.SimpleNamespace(self_statement=None)

    monkeypatch.setattr(chat_service, "async_session_factory",
                        lambda: _B5C2DB(char, seen))
    monkeypatch.setattr(chat_service, "SELF_STATEMENT_MAX_LEN", 7)
    asyncio.run(chat_settlement._save_bio_update(7, "字" * 30, 3))
    assert char.self_statement == "字" * 7, "写入点没经 _cs 取常量（长度上限漂移）"
    assert seen == ["flush", "commit"]


# ───────── ⑫-5 命名空间归属：既有局部工厂 ＋ 解析点在 chat_service ─────────
def test_批5二_bump_relationship仍用函数体内局部工厂而非chat_service桩(monkeypatch):
    """既有语义（搬前搬后一字一致）：它自己 import 工厂，chat_service 的桩碰不到它。
    谁将来「顺手」改成 ``_cs.async_session_factory``——那是**行为**改动，不是搬家，会红在这里。"""
    import app.db.database as db_mod

    hits: list = []

    def _local():
        hits.append("database")
        raise RuntimeError("db down")

    def _decoy():
        hits.append("chat_service")
        raise AssertionError("不该在 chat_service 命名空间解析")

    monkeypatch.setattr(db_mod, "async_session_factory", _local)
    monkeypatch.setattr(chat_service, "async_session_factory", _decoy)
    asyncio.run(chat_settlement._bump_relationship(7, 3))
    assert hits == ["database"], f"局部工厂语义被改写：{hits}"


def test_批5二_回指名的解析点在chat_service而非chat_settlement自身(monkeypatch):
    """代价面（⑪-2 的镜像）：即便有人在 chat_settlement 里落下同名模块属性，搬走的函数也只认
    chat_service 那一份。记下来防止将来「顺手改成本地裸名」——那不会红，只会让 9 处桩失效。"""
    from app.application import relational_drive_service

    hits: list = []

    def _via_cs():
        hits.append("cs")
        raise RuntimeError("db down")

    def _via_settlement():
        hits.append("settlement")
        raise AssertionError("不该在 chat_settlement 命名空间解析")

    monkeypatch.setattr(relational_drive_service, "shadow_enabled", lambda: True)
    monkeypatch.setattr(chat_service, "async_session_factory", _via_cs)
    monkeypatch.setattr(chat_settlement, "async_session_factory", _via_settlement, raising=False)
    asyncio.run(chat_settlement._settle_relational_drive(7, 3))
    assert hits == ["cs"], f"解析点跑偏：{hits}"


# ───────── ⑫-6 本组零兄弟互调（调用图未被接管） ─────────
def test_批5二_零兄弟互调且save_status_update不接管状态评估触发(monkeypatch):
    calls: list = []

    async def _spy(*_a, **_kw):
        calls.append("state_eval")

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service, "_trigger_state_eval", _spy)
    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    assert asyncio.run(chat_settlement._save_status_update(7, "两人在吃饭", 3)) is None
    assert calls == [], "_save_status_update 里多了一条状态评估触发（调用图漂移）"
    code = _b5c1_code(chat_settlement)
    for name in CS_SETTLE_NAMES:
        assert "_cs.%s(" % name not in code, f"意外出现兄弟回指 _cs.{name}（本组实测零兄弟互调）"


def test_批5二_初始自述去重集合随迁且命中即零取库(monkeypatch):
    """``_initial_bio_done``（进程内去重集）随 ``_generate_initial_bio`` 一起搬入，是本模块模块级状态；
    库内除本函数外无人引用 ⇒ chat_service 不许再留第二份（两份集合会让去重静默失效）。"""
    hits: list = []

    def _boom():
        hits.append("factory")
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    monkeypatch.setattr(chat_settlement, "_initial_bio_done", {4242})
    asyncio.run(chat_settlement._generate_initial_bio(4242, 3))
    assert hits == [], "去重集没随迁（命中仍去查库）"
    assert not hasattr(chat_service, "_initial_bio_done"), "chat_service 里还留着第二份集合"


# ───────── ⑫-7 chat_settlement 的依赖纪律（源码锚定） ─────────
def test_批5二_chat_settlement顶层不import_chat_service也不具名import被桩三名():
    code = _b5c1_code(chat_settlement)
    imports = _import_lines(code)
    top = [ln for ln in imports if not ln.startswith((" ", "\t"))]
    # ① 被桩三名一律不许在顶层具名 import（解析点一挪，9+3 处桩与两处写入点当场静默失效）
    for name in B5C2_CS_ATTRS:
        hit = [ln for ln in top if re.search(r"\b%s\b" % re.escape(name), ln)]
        assert hit == [], f"chat_settlement 顶层具名 import 了被桩名字 {name}：{hit}"
    # ② 原模块的顶层 import 一律不许出现（唯一例外＝_bump_relationship 体内既有的那条局部 import）
    for frag in ("from app.db.database import", "from app.utils.async_tasks import",
                 "from app.memory.extractor import"):
        assert [ln for ln in top if frag in ln] == [], f"顶层直接从原模块取依赖：{frag}"
    assert code.count("    from app.db.database import async_session_factory") == 1, (
        "_bump_relationship 的局部工厂 import 数量与本刀实测不符（逐字节搬家的锚点）"
    )
    # ③ chat_service 只能函数体内 import（提到顶层 ⇒ import 期成环）
    cs = [ln for ln in imports if re.search(r"\bchat_service\b", ln)]
    assert cs, "chat_settlement 没经 chat_service 现取被桩依赖"
    assert [ln for ln in cs if not ln.startswith((" ", "\t"))] == [], "chat_service import 被提到顶层"
    assert "from app.application.chat_service import" not in code, "不许具名 import chat_service 成员"
    # ④ 正向锚定：三个回指点一个都不能少，数量与本刀实测一致
    assert code.count("_cs.async_session_factory") == 7, code.count("_cs.async_session_factory")
    assert code.count("_cs.SELF_STATEMENT_MAX_LEN") == 2
    assert "_cs.spawn_background" in code
    assert code.count("from app.application import chat_service as _cs") == 7
    assert code.count("_cs.") >= 10, f"回指点不足：{code.count('_cs.')}"
    assert "_cs.agent" not in code, "本组不碰 chat_service.agent（出现即说明搬进了不该搬的东西）"


# ───────── ⑫-8 外部调用方仍可 import（两种导入顺序都不成环） ─────────
def test_批5二_结算组名字仍能按chat_service路径import且两种顺序都不成环():
    snippet = (
        "import app.application.%s as m; "
        "import app.api.chat, app.agent.graph, app.application.chat_store; "
        "from app.application.chat_service import (_save_bio_update, _generate_initial_bio, "
        "_save_status_update, _trigger_state_eval, _bump_relationship, _settle_relational_drive, "
        "_release_drive_on_reply, _settle_thought_pool_turn); "
        "import app.application.chat_service as cs, app.application.chat_settlement as st; "
        "assert all(getattr(cs, n) is getattr(st, n) for n in %r); "
        "assert not hasattr(cs, '_initial_bio_done')"
    )
    for first in ("chat_service", "chat_settlement"):
        probe = subprocess.run(
            [sys.executable, "-c", snippet % (first, list(CS_SETTLE_NAMES))],
            cwd=str(_BACKEND_PY), capture_output=True, text=True,
        )
        assert probe.returncode == 0, f"先导入 {first} 这条路炸了：{probe.stderr[-500:]}"


# ═══════════ ⑬ A20 批 5 第三刀（收口）：工具与降级下沉 application/chat_tooling ═══════════
#
# 第三刀把 chat_service 的**思考挡位 / 冷战拦截 / 情绪状态 / 降级续写 / 括号推理合并 /
# 可靠度核查调度 / 回合后处理** 7 个函数逐字节搬到 ``app/application/chat_tooling.py``，
# chat_service 只剩 ``_run_agent_core`` 与三个对外端点（A20 的收口刀）。刀法沿用 ⑪⑫：
# **模块属性回指** ⇒ tests 的打桩面零迁移。本组被回指的 chat_service 命名空间名字比前两刀宽——
# 单是 ``_run_post_processing`` 一处就引用了会话工厂、后台任务、记忆提取、第二刀结算八名与
# ``_gen_image_flow``（合计 15 个）；实测按 chat_service 路径打桩的分布：
# ``async_session_factory`` 18 处 / ``_run_agent_core`` 17 处 / ``_run_post_processing`` 13 处 /
# ``spawn_background`` 4 处 / ``_trigger_state_eval`` 2 处 / ``add_chat_memory_extraction``、
# ``_generate_initial_bio``、``_bump_relationship``、``_gen_image_flow`` 各 1 处。
#
# 与任务书的一条实测差别（⑬-6／⑬-8 钉住）：``_run_agent_core`` 与模块级 ``agent`` 确实留在
# chat_service，但**本组 7 个函数对它没有任何调用点**（``_run_post_processing`` 末尾那句
# 「核验 _run_agent_core 收尾存在」是注释不是调用）⇒ chat_tooling 里既不会出现 ``_cs._run_agent_core``
# 也不会出现 ``_cs.agent``。那 17＋1 处桩依赖的是三个**对外端点**在 chat_service 自身命名空间解析
# 裸名，这一条边搬家没碰：⑬-6 直接把 ``send_and_receive`` 驱起来验桩仍然穿透（等价于任务书要求的
# 「``_run_agent_core`` 打桩穿透」，且比源码字面断言更硬）。
#
# 另一条：``_PUNCT_ONLY_RE`` / ``_DEGRADED_REASONING_MIN`` / ``_DEGRADED_CONTINUATION_TIMEOUT_S``
# 三个常量**留在 chat_service**（``_run_agent_core`` 也在用前两个，与 ⑫-4 的
# ``SELF_STATEMENT_MAX_LEN`` 同法），本组只回指取 ⇒ ⑬-4 钉住「改 chat_service 侧常量立即生效」。
from app.application import chat_tooling  # noqa: E402

CS_TOOLING_NAMES = (
    "_load_reasoning_level",
    "_cold_war_block",
    "_resolve_emotional_state",
    "_try_degraded_continuation",
    "_merge_bracket_reasoning",
    "_schedule_reliability_fact_check",
    "_run_post_processing",
)
# 桩仍在 chat_service 侧、必须经 ``_cs`` 现取的名字（3 名直接依赖 + 结算八名 + 生图 + 兄弟 + 两常量）
B5C3_CS_ATTRS = (
    "async_session_factory", "spawn_background", "add_chat_memory_extraction",
    "_save_bio_update", "_save_status_update", "_generate_initial_bio", "_bump_relationship",
    "_settle_relational_drive", "_release_drive_on_reply", "_settle_thought_pool_turn",
    "_trigger_state_eval", "_gen_image_flow", "_schedule_reliability_fact_check",
    "_PUNCT_ONLY_RE", "_DEGRADED_CONTINUATION_TIMEOUT_S",
)
# 本组实测零调用点／零引用 ⇒ chat_tooling 既不具名绑定、也不许回指（出现即说明搬进了不该搬的东西）
B5C3_NOT_IN_TOOLING = ("_run_agent_core", "agent")


class _B5C3Res:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return types.SimpleNamespace(all=lambda: [])


class _B5C3DB:
    """最小假库：execute 一律回同一预置行（角色查询与最近对话查询共用）"""

    def __init__(self, row=None):
        self._row = row

    async def execute(self, *_a, **_kw):
        return _B5C3Res(self._row)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _B5C3AiDB:
    """AI 消息落库假库：add/flush/commit 记账，refresh 补 id 与 created_at"""

    def __init__(self, sink):
        self._sink = sink

    def add(self, obj):
        self._sink.append("add")

    async def flush(self):
        self._sink.append("flush")

    async def commit(self):
        self._sink.append("commit")

    async def refresh(self, obj):
        obj.id = 9001
        obj.created_at = _B5C1Ts()
        self._sink.append("refresh")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


# ───────── ⑬-1 同名同对象 ＋ chat_service 只剩具名重导出 ─────────
def test_批5三_七个名字在chat_service与chat_tooling里是同一个对象():
    missing = [n for n in CS_TOOLING_NAMES
               if not hasattr(chat_service, n) or not hasattr(chat_tooling, n)]
    assert missing == [], f"重导出缺名字：{missing}"
    drifted = [n for n in CS_TOOLING_NAMES if getattr(chat_service, n) is not getattr(chat_tooling, n)]
    assert drifted == [], f"两侧不是同一对象（两份实现会漂移）：{drifted}"
    src = _CHAT_SERVICE_PY.read_text(encoding="utf-8")
    for name in CS_TOOLING_NAMES:
        assert getattr(chat_tooling, name).__module__ == chat_tooling.__name__, name
        assert not re.search(r"^(?:async )?def %s\b" % re.escape(name), src, re.M), (
            f"chat_service 里仍有 {name} 的原地定义")
    assert "from app.application.chat_tooling import" in src, (
        "chat_service 的具名重导出块不见了（_run_agent_core 与三端点的裸名调用点、"
        "以及 tests 按 chat_service 路径打的桩会静默失效）"
    )


# ───────── ⑬-2 收口断言：chat_service 只剩主干与三端点 ─────────
def test_批5三_收口后chat_service只剩run_agent_core与三个对外端点():
    src = _CHAT_SERVICE_PY.read_text(encoding="utf-8")
    top = [n.name for n in ast.parse(src).body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert top == ["_run_agent_core", "send_and_receive", "send_and_receive_chunked", "continue_chat"], (
        f"chat_service 的模块级函数面与本刀收口预期不符：{top}")
    for name in CS_TOOLING_NAMES:
        assert not re.search(r"^(?:async )?def %s\b" % re.escape(name), src, re.M), name
    for name in ("_run_agent_core", "send_and_receive", "send_and_receive_chunked", "continue_chat"):
        assert re.search(r"^async def %s\b" % re.escape(name), src, re.M), f"{name} 的定义丢了"


# ───────── ⑬-3 会话工厂：18 处桩一个都不许迁（两个取库点各一条） ─────────
def test_批5三_load_reasoning_level现取chat_service会话工厂(monkeypatch):
    calls: list = []

    def _boom():
        calls.append("factory")
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    assert asyncio.run(chat_tooling._load_reasoning_level(7)) == 0, "挡位读取失败一律回 0（既有语义）"
    assert calls == ["factory"], "取库点没回指 chat_service（会静默退化成真查库）"


def test_批5三_try_degraded_continuation现取chat_service会话工厂(monkeypatch):
    calls: list = []

    def _boom():
        calls.append("factory")
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service, "async_session_factory", _boom)
    assert asyncio.run(chat_tooling._try_degraded_continuation(1, 1, 13, "想了很多")) == ""
    assert calls == ["factory"], "降级续写取库点没回指 chat_service"


# ───────── ⑬-4 常量回指：补写超时与纯标点判据都现取 chat_service ─────────
def test_批5三_降级续写的超时与纯标点判据现取chat_service常量(monkeypatch):
    """三个常量留在 chat_service（_run_agent_core 也在用），搬家后仍旧「改一处两侧同时生效」。"""
    import app.agent.llm_client as llm_mod

    char = types.SimpleNamespace(name="小慧", personality="温柔", chat_style="口语")
    monkeypatch.setattr(chat_service, "async_session_factory", lambda: _B5C3DB(char))

    async def _slow(*_a, **_k):
        await asyncio.sleep(0.05)
        return "这就起身，你盯着我点。"

    monkeypatch.setattr(llm_mod, "chat_completion", _slow)
    monkeypatch.setattr(chat_service, "_DEGRADED_CONTINUATION_TIMEOUT_S", 0.001)
    assert asyncio.run(chat_tooling._try_degraded_continuation(1, 1, 13, "想了很多")) == "", (
        "补写超时没经 _cs 取常量（25 秒上限漂了）")

    async def _fast(*_a, **_k):
        return "这就起身，你盯着我点。"

    monkeypatch.setattr(llm_mod, "chat_completion", _fast)
    monkeypatch.setattr(chat_service, "_DEGRADED_CONTINUATION_TIMEOUT_S", 25.0)
    monkeypatch.setattr(chat_service, "_PUNCT_ONLY_RE", re.compile(r".*", re.S))
    assert asyncio.run(chat_tooling._try_degraded_continuation(1, 1, 13, "想了很多")) == "", (
        "纯标点判据没经 _cs 取常量（真正文会被误放行）")


# ───────── ⑬-5 回合后处理：15 个回指名全在 chat_service 解析 ─────────
def test_批5三_run_post_processing的后台任务与结算与生图全部现取chat_service桩(monkeypatch):
    """一条链钉死本刀最粗的接缝：``spawn_background`` / ``add_chat_memory_extraction`` /
    结算八名 / ``_gen_image_flow`` / 兄弟 ``_schedule_reliability_fact_check``；顺带钉住
    「零取库」与返回 None 的既有形状。"""
    hits: list = []
    coros: list = []

    def _factory():
        raise AssertionError("公共收尾不该自己取库（在场时间刷新只建协程、由 spawn 关闭）")

    def _spawn(coro, **_kw):
        coros.append(coro)

    async def _noop():
        return None

    def _rec(key):
        """同步记录器：``spawn_background`` 只提交协程、从不 await，故按「调用点」记账
        （＝名字在 chat_service 命名空间被解析并被调用），返回 no-op 协程交给哨兵 spawn。"""
        def _f(*a, **kw):
            hits.append((key, a, kw) if kw else (key, a))
            return _noop()
        return _f

    async def _save_bio_update(*a):
        hits.append(("save_bio", a))

    async def _save_status_update(*a):
        hits.append(("save_status", a))

    _generate_initial_bio = _rec("initial_bio")
    add_chat_memory_extraction = _rec("mem_extract")
    _bump_relationship = _rec("bump")
    _settle_relational_drive = _rec("drive")
    _release_drive_on_reply = _rec("release")
    _settle_thought_pool_turn = _rec("thought")
    _gen_image_flow = _rec("gen_image")

    def _trigger_state_eval(*a):
        hits.append(("state_eval", a))

    def _sibling(*a):
        hits.append(("reliability", a))

    for name, stub in (("async_session_factory", _factory), ("spawn_background", _spawn),
                       ("add_chat_memory_extraction", add_chat_memory_extraction),
                       ("_save_bio_update", _save_bio_update),
                       ("_save_status_update", _save_status_update),
                       ("_generate_initial_bio", _generate_initial_bio),
                       ("_bump_relationship", _bump_relationship),
                       ("_settle_relational_drive", _settle_relational_drive),
                       ("_release_drive_on_reply", _release_drive_on_reply),
                       ("_settle_thought_pool_turn", _settle_thought_pool_turn),
                       ("_trigger_state_eval", _trigger_state_eval),
                       ("_gen_image_flow", _gen_image_flow),
                       ("_schedule_reliability_fact_check", _sibling)):
        monkeypatch.setattr(chat_service, name, stub)
    # 代价面（⑪-2／⑫-5 的镜像）：即便有人在 chat_tooling 里落下同名模块属性，也只认 chat_service 那份
    for name in ("_bump_relationship", "_gen_image_flow", "spawn_background"):
        monkeypatch.setattr(chat_tooling, name,
                            lambda *_a, **_k: hits.append(("DECOY",)), raising=False)

    try:
        out = asyncio.run(chat_tooling._run_post_processing(
            9, 3, 7, "用户话", {}, "AI话", 1001, 1002,
            reliability=True, gen_prompt="画胖点", img_text=None))
    finally:
        for c in coros:
            c.close()
    assert out is None, "公共收尾返回值形状漂移"
    by = {h[0]: h[1:] for h in hits}
    assert "DECOY" not in by, "解析点跑到 chat_tooling 命名空间（chat_service 的桩会静默失效）"
    for want in ("save_bio", "save_status", "initial_bio", "mem_extract", "bump", "drive",
                 "release", "thought", "state_eval", "reliability", "gen_image"):
        assert want in by, f"{want} 没在 chat_service 命名空间解析：{sorted(by)}"
    assert by["save_bio"] == ((7, None, 3),), by["save_bio"]
    assert by["save_status"] == ((7, None, 3),), by["save_status"]
    assert by["mem_extract"][0] == (9, 7, 3, "用户话", "AI话"), by["mem_extract"]
    assert by["mem_extract"][1] == {"source_id": 1002}, by["mem_extract"]
    assert by["initial_bio"] == ((7, 3),)
    assert by["bump"] == ((7, 3),)
    assert by["drive"] == ((7, 3),)
    assert by["release"] == ((7, 3, 9),)
    assert by["thought"] == ((7, 3, 9),)
    assert by["state_eval"] == ((7, 3, "用户话", "AI话", None),), by["state_eval"]
    assert by["reliability"] == ((7, 3, "用户话", "AI话"),), "兄弟调用没走 _cs（reliability=True 未透传）"
    assert by["gen_image"] == ((3, 7, 9, "画胖点", None),), by["gen_image"]
    assert len(coros) >= 8, f"后台任务提交点少于本组实测：{len(coros)}"


# ───────── ⑬-6 _run_agent_core 的 17 处桩仍打在 chat_service（端点现取） ─────────
def test_批5三_run_agent_core桩仍按chat_service路径被端点现取(monkeypatch):
    """收口后主干没动：三个对外端点仍在 chat_service 里裸名解析 ``_run_agent_core``，
    所以 17 处 ``setattr(cs, "_run_agent_core", …)`` 一行都不必迁。这里把 ``send_and_receive``
    真驱一次（core 返回 None ⇒ 冷战拦截早退，零取库）。"""
    seen: list = []

    async def _persist(*_a, **_kw):
        return (11, {"id": 11})

    async def _core(*_a, **kw):
        seen.append(kw)
        return None

    def _no_db():
        raise AssertionError("core 早退时端点不该取库")

    monkeypatch.setattr(chat_service, "_persist_user_message", _persist)
    monkeypatch.setattr(chat_service, "_run_agent_core", _core)
    monkeypatch.setattr(chat_service, "async_session_factory", _no_db)
    out = asyncio.run(chat_service.send_and_receive(9, 2, 3, "在吗"))
    assert len(seen) == 1 and seen[0]["user_timer"] is True and seen[0]["channel_hint"] is None, seen
    assert out == {"ai_message": None, "memories_updated": False, "cold_war": True}, "早退返回形状漂移"


# ───────── ⑬-7 端点 → 搬走的公共收尾：重导出块保住那 13 处桩 ─────────
def test_批5三_端点经chat_service现取搬走的run_post_processing(monkeypatch):
    """另一半方向：端点里的裸名 ``_run_post_processing`` 现在指向 chat_tooling 的实现，
    桩仍打在 chat_service ⇒ 落库、事件、推送、收尾四步的顺序与入参一字未改。"""
    calls: list = []
    sink: list = []

    async def _persist(*_a, **_kw):
        return (11, {"id": 11})

    async def _core(*_a, **_kw):
        return {"final_state": {"reasoning": "想说一句", "status_update": "在吃饭"},
                "final_text": "在的。", "gen_prompt": None, "img_text": None}

    async def _post(*a, **kw):
        calls.append((a, kw))

    async def _evt(*_a, **_kw):
        sink.append("event")

    async def _notify(*_a, **_kw):
        sink.append("notify")

    monkeypatch.setattr(chat_service, "_persist_user_message", _persist)
    monkeypatch.setattr(chat_service, "_run_agent_core", _core)
    monkeypatch.setattr(chat_service, "_run_post_processing", _post)
    monkeypatch.setattr(chat_service, "append_domain_event", _evt)
    monkeypatch.setattr(chat_service, "_push_user_notify", _notify)
    monkeypatch.setattr(chat_service, "async_session_factory", lambda: _B5C3AiDB(sink))
    out = asyncio.run(chat_service.send_and_receive(9, 2, 3, "在吗"))
    assert out["ai_message"]["id"] == 9001 and out["ai_message"]["content"] == "在的。", out
    assert out["ai_message"]["created_at"] == "2026-10-02T03:04:05", out
    assert out["memories_updated"] is False
    assert sink[:2] == ["add", "flush"] and "event" in sink and "notify" in sink, sink
    assert len(calls) == 1, "端点没在 chat_service 命名空间解析搬走的公共收尾"
    assert calls[0][0] == (9, 2, 3, "在吗", {"reasoning": "想说一句", "status_update": "在吃饭"},
                           "在的。", 9001, 11), calls[0][0]
    assert calls[0][1] == {"reliability": True, "gen_prompt": None, "img_text": None}, calls[0][1]


# ───────── ⑬-8 chat_tooling 的依赖纪律（源码锚定） ─────────
def test_批5三_chat_tooling顶层不import_chat_service也不具名import被桩十五名():
    code = _b5c1_code(chat_tooling)
    imports = _import_lines(code)
    top = [ln for ln in imports if not ln.startswith((" ", "\t"))]
    # ① 被桩 15 名一律不许在顶层具名 import（解析点一挪，18+4+2+1×N 处桩当场静默失效）
    for name in B5C3_CS_ATTRS:
        hit = [ln for ln in top if re.search(r"\b%s\b" % re.escape(name), ln)]
        assert hit == [], f"chat_tooling 顶层具名 import 了被桩名字 {name}：{hit}"
    # ② 原模块的顶层 import 不许出现（会话工厂／后台任务／记忆提取／结算都只能经 _cs）
    for frag in ("from app.db.database import", "from app.utils.async_tasks import",
                 "from app.memory import", "from app.application.chat_settlement import"):
        assert [ln for ln in top if frag in ln] == [], f"顶层直接从原模块取被桩依赖：{frag}"
    # ③ chat_service 只能函数体内 import（提到顶层 ⇒ import 期成环）
    cs = [ln for ln in imports if re.search(r"\bchat_service\b", ln)]
    assert cs, "chat_tooling 没经 chat_service 现取被桩依赖"
    assert [ln for ln in cs if not ln.startswith((" ", "\t"))] == [], "chat_service import 被提到顶层"
    assert "from app.application.chat_service import" not in code, "不许具名 import chat_service 成员"
    # ④ 正向锚定：三个回指函数与回指点数量同本刀实测一致；本组零调用点的两名不许出现
    assert code.count("from app.application import chat_service as _cs") == 3, (
        "回指函数数量与本刀实测不符（_load_reasoning_level / _try_degraded_continuation / "
        "_run_post_processing）")
    assert code.count("_cs.") >= 20, f"回指点不足：{code.count('_cs.')}"
    assert code.count("_cs.spawn_background") == 14, code.count("_cs.spawn_background")
    assert code.count("_cs.async_session_factory") == 3, code.count("_cs.async_session_factory")
    for name in B5C3_NOT_IN_TOOLING:
        assert "_cs.%s" % name not in code, f"本组对 {name} 零调用点，出现即搬错了东西"
        assert not hasattr(chat_tooling, name), f"chat_tooling 不该绑定 {name}（端点的桩解析点会挪窝）"
    # ⑤ 三个降级常量留在 chat_service，本模块只回指取（不许有第二份定义）
    for const in ("_PUNCT_ONLY_RE", "_DEGRADED_REASONING_MIN", "_DEGRADED_CONTINUATION_TIMEOUT_S"):
        assert not hasattr(chat_tooling, const), f"chat_tooling 里出现第二份 {const}"
        assert hasattr(chat_service, const), f"{const} 被剪掉了（_run_agent_core 与 _cs 回指都要它）"


# ───────── ⑬-9 外部调用方仍可 import（两种导入顺序都不成环） ─────────
def test_批5三_工具组名字仍能按chat_service路径import且两种顺序都不成环():
    snippet = (
        "import app.application.%s as m; "
        "import app.api.chat, app.application.chat_store, app.application.chat_settlement; "
        "from app.application.chat_service import (_load_reasoning_level, _cold_war_block, "
        "_resolve_emotional_state, _try_degraded_continuation, _merge_bracket_reasoning, "
        "_schedule_reliability_fact_check, _run_post_processing, _gen_image_flow); "
        "import app.application.chat_service as cs, app.application.chat_tooling as tl; "
        "assert all(getattr(cs, n) is getattr(tl, n) for n in %r); "
        "assert not hasattr(tl, '_run_agent_core') and hasattr(cs, '_PUNCT_ONLY_RE')"
    )
    for first in ("chat_service", "chat_tooling"):
        probe = subprocess.run(
            [sys.executable, "-c", snippet % (first, list(CS_TOOLING_NAMES))],
            cwd=str(_BACKEND_PY), capture_output=True, text=True,
        )
        assert probe.returncode == 0, f"先导入 {first} 这条路炸了：{probe.stderr[-500:]}"


# ───────── A22 第一刀：system.py 用量/面板块 ↔ usage_service.py 接缝 ─────────
A22C1_FUNC_NAMES = (
    "get_llm_usage",
    "_usage_metrics_blank",
    "_usage_window_bounds",
    "_usage_window_descriptor",
    "_read_usage_window",
    "_aggregate_usage_rows",
    "_usage_emit",
    "usage_report",
    "_panel_days_or_raise",
    "_panel_estimated_segment",
    "_panel_money_segment",
    "_blank_usage_panel",
    "usage_panel",
    "update_llm_usage_limit",
    "_unknown_usage",
    "_empty_breakdown",
    "_clamp_breakdown_samples",
    "_aggregate_section_breakdown",
    "_price_range_for",
    "_cost_estimate",
)

A22C1_CONST_NAMES = (
    "_USAGE_UNTAGGED",
    "_USAGE_UNKNOWN",
    "_PANEL_DEFAULT_DAYS",
    "_PANEL_MIN_DAYS",
    "_PANEL_MAX_DAYS",
    "_CLIP_ROUTE",
    "_CLIP_WINDOW_HOURS",
    "_USAGE_ROUTE",
    "_SECTION_ROUTE",
    "BREAKDOWN_DEFAULT_SAMPLES",
    "BREAKDOWN_MAX_SAMPLES",
    "_BREAKDOWN_TURN_SECTIONS",
    "_TOKEN_PRICE_RANGES",
    "_PRICE_CURRENCY",
    "_PER_MILLION_TOKENS",
)

def _seam_src_path(module_name: str):
    """接缝守卫专用：按模块名解析源文件，禁止写死本机绝对路径（CI 上无 D 盘）。"""
    import importlib
    from pathlib import Path
    return Path(importlib.import_module(module_name).__file__).resolve()


A22C1_SYSTEM_PY = _seam_src_path("app.application.system")
A22C1_USAGE_PY = _seam_src_path("app.application.usage_service")


def _a22c1_system_src():
    return A22C1_SYSTEM_PY.read_text(encoding="utf-8")


def _a22c1_usage_src():
    return A22C1_USAGE_PY.read_text(encoding="utf-8")


def test_A22第一刀_20函数同对象且常量随迁():
    from app.application import system as sy, usage_service as us

    for name in A22C1_FUNC_NAMES:
        assert hasattr(sy, name) and hasattr(us, name), name
        assert getattr(sy, name) is getattr(us, name), name
    for name in A22C1_CONST_NAMES:
        assert hasattr(sy, name) and hasattr(us, name), name
        if name == "_TOKEN_PRICE_RANGES":
            assert sy._TOKEN_PRICE_RANGES is us._TOKEN_PRICE_RANGES
        else:
            assert getattr(sy, name) == getattr(us, name), name


def test_A22第一刀_源码锚定原定义消失且usage顶层不回指():
    system_src = _a22c1_system_src()
    for name in A22C1_FUNC_NAMES:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, system_src, re.MULTILINE) is None, name

    usage_src = _a22c1_usage_src()
    tree = ast.parse(usage_src)
    for node in tree.body:
        assert not (
            isinstance(node, ast.ImportFrom)
            and node.module == "app.application.system"
        ), "usage_service.py 顶层不得 import system"


def test_A22第一刀_api_system调用路径仍可解析():
    import app.api.system as api_system  # noqa: F401
    from app.application import system as sy

    assert api_system.__name__ == "app.api.system"
    for name in ("get_llm_usage", "usage_panel", "update_llm_usage_limit"):
        assert hasattr(sy, name), name


# ─────── A22 第二刀：system.py 配置/探针块 ↔ system_config_service.py 接缝 ───────
A22C2_FUNC_NAMES = (
    "_task_cfg_payload",
    "_get_task_cfg",
    "get_api_config",
    "update_api_config",
    "get_server_api_config",
    "update_server_api_config",
    "get_task_llm_catalog",
    "get_task_api_config",
    "update_task_api_config",
    "get_server_task_api_config",
    "update_server_task_api_config",
    "test_api_connection",
    "_probe_chat",
    "_probe_models_list",
    "_probe_image",
    "_probe_image_dashscope",
    "_probe_speech",
    "_status_code_of",
    "_classify_probe_error",
    "get_image_gen_server_config",
    "update_image_gen_server_config",
    "get_vlm_server_config",
    "update_vlm_server_config",
    "get_speech_server_config",
    "update_speech_server_config",
    "speech_preview",
)

A22C2_CONST_NAMES = (
    "_API_CFG_FIELDS",
    "_VLM_CFG_FIELDS",
    "_IMAGE_CFG_FIELDS",
    "_SPEECH_CFG_FIELDS",
    "_MODALITY_LABELS",
)

# 刻意**留在** system.py 的名字（防「顺手多搬一刀」把它们也挪走）
A22C2_STAY_NAMES = (
    "_logger",
    "_is_private_ipv4",
    "_get_lan_ip",
    "_require_admin",
    "_require_server_admin",
    "_cfg_snapshot",
    "_audit",
    "_changelog_title",
    "_parse_changelog",
    "_load_backup_module",
    "_backup_info",
    "system_status_public",
    "system_status",
    "get_updates",
    "get_feature_flags",
    "update_feature_flag",
    "read_account_context_budget_tier",
    "set_context_budget_tier",
    "get_context_budget",
    "trigger_backup",
    "download_backup",
)

A22C2_SYSTEM_PY = _seam_src_path("app.application.system")
A22C2_CFG_PY = _seam_src_path("app.application.system_config_service")


def _a22c2_system_src():
    return A22C2_SYSTEM_PY.read_text(encoding="utf-8")


def _a22c2_cfg_src():
    return A22C2_CFG_PY.read_text(encoding="utf-8")


def test_A22第二刀_26函数同对象且常量随迁():
    from app.application import system as sy, system_config_service as sc

    assert len(A22C2_FUNC_NAMES) == 26
    for name in A22C2_FUNC_NAMES:
        assert hasattr(sy, name) and hasattr(sc, name), name
        assert getattr(sy, name) is getattr(sc, name), name
    for name in A22C2_CONST_NAMES:
        assert hasattr(sy, name) and hasattr(sc, name), name
        if name == "_MODALITY_LABELS":
            assert sy._MODALITY_LABELS is sc._MODALITY_LABELS
        else:
            assert getattr(sy, name) == getattr(sc, name), name


def test_A22第二刀_源码锚定原定义消失且新模块只在函数内回指():
    system_src = _a22c2_system_src()
    for name in A22C2_FUNC_NAMES:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, system_src, re.MULTILINE) is None, name
    for name in A22C2_CONST_NAMES:
        assert re.search(r"^%s\s*=" % re.escape(name), system_src, re.MULTILINE) is None, name

    cfg_src = _a22c2_cfg_src()
    tree = ast.parse(cfg_src)
    assert any(isinstance(n, ast.FunctionDef) or isinstance(n, ast.AsyncFunctionDef)
               for n in tree.body), "system_config_service.py 顶层应有函数定义"
    for node in tree.body:
        assert not (
            isinstance(node, ast.ImportFrom)
            and node.module == "app.application.system"
        ), "system_config_service.py 顶层不得 import system（会与重导出成环）"
    # 跨块回指必须存在（_require_admin/_require_server_admin/_cfg_snapshot/_audit 仍在 system.py）
    assert "_sys." in cfg_src
    assert "from app.application import system as _sys" in cfg_src
    # 且回指只能出现在函数体内（缩进层）——顶层出现即成环
    for line in cfg_src.split("\n"):
        if line.strip() == "from app.application import system as _sys":
            assert line.startswith("    "), "顶层回指：" + line


def test_A22第二刀_api_system调用路径仍可解析():
    import app.api.system as api_system  # noqa: F401
    from app.application import system as sy

    assert api_system.__name__ == "app.api.system"
    for name in ("get_api_config", "update_api_config", "test_api_connection",
                 "get_speech_server_config", "speech_preview"):
        assert hasattr(sy, name), name


def test_A22第二刀_留在system的名字未被搬走():
    from app.application import system as sy, system_config_service as sc

    for name in A22C2_STAY_NAMES:
        assert hasattr(sy, name), name
    for name in ("_require_admin", "_require_server_admin", "_cfg_snapshot", "_audit"):
        assert not hasattr(sc, name), name + " 不应出现在新模块（它留在 system.py）"


# ── A22 第三刀：system.py feature flag 块 / 上下文预算块 ↔ 两个新模块接缝 ──
A22C3_FF_NAMES = ("get_feature_flags", "update_feature_flag")
A22C3_CB_NAMES = ("read_account_context_budget_tier", "set_context_budget_tier",
                  "get_context_budget")

A22C3_SYSTEM_PY = _seam_src_path("app.application.system")
A22C3_FF_PY = _seam_src_path("app.application.feature_flag_service")
A22C3_CB_PY = _seam_src_path("app.application.context_budget_service")


def _a22c3_src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _A22C3BoomDb:
    """execute 即抛：让 get_context_budget 走 fail-open，只用内存段（零真库、零 IO）。"""

    async def execute(self, *_a, **_k):
        raise RuntimeError("db down")


def test_A22第三刀_5函数两侧同一对象():
    from app.application import system as sy
    from app.application import feature_flag_service as ff
    from app.application import context_budget_service as cb

    assert len(A22C3_FF_NAMES) + len(A22C3_CB_NAMES) == 5
    for name in A22C3_FF_NAMES:
        assert hasattr(sy, name) and hasattr(ff, name), name
        assert getattr(sy, name) is getattr(ff, name), name
    for name in A22C3_CB_NAMES:
        assert hasattr(sy, name) and hasattr(cb, name), name
        assert getattr(sy, name) is getattr(cb, name), name


def test_A22第三刀_cost_estimate桩经system穿透到context_budget_service(monkeypatch):
    """本刀最关键：桩打在 system 上，调用点已搬到新模块，必须仍走桩（否则静默退化成真算）。"""
    from app.application import system as sy
    from app.application import context_budget_service as cb

    calls: list = []

    def _stub(budget_tokens, model, provider):
        calls.append((budget_tokens, model, provider))
        return {"status": "stubbed"}

    monkeypatch.setattr(sy, "_cost_estimate", _stub)
    payload = asyncio.run(cb.get_context_budget(7, _A22C3BoomDb()))

    assert calls, "system._cost_estimate 桩没被走到：新模块把名字解析到了自己的命名空间"
    assert payload["cost_estimate"] == {"status": "stubbed"}
    assert calls[0][0] == payload["effective_budget_tokens"], "估算基数仍是生效预算"
    assert payload["error"], "查库失败要 fail-open 标记，不抛 500"


def test_A22第三刀_TOKEN_PRICE_RANGES桩经system穿透到context_budget_service(monkeypatch):
    """同法验价目表：桩仍是 system 上的模块属性（usage_service 内 _sys._TOKEN_PRICE_RANGES 现取）。"""
    from app.application import system as sy
    from app.application import context_budget_service as cb

    base = asyncio.run(cb.get_context_budget(7, _A22C3BoomDb()))
    assert base["cost_estimate"]["status"] == "unavailable"
    assert base["cost_estimate"]["reason"] == "no_price_table", "价目表默认必须为空"

    monkeypatch.setattr(sy, "_TOKEN_PRICE_RANGES", {"default": (0.5, 1.0)})
    got = asyncio.run(cb.get_context_budget(7, _A22C3BoomDb()))
    cost = got["cost_estimate"]
    assert cost["status"] == "ok" and cost["price_source"] == "default"
    assert cost["per_million_low"] == 0.5 and cost["per_million_high"] == 1.0
    assert cost["budget_tokens"] == got["effective_budget_tokens"]


def test_A22第三刀_源码锚定原定义消失且新模块只在函数内回指():
    system_src = _a22c3_src(A22C3_SYSTEM_PY)
    for name in A22C3_FF_NAMES + A22C3_CB_NAMES:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, system_src, re.MULTILINE) is None, name

    for path in (A22C3_FF_PY, A22C3_CB_PY):
        src = _a22c3_src(path)
        tree = ast.parse(src)
        assert any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   for n in tree.body), path.name + " 顶层应有函数定义"
        for node in tree.body:
            assert not (
                isinstance(node, ast.ImportFrom)
                and node.module == "app.application.system"
            ), path.name + " 顶层不得 import system（会与重导出成环）"
        assert "_sys." in src, path.name + " 应有 _sys. 回指调用"
        assert "from app.application import system as _sys" in src
        # 回指 import 只能出现在函数体内（缩进层）——顶层出现即成环
        for line in src.split("\n"):
            if line.strip() == "from app.application import system as _sys":
                assert line.startswith("    "), path.name + " 顶层回指：" + line


def test_A22第三刀_api_system调用路径仍可解析():
    import app.api.system as api_system  # noqa: F401
    from app.application import system as sy

    assert api_system.__name__ == "app.api.system"
    for name in A22C3_FF_NAMES + A22C3_CB_NAMES:
        assert hasattr(sy, name), name


# ── A22 第四刀（收尾）：状态/公告块 + 备份块 ↔ 两个新模块；system.py 自此为薄壳 ──
A22C4_STATUS_NAMES = (
    "_is_private_ipv4",
    "_get_lan_ip",
    "system_status_public",
    "system_status",
    "get_updates",
    "_changelog_title",
    "_parse_changelog",
)
A22C4_BACKUP_NAMES = (
    "_load_backup_module",
    "_backup_info",
    "trigger_backup",
    "download_backup",
)
# 刻意**留在** system.py 的四个共享辅助（薄壳后本文件只应有这四个 def）
A22C4_HELPER_NAMES = ("_require_admin", "_require_server_admin", "_cfg_snapshot", "_audit")

A22C4_SYSTEM_PY = _seam_src_path("app.application.system")
A22C4_STATUS_PY = _seam_src_path("app.application.system_status_service")
A22C4_BACKUP_PY = _seam_src_path("app.application.system_backup_service")


def _a22c4_src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _a22c4_top_defs(src: str) -> list:
    tree = ast.parse(src)
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_A22第四刀_11函数两侧同一对象():
    from app.application import system as sy
    from app.application import system_status_service as ss
    from app.application import system_backup_service as sb

    assert len(A22C4_STATUS_NAMES) + len(A22C4_BACKUP_NAMES) == 11
    for name in A22C4_STATUS_NAMES:
        assert hasattr(sy, name) and hasattr(ss, name), name
        assert getattr(sy, name) is getattr(ss, name), name
    for name in A22C4_BACKUP_NAMES:
        assert hasattr(sy, name) and hasattr(sb, name), name
        assert getattr(sy, name) is getattr(sb, name), name


def test_A22第四刀_load_backup_module桩打在system上仍被trigger_backup走到(monkeypatch, tmp_path):
    """本刀命门：桩仍打在 system 上（tests/test_backup_api.py:58），调用点却已搬进备份模块。

    回指若写成顶层 import 或在自身命名空间解析裸名，该桩会**静默失效**——测试不红，而是真按
    文件路径加载 scripts/backup.py 并往生产 data/ 写 zip。故这里直接调新模块的函数验桩。
    """
    from app.application import system as sy
    from app.application import system_backup_service as sb

    loads: list = []
    audits: list = []

    class _Mod:
        BACKUP_ROOT = str(tmp_path)

        @staticmethod
        def backup_day_key():
            return "20261002"

        @staticmethod
        def do_backup():
            (tmp_path / "20261002.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    def _load():
        loads.append("load")
        return _Mod

    async def _pass_gate(user_id, lang="zh"):
        return None

    async def _audit(db, actor_user_id, action, target=None, before=None, after=None):
        audits.append((action, after))

    monkeypatch.setattr(sy, "_load_backup_module", _load)
    monkeypatch.setattr(sy, "_require_server_admin", _pass_gate)
    monkeypatch.setattr(sy, "_audit", _audit)

    info = asyncio.run(sb.trigger_backup(1, "zh"))
    assert loads == ["load"], (
        "system._load_backup_module 桩没被走到：备份模块把名字解析到了自己的命名空间")
    assert info["status"] == "ok" and info["path"] == "20261002.zip"
    assert info["size"] > 0 and info["created_at"], info

    # _sys._audit 回指同样必须经 system（审计动作不因为搬家就丢掉）
    assert audits and audits[0][0] == "server.backup.trigger", audits
    assert audits[0][1] == {"path": "20261002.zip", "size": info["size"]}, audits

    # 第二个调用点：download_backup 走同一条桩
    resp = asyncio.run(sb.download_backup(1, "zh"))
    assert loads == ["load", "load"], "download_backup 未走 system 上的桩"
    assert Path(resp.path).name == "20261002.zip"


def test_A22第四刀_源码锚定原定义消失且回指只在函数体内():
    system_src = _a22c4_src(A22C4_SYSTEM_PY)
    for name in A22C4_STATUS_NAMES + A22C4_BACKUP_NAMES:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, system_src, re.MULTILINE) is None, name

    status_src = _a22c4_src(A22C4_STATUS_PY)
    backup_src = _a22c4_src(A22C4_BACKUP_PY)
    for path in (A22C4_STATUS_PY, A22C4_BACKUP_PY):
        src = _a22c4_src(path)
        tree = ast.parse(src)
        assert _a22c4_top_defs(src), path.name + " 顶层应有函数定义"
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                hit = node.module == "app.application.system" or (
                    node.module == "app.application"
                    and any(a.name == "system" for a in node.names))
                assert not hit, path.name + " 顶层不得 import system（会与重导出成环）"
        for line in src.split("\n"):
            if line.strip() == "from app.application import system as _sys":
                assert line.startswith("    "), path.name + " 顶层回指：" + line

    # 备份块确实需要三个回指（门禁 / 审计 / 桩口径），逐个钉死调用点写法
    assert "await _sys._require_server_admin(user_id, lang)" in backup_src
    assert "mod = _sys._load_backup_module()" in backup_src
    assert "await _sys._audit(" in backup_src
    # 状态块 7 个函数自洽（鉴权在 api 层），四个共享辅助一个都用不到 → 无任何回指
    assert "_sys" not in status_src


def test_A22第四刀_system收成薄壳只剩四个共享辅助():
    system_src = _a22c4_src(A22C4_SYSTEM_PY)
    assert sorted(_a22c4_top_defs(system_src)) == sorted(A22C4_HELPER_NAMES)

    # 四个辅助没被「顺手多搬一刀」：两个新模块里不得出现它们的定义
    for path in (A22C4_STATUS_PY, A22C4_BACKUP_PY):
        got = set(_a22c4_top_defs(_a22c4_src(path)))
        assert not (got & set(A22C4_HELPER_NAMES)), path.name

    # docstring 已改成薄壳（facade）描述，并点名全部六个业务实现模块
    doc = ast.get_docstring(ast.parse(system_src)) or ""
    assert "facade" in doc, doc
    for mod in ("usage_service", "system_config_service", "feature_flag_service",
                "context_budget_service", "system_status_service", "system_backup_service"):
        assert mod in doc, mod

    from app.application import system as sy

    for name in A22C4_HELPER_NAMES:
        assert getattr(sy, name) is not None, name
    assert hasattr(sy, "_logger"), "_logger 属薄壳保留名（A22 第二刀 STAY 清单在册）"

    import app.api.system as api_system  # noqa: F401

    assert api_system.__name__ == "app.api.system"


# ── A22 第五刀（2026-10-02）：api/games.py 业务辅助 ↔ application/game_service.py 接缝 ──
# games.py 是**路由文件**：tests 里 30+ 处按字符串路径打桩（"app.api.games.<name>"），另有对象式
# 桩打在 games 模块上（_settle_game / _abort_game / _guard_stop_visible / _guard_stop …）。搬走的
# 辅助一律在**函数体内** ``from app.api import games as _g`` 再走 ``_g.<name>``，两类桩才照样穿透；
# 回指写成顶层 import 会与 games.py 的重导出成环，写成自身裸名则桩**静默失效**（比红更险）。
A22C5_MOVED_NAMES = (
    "_lazy_lock", "_load_char_map", "_create_session_in_db", "_build_user_view",
    "_build_state", "_abort_game", "_settle_game", "_run_surrender",
    "_apply_fail_force_push", "_as_decision_dict", "_guard_stop_visible",
    "_apply_fail_abort", "_emergency_stop_after_crash", "_mirror_to_group",
    "_check_char_owned",
)
# 必须留在 games.py 的名字：3 个原地定义 + 2 个 import 绑定（tests 打的就是 games 上的这两个绑定）
A22C5_STAY_DEFS = ("_resume_ai_turns", "_broadcast_game_event", "_guard_stop")
A22C5_STAY_BOUND = ("ai_decide", "async_session_factory")
# 路由与调度层（一个都不许搬走）
A22C5_ROUTE_NAMES = (
    "catalog", "create_session", "input_seat", "player_action", "surrender_session",
    "get_state", "games_ws", "join_session", "abort_session", "get_archive", "history",
    "get_content", "put_content", "delete_content", "game_stats", "game_achievements",
    "resume_stuck_games", "_resume_ai_turns", "_guard_stop", "_broadcast_game_event",
)

A22C5_GAMES_PY = _seam_src_path("app.api.games")
A22C5_SVC_PY = _seam_src_path("app.application.game_service")


def _a22c5_src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _a22c5_top_defs(src: str) -> list:
    tree = ast.parse(src)
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


class _A22C5Sess:
    """async_session_factory 桩：被 ``async with`` 进入时记一笔 "db"。"""

    def __init__(self, db, sink):
        self._db, self._sink = db, sink

    async def __aenter__(self):
        self._sink.append("db")
        return self._db

    async def __aexit__(self, *exc):
        return False


class _A22C5Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return None


class _A22C5Db:
    """execute 返回预置行；commit/rollback/refresh 只记录（零真库、零 IO）。"""

    def __init__(self, rows=(), sink=None):
        self.rows = list(rows)
        self.sink = sink if sink is not None else []

    async def execute(self, *_a, **_k):
        return _A22C5Result(self.rows)

    async def get(self, _model, _ident):
        return None

    async def commit(self):
        self.sink.append("commit")

    async def rollback(self):
        self.sink.append("rollback")

    async def refresh(self, _obj):
        self.sink.append("refresh")


def test_A22第五刀_15函数两侧同一对象且5个名字仍留在games本地():
    from app.api import games as g
    from app.application import game_service as gs
    from app.db import database as dbmod
    from app.games import ai_player

    assert len(A22C5_MOVED_NAMES) == 15
    for name in A22C5_MOVED_NAMES:
        assert hasattr(g, name) and hasattr(gs, name), name
        assert getattr(g, name) is getattr(gs, name), name + " 两侧必须是同一对象（具名重导出）"

    for name in A22C5_STAY_DEFS:
        assert getattr(g, name).__module__ == "app.api.games", name + " 必须仍在 games.py 原地定义"
    # ai_decide / async_session_factory 本质是 import 名（不可能 __module__ 成 games），
    # 钉的是「games 上的绑定仍是真身」+「新模块里没有它们」
    assert g.ai_decide is ai_player.ai_decide
    assert g.async_session_factory is dbmod.async_session_factory
    for name in A22C5_STAY_DEFS + A22C5_STAY_BOUND:
        assert not hasattr(gs, name), name + " 不得出现在 game_service（它是 games.py 的私产）"

    # 新模块的顶层定义恰好等于搬家清单：既没漏搬，也没「顺手多搬」
    assert sorted(_a22c5_top_defs(_a22c5_src(A22C5_SVC_PY))) == sorted(A22C5_MOVED_NAMES)


def test_A22第五刀_字符串路径桩打在games上仍被搬走的辅助走到(monkeypatch):
    """本刀命门①：``"app.api.games.async_session_factory"`` 的 10 处字符串路径桩。"""
    from app.api import games as g
    from app.application import game_service as gs

    sink: list = []
    row = types.SimpleNamespace(id=7, user_id=1)

    def _factory():
        return _A22C5Sess(_A22C5Db([row], sink), sink)

    monkeypatch.setattr("app.api.games.async_session_factory", _factory)
    got = asyncio.run(gs._load_char_map([7], 1))
    assert sink == ["db"], (
        "app.api.games.async_session_factory 桩没被走到：新模块把名字解析到了自己的命名空间")
    assert got == {7: row}, got

    # _resume_ai_turns（10 处）/ ai_decide（8 处）都留在 games.py：桩落得上、名字仍在其位
    async def _noop_ai(session_id):
        return None

    async def _stub_ai(engine, seat):
        return {"action": "noop"}

    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    monkeypatch.setattr("app.api.games.ai_decide", _stub_ai)
    assert g._resume_ai_turns is _noop_ai and g.ai_decide is _stub_ai


def test_A22第五刀_对象式桩打在games上仍被搬走的辅助走到(monkeypatch):
    """本刀命门②：桩打在 games 模块属性上，调用点却已搬进 game_service。"""
    from app.api import games as g
    from app.application import game_service as gs

    calls: list = []
    real_guard_stop = g._guard_stop  # 末尾要跑真身，故先存下来（下面会把它换成桩）

    async def _fake_settle(db, session, engine, winner):
        calls.append(("settle", winner))

    async def _surrender_ok(seat):
        return {"ok": True, "end": True, "winner": "1", "events": []}

    monkeypatch.setattr(g, "_settle_game", _fake_settle)
    engine = types.SimpleNamespace(apply_surrender=_surrender_ok)
    out = asyncio.run(gs._run_surrender(None, None, engine, 2, []))
    assert out == {"ok": True, "ended": True, "winner": "1"}, out
    assert calls == [("settle", "1")], "games._settle_game 桩没被走到（裸名解析会静默跑去真结算）"

    # _guard_stop_visible：_g._guard_stop（STAY 名）与 _g._broadcast_game_event（STAY 名）都要现取
    async def _fake_guard_stop(db, session, engine, reason=None):
        calls.append(("guard_stop", reason))

    async def _fake_broadcast(session_id, event, phase):
        calls.append(("broadcast", phase))

    async def _persist_event(db, ev):
        calls.append(("persist", ev["phase"]))

    sink: list = []
    monkeypatch.setattr(g, "_guard_stop", _fake_guard_stop)
    monkeypatch.setattr(g, "_broadcast_game_event", _fake_broadcast)
    eng = types.SimpleNamespace(has_draw_semantics=True, persist_event=_persist_event,
                                player_at=lambda seat: None)
    session = types.SimpleNamespace(id=11, group_id=None)
    ev = asyncio.run(gs._guard_stop_visible(
        _A22C5Db([], sink), session, eng, 11, reason="r", reason_tag="t"))
    assert ev["phase"] == "result" and ev["payload"]["winner_side"] == "draw", ev
    assert ("persist", "result") in calls, calls
    assert ("guard_stop", "r") in calls, calls
    assert calls[-1] == ("broadcast", "result"), "可见结束事件必须经 games 上的广播桩推出去"

    # _guard_stop 自己留在 games.py：它解析的裸名就是 games 全局里的重导出/桩 → 天然仍穿透
    async def _fake_abort(db, session, engine, reason):
        calls.append(("abort", reason))

    monkeypatch.setattr(g, "_abort_game", _fake_abort)
    calls.clear()
    asyncio.run(real_guard_stop(None, None, types.SimpleNamespace(has_draw_semantics=True), "r"))
    assert calls == [("settle", "draw")], calls
    calls.clear()
    asyncio.run(real_guard_stop(None, None, types.SimpleNamespace(has_draw_semantics=False), "r2"))
    assert calls == [("abort", "r2")], calls


def test_A22第五刀_源码锚定原定义消失且回指只在函数体内():
    games_src = _a22c5_src(A22C5_GAMES_PY)
    for name in A22C5_MOVED_NAMES:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, games_src, re.MULTILINE) is None, name + " 原定义应已搬走"
    for name in A22C5_STAY_DEFS:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, games_src, re.MULTILINE) is not None, name + " 必须仍有原地定义"
    assert "from app.db.database import async_session_factory" in games_src
    assert "_ai_turn_locks: dict[int, asyncio.Lock] = {}" in games_src
    assert "_game_ws_clients: dict[int, set[WebSocket]] = {}" in games_src
    assert "from app.application.game_service import (" in games_src

    svc_src = _a22c5_src(A22C5_SVC_PY)
    tree = ast.parse(svc_src)
    assert _a22c5_top_defs(svc_src), "game_service 顶层应有函数定义"
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            hit = node.module == "app.api.games" or (
                node.module == "app.api" and any(a.name == "games" for a in node.names))
            assert not hit, "game_service 顶层不得 import games（会与 games.py 的重导出成环）"
    for line in svc_src.split("\n"):
        if line.strip() == "from app.api import games as _g":
            assert line.startswith("    "), "顶层回指：" + line
    assert "_g." in svc_src, "game_service 应有 _g. 回指调用"

    # 关键回指调用点逐个钉死：少一个，对应那批桩就静默失效（测试不红）
    for needle in (
        "return _g._ai_turn_locks.setdefault(session_id, asyncio.Lock())",
        "async with _g.async_session_factory() as db:",
        "engine_cls = _g.engine_for(game_type)",
        "await _g._settle_game(db, session, engine, winner)",
        "await _g._guard_stop(db, session, engine, reason=reason)",
        'await _g._broadcast_game_event(session_id, end_ev, "result")',
        "await _g._guard_stop_visible(",
        "_g._game_ws_clients.pop(sid, None)",
    ):
        assert needle in svc_src, needle


def test_A22第五刀_路由与进程内表一个都没搬走():
    games_src = _a22c5_src(A22C5_GAMES_PY)
    defs = set(_a22c5_top_defs(games_src))
    for name in A22C5_ROUTE_NAMES:
        assert name in defs, name + " 属路由/调度层，必须留在 games.py"
    assert games_src.count("@router.") == 15, "路由数量不得变化"

    svc_defs = set(_a22c5_top_defs(_a22c5_src(A22C5_SVC_PY)))
    assert not (svc_defs & set(A22C5_ROUTE_NAMES)), "路由与调度层不得出现在 game_service"
    for name in A22C5_STAY_DEFS + A22C5_STAY_BOUND:
        assert name not in svc_defs, name + " 不得出现在 game_service"


# ── A22 第六刀（③a，2026-10-02）：message_generator 纯函数助手 ↔ scheduling/message_text.py ──
# 本刀**只搬一处桩都没有的纯函数**（对象式 + 字符串路径两种打桩都扫过）：message_generator 侧靠
# 具名重导出保住大函数里的裸名调用点，generate_proactive_event（638 行、2 处字符串路径桩）留原地。
# message_text 反过来**绝不** import message_generator（顶层回指必成环：mg → mt → mg）。
A22C6_FUNC_NAMES = (
    "_has_visible_content", "_segment_guard_on", "_quote_unbalanced", "_has_unclosed_delimiter",
    "_split_response_lines", "_normalize_segments", "_scene_is_school",
    "_conflicting_segment_indexes", "_apply_segment_guard", "_has_invitation",
    "_naturalness_flag", "score_naturalness", "_validate_segments",
    "_context_overlap_ratio", "_parrot_blocked",
)
# 常量随迁（system.py 同口径）：定义进了 message_text，message_generator 仍具名重导出供调用方取用
A22C6_CONST_NAMES = (
    "_VISIBLE_RE", "_SEGMENT_MIN_LEN", "_FAMILY_SCENE_WORDS", "_SCHOOL_SCENE_WORDS",
    "_QUOTE_PAIRS", "_QUOTE_CLOSES", "_BANNED_WORDS", "_MAX_SEGMENT_LEN",
    "_ABRUPT_OPENING_WORDS", "_TEMPLATE_PHRASES", "_INVITATION_RE",
    "_PARROT_OVERLAP_THRESHOLD", "_PARROT_MIN_CHARS",
)
# 必须留在 message_generator 的原地定义（③b 后只剩带 2 处字符串路径桩的大函数）
# 2026-10-02 A22 ③b 锚点迁移：_proactive_self_search / _gen_with_reasoning / _describe_* / _load_* /
# generate_*_message 已搬入 message_context / message_llm（见下方「第六刀b」守卫），断言原意不变。
A22C6_STAY_DEFS = (
    "generate_proactive_event",
)

A22C6_MG_PY = _seam_src_path("app.scheduling.message_generator")
A22C6_MT_PY = _seam_src_path("app.scheduling.message_text")


def _a22c6_src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _a22c6_top_defs(src: str) -> list:
    tree = ast.parse(src)
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_A22第六刀_15函数两侧同一对象且常量随迁():
    from app.scheduling import message_generator as mg, message_text as mt

    assert len(A22C6_FUNC_NAMES) == 15
    for name in A22C6_FUNC_NAMES:
        assert hasattr(mg, name) and hasattr(mt, name), name
        assert getattr(mg, name) is getattr(mt, name), name + " 两侧必须同一对象（具名重导出）"
        assert getattr(mt, name).__module__ == "app.scheduling.message_text", name
    for name in A22C6_CONST_NAMES:
        assert getattr(mg, name) is getattr(mt, name), name + " 常量随迁后仍须按同名取到"

    # message_text 的顶层函数恰好等于搬家清单：既没漏搬，也没「顺手多搬」
    assert sorted(_a22c6_top_defs(_a22c6_src(A22C6_MT_PY))) == sorted(A22C6_FUNC_NAMES)


def test_A22第六刀_源码锚定原定义消失且留在原地的一个没搬():
    mg_src = _a22c6_src(A22C6_MG_PY)
    for name in A22C6_FUNC_NAMES:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, mg_src, re.M) is None, name + " 的原定义应已从 message_generator 消失"
    for name in A22C6_STAY_DEFS:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, mg_src, re.M), name + " 必须仍在 message_generator 原地定义"

    mt_src = _a22c6_src(A22C6_MT_PY)
    for node in ast.parse(mt_src).body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "app.scheduling.message_generator", (
                "message_text 顶层回指 message_generator 会成环")
    assert "app.scheduling.message_generator" not in mt_src, (
        "message_text 任何位置都不得回指 message_generator（纯函数不该反向依赖生成器）")


def test_A22第六刀_message_text顶层只依赖标准库():
    """纯函数边界：顶层 import 只允许 re（IO / LLM / DB / flag 模块一律不得进）。"""
    tree = ast.parse(_a22c6_src(A22C6_MT_PY))
    top: list = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            top.append(node.module)
    assert top == ["re"], top


def test_A22第六刀_纯函数行为等价抽检():
    from app.scheduling import message_generator as mg, message_text as mt

    segs = ["哈哈哈，今天天气不错", "记得吗"]
    assert mg.score_naturalness(segs) == mt.score_naturalness(segs)
    assert mg.score_naturalness("嗯") == mt.score_naturalness("嗯") == 0.3
    assert mg.score_naturalness(segs) < mg.NATURALNESS_RETRY_THRESHOLD, "突兀开口+模板句式应压到低分"

    # _normalize_segments：空段/纯标点丢弃，碎片段并入前一段
    got = mt._normalize_segments(["今天天气不错呀", "嗯", "", "  ", "走，去食堂吃饭吧"])
    assert got == ["今天天气不错呀嗯", "走，去食堂吃饭吧"], got
    assert mg._normalize_segments(["嗯", "呀"]) == [], "整条都是碎片时宁可不发残句"

    # _split_response_lines：未闭合引号不落刀，后续行并入当前段
    resp = "“我先说到这儿\n你慢慢忙"
    assert mg._split_response_lines(resp) == mt._split_response_lines(resp) == ["“我先说到这儿你慢慢忙"]
    assert mt._split_response_lines("今天下了点雨\n要不要带伞") == ["今天下了点雨", "要不要带伞"]

    # 现实冲突判定（家庭场景词 vs 住校场景）：两侧同对象、结果一致
    _campus_segs = ["锅里给你留了饭哦", "你记得早点睡呀"]
    conf = mt._conflicting_segment_indexes(_campus_segs, "宿舍")
    assert conf == [0] == mg._conflicting_segment_indexes(_campus_segs, "宿舍")
    kept, _left = mt._apply_segment_guard(_campus_segs, "宿舍", drop_conflicts=True)
    assert kept == ["你记得早点睡呀"] and _left == [0], (kept, _left)
    assert mt._conflicting_segment_indexes(_campus_segs, "家里") == [], "非住校场景不算穿帮"


# ── A22 第六刀（③b，2026-10-02）：message_generator 上下文装配 + 生成块 ↔ message_context / message_llm ──
# ③b 把「上下文装配 + 状态轨迹」12 函数 + 7 常量搬到 message_context，「LLM 生成助手 + 三个节日消息」5 函数
# 搬到 message_llm。与 ③a 的关键差异：搬走的代码里引用了**留在 message_generator 的名字**
# （chat_completion / load_character_reasoning_level / _logger）以及**同批搬走、桩打在 mg 上的名字**
# （_gen_with_reasoning）——这些一律在函数体内 ``from app.scheduling import message_generator as _mg`` 现取
# ``_mg.<name>``。若写成裸名，桩就静默失效（测试变绿却查真库/真调 LLM）。下面把这条接缝钉死。
A22C7_CTX_FUNCS = (
    "_describe_now", "_describe_idle", "_load_recent_reflection", "_load_scene_facts",
    "_load_authoritative_user_location", "two_pass_trace_allowed", "_load_state_trace",
    "_prepend_state_trace", "_note_state_trace_injected", "_note_state_trace_gate",
    "_predict_notify_surface", "_load_identity_block",
)
A22C7_CTX_CONSTS = (
    "TWO_PASS_TRACE_GRAY_CHARS", "TWO_PASS_TRACE_ALL_FLAG", "GATE_ROUTE", "GATE_NOT_ALLOWED",
    "GATE_EMPTY_TRACE", "GATE_INJECTED", "GATE_TRACE_ERROR",
)
A22C7_LLM_FUNCS = (
    "_gen_with_reasoning", "_proactive_self_search", "generate_birthday_message",
    "generate_anniversary_message", "generate_holiday_message",
)
# 必须留在 message_generator 的（带 2 处字符串路径桩的大函数 + ③a/② 的重导出，一个都不许搬走）
A22C7_MG_STAY = ("generate_proactive_event",)
# 留在 message_generator、但被搬走代码经 _mg 现取的名字
A22C7_STAY_NAMES = ("chat_completion", "load_character_reasoning_level", "_logger")
# 每个搬走的函数里「必须以 _mg.<name> 出现、禁止裸名」的桩名（变异守卫用；还原 _mg. 立即变红）
A22C7_MG_REFS = {
    ("message_llm.py", "_gen_with_reasoning"): {"chat_completion", "load_character_reasoning_level", "_logger"},
    ("message_llm.py", "_proactive_self_search"): {"_logger", "_gen_with_reasoning"},
    ("message_llm.py", "generate_birthday_message"): {"_gen_with_reasoning", "_load_identity_block"},
    ("message_llm.py", "generate_anniversary_message"): {"_gen_with_reasoning", "_load_identity_block"},
    ("message_llm.py", "generate_holiday_message"): {"_gen_with_reasoning", "_load_identity_block"},
    ("message_context.py", "_load_state_trace"): {"_logger"},
    ("message_context.py", "_note_state_trace_gate"): {"_logger"},
}


def _a22c7_src(mod) -> str:
    from pathlib import Path
    return Path(mod.__file__).read_text(encoding="utf-8")


def _a22c7_top_defs(mod) -> list:
    return [n.name for n in ast.parse(_a22c7_src(mod)).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _a22c7_find_fn(mod, name):
    for n in ast.walk(ast.parse(_a22c7_src(mod))):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n


def test_A22第六刀b_1_重导出17函数7常量两侧同一对象():
    from app.scheduling import message_generator as mg, message_context as ctx, message_llm as llm

    assert len(A22C7_CTX_FUNCS) == 12 and len(A22C7_CTX_CONSTS) == 7 and len(A22C7_LLM_FUNCS) == 5
    for name in A22C7_CTX_FUNCS:
        assert hasattr(mg, name) and hasattr(ctx, name), name
        assert getattr(mg, name) is getattr(ctx, name), name + " 两侧必须同一对象（具名重导出）"
        assert getattr(ctx, name).__module__ == "app.scheduling.message_context", name
    for name in A22C7_CTX_CONSTS:
        assert getattr(mg, name) is getattr(ctx, name), name + " 常量随迁后仍须按同名取到"
    for name in A22C7_LLM_FUNCS:
        assert hasattr(mg, name) and hasattr(llm, name), name
        assert getattr(mg, name) is getattr(llm, name), name + " 两侧必须同一对象（具名重导出）"
        assert getattr(llm, name).__module__ == "app.scheduling.message_llm", name

    # 新文件顶层函数恰好等于搬家清单（既没漏搬，也没顺手多搬）
    assert sorted(_a22c7_top_defs(ctx)) == sorted(A22C7_CTX_FUNCS)
    assert sorted(_a22c7_top_defs(llm)) == sorted(A22C7_LLM_FUNCS)

    # message_generator 侧：17 个搬走的 def 原定义全部消失，generate_proactive_event 仍原地定义
    mg_src = _a22c7_src(mg)
    for name in A22C7_CTX_FUNCS + A22C7_LLM_FUNCS:
        assert re.search(r"^(?:async )?def %s\b" % re.escape(name), mg_src, re.M) is None, \
            name + " 的原定义应已从 message_generator 消失"
    for name in A22C7_MG_STAY:
        assert re.search(r"^(?:async )?def %s\b" % re.escape(name), mg_src, re.M), \
            name + " 必须仍在 message_generator 原地定义"
    assert mg.generate_proactive_event.__module__ == "app.scheduling.message_generator"


def test_A22第六刀b_2_留原地三名字仍挂在mg():
    from app.scheduling import message_generator as mg
    for name in A22C7_STAY_NAMES:
        assert hasattr(mg, name), name + " 应仍挂在 message_generator 上"
    # 9 个 generate_proactive_event 仍在用的常量也必须留在原地
    for const in ("_SEGMENT_MAX", "_EVENT_DESC", "_SENT_SPLIT", "NATURALNESS_RETRY_THRESHOLD",
                  "NATURALNESS_SKIP_THRESHOLD", "_TIER_GUIDE", "_INTENT_GUIDE", "_OPENER_BANK"):
        assert hasattr(mg, const), const + " 常量必须留在 message_generator"


def test_A22第六刀b_3_gen_with_reasoning穿透chat_completion桩(monkeypatch):
    """把 mg.chat_completion 换成哨兵并驱动 mg._gen_with_reasoning：哨兵必须被命中（证明走 _mg 现取）。"""
    import app.scheduling.message_generator as mg
    hits = {}

    async def _sentinel_completion(**kw):
        hits["kw"] = kw
        return "SENT-INJECTED"

    async def _level0(_cid):
        return 0

    monkeypatch.setattr(mg, "chat_completion", _sentinel_completion)
    monkeypatch.setattr(mg, "load_character_reasoning_level", _level0)
    out, reason = asyncio.run(mg._gen_with_reasoning(
        [{"role": "user", "content": "hi"}], 1, 2, temperature=0.5, max_tokens=8))
    assert "kw" in hits, "_gen_with_reasoning 用了裸 chat_completion（桩静默失效会真调 LLM）"
    assert hits["kw"]["task"] == "message"
    assert out == "SENT-INJECTED" and reason == ""


def test_A22第六刀b_4_generate_birthday穿透_gen_with_reasoning桩(monkeypatch):
    """桩 mg._gen_with_reasoning 后驱动 mg.generate_birthday_message：桩必须被命中（证明走 _mg）。"""
    import app.scheduling.message_generator as mg
    import app.scheduling.state_guard as sg
    hits = {}

    async def _gen_stub(messages, character_id, user_id, temperature=0.8, max_tokens=400):
        hits["called"] = True
        return ("祝福内容", "reason")

    async def _ident(_cid):
        return ""

    async def _anchor(**_kw):
        return ""

    monkeypatch.setattr(mg, "_gen_with_reasoning", _gen_stub)
    monkeypatch.setattr(mg, "_load_identity_block", _ident)
    monkeypatch.setattr(sg, "current_state_anchor", _anchor)
    monkeypatch.setattr(sg, "guard_block", lambda t: "")
    out = asyncio.run(mg.generate_birthday_message("小阳", "活泼", "用户", character_id=11, user_id=1))
    assert hits.get("called"), "generate_birthday_message 用了裸 _gen_with_reasoning"
    assert out == "祝福内容"


def test_A22第六刀b_5_proactive_self_search穿透_gen_with_reasoning桩(monkeypatch):
    """驱动 _proactive_self_search 走到 regen：regen 必须命中 mg._gen_with_reasoning 桩（证明 _mg 现取）。"""
    import app.scheduling.message_generator as mg
    import app.agent.actions as actions
    import app.agent.loop as loop
    import app.application.chat.tools as tools
    hits = {}

    async def _gen_stub(messages, character_id, user_id, temperature=0.0, max_tokens=0):
        hits["regen"] = True
        return ("已查证后的正文", "")

    def _fake_extract(b):
        if "[SEARCH]" in (b or ""):
            return (b.replace("[SEARCH]x[/SEARCH]", ""), "x")
        return (b or "", "")

    monkeypatch.setattr(mg, "_gen_with_reasoning", _gen_stub)
    monkeypatch.setattr(loop, "MAX_SEARCH_ROUNDS", 1)
    monkeypatch.setattr(actions, "extract_search", _fake_extract)
    monkeypatch.setattr(tools, "_search_throttle", lambda uid: True)
    monkeypatch.setattr(tools, "_search_inject_enabled", lambda: True)

    async def _fake_search(_q):
        return "搜索结果一二三"

    monkeypatch.setattr(tools, "_run_web_search", _fake_search)
    body, no_msg = asyncio.run(mg._proactive_self_search(
        "原始[SEARCH]x[/SEARCH]", messages=[{"role": "user", "content": "hi"}], character_id=7, user_id=3))
    assert hits.get("regen"), "_proactive_self_search 用了裸 _gen_with_reasoning"
    assert no_msg is False and body == "已查证后的正文"


def test_A22第六刀b_6_load_state_trace与note_gate穿透logger桩(monkeypatch):
    """异常路径下 _load_state_trace / _note_state_trace_gate 必须命中 mg._logger 桩（证明 _mg._logger）。"""
    import app.scheduling.message_generator as mg
    import app.db.database as db
    import app.memory.observability as obs
    hits = {"w": 0, "g": 0}

    class _SentLogger:
        def warning(self, *_a, **_k):
            hits["w"] += 1

    monkeypatch.setattr(mg, "_logger", _SentLogger())

    def _boom_factory(*_a, **_k):
        raise RuntimeError("db off")

    monkeypatch.setattr(db, "async_session_factory", _boom_factory)
    text, ms, err = asyncio.run(mg._load_state_trace(13, 1))
    assert (text, ms, err) == ("", 0.0, True)
    assert hits["w"] >= 1, "_load_state_trace 未走 _mg._logger.warning（裸 _logger 会在真库异常时崩或漏日志）"

    def _boom_obs(*_a, **_k):
        raise RuntimeError("obs down")

    monkeypatch.setattr(obs, "obs_event", _boom_obs)
    mg._note_state_trace_gate(13, mg.GATE_NOT_ALLOWED)
    assert hits["w"] >= 2, "_note_state_trace_gate 未走 _mg._logger.warning"


def test_A22第六刀b_7_两个新模块顶层不成环():
    """message_context / message_llm 顶层绝不绑定 message_generator（顶层回指必成环 mg→ctx/llm→mg）。"""
    import app.scheduling.message_context as ctx
    import app.scheduling.message_llm as llm
    for mod, tag in ((ctx, "message_context"), (llm, "message_llm")):
        assert "message_generator" not in mod.__dict__, tag + " 顶层不得绑定 message_generator"
        tree = ast.parse(_a22c7_src(mod))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                assert node.module != "app.scheduling.message_generator", tag
                assert not any(a.name == "message_generator" for a in node.names), tag
            elif isinstance(node, ast.Import):
                assert not any(a.name == "app.scheduling.message_generator" for a in node.names), tag


def test_A22第六刀b_8_mg回指静态锚定_变异守卫():
    """桩名必须以 _mg.<name> 出现、绝不裸名；且函数内确有 message_generator as _mg 的现取 import。

    这正是把 `_mg.chat_completion` 改回裸 `chat_completion` 时立即变红的那道门（§6.5 变异自测的可复核形式）。
    """
    import app.scheduling.message_context as ctx
    import app.scheduling.message_llm as llm
    mods = {"message_context.py": ctx, "message_llm.py": llm}
    for (file_tag, fn_name), stubbed in A22C7_MG_REFS.items():
        fn = _a22c7_find_fn(mods[file_tag], fn_name)
        assert fn is not None, (file_tag, fn_name)
        bare = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        mg_attrs = {a.attr for a in ast.walk(fn)
                    if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id == "_mg"}
        assert bare & stubbed == set(), "%s.%s 出现裸桩名 %s（应改回 _mg.）" % (file_tag, fn_name, bare & stubbed)
        assert stubbed <= mg_attrs, "%s.%s 缺 _mg. 现取 %s" % (file_tag, fn_name, stubbed - mg_attrs)
        has_mg = any(isinstance(n, ast.ImportFrom) and n.module == "app.scheduling"
                     and any(a.name == "message_generator" and a.asname == "_mg" for a in n.names)
                     for n in ast.walk(fn))
        assert has_mg, "%s.%s 缺函数内 message_generator as _mg 现取 import" % (file_tag, fn_name)


def test_A22第六刀b_9_load_recent_reflection重导出与桩隔离(monkeypatch):
    """桩打在 mg 上生效、且不污染 ctx 本体（generate_proactive_event 里的裸名调用点据此走桩）。"""
    import app.scheduling.message_generator as mg
    import app.scheduling.message_context as ctx
    orig = ctx._load_recent_reflection

    async def _stub(_cid):
        return "STUBBED"

    assert mg._load_recent_reflection is orig, "重导出两侧应同一对象"
    monkeypatch.setattr(mg, "_load_recent_reflection", _stub)
    assert getattr(mg, "_load_recent_reflection") is _stub
    assert ctx._load_recent_reflection is orig, "打桩只动 mg 命名空间，不得回写 ctx"
    assert asyncio.run(mg._load_recent_reflection(13)) == "STUBBED"


# ── A22 第八刀（④a，2026-10-03）：registry 插件扫描/加载/库同步/安装溯源块 ↔ plugins/plugin_store ──
# ④a 把 registry 的 12 个函数整体搬到 ``app/plugins/plugin_store.py``，registry 侧靠**具名重导出**保留同名符号。
# 与 ③a/③b 同一条命门：搬走的代码里引用了**留在 registry 的打桩锚点**（``_loaded`` / ``_enabled`` /
# ``_db_config`` / ``_db_prov`` / ``USER_DIR`` / ``EXAMPLE_DIR`` / ``_logger``，以及 ``push_sdk_context`` /
# ``reset_sdk_context`` / ``clear_runtime_scope_caches`` / ``backfill_plugin_consents_once`` / ``_parse_perms``
# / ``_upsert_plugin_consent``），还有**同批搬走、但桩打在 registry 上的 ``load_plugin_dir``**——这些一律在
# **函数体内** ``from app.plugins import registry as _reg`` 现取 ``_reg.<name>``。写成裸名或 import 期绑定时，
# tests 里 setattr(registry, "sync_plugins_db" / "resolve_plugin_dir" / "load_plugin_dir", …) 那批桩会静默失效，
# 表现为「测试变绿却去扫真实示例目录 / 写真实库」。下面把这条接缝钉死（零 DB、零网络：哨兵全局 + 打桩目录）。
A22C8_MOVED_NAMES = (
    "_scan_dir", "resolve_plugin_dir", "_discard_partial_load", "load_plugin_dir",
    "_py_default_literal", "_reconcile_plugin_schema", "_ensure_plugin_tables_sync",
    "ensure_plugin_tables", "sync_plugins_db", "_row_prov", "get_plugin_provenance",
    "record_install_provenance",
)
# 留在 registry 的锚点（tests 直接在 registry 上打桩/读取，一个都不许搬、也不许在 plugin_store 另立一份）
A22C8_ANCHORS = (
    "_loaded", "_enabled", "_db_config", "_db_prov", "USER_DIR", "EXAMPLE_DIR",
    "PROJECT_ROOT", "_logger", "_RUNTIME_SCOPE_TTL",
)
# 仍在 registry 原地定义的（hook 分发与 API 面，一个都不许搬走）
# R4 随迁（A22 第九刀 · ④c，2026-10-03）：``plugin_disabled_route_gate_enabled`` 已进 ④c 搬家清单
# （现定义在 plugins/plugin_scope.py，registry 侧具名重导出）。断言原意一字未变——「本名单里的名字必须
# 仍在 registry 原地定义」，只把随代码搬家的名字跟着搬走；它的接缝由 ④c 守卫第 6 条接管。
A22C8_STAY_DEFS = ("run_hook", "run_hook_collect", "list_plugins", "get_plugin",
                   "set_plugin_state", "run_plugin_action",
                   "mount_plugin_routers", "preload_channels")
# 每个搬走的函数里「必须以 _reg.<name> 出现、禁止裸名」的名字（变异守卫用；还原任一处 _reg. 立即变红）
A22C8_STAY_REFS = {
    "resolve_plugin_dir": {"EXAMPLE_DIR", "USER_DIR"},
    "_discard_partial_load": {"_loaded", "_enabled"},
    "load_plugin_dir": {"_logger", "_loaded", "push_sdk_context", "reset_sdk_context"},
    "_ensure_plugin_tables_sync": {"_logger"},
    "sync_plugins_db": {"_loaded", "_enabled", "_db_config", "_db_prov", "EXAMPLE_DIR", "USER_DIR",
                        "_logger", "clear_runtime_scope_caches", "backfill_plugin_consents_once",
                        "load_plugin_dir"},
    "record_install_provenance": {"_logger", "_db_prov", "_parse_perms", "_upsert_plugin_consent"},
}
A22C8_REGISTRY_PY = _seam_src_path("app.plugins.registry")
A22C8_STORE_PY = _seam_src_path("app.plugins.plugin_store")


def _a22c8_src(path) -> str:
    return Path(str(path)).read_text(encoding="utf-8")


def _a22c8_top_defs(src: str) -> list:
    return [n.name for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _a22c8_find_fn(mod, name):
    for n in ast.walk(ast.parse(_a22c8_src(mod.__file__))):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n


def test_A22第八刀a_1_重导出12函数两侧同一对象():
    import app.plugins.plugin_store as store
    import app.plugins.registry as reg

    assert len(A22C8_MOVED_NAMES) == 12
    for name in A22C8_MOVED_NAMES:
        assert hasattr(reg, name) and hasattr(store, name), name
        assert getattr(reg, name) is getattr(store, name), name + " 两侧必须同一对象（具名重导出）"
        # 同一函数对象：__module__ 只能是 plugin_store（registry 侧不许复制一份实现）
        assert getattr(reg, name).__module__ == "app.plugins.plugin_store", name


def test_A22第八刀a_2_打桩锚点仍挂在registry且没被另立一份():
    import app.plugins.plugin_store as store
    import app.plugins.registry as reg

    for name in A22C8_ANCHORS:
        assert hasattr(reg, name), name + " 必须仍挂在 registry 上（tests 直接在 registry 打桩）"
        assert not hasattr(store, name), name + " 不得在 plugin_store 里另立副本（有了副本桩就静默失效）"
    for name in ("run_hook", "run_hook_collect", "list_plugins", "get_plugin"):
        assert getattr(reg, name).__module__ == "app.plugins.registry", name + " 必须留在 registry"
    # 可变全局仍是 dict 本体（就地写＝跨模块共享同一份；重导出不得换成新对象）
    for name in ("_loaded", "_enabled", "_db_config", "_db_prov"):
        assert isinstance(getattr(reg, name), dict), name


def test_A22第八刀a_3_plugin_store顶层函数恰好等于搬家清单():
    assert sorted(_a22c8_top_defs(_a22c8_src(A22C8_STORE_PY))) == sorted(A22C8_MOVED_NAMES), (
        "新文件顶层 def 必须恰好是 12 个搬家清单：既没漏搬，也没顺手多搬")


def test_A22第八刀a_4_reg现取静态锚定_变异守卫():
    """桩名必须以 _reg.<name> 出现、绝不裸名，且函数体内确有 registry as _reg 现取 import。

    这就是把 `_reg.USER_DIR` 改回裸 `USER_DIR` 时立即变红的那道门（§变异自测的可复核形式）。
    """
    import app.plugins.plugin_store as store

    for fn_name, stubbed in A22C8_STAY_REFS.items():
        fn = _a22c8_find_fn(store, fn_name)
        assert fn is not None, fn_name
        bare = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        reg_attrs = {a.attr for a in ast.walk(fn)
                     if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                     and a.value.id == "_reg"}
        assert bare & stubbed == set(), "%s 出现裸桩名 %s（应改回 _reg.）" % (fn_name, bare & stubbed)
        assert stubbed <= reg_attrs, "%s 缺 _reg. 现取 %s" % (fn_name, stubbed - reg_attrs)
        has_reg = any(isinstance(n, ast.ImportFrom) and n.module == "app.plugins"
                      and any(a.name == "registry" and a.asname == "_reg" for a in n.names)
                      for n in ast.walk(fn))
        assert has_reg, "%s 缺函数内 registry as _reg 现取 import" % fn_name


def test_A22第八刀a_5_sync_plugins_db穿透registry目录与哨兵全局(monkeypatch, tmp_path):
    """打桩 registry.USER_DIR + 哨兵 _loaded/_enabled 后驱动 registry.sync_plugins_db：哨兵必须被命中。

    写成裸名（import 期绑定）时本例会去扫**真实**示例目录并走进写库分支（哨兵也不会被清空），
    正是「测试变绿却扫真目录 / 写真库」那种静默失效。load_plugin_dir 桩返回 None ⇒ seen 为空 ⇒ 早退。
    """
    import app.games.registry as games_reg
    import app.plugins.registry as reg
    import app.providers.registry as prov_reg
    import app.scheduling.sources.strategy as strategy

    hits = {"scan": [], "backfill": 0, "clear": 0}
    examples = tmp_path / "examples"
    user = tmp_path / "user"
    examples.mkdir()
    (user / "a22c8_pack").mkdir(parents=True)
    (user / "a22c8_pack" / "manifest.json").write_text("{}", encoding="utf-8")

    def _load_stub(path):
        hits["scan"].append(str(path))
        return None

    async def _backfill_stub():
        hits["backfill"] += 1
        return 0

    sentinel_loaded = {"keep": 1}
    sentinel_enabled = {"keep": True}
    sentinel_cfg = {"keep": {}}
    sentinel_prov = {"keep": {}}
    monkeypatch.setattr(reg, "EXAMPLE_DIR", examples)
    monkeypatch.setattr(reg, "USER_DIR", user)
    monkeypatch.setattr(reg, "load_plugin_dir", _load_stub)
    monkeypatch.setattr(reg, "backfill_plugin_consents_once", _backfill_stub)
    monkeypatch.setattr(reg, "clear_runtime_scope_caches",
                        lambda: hits.__setitem__("clear", hits["clear"] + 1))
    monkeypatch.setattr(reg, "_loaded", sentinel_loaded)
    monkeypatch.setattr(reg, "_enabled", sentinel_enabled)
    monkeypatch.setattr(reg, "_db_config", sentinel_cfg)
    monkeypatch.setattr(reg, "_db_prov", sentinel_prov)
    # 三处「同源注册清理」是本函数的真实副作用：本例只验接缝，一律中和，不动其它用例的注册表
    monkeypatch.setattr(games_reg, "unregister_games_not_in", lambda _s: None)
    monkeypatch.setattr(prov_reg, "unregister_providers_not_in", lambda _s: None)
    monkeypatch.setattr(strategy, "reset_registrations", lambda: None)

    asyncio.run(reg.sync_plugins_db())

    assert hits["scan"], "sync_plugins_db 没走 registry.load_plugin_dir 桩（裸名 ⇒ 桩静默失效）"
    assert str(user) in hits["scan"][0], "扫的不是打桩后的 USER_DIR（说明用的是 import 期绑定）"
    assert hits["backfill"] == 1 and hits["clear"] == 1, "早退分支未命中桩（真打了库或没走 _reg.）"
    assert sentinel_loaded == {} and sentinel_enabled == {}, "哨兵 _loaded/_enabled 未被清空（没走 _reg.）"
    assert sentinel_cfg == {} and sentinel_prov == {}, "哨兵 _db_config/_db_prov 未被清空（没走 _reg.）"


def test_A22第八刀a_6_resolve与discard与load三处穿透打桩目录与哨兵(monkeypatch, tmp_path):
    """resolve_plugin_dir 每次调用现取目录；_discard_partial_load / load_plugin_dir 读写哨兵 _loaded。"""
    import app.plugins.plugin_store as store
    import app.plugins.registry as reg

    examples = tmp_path / "examples"
    user = tmp_path / "user"
    (examples / "in_examples").mkdir(parents=True)
    (examples / "in_examples" / "manifest.json").write_text("{}", encoding="utf-8")
    (user / "in_user").mkdir(parents=True)
    (user / "in_user" / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(reg, "EXAMPLE_DIR", examples)
    monkeypatch.setattr(reg, "USER_DIR", user)

    assert reg.resolve_plugin_dir("in_user") == user / "in_user"
    assert reg.resolve_plugin_dir("in_examples") == examples / "in_examples"
    assert reg.resolve_plugin_dir("ghost") is None
    # 把 USER_DIR 再挪走：同一个函数调用必须跟着新桩值走（证明调用时刻现取，不是 import 期焊死）
    monkeypatch.setattr(reg, "USER_DIR", tmp_path / "nowhere")
    assert reg.resolve_plugin_dir("in_user") is None

    sentinel_loaded = {"ghost": {"info": {}}, "real": {"info": {"name": "real"}}}
    sentinel_enabled = {"ghost": True, "real": True}
    monkeypatch.setattr(reg, "_loaded", sentinel_loaded)
    monkeypatch.setattr(reg, "_enabled", sentinel_enabled)
    reg._discard_partial_load("ghost")   # 占位条目（info 无 name）→ 清掉
    reg._discard_partial_load("real")    # 真实条目 → 不动
    reg._discard_partial_load(None)      # 早退分支
    assert "ghost" not in sentinel_loaded and "ghost" not in sentinel_enabled
    assert "real" in sentinel_loaded and "real" in sentinel_enabled

    # load_plugin_dir：config-only 加载（type=hybrid、无 main.py），写进的是哨兵 _loaded、不打库
    pack = tmp_path / "a22c8_cfg"
    pack.mkdir()
    (pack / "manifest.json").write_text(
        '{"name": "a22c8_cfg", "version": "1.0.0", "description": "d", "type": "hybrid"}',
        encoding="utf-8")
    loaded = {"pre": 1}
    ensure_calls = []
    monkeypatch.setattr(reg, "_loaded", loaded)
    monkeypatch.setattr(store, "_ensure_plugin_tables_sync", lambda: ensure_calls.append(1) or [])
    info = reg.load_plugin_dir(pack)
    assert info and info["name"] == "a22c8_cfg", info
    assert "pre" in loaded and loaded["a22c8_cfg"]["info"]["name"] == "a22c8_cfg", (
        "load_plugin_dir 没写进哨兵 _loaded（没走 _reg._loaded 现取）")
    assert ensure_calls == [1], "load_plugin_dir 未走 _ensure_plugin_tables_sync 桩（真建表＝越界打库）"


def test_A22第八刀a_7_plugin_store顶层不成环():
    """plugin_store 顶层绝不绑定 registry（registry 重导出 plugin_store，顶层回指必成环）。"""
    import app.plugins.plugin_store as store

    assert "registry" not in store.__dict__, "plugin_store 顶层不得绑定 registry"
    for node in ast.parse(_a22c8_src(store.__file__)).body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "app.plugins", "plugin_store 顶层 from app.plugins import registry 会成环"
            assert not any(a.name == "registry" for a in node.names), "顶层不得 import registry"
        elif isinstance(node, ast.Import):
            assert not any(a.name == "app.plugins.registry" for a in node.names), (
                "顶层不得 import app.plugins.registry")


def test_A22第八刀a_8_源码锚定原定义消失且留在原地的一个没搬():
    reg_src = _a22c8_src(A22C8_REGISTRY_PY)
    for name in A22C8_MOVED_NAMES:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, reg_src, re.M) is None, name + " 的原定义应已从 registry 消失"
    for name in A22C8_STAY_DEFS:
        pattern = r"^(?:async )?def %s\b" % re.escape(name)
        assert re.search(pattern, reg_src, re.M), name + " 必须仍在 registry 原地定义"
    assert "from app.plugins.plugin_store import" in reg_src, "registry 侧必须有具名重导出块"
    defs = _a22c8_top_defs(reg_src)
    # R4 随迁（A22 第九刀 ④b，2026-10-03）：④b 又从 registry 搬走 12 个顶层 def，
    # 故本锚点由「54 - 12 = 42」改为「42 - ④b 的 12 = 30」；断言原意未变（一个不多一个不少），
    # 只把写死的数换成对下一刀也成立的表达式。
    # R4 再随迁（A22 第九刀 ④c，2026-10-03）：④c 再搬走 15 个顶层 def ⇒ 30 - 15 = 15。本行按
    # 「42 - ④b 名单 - ④c 名单」写成表达式，④d 若继续搬家同样不必改本行（名单在文件末尾定义，
    # 断言在运行期取全局名，故此处前向引用成立）。
    assert (len(defs) == 42 - len(A22C9_MOVED_NAMES) - len(A22C10_MOVED_NAMES)
            and set(defs).isdisjoint(set(A22C8_MOVED_NAMES) | set(A22C10_MOVED_NAMES))), (
        "registry 顶层函数应为搬家后剩下的 %d 个（54 - ④a 12 - ④b 12 - ④c 15），一个不多一个不少"
        % (42 - len(A22C9_MOVED_NAMES) - len(A22C10_MOVED_NAMES)))


# ── A22 第九刀（④b，2026-10-03）：registry 插件同意/能力块 ↔ plugins/plugin_consent ──
# ④b 把 registry 的 12 个 consent/能力函数整体搬到 ``app/plugins/plugin_consent.py``，
# registry 侧具名重导出。预扫用 AST 实测（不是人眼扫）：**只有 3 个名字需要 `_reg.` 回指**
# ——``_logger``（4 个函数引用；tests 2 处打桩）/ ``_db_prov``（1 个函数引用；tests 5 处打桩）/
# ``get_plugin_provenance``（2 个函数引用；④a 从 registry 重导出而来）。``json`` 是标准库，
# 新模块直接 import，不走回指。
# ⚠ 本刀特有的**跨刀耦合**：④a 的守卫把 ``_parse_perms`` / ``_upsert_plugin_consent`` /
#   ``backfill_plugin_consents_once`` 写进了 ``A22C8_STAY_REFS``（要求 plugin_store 以 ``_reg.<name>``
#   调用它们），而这三个名字 ④b 又搬走了 ⇒ 它们必须仍能经 registry 的重导出解析到实现，
#   否则 ④a 的穿透例会「守卫变绿却扫真库」。下面第 7 条专门钉这件事。
A22C9_MOVED_NAMES = (
    "_parse_perms", "_upsert_plugin_consent", "backfill_plugin_consents_once",
    "consent_state", "consent_matches", "get_plugin_consented_permissions",
    "get_tenant_consented_permissions", "grant_plugin_consent", "has_capability_permission",
    "require_plugin_consent", "resolve_tenant_for_user", "verify_plugin_signature",
)
# 仍留在 registry、必须靠 _reg. 现取的名字（tests 在其上打桩 ⇒ 不许在 plugin_consent 另立副本）
A22C9_STAY_REFS = {
    "_upsert_plugin_consent": {"_parse_perms"},
    "backfill_plugin_consents_once": {"_logger", "_parse_perms", "_upsert_plugin_consent"},
    "get_plugin_consented_permissions": {"get_plugin_provenance", "get_tenant_consented_permissions"},
    "get_tenant_consented_permissions": {"_logger", "_parse_perms"},
    "grant_plugin_consent": {"_db_prov", "_upsert_plugin_consent", "get_plugin_provenance"},
    "has_capability_permission": {"_logger", "get_tenant_consented_permissions"},
    "require_plugin_consent": {"consent_matches", "consent_state", "get_tenant_consented_permissions", "grant_plugin_consent"},
    "resolve_tenant_for_user": {"_logger"},
}
# 明确留在 registry 原地的，一个都不许被 ④b 顺手带走
# R4 随迁（A22 第九刀 · ④c，2026-10-03）：本名单原先含「④c 预留的 6 个可见性/作用域函数」
# （plugin_user_scope_enabled / plugin_visible_to_tenant / plugin_is_builtin /
#  clear_runtime_scope_caches / resolve_caller_tenant_cached / plugin_disabled_route_gate_enabled），
#  ④c 已把它们搬进 plugins/plugin_scope.py ⇒ 跟着搬家移除。断言原意一字未变（列出的名字必须仍在
#  registry 原地定义）；这 6 个名字的接缝改由 ④c 守卫（本文件末尾 A22C10_*）与 registry 的重导出块接管。
A22C9_STAY_DEFS = ("run_hook", "run_hook_collect", "list_plugins", "get_plugin",
                   "mount_plugin_routers", "preload_channels")
A22C9_REGISTRY_PY = _seam_src_path("app.plugins.registry")
A22C9_CONSENT_PY = _seam_src_path("app.plugins.plugin_consent")


def _a22c9_src(path) -> str:
    return Path(str(path)).read_text(encoding="utf-8")


def _a22c9_top_defs(src: str) -> list:
    return [n.name for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _a22c9_find_fn(path, name):
    for n in ast.walk(ast.parse(_a22c9_src(path))):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n


def test_A22第九刀b_1_重导出12函数两侧同一对象():
    import app.plugins.plugin_consent as consent
    import app.plugins.registry as reg

    assert len(A22C9_MOVED_NAMES) == 12
    for name in A22C9_MOVED_NAMES:
        assert hasattr(reg, name) and hasattr(consent, name), name
        assert getattr(reg, name) is getattr(consent, name), name + " 两侧必须同一对象（具名重导出）"
        assert getattr(reg, name).__module__ == "app.plugins.plugin_consent", (
            name + " 的 __module__ 只能是 plugin_consent（registry 侧不许复制一份实现）")


def test_A22第九刀b_2_打桩锚点仍在registry且没在plugin_consent另立副本():
    import app.plugins.plugin_consent as consent
    import app.plugins.registry as reg

    for name in ("_logger", "_db_prov", "get_plugin_provenance"):
        assert hasattr(reg, name), name + " 必须仍挂在 registry 上（tests 在 registry 打桩）"
        assert not hasattr(consent, name), (
            name + " 不得在 plugin_consent 里另立一份 —— 有副本则 setattr(registry, ...) 的桩静默失效")
    assert isinstance(getattr(reg, "_db_prov"), dict), "_db_prov 必须是同一个 dict 本体"


def test_A22第九刀b_3_plugin_consent顶层函数恰好等于搬家清单():
    assert sorted(_a22c9_top_defs(_a22c9_src(A22C9_CONSENT_PY))) == sorted(A22C9_MOVED_NAMES), (
        "新文件顶层 def 必须恰好是 ④b 的 12 个：既没漏搬，也没顺手多搬")


def test_A22第九刀b_4_reg现取静态锚定_变异守卫():
    """把某处 `_reg._logger` 改回裸名，本例必须立即变红（§5.3 变异自测的可复核形式）。"""
    for fn_name, stubbed in A22C9_STAY_REFS.items():
        fn = _a22c9_find_fn(A22C9_CONSENT_PY, fn_name)
        assert fn is not None, fn_name
        bare = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        reg_attrs = {a.attr for a in ast.walk(fn)
                     if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                     and a.value.id == "_reg"}
        assert bare & stubbed == set(), "%s 出现裸桩名 %s（应改回 _reg.）" % (fn_name, bare & stubbed)
        assert stubbed <= reg_attrs, "%s 缺 _reg. 现取 %s" % (fn_name, stubbed - reg_attrs)
        has_reg = any(isinstance(n, ast.ImportFrom) and n.module == "app.plugins"
                      and any(a.name == "registry" and a.asname == "_reg" for a in n.names)
                      for n in ast.walk(fn))
        assert has_reg, "%s 缺函数内 registry as _reg 现取 import" % fn_name


def test_A22第九刀b_5_穿透_patch_registry_logger后异常分支必须命中桩(monkeypatch):
    """打桩 ``registry._logger`` 后驱动 ``resolve_tenant_for_user`` 的失败分支：桩必须被命中。

    写成裸名或 import 期绑定时会拿到 plugin_consent 自己那份 logger ⇒ registry 上 2 处
    ``setattr(registry, "_logger", ...)`` 静默失效，正是「测试变绿却用真实现」。
    零 DB：把 ``async_session_factory`` 换成一个直接抛的假工厂，异常在 try 内被吃掉。
    """
    import app.db.database as dbmod
    import app.plugins.plugin_consent as consent
    import app.plugins.registry as reg

    hits = []

    class _RecLogger:
        def warning(self, *a):
            hits.append(a)

        def info(self, *a):
            hits.append(a)

    def _boom():
        raise RuntimeError("a22c9 sentinel")

    monkeypatch.setattr(reg, "_logger", _RecLogger())
    monkeypatch.setattr(dbmod, "async_session_factory", _boom)
    assert asyncio.run(consent.resolve_tenant_for_user(7)) is None
    assert hits, ("_logger 桩没被命中 ⇒ plugin_consent 里是裸名或 import 期绑定"
                  " ⇒ registry._logger 的 2 处桩静默失效")


def test_A22第九刀b_6_顶层不成环():
    tree = ast.parse(_a22c9_src(A22C9_CONSENT_PY))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert not (node.module == "app.plugins"
                        and any(a.name == "registry" for a in node.names)), (
                "顶层不得 import app.plugins.registry（会与 registry→plugin_consent 成环）")
        if isinstance(node, ast.Import):
            assert not any(a.name == "app.plugins.registry" for a in node.names)
    import app.plugins.plugin_consent as consent
    assert "registry" not in vars(consent), "registry 不得出现在 plugin_consent 的模块命名空间里"


def test_A22第九刀b_7_跨刀耦合_plugin_store的回指在搬家后仍解析得到实现():
    """④a 的 plugin_store 用 ``_reg._parse_perms`` / ``_reg._upsert_plugin_consent`` /
    ``_reg.backfill_plugin_consents_once``；这三个名字 ④b 搬走了 ⇒ 必须仍能经 registry
    重导出解析到 plugin_consent 的实现，否则 ④a 的穿透例会静默走真库。
    """
    import app.plugins.registry as reg

    for name in ("_parse_perms", "_upsert_plugin_consent", "backfill_plugin_consents_once"):
        target = getattr(reg, name, None)
        assert callable(target), name + " 必须仍能从 registry 取到（④a 的 _reg.<name> 靠它）"
        assert target.__module__ == "app.plugins.plugin_consent", (
            name + " 应解析到 plugin_consent 的实现，而不是被 registry 另立一份")


def test_A22第九刀b_8_源码锚定原定义消失且留在原地的一个没搬():
    reg_src = _a22c9_src(A22C9_REGISTRY_PY)
    for name in A22C9_MOVED_NAMES:
        assert re.search("^(?:async )?def " + re.escape(name) + chr(92) + "b", reg_src, re.M) is None, (
            name + " 的原定义应已从 registry 消失")
    for name in A22C9_STAY_DEFS:
        assert re.search("^(?:async )?def " + re.escape(name) + chr(92) + "b", reg_src, re.M), (
            name + " 必须仍在 registry 原地定义（属 ④c 或 hook 分发面）")
    assert "from app.plugins.plugin_consent import" in reg_src, "registry 侧必须有具名重导出块"
    defs = _a22c9_top_defs(reg_src)
    assert set(defs).isdisjoint(A22C9_MOVED_NAMES), "④b 搬走的名字不得仍在 registry 顶层定义"


# ── A22 第九刀（④c，2026-10-03）：registry 租户可见性/运行时作用域块 ↔ plugins/plugin_scope ──
# ④c 把 registry 的 15 个「可见性与作用域」函数整体搬到 ``app/plugins/plugin_scope.py``，registry 侧
# 具名重导出。本刀与 ④a/④b 的结构性差别是：**状态本体一个都不许搬**——``_RUNTIME_SCOPE_TTL`` 与三个
# 缓存（``_visible_names_cache`` / ``_caller_tenant_cache`` / ``_warned_no_caller``）必须仍是 registry
# 的那一份，因为 tests 直接读 ``registry._warned_no_caller``（test_plugin_runtime_scope_m4.py:240）、
# 并把 ``clear_runtime_scope_caches`` 当打桩目标（本文件 ④a 的 test_A22第八刀a_5_*，行号会随追加漂移故不写死）。plugin_scope 只经
# ``_reg.`` **就地**读写它们；一旦在新模块另立副本，哨兵注入与桩会同时静默失效（守卫第 2、7 条钉死）。
# 预扫三口径实测（不是人眼扫）：对象式打桩 2 处——``clear_runtime_scope_caches``（1）与
# ``plugin_disabled_route_gate_enabled``（test_mount_plugin_routers_enabled_gate.py:44）；
# 字符串路径 ``app.plugins.registry.<本刀名字>`` **0 处**；scripts/ 与 app/ 里的写死源码锚点 **0 处**
# （extension_audit 的 B-3 早在 ④b 就改成「按定义处解析」，不再认 registry.py）。
A22C10_MOVED_NAMES = (
    "plugin_user_scope_enabled", "plugin_visible_to_tenant", "plugin_runtime_scope_enabled",
    "plugin_is_builtin", "clear_runtime_scope_caches", "resolve_caller_tenant_cached",
    "_visible_plugin_names", "_runtime_scope_viewer", "plugin_in_runtime_scope",
    "plugin_visible_for_caller", "plugin_disabled_route_gate_enabled", "plugin_http_gate",
    "_warn_no_caller_once", "_resolve_hook_scope", "resolve_viewer_tenant",
)
# 状态本体：留在 registry、在 plugin_scope 里**不许出现同名对象**（另立副本＝桩与哨兵双双静默失效）
A22C10_STATE = ("_RUNTIME_SCOPE_TTL", "_visible_names_cache", "_caller_tenant_cache", "_warned_no_caller")
# 仍在 registry 原地定义的（hook 分发与 API 面），④c 一个都不许顺手带走
A22C10_STAY_DEFS = ("run_hook", "run_hook_collect", "list_plugins", "get_plugin", "set_plugin_state",
                    "run_plugin_action", "mount_plugin_routers", "preload_channels",
                    "push_sdk_context", "reset_sdk_context", "current_sdk_context")
# 每个搬走的函数里「必须以 _reg.<name> 出现、禁止裸名」的名字（变异守卫用；还原任一处 _reg. 立即变红）。
# 名字来自预扫 AST 实测：_time 是标准库、与本刀无关，按 ④b 的 json 同法在新模块顶层 import。
A22C10_STAY_REFS = {
    "plugin_is_builtin": {"_db_prov"},
    "clear_runtime_scope_caches": {"_visible_names_cache", "_caller_tenant_cache", "_warned_no_caller"},
    "resolve_caller_tenant_cached": {"_RUNTIME_SCOPE_TTL", "_caller_tenant_cache", "resolve_tenant_for_user"},
    "_visible_plugin_names": {"_RUNTIME_SCOPE_TTL", "_db_prov", "_loaded", "_visible_names_cache",
                              "plugin_visible_to_tenant"},
    "_runtime_scope_viewer": {"resolve_caller_tenant_cached"},
    "plugin_in_runtime_scope": {"plugin_runtime_scope_enabled", "_runtime_scope_viewer",
                                "plugin_is_builtin", "_visible_plugin_names"},
    "plugin_visible_for_caller": {"plugin_in_runtime_scope"},
    "plugin_http_gate": {"_enabled", "push_sdk_context", "reset_sdk_context",
                         "plugin_disabled_route_gate_enabled", "plugin_visible_for_caller"},
    "_warn_no_caller_once": {"_logger", "_warned_no_caller"},
    "_resolve_hook_scope": {"plugin_runtime_scope_enabled", "_loaded", "_runtime_scope_viewer",
                            "plugin_is_builtin", "_visible_plugin_names", "_warn_no_caller_once"},
    "resolve_viewer_tenant": {"plugin_user_scope_enabled", "resolve_tenant_for_user"},
}
A22C10_REGISTRY_PY = _seam_src_path("app.plugins.registry")
A22C10_SCOPE_PY = _seam_src_path("app.plugins.plugin_scope")


def _a22c10_src(path) -> str:
    return Path(str(path)).read_text(encoding="utf-8")


def _a22c10_top_defs(src: str) -> list:
    return [n.name for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _a22c10_find_fn(path, name):
    for n in ast.walk(ast.parse(_a22c10_src(path))):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n


def test_A22第九刀c_1_重导出15函数两侧同一对象():
    import app.plugins.plugin_scope as scope
    import app.plugins.registry as reg

    assert len(A22C10_MOVED_NAMES) == 15
    for name in A22C10_MOVED_NAMES:
        assert hasattr(reg, name) and hasattr(scope, name), name
        assert getattr(reg, name) is getattr(scope, name), name + " 两侧必须同一对象（具名重导出）"
        assert getattr(reg, name).__module__ == "app.plugins.plugin_scope", (
            name + " 的 __module__ 只能是 plugin_scope（registry 侧不许复制一份实现）")


def test_A22第九刀c_2_状态本体只在registry且没在plugin_scope另立副本():
    import app.plugins.plugin_scope as scope
    import app.plugins.registry as reg

    for name in A22C10_STATE:
        assert hasattr(reg, name), name + " 必须仍挂在 registry 上（tests 直接读 registry._warned_no_caller）"
        assert not hasattr(scope, name), (
            name + " 不得在 plugin_scope 里另立一份 —— 有副本则 setattr(registry, ...) 与哨兵注入静默失效")
    # 可变全局必须是同一个对象本体（就地 clear/写＝跨模块共享；重导出不得换成新对象）
    for name in ("_visible_names_cache", "_caller_tenant_cache"):
        assert isinstance(getattr(reg, name), dict), name
    assert isinstance(reg._warned_no_caller, set)
    assert isinstance(reg._RUNTIME_SCOPE_TTL, float)


def test_A22第九刀c_3_plugin_scope顶层函数恰好等于搬家清单():
    defs = _a22c10_top_defs(_a22c10_src(A22C10_SCOPE_PY))
    assert sorted(defs) == sorted(A22C10_MOVED_NAMES), (
        "新文件顶层 def 必须恰好是 ④c 的 15 个：既没漏搬，也没顺手多搬（实际：%s）" % sorted(defs))
    # _time 是标准库单调钟、不在打桩面上，按 ④b 的 json 同法在新模块顶层 import（不许经 _reg. 绕）
    assert re.search("^import time as _time", _a22c10_src(A22C10_SCOPE_PY), re.M), (
        "plugin_scope 顶层应 import time as _time")
    src = _a22c10_src(A22C10_SCOPE_PY)
    assert "_reg._time" not in src, "_time 不该被当成 registry 的桩名"


def test_A22第九刀c_4_reg现取静态锚定_变异守卫():
    """把某处 `_reg._logger` 改回裸名，本例必须立即变红（§5.3 变异自测的可复核形式）。"""
    for fn_name, stubbed in A22C10_STAY_REFS.items():
        fn = _a22c10_find_fn(A22C10_SCOPE_PY, fn_name)
        assert fn is not None, fn_name
        bare = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        reg_attrs = {a.attr for a in ast.walk(fn)
                     if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                     and a.value.id == "_reg"}
        assert bare & stubbed == set(), "%s 出现裸桩名 %s（应改回 _reg.）" % (fn_name, bare & stubbed)
        assert stubbed <= reg_attrs, "%s 缺 _reg. 现取 %s" % (fn_name, stubbed - reg_attrs)
        has_reg = any(isinstance(n, ast.ImportFrom) and n.module == "app.plugins"
                      and any(a.name == "registry" and a.asname == "_reg" for a in n.names)
                      for n in ast.walk(fn))
        assert has_reg, "%s 缺函数内 registry as _reg 现取 import" % fn_name
        # 反向自证：_reg. 后面挂的名字必须真的存在于 registry 命名空间（防止把桩名打错成静默 AttributeError）
        import app.plugins.registry as reg
        for attr in reg_attrs:
            assert hasattr(reg, attr), "%s 里 _reg.%s 解析不到" % (fn_name, attr)


def test_A22第九刀c_5_穿透_resolve_hook_scope必须走registry上的flag与谓词桩(monkeypatch):
    """打桩 registry 的 flag 口与可见性谓词后驱动**同样搬走的** ``_resolve_hook_scope``：桩必须被命中。

    写成裸名（或 import 期绑定）时，新模块解析到自己那份真实现 ⇒ fail-closed 集合与告警都会按真
    ``_db_prov`` 走，正是「测试变绿却用真实现」。零 DB：全程只碰哨兵 dict 与桩。
    """
    import app.plugins.plugin_scope as scope
    import app.plugins.registry as reg

    hits = []

    def _flag_on():
        hits.append("flag")
        return True

    async def _viewer_no_caller(user_id, tenant_id):
        hits.append("viewer")
        return None

    monkeypatch.setattr(reg, "plugin_runtime_scope_enabled", _flag_on)
    monkeypatch.setattr(reg, "_runtime_scope_viewer", _viewer_no_caller)
    monkeypatch.setattr(reg, "plugin_is_builtin",
                        lambda n: (hits.append("builtin:" + str(n)) or n == "inhouse"))
    monkeypatch.setattr(reg, "_loaded", {"inhouse": {}, "foreign": {}})
    monkeypatch.setattr(reg, "_warned_no_caller", set())

    allowed, viewer = asyncio.run(scope._resolve_hook_scope(
        "context_inject", user_id=None, tenant_id=None, callsite="a22c10"))

    assert "flag" in hits, "没走 registry.plugin_runtime_scope_enabled 桩（同批互调没现取）"
    assert "viewer" in hits, "没走 registry._runtime_scope_viewer 桩"
    assert allowed == frozenset({"inhouse"}) and viewer is None, (
        "fail-closed 集合不对：%s（应只留被桩判为内置的那个）" % (allowed,))
    assert reg._warned_no_caller == {"context_inject@a22c10"}, (
        "告警没写进 registry._warned_no_caller（⇒ _warn_no_caller_once 用的是裸名/副本）")


def test_A22第九刀c_6_穿透_http_gate禁用闸与上下文都走registry桩(monkeypatch):
    """``plugin_http_gate`` 是搬走的工厂，其闭包必须经 ``_reg.`` 取禁用闸桩、``_enabled`` 哨兵与 push/reset。

    改前 tests 里 ``setattr(registry, "plugin_disabled_route_gate_enabled", ...)`` 直接生效；闭包内写成
    裸名后桩静默失效 ⇒ 停用插件的自定义 REST 会「测试绿着继续 200」，这正是本刀最贵的失效模式。
    """
    from fastapi import HTTPException

    import app.plugins.plugin_scope as scope
    import app.plugins.registry as reg

    # ① 禁用闸开 + 插件停用 → 404
    monkeypatch.setattr(reg, "plugin_disabled_route_gate_enabled", lambda: True)
    monkeypatch.setattr(reg, "_enabled", {"p": False})

    async def _first(_gate):
        return await _gate.__anext__()

    gate = scope.plugin_http_gate("p")
    agen = gate(user_id=7, lang="zh")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(_first(agen))
    assert ei.value.status_code == 404, "禁用闸桩没命中（裸名 ⇒ registry 上的 setattr 静默失效）"

    # ② 两道闸都放行 → 过闸后必须 push/reset（caller 进上下文）
    hits = []

    def _push(name, **kw):
        hits.append(("push", name, kw))
        return "TOK"

    def _reset(token):
        hits.append(("reset", token))

    async def _visible(_name, _uid):
        hits.append("visible")
        return True

    monkeypatch.setattr(reg, "plugin_disabled_route_gate_enabled", lambda: False)
    monkeypatch.setattr(reg, "plugin_visible_for_caller", _visible)
    monkeypatch.setattr(reg, "push_sdk_context", _push)
    monkeypatch.setattr(reg, "reset_sdk_context", _reset)

    gate2 = scope.plugin_http_gate("p")
    agen2 = gate2(user_id=7, lang="zh")

    async def _drive():
        await agen2.__anext__()
        try:
            await agen2.__anext__()
        except StopAsyncIteration:
            pass

    asyncio.run(_drive())
    assert hits == ["visible", ("push", "p", {"user_id": 7}), ("reset", "TOK")], (
        "闸或上下文接缝没走 registry 桩：%s" % (hits,))


def test_A22第九刀c_7_穿透_缓存本体被就地读写且TTL也从registry取(monkeypatch):
    """哨兵缓存注入 registry 后：新模块的写必须落在同一个对象上（否则 30s 缓存与重扫失效各算各的）。"""
    import app.plugins.plugin_scope as scope
    import app.plugins.registry as reg

    seen_vis, seen_caller, seen_warn = {}, {}, set()
    monkeypatch.setattr(reg, "_visible_names_cache", seen_vis)
    monkeypatch.setattr(reg, "_caller_tenant_cache", seen_caller)
    monkeypatch.setattr(reg, "_warned_no_caller", seen_warn)
    monkeypatch.setattr(reg, "_RUNTIME_SCOPE_TTL", 9999.0)
    monkeypatch.setattr(reg, "_loaded", {"mine": {}, "other": {}})
    monkeypatch.setattr(reg, "_db_prov", {"mine": {"owner_tenant_id": 5},
                                          "other": {"owner_tenant_id": 9}})
    calls = []

    def _pred(**kw):
        calls.append(kw["owner_tenant_id"])
        return kw["owner_tenant_id"] == kw["viewer_tenant_id"]

    monkeypatch.setattr(reg, "plugin_visible_to_tenant", _pred)

    got = scope._visible_plugin_names(5)
    assert got == frozenset({"mine"}), "没走 registry.plugin_visible_to_tenant 桩"
    assert list(seen_vis.keys()) == [5] and seen_vis[5][1] == frozenset({"mine"}), (
        "缓存没写进 registry 的那个 dict 本体（⇒ plugin_scope 里是副本）")

    # 第二次必须吃 30s 缓存（TTL 也从 _reg. 取；桩数不增即证明读到了 registry 的大 TTL）
    got2 = scope._visible_plugin_names(5)
    assert got2 == got and calls == [5, 9], "缓存/TTL 现取失效（第二次又重算了）"

    async def _tenant(_uid):
        calls.append("tenant")
        return 42

    monkeypatch.setattr(reg, "resolve_tenant_for_user", _tenant)
    assert asyncio.run(scope.resolve_caller_tenant_cached(3)) == 42
    assert asyncio.run(scope.resolve_caller_tenant_cached(3)) == 42
    assert calls.count("tenant") == 1 and 3 in seen_caller, (
        "caller 缓存没落在 registry 的 _caller_tenant_cache 本体上")

    scope.clear_runtime_scope_caches()
    assert seen_vis == {} and seen_caller == {} and seen_warn == set(), (
        "clear_runtime_scope_caches 没清 registry 的三个状态本体")


def test_A22第九刀c_8_顶层不成环与源码锚定():
    """registry 先被导入也不能成环；搬走的名字原定义从 registry 消失、留原地的一律还在。"""
    import app.plugins.plugin_scope as scope

    for node in ast.parse(_a22c10_src(A22C10_SCOPE_PY)).body:
        if isinstance(node, ast.ImportFrom):
            assert not (node.module == "app.plugins"
                        and any(a.name == "registry" for a in node.names)), (
                "顶层不得 import app.plugins.registry（会与 registry→plugin_scope 成环）")
        if isinstance(node, ast.Import):
            assert not any(a.name == "app.plugins.registry" for a in node.names)
    assert "registry" not in vars(scope), "registry 不得出现在 plugin_scope 的模块命名空间里"

    reg_src = _a22c10_src(A22C10_REGISTRY_PY)
    for name in A22C10_MOVED_NAMES:
        assert re.search("^(?:async )?def " + re.escape(name) + chr(92) + "b", reg_src, re.M) is None, (
            name + " 的原定义应已从 registry 消失")
    for name in A22C10_STAY_DEFS:
        assert re.search("^(?:async )?def " + re.escape(name) + chr(92) + "b", reg_src, re.M), (
            name + " 必须仍在 registry 原地定义（hook 分发面与上下文）")
    assert "from app.plugins.plugin_scope import" in reg_src, "registry 侧必须有具名重导出块"
    for name in A22C10_STATE:
        assert re.search("^" + re.escape(name) + chr(92) + "b", reg_src, re.M), (
            name + " 状态本体必须仍在 registry 里定义（④c 只搬函数，不搬状态）")
    defs = _a22c10_top_defs(reg_src)
    assert set(defs).isdisjoint(A22C10_MOVED_NAMES), "④c 搬走的名字不得仍在 registry 顶层定义"
    # 重导出名单用 AST 取（禁止子串匹配：resolve_caller_tenant_cached 里就含 _caller_tenant_cache）
    imported = set()
    for node in ast.parse(reg_src).body:
        if isinstance(node, ast.ImportFrom) and node.module == "app.plugins.plugin_scope":
            imported = {a.name for a in node.names}
    assert imported == set(A22C10_MOVED_NAMES), (
        "registry 的 ④c 重导出名单必须恰好等于搬家清单（多一个＝状态被搬走，少一个＝桩锚点丢失）")
    assert imported.isdisjoint(A22C10_STATE), "状态名字不许出现在重导出名单里（状态只有一个家）"


# ── A22 第九刀（③c，2026-10-03）：`generate_proactive_event` 635 行 → 内部切函数 ──
# ③c 与前面各刀不同：**函数本体与它所在模块一律不动**。预扫口径②实测它有 2 处字符串路径打桩
# （`"app.scheduling.message_generator.generate_proactive_event"`：test_proactive_enhance.py:256 /
# test_scheduler_stats_fixes.py:223）⇒ 名字一挪就是 R1 那种「桩静默失效＝测试绿着真调 LLM」。
# 所以这一刀是「切超大函数」：
#   刀1  六个前置素材 loader 下沉 `scheduling/proactive_material.py`（回指面只有 2 个名字、
#        且 tests 0 处打桩：`RECALL_SHARED` / `format_memory_line`，仍按 R2 走 `_mg.` 现取）；
#   刀2  `for attempt in range(2)` 两轮生成循环 → 同文件 `_run_two_rounds`；
#   刀3  循环后六道守卫 → 同文件六个模块级函数。
# 刀2/刀3 刻意留在 message_generator 里：循环体与守卫里的 `_gen_with_reasoning` /
# `score_naturalness` / `_validate_segments` / `_logger` 全是 tests 打在 mg 上的名字，
# 同文件解析＝锚点零迁移（跨模块反而制造 12 个新接缝）。
A22C11_LOADER_NAMES = ("_load_user_profile", "_load_persona_extra", "_load_weather_line",
                       "_load_check_in_line", "_load_recent_memories", "_load_current_state_anchor")
A22C11_LOCAL_FNS = ("_run_two_rounds", "_guard_reality_conflict", "_guard_location_conflict",
                    "_guard_parrot", "_harvest_check_in", "_harvest_memo",
                    "_drop_if_no_visible_content")
# 刀1 的回指面（mg 模块级名字，新模块必须 _mg. 现取）
A22C11_MG_REFS = {"_load_recent_memories": {"RECALL_SHARED", "format_memory_line"}}
# 主函数尾部六道守卫的**调用顺序**（改前顺序，③c 一字未动；顺序变了行为就变：
# 例如「位置冲突整条不发」必须排在「字面重合」之前，否则复述判定会吞掉位置守卫的 obs 留痕）
A22C11_CALL_ORDER = ("_guard_reality_conflict", "_guard_location_conflict", "_guard_parrot",
                     "_harvest_check_in", "_harvest_memo", "_drop_if_no_visible_content")
A22C11_MG_PY = _seam_src_path("app.scheduling.message_generator")
A22C11_PM_PY = _seam_src_path("app.scheduling.proactive_material")


def _a22c11_src(path) -> str:
    return Path(str(path)).read_text(encoding="utf-8")


def _a22c11_top_defs(src: str) -> list:
    return [n.name for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _a22c11_find_fn(src, name):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n


def test_A22第九刀c3_1_loader六个两侧同一对象():
    import app.scheduling.message_generator as mg
    import app.scheduling.proactive_material as pm

    assert len(A22C11_LOADER_NAMES) == 6
    for name in A22C11_LOADER_NAMES:
        assert hasattr(mg, name) and hasattr(pm, name), name
        assert getattr(mg, name) is getattr(pm, name), name + " 两侧必须同一对象（具名重导出）"
        assert getattr(mg, name).__module__ == "app.scheduling.proactive_material", name


def test_A22第九刀c3_2_主函数体内不再有嵌套def():
    """③c 的全部意义：`generate_proactive_event` 不再是「函数套函数 + 635 行」。"""
    src = _a22c11_src(A22C11_MG_PY)
    fn = _a22c11_find_fn(src, "generate_proactive_event")
    assert fn is not None
    inner = [n.name for n in ast.walk(fn)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn]
    assert inner == [], "主函数里又出现嵌套 def：%s" % inner
    assert fn.end_lineno - fn.lineno + 1 <= 300, (
        "主函数已回涨到 %d 行（③c 收口时 263 行）" % (fn.end_lineno - fn.lineno + 1))
    for name in A22C11_LOCAL_FNS:
        assert _a22c11_find_fn(src, name) is not None, name + " 必须仍在 mg 里（同文件提取）"


def test_A22第九刀c3_3_gather九个协程个数与顺序不许变():
    """G-P2-2 的并发收益所在：9 个 loader 挤在同一个 asyncio.gather、按位置解包成 9 个名字。

    串行化或改顺序＝主动链路时延与限流行为都变（原注释写明「原串行约 10 次 DB/外部调用」）。
    """
    src = _a22c11_src(A22C11_MG_PY)
    fn = _a22c11_find_fn(src, "generate_proactive_event")
    g = next((n for n in ast.walk(fn) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", "") == "gather"), None)
    assert g is not None, "gather 不见了"
    order = [ast.unparse(a) for a in g.args]
    names = [re.match(r"([A-Za-z_][A-Za-z_0-9]*)\(", o).group(1) for o in order]
    assert len(names) == 9, "协程数不是 9：%s" % names
    assert names == ["_load_user_profile", "_load_persona_extra", "_load_weather_line",
                     "_load_check_in_line", "_load_recent_memories", "_load_recent_reflection",
                     "_load_current_state_anchor", "_load_scene_facts",
                     "_load_authoritative_user_location"], names
    # 六个下沉 loader 必须全部带实参（闭包捕获已取消，漏传＝运行期 NameError 而不是变绿）
    for o, n in zip(order, names):
        if n in A22C11_LOADER_NAMES:
            assert o.strip().endswith(")") and len(o.strip()) > len(n) + 2, n + " 没传参"


def test_A22第九刀c3_4_mg现取静态锚定_变异守卫():
    """把 `_mg.RECALL_SHARED` 改回裸名，本例必须立即变红（§5.3 变异自测的可复核形式）。"""
    src = _a22c11_src(A22C11_PM_PY)
    for fn_name, stubbed in A22C11_MG_REFS.items():
        fn = _a22c11_find_fn(src, fn_name)
        assert fn is not None, fn_name
        bare = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        mg_attrs = {a.attr for a in ast.walk(fn)
                    if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                    and a.value.id == "_mg"}
        assert bare & stubbed == set(), "%s 出现裸名 %s（应改回 _mg.）" % (fn_name, bare & stubbed)
        assert stubbed <= mg_attrs, "%s 缺 _mg. 现取 %s" % (fn_name, stubbed - mg_attrs)
        assert any(isinstance(n, ast.ImportFrom) and n.module == "app.scheduling"
                   and any(a.name == "message_generator" and a.asname == "_mg" for a in n.names)
                   for n in ast.walk(fn)), "%s 缺函数内 message_generator as _mg 现取 import" % fn_name
    # 反向：mg 侧这两个绑定必须还在（ruff 的「未使用 import」清理最容易把它们顺手删掉）
    import app.scheduling.message_generator as mg
    for name in ("RECALL_SHARED", "format_memory_line"):
        assert hasattr(mg, name), name + " 已从 mg 命名空间消失 ⇒ 下沉后的 _mg.<name> 直接 AttributeError"
    # 用 AST 判（不能拿子串比：本模块 docstring 里就写着「与 ③a/③b 的 `_mg._logger` 同一口径」）
    used_attrs = {n.attr for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                  and n.value.id == "_mg"}
    assert "logger" not in used_attrs and "_logger" not in used_attrs, (
        "loader 并没有引用 _logger，凭空造回指＝多一条假接缝：%s" % sorted(used_attrs))


def test_A22第九刀c3_5_proactive_material顶层不成环且恰好六个():
    src = _a22c11_src(A22C11_PM_PY)
    assert sorted(_a22c11_top_defs(src)) == sorted(A22C11_LOADER_NAMES), (
        "新模块顶层 def 必须恰好是刀1 的 6 个（实际：%s）" % _a22c11_top_defs(src))
    import app.scheduling.proactive_material as pm
    assert "message_generator" not in vars(pm), "顶层不得绑定 message_generator（顶层回指必成环）"
    for node in ast.parse(src).body:
        if isinstance(node, ast.ImportFrom):
            assert not (node.module == "app.scheduling"
                        and any(a.name == "message_generator" for a in node.names)), "顶层 import mg 会成环"
        if isinstance(node, ast.Import):
            assert not any(a.name == "app.scheduling.message_generator" for a in node.names)


def test_A22第九刀c3_6_三道dropped守卫真的判定得到():
    """行为穿透：守卫的「整条丢弃」必须真回传 dropped，而不是静默继续发。"""
    import app.scheduling.message_generator as mg

    # ① 字面重合：桩说复述 ⇒ dropped=True
    orig = mg._parrot_blocked
    mg._parrot_blocked = lambda segs, ctx: (True, 0.93)
    try:
        assert mg._guard_parrot(["在干嘛呢"], last_context="在干嘛呢", character_id=7) is True
    finally:
        mg._parrot_blocked = orig

    # ② 无可见内容：桩判无内容 ⇒ dropped=True
    orig2 = mg._has_visible_content
    mg._has_visible_content = lambda segs: False
    try:
        assert mg._drop_if_no_visible_content(["……"], character_id=7) is True
    finally:
        mg._has_visible_content = orig2

    # ③ 位置冲突：桩判每段都冲突 ⇒ (segments, True)；user_loc_line 为空 ⇒ 原样返回不丢弃
    import app.memory.location_guard as lg
    orig3 = lg.location_conflict
    lg.location_conflict = lambda s, loc: "长沙"
    try:
        kept, dropped = mg._guard_location_conflict(["他在长沙"], user_loc_line="权威位置：上海",
                                                    character_id=7)
        # 改前语义：全冲突时 return 的是**原 segments**（调用方直接丢弃，不重新赋值）
        assert dropped is True and kept == ["他在长沙"]
        kept2, dropped2 = mg._guard_location_conflict(["在干嘛"], user_loc_line="", character_id=7)
        assert dropped2 is False and kept2 == ["在干嘛"]
    finally:
        lg.location_conflict = orig3


def test_A22第九刀c3_7_主函数尾部调用顺序与提前返回接线():
    """六道守卫的调用顺序＝改前顺序；每道 dropped 之后必须紧跟原样的提前 return。

    顺序变了不会报错、只会静默改变「谁先决定不发」——所以只能靠结构断言钉。
    """
    src = _a22c11_src(A22C11_MG_PY)
    fn = _a22c11_find_fn(src, "generate_proactive_event")
    calls = [n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in A22C11_CALL_ORDER]
    assert sorted(calls) == sorted(A22C11_CALL_ORDER), "六道守卫少了一道或多道：%s" % calls
    ordered = [c for c in _a22c11_ordered_calls(fn)]
    assert ordered == list(A22C11_CALL_ORDER), "调用顺序变了：%s" % ordered
    body = ast.get_source_segment(src, fn)
    assert body.count("return [] if not return_reasoning else ([], last_reasoning)") >= 4, (
        "dropped 之后的提前 return 接线数量不对（改前是 4 处：位置/复述/自然度降级/无可见内容）")


def _a22c11_ordered_calls(fn):
    """按源码出现顺序（不是 ast.walk 的层级顺序）取守卫调用名。"""
    found = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in A22C11_CALL_ORDER:
            found.append((node.lineno, node.func.id))
    return [name for _, name in sorted(found)]


def test_A22第九刀c3_8_两轮循环的提前返回语义回传不丢():
    """`_run_two_rounds` 的 aborted 必须真能回传（首轮实测漏了收尾 return ⇒ 解包 None）。"""
    import inspect

    import app.scheduling.message_generator as mg

    src = inspect.getsource(mg._run_two_rounds)
    assert "return segments, last_reasoning, False" in src, (
        "循环自然结束后没有显式返回 ⇒ 调用方解包 None（TypeError: cannot unpack non-iterable NoneType）")
    assert src.count("return [], last_reasoning, True") == 1, "自主搜索「本轮不说」的 aborted 回传点应恰好 1 处"
    # 调用方必须把 aborted 还原成改前的返回形态
    caller = ast.get_source_segment(_a22c11_src(A22C11_MG_PY),
                                    _a22c11_find_fn(_a22c11_src(A22C11_MG_PY), "generate_proactive_event"))
    assert "if _aborted:" in caller and "return [] if not return_reasoning else ([], last_reasoning)" in caller


# ── A22 第九刀（⑤-c，2026-10-03）：legacy 的 13 段内联重算兜底已删除，本块钉住"不许复活" ──
# 背景：`build_context_legacy` 原有 13 段 `if 〔key〕 not in _registry_done:` —— 注册表某段抛异常时
# 由这里再算一遍。删除判据是**代码自己指定的前置观测**（提交 61ad71de，2026-09-01，原话
# 「为 legacy.py 删除前置观测 A/B……观测一版本无命中后再清」）：A＝context_legacy_flag_off、
# B＝context_section_failed，4.5 周内**双 0 命中**，而观测通道本身活跃（memory_obs 59,005 行 /
# 最近 7 天 52,988 行）⇒ 0 是真没触发，不是没在观测。
# 语义变化（有意为之）：section 抛异常时该段不再重算，而是落到默认值（"无"/"暂无"/空串），
# 缺哪一段从日志与 context_section_failed 留痕可见＝fail-visible；收益是「按 caller 过滤的查询」
# 从此只有一份实现（B 家族 43 处审计当初就是为了抓这份重复实现漏传 caller）。
A22C12_DROPPED_KEYS = ("chat_history", "world_facts", "moments", "pets", "user_emotion",
                       "user_manual_state", "phone_perception", "phone_desktop", "pending_timer",
                       "current_time", "location", "user_info", "cognitive_plan")
# 13 段的**默认值行**（兜底删了，名字必须还在——B 类覆盖块与尾部装配仍读它们）
A22C12_DEFAULTS = {
    "chat_history": 'chat_history = ""',
    "world_facts": 'world_facts_text = "无"',
    "moments": 'moments_text = "\\u6682\\u65e0"',
    "pets": 'pets_text = "无"',
    "user_emotion": 'user_emotion = "无"',
    "user_manual_state": 'user_manual_state = ""',
    "phone_desktop": 'phone_desktop = "无"',
    "current_time": 'current_time_str = ""',
    "location": 'location_text = ""',
    "user_info": 'user_notes_text = ""',
    "cognitive_plan": 'cognitive_plan = ""',
}
A22C12_LEGACY_PY = _seam_src_path("app.agent.context.legacy")


def _a22c12_src() -> str:
    return Path(str(A22C12_LEGACY_PY)).read_text(encoding="utf-8")


def test_A22第九刀e_1_十三段内联兜底不许复活():
    src = _a22c12_src()
    for k in A22C12_DROPPED_KEYS:
        assert ('"%s" not in _registry_done' % k) not in src, (
            k + " 的内联重算兜底又回来了 ⇒ 按 caller 过滤的查询重新变成两份实现")
        assert ('\'%s\' not in _registry_done' % k) not in src, k
    assert src.count("not in _registry_done") == 0, "兜底以别的形式复活了"


def test_A22第九刀e_2_默认值行一个都不许跟着删():
    src = _a22c12_src()
    for k, line in A22C12_DEFAULTS.items():
        assert line in src, k + " 的默认值赋值行被连带删掉了 ⇒ 尾部装配 NameError"


def test_A22第九刀e_3_注册表必须仍覆盖这十三个键():
    """核心防回归：将来谁新加/恢复一段 legacy 内联、而注册表没接管，本例立刻红。"""
    from app.agent.context.sections import get_sections

    keys = {s.key for s in get_sections()}
    missing = [k for k in A22C12_DROPPED_KEYS if k not in keys]
    assert missing == [], "这些键注册表没接管：%s（那就不该删 legacy 兜底）" % missing
    assert len(keys) >= 47, "注册表段数意外变少：%d" % len(keys)


def test_A22第九刀e_4_fail_visible出口必须还在():
    """删兜底后，「哪一段没产出」唯一的可见途径就是这条观测 + 一行 WARNING。"""
    src = Path(str(_seam_src_path("app.agent.context"))).read_text(encoding="utf-8")
    assert '"context_section_failed"' in src, (
        "context_section_failed 观测点消失 ⇒ section 异常变成完全静默，兜底已删、告警也没了")
    assert "context section %s failed" in src, "section 异常的 WARNING 日志被删"


def test_A22第九刀e_5_装配级内联对照已随兜底移除():
    """那 3 例的验证对象已不存在；反证在正证消失后会变成恒真空断言，所以必须一起删。"""
    # legacy.py 在 backend/app/agent/context/ ⇒ parents[3] 就是 backend
    failclosed = Path(str(A22C12_LEGACY_PY)).parents[3] / "tests" / "test_context_no_caller_failclosed.py"
    src = failclosed.read_text(encoding="utf-8")
    for gone in ("_run_legacy_assembly", "test_legacy_assembly_with_caller_injects",
                 "test_legacy_assembly_without_caller_failclosed",
                 "test_legacy_inline_pets_call_site"):
        assert gone not in src, gone + " 还在，但它验的代码已删 ⇒ 空断言"
    # 生产路径的 caller 隔离仍由 section 级用例钉住（不是删了就没人管）
    assert "def test_pets_section_failclosed" in src, "宠物那处的 section 级孪生用例必须仍在"
    assert "def _guard_assembled" in src, "纯 legacy 分支（MCP/trim）用例还在用这个夹具，不许一起清"


def test_A22第九刀e_6_legacy只剩一份分类实现():
    """⑤-c 顺带消灭的重复实现：消息分类只允许注册表侧一个调用点。"""
    from app.agent.context import section_persona
    import app.agent.context.legacy as lg

    assert "app.agent.message_classifier" not in _a22c12_src(), (
        "legacy 又引用分类器 ⇒ 与 section_persona 形成双实现")
    ps = Path(str(section_persona.__file__)).read_text(encoding="utf-8")
    assert "build_perception_section" in ps, "注册表侧的分类调用点消失了（唯一实现没了）"
    assert lg.build_context_legacy is not None
