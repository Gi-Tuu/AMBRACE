"""核心记忆 / 开放循环 / 关系锚点（World & Cognition P1，2026-08-15）

- Core Memory：高重要 + 多次确认的记忆 → 对话无条件注入（不靠向量检索）
- Open Loops：未完成事项（active Goal / 未到期承诺计时 / 到期备忘）→ 条件注入
- Relationship Anchors：importance ≥ 80 的关系/共享记忆 → 优先注入
"""
from datetime import datetime, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger

_logger = get_logger("memory.core")

# 晋升阈值：importance ≥ 80（4 星+）且用户确认 ≥ 2 次 → 核心记忆
CORE_MIN_IMPORTANCE = 80.0
CORE_MIN_CONFIRMATIONS = 2
CORE_MAX_PER_CHAR = 30  # 每角色核心记忆上限，超出按 importance 淘汰

# 核心分类：身份 / 偏好 / 里程碑 / 承诺
CORE_CATEGORY_BY_SUBTYPE = {
    "name": "identity", "age": "identity", "location": "identity", "job": "identity",
    "relationship": "identity", "family": "identity", "education": "identity",
    "food": "preference", "hobby": "preference", "dislike": "preference",
    "habit": "preference", "preference": "preference", "style": "preference",
    "anniversary": "milestone", "milestone": "milestone", "life_event": "milestone",
    "commitment": "commitment", "promise": "commitment", "goal": "commitment",
}


def _core_category(sub_type: str | None, memory_type: str | None) -> str | None:
    if sub_type:
        c = CORE_CATEGORY_BY_SUBTYPE.get(sub_type)
        if c:
            return c
    if memory_type == "user_info":
        return "identity"
    if memory_type == "preference":
        return "preference"
    return None


def _perception_isolate_on() -> bool:
    """批 0-2 M2（2026-09-28）：隔离禁令总闸，默认关（关＝逐字节旧行为）。

    任何异常回落 False——读不到开关就等于没接线（方案风险 R8：回退必须退得干净）。
    """
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("perception_isolate", False))
    except Exception:
        return False


# ── 批 0-8 晋升审计（2026-09-28，雷达 13 ＋ 簇5）────────────────────────────────
# 目标：查得到「哪条记忆、什么时候、因为什么进了核心」，为后续治理与评测留证据。
# 硬约束：**零新表、零新列、不改任何判据与阈值**——晋升条件（CORE_MIN_IMPORTANCE /
#   CORE_MIN_CONFIRMATIONS / 高价值类别 +100 / 来源隔离闸）一字未动，本段只在判定成立后补留痕。
# 载体：复用 #70-M3 的 memory_write_receipts（``emit_memory_receipt``），新增 action="promote"，
#   与既有 action="utility_feedback" 同一先例（借表存证、不新增列/约束；action 列 String(20) 装得下）。
# 闸控：``emit_memory_receipt`` 首行受既有 flag ``memory_write_receipt``（**默认关**）⇒ 关=零写入、
#   晋升路径逐字节旧行为，故本批**不新增 flag**（再加一层闸只会多出两个开关的口径漂移；
#   且本单文件隔离不允许改 AGENT_FLAGS 硬编码默认表与 application/flag_catalog.py，新键也无法热切）。
# 纪律：沿用本模块「失败静默」——留痕任何异常都不许影响晋升结果（与既有 except 包裹同口径）。
#
# 只读查询口径（全部 sqlite3 ``mode=ro``；现成端点只到「事实修正历史」，晋升审计取 SQL）：
#   -- 某角色什么时候、因为什么进了核心（按时间倒序）
#   SELECT memory_id, reason, detail_json, created_at FROM memory_write_receipts
#    WHERE character_id = ? AND action = 'promote' ORDER BY id DESC;
#   -- 晋升依据分布（重要度门槛 / 高价值类别 / 用户确认三路各占多少）
#   SELECT json_extract(detail_json, '$.rule') AS rule, count(*) FROM memory_write_receipts
#    WHERE action = 'promote' GROUP BY rule ORDER BY 2 DESC;
#   -- 「进了核心但行上已不是核心」的漂移（挤位/人工改动没留痕的行，靠两表对账暴露）
#   SELECT r.memory_id FROM memory_write_receipts r
#    JOIN memories m ON m.id = r.memory_id
#    WHERE r.action = 'promote' AND m.is_core = 0 GROUP BY r.memory_id;
ACTION_PROMOTE = "promote"

# 晋升依据标识（回显**既有**阈值，只为让 reason 自证「因为什么」）
_RULE_IMPORTANCE_CONFIRMED = "importance>=%d&confirmed>=%d" % (
    CORE_MIN_IMPORTANCE, CORE_MIN_CONFIRMATIONS)
_RULE_HIGH_VALUE = "high_value_category&importance>=100"
_RULE_USER_CONFIRMATION = "user_confirmation(" + _RULE_IMPORTANCE_CONFIRMED + ")"


def _promote_snapshot(m: Memory, category: str | None) -> dict:
    """晋升审计所需的行内快照（**必须在 commit 前取**：异步会话 commit 后取属性有隐式刷新风险）。"""
    return {
        "character_id": m.character_id,
        "memory_id": m.id,
        "importance": float(m.importance or 0),
        "confirmation_count": int(m.confirmation_count or 0),
        "core_category": category,
        "memory_type": m.memory_type,
        "sub_type": m.sub_type,
        "source": m.source,
        "epistemic_status": m.epistemic_status,
    }


def _emit_promote_audit(snap: dict, *, rule: str, trigger: str, evicted_id: int | None) -> None:
    """晋升留痕一条（批 0-8）：reason 写清晋升依据（重要度 / 确认数 / 类型），detail 存全量读数。

    ``trigger``＝哪条路径促成的晋升（write=写入后自动检查 / confirmation=用户确认计数）；
    ``evicted_id``＝因每角色上限（CORE_MAX_PER_CHAR）被挤掉核心位的那条（没有则 None），
    一次晋升同时带走「谁让了位」，不必再为淘汰单开一类回执。
    """
    try:
        from app.memory.receipt import emit_memory_receipt

        _cat = snap["core_category"] or "-"
        _reason = (
            "core promote (%s) via %s: importance=%.0f confirmed=%d "
            "category=%s type=%s sub_type=%s source=%s"
        ) % (
            trigger, rule, snap["importance"], snap["confirmation_count"],
            _cat, snap["memory_type"] or "-", snap["sub_type"] or "-", snap["source"] or "-",
        )
        emit_memory_receipt(
            snap["character_id"], snap["memory_id"], ACTION_PROMOTE,
            reason=_reason,
            detail={
                "trigger": trigger,
                "rule": rule,
                "importance": snap["importance"],
                "confirmation_count": snap["confirmation_count"],
                "core_category": snap["core_category"],
                "memory_type": snap["memory_type"],
                "sub_type": snap["sub_type"],
                "source": snap["source"],
                "epistemic_status": snap["epistemic_status"],
                "cap_per_char": CORE_MAX_PER_CHAR,
                "evicted_memory_id": evicted_id,
            },
        )
    except Exception as e:
        _logger.warning("promote audit failed mem=%s: %s", snap.get("memory_id"), e)


def _promote_rule_for(pct: float, confirmed: int, category: str | None) -> str:
    """晋升依据标识（纯函数）：只回显既有判据走了哪条，不参与任何判定。"""
    if pct >= CORE_MIN_IMPORTANCE and confirmed >= CORE_MIN_CONFIRMATIONS:
        return _RULE_IMPORTANCE_CONFIRMED
    if (category or "") in ("identity", "preference", "commitment") and pct >= 100.0:
        return _RULE_HIGH_VALUE
    return "unknown"


def _quarantined_from_core(source, epistemic_status) -> bool:
    """晋升来源闸（禁令 1）：被隔离的记忆不得晋升 is_core。

    判据**只调用** M0 的纯函数 ``perception_tier.is_quarantined``（来源 perception 且未被认可为 FACT），
    flag 关时恒 False ⇒ 晋升条件逐字节不变。用户点「这是真的」把认知状态升到 FACT 后自动脱隔，
    之后照旧按 importance/confirmation_count 竞争名额，不设第二条晋升通道（方案 §2.3）。
    """
    if not _perception_isolate_on():
        return False
    from app.memory.perception_tier import is_quarantined
    try:
        return bool(is_quarantined(source, epistemic_status))
    except Exception:
        return False  # 判据异常 ⇒ 按旧行为放行，绝不因为隔离面出错而吞掉晋升


async def maybe_promote_core(memory_id: int, importance: float,
                             sub_type: str | None, memory_type: str | None) -> None:
    """写入后自动晋升检查：高重要 或（已确认≥2次 且 重要≥阈值）→ is_core。失败静默。"""
    try:
        async with async_session_factory() as db:
            m = await db.get(Memory, memory_id)
            if m is None or m.is_core:
                return
            if _quarantined_from_core(m.source, m.epistemic_status):
                return  # 禁令 1：未获认可的感知派生条不进核心记忆
            pct = float(m.importance or 0)
            confirmed = int(m.confirmation_count or 0)
            promote = pct >= CORE_MIN_IMPORTANCE and confirmed >= CORE_MIN_CONFIRMATIONS
            # 用户明确高价值类型（身份/偏好/承诺）+ 单次高重要也晋升（降低门槛）
            if not promote:
                cat = _core_category(sub_type, memory_type)
                promote = cat in ("identity", "preference", "commitment") and pct >= 100.0
            if not promote:
                return
            # 批 0-8 晋升审计：先取行内快照（commit 后属性有刷新风险），提交成功后再发回执
            _rule = _promote_rule_for(pct, confirmed, _core_category(sub_type, memory_type))
            _evicted_id: int | None = None
            # 超上限淘汰最不重要的一条
            cnt = (await db.execute(
                select(func.count()).where(Memory.character_id == m.character_id, Memory.is_core == True)
            )).scalar() or 0
            if cnt >= CORE_MAX_PER_CHAR:
                old = (await db.execute(
                    select(Memory).where(
                        Memory.character_id == m.character_id, Memory.is_core == True,
                    ).order_by(Memory.importance.asc()).limit(1)
                )).scalar_one_or_none()
                if old is not None:
                    old.is_core = False
                    _evicted_id = old.id  # 谁让了位（随本次晋升一并留证，不再单开一类回执）
            m.is_core = True
            m.core_category = _core_category(sub_type, memory_type) or "identity"
            _snap = _promote_snapshot(m, m.core_category)
            await db.commit()
            _emit_promote_audit(_snap, rule=_rule, trigger="write", evicted_id=_evicted_id)
    except Exception as e:
        _logger.warning("maybe_promote_core failed mem=%s: %s", memory_id, e)


async def confirm_memory(memory_id: int) -> None:
    """用户确认信号（"对/没错/记得"）：confirmation_count+1，达阈值自动晋升。失败静默。"""
    try:
        _snap: dict | None = None
        async with async_session_factory() as db:
            m = await db.get(Memory, memory_id)
            if m is None:
                return
            m.confirmation_count = (m.confirmation_count or 0) + 1
            if (m.confirmation_count >= CORE_MIN_CONFIRMATIONS
                    and float(m.importance or 0) >= CORE_MIN_IMPORTANCE
                    and not _quarantined_from_core(m.source, m.epistemic_status)):
                # 禁令 1 同口径：确认计数照旧累加（用户信号不丢），但未认可的感知条不因此拿到 is_core
                m.is_core = True
                m.core_category = _core_category(m.sub_type, m.memory_type) or "identity"
                _snap = _promote_snapshot(m, m.core_category)  # 批 0-8：本次由「用户确认」促成的晋升
            await db.commit()
        if _snap is not None:
            _emit_promote_audit(_snap, rule=_RULE_USER_CONFIRMATION,
                                trigger="confirmation", evicted_id=None)
    except Exception as e:
        _logger.warning("confirm_memory failed mem=%s: %s", memory_id, e)


async def get_core_memories(character_id: int, limit: int = 10) -> list[Memory]:
    """核心记忆（无条件注入源）。"""
    try:
        # 2026-09-17 批次一（任务2）：无条件注入 = 现状面 → 恒 active 新口径
        from app.memory.service import current_facts_status_clause
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.is_core == True,
                    Memory.is_archived == False,
                    current_facts_status_clause(),
                ).order_by(Memory.importance.desc()).limit(limit)
            )).scalars().all()
            return list(rows)
    except Exception as e:
        _logger.warning("get_core_memories failed char=%d: %s", character_id, e)
        return []


async def get_relationship_anchors(character_id: int, user_id: int, limit: int = 5) -> list[Memory]:
    """关系锚点：importance ≥ 80 的关系/共享/事件记忆。"""
    try:
        # 2026-09-17 批次一（任务2）：关系锚点注入 = 现状面 → 恒 active 新口径
        from app.memory.service import current_facts_status_clause
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.user_id == user_id,
                    Memory.is_archived == False,
                    Memory.importance >= 80.0,
                    Memory.memory_type.in_(["event", "insight"]),
                    current_facts_status_clause(),
                ).order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(limit)
            )).scalars().all()
            return list(rows)
    except Exception as e:
        _logger.warning("get_relationship_anchors failed char=%d: %s", character_id, e)
        return []


async def get_open_loops(character_id: int, user_id: int, limit: int = 10) -> list[str]:
    """开放循环（未完成事项）文本列表：active Goal + 未到期计时承诺。"""
    loops: list[str] = []
    try:
        from app.models.life import LifeGoal
        from app.models.life import ScheduledEvent
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with async_session_factory() as db:
            goals = (await db.execute(
                select(LifeGoal).where(
                    LifeGoal.character_id == character_id,
                    LifeGoal.status == "active",
                ).order_by(LifeGoal.priority.desc()).limit(limit)
            )).scalars().all()
            for g in goals:
                loops.append(f"进行中的目标：{g.title or ''}")
            # 未到期的承诺计时（AI/用户承诺过的时间点）
            timers = (await db.execute(
                select(ScheduledEvent).where(
                    ScheduledEvent.character_id == character_id,
                    ScheduledEvent.user_id == user_id,
                    ScheduledEvent.status == "pending",
                    ScheduledEvent.trigger_at > now,
                ).order_by(ScheduledEvent.trigger_at.asc()).limit(limit)
            )).scalars().all()
            for t in timers:
                hint = (t.content_hint or "").strip()
                left = max(1, int((t.trigger_at - now).total_seconds() / 60))
                owner = "用户" if (t.owner or "ai") == "user" else "你"
                loops.append(f"{owner}说过「{hint or '某件事'}」，约 {left} 分钟后到点")
    except Exception as e:
        _logger.warning("get_open_loops failed char=%d: %s", character_id, e)
    return loops[:limit]
