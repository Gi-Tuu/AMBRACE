# -*- coding: utf-8 -*-
"""压缩存活项清单（P1，2026-09-23，雷达 §2「压缩存活项清单」）。

要解决的问题：长对话被压缩/裁剪后，留在窗口里的往往是「碎片化的过去」——当前目标、还没办完的
约定、用户说过的硬约束最容易掉出窗口，于是模型把已经改变的状况继续当成此刻说。这与 two-pass
现状 trace 对冲的是同一类错误（错误/过时的现状会被生成端**系统性放大**），区别在口径：本清单要
求的是**这几段在压缩后仍然存活**，而不是重读一遍现状。

口径与边界（与 ``app/scheduling/state_trace.py`` 同一族硬约束）：
- 三段固定顺序：①当前目标（``user_facts.slot=goal_state``，另有 job/living/location 时合成一行
  「当前处境」）→ ②未决问题/计划（``prospective_intents.status=pending``，按 ``due_start`` 取最近
  ``INTENT_LIMIT`` 条，带 ``due_end`` 的标注期限）→ ③硬约束（``user_facts.value`` 字面命中强约束词）；
- 只读、纯规则拼装：**不调 LLM、不写库、不上屏**；任何异常收敛为**空串 + WARNING**（fail-open），
  绝不冒泡到主链路；
- 槽值一律走读取侧白名单（敏感槽 relationship/health 未经该账号显式开启永不带出），
  ``valid_to`` 已过期的槽值不进（属旧现状）；
- 硬约束只做**字面**筛选（命中强约束词才算，判定在 Python 侧做，不拼 LIKE 通配符），
  一条都取不到就写「（无显式硬约束记录）」，**绝不编造**；
- 单行 ≤ ``CHECKLIST_LINE_CHARS`` 字符、总长 ≤ ``CHECKLIST_TOTAL_CHARS`` 字符；空段整段省略
  （不输出空标题）；三段全空返回空串；
- 不新增第三方依赖、不新增表、不改 schema。
"""
from __future__ import annotations

from sqlalchemy import select

from app.utils.logger import get_logger

_logger = get_logger("agent.survival_checklist")

CHECKLIST_LINE_CHARS = 120   # 单行截断
CHECKLIST_TOTAL_CHARS = 600  # 总长硬上限
INTENT_LIMIT = 5             # 未决问题/计划取最近 N 条
HARD_LIMIT = 5               # 硬约束最多取 N 条（清单要「存活」不是「全量」）

_HEADER = "【存活项清单】（这些是压缩后仍然成立的部分：目标没实现就说还没实现，约定没兑现就说还没兑现；硬约束照办，别改写别省略）"
_SEC_GOAL = "· 当前目标"
_SEC_OPEN = "· 未决问题/计划"
_SEC_HARD = "· 硬约束"
_SEC_HEADERS = (_SEC_GOAL, _SEC_OPEN, _SEC_HARD)

# 一段里查不到硬约束条目时的占位行（明确「没有记录」，而不是留空让模型自行脑补）
_NO_CONSTRAINT = "（无显式硬约束记录）"

# 强约束词（字面命中才算硬约束；宁漏不编，不做任何语义推断）
_CONSTRAINT_MARKERS: tuple[str, ...] = ("别", "不要", "必须", "记得", "绝不", "一定")

# user_facts.slot → 中文标签
_SLOT_LABEL = {
    "goal_state": "当前目标",
    "job": "工作/学业",
    "living": "居住情况",
    "location": "位置/城市",
}
# 「当前处境」合成行取材槽（固定顺序）
_SITUATION_SLOTS: tuple[str, ...] = ("job", "living", "location")
# 本模块读取 user_facts 的目的槽（不含 relationship/health 两敏感槽；读取侧白名单仍会再兜一层）
_TARGET_SLOTS: frozenset[str] = frozenset(("goal_state",) + _SITUATION_SLOTS)


def _get(row, key, default=None):
    """ORM 行 / dict 通用取值（渲染函数因此可脱离数据库直接测）。"""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _one_line(text, limit: int = CHECKLIST_LINE_CHARS) -> str:
    """压成单行并截断（禁止多行原文塞进清单打乱分区结构）。"""
    return " ".join(str(text or "").split())[:limit]


def is_hard_constraint(value) -> bool:
    """是否字面命中强约束词（纯函数；空值恒 False）。"""
    text = _one_line(value)
    return any(marker in text for marker in _CONSTRAINT_MARKERS)


def goal_line(row) -> str:
    """goal_state 槽值行 → 「当前目标」行；无值返回空串。"""
    value = _one_line(_get(row, "value"))
    return f"- 当前目标：{value}" if value else ""


def situation_line(by_slot: dict) -> str:
    """job/living/location 槽值 → 合成一行「当前处境」；三者全空返回空串。

    ``by_slot``：slot → 现行值（已剔除过期行）。合成一行而非拆成三条，是为了在总长预算里给
    目标/计划/硬约束留出位置。
    """
    parts = []
    for slot in _SITUATION_SLOTS:
        value = _one_line((by_slot or {}).get(slot))
        if value:
            parts.append(f"{_SLOT_LABEL[slot]}：{value}")
    return _one_line("- 当前处境：" + "；".join(parts)) if parts else ""


def _due_label(due_end) -> str:
    """due_end → 「（截止 MM-DD）」（北京时间日期）；缺失/非法返回空串（宁漏不编）。"""
    if due_end is None or not hasattr(due_end, "strftime"):
        return ""
    try:
        from app.utils.timeutil import app_tz_offset_hours, shift_utc_naive, to_naive_utc
        return "（截止 " + shift_utc_naive(to_naive_utc(due_end), app_tz_offset_hours()).strftime("%m-%d") + "）"
    except Exception:
        return ""


def intent_line(row) -> str:
    """prospective_intents 行 → 未决计划行；无内容返回空串。

    有 ``due_end`` 时标注期限——「到什么时候之前」是压缩后最容易丢的部分。
    """
    content = _one_line(_get(row, "content"))
    if not content:
        return ""
    return _one_line(f"- {content}{_due_label(_get(row, 'due_end'))}")


def hard_constraint_line(value) -> str:
    """user_facts 值 → 硬约束行（原样引用，不改写、不补全）；无值返回空串。"""
    text = _one_line(value)
    return f"- {text}" if text else ""


def render_survival_checklist(goal_lines: list[str] | None = None,
                              open_lines: list[str] | None = None,
                              hard_lines: list[str] | None = None) -> str:
    """三段 → 纯文本清单（**纯函数，不查库**）；全空返回空串；总长硬上限。

    段顺序固定（当前目标 → 未决问题/计划 → 硬约束）；空段整段省略；预算用尽即停。
    唯一例外：前两段有内容而硬约束段为空时仍输出 ``_NO_CONSTRAINT`` 占位行——
    写明「没有记录」比留空更不容易被模型自行脑补。
    """
    goal_items = [_one_line(ln) for ln in (goal_lines or []) if str(ln or "").strip()]
    open_items = [_one_line(ln) for ln in (open_lines or []) if str(ln or "").strip()]
    hard_items = [_one_line(ln) for ln in (hard_lines or []) if str(ln or "").strip()]
    if not hard_items and (goal_items or open_items):
        hard_items = [_NO_CONSTRAINT]

    parts: list[str] = []
    for header, items in ((_SEC_GOAL, goal_items), (_SEC_OPEN, open_items), (_SEC_HARD, hard_items)):
        if items:
            parts.append(header)
            parts.extend(items)
    if not parts:
        return ""
    out = [_HEADER]
    used = len(_HEADER)
    for part in parts:
        if used + 1 + len(part) > CHECKLIST_TOTAL_CHARS:
            break
        out.append(part)
        used += 1 + len(part)
    if len(out) > 1 and out[-1] in _SEC_HEADERS:  # 只有分区头 = 该段没装进任何行，撤掉头
        out.pop()
    if len(out) <= 1:
        return ""
    return "\n".join(out)[:CHECKLIST_TOTAL_CHARS]


def _section_counts(goal_lines: list[str] | None, open_lines: list[str] | None,
                    hard_lines: list[str] | None) -> dict:
    """三段条数（留痕用，**不含正文**）；占位行不计入硬约束条数。"""
    def _n(lines):
        return len([ln for ln in (lines or []) if str(ln or "").strip()])
    return {
        "goal_n": _n(goal_lines),
        "open_n": _n(open_lines),
        "hard_n": len([ln for ln in (hard_lines or [])
                       if str(ln or "").strip() and _one_line(ln) != _NO_CONSTRAINT]),
    }


async def _readable_slots(user_id) -> list[str]:
    """读取侧槽白名单（红线：敏感槽 relationship/health 未经该账号显式开启则永不带出）。"""
    try:
        from app.memory.user_facts import readable_user_fact_slots_for
        return list(await readable_user_fact_slots_for(user_id) or [])
    except Exception as e:
        _logger.warning("survival checklist slot gate failed user=%s: %s", user_id, e)
    try:
        from app.memory.user_facts import enabled_user_fact_slots
        return list(enabled_user_fact_slots() or [])  # 按账号解析失败 → 全局口径（同样不旁路敏感槽）
    except Exception:
        return []


async def build_checklist_detail(db, *, user_id=None, character_id=None) -> tuple[str, dict]:
    """只读拼装清单，返回 ``(清单文本, 三段条数)``；异常收敛为 ``("", 全 0)`` + WARNING。

    ``db`` 由调用方提供（同一会话复用连接；本函数**只读、绝不 commit**）。
    """
    try:
        from app.models.memory import ProspectiveIntent
        from app.models.user import GlobalUserFact

        by_slot: dict[str, str] = {}
        hard_values: list[str] = []
        slots = await _readable_slots(user_id) if user_id else []
        if user_id and slots:
            from app.memory.user_facts import fact_is_expired
            rows = (await db.execute(
                select(GlobalUserFact).where(
                    GlobalUserFact.user_id == user_id,
                    GlobalUserFact.slot.in_(slots),
                ).order_by(GlobalUserFact.updated_at.desc())
            )).scalars().all()
            for row in rows:
                if fact_is_expired(row):  # TTL 过期槽值属旧现状，不进清单
                    continue
                slot = str(_get(row, "slot") or "")
                value = _one_line(_get(row, "value"))
                if not slot or not value:
                    continue
                if slot in _TARGET_SLOTS:
                    by_slot.setdefault(slot, value)  # updated_at 倒序 → 首条即现行值
                if is_hard_constraint(value) and len(hard_values) < HARD_LIMIT:
                    hard_values.append(value)

        goal_lines: list[str] = []
        if by_slot.get("goal_state"):
            goal_lines.append(_one_line(f"- 当前目标：{by_slot['goal_state']}"))
        situation = situation_line(by_slot)
        if situation:
            goal_lines.append(situation)

        open_lines: list[str] = []
        if user_id or character_id:
            stmt = select(ProspectiveIntent).where(ProspectiveIntent.status == "pending")
            # discharged/matched/stale/expired 不进；按 due_start 取最近（无期限的排最后）
            if user_id:
                stmt = stmt.where(ProspectiveIntent.user_id == user_id)
            if character_id:
                stmt = stmt.where(ProspectiveIntent.character_id == character_id)
            rows = (await db.execute(
                stmt.order_by(
                    ProspectiveIntent.due_start.is_(None).asc(),
                    ProspectiveIntent.due_start.asc(),
                    ProspectiveIntent.id.asc(),
                ).limit(INTENT_LIMIT)
            )).scalars().all()
            open_lines = [ln for ln in (intent_line(r) for r in rows) if ln]

        hard_lines = [ln for ln in (hard_constraint_line(v) for v in hard_values) if ln]
        text = render_survival_checklist(goal_lines, open_lines, hard_lines)
        return text, _section_counts(goal_lines, open_lines, hard_lines)
    except Exception as e:
        _logger.warning("survival checklist build failed char=%s user=%s: %s", character_id, user_id, e)
        return "", {"goal_n": 0, "open_n": 0, "hard_n": 0}


async def build_survival_checklist(db, *, user_id=None, character_id=None) -> str:
    """压缩存活项清单（纯确定性拼装）；全空/异常返回空串。见 :func:`build_checklist_detail`。"""
    text, _counts = await build_checklist_detail(db, user_id=user_id, character_id=character_id)
    return text
