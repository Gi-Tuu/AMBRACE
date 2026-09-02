"""主动到期复习（P1，2026-08-05）：到期记忆 → 角色自然提起 → 用户回应判定强化。

数据流：
- collect_review_events：arbiter tick 扫描 next_review_at 到期且 importance>=40 的记忆（每角色 1 条候选）
- run_memory_review：生成并发送自然提及（LLM 1 次），记录 ProactiveMessageLog(extra_meta.memory_id)，
  并将 next_review_at 推迟 REVIEW_RETRY_DAYS（等待用户回应窗口）
- maybe_review_success：用户回复时调用，24h 内有 review 记录且回复与记忆内容弱相关 → 强化 S×2 并重排
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.models.character import AICharacter
from app.memory.flags import memory_v2_enabled as _memory_v2_enabled
from app.models.character import ProactiveMessageLog
from app.memory.constants import (
    REVIEW_MIN_IMPORTANCE, REVIEW_MAX_PER_DAY, REVIEW_RETRY_DAYS, REVIEW_SUCCESS_WINDOW_HOURS,
    REVIEW_MIN_INTERVAL_MINUTES, REINFORCE_FACTOR_RETRIEVE,
)
from app.utils.logger import get_logger
from app.utils.dnd import user_in_dnd_period as _user_in_dnd_period

_logger = get_logger("scheduler.memory_review")

REVIEW_TYPE = "memory_review"
_TYPE_LABEL = {"user_info": "关于你的事", "preference": "你的喜好", "event": "发生过的事", "insight": "我的一些想法"}


async def collect_review_events() -> list[dict]:
    """扫描到期记忆 → 每角色 1 条候选（arbiter 事件源，priority=1）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Memory.character_id, Memory.user_id, Memory.id)
            .where(
                Memory.is_archived == False,
                Memory.is_pinned == False,
                Memory.is_locked == False,
                Memory.importance >= REVIEW_MIN_IMPORTANCE,
                Memory.next_review_at.is_not(None),
                Memory.next_review_at <= now,
                _active_status_clause(),
            )
            .order_by(Memory.next_review_at.asc())
        )).all()
    # 每角色只取最早到期的一条
    per_char: dict[int, tuple] = {}
    for cid, uid, mid in rows:
        if uid and cid not in per_char:
            per_char[cid] = (cid, uid, mid)
    # 审计 P2-04：无活跃会话的角色不产生复习候选（避免每 tick 空转出候选 + 写 rejected 日志）
    if per_char:
        try:
            from app.models.chat import ChatSession
            async with async_session_factory() as db:
                _sess = (await db.execute(
                    select(ChatSession.character_id).where(
                        ChatSession.character_id.in_(list(per_char.keys())),
                        ChatSession.is_active == True,  # noqa: E712
                    ).distinct()
                )).scalars().all()
            _active_chars = set(_sess)
            per_char = {k: v for k, v in per_char.items() if k in _active_chars}
        except Exception as e:
            _logger.warning("review active-session filter failed: %s", e)
    return [
        {"type": REVIEW_TYPE, "priority": 1, "candidate": {
            "character_id": cid, "user_id": uid, "memory_id": mid,
        }}
        for cid, uid, mid in per_char.values()
    ]


async def _daily_count(db, character_id: int) -> int:
    cn_tz = timezone(timedelta(hours=8))
    today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    return (await db.execute(
        select(func.count(ProactiveMessageLog.id)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == REVIEW_TYPE,
            ProactiveMessageLog.created_at >= today_start,
        )
    )).scalar() or 0


def _review_daily_cap() -> int:
    """M1-S7（2026-08-31）：复习日额度 flag 化——review_daily_plus 开=4（默认），关=回退 3。

    纯函数便于单测；90 分钟最小间隔不变（防轰炸靠间隔而非额度）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return REVIEW_MAX_PER_DAY + 1 if AGENT_FLAGS.get("review_daily_plus", True) else REVIEW_MAX_PER_DAY
    except Exception:
        return REVIEW_MAX_PER_DAY


# M1-S7：敷衍回复词表——极短纯语气词/纯标点不算"接住"（其余实质回复 24h 内均算复习成功）
_NON_SUBSTANTIVE_REPLIES = {
    "哦", "嗯", "啊", "呃", "额", "噢", "哦哦", "嗯嗯", "噢噢", "呃呃",
    "呵呵", "哈哈", "哦了", "嗯了", "行吧", "哦呀",
}


def _is_substantive_reply(text: str) -> bool:
    """M1-S7（纯函数）：实质回复判定——去标点/空白后 ≥2 字且非敷衍词表即算（治 P-E1 误判失败）。"""
    import re as _re
    t = _re.sub(r"[\s。，！？…~、；：,.!?;:()（）\"'“”‘’【】\[\]…]+", "", text or "")
    if not t or len(t) < 2:
        return False
    return t not in _NON_SUBSTANTIVE_REPLIES


async def _last_review_at(db, character_id: int):
    """该角色最近一条复习消息发送时间（最小间隔保护用）。"""
    return (await db.execute(
        select(func.max(ProactiveMessageLog.created_at)).where(
            ProactiveMessageLog.character_id == character_id,
            ProactiveMessageLog.message_type == REVIEW_TYPE,
        )
    )).scalar_one_or_none()


async def run_memory_review(char_id: int, user_id: int, memory_id: int) -> bool:
    """执行一次主动复习：限额/免打扰/会话检查 → 先占位重排（防失败重试烧 token）→ LLM 生成 → 发送 → 记录。"""
    from app.scheduling.scheduler import send_to_session
    from app.application.chat_service import get_latest_session_id
    from app.scheduling.triggers import memory_review_enabled
    # 0) 主动互动主开关 / 记忆复习子开关 任一关闭则不发送
    if not await memory_review_enabled(char_id):
        return False
    # 1) 无活跃会话直接放弃（不生成、不占位：下个 tick 会再来，但零成本）
    session_id = await get_latest_session_id(user_id, char_id)
    if session_id is None:
        return False

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        if await _daily_count(db, char_id) >= _review_daily_cap():
            _logger.info("Memory review char=%d skipped: daily limit", char_id)
            return False
        last = await _last_review_at(db, char_id)
        if last is not None:
            last = last.replace(tzinfo=None) if last.tzinfo else last
            if now_naive - last < timedelta(minutes=REVIEW_MIN_INTERVAL_MINUTES):
                return False
        if await _user_in_dnd_period(db, user_id):
            return False
        mem = await db.get(Memory, memory_id)
        if mem is None or mem.is_archived or mem.is_pinned or mem.is_locked:
            return False
        char = await db.get(AICharacter, char_id)
        content_src = mem.content
        mem_type = mem.memory_type
        # 先占位：无论后续 LLM/发送是否成功，3 天后才再试（防止无会话/限流导致每 30s 烧一次 LLM）
        mem.next_review_at = now_naive + timedelta(days=REVIEW_RETRY_DAYS)
        await db.commit()

    char_name = char.name if char else "我"
    personality = (char.personality or "友善")[:100] if char else "友善"
    # 注入最近聊天上下文：让记忆自然融入当前话题，避免生硬转折割裂对话
    recent_context = ""
    try:
        from app.scheduling.triggers import get_last_messages
        recent_context = (await get_last_messages(session_id))[:500]
    except Exception:
        pass
    try:
        from app.agent.llm_client import chat_completion
        from app.agent.user_profile import build_role_prompt_block
        identity = ""
        try:
            identity = await build_role_prompt_block(char, user_id)
        except Exception:
            identity = f"你是{char_name}，性格{personality}。"
        # 认知循环 v2.1：主动通道 persona 统一层（关系温度/剧情状态/进行中话题；开关关=空串）
        active_persona = ""
        try:
            from app.agent.persona import build_active_channel_persona
            active_persona = await build_active_channel_persona(char_id, user_id)
        except Exception:
            active_persona = ""
        persona_block = f"{active_persona}\n" if active_persona else ""
        label = _TYPE_LABEL.get(mem_type, "一件事")
        context_line = f"\n你们最近在聊：\n{recent_context}\n" if recent_context else ""
        hint = (
            f"{identity}\n"
            f"{persona_block}"
            f"你想起了{label}：{content_src[:120]}{context_line}"
            "自然地跟用户提一句，像老朋友聊天一样（1-2 句话，口语化），"
            "尽量顺着最近的聊天话题自然带出，不要生硬转折，不要提'记忆''复习''想起以前记录'这类字眼。"
            "必须全程以第一人称'我'说话（你=角色本人），不要以旁观者视角提及你自己的名字或'某人'这类第三人称。"
        )
        from app.agent.llm_client import load_character_reasoning_level
        _rl = await load_character_reasoning_level(char_id)
        _msgs = [
            {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
            {"role": "user", "content": hint},
        ]
        # D2-D（2026-08-18）：记忆复习关闭深度思考——统一走挡位 1/0 的 prompt 引导分支
        # （挡位 1 保留「先在心里简短想一下」引导；_review_reasoning 恒为空串，_review_extra 仅保留 memory_id）
        _review_reasoning = ""
        if _rl == 1:
            _msgs[0] = {"role": "system", "content": "先在心里简短想一下怎么说合适，然后直接输出要说的话，不要加引号和标注。"}
        text = await chat_completion(messages=_msgs, temperature=0.85, max_tokens=256,
                                     task="review", user_id=user_id)
        text = (text or "").strip().strip('"').strip("'")
        if not text or len(text) < 2:
            return False
    except Exception as e:
        _logger.warning("Memory review LLM failed char=%d: %s", char_id, e)
        return False

    # 发送（send_to_session 内部已落库 ChatMessage + ProactiveMessageLog[含 extra_meta] + WS 推送）
    _review_extra = {"memory_id": memory_id}
    if _review_reasoning:
        _review_extra["reasoning"] = _review_reasoning
    await send_to_session(
        session_id=session_id, character_id=char_id, user_id=user_id,
        content=text[:500], message_type=REVIEW_TYPE,
        extra_meta=json.dumps(_review_extra, ensure_ascii=False),
    )
    _logger.info("Memory review sent char=%d mem=%d", char_id, memory_id)
    return True


async def maybe_review_success(user_id: int, character_id: int, user_content: str) -> int:
    """用户回复时调用：24h 内有 review 记录且回复"接住"了复习 → 强化 S×4/3（成功复习）。

    返回强化条数。M1-S7 判定（2026-08-31）：字符相似 >0.15 照旧命中；
    相似未命中但属实质回复（_is_substantive_reply，排除哦/嗯等敷衍）也算成功——
    避免"用户明明回应了却被判失败、3 天后才重试"。
    """
    from difflib import SequenceMatcher
    if not user_content or not user_content.strip():
        return 0
    window_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=REVIEW_SUCCESS_WINDOW_HOURS)
    async with async_session_factory() as db:
        logs = (await db.execute(
            select(ProactiveMessageLog)
            .where(
                ProactiveMessageLog.character_id == character_id,
                ProactiveMessageLog.message_type == REVIEW_TYPE,
                ProactiveMessageLog.created_at >= window_start,
            )
            .order_by(ProactiveMessageLog.id.desc())
        )).scalars().all()
        if not logs:
            return 0
        # 只处理最新一条 review（避免同一记忆反复强化）
        log = logs[0]
        try:
            meta = json.loads(log.extra_meta or "{}")
        except Exception:
            meta = {}
        memory_id = meta.get("memory_id")
        if not memory_id:
            return 0
        mem = await db.get(Memory, memory_id)
        if mem is None or mem.is_archived or mem.is_pinned or mem.is_locked:
            return 0
        a = (user_content or "").strip()[:100]
        b = (mem.content or "").strip()[:100]
        if len(a) < 2 or len(b) < 2:
            return 0
        # M1-S7（2026-08-31）：成功判定放宽——相似命中照旧；非相似但属实质回复（非敷衍）也算"接住"
        #（治"用户明明回应了却被判失败、3 天后才重试"的 P-E1；敷衍词表极短无内容）
        if SequenceMatcher(None, a, b).ratio() < 0.15 and not _is_substantive_reply(a):
            return 0
        mem_id_for_reinforce = mem.id
    from app.memory.service import reinforce_memories
    await reinforce_memories([mem_id_for_reinforce], factor=REINFORCE_FACTOR_RETRIEVE * 4 / 3)
    _logger.info("Memory review success: char=%d mem=%d user replied related", character_id, mem_id_for_reinforce)
    return 1


# ── 记忆架构 v2.1 Phase 4b：情境驱动复习（与时间驱动并存）──
# 入口：chat_service 感知 deep/emotion 或命中进行中目标 → queue_contextual_review_for
# 执行：入队后延迟 _CONTEXTUAL_DELAY_SECONDS，由 arbiter collect_contextual_events 消费，
#       复用 run_memory_review（限额/间隔/免打扰/开关复检；用户活跃时 arbiter 拦截等待）
_contextual_pending: dict[int, dict] = {}  # char_id -> {user_id, memory_id, due_at}
_CONTEXTUAL_DELAY_SECONDS = 120


async def _msg_hits_goal(character_id: int, user_id: int, user_msg: str) -> bool:
    """用户消息与进行中目标/未完成话题重叠（话题命中）"""
    try:
        from app.models.memory import ConversationTopic
        from app.agent.topic_tracker import _overlap
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ConversationTopic).where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
            )).scalars().all()
        return any(r.topic and _overlap(r.topic, user_msg) for r in rows)
    except Exception:
        return False


async def _pick_contextual_memory(character_id: int, user_id: int, user_msg: str) -> int | None:
    """情境复习候选记忆：优先进行中目标/未完成话题关联记忆，其次意义/情绪/关系重要记忆。"""
    try:
        from app.models.memory import ConversationTopic
        from app.models.memory import Memory
        from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory)
                .where(
                    Memory.character_id == character_id,
                    Memory.is_archived == False,
                    Memory.is_pinned == False,
                    Memory.is_locked == False,
                    Memory.importance >= REVIEW_MIN_IMPORTANCE,
                    _active_status_clause(),
                )
                .order_by(Memory.importance.desc(), Memory.id.desc())
                .limit(20)
            )).scalars().all()
            if not rows:
                return None
            topics = (await db.execute(
                select(ConversationTopic).where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
            )).scalars().all()
        from app.agent.topic_tracker import _overlap
        for m in rows:
            if m.why_it_matters is not None:
                return m.id
            if any(t.topic and _overlap(t.topic, m.content or "") for t in topics):
                return m.id
        for m in rows:
            if m.sub_type in ("emotion", "relationship"):
                return m.id
        return rows[0].id
    except Exception:
        return None


async def queue_contextual_review(character_id: int, user_id: int, memory_id: int) -> None:
    """入队情境复习（开关开启 + 未在排队中）；限额/间隔/DND 由 run_memory_review 执行时复检。"""
    try:
        if character_id in _contextual_pending:
            return
        if not await _memory_v2_enabled(character_id):
            return
        import time as _time
        _contextual_pending[character_id] = {
            "user_id": user_id, "memory_id": memory_id,
            "due_at": _time.time() + _CONTEXTUAL_DELAY_SECONDS,
        }
        _logger.info("Contextual review queued char=%d mem=%d", character_id, memory_id)
    except Exception as e:
        _logger.warning("Contextual review queue failed: %s", e)


async def queue_contextual_review_for(
    character_id: int, user_id: int, user_msg: str, perception: dict | None = None,
) -> None:
    """感知情境入口（chat_service 调用）：deep/emotion 意图或命中进行中目标 → 选记忆并入队。"""
    try:
        intent = (perception or {}).get("intent") or ""
        if intent not in ("deep", "emotion"):
            if not await _msg_hits_goal(character_id, user_id, user_msg):
                return
        memory_id = await _pick_contextual_memory(character_id, user_id, user_msg)
        if not memory_id:
            return
        await queue_contextual_review(character_id, user_id, memory_id)
    except Exception as e:
        _logger.warning("Contextual review trigger failed: %s", e)


async def collect_contextual_events() -> list[dict]:
    """arbiter 事件源：到期情境复习候选（priority=2，与状态触发同级）。"""
    import time as _time
    events = []
    for char_id, info in list(_contextual_pending.items()):
        if _time.time() >= info["due_at"]:
            _contextual_pending.pop(char_id, None)
            events.append({
                "type": "memory_review_contextual", "priority": 2,
                "candidate": {
                    "character_id": char_id, "user_id": info["user_id"],
                    "memory_id": info["memory_id"],
                },
            })
    return events
