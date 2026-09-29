# -*- coding: utf-8 -*-
"""outreach 投放口径三闸（2026-09-13 Codex→dsh 交接 §二）——纯决策层：常量 + 纯函数（零 IO）。

背景（交接 §一，09-13 复算）：主动消息 60 分钟回复率让位后 20.5% → 15.3%；瓶颈在**时机与内容**——
类型效应极强（plugin 88.5% / state_trigger 76.1% vs ai_care 3.2% / life_regression 13.6% /
memory_review 20.0%）；时段效应（晚 18–23 点 52.1% 最高）；单会话集中（session 11 窗口内 105 条）。
本模块只做三条闸「该不该投放」的纯判定；计数/查询/发送等 IO 留在 scheduling/arbiter.py。

三个独立开关（AGENT_FLAGS 登记，**默认 False = 逐字节现状**；置 False 即一键回退）：

- ``outreach_hour_window_v1``  ① 时段窗口闸：低效类型仅在 12:00–23:00（北京时间）投放；
- ``outreach_type_mix_v1``     ② 类型配比闸：memory_review ≤6/日、ai_care ≤4/日
  （并承载 memory_review「可回复化」提示词开关，见 scheduling/memory_review.py）；
- ``outreach_session_rate_v1`` ③ 单会话限频闸：同 (character_id, session_id) ≤8/日 且最小间隔 45 分钟。

灰度（对齐 M3-b ``section_working_state.WORKING_STATE_INJECT_GRAY_CHARS`` 写法）：开关开 **且**
角色在白名单 / 命中比例桶才生效。当前白名单只有 char13、比例 1.0。
约定：白名单置为空集 = 全量（扩量终点）；比例 1.0 = 白名单内全量。

边界（同 decision.py / outreach.py 的强约束）：本模块不感知 DB/FastAPI、
不 import 任何调度/模型模块；唯一的外部依赖「读运行时开关」经 PacingPorts
（app/domain/proactivity/ports.py）由上层注入，生产实现 =
app/application/proactivity_pacing_ports.production_pacing_ports（见文末「已去 IO」）。
"""
from __future__ import annotations

import hashlib

from app.domain.proactivity.ports import PacingPorts, PacingPortsNotInjected

# ── 开关名（必须与 app/agent/loop.py 的 AGENT_FLAGS 键一致；runtime_flags 只支持 bool 覆盖）──
FLAG_HOUR_WINDOW = "outreach_hour_window_v1"
FLAG_TYPE_MIX = "outreach_type_mix_v1"
FLAG_SESSION_RATE = "outreach_session_rate_v1"
PACING_FLAGS = (FLAG_HOUR_WINDOW, FLAG_TYPE_MIX, FLAG_SESSION_RATE)

# ── 灰度：角色白名单 + 比例桶（默认只 char13，先拿证据再扩量）──
OUTREACH_PACING_GRAY_CHARS = frozenset({13})
OUTREACH_PACING_RATIO = 1.0

# ── ① 时段窗口闸 ──
# 低效类型（交接 §①）：单向推送型，窗口外投放基本无回复；互动型/事件型不受此闸限制。
# memory_review_contextual 与 memory_review 同 message_type（REVIEW_TYPE），一并纳入以免口径分裂。
LOW_YIELD_TYPES = frozenset({
    "ai_care", "life_regression", "memory_review", "memory_review_contextual",
})
# 允许窗口 12:00–23:00（北京时间，半开区间 [12, 23)）；窗口外跳过，日志 `[gate=hour]`。
HOUR_WINDOW_START = 12
HOUR_WINDOW_END = 23

# ── ② 类型配比闸 ──
# 每角色每日上限（按「已发送」计数，口径见 arbiter.get_daily_sent_count）；
# 不新增「必须发够互动型」的强制项，避免行为突变。
TYPE_DAILY_LIMITS = {"memory_review": 6, "ai_care": 4}
# etype → 计数用 message_type（memory_review_contextual 与 memory_review 合并计数）。
TYPE_MIX_COUNTED_TYPES = {
    "memory_review": "memory_review",
    "memory_review_contextual": "memory_review",
    "ai_care": "ai_care",
}

# ── ③ 单会话限频闸 ──
# 与既有 MAX_PER_HOUR（角色维度）叠加、不替换；同样按「已发送」计数。
SESSION_DAILY_LIMIT = 8
SESSION_MIN_INTERVAL_MINUTES = 45
# 覆盖类型：会向用户会话推送、可延后的主动类型。
# 豁免（2026-09-13 Codex 拍板，dsh 复核建议）：以下三类**不进会话额度**——
#   plugin           抖音等渠道的评论/互动回复，属"用户触发的互动"，回复率最高（88.5%），必须送达；
#   state_trigger    状态事件互动，回复率 76.1%；
#   prospective_intent 一次性兑现（幂等已认领），被会话额度吞掉就永久丢失；
# 另外 timer（定时承诺必须兑现）与节庆/纪念日（必须送达）本就不在集合内。
SESSION_RATE_TYPES = frozenset({
    "greeting", "proactive_chat", "goodnight", "status_update",
    "memory_review", "memory_review_contextual", "emotion_care", "pet_remind",
    "ai_care", "life_regression", "motivation", "unfinished_topic",
})
# 显式声明豁免集合（供测试与排查引用）
SESSION_RATE_EXEMPT_TYPES = frozenset({"plugin", "state_trigger", "prospective_intent"})


# ── IO 端口（断点 #1 · V2b）：只有「取运行时开关表」这一项外部依赖 ──────────────
_DEFAULT_PORTS: PacingPorts | None = None


def set_default_pacing_ports(ports: PacingPorts | None) -> None:
    """注册默认端口实现（传 None 清空＝恢复「未注入」状态，供测试与排查使用）。"""
    global _DEFAULT_PORTS
    _DEFAULT_PORTS = ports


def _bind_production_ports() -> PacingPorts:
    """迁移期兼容钩子：无人注入时惰性绑定 application 侧生产实现（读的还是同一张 AGENT_FLAGS）。

    存在的理由：本模块的判定结果直接决定主动消息发不发，闸门里任何异常都会被 arbiter._pacing_gate
    的 fail-open 吞掉（表现为「开关悄悄失效」）。留着这条兜底，未注入调用点（如
    memory_review.replyable_question_enabled 的 flags=None 分支、既有测试直接调
    pacing.flag_on/gate_active）行为与改动前逐字一致；生产主路径由 scheduling/arbiter.py 显式注入。
    """
    from app.application.proactivity_pacing_ports import production_pacing_ports
    return production_pacing_ports


def _resolve_ports(ports: PacingPorts | None = None) -> PacingPorts:
    """取端口实现：显式注入优先 → 默认端口 → 惰性绑定生产实现 → 清晰报错（不静默降级）。"""
    global _DEFAULT_PORTS
    if ports is not None:
        return ports
    if _DEFAULT_PORTS is not None:
        return _DEFAULT_PORTS
    try:
        bound = _bind_production_ports()
    except PacingPortsNotInjected:
        raise
    except Exception as e:
        raise PacingPortsNotInjected(
            "pacing 开关端口未注入且生产实现绑定失败：请显式传入 PacingPorts（生产实现 "
            "app.application.proactivity_pacing_ports.production_pacing_ports）——架构地图断点 #1"
        ) from e
    _DEFAULT_PORTS = bound
    return bound


def _bucket_0_999(key: str) -> int:
    """稳定分桶：同一 key 恒定落在同一 0-999 桶（md5，跨进程/重启一致）。"""
    return int(hashlib.md5(str(key).encode("utf-8")).hexdigest()[:8], 16) % 1000


def traffic_hit(key: str, ratio: float) -> bool:
    """确定性小流量命中：桶号 < ratio*1000 即命中（纯函数）。

    ratio<=0 → 恒 False；ratio>=1 → 恒 True（全量）。与 M3-b 同法同口径。
    """
    if ratio <= 0:
        return False
    if ratio >= 1:
        return True
    return _bucket_0_999(key) < int(round(ratio * 1000))


def pacing_gray_hit(
    character_id,
    session_id=None,
    *,
    chars: frozenset = OUTREACH_PACING_GRAY_CHARS,
    ratio: float = OUTREACH_PACING_RATIO,
) -> bool:
    """角色是否命中灰度（白名单 + 比例桶）。

    - character_id 为空 / 非法 → False（fail-closed 到不生效）；
    - chars 为空集 → 跳过白名单校验（约定：空白名单 = 全量，扩量终点），仍受 ratio 约束；
    - 否则须在白名单内且命中 ``pacing:<cid>:<session|0>`` 比例桶。
    """
    if character_id is None:
        return False
    try:
        cid = int(character_id)
    except (TypeError, ValueError):
        return False
    if chars and cid not in chars:
        return False
    return traffic_hit(f"pacing:{cid}:{session_id if session_id is not None else 0}", ratio)


def flag_on(key: str, *, flags=None, ports: PacingPorts | None = None) -> bool:
    """读 ``AGENT_FLAGS``（经端口取；失败 fail-safe 返回 False = 走旧路径，与项目既有 flag 读法一致）。

    显式传 ``flags`` 时完全不碰端口（纯判定路径，零 IO）；端口自身取表失败按旧口径返回 False，
    只有「一个端口都拿不到」才抛 PacingPortsNotInjected（不静默降级成「开关恒关」）。
    """
    if flags is None:
        try:
            flags = _resolve_ports(ports).flags()
        except PacingPortsNotInjected:
            raise
        except Exception:
            return False
    try:
        return bool(flags.get(key, False))
    except Exception:
        return False


def gate_active(
    character_id,
    session_id,
    key: str,
    *,
    flags=None,
    chars: frozenset = OUTREACH_PACING_GRAY_CHARS,
    ratio: float = OUTREACH_PACING_RATIO,
    ports: PacingPorts | None = None,
) -> bool:
    """单个闸门是否对本次投放生效：开关开 **且** 角色命中灰度（默认关 = 恒 False）。"""
    if not flag_on(key, flags=flags, ports=ports):
        return False
    return pacing_gray_hit(character_id, session_id, chars=chars, ratio=ratio)


def _hour_in_ranges(cn_hour: int, ranges) -> bool:
    """小时是否落在活跃时段（支持跨天区间，如 [22, 2]）；非法输入跳过。"""
    for item in ranges or ():
        try:
            s, e = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if s <= e:
            if s <= cn_hour < e:
                return True
        elif cn_hour >= s or cn_hour < e:
            return True
    return False


def hour_window_allows(
    etype: str,
    cn_hour: int,
    *,
    active_hours=None,
    start: int = HOUR_WINDOW_START,
    end: int = HOUR_WINDOW_END,
) -> bool:
    """① 时段窗口闸纯判定：低效类型只在 [start, end) 投放；其余类型恒放行。

    个性化（交接 §①「能低成本做就做」）：用户已学到的活跃时段可**放宽**窗口
    （``active_hours`` 命中即允许）。只扩不缩——默认窗口永远有效，避免个性化反而裁掉
    数据里回复率最高的 18–23 点；无数据 / 解析失败回退默认窗口。
    """
    if etype not in LOW_YIELD_TYPES:
        return True
    if start <= cn_hour < end:
        return True
    return _hour_in_ranges(cn_hour, active_hours)


def type_mix_allows(etype: str, sent_today: int, *, limits=None) -> bool:
    """② 类型配比闸纯判定：该类型今日「已发送」数 < 日上限 → 放行；无上限类型恒放行。"""
    counted = TYPE_MIX_COUNTED_TYPES.get(etype)
    if counted is None:
        return True
    cap = (TYPE_DAILY_LIMITS if limits is None else limits).get(counted)
    if cap is None:
        return True
    return int(sent_today or 0) < int(cap)


def session_rate_allows(
    sent_today: int,
    minutes_since_last: float | None,
    *,
    daily_limit: int = SESSION_DAILY_LIMIT,
    min_interval_minutes: int = SESSION_MIN_INTERVAL_MINUTES,
) -> bool:
    """③ 单会话限频闸纯判定：日上限 + 最小间隔（两者都按「已发送」计数）。

    边界：``minutes_since_last == min_interval_minutes``（恰好 45 分钟）**放行**（半开区间）；
    无历史发送（None）只受日上限约束。
    """
    if int(sent_today or 0) >= int(daily_limit):
        return False
    if minutes_since_last is not None and float(minutes_since_last) < float(min_interval_minutes):
        return False
    return True


# ── 本文件已去 IO（架构地图断点 #1 · domain 去 IO 铺开 V2b，2026-09-29）──────────
# 1. 新增纯类型层 app/domain/proactivity/ports.py：PacingPorts 协议（typing.Protocol，
#    无框架，**1 个方法** = flags()）+ PacingPortsNotInjected；只依赖 typing。
# 2. 本模块去掉唯一一处跨层 IO import：原 flag_on 函数体内的
#    ``from app.agent.loop import AGENT_FLAGS`` 改为 ports.flags()；生产实现
#    app/application/proactivity_pacing_ports.production_pacing_ports 里仍是函数级惰性
#    import（与旧版同一位置、同一张表），故既有测试对 AGENT_FLAGS 的 setitem/setattr
#    打桩照旧生效。显式传 flags 的纯判定路径完全不碰端口。
# 3. 零行为核对：三个开关名、灰度白名单 frozenset({13}) 与比例 1.0、LOW_YIELD_TYPES /
#    TYPE_DAILY_LIMITS({"memory_review":6,"ai_care":4}) / TYPE_MIX_COUNTED_TYPES /
#    HOUR_WINDOW_START=12 / HOUR_WINDOW_END=23 / SESSION_DAILY_LIMIT=8 /
#    SESSION_MIN_INTERVAL_MINUTES=45 / SESSION_RATE_TYPES / SESSION_RATE_EXEMPT_TYPES
#    全部未动；hour_window_allows → _hour_in_ranges、type_mix_allows、session_rate_allows、
#    pacing_gray_hit（None/非法 id → False；空白名单=全量；ratio<=0 恒 False、>=1 恒 True）
#    四个纯函数的判定顺序与边界（含「恰好 45 分钟放行」）逐字未动；
#    flag_on 的两层 fail-safe（取表失败 → False、flags.get 异常 → False）逐字未动。
# 4. 接线：scheduling/arbiter.py `_pacing_gate` 四处 gate_active 显式传 ports（只加接线）；
#    scheduling/memory_review.py 与既有测试未改（靠第 5 条兜底，行为逐字一致）。
# 5. 遗留（下一批删）：`_DEFAULT_PORTS` + `_bind_production_ports` 这条迁移期兼容钩子。
#    它存在的唯一理由是「未注入调用点一旦抛错，会被 arbiter._pacing_gate 的 fail-open
#    吞成『开关悄悄失效』」；等所有调用点显式注入后整体删除，本文件即达 domain 零 IO 终态。
#
# 同类待做 domain 文件清单（只列名字，本批未改）：
# - app/domain/emotion/care.py（四个迁移期兼容钩子 + _LegacyCarePorts 段待删）
