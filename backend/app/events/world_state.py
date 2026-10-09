# -*- coding: utf-8 -*-
"""World State 只读统一入口（P0 语义统一 · 第 2 步，2026-09-29；**零行为**）。

定位（方案 §1.4「六存储 + 两派生」＋ 差距表 G11）
────────────────────────────────────────────────────────
World State 实况是 **6 个物理存储 + 2 个读时派生视图**，注入侧五个分区（order 11 / 20 / 42 /
44 / 45）各自查各自、各用各的时间语义；本模块只提供**一个只读聚合函数**，把八路读齐并逐路
打标签（来源模块/表、as_of 口径、条目数、耗时 ms），让调用方第一次能在同一份 dict 里看到
「同一谓词在多套存储里的不同说法」。

**本步不接管任何读点**：五个注入分区（``section_working_state.py:107`` /
``section_current_state.py:14`` / ``section_user_now.py:20`` / ``section_world.py:118`` /
``section_curated.py:37``）一律未改；接线与「时效口径单一来源」留给方案**步骤 5**。

八路与 §1.4 的对应（逐路 rg 核实后的 file:line）
────────────────────────────────────────────────────────
六存储：
  1 ``world_facts``            表 ``models/memory/__init__.py:279``；读 ``events/facts.py:730
                               get_active_facts``（谓词级明细）＋ ``:771 get_character_view``
                               （注入文本，order=45 分区用的就是它，``section_world.py:123``）
  2 ``character_current_status`` 列 ``models/character/__init__.py:49``；写 ``chat_service.py:215``；
                               读侧无 API（persona 模板直读 ``agent/persona.py:79,205``）⇒ 本路按主键
                               共用快照会话读该列（``db.get``，不写）
  3 ``character_states``       表 ``models/character/__init__.py:77``（八维定义 ``application/
                               character_state_service.py:15 DIMENSIONS``）。**故意不调** ``:331
                               get_character_states``：它惰性 INSERT + 漂移结算 ``db.commit()``
                               （``:339-346``）⇒ 违背本入口「纯只读」硬约束；此处改为同表同字段的
                               裸 SELECT（口径差异见 AS_OF_BASIS，步骤 5 决定取舍）
  4 ``life_states``            表 ``models/life/__init__.py:22``（``needs_json:29``）；消费方
                               ``life/decision.py:139 decide(snap)``（纯函数，非注入面）
  5 ``working_state``          出口 ``application/working_state_service.py:41 get_latest``；
                               注入分区 ``agent/context/section_working_state.py:107``（order=20）
  6 ``user_facts``             表 ``models/user/__init__.py:181``；读 ``memory/user_facts.py:372
                               get_active_user_facts``；注入分区 ``section_user_now.py:20``（order=44）
两派生视图：
  A ``current_state_anchor``   ``memory/current_state.py:101 current_user_state_anchor``（三源读时拼）；
                               注入分区 ``section_current_state.py:14``（order=42）
  B ``status_memories``        ``memories`` 表里的状态派生条（source=``status``、sub_type=``status``，
                               ``chat_service.py:229-234``）；读侧判据 ``events/facts.py:92
                               status_memory_expired``，注入走通用记忆检索

刻意**不**提供的路（登记给步骤 5，勿在此越界）：``events/facts.py:673 get_curated_facts``（同表
``world_facts`` 的 kind 分治读法，属第 1 路的另一个视图，独立 quota 语义）。

时区口径（AGENTS.md「时间存 UTC（naive），北京时间 = UTC+8」）
────────────────────────────────────────────────────────
库内所有时间列都是 **naive UTC**。本模块只做 naive-UTC 与 naive-UTC 之间的减法（窗口裁剪），
**不做任何本地时区换算**，因此不存在北京日界问题；唯一的时间入口是
``utils/timeutil.now_naive_utc()``（全库统一写库/比较时间函数，返回 UTC 去 tzinfo）。
调用方若要展示成北京时间，自行用 ``timeutil.shift_utc_naive(dt, 8)``——本模块不代劳，
免得把「快照口径」与「展示口径」混成一份数据。

零行为与只读边界
────────────────────────────────────────────────────────
- 不写库、不调 LLM、**不加缓存**（方案步骤 2 第 2 项明令；加了就和各分区 quota 语义纠缠）；
- 会话口径：路 2/3/4/5/6/B 共用**同一个只读会话**（``db.get`` / ``select``，全程无 ``add`` /
  ``flush`` / ``commit``）；路 1 与派生 A 直接调原注入侧函数（其内部各自开自己的只读会话），
  以保证「与注入侧逐值等价」优先于「省一次连接」；
- **异常隔离**：单路抛错只把该路标 ``degraded`` 并记原因（WARNING 日志），整体不抛——
  与项目既有 fail-open 习惯（``facts.get_active_facts`` 失败返回 ``[]``、
  ``working_state_section`` 失败静默）一致；
- 会话本身开不起来时八路全标 ``degraded``，函数仍返回完整结构。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from app import actors
from app.utils.timeutil import now_naive_utc

_logger = logging.getLogger("events.world_state")

# 入口版本（结构变更时递增，供调用方判别快照形状）
# v2（A41，2026-10-10）：路 2/3/4/5 的 ``fresh_until`` / ``fresh_at_as_of`` 从恒 (None, True) 改为
# 按 facts 同名谓词的窗口计算 ⇒ 读侧第一次能区分「刚写」与「早就过期」。形状本身没删字段，
# 但**语义变了**，按「结构变更时递增」的原文口径递增一位，让调用方（Workspace 投影、现状锚点）
# 能判别自己拿到的是哪一版口径。
SNAPSHOT_VERSION = "v2"

# 方案 §1.4 的八路：六存储 + 两派生视图（顺序即 §1.4 表序，稳定，勿随意重排）
STORE_ROUTES: tuple[str, ...] = (
    "world_facts",
    "character_current_status",
    "character_states",
    "life_states",
    "working_state",
    "user_facts",
)
DERIVED_ROUTES: tuple[str, ...] = (
    "current_state_anchor",
    "status_memories",
)
ALL_ROUTES: tuple[str, ...] = STORE_ROUTES + DERIVED_ROUTES

# 各路的**权威事实源**标签（调用方据此判断「同一谓词该信谁」；方案断点 #9 的判效基准）
AUTHORITATIVE_SOURCE: dict[str, str] = {
    "world_facts": "WorldFact 管「现状」（新鲜窗，注入对话）",
    "character_current_status": "角色当下状态的第二份权威（列上无 TTL、无 supersede；新鲜度是读侧口径，见 AS_OF_BASIS）",
    "character_states": "角色八维情绪/生理态（随漂移连续变化）",
    "life_states": "AI 生活链自有状态（8 需求 + 位置/房间）",
    "working_state": "会话滚动工作记忆（三桶 diff）",
    "user_facts": "用户客观现状的**用户级唯一事实源**（跨角色共享）",
    "current_state_anchor": "派生视图：用户现状权威锚点（读时拼，不落库）",
    "status_memories": "派生视图：状态更新在记忆面的落点（长期、走衰减）",
}

# 各路的 as_of 口径说明（**逐路如实登记**：谁真的吃 as_of、谁只是打标）
AS_OF_BASIS: dict[str, str] = {
    "world_facts": "新鲜窗判据用 as_of；行集与 audience 过滤沿用 get_active_facts 原口径",
    "character_current_status": "列上无该状态自己的时间戳（行级 updated_at 被任意列改写刷新 ⇒ 只是粗粒度「最近碰过」）；"
                                "as_of 与 facts 的 status 窗一起判 fresh_at_as_of，行值本身不随 as_of 变化",
    "character_states": "读的是**库内存量值**（裸 SELECT，不回放漂移、不惰性建行）⇒ 与走 get_character_states 的注入侧可能差一次漂移结算的量；"
                        "as_of 除时龄外还按 facts 的 mood 窗判 fresh_at_as_of",
    "life_states": "as_of 决定 needs 距上次 tick 的小时数，并按 facts 的 location 窗判 fresh_at_as_of；需求值不回放",
    "working_state": "行集＝该 (user,char) 最新一条（id 降序）；as_of 除滚动时龄外，按 facts 的 status 窗判 fresh_at_as_of",
    "user_facts": "行集沿用 get_active_user_facts（内含 valid_to 过期剔除）；as_of 另逐路给 fresh_at_as_of",
    "current_state_anchor": "锚点函数内部自取 now ⇒ 文本对应当前时刻；as_of 为快照口径",
    "status_memories": "判据 status_memory_expired 的 now 形参**显式用 as_of**",
}

# 各路读点的 file:line（仓库相对路径）。**行号按本步实施时的树逐路 grep 复核**——方案 §1.4
# 撰写时的行号已随并发批次漂移（如 ``events/facts.py`` 被断点 #9 改过），照抄文档会指错地方。
ROUTE_READERS: dict[str, tuple[str, str]] = {
    "world_facts": ("app.events.facts", "backend/app/events/facts.py:730,771"),
    "character_current_status": ("app.models.character.AICharacter", "backend/app/models/character/__init__.py:49"),
    "character_states": ("app.models.character.CharacterState 表 + DIMENSIONS 字段清单（裸 SELECT，不走带写的出口）",
                         "backend/app/models/character/__init__.py:77 / backend/app/application/character_state_service.py:15"),
    "life_states": ("app.models.life.LifeState", "backend/app/models/life/__init__.py:22"),
    "working_state": ("app.application.working_state_service", "backend/app/application/working_state_service.py:41"),
    "user_facts": ("app.memory.user_facts", "backend/app/memory/user_facts.py:372 / backend/app/models/user/__init__.py:181"),
    "current_state_anchor": ("app.memory.current_state", "backend/app/memory/current_state.py:101"),
    "status_memories": ("app.events.facts(判据) + app.models.memory.Memory", "backend/app/application/chat_service.py:229"),
}

# 状态派生条的身份判据（chat_service.py:229-234 写入时的三个字面值；与 facts 侧同源）
_STATUS_MEM_TYPE = "insight"   # 写入侧 memory_type（chat_service.py:229）
# status_memories 取多少条（与注入侧「近若干条」量级对齐；纯上限，不影响判据）
_STATUS_MEMORIES_LIMIT = 12

# ── A41（2026-10-10，A37 批 4）：四张瞬时视图补上新鲜度边界（不变量 I9 的推广）──
# 原先只有路 1（world_facts，走 facts 的新鲜窗）与路 6（user_facts，带 valid_to）自带失效时刻，
# 路 2/3/4/5 的 ``fresh_until`` 恒 ``None``（旧注释直说「永不自动失效」）⇒ 消费侧（Workspace 投影、
# 现状锚点判效）看不出「一条十分钟前的状态」和「一条一周前的状态」有何区别。
# **窗口数值一律取自 events/facts.py**（``_TRANSIENT_FRESH_HOURS`` 与三个 ``*_FRESH_HOURS`` 常量），
# 本文件不新写小时数字面量——断点 #9 的「单一来源」口径同样适用于快照面。
# 映射依据（逐路如实）：路 2 写的是 ``current_status``（与 status 谓词同语义）；路 3 是情绪/生理
# 八维（与 mood 同寿命）；路 4 带位置/房间（与 location 同窗）；路 5 是会话滚动记忆（一次会话尺度，
# 与 status 同窗）。补齐只**新增标注**，不删行、不改任何存量值。
_VIEW_FRESH_PREDICATE: dict[str, str] = {
    "character_current_status": "status",
    "character_states": "mood",
    "life_states": "location",
    "working_state": "status",
}


def _view_freshness(route: str, asserted, as_of) -> tuple[str | None, bool]:
    """该视图的 (fresh_until, fresh_at_as_of)：断言时刻 + facts 的同一窗口。

    断言时刻缺失 ⇒ ``(None, False)``：既给不出失效时刻，也不当作今天的事实（保守，不虚构）。
    """
    from app.events import facts as _facts
    window = _facts._TRANSIENT_FRESH_HOURS[_VIEW_FRESH_PREDICATE[route]]
    until = (asserted + timedelta(hours=window)) if asserted is not None else None
    return _iso(until), bool(_facts._predicate_fresh(asserted, as_of, window))


def _iso(dt) -> str | None:
    """datetime → 可比较的 ISO 文本（naive UTC，全路同一口径）；None 原样。"""
    return None if dt is None else dt.isoformat(sep=" ", timespec="seconds")


def _naive_utc(dt):
    """归一为 naive UTC（复用 timeutil.to_naive_utc：带 tzinfo 先 astimezone(UTC) 再去 tzinfo）。"""
    from app.utils.timeutil import to_naive_utc
    return to_naive_utc(dt)


def _actor_of(author_value, *, default: str = actors.ACTOR_UNSET) -> str:
    """归属归一：复用 ``actors.normalize_sender``（＝ ``write.py::_normalize_sender`` 逐字口径）。

    未登记/空值 ⇒ ``default``（``actors.ACTORS`` 里没有 ``unset``——它是「没有归属」的占位，
    不是一种归属，见 actors.py:45 注释）。
    """
    try:
        return actors.normalize_sender(author_value) or default
    except Exception:
        return default


def _safe_json(raw) -> dict | list:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


# ─────────────────────── 六存储 ───────────────────────

async def _read_world_facts(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """路 1 ``world_facts``：谓词级明细 + 注入文本（行集取自 get_active_facts 原函数，零复制过滤）。"""
    from app.events import facts as _facts
    rows = await _facts.get_active_facts(
        character_id=character_id, user_id=user_id,
        viewer_type="character", viewer_id=character_id, limit=_facts.MAX_FACTS_PER_CHAR,
    )
    # view_text 用 get_character_view 的**默认 limit**（6）＝ section_world.py:123 实际喂给模型的那串字
    view_text = await _facts.get_character_view(character_id, user_id)
    out: list[dict] = []
    for r in rows:
        asserted = _naive_utc(r.asserted_at)
        window_h = _facts._TRANSIENT_FRESH_HOURS.get(r.predicate)
        fresh_until = (asserted + timedelta(hours=window_h)) if (asserted and window_h) else None
        for cand in (r.expires_at, r.stale_after):
            cand = _naive_utc(cand)
            if cand is not None and (fresh_until is None or cand < fresh_until):
                fresh_until = cand  # 取最早失效者（三套时效并存时不放大有效期）
        out.append({
            "source_store": "world_facts",
            "source_table": "world_facts",
            "subject_type": r.subject_type,
            "subject_id": r.subject_id,
            "actor": _actor_of(r.author),
            "epistemic_status": r.epistemic_status,
            "predicate": r.predicate,
            "kind": r.kind,
            "value": r.object_value,
            "asserted_at": _iso(asserted),
            "fresh_until": _iso(fresh_until),
            "is_authoritative": bool(getattr(r, "is_authoritative", False)),
            "fresh_at_as_of": (r.predicate not in _facts.TRANSIENT_PREDICATES)
            or (asserted is not None
                and _facts._predicate_fresh(asserted, as_of, window_h or _facts.STATUS_FRESH_HOURS)),
            "line": _facts.fact_text(r),
        })
    # 注入文本单独占一条（view_text 是 order=45 分区真正喂给模型的那串字）；无事实时 view_text=""
    if view_text:
        out.append({
            "source_store": "world_facts", "source_table": "world_facts",
            "subject_type": None, "subject_id": None,
            "actor": actors.ACTOR_SYSTEM, "epistemic_status": actors.EPISTEMIC_FACT,
            "predicate": None, "kind": "_view_text", "value": view_text,
            "asserted_at": None, "fresh_until": None,
            "is_authoritative": False, "fresh_at_as_of": True, "line": view_text,
        })
    return out


async def _read_character_current_status(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """路 2 ``character_current_status``：角色当下状态的第二份权威（列上无 TTL、无 supersede）。

    A41：写侧确实没有失效时刻，但**读侧不能因此当作永不过期**（不变量 I9）——按行级
    ``updated_at`` + facts 的 status 窗给出边界，并在 ``AS_OF_BASIS`` 如实登记「这是读侧口径」。
    """
    from app.models.character import AICharacter
    row = await db.get(AICharacter, character_id)
    if row is None:
        return []
    asserted = _naive_utc(getattr(row, "updated_at", None))
    until, fresh = _view_freshness("character_current_status", asserted, as_of)
    return [{
        "source_store": "character_current_status",
        "source_table": "ai_characters",
        "subject_type": actors.ACTOR_CHARACTER,
        "subject_id": character_id,
        "actor": actors.ACTOR_CHARACTER,
        "epistemic_status": actors.EPISTEMIC_FACT,
        "predicate": "status",
        "kind": "status",
        "value": row.current_status,
        "asserted_at": _iso(asserted),
        # A41：列本身没有 TTL，但**读侧必须有一条新鲜度边界**（I9）；窗口取自 facts 的 status 窗
        "fresh_until": until,
        "is_authoritative": False,
        "fresh_at_as_of": fresh,
        "line": f"角色当下状态: {row.current_status}",
    }]


async def _read_character_states(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """路 3 ``character_states``：八维存量值（裸 SELECT）。

    **不复用** ``character_state_service.get_character_states``（:331）：该出口对缺行做惰性
    ``db.add`` + ``commit``、并在漂移有变化时再 ``commit``（:339-346）——那会让「只读快照」写库，
    与本入口的硬约束冲突。八维字段名仍取自同一份 ``DIMENSIONS``（:15），不新造字面量。
    """
    from sqlalchemy import select
    from app.models.character import CharacterState
    from app.application.character_state_service import DIMENSIONS
    row = (await db.execute(
        select(CharacterState).where(CharacterState.character_id == character_id)
    )).scalar_one_or_none()
    if row is None:
        return []          # 缺行不建行（建行属写侧，交 get_character_states / 步骤 5）
    dims = [k for k, _label, _desc in DIMENSIONS]
    cn = {k: label for k, label, _desc in DIMENSIONS}
    updated = _naive_utc(getattr(row, "updated_at", None))
    values = {k: getattr(row, k, None) for k in dims}
    values["trust"] = getattr(row, "trust", None)
    until, fresh = _view_freshness("character_states", updated, as_of)
    return [{
        "source_store": "character_states",
        "source_table": "character_states",
        "subject_type": actors.ACTOR_CHARACTER,
        "subject_id": character_id,
        "actor": actors.ACTOR_CHARACTER,
        "epistemic_status": actors.EPISTEMIC_FACT,
        "predicate": "state_vector",
        "kind": "character_state",
        "value": values,                              # 八维 + trust（库内存量，未回放漂移）
        "labels": cn,
        "asserted_at": _iso(updated),
        # A41：八维随漂移连续变化、列上无失效时刻 ⇒ 用 facts 的 mood 窗作读侧边界（I9）
        "fresh_until": until,
        "is_authoritative": False,
        "fresh_at_as_of": fresh,
        "age_hours_at_as_of": (round((as_of - updated).total_seconds() / 3600.0, 2)
                               if updated else None),
        "line": "；".join(f"{cn[k]}{values.get(k)}" for k in dims),
    }]


async def _read_life_states(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """路 4 ``life_states``：AI 生活链 8 项需求（life/decision.py:139 的输入面，非注入面）。"""
    from app.models.life import LifeState
    from sqlalchemy import select
    row = (await db.execute(
        select(LifeState).where(LifeState.character_id == character_id)
    )).scalar_one_or_none()
    if row is None:
        return []
    needs = _safe_json(row.needs_json)
    needs = needs if isinstance(needs, dict) else {}
    last_tick = _naive_utc(row.last_tick_at)
    age_h = round((as_of - last_tick).total_seconds() / 3600.0, 2) if last_tick else None
    until, fresh = _view_freshness("life_states", last_tick, as_of)
    return [{
        "source_store": "life_states",
        "source_table": "life_states",
        "subject_type": actors.ACTOR_CHARACTER,
        "subject_id": character_id,
        "actor": actors.ACTOR_CHARACTER,
        "epistemic_status": actors.EPISTEMIC_FACT,
        "predicate": "needs",
        "kind": "life_state",
        "value": needs,
        "extra": {"location": row.location, "current_room": row.current_room, "phase": row.phase},
        "asserted_at": _iso(last_tick),
        # A41：needs/位置由 life_loop 逐 tick 覆写、列上无失效时刻 ⇒ 用 facts 的 location 窗（I9）
        "fresh_until": until,
        "is_authoritative": False,
        "fresh_at_as_of": fresh,
        "age_hours_at_as_of": age_h,
        "line": "需求 " + " ".join(f"{k}={v}" for k, v in sorted(needs.items())),
    }]


async def _read_working_state(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """路 5 ``working_state``：会话滚动工作记忆最新一条（出口 get_latest，复用不自写查询）。"""
    from app.application.working_state_service import get_latest
    row = await get_latest(db, user_id, character_id)
    if row is None:
        return []
    created = _naive_utc(getattr(row, "created_at", None) or getattr(row, "updated_at", None))
    age_h = round((as_of - created).total_seconds() / 3600.0, 2) if created else None
    until, fresh = _view_freshness("working_state", created, as_of)
    return [{
        "source_store": "working_state",
        "source_table": "memories",
        "subject_type": actors.ACTOR_CHARACTER,
        "subject_id": row.id,
        "actor": actors.ACTOR_SYSTEM,           # 滚动评估由系统产出（非用户亲口）
        "epistemic_status": actors.EPISTEMIC_INFERRED,
        "predicate": "working_state",
        "kind": "working_state",
        "value": _safe_json(row.content),       # 三桶（parsed 失败 ⇒ {}）
        "memory_id": row.id,
        "asserted_at": _iso(created),
        # A41：本行只被「下一条滚动」覆盖，长期不聊就一直挂着 ⇒ 补一条会话尺度的边界（I9）
        "fresh_until": until,
        "is_authoritative": False,
        "fresh_at_as_of": fresh,
        "age_hours_at_as_of": age_h,
        "line": f"working_state#{row.id}",
    }]


async def _read_user_facts(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """路 6 ``user_facts``：用户硬档案（复用 get_active_user_facts，含启用槽与 valid_to 口径）。"""
    from app.memory import user_facts as _uf
    out: list[dict] = []
    for r in await _uf.get_active_user_facts(user_id):
        valid_from = _naive_utc(r.valid_from)
        valid_to = _naive_utc(r.valid_to)
        out.append({
            "source_store": "user_facts",
            "source_table": "user_facts",
            "subject_type": actors.ACTOR_USER,
            "subject_id": user_id,
            "actor": _actor_of(r.source, default=actors.ACTOR_USER),
            "epistemic_status": r.epistemic_status,
            "predicate": r.slot,
            "kind": "user_fact",
            "value": r.value,
            "confidence": r.confidence,
            "asserted_at": _iso(valid_from),
            "fresh_until": _iso(valid_to),      # None＝不过期（VOLATILE_FACT_TTL_DAYS 未登记的槽）
            "is_authoritative": True,           # 用户级唯一事实源（models/user 注释口径）
            "fresh_at_as_of": not _uf.fact_is_expired(r, as_of),
            "line": f"{r.slot}={r.value}",
        })
    return out


# ─────────────────────── 两派生视图 ───────────────────────

async def _read_current_state_anchor(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """派生 A ``current_state_anchor``：用户现状权威锚点文本（三源读时拼，本身不落库）。"""
    from app.memory.current_state import current_user_state_anchor
    text = await current_user_state_anchor(character_id=character_id, user_id=user_id)
    if not text:
        return []
    return [{
        "source_store": "current_state_anchor",
        "source_table": "(derived: world_facts + users + user_facts)",
        "subject_type": actors.ACTOR_USER,
        "subject_id": user_id,
        "actor": actors.ACTOR_SYSTEM,
        "epistemic_status": actors.EPISTEMIC_FACT,
        "predicate": "anchor_text",
        "kind": "derived",
        "value": text,
        "asserted_at": None,          # 派生视图无自身时间戳（三源各自时间戳在源路里）
        "fresh_until": None,
        "is_authoritative": True,     # 提示词层面声明「以此为准，旧记忆不得与此矛盾」
        "fresh_at_as_of": True,
        "line": text.strip(),
    }]


async def _read_status_memories(db, *, user_id: int, character_id: int, as_of) -> list[dict]:
    """派生 B ``status_memories``：状态更新在记忆面的落点（同语义第三份落点，走衰减）。

    身份判据与失效时刻都**直接复用** facts 侧单一来源（``is_status_derived_memory`` /
    ``status_memory_expiry`` / ``status_memory_expired``，``events/facts.py:75,80,92``），
    不在此另写字面量、不另算小时数（断点 #9 收口后两侧同瞬间）。
    """
    from sqlalchemy import select
    from app.events import facts as _facts
    from app.models.memory import Memory
    rows = list((await db.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.character_id == character_id,
            Memory.sub_type == _facts.STATUS_MEMORY_SUB_TYPE,
            Memory.source == _facts.STATUS_MEMORY_SOURCE,
        ).order_by(Memory.id.desc()).limit(_STATUS_MEMORIES_LIMIT)
    )).scalars().all())
    out: list[dict] = []
    for r in rows:
        # 身份二次确认用同一纯函数（SQL 已按两列过滤，此处防列值漂移）
        if not _facts.is_status_derived_memory(getattr(r, "sub_type", None), getattr(r, "source", None)):
            continue
        created = _naive_utc(r.created_at)
        exp = _facts.status_memory_expiry(_naive_utc(r.valid_to), created)
        expired = _facts.status_memory_expired(
            getattr(r, "sub_type", None), getattr(r, "source", None),
            _naive_utc(r.valid_to), created, as_of,
        )
        out.append({
            "source_store": "status_memories",
            "source_table": "memories",
            "subject_type": actors.ACTOR_CHARACTER,
            "subject_id": r.character_id,
            "actor": _actor_of(r.speaker_type),
            "epistemic_status": r.epistemic_status,
            "predicate": "status",
            "kind": _STATUS_MEM_TYPE,
            "value": r.content,
            "memory_id": r.id,
            "valid_to": _iso(_naive_utc(r.valid_to)),          # 断点 #9 同源标记（存量行为 None）
            "asserted_at": _iso(created),
            "fresh_until": _iso(_naive_utc(exp)),              # None ⇒ 无从判断（按不剔除处理）
            "is_authoritative": False,
            "fresh_at_as_of": not expired,
            "line": (r.content or "")[:120],
        })
    return out


_ROUTE_READERS_IMPL = {
    "world_facts": _read_world_facts,
    "character_current_status": _read_character_current_status,
    "character_states": _read_character_states,
    "life_states": _read_life_states,
    "working_state": _read_working_state,
    "user_facts": _read_user_facts,
    "current_state_anchor": _read_current_state_anchor,
    "status_memories": _read_status_memories,
}


def _new_route(name: str) -> dict:
    module, at = ROUTE_READERS[name]
    return {
        "route": name,
        "kind": "store" if name in STORE_ROUTES else "derived_view",
        "source_module": module,
        "source_at": at,
        "source_table": None,
        "authoritative_source": AUTHORITATIVE_SOURCE[name],
        "as_of_basis": AS_OF_BASIS[name],
        "status": "empty",
        "error": None,
        "count": 0,
        "elapsed_ms": 0.0,
        "items": [],
        "predicates": [],
    }


def _cross_store(rows_by_route: dict[str, list[dict]]) -> dict[str, dict[str, list[str]]]:
    """同一 predicate 在多套存储里的不同说法（方案步骤 2 判效 ②：首次量化 #9/#12 的不一致率）。"""
    out: dict[str, dict[str, list[str]]] = {}
    for name, items in rows_by_route.items():
        for it in items:
            p = it.get("predicate")
            if not p:
                continue
            out.setdefault(p, {}).setdefault(name, [])
            out[p][name].append(str(it.get("value"))[:160])
    return {p: dict(stores) for p, stores in sorted(out.items())}


async def world_state_snapshot(user_id: int, character_id: int,
                               *, as_of: datetime | None = None) -> dict:
    """World State 只读统一入口：一次读齐六存储 + 两派生视图并逐路打标签（**纯只读、零行为、整体不抛**）。

    参数
    ──
    user_id / character_id：快照主体。
    as_of：naive UTC 观察时刻；``None`` ⇒ :func:`app.utils.timeutil.now_naive_utc`。
        带 tzinfo 的入参先经 ``to_naive_utc`` 归一（保持同一时刻）——**不做本地时区换算**，
        与库内 naive-UTC 约定一致（AGENTS.md「时间存 UTC（naive），北京时间 = UTC+8」）。

    返回（结构化 dict，键稳定）
    ──
    ``{"version", "user_id", "character_id", "as_of", "as_of_source", "tz_basis",
       "routes": {路名: {"route","kind","source_module","source_at","source_table",
                          "authoritative_source","as_of_basis","status","error","count",
                          "elapsed_ms","items","predicates"}},
       "cross_store": {谓词: {路名: [值…]}},
       "summary": {"total_ms","route_count","item_count","ok","empty","degraded"}}``

    每路 ``status`` ∈ ``ok`` / ``empty``（读通但零行）/ ``degraded``（该路抛错，已记 ``error``，
    其余路不受影响）。单路异常只标该路、整体不抛；全程零 ``add`` / ``flush`` / ``commit``。
    """
    if as_of is None:
        as_of_src = "now_naive_utc()"
        moment = now_naive_utc()
    else:
        as_of_src = "caller"
        moment = _naive_utc(as_of)
    routes: dict[str, dict] = {name: _new_route(name) for name in ALL_ROUTES}
    t_all = time.perf_counter()
    db = None
    try:
        from app.db.database import async_session_factory
        db = async_session_factory()
    except Exception as e:  # 会话开不起来：八路全 degraded，仍返回完整结构
        _logger.warning("world_state snapshot session open failed user=%s char=%s: %s",
                        user_id, character_id, e)
        for name, meta in routes.items():
            meta.update(status="degraded", error=f"session_unavailable: {type(e).__name__}: {e}")

    rows_by_route: dict[str, list[dict]] = {}
    try:
        if db is not None:                    # 会话开不起来时八路已全 degraded，不再空跑
            for name, meta in routes.items():
                t0 = time.perf_counter()
                try:
                    items = await _ROUTE_READERS_IMPL[name](
                        db, user_id=user_id, character_id=character_id, as_of=moment,
                    )
                    meta["count"] = len(items)
                    meta["items"] = items
                    meta["source_table"] = items[0].get("source_table") if items else None
                    meta["predicates"] = sorted({str(i["predicate"]) for i in items if i.get("predicate")})
                    meta["status"] = "ok" if items else "empty"
                except Exception as e:
                    _logger.warning("world_state route degraded=%s user=%s char=%s: %s: %s",
                                    name, user_id, character_id, type(e).__name__, e)
                    meta["status"] = "degraded"
                    meta["error"] = f"{type(e).__name__}: {e}"
                    # 回滚仅用于**解除共享会话的 PendingRollback 状态**（否则单路 DB 异常会污染后续
                    # 各路、把「异常隔离」变成级联失败）；本函数全程不 add/flush/commit，回滚不落任何变更。
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                finally:
                    meta["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
                    if meta["status"] != "degraded":
                        rows_by_route[name] = meta["items"]
    finally:
        if db is not None:
            try:
                await db.close()
            except Exception as e:  # 兜底：关不掉也只是句柄提示，不影响返回
                _logger.warning("world_state snapshot session close failed: %s", e)

    statuses = [m["status"] for m in routes.values()]
    return {
        "version": SNAPSHOT_VERSION,
        "user_id": user_id,
        "character_id": character_id,
        "as_of": _iso(moment),
        "as_of_source": as_of_src,
        "tz_basis": "naive UTC（库内统一；北京时间 = UTC+8，本入口不做本地时区换算）",
        "routes": routes,
        "cross_store": _cross_store(rows_by_route),
        "summary": {
            "total_ms": round((time.perf_counter() - t_all) * 1000.0, 2),
            "route_count": len(routes),
            "item_count": sum(m["count"] for m in routes.values()),
            "ok": statuses.count("ok"),
            "empty": statuses.count("empty"),
            "degraded": statuses.count("degraded"),
            "degraded_routes": [n for n, m in routes.items() if m["status"] == "degraded"],
        },
    }


__all__ = ["SNAPSHOT_VERSION", "STORE_ROUTES", "DERIVED_ROUTES", "ALL_ROUTES", "world_state_snapshot"]

# ── 本入口的性质（务必照此理解，勿越界）──────────────────────────────
# 1. **只读聚合**：本函数只做「读齐 + 打标签 + 计时」，**不改变任何注入文本**；八个注入侧读点
#    （section_world / section_current_state / section_user_now / section_working_state /
#    section_curated / persona / legacy.py:268）一行都没改，也不得在本步改。
# 2. 接线（分区改调本快照、quota 归属、时效口径单一来源 fresh_until()、legacy 旧路径计数）
#    全部留给方案**步骤 5**；同批在做的断点 #9（状态三写收口）由它自己落地，本模块只登记实况。
# 3. 不加缓存（方案步骤 2 第 2 项明令）：调用方自行决定何时快照。
# 4. 死读路径埋点在 ``memory/current_state.py`` 的 ``_char_world_user_facts``（subject=user 命中行数，
#    走 ``memory/observability.obs_event``，零写库）——本模块经派生 A 路触发它，不重复计数。
