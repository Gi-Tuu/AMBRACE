# -*- coding: utf-8 -*-
"""A37 批 3 守卫（审计 §4 批 3 · 守卫②）：速率闸的返回值只许用于**速率**，新鲜度判定不许塞进去。

为什么单独钉这一条（审计 §1 的共性病灶）：现网所有"发送前再查一遍"的写法都属于速率闸，
而真正缺的是新鲜度/新事件判定。两者一旦混进同一个返回值，读代码的人会把"额度用满"当成
"这事已经过时"（或反过来），口径就再也分不开了——所以这里钉的是**语义边界**，不是数量：

① ``executors/guards.pre_gates`` 里出现的闸名必须逐字等于钉死的速率/时段白名单，
   新增一道闸必须先来这里分类（是速率→进白名单；是新鲜度→**不许进**）；
② ``pre_gates`` 的返回值只有 ``False``／``None`` 二值（"现在能不能再发一条"），
   不许返回三档判定（cancel/regenerate/keep）、原因字符串或布尔之外的任何东西；
③ 反向：出口闸③（``scheduler.gate3_conflict_reason``）与落库前复检里**不许出现速率符号**
   （``MAX_PER_HOUR`` / ``hourly_active`` / pacing…）——新鲜度闸不掺额度，否则"读不到现状"
   会被算成"额度已满"这类完全不同的拦截理由；
④ 各自唯一实现点：闸②在 ``scheduling/freshness.py``、闸③出口在 ``scheduling/scheduler.py``、
   速率闸在 ``executors/guards.py``，三处不得互相 import 判定函数。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.scheduling.executors import pre_gates

APP = Path(__file__).resolve().parents[1] / "app"
GUARDS = (APP / "scheduling" / "executors" / "guards.py").read_text(encoding="utf-8")
SCHED = (APP / "scheduling" / "scheduler.py").read_text(encoding="utf-8")
ARB = (APP / "scheduling" / "arbiter.py").read_text(encoding="utf-8")

# ① 钉死：pre_gates 现在只叫这几样东西（全是"时段/活跃/额度"，没有一样读"现状新不新"）
GATE_CALLS = {
    "is_dnd_now",          # 免打扰时段
    "has_user_said_sleep",  # 用户说过睡觉（时段型）
    "is_user_active",       # 用户正在聊 → 暂停（活跃态，非新鲜度）
    "hourly_active",        # 每小时条数
    "pacing_gate",          # 投放口径三闸（时段窗/类型配比/单会话限频）
    "mark_gate",            # 留痕（不判定）
}
# ③ 钉死：这些符号是速率侧的，不许出现在新鲜度侧的实现体里
RATE_ONLY = ("MAX_PER_HOUR", "hourly_active", "get_hourly_active_count", "pacing_gate",
             "session_rate_allows", "type_mix_allows", "MIN_PROACTIVE_INTERVAL_MINUTES")
# ② 钉死：这些符号是新鲜度侧的，不许出现在速率闸源码里
FRESH_ONLY = ("gate3", "snapshot_at", "freshness", "revalidate", "proactive_freshness",
              "new_user_msg", "unknown_snapshot")


def _body(src: str, defline: str) -> str:
    """取出某个顶层函数/常量段的源码体（从 defline 到下一个顶层 def/class/分隔注释）。"""
    i = src.index(defline)
    m = re.search(r"\n(?:async )?def |\n# ──|\nclass ", src[i + len(defline):])
    return src[i:i + len(defline) + (m.start() if m else 6000)]


def _calls(block: str) -> set[str]:
    return set(re.findall(r"\bg\.([a-z_]+)\(", block))


def test_pregates_里的闸名逐字等于速率白名单():
    got = _calls(GUARDS)
    assert got == GATE_CALLS, (
        f"pre_gates 叫到的闸与白名单不一致：多出来 {sorted(got - GATE_CALLS)}、"
        f"少了 {sorted(GATE_CALLS - got)}。新增闸必须先来本测试分类——"
        "是速率/时段才进白名单；是新鲜度（读现状、判快照）一律不许塞进 pre_gates（审计守卫②）")


def test_pregates_源码不得出现新鲜度判据():
    hit = [tok for tok in FRESH_ONLY if tok in GUARDS]
    assert not hit, f"速率闸里混进了新鲜度符号 {hit}：两种口径一旦合体，'额度已满'就会被读成'这事过时了'"


def test_出口闸三实现体不得出现速率符号():
    body = _body(SCHED, "async def gate3_conflict_reason(")
    hit = [tok for tok in RATE_ONLY if tok in body]
    assert not hit, f"闸③（新鲜度）里掺了速率符号 {hit}：读不到现状会被算成额度类拦截，原因不可归因（I4/I5）"


def test_flush_速率复检不读消息表判新鲜度():
    """arbiter 的 flush 复检只做"生成侧已在查的速率/活跃"这一件事，新鲜度归出口。"""
    body = _body(ARB, "async def _flush_recheck_reason(")
    assert "is_user_active" in body and "get_hourly_active_count" in body and "MAX_PER_HOUR" in body, (
        "flush 侧的速率复检不见了＝C28 又回到「生成时查一次、发的时候不查」")
    for tok in ("gate3_conflict_reason", "ChatMessage", "created_at"):
        assert tok not in body, f"flush 速率复检里出现了 {tok}＝把新鲜度判定塞进了速率复检（守卫②）"


def test_pregates_返回值只有_False_与_None_两种():
    """二值口径：命中 False（跳过）、放行 None。任何字符串/枚举/三档判定都是口径污染。"""
    from app.scheduling.executors.context import GateBundle

    def _bundle(block_on: str):
        async def _dnd(*_a, **_k):
            return "dnd" if block_on == "is_dnd_now" else None

        async def _sleep(*_a, **_k):
            return True if block_on == "has_user_said_sleep" else False

        async def _active(*_a, **_k):
            return True if block_on == "is_user_active" else False

        async def _hourly(*_a, **_k):
            return 999 if block_on == "hourly_active" else 0

        async def _pacing(*_a, **_k):
            return "pacing" if block_on == "pacing_gate" else None

        return GateBundle(
            is_dnd_now=_dnd, has_user_said_sleep=_sleep, is_user_active=_active,
            hourly_active=_hourly, pacing_gate=_pacing, mark_gate=lambda *_a: None,
            session_factory=lambda: None, app_day_start=lambda: None,
        )

    item = {"type": "greeting", "candidate": {"character_id": 71, "user_id": 72}}
    names = ("is_dnd_now", "has_user_said_sleep", "is_user_active", "pacing_gate", "hourly_active")
    res = [asyncio.run(pre_gates(item, "greeting", _bundle(n))) for n in names]
    res.append(asyncio.run(pre_gates(item, "greeting", _bundle("none"))))
    for r in res:
        assert r is None or r is False, f"pre_gates 返回了 {r!r}：不是二值口径（不许把判定档位塞进返回值）"
    assert res[-1] is None, "桩全放行时 pre_gates 却返回 False＝有闸没被桩覆盖"
    assert res[:-1].count(False) >= 3, (
        f"五道速率闸只命中 {res[:-1].count(False)} 道（预期 ≥3）：闸被绕过或桩失效，本守卫会瞎掉")


def test_三种闸各只有一个实现点():
    """闸②（生成前）／闸③（发送前）／速率闸（投放前）各处其位，不许互相 import 判定函数。"""
    defs = {
        "pre_gates": "app/scheduling/executors/guards.py",
        "gate3_conflict_reason": "app/scheduling/scheduler.py",
        "pre_send_check": "app/scheduling/freshness.py",
    }
    for name, expect in defs.items():
        owners = [str(p.relative_to(APP.parent)).replace("\\", "/")
                  for p in APP.rglob("*.py")
                  if re.search(r"^(?:async )?def %s\(" % re.escape(name), p.read_text(encoding="utf-8"), re.M)]
        assert owners == [expect], f"{name} 的定义点漂了：{owners}（预期只有 {expect}）"
    # 速率闸不得 import 新鲜度模块（判定逻辑必须留在各自家里）
    assert "import freshness" not in GUARDS and "from app.scheduling.freshness" not in GUARDS


def test_闸三两档默认关_关时零额外查询():
    """默认关是硬口径（I10）：两档都关 ⇒ `gate3_flags()` 返回 (False, False)，出口一次查询都不发。"""
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling.scheduler import gate3_flags

    assert AGENT_FLAGS["proactive_gate3_shadow"] is False
    assert AGENT_FLAGS["proactive_gate3_enforce"] is False
    assert gate3_flags() == (False, False), "两档默认关却读出了开＝默认值被改了，逐字节旧行为不成立"
