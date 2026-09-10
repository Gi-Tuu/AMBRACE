"""记忆时态与计划有效期判定（主动复习「回忆化」L0，2026-09-09；纯函数、零 LLM）。

tense 取值：
- enduring  恒久：印象/偏好/关系/身份/核心记忆（可反复回忆、可走到 S_MAX）
- episodic  往事：已发生的一次性事件（只能怀旧，低频）
- plan      未来计划/行程/安排（有有效期；未过期可临期确认，过期后只能怀旧）
- transient 瞬时状态（status 类，通常已走 world_facts TTL，复习不主动提）

误判守卫（交接要求）：中文将来时标记词在「转述/反问/假设/对话引用」里常见
（如"你说要去…生成了…事件"这类元对话），故命中引用词/疑问/否定时不判 plan；
_DONE_MARKERS（回来了/到家/结束了…）优先级最高——已完成的行程不再视为未过期计划。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# 将来时 / 计划信号（命中即倾向 plan；L4 维护脚本亦以其做 SQL 预筛，故公开）
PLAN_MARKERS = (
    "将去", "将要", "即将", "计划", "打算", "准备", "预备", "约好", "约定", "安排",
    "明天", "后天", "大后天", "下周", "下个星期", "过两天", "改天", "近期",
    "行程", "出发", "车票", "机票", "高铁", "火车", "号去", "号出发",
    "出差", "旅行", "旅游", "去旅游", "准备去", "要去", "会去", "打算去",
)
# 明确"已经回来 / 完成 / 结束"的反信号（命中则不判为未过期计划）
_DONE_MARKERS = ("回来了", "已回", "到家", "结束了", "玩完", "去过了", "完成了", "归来", "返程回到")

# 语境守卫：转述/元对话引用（"你说要去…生成了…事件"这类）不判 plan
_META_GUARDS = ("你说", "我说", "刚才说", "生成了", "事件", "bug", "报错", "日志", "你问", "我问")
# 语境守卫：疑问 / 假设（"要不要/是不是/吗？"）不判 plan
_QUESTION_GUARDS = ("？", "?", "吗", "要不要", "是不是", "该不该", "会不会")
# 语境守卫：对计划本身的否定 / 撤销不判 plan
_NEGATION_GUARDS = ("不打算", "没打算", "没计划", "不计划", "取消了", "改主意")

# 相对日期（以记忆创建时间为锚解释）
_REL_DAYS = {"明天": 1, "明儿": 1, "后天": 2, "大后天": 3, "下周": 7, "下个星期": 7, "过两天": 2}
# X月X日 / X号
_RE_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
_RE_NUM_HAO = re.compile(r"(\d{1,2})\s*号")

# 计划默认有效期（解析不到明确日期时的保守兜底；以记忆创建时间为锚，旧"近期"计划才会过期）
PLAN_DEFAULT_HORIZON_DAYS = 7
# 行程类（出行/车票/出发/旅游）在途缓冲：计划日期之后再给 N 天再判过期
TRIP_BUFFER_DAYS = 3


def _g(m, key: str, default=None):
    """统一取值器：同时兼容 ORM 实例与检索侧 dict（format_memory_line 入参是 dict）。

    缺字段安全返回 default（再按缺省语义降级为 enduring），不误伤 shared_events 那种
    只有 content/created_at 的极简 dict。
    """
    if isinstance(m, dict):
        return m.get(key, default)
    return getattr(m, key, default)


def _text(m) -> str:
    return f"{_g(m, 'title', '') or ''} {_g(m, 'content', '') or ''} {_g(m, 'why_it_matters', '') or ''}"


def _has_guard(text: str) -> bool:
    """语境守卫：命中引用/疑问/否定任一 → 不按将来时标记词判 plan。"""
    return (any(k in text for k in _META_GUARDS)
            or any(k in text for k in _QUESTION_GUARDS)
            or any(k in text for k in _NEGATION_GUARDS))


def classify_tense(m) -> str:
    """返回 enduring / episodic / plan / transient（纯规则，零 LLM）。"""
    mtype = _g(m, "memory_type", "") or ""
    sub = (_g(m, "sub_type", "") or "")
    t = _text(m)

    # 1) 恒久：核心记忆 / 关系身份 / 偏好 / 一般印象
    if _g(m, "is_core", False) or _g(m, "core_category", None) == "identity":
        return "enduring"
    if mtype in ("preference", "user_info") and sub != "extracted":
        return "enduring"
    if sub in ("relationship", "emotion"):
        return "enduring"
    # 2) L4 提取侧显式标注的计划（sub_type=plan）
    if sub == "plan":
        return "plan"
    # 3) 瞬时状态
    if sub == "status" or "状态更新" in t:
        return "transient"
    # 4) 未来计划：命中将来时标记词、无完成信号、无语境守卫
    if any(k in t for k in PLAN_MARKERS):
        if not any(k in t for k in _DONE_MARKERS) and not _has_guard(t):
            return "plan"
    # 5) 其余 event / 日记 / 时刻 = 往事（含含完成信号的"计划已结束"记录）
    if mtype in ("event", "insight"):
        return "episodic"
    return "enduring"


def _reference_time(m, now: datetime) -> datetime:
    """解释相对日期的锚点：记忆创建时间（缺失用 now）。"""
    ca = _g(m, "created_at", None)
    if ca is None:
        return now
    return ca.replace(tzinfo=None) if getattr(ca, "tzinfo", None) else ca


def _explicit_plan_date(m, now: datetime) -> datetime | None:
    """从文本解析计划日期（以记忆创建时间为锚解释"明天/8月18日"等；解析不到返回 None）。"""
    text = _text(m)
    ref = _reference_time(m, now)
    # 相对日期
    for k, d in _REL_DAYS.items():
        if k in text:
            return ref + timedelta(days=d)
    # X月X日
    mm = _RE_MD.search(text)
    if mm:
        mon, day = int(mm.group(1)), int(mm.group(2))
        try:
            dt = datetime(ref.year, mon, day)
            if dt < ref - timedelta(days=2):   # 该日相对记录时已过早 → 视为次年（兜底）
                dt = datetime(ref.year + 1, mon, day)
            return dt
        except ValueError:
            return None
    # X号（默认当月）
    mh = _RE_NUM_HAO.search(text)
    if mh:
        day = int(mh.group(1))
        try:
            dt = datetime(ref.year, ref.month, day)
            if dt < ref - timedelta(days=2):
                y, mo = (ref.year, ref.month + 1) if ref.month < 12 else (ref.year + 1, 1)
                dt = datetime(y, mo, day)
            return dt
        except ValueError:
            return None
    return None


def _is_trip(text: str) -> bool:
    return any(k in text for k in ("出行", "出发", "车票", "机票", "火车", "高铁", "出差", "旅游", "旅行", "行程"))


def plan_valid_until(m, now: datetime | None = None) -> datetime | None:
    """计划记忆的有效期截止（UTC naive，与 next_review_at 同口径）。

    优先用已写入的 valid_to；否则解析文本日期 + 在途缓冲；再否则以记忆创建时间为锚给默认水平窗。
    非 plan 类型返回 None。
    """
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    if classify_tense(m) != "plan":
        return None
    # 已显式写过 valid_to（L4 提取侧 / supersede）
    vt = _g(m, "valid_to", None)
    if vt is not None:
        return vt.replace(tzinfo=None) if getattr(vt, "tzinfo", None) else vt
    base = _explicit_plan_date(m, now)
    if base is None:
        base = _reference_time(m, now) + timedelta(days=PLAN_DEFAULT_HORIZON_DAYS)
    if _is_trip(_text(m)):
        base = base + timedelta(days=TRIP_BUFFER_DAYS)
    return base


def is_plan_expired(m, now: datetime | None = None) -> bool:
    """plan 且已过有效期；文本若已出现"回来了/结束"等完成信号也直接判过期。"""
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    if classify_tense(m) != "plan":
        return False
    if any(k in _text(m) for k in _DONE_MARKERS):
        return True
    vu = plan_valid_until(m, now)
    return vu is not None and now > vu


def days_since(m, now: datetime | None = None) -> int | None:
    """记忆记录日期距今天数（给生成 hint 用，制造时间距离感）。"""
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    ca = _g(m, "created_at", None)
    if ca is None:
        return None
    ca = ca.replace(tzinfo=None) if getattr(ca, "tzinfo", None) else ca
    return max(0, (now - ca).days)
