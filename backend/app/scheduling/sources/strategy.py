"""AMBRACE 3.10 / X6（2026-09-16）—— 主动「内容策略包」内核侧桥接。

职责边界（改动前先读，别越界）
------------------------------
- **内核（本模块 + 各策略源 + plugin 源 + arbiter）**：选人（roster）、频控、去重、
  关系门、免打扰、拍板与发送。
- **策略包（插件）**：只回答「今天该不该发、发哪一类、文案怎么说」，返回候选；
  **不做选人、不做频控、不去重、不判免打扰、不自己发消息**。

让位机制（防双发）
------------------
flag ``proactive_strategy_plugins``（默认 False）开 **且** 有已启用插件接管某类别时
（manifest ``config.strategy_category`` 声明 或 ``sdk.register_proactive_strategy`` 注册）：

1. 内核同名策略源整体让位（special / rhythm / memory_review）→ 同一触发日同一类别只有一个生产者；
2. 内核仍按 ``(character_id, message_type, 北京日界)`` 对策略候选做去重兜底
   —— 即使让位判定失效（如用户改了 config），也只会发一次；
3. flag 关 / 无策略包接管时：hook ctx 不下发 roster → 策略包返回空 →
   内核各源行为与现状逐字节一致（零行为变化）。

X6-b（2026-09-17）新增两个端口
------------------------------
1. **只读素材端口**（pull 式）：``build_proactive_context()`` 供 ``sdk.get_proactive_context``
   调用——策略包按需拉取内核侧只读素材（roster / character_state / due_reviews /
   recent_intents / time_ctx），manifest ``context_keys`` 白名单决定能读什么，逐 key
   fail-open（单 key 失败只丢该 key），单次返回体量设上限。
2. **类别注册口**：``register_strategy()`` 供 ``sdk.register_proactive_strategy`` 调用——
   让位表与 message_type 白名单由「内核兜底 + 插件注册」动态构建，冲突拒绝后加载者。

X6-c（2026-09-17）扩两个类别 + 四个素材 key
------------------------------------------
- 新增 ``motivation`` / ``unfinished_topic`` 两个可外放类别：二者各有**独立配额与独立
  生成链路**，除「让位 + 去重」外还登记了 **内核 prepare**（:func:`prepare_candidate`）——
  配额判定、去重、关系门、免打扰、素材装配全部在内核，策略包只给候选与「想发什么」；
- 素材端口补 4 个 key：``relationship`` / ``user_rhythm`` / ``quota`` / ``open_topics``
  （``quota`` 需类别参数，由 sdk 按调用插件自动推导或显式传入）；
- **去重口径按类别登记**（:data:`CATEGORY_DEDUP`）：节律/想念落库是 ``storyline``，
  按 ``proactive_message_logs`` 查 message_type 会空转，故这两类改用
  ``proactive_trigger_logs``(decision=approved) 判定「当日/近 6h 是否已发」。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.utils.logger import get_logger

_logger = get_logger("scheduler.sources.strategy")

# Feature Flag 名（硬编码默认在 app/agent/loop.py AGENT_FLAGS）
STRATEGY_FLAG = "proactive_strategy_plugins"
# 插件 manifest config 中声明「本包接管的策略类别」的键
STRATEGY_CATEGORY_KEY = "strategy_category"
# 策略候选声明落库口径的键（内核校验后才采信）
STRATEGY_CANDIDATE_KEY = "strategy"

# ── 类别注册（X6-b）────────────────────────────────────────────────────────
# 类别名 / message_type 命名规则：小写字母开头，仅小写字母数字下划线（防伪造内核事件类型）
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MESSAGE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_MESSAGE_TYPES = 8

# 内核兜底类别（内置策略源的 message_type 白名单）。
# 插件注册同名类别会被拒绝（与内置类别冲突）——内置口径由内核自己守。
BUILTIN_CATEGORIES: dict[str, tuple[str, ...]] = {
    "special": ("birthday", "holiday", "anniversary"),
}

# 内核执行路由：策略候选落回内核哪条执行链。
#   "candidate" = 用候选声明的 message_type 作为 arbiter 事件类型（走内核既定执行链，
#                 如 rhythm→剧情线、memory_review→run_memory_review、
#                 motivation→剧情线想念通道（独立配额）、unfinished_topic→run_unfinished_topic，
#                 频控/抽检语义不变）；
#   未登记 = 走 hint 生成路径（type="plugin"），第三方策略包默认全部落这里。
CATEGORY_EXEC_ROUTE: dict[str, str] = {
    "rhythm": "candidate",
    "memory_review": "candidate",
    "motivation": "candidate",
    "unfinished_topic": "candidate",
}
# 内核执行前处理（频控闸 + 素材装配）：类别 → 实现键（见 prepare_candidate）
# 这两类各有独立配额与独立生成链路，**必须**在内核 prepare 里把配额/去重/关系门/免打扰做完，
# 策略包只提供候选与「想发什么」（它无状态、每 tick 都投，靠 prepare 收口）。
CATEGORY_KERNEL_PREP: dict[str, str] = {
    "rhythm": "rhythm",
    "motivation": "motivation",
    "unfinished_topic": "unfinished_topic",
}

# 类别配额的计数口径（X6-c）：类别 → 计数来源。
#   "message_log" = proactive_message_logs(message_type=落库口径)（「已发送」口径，默认）；
#   "trigger_log" = proactive_trigger_logs(trigger_type=事件类型, decision=approved)
#                   （motivation 专用：想念消息落库 message_type 统一为 storyline，
#                    只有触发日志能区分，与 arbiter.get_motivation_approved_count 同口径）。
CATEGORY_QUOTA_SOURCE: dict[str, str] = {"motivation": "trigger_log"}

# 类别去重口径（X6-c）：类别 → (计数来源, 窗口)；未登记类别走 DEFAULT_DEDUP。
#   "day" = 北京当日已发过即不再发；"6h" = 近 6 小时已发过即不再发。
#   rhythm / motivation 走 trigger_log：二者落库 message_type 是 storyline，
#   按 proactive_message_logs 查会「去重闸空转」（与 X6-b 已知问题同源）。
CATEGORY_DEDUP: dict[str, tuple[str, str]] = {
    "rhythm": ("trigger_log", "day"),
    "memory_review": ("message_log", "day"),
    "motivation": ("trigger_log", "6h"),
    "unfinished_topic": ("message_log", "day"),
}
DEFAULT_DEDUP: tuple[str, str] = ("message_log", "day")

# category -> {"source": 插件名, "message_types": tuple[str, ...]}
_REGISTRY: dict[str, dict] = {}
# 注册被拒原因（加载告警；只留最近若干条，供排障与测试断言）
_REGISTRY_WARNINGS: list[str] = []
MAX_WARNINGS = 20

# ── 只读素材端口（X6-b）────────────────────────────────────────────────────
# 白名单 key（manifest.context_keys 只能从这里选；与 plugins/manifest.VALID_CONTEXT_KEYS 同步）
CONTEXT_KEYS: tuple[str, ...] = (
    "roster",           # 谁有资格（内核选人结果）
    "character_state",  # 角色当前八维状态（需 character_id）
    "due_reviews",      # 该角色到期/待复习记忆（需 character_id）
    "recent_intents",   # 该角色最近的前瞻意图（需 character_id）
    "time_ctx",         # 北京日期/小时/时段（全局）
    # X6-c：供 motivation / unfinished_topic 两类使用
    "relationship",     # 关系标量 trust/attachment/curiosity（需 character_id）
    "user_rhythm",      # 距上次用户消息小时数 + 用户活跃时段权重（需 character_id）
    "quota",            # 该类别 6h / 当日已用数与上限（需 character_id；见 quota_used）
    "open_topics",      # 未收尾话题 / 最近话头（需 character_id）
)
# 需要 character_id 的 key（不给 = 不下发，防跨角色取数）
PER_CHAR_CONTEXT_KEYS: tuple[str, ...] = (
    "character_state", "due_reviews", "recent_intents",
    "relationship", "user_rhythm", "quota", "open_topics",
)
# 单 key 条数上限（防插件把上下文吃爆）
CONTEXT_ITEM_LIMITS: dict[str, int] = {
    "roster": 20, "character_state": 8, "due_reviews": 3, "recent_intents": 3, "time_ctx": 8,
    "relationship": 8, "user_rhythm": 8, "quota": 8, "open_topics": 3,
}
# 文本字段截断长度
CONTEXT_SUMMARY_CHARS = 80
# 单次返回体量上限（字符，序列化后；超出则逐条瘦身）
MAX_CONTEXT_CHARS = 6000
# 角色状态八维（与 sdk.get_life_state 同口径）
_CHAR_STATE_KEYS = (
    "mood", "body_temp", "desire", "possessiveness",
    "fatigue", "sensitivity", "comfort", "anger",
)
# 关系标量（与 sdk.get_relationship 同口径）
_RELATION_KEYS = ("trust", "attachment", "curiosity")


def _warn(msg: str) -> None:
    """登记一条加载告警（有界）+ 打日志（供扩展页/排障）。"""
    try:
        _REGISTRY_WARNINGS.append(msg)
        del _REGISTRY_WARNINGS[:-MAX_WARNINGS]
    except Exception:
        pass
    _logger.warning("proactive strategy: %s", msg)


# ---------------------------------------------------------------- flag / 让位

def strategy_enabled() -> bool:
    """flag 是否开启（读 AGENT_FLAGS 内存；异常→False，即回退旧行为）。"""
    try:
        from app.agent.loop import AGENT_FLAGS

        return bool(AGENT_FLAGS.get(STRATEGY_FLAG, False))
    except Exception:
        return False


def register_strategy(category: str, message_types: Iterable[str], source: str) -> bool:
    """内核侧登记一个策略类别（插件加载期调用；冲突/非法 → False，不覆盖已有登记。

    拒绝规则（fail-back：被拒 = 该包不接管，内核行为不变）：
    1. 类别名非法（非 ``[a-z][a-z0-9_]{0,31}``）；
    2. 与内核内置类别冲突（``BUILTIN_CATEGORIES``，口径由内核守）；
    3. message_types 为空 / 非字符串 / 命名非法 / 超过 ``MAX_MESSAGE_TYPES``；
    4. 该类别已被**另一个**插件注册（先到先得，后加载者被拒并留加载告警）。
    同一插件重复登记（重载）= 覆盖，不算冲突。
    """
    try:
        cat = category.strip() if isinstance(category, str) else ""
        if not _CATEGORY_RE.fullmatch(cat):
            _warn(f"策略类别名非法被拒: {category!r}（source={source}）")
            return False
        if cat in BUILTIN_CATEGORIES:
            _warn(f"策略类别与内置类别冲突被拒: {cat}（source={source}）")
            return False
        mts = _normalize_message_types(message_types)
        if not mts:
            _warn(f"策略类别 {cat} 的 message_types 非法被拒（source={source}）")
            return False
        prev = _REGISTRY.get(cat)
        if prev and prev.get("source") != source:
            _warn(f"策略类别 {cat} 已被插件 {prev.get('source')} 注册，拒绝后加载者 {source}")
            return False
        _REGISTRY[cat] = {"source": source, "message_types": mts}
        return True
    except Exception as e:  # 隔离：注册失败只影响该包
        _warn(f"策略类别注册异常: {category!r}（source={source}）: {e}")
        return False


def _normalize_message_types(message_types: Iterable[str]) -> tuple[str, ...]:
    """清洗 message_type 白名单：保序去重、命名校验、条数上限；非法返回空元组。"""
    if isinstance(message_types, str) or not isinstance(message_types, Iterable):
        return ()
    out: list[str] = []
    for mt in message_types:
        s = mt.strip() if isinstance(mt, str) else ""
        if not _MESSAGE_TYPE_RE.fullmatch(s):
            return ()
        if s not in out:
            out.append(s)
        if len(out) > MAX_MESSAGE_TYPES:
            return ()
    return tuple(out)


def reset_registrations() -> None:
    """清空全部插件类别登记（sync_plugins_db 重扫前调用，防残留幽灵类别）。"""
    _REGISTRY.clear()
    _REGISTRY_WARNINGS.clear()


def strategy_registry() -> dict[str, dict]:
    """当前类别登记表快照（观测/测试用）：{category: {"source", "message_types"}}。"""
    return {k: {"source": v.get("source"), "message_types": tuple(v.get("message_types") or ())}
            for k, v in _REGISTRY.items()}


def strategy_warnings() -> list[str]:
    """最近若干条注册告警（加载告警）。"""
    return list(_REGISTRY_WARNINGS)


def category_message_types() -> dict[str, tuple[str, ...]]:
    """message_type 白名单：内核兜底 + 插件注册（冲突已拒，故不重叠）。"""
    out: dict[str, tuple[str, ...]] = dict(BUILTIN_CATEGORIES)
    for cat, rec in _REGISTRY.items():
        out[cat] = tuple(rec.get("message_types") or ())
    return out


def claimed_categories() -> set[str]:
    """当前【已启用】插件接管的策略类别集合（进程内缓存读取，零 DB）。

    来源二选一即可：manifest ``config.strategy_category`` 声明，或 ``sdk.register_proactive_strategy``
    注册。未启用 / 插件已卸载的登记不算接管。异常→空集（=内核不让位，回退旧行为）。
    """
    out: set[str] = set()
    try:
        from app.plugins.registry import list_plugins

        enabled: set[str] = set()
        for p in list_plugins():
            if not p.get("enabled"):
                continue
            enabled.add(str(p.get("name") or ""))
            cat = (p.get("config") or {}).get(STRATEGY_CATEGORY_KEY)
            if isinstance(cat, str) and cat.strip():
                out.add(cat.strip())
        out |= {cat for cat, rec in _REGISTRY.items() if rec.get("source") in enabled}
    except Exception as e:  # 隔离：扫描失败 = 不让位
        _logger.warning("strategy claims scan failed: %s", e)
        return set()
    return out


def category_yielded(category: str) -> bool:
    """该策略类别是否已被策略包接管（内核对应源应让位）。flag 关 → 恒 False。"""
    if not strategy_enabled():
        return False
    return category in claimed_categories()


def category_of(candidate: dict) -> str | None:
    """读出候选声明的策略类别（非空字符串才算）。"""
    v = (candidate or {}).get(STRATEGY_CANDIDATE_KEY)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def message_type_of(candidate: dict) -> str | None:
    """策略候选声明的落库口径（内核白名单校验后才采信，供去重与统计使用）。

    非策略候选 / 类别未知 / message_type 不在该类别白名单 → 返回 None（按普通插件候选处理）。
    """
    cat = category_of(candidate)
    if not cat:
        return None
    allowed = category_message_types().get(cat)
    if not allowed:
        return None
    mt = (candidate or {}).get("message_type")
    if isinstance(mt, str) and mt in allowed:
        return mt
    return None


def exec_type_of(candidate: dict) -> str:
    """策略候选在 arbiter 的事件类型（内核执行路由）。

    - 登记了 ``CATEGORY_EXEC_ROUTE == "candidate"`` 的类别：用其 message_type 作事件类型
      （走内核既定执行链，频控/抽检语义不变）；
    - 其余（含未登记类别，如第三方策略包）：``"plugin"``（hint 生成路径）。
    """
    cat = category_of(candidate)
    if not cat or CATEGORY_EXEC_ROUTE.get(cat) != "candidate":
        return "plugin"
    return message_type_of(candidate) or "plugin"


async def prepare_candidate(candidate: dict) -> dict | None:
    """内核执行前处理（频控闸 + 素材装配）：返回装配后的候选，None = 丢弃。

    只有登记了 ``CATEGORY_KERNEL_PREP`` 的类别才会被处理；其余原样返回。
    处理异常 → None（宁可不发，也不绕过内核频控）。

    X6-c：``motivation`` / ``unfinished_topic`` 两类各有独立配额与独立生成链路，
    配额判定 / 去重 / 关系门 / 免打扰 / 素材装配**全部在各自内核源里做**
    （策略包无状态、每 tick 都投同样的候选，靠这些闸收口成「一天只发该发的量」）。
    """
    cat = category_of(candidate)
    key = CATEGORY_KERNEL_PREP.get(cat or "")
    if not key:
        return candidate
    try:
        if key == "rhythm":
            from .rhythm import prepare_strategy_candidate as _prep
        elif key == "motivation":
            from .motivation import prepare_strategy_candidate as _prep
        elif key == "unfinished_topic":
            from .unfinished_topic import prepare_strategy_candidate as _prep
        else:
            return candidate
        return await _prep(candidate)
    except Exception as e:
        _logger.warning("strategy kernel prep failed(%s): %s", cat, e)
        return None


def category_of_source(source: str) -> str | None:
    """该插件（source）登记的第一个策略类别（供 sdk 推导 quota 的类别参数）。

    一个插件登记多个类别时结果不唯一，此时策略包应显式传 ``category``。
    """
    try:
        for cat, rec in _REGISTRY.items():
            if rec.get("source") == source:
                return cat
    except Exception:
        pass
    return None


async def _count_message_log(character_id: int, message_type: str, since) -> int:
    """proactive_message_logs 计数（「已发送」口径）。"""
    from sqlalchemy import func, select

    from app.db.database import async_session_factory
    from app.models.character import ProactiveMessageLog

    async with async_session_factory() as db:
        return int(
            (
                await db.execute(
                    select(func.count()).where(
                        ProactiveMessageLog.character_id == character_id,
                        ProactiveMessageLog.message_type == message_type,
                        ProactiveMessageLog.created_at >= since,
                    )
                )
            ).scalar()
            or 0
        )


async def _count_trigger_log(character_id: int, trigger_type: str, since) -> int:
    """proactive_trigger_logs 计数（「已执行」口径，decision=approved）。

    trigger_type 即 arbiter 事件类型（= 策略候选的 message_type），与
    ``arbiter.log_trigger_candidate`` 写入口径一致。
    """
    from sqlalchemy import func, select

    from app.db.database import async_session_factory
    from app.models.character import ProactiveTriggerLog

    async with async_session_factory() as db:
        return int(
            (
                await db.execute(
                    select(func.count()).where(
                        ProactiveTriggerLog.character_id == character_id,
                        ProactiveTriggerLog.trigger_type == trigger_type,
                        ProactiveTriggerLog.decision == "approved",
                        ProactiveTriggerLog.created_at >= since,
                    )
                )
            ).scalar()
            or 0
        )


def _since(window: str):
    """窗口 → 起始时间（naive UTC）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if window == "6h":
        return now - timedelta(hours=6)
    from app.utils.timeutil import beijing_day_start_utc

    return beijing_day_start_utc()


async def quota_used(character_id: int, category: str, *, message_type: str | None = None) -> dict:
    """内核只读配额计数（X6-c）：该类别近 6h / 北京当日**已用数**与上限。

    计数口径按 :data:`CATEGORY_QUOTA_SOURCE` 分两类（见其注释）：想念类只有触发日志能区分，
    其余走「已发送」消息日志。上限只填内核已知的类别（未知 → ``None``，策略包只拿到计数）。

    返回 ``{"used_6h": int, "used_today": int, "limit_6h": int|None, "limit_day": int|None}``；
    查询失败**上抛**（调用方决定：素材端口丢该 key，内核 prepare 丢弃候选）。
    """
    mt = message_type or category
    source = CATEGORY_QUOTA_SOURCE.get(category, "message_log")
    counter = _count_trigger_log if source == "trigger_log" else _count_message_log
    return {
        "used_6h": await counter(character_id, mt, _since("6h")),
        "used_today": await counter(character_id, mt, _since("day")),
        "limit_6h": category_quota_limits(category).get("6h"),
        "limit_day": category_quota_limits(category).get("day"),
    }


def category_quota_limits(category: str) -> dict:
    """该类别的内核配额上限（未知类别 → 空 dict，策略包只拿到已用计数）。"""
    if category == "motivation":
        from app.domain.proactivity.decision import MOTIVATION_MAX_PER_DAY, MOTIVATION_MAX_PER_6H

        return {"6h": MOTIVATION_MAX_PER_6H, "day": MOTIVATION_MAX_PER_DAY}
    if category == "unfinished_topic":
        from app.scheduling.unfinished_topic import MAX_DAILY

        return {"day": MAX_DAILY}
    return {}


async def sent_recently(character_id: int, category: str, message_type: str) -> bool:
    """该角色该口径下「近期是否已发过」（内核去重，按类别登记，见 :data:`CATEGORY_DEDUP`）。

    默认（未登记类别）与 ``sent_today`` 完全一致；查询失败 fail-open 返回 False
    （宁可多一次，还有小时限额与内核 prepare 的配额闸兜底）。
    """
    try:
        source, window = CATEGORY_DEDUP.get(category or "", DEFAULT_DEDUP)
        if source == "message_log" and window == "day":
            return await sent_today(character_id, message_type)   # 沿用既有实现（含 fail-open）
        counter = _count_trigger_log if source == "trigger_log" else _count_message_log
        return await counter(character_id, message_type, _since(window)) > 0
    except Exception as e:
        _logger.warning(
            "strategy dedup query failed char=%s cat=%s type=%s: %s",
            character_id, category, message_type, e,
        )
        return False


async def sent_today(character_id: int, message_type: str) -> bool:
    """该角色今天（北京日界）是否已发过该 message_type 的主动消息。

    内核侧去重兜底：策略包无状态、每 tick 都会投同样的候选，靠这里收口成「同一触发日只发一次」。
    查询失败 fail-open 返回 False（宁可多一次，也不阻塞发送；还有小时限额兜底）。
    """
    try:
        from sqlalchemy import select

        from app.db.database import async_session_factory
        from app.models.character import ProactiveMessageLog
        from app.utils.timeutil import beijing_day_start_utc

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(ProactiveMessageLog.id)
                    .where(
                        ProactiveMessageLog.character_id == character_id,
                        ProactiveMessageLog.message_type == message_type,
                        ProactiveMessageLog.created_at >= beijing_day_start_utc(),
                    )
                    .limit(1)
                )
            ).first()
        return row is not None
    except Exception as e:
        _logger.warning(
            "strategy dedup query failed char=%s type=%s: %s", character_id, message_type, e
        )
        return False


async def build_roster() -> list[dict[str, Any]]:
    """内核「选人」：给出开启主动互动、且有会话的角色名单（供策略包映射候选，不下发策略判定）。

    只做「谁有资格被考虑」：角色激活 + 主动互动开关 + 存在会话；
    **不做**日期/节日/纪念日判定、不做节流（那是策略包与频控的事）。
    每角色 2 次小查询（最新会话 / 首个会话），仅在 flag 开且有包接管时调用。
    """
    from app.scheduling.triggers import get_active_characters, get_first_session, get_latest_session

    roster: list[dict[str, Any]] = []
    for c in await get_active_characters():
        try:
            latest = await get_latest_session(c["character_id"], c["user_id"])
            if not latest:
                continue
            first = await get_first_session(c["character_id"], c["user_id"])
            first_at = first.get("created_at") if first else None
            roster.append(
                {
                    "character_id": c["character_id"],
                    "user_id": c["user_id"],
                    "character_name": c.get("character_name") or "",
                    "nickname": c.get("nickname") or c.get("username") or "",
                    "birthday": c.get("birthday"),          # MM-DD 或 None
                    "birthday_enabled": bool(c.get("birthday_enabled")),
                    "holiday_enabled": bool(c.get("holiday_enabled")),
                    "session_id": latest.get("id"),
                    "first_session_at": first_at.isoformat() if first_at else None,
                }
            )
        except Exception as e:
            _logger.warning("roster build failed char=%s: %s", c.get("character_id"), e)
    return roster


def build_hook_ctx(categories: set[str], roster: list[dict]) -> dict:
    """拼装下发给 proactive_candidate hook 的 ctx（仅在 flag 开且有接管时调用）。"""
    return {"strategy_categories": sorted(categories), "roster": roster}


# ---------------------------------------------------------------- 只读素材端口

def _fit(payload: dict) -> dict:
    """体量收口：序列化后超过 MAX_CONTEXT_CHARS 时，从最长的列表逐条瘦身。"""
    try:
        while len(json.dumps(payload, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
            big = None
            for k, v in payload.items():
                if isinstance(v, list) and v and (big is None or len(v) > len(payload[big])):
                    big = k
            if big is None:
                break
            payload[big] = payload[big][:-1]
    except Exception as e:
        _logger.warning("proactive context fit failed: %s", e)
        return {}
    return payload


def _iso(dt: Any) -> str | None:
    try:
        return dt.isoformat() if dt is not None else None
    except Exception:
        return None


def _clip(v: Any, n: int = CONTEXT_SUMMARY_CHARS) -> str:
    return str(v or "")[:n]


async def _ctx_roster(character_id: int | None) -> list[dict]:
    return (await build_roster())[: CONTEXT_ITEM_LIMITS["roster"]]


async def _ctx_character_state(character_id: int | None) -> dict:
    """角色当前八维状态（复用既有只读封装 get_character_states，不新查库绕开）。"""
    from app.application.character_state_service import get_character_states

    st = await get_character_states(int(character_id))
    out = {}
    for k in _CHAR_STATE_KEYS:
        try:
            out[k] = int(st.get(k, 50))
        except Exception:
            out[k] = 50
    return out


async def _ctx_due_reviews(character_id: int | None) -> list[dict]:
    """该角色到期/待复习的记忆（复用 collect_review_events 的到期与时态口径 + 活跃会话过滤）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.memory import Memory
    from app.scheduling.memory_review import collect_review_events

    events = await collect_review_events()   # 内核既定口径：到期 + 时态过滤 + 有活跃会话
    ids = [int(e["candidate"]["memory_id"]) for e in events
           if int(e["candidate"].get("character_id") or 0) == int(character_id)]
    if not ids:
        return []
    ids = ids[: CONTEXT_ITEM_LIMITS["due_reviews"]]
    async with async_session_factory() as db:
        rows = (await db.execute(select(Memory).where(Memory.id.in_(ids)))).scalars().all()
    by_id = {m.id: m for m in rows}
    out = []
    for mid in ids:
        m = by_id.get(mid)
        if m is None:
            continue
        out.append({
            "id": mid,
            "summary": _clip(getattr(m, "title", None) or m.content),
            "memory_type": m.memory_type or "",
            "importance": float(getattr(m, "importance", 0) or 0),
            "due_at": _iso(getattr(m, "next_review_at", None)),
        })
    return out


async def _ctx_recent_intents(character_id: int | None) -> list[dict]:
    """该角色最近未完成的前瞻意图（pending；id + 摘要 + 状态 + 到期窗口）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.memory import ProspectiveIntent

    limit = CONTEXT_ITEM_LIMITS["recent_intents"]
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ProspectiveIntent)
                .where(
                    ProspectiveIntent.character_id == int(character_id),
                    ProspectiveIntent.status == "pending",
                )
                .order_by(ProspectiveIntent.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [{
        "id": r.id,
        "summary": _clip(r.content),
        "status": r.status or "",
        "kind": r.kind or "",
        "due_at": _iso(r.due_start) or _iso(r.due_end),
    } for r in rows]


async def _ctx_relationship(character_id: int | None) -> dict:
    """关系标量（与 ``sdk.get_relationship`` **同一数据源**：character_state_service）。"""
    from app.application.character_state_service import get_character_states

    st = await get_character_states(int(character_id))
    out = {}
    for k in _RELATION_KEYS:
        try:
            out[k] = int(st.get(k, 50))
        except Exception:
            out[k] = 50
    return out


async def _ctx_user_rhythm(character_id: int | None) -> dict:
    """用户作息侧素材（只读，复用既有学习结果，不触发重学/不写库）。

    - ``hours_since_last_user_message``：距该角色最近一条用户消息的小时数（跨会话；
      从未对话 → ``None``，策略包按「无信号」处理）；
    - ``active_hours`` / ``weight``：已学到的活跃时段与当前小时权重（``get_active_hours``
      只读 + 纯函数 ``hourly_rhythm_weight``；未学到 → ``weight=1.0``，即不挡）。
    """
    from app.scheduling import arbiter
    from app.scheduling.user_rhythm import get_active_hours, hourly_rhythm_weight

    hours = await arbiter.get_hours_since_last_user_message(int(character_id))
    user_id = await _user_id_of(int(character_id))
    active: list[list[int]] = []
    weight = 1.0
    if user_id:
        active = list(await get_active_hours(user_id) or [])
        cn_hour = datetime.now(timezone(timedelta(hours=8))).hour
        weight = hourly_rhythm_weight(cn_hour, active)
    return {
        "hours_since_last_user_message": None if hours is None else round(float(hours), 2),
        "active_hours": active[:4],
        "weight": float(weight),
        "learned": bool(active),
    }


async def _quota_for(character_id: int | None, category: str | None) -> dict:
    """``quota``：该类别 6h / 当日已用数与上限（类别由 sdk 推导或策略包显式传入）。"""
    cat = (category or "").strip()
    if not cat:
        return {}
    return await quota_used(int(character_id), cat)


async def _ctx_open_topics(character_id: int | None) -> list[dict]:
    """未收尾话题 / 最近话头（复用 topic_tracker 的时效口径：进行中 + 72h/目标 14 天）。"""
    from sqlalchemy import select

    from app.agent.topic_tracker import PROACTIVE_FRESH_TOPIC_HOURS, PROACTIVE_GOAL_MAX_DAYS
    from app.db.database import async_session_factory
    from app.models.memory import ConversationTopic

    limit = CONTEXT_ITEM_LIMITS["open_topics"]
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ConversationTopic)
                .where(
                    ConversationTopic.character_id == int(character_id),
                    ConversationTopic.status == "进行中",
                )
                .order_by(
                    ConversationTopic.importance.desc(),
                    ConversationTopic.last_touched_at.desc(),
                )
                .limit(limit)
            )
        ).scalars().all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    for r in rows:
        last = r.last_touched_at
        last = last.replace(tzinfo=None) if last is not None and last.tzinfo else last
        if last is None:
            continue
        age_h = (now - last).total_seconds() / 3600.0
        max_h = PROACTIVE_GOAL_MAX_DAYS * 24 if r.goal else PROACTIVE_FRESH_TOPIC_HOURS
        if age_h > max_h:
            continue                     # 过期话题不主动续（与 topic_tracker 同口径）
        out.append({
            "id": r.id,
            "topic": _clip(r.topic),
            "importance": float(getattr(r, "importance", 0) or 0),
            "goal": bool(getattr(r, "goal", False)),
            "hours_since": round(age_h, 1),
        })
    return out


async def _user_id_of(character_id: int) -> int | None:
    """角色归属用户（只读小查询；取不到 → None，素材 degrade 但不失败）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.chat import ChatSession

    async with async_session_factory() as db:
        return (
            await db.execute(
                select(ChatSession.user_id)
                .where(ChatSession.character_id == character_id)
                .order_by(ChatSession.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def _ctx_time_ctx(character_id: int | None) -> dict:
    """北京日期/小时/时段语义（中性表述，不含任何具体地点/节假日硬编码）。"""
    from app.scheduling.life_rhythm import get_time_window
    from app.utils.timeutil import app_local_now

    now = app_local_now()
    window = get_time_window() or {}
    return {
        "date": now.strftime("%Y-%m-%d"),
        "hour": int(now.hour),
        "minute": int(now.minute),
        "weekday": int(now.weekday()),          # 0=周一 … 6=周日
        "is_weekend": bool(now.weekday() >= 5),
        "window": window.get("name") or "",     # 清晨/上午/午间/下午/傍晚/晚间/深夜
        "tendencies": [str(t) for t in (window.get("tendencies") or [])][:4],
    }


_CONTEXT_BUILDERS = {
    "roster": _ctx_roster,
    "character_state": _ctx_character_state,
    "due_reviews": _ctx_due_reviews,
    "recent_intents": _ctx_recent_intents,
    "time_ctx": _ctx_time_ctx,
    "relationship": _ctx_relationship,
    "user_rhythm": _ctx_user_rhythm,
    "open_topics": _ctx_open_topics,
}
# 需要「类别」参数的素材（quota：哪个类别的配额）——签名 ``(character_id, category)``
_CONTEXT_BUILDERS_PER_CATEGORY = {"quota": _quota_for}


def _builder_of(key: str):
    return _CONTEXT_BUILDERS.get(key) or _CONTEXT_BUILDERS_PER_CATEGORY.get(key)


async def build_proactive_context(
    keys: Iterable[str], *, character_id: int | None = None, category: str | None = None,
) -> dict:
    """只读素材端口内核侧实现（pull 式，白名单 + 限量 + 逐 key fail-open）。

    - ``keys`` 已由 sdk 侧按 manifest ``context_keys`` 白名单过滤；此处再与 ``CONTEXT_KEYS`` 求交；
    - 需要 ``character_id`` 的 key（character_state/due_reviews/recent_intents/relationship/
      user_rhythm/quota/open_topics）未提供则跳过；
    - ``category`` 仅供 ``quota`` 使用（哪个类别的配额）；未传则该 key 返回空；
    - 单 key 构造失败只丢该 key（不阻塞主链路），整体异常返回已构造部分 / 空 dict。
    """
    out: dict[str, Any] = {}
    try:
        for k in keys or ():
            builder = _builder_of(k)
            if builder is None:
                continue
            if k in PER_CHAR_CONTEXT_KEYS and not character_id:
                continue
            try:
                if k in _CONTEXT_BUILDERS_PER_CATEGORY:
                    out[k] = await builder(character_id, category)
                else:
                    out[k] = await builder(character_id)
            except Exception as e:
                _logger.warning("proactive context %s failed char=%s: %s", k, character_id, e)
    except Exception as e:  # 隔离：素材端口绝不阻塞主动链路
        _logger.warning("proactive context build failed: %s", e)
    return _fit(out)
