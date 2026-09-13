# -*- coding: utf-8 -*-
"""Ariadne 模块G：前瞻意图（promise/cue）采集、状态机、到期采集与兑现（2026-09-04）。

- 不进 memories，不参与检索/衰减/查重/#70 supersede；
- promise：有 due 时间窗，Scheduler 周期扫 due_end<=now 的 pending；
- cue：due 为空，聊天时由 context 分区用 cue_terms 做确定性匹配（在线零 LLM）；
- 兑现即焚 discharged（一次性）；due_end+7 天未兑现 → expired（留痕不删）；用户取消 → cancelled；
- 时间型触发经 3.10 TriggerSource（scheduling/sources/prospective_intent.py）接入 arbiter，
  本模块放真实逻辑（薄 source 只适配，对齐 unfinished_topic 的分工）。

前瞻约定治理（2026-09-13，交接：主体口径 / 时效收窄 / 去模板去重 / 存量清理）：
- ① side=self|user：落库时按 content/kind 判定「谁答应的」，写进 cue_terms_json 元数据
  （只加标记不改语义，零迁移）；渲染按 side 分流，由既有 promise_self_side_split 开关门控，
  开关关=逐字节回退当前口径。
- ② 时效收窄：只有 due_end 之后 ≤ PROMISE_ACTIVE_WINDOW_HOURS 小时才算「可自然提起」；
  超窗由 mark_stale_overdue() 置 stale（留痕不删，仍可检索/回忆，但不进主动提起）。
- ③ 去模板去重：同一 intent 幂等只提一次（原子认领 pending→discharged，失败回滚）；
  同角色一小时内同款开场最多 1 条（recent_opener_exists）；写入期近重复合并。
- ④ 存量清理只读/写脚本化：scripts/cleanup_prospective_intents.py（dry-run 默认）。

TODO（Ariadne 模块G 一期裁剪，2026-09-04 拍板，不实现）：
- kind=wish（无条件心愿）：一期裁掉，只做 promise/cue；
- 线索型主动推送（到期 cue 主动发消息）：一期只注入本轮提醒块，主动推送二期；
- cue 匹配一期之子串，正则/语义匹配二期（可复用 LorebookEntry 的 is_regex 经验）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db.database import async_session_factory
from app.models.character import ProactiveMessageLog
from app.models.memory import ProspectiveIntent
from app.utils.logger import get_logger

_logger = get_logger("scheduling.prospective_intent")

GRACE_DAYS = 7              # 到期宽限：超过 due_end 7 天仍未兑现 → expired
PROMISE_SCAN_LOOKAHEAD = timedelta(days=3650)  # 防御性上限（无 due_end 的 cue 不被时间扫描捞到）

# ── 前瞻约定治理常量（2026-09-13）──
PROMISE_ACTIVE_WINDOW_HOURS = 12   # ② due_end 之后 ≤ N 小时才可自然提起（超窗 → stale）
STALE_NODUE_DAYS = 30              # ④ 无 due 且创建超 M 天 → stale（与清理脚本默认阈值一致）
SIMILAR_INTENT_THRESHOLD = 0.95    # ③ 写入期近重复合并阈值（同角色 + 同 due 窗口）
OPENING_COOLDOWN_HOURS = 1         # ③ 同款开场冷却：同角色一小时内最多 1 条
IDEMPOTENT_FIRED_PREFIX = "prospective_intent_fired"  # ③ 幂等键前缀

# ① 主体口径判定（零 LLM）：自述开头的 AI 承诺 → side=self；显式「用户…」或 cue → side=user。
_SELF_SIDE_PREFIXES = (
    "我承诺", "我答应", "我保证", "我来", "我会", "我去", "我要",
    "我将", "我负责", "我准备", "我打算",
)
_USER_SIDE_PREFIXES = ("用户", "对方", "ta", "TA", "他", "她")
_USER_SIDE_INFIX = ("用户", "对方")

# ③ 同款开场（模板复读）标志串；命中这些开场的主动消息计入一小时内冷却。
_PROACTIVE_OPENING_MARKERS = (
    "我记得你之前说过", "你之前不是说", "我记得你说过", "你之前说过",
    "我记得你之前", "你之前不是说过", "我记得你以前说过",
)
# ② 禁止的「当下时态断言」：跨天/迟到提起时不得暗示现在正是那个时候。
_PRESENT_TENSE_ASSERTIONS = (
    "现在时间也差不多了", "现在时间差不多了", "现在已经到时间了",
    "现在已经差不多了", "时间也差不多了",
)


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _loads_cue_terms(s: str) -> list[str]:
    """cue_terms_json → terms list。

    兼容两种格式：
    - 旧：`["火锅", "周末"]`（直接 list[str]）
    - 新（2026-09-07 放宽口径）：`{"confidence": "low|medium|high", "terms": ["火锅", "周末"]}`
    无法解析时回退空列表，匹配静默失败（fail-open）。
    """
    try:
        v = json.loads(s or "")
    except Exception:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, dict):
        terms = v.get("terms")
        if isinstance(terms, list):
            return [str(x).strip() for x in terms if str(x).strip()]
    return []


# ───────────────────────── ① 主体口径（side=self|user），零 LLM，落库即定 ─────────────────────────
def classify_intent_side(content: str, kind: str = "promise") -> str:
    """判定「这个约定/承诺是谁答应的」：'self'（AI 自己许的）或 'user'（用户侧）。

    规则（与交接 ① 对齐，保守）：
    - kind=cue → user（线索型一律用户侧）；
    - content 以「用户/对方/他/她/TA」开头 → user；
    - content 以「我承诺/我答应/我来…」这类自述开头 → self；
    - 其余含「用户/对方」→ user，含「我承诺/我答应/我保证」→ self；
    - 兜底 user（与既有渲染口径一致，避免把 AI 承诺误标为用户侧）。
    """
    t = (content or "").strip()
    if kind == "cue":
        return "user"
    if t.startswith(_USER_SIDE_PREFIXES):
        return "user"
    if t.startswith(_SELF_SIDE_PREFIXES):
        return "self"
    if any(p in t for p in _USER_SIDE_INFIX):
        return "user"
    if any(p in t for p in ("我承诺", "我答应", "我保证")):
        return "self"
    return "user"


def _loads_intent_meta(s: str) -> dict:
    """cue_terms_json → 元数据 dict；旧 list 格式 / 解析失败 → {}（fail-open）。"""
    try:
        v = json.loads(s or "")
    except Exception:
        return {}
    return v if isinstance(v, dict) else {}


def get_intent_side(row) -> str:
    """读出一条 intent 的 side：优先元数据落库值，旧行回退按 content/kind 现算。"""
    meta = _loads_intent_meta(getattr(row, "cue_terms_json", "") or "")
    side = meta.get("side")
    if side in ("self", "user"):
        return side
    return classify_intent_side(
        getattr(row, "content", "") or "", getattr(row, "kind", "promise") or "promise"
    )


# ───────────────────────── ③ 近重复 intent 合并（写入期，保守阈值）─────────────────────────
def normalize_intent_text(text: str) -> str:
    """归一化 intent 文本：去空白/标点、小写，供相似度比较。"""
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】~～\-—]", "", (text or "").lower())


def similar_intent_text(a: str, b: str, threshold: float = SIMILAR_INTENT_THRESHOLD) -> bool:
    """两段 intent 文本是否高度相近（同义重复）。纯函数、零 IO。

    先比归一化全等；再做「长度接近的子串包含」与 SequenceMatcher 比值（阈值默认 0.95，
    只合并近乎逐字重复者，不吞并"带你去吃火锅"/"下周带你去吃火锅"这类不同粒度）。
    """
    na, nb = normalize_intent_text(a), normalize_intent_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        if min(len(na), len(nb)) / max(len(na), len(nb)) >= threshold:
            return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def fired_idempotency_key(intent_id: int) -> str:
    """③ 同一 intent 提起幂等键（防重复落地；随主动消息 extra_meta 留痕）。"""
    return f"{IDEMPOTENT_FIRED_PREFIX}:{int(intent_id)}"


def has_proactive_opening(text: str) -> bool:
    """③ 字符串级检查：文本是否以「我记得你之前说过 / 你之前不是说」这类同款开场起头。"""
    return any(m in (text or "") for m in _PROACTIVE_OPENING_MARKERS)


def has_forbidden_present_tense(text: str) -> bool:
    """② 渲染闸门：禁止「现在时间也差不多了」这类当下时态断言。"""
    return any(m in (text or "") for m in _PRESENT_TENSE_ASSERTIONS)


# ───────────────────────── 写入（extractor 便车调用，幂等）─────────────────────────
async def _find_near_duplicate(
    db, *, character_id: int, content: str, due_start: datetime | None, due_end: datetime | None,
    side: str = "user",
) -> int | None:
    """③ 同角色 + 同 due 窗口 + 语义高度相近的 pending promise → 返回既有 id（合并）。

    ① 口径纠正：若既有行被判为 user 侧、而本次新内容明确是 self 侧，则把既有行的
    side 元数据升级为 self（只改标记，渲染即回到自述口径）。
    """
    rows = (await db.execute(
        select(ProspectiveIntent).where(
            ProspectiveIntent.character_id == character_id,
            ProspectiveIntent.kind == "promise",
            ProspectiveIntent.status == "pending",
            ProspectiveIntent.due_start == due_start,
            ProspectiveIntent.due_end == due_end,
        )
    )).scalars().all()
    for r in rows:
        if not similar_intent_text(r.content, content):
            continue
        if side == "self" and get_intent_side(r) != "self":
            meta = _loads_intent_meta(r.cue_terms_json)
            meta["side"] = "self"
            r.cue_terms_json = json.dumps(meta, ensure_ascii=False)
            db.add(r)
            await db.commit()
        return r.id
    return None


async def upsert_intent(
    *, user_id: int, character_id: int, content: str, kind: str = "promise",
    cue_terms: list[str] | None = None, due_start: datetime | None = None,
    due_end: datetime | None = None, source_message_id: int | None = None,
    chat_session_id: int | None = None,
    confidence: str = "medium", side: str | None = None,
) -> int | None:
    """落一条前瞻意图。同一 source_message_id 已存在 → 幂等跳过，返回既有 id。

    保守原则（宁漏不误）：content 为空 / promise 缺时间且无线索 / cue 缺线索 → 不写。

    置信（confidence，2026-09-07 放宽口径引入）：写入 cue_terms_json 的 dict 包装
    （`{"confidence": "low|medium|high", "terms": [...]}`）。旧 list 格式仍可被
    ``_loads_cue_terms`` 解析（match_cue_intents 向后兼容）。

    ① 主体口径（2026-09-13）：side 缺省按 content/kind 现算，与 confidence/terms 一起写进
    cue_terms_json 元数据（`"side": "self"|"user"`，只加标记不改语义、零迁移）；显式传 side
    可覆盖。渲染侧是否按 side 分流由 promise_self_side_split 开关决定（见 run_prospective_due）。

    ③ 去重（2026-09-13）：同角色 + 同 due 窗口 + 近逐字重复的 pending promise → 复用既有行。
    """
    content = (content or "").strip()
    if not content or kind not in ("promise", "cue"):
        return None
    cues = [c.strip() for c in (cue_terms or []) if c and len(c.strip()) >= 2][:6]
    if kind == "cue" and not cues:
        return None
    if kind == "promise" and due_end is None and not cues:
        # 既无时间窗又无线索的「承诺」无法可靠兑现，宁可不写
        return None
    if confidence not in ("low", "medium", "high"):
        confidence = "medium"
    if side not in ("self", "user"):
        side = classify_intent_side(content, kind)

    async with async_session_factory() as db:
        if source_message_id is not None:
            existed = (await db.execute(
                select(ProspectiveIntent).where(
                    ProspectiveIntent.source_message_id == source_message_id,
                    ProspectiveIntent.character_id == character_id,
                    ProspectiveIntent.content == content,
                )
            )).scalar_one_or_none()
            if existed is not None:
                return existed.id
        if kind == "promise":
            dup_id = await _find_near_duplicate(
                db, character_id=character_id, content=content,
                due_start=due_start, due_end=due_end, side=side,
            )
            if dup_id is not None:
                return dup_id
        cue_payload = json.dumps(
            {"confidence": confidence, "terms": cues, "side": side}, ensure_ascii=False
        )
        row = ProspectiveIntent(
            user_id=user_id, character_id=character_id, content=content[:500],
            kind=kind, cue_terms_json=cue_payload,
            due_start=due_start, due_end=due_end, status="pending",
            source_message_id=source_message_id, chat_session_id=chat_session_id,
        )
        db.add(row)
        await db.commit()
        return row.id


# ───────────────────────── 状态流转（纯状态，留痕不物理删）─────────────────────────
async def _set_status(ids: list[int], status: str, *, discharge: bool = False) -> None:
    if not ids:
        return
    async with async_session_factory() as db:
        rows = (await db.execute(select(ProspectiveIntent).where(ProspectiveIntent.id.in_(ids)))).scalars().all()
        for r in rows:
            r.status = status
            if discharge:
                r.discharged_at = _now_naive()
            db.add(r)
        await db.commit()


async def expire_overdue() -> int:
    """due_end + 7 天仍 pending → expired（周期任务调用，幂等）。"""
    cutoff = _now_naive() - timedelta(days=GRACE_DAYS)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status == "pending",
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_not(None),
                ProspectiveIntent.due_end < cutoff,
            )
        )).scalars().all()
        for r in rows:
            r.status = "expired"
            db.add(r)
        await db.commit()
        return len(rows)


async def mark_stale_overdue(*, window_hours: float | None = None) -> int:
    """② 超窗未兑现 → stale（留痕不删，仍可检索/回忆，但不进主动提起）。

    判定：status=pending、kind=promise、due_end 非空且 `due_end < now - N 小时`
    （N 默认 ``PROMISE_ACTIVE_WINDOW_HOURS``）。周期任务调用，幂等。
    只动 pending：discharged/cancelled/expired/matched/cue 一律不受影响。
    """
    hours = PROMISE_ACTIVE_WINDOW_HOURS if window_hours is None else float(window_hours)
    cutoff = _now_naive() - timedelta(hours=hours)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status == "pending",
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_not(None),
                ProspectiveIntent.due_end < cutoff,
            )
        )).scalars().all()
        for r in rows:
            r.status = "stale"
            db.add(r)
        await db.commit()
        return len(rows)


async def cancel_by_content(character_id: int, text: str) -> int:
    """用户说「算了/不用了」且文本高相似命中某 pending 意图 → cancelled（保守，需明显指向）。"""
    text = (text or "").strip()
    if len(text) < 2:
        return 0
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.character_id == character_id,
                ProspectiveIntent.status == "pending",
            )
        )).scalars().all()
        hit = [r for r in rows if (r.content or "")[:10] in text or text in (r.content or "")]
        for r in hit:
            r.status = "cancelled"
            db.add(r)
        await db.commit()
        return len(hit)


# ───────────────────────── 时间型：Scheduler 采集到期承诺 ─────────────────────────
async def collect_due_promises() -> list[dict]:
    """返回「可自然提起」的 pending promise 候选，供 TriggerSource。

    ② 时效收窄（2026-09-13）：先 expire（7 天宽限）/ mark_stale（N 小时超窗）清理，
    再只捞 `now - N 小时 <= due_end <= now` 的窗口内候选——迟到一天不再被翻出来提。
    """
    await expire_overdue()          # 先清 7 天宽限外（保持既有 expired 语义）
    await mark_stale_overdue()      # 再把 N 小时超窗置 stale（不进主动提起）
    now = _now_naive()
    window_start = now - timedelta(hours=PROMISE_ACTIVE_WINDOW_HOURS)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status == "pending",
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_not(None),
                ProspectiveIntent.due_end <= now,
                ProspectiveIntent.due_end >= window_start,
            ).order_by(ProspectiveIntent.due_end.asc())
        )).scalars().all()
        candidates = []
        for r in rows:
            candidates.append({
                "pis_id": r.id, "user_id": r.user_id, "character_id": r.character_id,
                "content": r.content, "due_end": r.due_end,
                "side": get_intent_side(r),  # ① 主体口径（self|user）
                "session_id": r.chat_session_id,  # arbiter 发送/追踪统一用 session_id 键
                "chat_session_id": r.chat_session_id,
            })
        return candidates


async def mark_discharged_many(ids: list[int]) -> None:
    await _set_status(ids, "discharged", discharge=True)


# ───────────────────────── 线索型：聊天确定性匹配（零 LLM）─────────────────────────
def _cue_hit(cue_terms: list[str], user_text: str) -> bool:
    t = (user_text or "").lower()
    return any(c.lower() in t for c in cue_terms if len(c) >= 2)


async def match_cue_intents(character_id: int, user_text: str) -> list[ProspectiveIntent]:
    """当前用户文本命中某 pending cue 的 cue_terms → 返回命中行（确定性子串，正则可二期）。

    一期：只用于「本轮注入提醒块」，不主动发消息；命中后置 matched（不 discharged，
    因为线索可能被多次提及，是否兑现由对话推进决定，到期/取消再终态）。
    """
    if not (user_text or "").strip():
        return []
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.character_id == character_id,
                ProspectiveIntent.status.in_(["pending", "matched"]),
                ProspectiveIntent.kind == "cue",
            )
        )).scalars().all()
        hit = [r for r in rows if _cue_hit(_loads_cue_terms(r.cue_terms_json), user_text)]
        changed = False
        for r in hit:
            if r.status == "pending":
                r.status = "matched"
                db.add(r)
                changed = True
        if changed:
            await db.commit()
        return hit


# ───────────────────────── 时间型：到期承诺自然提起（arbiter._execute 调用）─────────────────────────
def _side_split_on() -> bool:
    """① 渲染分流开关：沿用既有 promise_self_side_split（默认关＝逐字节回退当前口径）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("promise_self_side_split", False))
    except Exception:
        return False


def _is_cross_day(due_end: datetime | None, now_naive: datetime | None = None) -> bool:
    """② due_end 是否已跨天（与 collect 同口径：库内 naive 日期直接比较）。"""
    if due_end is None:
        return False
    return due_end.date() < (now_naive or _now_naive()).date()


def _build_prospective_hint_legacy(char_name: str, content: str) -> str:
    """① flag 关时的旧话术（与 2026-09-04 上线版本逐字节一致，保证可回退）。"""
    return (
        f"你是{char_name}。你和用户之前有过一个约定/用户曾提到过：「{content}」。"
        "现在到了合适的时间，请用自己的语气自然提起这件事（可以说'我记得你之前说过…'，"
        "但不要生硬念稿、不要提'AI'、不要加引号标注），并顺势把话题抛给用户，不要替用户做决定。"
    )


def _build_prospective_hint(
    char_name: str, content: str, side: str, due_end: datetime | None = None,
    *, recent_opener: bool = False, now_naive: datetime | None = None,
) -> str:
    """① 按 side 分流 + ② 跨天时间锚 + ③ 冷却期换开场 的渲染提示词（纯函数）。"""
    if side == "self":
        head = (
            f"你是{char_name}。这件事是**你自己**之前说要做的：「{content}」。"
            "现在到了合适的时间，请用你自己的口吻自然提起——自述口径（例如"
            "「我把晚饭的事做好」「昨天说我来做晚饭，这就去弄」），把它当作你自己要去做的事。"
            "禁止使用「你之前说过…」「你之前不是说…」这类把责任推给用户的说法，"
            "禁止把它说成是用户答应的，不要替用户做决定。"
        )
    else:
        head = (
            f"你是{char_name}。用户之前提过/答应过：「{content}」。"
            "现在到了合适的时间，请用自己的语气自然提起（可以说'我记得你之前说过…'），"
            "并顺势把话题抛给用户，不要替用户做决定。"
        )
    parts = [head]
    if _is_cross_day(due_end, now_naive):
        parts.append(
            "注意：这件事约定的是" + due_end.strftime("%Y-%m-%d") + "，已经跨天。"
            "提起时必须带明确的时间锚（例如「昨天你说过…」「之前你提到…」），"
            "严禁使用「现在时间也差不多了」「现在已经到时间了」这类当下时态断言，"
            "也不得暗示现在正是那个时候。"
        )
    if recent_opener:
        parts.append(
            "另外：你最近一小时内已经用过「我记得你之前说过 / 你之前不是说」这类开场，"
            "这次必须换一种说法，不要再重复同款开场。"
        )
    parts.append("不要生硬念稿、不要提'AI'、不要加引号标注。")
    return "".join(parts)


async def claim_intent_for_fire(intent_id: int) -> bool:
    """③ 幂等认领：pending → discharged（原子条件更新，rowcount==1 才算本次认领成功）。

    同一 intent 的并发/重复触发只有一次能认领成功 → 只落地一条主动消息（幂等键语义）。
    生成/发送失败由调用方 ``_revert_to_pending`` 回滚，失败仍可下轮重试。
    """
    async with async_session_factory() as db:
        res = await db.execute(
            update(ProspectiveIntent)
            .where(ProspectiveIntent.id == intent_id, ProspectiveIntent.status == "pending")
            .values(status="discharged", discharged_at=_now_naive())
        )
        await db.commit()
        return bool(getattr(res, "rowcount", 0))


async def _revert_to_pending(intent_id: int) -> None:
    """认领后生成/发送失败 → 回滚为 pending（只回滚仍处于 discharged 的行）。"""
    async with async_session_factory() as db:
        await db.execute(
            update(ProspectiveIntent)
            .where(ProspectiveIntent.id == intent_id, ProspectiveIntent.status == "discharged")
            .values(status="pending", discharged_at=None)
        )
        await db.commit()


async def recent_opener_exists(character_id: int, *, now: datetime | None = None) -> bool:
    """③ 同款开场冷却：同角色最近 OPENING_COOLDOWN_HOURS 小时内是否已发过同款开场主动消息。"""
    cutoff = (now or _now_naive()) - timedelta(hours=OPENING_COOLDOWN_HOURS)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProactiveMessageLog.content).where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.created_at >= cutoff,
            )
        )).scalars().all()
    return any(has_proactive_opening(c) for c in rows)


async def run_prospective_due(candidate: dict) -> bool:
    """把一条到期承诺组织成一次自然主动消息；成功发送后 discharged（一次性）。

    发送走既有 engine.send_to_session（与 run_unfinished_topic 同构），
    走正常 run_tick 的角色分组/优先级/免打扰/额度，不另开旁路通道。

    ③ 幂等（2026-09-13）：进函数先原子认领 pending→discharged，拿不到＝已提过 → 跳过；
    生成/发送失败回滚 pending，下轮仍可重试（失败不算已提）。
    ① 渲染：promise_self_side_split 开 → 按 side 分流 + 跨天时间锚；关 → 旧话术逐字节回退。
    ②③ 输出闸门：当下时态断言 / 冷却期内同款开场 → 约束重生成一次，仍违规则跳过。
    """
    char_id = candidate["character_id"]
    user_id = candidate["user_id"]
    session_id = candidate.get("session_id") or candidate.get("chat_session_id")
    content = (candidate.get("content") or "").strip()
    pis_id = candidate.get("pis_id")
    if pis_id is None or not session_id:
        return False
    intent_id = int(pis_id)
    if not await claim_intent_for_fire(intent_id):
        _logger.info("Prospective due skipped (already fired) pis=%s", pis_id)
        return False
    try:
        from app.agent.llm_client import chat_completion
        from app.models.character import AICharacter
        from app.scheduling import scheduler as engine
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
        char_name = char.name if char else "我"
        side = candidate.get("side") if candidate.get("side") in ("self", "user") else None
        if side is None:
            async with async_session_factory() as db:
                row = await db.get(ProspectiveIntent, intent_id)
            side = get_intent_side(row) if row is not None else classify_intent_side(content)
        # ③ 开场冷却独立于 ① 的 side 开关（模板复读治理必须始终生效）；
        # ① side 分流才由 promise_self_side_split 门控（关＝旧话术逐字节回退）。
        recent_opener = await recent_opener_exists(char_id)
        if _side_split_on():
            hint = _build_prospective_hint(
                char_name, content, side, candidate.get("due_end"), recent_opener=recent_opener,
            )
        else:
            hint = _build_prospective_hint_legacy(char_name, content)
        _sys = {"role": "system", "content": "直接输出内容，不要加引号和标注。"}
        msg = (await chat_completion(
            messages=[_sys, {"role": "user", "content": hint}],
            temperature=0.85, max_tokens=256, task="message",
        ) or "").strip().strip('"').strip("'")
        if has_forbidden_present_tense(msg) or (recent_opener and has_proactive_opening(msg)):
            constrained = hint + (
                "\n严禁使用「现在时间也差不多了」这类当下时态断言；"
                "严禁重复「我记得你之前说过 / 你之前不是说」这类开场。"
            )
            msg = (await chat_completion(
                messages=[_sys, {"role": "user", "content": constrained}],
                temperature=0.7, max_tokens=256, task="message",
            ) or "").strip().strip('"').strip("'")
            if has_forbidden_present_tense(msg) or (recent_opener and has_proactive_opening(msg)):
                _logger.info("Prospective due gated (templated/present-tense) pis=%s", pis_id)
                await _revert_to_pending(intent_id)
                return False
        if len(msg) < 2:
            await _revert_to_pending(intent_id)
            return False
        await engine.send_to_session(
            session_id, char_id, user_id, msg, message_type="prospective_intent",
            extra_meta=json.dumps(
                {"pis_id": intent_id, "idem": fired_idempotency_key(intent_id)},
                ensure_ascii=False,
            ),
        )
        _logger.info("Prospective due sent char=%d pis=%s side=%s", char_id, pis_id, side)
        return True
    except Exception as e:
        await _revert_to_pending(intent_id)
        _logger.warning("prospective_due failed pis=%s: %s", pis_id, e)
        return False  # 回滚 pending，等下个 tick（配合 collect 的到期顺序自然重试）
