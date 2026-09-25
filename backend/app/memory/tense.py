"""记忆时态与计划有效期判定（主动复习「回忆化」L0，2026-09-09；纯函数、零 LLM）。

tense 取值：
- enduring  恒久：印象/偏好/关系/身份/核心记忆（可反复回忆、可走到 S_MAX）
- episodic  往事：已发生的一次性事件（只能怀旧，低频）
- plan      未来计划/行程/安排（有有效期；未过期可临期确认，过期后只能怀旧）
- transient 瞬时状态（status 类，通常已走 world_facts TTL，复习不主动提）

误判守卫（交接要求）：中文将来时标记词在「转述/反问/假设/对话引用」里常见
（如"你说要去…生成了…事件"这类元对话），故命中引用词/疑问/否定时不判 plan；
_DONE_MARKERS（回来了/到家/结束了…）优先级最高——已完成的行程不再视为未过期计划。
2026-09-17 批次一：位置/易变现状不再一律判「恒久画像」（任务1）；天然已发生来源整体提前到
PLAN_MARKERS 之前归往事（任务3）。

阶段 0 观测约定（2026-09-25，决策层接线）：本模块**保持同步纯函数**——不 await、不写库、不加埋点。
影子留痕一律发生在**调用方**：由 app/domain/decision/layer.py 的 observe_tense_decision 记录，
它只**读**本模块的 PLAN_MARKERS / _DONE_MARKERS / _has_guard / _text 做「判定输入」留痕，
不参与判定。因此这些常量与取值器改名/删除时判定本身不受影响，但留痕字段会跟着变——改前先确认
layer 侧取值仍然合理（开关 decision_layer_shadow 默认关，关时留痕链路整条不执行）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.memory.meta_guard import is_meta_without_anchor, title_evidence_ok

# 将来时 / 计划信号（命中即倾向 plan；L4 维护脚本亦以其做 SQL 预筛，故公开）
PLAN_MARKERS = (
    "将去", "将要", "即将", "计划", "打算", "准备", "预备", "约好", "约定", "安排",
    "明天", "后天", "大后天", "下周", "下个星期", "过两天", "改天", "近期",
    "行程", "出发", "车票", "机票", "高铁", "火车", "号去", "号出发",
    "出差", "旅行", "旅游", "去旅游", "准备去", "要去", "会去", "打算去",
)
# 明确"已经回来 / 完成 / 结束"的反信号（命中则不判为未过期计划）
_DONE_MARKERS = ("回来了", "已回", "到家", "结束了", "玩完", "去过了", "完成了", "归来", "返程回到")

# ── 易变现状（2026-09-17 批次一任务1）──
# 位置/在途/心情/状态类子类不再一律当「无时效恒久画像」：旧顺序里它们通常是 user_info/location，
# 被「user_info/preference 且 sub != extracted → enduring」直接吞掉，于是「在长沙」「从长沙回来」
# 全被当现行事实注入现状面（用户 8 月底已回湛江，AI 仍反复「你在长沙」）。
_VOLATILE_SUBTYPES = {"location", "current_location", "current_state", "trip", "mood", "status"}
# 位置里的「稳定态」标记：命中才算恒久（常驻地/家乡/定居），否则一律按易变处理
_STABLE_LOCATION_MARKERS = ("常驻", "常住", "家乡", "老家", "定居", "现居", "长期在")

# ── 天然已发生来源（2026-09-17 批次一任务3）──
# moment/diary/group(shared)/life_event/game_summary 只要文本含「打算/明天/要去/旅行/攻略/出发」
# 就会被 PLAN_MARKERS 判成 plan（生产库实证：同一家庭群聊发言在 id=10405/10409/10413 全判 plan）。
_HAPPENED_TYPES = {"event", "insight", "diary", "moment", "life_event", "game_summary", "summary", "shared_event"}
_HAPPENED_SUBS = {"moment", "diary", "group", "group_shared", "shared_event", "life_event", "game_summary", "game_result"}
# 生产库口径（2026-09-17 只读核对）：群聊逐条记忆的 sub_type 实际是 "group"（见 id=10405/10409/10413），
# 故在交接给出的 group_shared 之外补 "group"。
# 预计划短路豁免：memory_type="event" 是抽取侧「计划」记忆的默认载体，线上既有契约
# （test_memory_reminisce L0 语料 7018/7021「用户近期将去长沙」）要求「event + 计划词」仍判 plan；
# 完成信号那类由 _DONE_MARKERS 归往事，无计划词的 event 仍在第 5 步落 episodic。
# insight/moment/diary/群聊复盘/life_event/game_summary 则属「天然已发生来源」，整体提前归往事。
_PLAN_FIRST_EXEMPT = {"event"}

# 语境守卫：转述/元对话引用（"你说要去…生成了…事件"这类）不判 plan
_META_GUARDS = ("你说", "我说", "刚才说", "生成了", "事件", "bug", "报错", "日志", "你问", "我问")
# 语境守卫：疑问 / 假设（"要不要/是不是/吗？"）不判 plan
_QUESTION_GUARDS = ("？", "?", "吗", "要不要", "是不是", "该不该", "会不会")
# 语境守卫：对计划本身的否定 / 撤销不判 plan
_NEGATION_GUARDS = ("不打算", "没打算", "没计划", "不计划", "取消了", "改主意")

# ── 元对话 / 一次性琐事收窄兜底（2026-09-17 批次二任务1）──
# 生产实证：一次性琐事（"买了三盒披萨回家"，user_info/extracted）与对 AI 的情绪宣泄
# （id=9814「用户的名字」title + 记忆回退整段气话）走末尾兜底 return "enduring"，长期随画像注入。
# 收窄口径：能判定为「已发生」的走 episodic，未知来源才保守 enduring。
_META_GUARD_SUB = "meta_guard"   # extractor 元对话守卫降级时写入的 sub_type（恒非恒久）
# 一次性已发生动作（仅用于 user_info/extracted 兜底收窄；宁紧勿松，只认「了」类过去动作）
_ONE_OFF_ACTIONS = (
    "买了", "去了", "吃了", "喝了", "看了", "逛了", "做了", "拿了", "点了",
    "订了", "收到了", "遇到了", "见到了", "玩完",
)
# 数量词形态的一次性消费（生产实证 id=5856「买三盒披萨回家」无「买了」，需量词兜底）
_ONE_OFF_QUANT_RE = r"(?:买|吃|喝|拿|点|订)[\u4e00-\u9fa5]{0,2}(?:盒|杯|碗|份|斤|瓶|袋|个|块)"
_PAST_ACTION_RE = re.compile("|".join(_ONE_OFF_ACTIONS) + "|" + _ONE_OFF_QUANT_RE)

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


def is_happened_source(m) -> bool:
    """来源是否「天然已发生」（moment/diary/群聊共享/life_event/game_summary…）。

    2026-09-17 批次一任务3：注入侧（format_memory_line）据此统一打 tense_hint="episodic"，
    与 classify_tense 的 _HAPPENED_* 判定同源；兼容 memory_type 缺失、只用 type 的检索侧 dict。
    """
    mtype = (_g(m, "memory_type", "") or _g(m, "type", "") or "")
    sub = (_g(m, "sub_type", "") or "")
    return mtype in (_HAPPENED_TYPES - _PLAN_FIRST_EXEMPT) or sub in _HAPPENED_SUBS


def classify_tense(m) -> str:
    """返回 enduring / episodic / plan / transient（纯规则，零 LLM）。"""
    mtype = _g(m, "memory_type", "") or ""
    sub = (_g(m, "sub_type", "") or "")
    t = _text(m)

    # 1) 恒久：核心记忆 / 关系身份 / 偏好 / 一般印象
    if _g(m, "is_core", False) or _g(m, "core_category", None) == "identity":
        return "enduring"
    # 1.5) L4 提取侧显式标注的计划（sub_type=plan）**优先于按 memory_type 的恒久粗判**
    #      （2026-09-16 批次一任务3 修正判定顺序）：原顺序把 sub_type='plan' 排在
    #      「user_info/preference 且 sub != extracted → enduring」之后，导致显式计划标记被吞掉——
    #      线上实证 9865「用户说吃饱了要去睡觉，下午可能没课」(user_info/plan) 恒为 enduring，
    #      valid_to 永远不写、永不过期。显式标注是提取侧（review_plan_validity_extract）写下的权威
    #      计划信号，先判它不放宽「已过期」边界（is_plan_expired 仍要求已过有效期）。
    if (_g(m, "sub_type", "") or "") == "plan":
        return "plan"
    # 1.55) 元对话守卫（2026-09-17 批次二任务1）：extractor/response_parser 判定「讨论 AI 本身 /
    #       记忆机制 / 报错 / 情绪宣泄且无真实事实锚点」时写 sub_type=meta_guard 或泛化 title。
    #       这类内容既不是计划也不该进恒久画像（生产实证 id=9814 title「用户的名字」+ 记忆回退气话），
    #       恒归往事。必须排在 2.6「user_info/preference 恒久粗判」之前——存量行 sub_type 常为 NULL，
    #       放末尾兜底会被 2.6 提前吞掉。
    if sub == _META_GUARD_SUB or is_meta_without_anchor(t):
        return "episodic"
    # 1.52) title 证据锚点（批次二任务1.3，存量侧）：title 标「用户的名字/职业」但内容无证据
    #       （生产实证 id=6708 买酱油对话被标「用户的职业」）→ 泛化语义上等同「一段对话」，
    #       不进恒久画像（写侧已由 response_parser 泛化 title + 降 event 拦住新行）。
    if not title_evidence_ok(_g(m, "title", "") or "", _g(m, "content", "") or ""):
        return "episodic"
    # 1.6) 易变现状（2026-09-17 批次一任务1）：位置/在途/心情/状态类不再一律判恒久。
    #      位置：稳定态标记（常驻/老家/定居…）→ enduring；完成信号（回来了/到家…）→ episodic
    #      （已发生的往事）；其余（正在长沙/出差中）→ transient（瞬时现状，走 TTL，复习不主动提）。
    #      其余易变子类同理。status=stale/superseded/expired 的行另由 format.py 强制打
    #      ［往事/已过时］前缀，且「现状面」状态子句 current_facts_status_clause() 一律不取。
    if sub == "location":
        if any(k in t for k in _STABLE_LOCATION_MARKERS):
            return "enduring"
        if any(k in t for k in _DONE_MARKERS):
            return "episodic"      # 「从长沙回来了」= 已发生的往事
        return "transient"         # 「正在长沙/出差中」= 瞬时现状
    if sub in (_VOLATILE_SUBTYPES - {"location"}):
        if any(k in t for k in _DONE_MARKERS):
            return "episodic"
        return "transient"
    if mtype in ("preference", "user_info") and sub != "extracted":
        return "enduring"
    if sub in ("relationship", "emotion"):
        return "enduring"
    # 3) 瞬时状态
    if sub == "status" or "状态更新" in t:
        return "transient"
    # 3.5) 天然已发生来源（2026-09-17 批次一任务3）：把 moment/diary/群聊共享/life_event/
    #      game_summary 整体提到 PLAN_MARKERS 判定之前归往事（显式 sub_type=plan 已在最前面
    #      拦截），避免「已发生的记录」仅因文本含计划词被判未过期计划。
    if mtype in (_HAPPENED_TYPES - _PLAN_FIRST_EXEMPT) or sub in _HAPPENED_SUBS:
        return "episodic"
    # 4) 未来计划：命中将来时标记词、无完成信号、无语境守卫
    if any(k in t for k in PLAN_MARKERS):
        if not any(k in t for k in _DONE_MARKERS) and not _has_guard(t):
            return "plan"
    # 5) 其余 event / 日记 / 时刻 = 往事（含含完成信号的"计划已结束"记录）
    if mtype in ("event", "insight"):
        return "episodic"
    # 6) 收窄兜底（2026-09-17 批次二任务1）：未知来源才保守 enduring。
    #    a) 「天然已发生来源」再兜一层（与 3.5 同源，防后续新增分支把已发生来源漏回恒久）；
    if mtype in (_HAPPENED_TYPES - _PLAN_FIRST_EXEMPT) or sub in _HAPPENED_SUBS:
        return "episodic"
    #    b) user_info/extracted 的一次性已发生琐事（"买了三盒披萨回家"）→ 往事，不做恒久画像。
    if mtype == "user_info" and sub == "extracted" and _PAST_ACTION_RE.search(t):
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
