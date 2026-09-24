# -*- coding: utf-8 -*-
"""现状 trace 构造（two-pass POC，2026-09-23）：主动消息生成前的「当前现状速读」临时重读指引。

为什么**只取 active 且置信达标**（硬口径，禁止放宽）：
- 雷达 Random Trace 警示——错误现状会被生成端**系统性放大**（14.2 < 29.2：带错现状比不带现状更糟）。
  所以 ``world_facts`` 只允许 ``status == "active"`` 且 ``confidence >= TRACE_CONFIDENCE_MIN`` 的行进
  trace；**expired / superseded 一律不进**，即便它 ``updated_at`` 更晚、看起来更“新”。

口径与边界（方案书 output/AMBRACE_two-pass_POC方案_20260923.md，本地 output 目录不入公开仓）：
- trace 只作**临时重读指引**：本次生成用完即弃——**不落库、不写记忆、不上屏、不调 LLM**
  （纯规则拼装，额外成本≈0，只增加 prompt 长度）；
- 三个分区固定顺序：现状事实 → 用户槽值 → 未完成计划；任一分区为空则整段省略（不输出空标记）；
  全空返回**空串**；
- 每行 ≤ ``TRACE_LINE_CHARS`` 字符、总长硬上限 ``TRACE_TOTAL_CHARS`` 字符；
- 任何异常一律收敛（返回空串 + WARNING），绝不让主链路崩；
- 退出条件：人工抽查 10 条「现状前置正确且未放大错误现状」≥ 8/10 才进灰度，否则关 flag 回滚
  （POC 期跑完即出结论，不做长期灰度）。
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.utils.logger import get_logger

_logger = get_logger("scheduling.state_trace")

# 置信地板：现行写入链路（chat_status 0.9 / life_activity 0.85 / 权威与编纂事实 1.0）全部远高于此值，
# 取 0.6 只挡明显推测/降级的行，不误伤真实现状（本模块要的是「不误删」也「不放大旧现状」）。
TRACE_CONFIDENCE_MIN = 0.6
TRACE_LINE_CHARS = 200    # 单行截断
TRACE_TOTAL_CHARS = 1200  # 总长硬上限

# Q1（2026-09-25）：同一类近况把「现状事实」段占满 ⇒ 按谓词收敛名额。
# 依据（生产库 char13 实测）：active 且置信达标的 71 条里 curated 68 条、status/activity/setting
# 各 1 条；旧 trace 8 条中 7 条是 curated（关系亲述 4 条 + 腰伤护理 3 条）——**重复只集中在
# curated**，所以只对 curated 设上限，其它谓词不限（它们本来就各只有一条，设限只会误伤）。
# 为什么不用相似度阈值判重：那 6 条关系亲述两两 SequenceMatcher ratio 仅 0.40~0.64、容器包含
# 几乎不命中（每次措辞都不同），阈值既抓不净又会误合并 ⇒ 判据只用「规范化后完全相同」。
TRACE_MAX_PER_PREDICATE = {"curated": 4}
# 先多取再收敛：被跳过（重复 / 超名额）的行不该白占 prompt 名额，多取一批让别的谓词补进来；
# 乘数 3 ≈ 覆盖 curated 的重复密度（68/71），硬上限 30 兜住查询与内存成本（不做无界多取）。
TRACE_FACT_FETCH_MULTIPLIER = 3
TRACE_FACT_FETCH_CAP = 30

_HEADER = "【当前现状速读】（只用于你落笔前对齐此刻的现实，不要逐条复述、不要当成必须完成的任务）"
_SEC_FACTS = "· 现状事实"
_SEC_SLOTS = "· 用户近况"
_SEC_INTENTS = "· 未完成计划"
_SEC_HEADERS = (_SEC_FACTS, _SEC_SLOTS, _SEC_INTENTS)

# world_facts.predicate → 中文标签（未知谓词原样输出，宁漏不编）
# C7（2026-09-24）：补 curated 的中文标签——此前会输出英文「- curated：…」夹在中文 prompt 里。
_PREDICATE_LABEL = {"status": "状态", "activity": "正在做", "location": "位置", "mood": "心情",
                    "curated": "近况"}
# user_facts.slot → 中文标签
_SLOT_LABEL = {
    "location": "位置/城市",
    "job": "工作/学业",
    "relationship": "感情状态",
    "living": "居住情况",
    "goal_state": "进行中计划状态",
    "health": "身体状态",
}


def _get(row, key, default=None):
    """ORM 行 / dict 通用取值（渲染函数因此可脱离数据库直接测）。"""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _one_line(text, limit: int = TRACE_LINE_CHARS) -> str:
    """压成单行并截断（禁止多行原文塞进 trace 打乱分区结构）。"""
    return " ".join(str(text or "").split())[:limit]


def fact_line(row) -> str:
    """world_facts 行 → trace 行（谓词标签 + 事实值）；无值返回空串。"""
    value = _one_line(_get(row, "object_value"))
    if not value:
        return ""
    predicate = str(_get(row, "predicate") or "").strip()
    label = _PREDICATE_LABEL.get(predicate, predicate)
    return f"- {label}：{value}" if label else f"- {value}"


def _merge_status_row(rows: list, status_row) -> list:
    """C7 保底（2026-09-24）：rows 里没有 predicate=="status" 的行时，把 status_row 插到最前。

    背景：事实按 updated_at desc 取 limit_facts（默认 8）条，char13 的最新若干条几乎
    全是 curated（腰伤/关系），真正的当前 status（例如「正在给轩做腰部护理」）会被挤出；
    这里保证它一定出现在 trace 里。已有 status 行或 status_row 为 None 时原样返回
    （纯函数，便于单测；_get 同时支持 ORM 行与 dict）。
    """
    if any(str(_get(r, "predicate") or "").strip() == "status" for r in rows):
        return rows
    if status_row is None:
        return rows
    return [status_row] + list(rows)


def _norm_fact_text(text) -> str:
    """判重用的规范化：去空白、去中英文标点/符号、转小写（只用于比较，不改原值）。"""
    return re.sub(r"[\W_]+", "", str(text or ""), flags=re.UNICODE).lower()


def select_fact_rows(rows, *, limit: int, max_per_predicate: dict | None = None) -> list:
    """Q1（2026-09-25）：事实行按谓词判重 + 名额收敛（**纯函数，零 DB / 零 LLM**）。

    ``rows`` 由调用方按 ``updated_at desc`` 排好 ⇒ 最先出现的那条＝最新的那条。
    - 判重：同 predicate 且规范化后的 object_value 完全相同 ⇒ 视为重复，只保留最先出现的；
    - 名额：某 predicate 已选条数达到 ``max_per_predicate[predicate]`` ⇒ 后续该谓词一律跳过
      （表里没有的谓词不限）；
    - 选满 ``limit`` 即停；``limit`` ≤ 0 返回空列表；
    - 脏行（predicate 为 None/空串、object_value 为空）不抛异常，按「谓词组键＝空串」正常参与
      判重与计数；绝不就地修改 ``rows``。
    """
    out: list = []
    if not limit or limit <= 0:
        return out
    quotas = max_per_predicate or {}
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    for row in rows or []:
        predicate = str(_get(row, "predicate") or "").strip()
        key = (predicate, _norm_fact_text(_get(row, "object_value")))
        if key in seen:
            continue
        cap = quotas.get(predicate)
        if cap is not None and counts.get(predicate, 0) >= cap:
            continue
        seen.add(key)
        counts[predicate] = counts.get(predicate, 0) + 1
        out.append(row)
        if len(out) >= limit:
            break
    return out


def slot_line(row) -> str:
    """user_facts 行 → trace 行；无值返回空串。"""
    value = _one_line(_get(row, "value"))
    if not value:
        return ""
    slot = str(_get(row, "slot") or "").strip()
    label = _SLOT_LABEL.get(slot, slot)
    return f"- {label}：{value}" if label else f"- {value}"


def intent_line(row) -> str:
    """prospective_intents 行 → trace 行；无内容返回空串。

    Q2（2026-09-25）：正文是当初写计划时的话，含「明天/下周三」这类相对时间词——事后注入即失真
    ⇒ 以该行 created_at（缺失则 updated_at）为基准日换成绝对日期；两者都没有或换算失败原样输出。
    """
    content = _one_line(_get(row, "content"))
    if not content:
        return ""
    # 函数内 import 与 try 同包：失败也只可能是「这一行不换算」，绝不冒泡到主链路
    try:
        from app.utils.relative_time import absolutize_relative_dates
        content = absolutize_relative_dates(
            content, _get(row, "created_at") or _get(row, "updated_at")) or content
    except Exception as e:  # 绝不冒泡到主链路
        _logger.warning("state trace intent absolutize failed: %s", e)
    return f"- {content}"


def render_state_trace(fact_lines: list[str] | None = None,
                       slot_lines: list[str] | None = None,
                       intent_lines: list[str] | None = None) -> str:
    """三分区 → 纯文本 trace（**纯函数，不查库**）；全空返回空串；总长硬上限。

    分区顺序固定（现状事实 → 用户槽值 → 未完成计划）；空分区整段省略；预算用尽即停。
    """
    parts: list[str] = []
    for header, lines in ((_SEC_FACTS, fact_lines), (_SEC_SLOTS, slot_lines), (_SEC_INTENTS, intent_lines)):
        items = [_one_line(ln) for ln in (lines or []) if str(ln or "").strip()]
        if items:
            parts.append(header)
            parts.extend(items)
    if not parts:
        return ""
    out = [_HEADER]
    used = len(_HEADER)
    for part in parts:
        if used + 1 + len(part) > TRACE_TOTAL_CHARS:
            break
        out.append(part)
        used += 1 + len(part)
    if len(out) > 1 and out[-1] in _SEC_HEADERS:  # 只有分区头 = 该段没装进任何行，撤掉头
        out.pop()
    if len(out) <= 1:
        return ""
    return "\n".join(out)[:TRACE_TOTAL_CHARS]


async def _readable_slots(user_id) -> list[str]:
    """读取侧槽白名单（红线：敏感槽 relationship/health 未经该账号显式开启则永不带出）。"""
    try:
        from app.memory.user_facts import readable_user_fact_slots_for
        return list(await readable_user_fact_slots_for(user_id) or [])
    except Exception as e:
        _logger.warning("state trace slot gate failed user=%s: %s", user_id, e)
    try:
        from app.memory.user_facts import enabled_user_fact_slots
        return list(enabled_user_fact_slots() or [])  # 按账号解析失败 → 全局口径（同样不旁路敏感槽）
    except Exception:
        return []


async def build_state_trace(db, *, character_id=None, user_id=None,
                            limit_facts: int = 8, limit_slots: int = 8,
                            limit_intents: int = 5) -> str:
    """只读拼装现状 trace（零 LLM / 零写入 / 不上屏）；异常收敛为空串 + WARNING。

    ``db`` 由调用方提供（同一会话内复用连接，POC 只读、绝不 commit）。
    """
    try:
        from app.models.memory import ProspectiveIntent, WorldFact
        from app.models.user import GlobalUserFact

        fact_rows: list = []
        if character_id and limit_facts > 0:
            fetch_limit = min(limit_facts * TRACE_FACT_FETCH_MULTIPLIER, TRACE_FACT_FETCH_CAP)
            stmt = select(WorldFact).where(
                WorldFact.character_id == character_id,
                # 硬口径：只取 active + 置信达标；expired / superseded 一律不进
                WorldFact.status == "active",
                WorldFact.confidence >= TRACE_CONFIDENCE_MIN,
            )
            if user_id:
                stmt = stmt.where(WorldFact.user_id == user_id)
            fact_rows = list((await db.execute(
                # Q1：先多取（多取的量在收敛时砍掉），被跳过的重复/超额条目才不至于白占名额
                stmt.order_by(WorldFact.updated_at.desc()).limit(fetch_limit)
            )).scalars().all())
            # Q1（2026-09-25）：判重 + 同谓词名额收敛回 limit_facts 条。
            fact_rows = select_fact_rows(fact_rows, limit=limit_facts,
                                         max_per_predicate=TRACE_MAX_PER_PREDICATE)
            # C7 保底（2026-09-24）：收敛后仍没有 status 行时，补查一条最新 status 置顶。
            # **必须在收敛之后**（顺序反了会出现「保底补上、又被收敛规则挤掉」的自我打架）；
            # 过滤条件与上面逐项一致（只多一个 predicate）。
            if not any(str(_get(r, "predicate") or "").strip() == "status" for r in fact_rows):
                sstmt = select(WorldFact).where(
                    WorldFact.character_id == character_id,
                    WorldFact.status == "active",
                    WorldFact.confidence >= TRACE_CONFIDENCE_MIN,
                    WorldFact.predicate == "status",
                )
                if user_id:
                    sstmt = sstmt.where(WorldFact.user_id == user_id)
                status_row = (await db.execute(
                    sstmt.order_by(WorldFact.updated_at.desc()).limit(1)
                )).scalars().first()
                fact_rows = _merge_status_row(fact_rows, status_row)

        slot_rows: list = []
        if user_id and limit_slots > 0:
            slots = await _readable_slots(user_id)
            if slots:
                slot_rows = list((await db.execute(
                    select(GlobalUserFact).where(
                        GlobalUserFact.user_id == user_id,
                        GlobalUserFact.slot.in_(slots),
                    ).order_by(GlobalUserFact.updated_at.desc()).limit(limit_slots)
                )).scalars().all())

        intent_rows: list = []
        if character_id and limit_intents > 0:
            istmt = select(ProspectiveIntent).where(
                ProspectiveIntent.character_id == character_id,
                ProspectiveIntent.status == "pending",  # discharged/matched/stale/expired 不进
            )
            if user_id:
                istmt = istmt.where(ProspectiveIntent.user_id == user_id)
            intent_rows = list((await db.execute(
                istmt.order_by(ProspectiveIntent.updated_at.desc()).limit(limit_intents)
            )).scalars().all())

        from app.memory.user_facts import fact_is_expired
        return render_state_trace(
            [fact_line(r) for r in fact_rows],
            [slot_line(r) for r in slot_rows if not fact_is_expired(r)],  # TTL 过期槽值同属旧现状
            [intent_line(r) for r in intent_rows],
        )
    except Exception as e:
        _logger.warning("state trace build failed char=%s: %s", character_id, e)
        return ""
