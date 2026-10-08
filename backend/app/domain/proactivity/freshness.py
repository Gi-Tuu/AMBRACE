"""A39（A37 批 2）：主动通道「闸②＝生成前重取现状」的**纯判定**。

边界（强约束，由 `test_freshness_purity_a39.py` 钉住）：本模块**零 IO**——不 import DB／models／
llm_client／scheduling，不调模型；所有事实由 `app/scheduling/freshness.py` 读好后传进来。
判据本身**不新写第四套正则**：到达／吃药／结果兑现／剧情推进这四类信号的识别一律复用
`scheduling` 里已有的 `_signal_seen`／`ready_result_seen`／`_story_advanced`，本模块只吃它们的布尔结果。

三档口径（写死，别在调用处各自解释）：
  cancel      事项已经没有对象的必要 ⇒ 不调 LLM、不发送
  regenerate  事还该做，但**手里的现状过期了** ⇒ 用新现状重写一遍（多一次 LLM，需显式放行）
  keep        现状没变 ⇒ 照旧生成
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

CANCEL = "cancel"
REGENERATE = "regenerate"
KEEP = "keep"

VERDICTS = (CANCEL, REGENERATE, KEEP)

# clock（闹钟／"明早八点"）类承诺的**档窗**：只允许「到点前 2 分钟」到「到期时刻」之间提起。
# 现网缺陷（A37 现场 id=160）＝日期档窗被当成"当天全天"，于是提前 55′29″ 就发了。
CLOCK_PRE_WINDOW = timedelta(minutes=2)
# 日期型（只有日期没有时刻）的合法提起窗＝到期日当天（北京自然日），这条沿用旧口径不改。
DATE_GRANULARITY_HOURS = (0, 0)

# 「快照陈旧」的判定门槛：collect 到 run 之间隔多久算值得重取（秒）。
# 只用来决定"要不要重读"，不决定"要不要发"——后者由上面三档给。
STALE_AFTER_SECONDS = 60


@dataclass(frozen=True)
class FreshFacts:
    """一次判定的全部输入（调用方负责读好；None＝该项未知，一律按"没变"处理＝不误杀）。"""

    signal_seen: bool = False          # 到达／吃药这类事件信号是否已出现在新消息里
    story_advanced: bool = False       # 剧情是否已推进（旧 trigger 描述的事已经发生了）
    result_ready: bool = False         # "准备好了叫我"这类结果是否已兑现
    topic_active: bool = True          # 未完成话题是否仍是"进行中"
    user_replied: bool = False         # 该会话里是否已有新的用户发言（覆盖旧快照）
    state_changed: bool = False        # 判定所需的现状数值是否真的变了（八维／情绪这类会自己漂的）
    underlying_gone: bool = False      # 依据的那条事实本身已失效（记忆归档／话题收尾／动态被删）
    snapshot_age_seconds: float = 0.0  # collect→run 的跨度
    now: datetime | None = None
    due_start: datetime | None = None
    due_end: datetime | None = None
    exact_moment: bool = False         # 该承诺是否带**精确时刻**（"明早八点"）；只有日期＝False


def _restrict(channel: str, f: FreshFacts) -> FreshFacts:
    """把白名单之外的事实打回默认值（未知＝不触发），未知通道一律不动。"""
    allowed = CHANNEL_ALLOWED_FIELDS.get(channel)
    if not allowed:
        return f
    defaults = FreshFacts()
    keep = {k: (getattr(f, k) if k in allowed else getattr(defaults, k))
            for k in f.__dataclass_fields__}
    return FreshFacts(**keep)


def clock_in_window(f: FreshFacts) -> bool:
    """精确时刻型承诺是否落在合法提起窗内（纯函数，零 IO）。

    返回 False ⇒ 调用方必须 cancel（还没到点就不要提），这是 A37 id=160 那类提前兑现的根闸。
    没有 due 信息时一律放行（保持旧行为，不让本闸变成"读不到就一律不发"的隐性收紧）。
    """
    if f.now is None or f.due_start is None or f.due_end is None:
        return True
    if not f.exact_moment:
        return True                                  # 日期型＝当天全天可提，沿用旧口径
    return (f.now >= f.due_start - CLOCK_PRE_WINDOW) and (f.now <= f.due_end)


def is_stale(f: FreshFacts) -> bool:
    """快照是否已经老到该重取（只决定"要不要再读一次库"，不决定发不发）。"""
    return float(f.snapshot_age_seconds or 0) >= STALE_AFTER_SECONDS


def decide(channel: str, f: FreshFacts) -> tuple[str, str]:
    """返回 ``(档位, 原因)``。原因串进影子留痕，判效时按原因分档统计，不许只报一个总数。

    顺序是**有意的**：先判"还有没有必要"（cancel），再判"现状过没过期"（regenerate），
    最后 keep。反过来会让"已兑现的事项"还白花一次重新生成的钱。

    另一条硬约束：调用方就算把全部事实都塞进来，**本通道白名单之外的事实也不参与判定**
    （见 `CHANNEL_ALLOWED_FIELDS`）——否则一个通道多读一张表就会把别的通道的判据牵进来，
    而这种串扰在日志里根本看不出来。
    """
    f = _restrict(channel, f)
    if not clock_in_window(f):
        return CANCEL, "未到点档窗"
    if f.signal_seen:
        return CANCEL, "事件信号已出现"
    if f.result_ready:
        return CANCEL, "等待结果已兑现"
    if channel == "unfinished_topic" and not f.topic_active:
        return CANCEL, "话题已不再是进行中"
    if f.story_advanced:
        return CANCEL, "剧情已推进"
    if channel == "unfinished_topic" and f.user_replied:
        return CANCEL, "用户已接着说过（旧话题不必复述）"
    # 「老」本身不是重写的理由——白烧一次 LLM 却拿回同样的现状。必须**真的变了**才 regenerate。
    if f.underlying_gone:
        return CANCEL, "依据的事实已失效"
    if is_stale(f) and (f.user_replied or f.state_changed):
        return REGENERATE, "快照过期且现状已变"
    return KEEP, "现状未变"

# 通道 → 该通道允许读哪些事实（防"某个通道偷偷多读一张表"，守卫按这张表核范围）
CHANNEL_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "prospective_clock": frozenset({"signal_seen", "now", "due_start", "due_end", "exact_moment"}),
    "prospective_arrival": frozenset({"signal_seen", "story_advanced"}),
    "prospective_medication": frozenset({"signal_seen", "story_advanced"}),
    "timer_ready": frozenset({"result_ready", "signal_seen"}),
    "timer_general": frozenset({"result_ready", "signal_seen", "story_advanced"}),
    "unfinished_topic": frozenset({"topic_active", "user_replied", "underlying_gone",
                                 "snapshot_age_seconds"}),
    "state_trigger_delayed": frozenset({"user_replied", "state_changed", "story_advanced",
                                   "snapshot_age_seconds"}),
    "life_regression": frozenset({"user_replied", "underlying_gone", "snapshot_age_seconds"}),
    "moment_comment": frozenset({"snapshot_age_seconds", "underlying_gone"}),
}


def unknown_channel(channel: str) -> bool:
    return channel not in CHANNEL_ALLOWED_FIELDS


def shadow_mark(verdict: str, reason: str, extra: dict[str, Any] | None = None) -> str:
    """影子留痕的口径串：``[fresh=cancel|原因|k=v ...]``。

    判效脚本按 ``fresh=`` 前缀解析，别在别处再拼一份格式（A37 审计 §4 批 2 的验收要求）。
    """
    parts = [f"fresh={verdict}", reason]
    for k, v in (extra or {}).items():
        parts.append(f"{k}={v}")
    return "[" + "|".join(parts) + "]"


def window_seconds(now: datetime, due_start: datetime, due_end: datetime, *,
                   exact: bool = True) -> float:
    """离档窗还有多远（负数＝已出窗）；纯函数，只给读数与回归用。"""
    if not exact:
        return (due_end - now).total_seconds()
    return (now - (due_start - CLOCK_PRE_WINDOW)).total_seconds()


__all__ = [
    "CANCEL",
    "CHANNEL_ALLOWED_FIELDS",
    "CLOCK_PRE_WINDOW",
    "FreshFacts",
    "KEEP",
    "REGENERATE",
    "STALE_AFTER_SECONDS",
    "VERDICTS",
    "clock_in_window",
    "decide",
    "is_stale",
    "shadow_mark",
    "unknown_channel",
    "window_seconds",
]
