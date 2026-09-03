# -*- coding: utf-8 -*-
"""Ariadne 模块G：前瞻意图（promise/cue）采集、状态机、到期采集与兑现（2026-09-04）。

- 不进 memories，不参与检索/衰减/查重/#70 supersede；
- promise：有 due 时间窗，Scheduler 周期扫 due_end<=now 的 pending；
- cue：due 为空，聊天时由 context 分区用 cue_terms 做确定性匹配（在线零 LLM）；
- 兑现即焚 discharged（一次性）；due_end+7 天未兑现 → expired（留痕不删）；用户取消 → cancelled；
- 时间型触发经 3.10 TriggerSource（scheduling/sources/prospective_intent.py）接入 arbiter，
  本模块放真实逻辑（薄 source 只适配，对齐 unfinished_topic 的分工）。

TODO（Ariadne 模块G 一期裁剪，2026-09-04 拍板，不实现）：
- kind=wish（无条件心愿）：一期裁掉，只做 promise/cue；
- 线索型主动推送（到期 cue 主动发消息）：一期只注入本轮提醒块，主动推送二期；
- cue 匹配一期之子串，正则/语义匹配二期（可复用 LorebookEntry 的 is_regex 经验）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import ProspectiveIntent
from app.utils.logger import get_logger

_logger = get_logger("scheduling.prospective_intent")

GRACE_DAYS = 7              # 到期宽限：超过 due_end 7 天仍未兑现 → expired
PROMISE_SCAN_LOOKAHEAD = timedelta(days=3650)  # 防御性上限（无 due_end 的 cue 不被时间扫描捞到）


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _loads(s, default):
    try:
        v = json.loads(s or "")
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


# ───────────────────────── 写入（extractor 便车调用，幂等）─────────────────────────
async def upsert_intent(
    *, user_id: int, character_id: int, content: str, kind: str = "promise",
    cue_terms: list[str] | None = None, due_start: datetime | None = None,
    due_end: datetime | None = None, source_message_id: int | None = None,
    chat_session_id: int | None = None,
) -> int | None:
    """落一条前瞻意图。同一 source_message_id 已存在 → 幂等跳过，返回既有 id。

    保守原则（宁漏不误）：content 为空 / promise 缺时间且无线索 / cue 缺线索 → 不写。
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
        row = ProspectiveIntent(
            user_id=user_id, character_id=character_id, content=content[:500],
            kind=kind, cue_terms_json=json.dumps(cues, ensure_ascii=False),
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
    """返回进入时间窗（due_end<=now 且未到 expired 宽限）的 pending promise 候选，供 TriggerSource。"""
    await expire_overdue()  # 顺手清过期
    now = _now_naive()
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ProspectiveIntent).where(
                ProspectiveIntent.status == "pending",
                ProspectiveIntent.kind == "promise",
                ProspectiveIntent.due_end.is_not(None),
                ProspectiveIntent.due_end <= now,
            ).order_by(ProspectiveIntent.due_end.asc())
        )).scalars().all()
        candidates = []
        for r in rows:
            candidates.append({
                "pis_id": r.id, "user_id": r.user_id, "character_id": r.character_id,
                "content": r.content, "due_end": r.due_end,
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
        hit = [r for r in rows if _cue_hit(_loads(r.cue_terms_json, []), user_text)]
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
async def run_prospective_due(candidate: dict) -> bool:
    """把一条到期承诺组织成一次自然主动消息；成功发送后 discharged（一次性）。

    发送走既有 engine.send_to_session（与 run_unfinished_topic 同构），
    走正常 run_tick 的角色分组/优先级/免打扰/额度，不另开旁路通道；失败不 discharged，下轮重试。
    """
    char_id = candidate["character_id"]
    user_id = candidate["user_id"]
    session_id = candidate.get("session_id") or candidate.get("chat_session_id")
    content = (candidate.get("content") or "").strip()
    try:
        from app.agent.llm_client import chat_completion
        from app.models.character import AICharacter
        from app.scheduling import scheduler as engine
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
        char_name = char.name if char else "我"
        hint = (
            f"你是{char_name}。你和用户之前有过一个约定/用户曾提到过：「{content}」。"
            "现在到了合适的时间，请用自己的语气自然提起这件事（可以说'我记得你之前说过…'，"
            "但不要生硬念稿、不要提'AI'、不要加引号标注），并顺势把话题抛给用户，不要替用户做决定。"
        )
        msg = await chat_completion(
            messages=[
                {"role": "system", "content": "直接输出内容，不要加引号和标注。"},
                {"role": "user", "content": hint},
            ],
            temperature=0.85, max_tokens=256, task="message",
        )
        msg = (msg or "").strip().strip('"').strip("'")
        if len(msg) < 2 or not session_id:
            return False
        await engine.send_to_session(
            session_id, char_id, user_id, msg, message_type="prospective_intent",
        )
        _logger.info("Prospective due sent char=%d pis=%s", char_id, candidate.get("pis_id"))
        await mark_discharged_many([candidate["pis_id"]])
        return True
    except Exception as e:
        _logger.warning("prospective_due failed pis=%s: %s", candidate.get("pis_id"), e)
        return False  # 不 discharged，等下个 tick（配合 collect 的到期顺序自然重试）
