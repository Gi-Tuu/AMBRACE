from app.utils.timeutil import beijing_day_start_utc as _beijing_day_start_utc
"""AI 间私聊（Phase 1）：同用户两个 AI 角色私下对话（不推送、不打扰）。

数据流：
- collect_ai_social_events：arbiter tick 采样——同用户活跃角色两两配对（不同名、非敌对），
  每日每对 <=2 次、间隔 >=6h，概率门控 → 事件（priority=0.5，低于一切用户相关事件）
- run_ai_social：按轮生成 2-4 句（A→B→A→B），每轮独立 LLM（带前文+双方身份块+用户近况+关系边界），
  首轮防重复（SequenceMatcher）→ 落库 ai_chats（round_seq 排序）
- 只读展示：GET /api/v1/ai-chats（用户隔离）
"""
import random
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlalchemy import select, func, or_

from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.ai_chat import AIChat
from app.models.memory import Memory
from app.utils.logger import get_logger

_logger = get_logger("scheduler.ai_social")

AI_SOCIAL_TYPE = "ai_social"
MAX_PER_DAY = 2  # 每对角色每日事件上限
MIN_INTERVAL_HOURS = 6  # 同对角色两次事件最小间隔
SAMPLE_PROBABILITY = 0.3  # 每次 tick 采样概率（30s tick，限额兜底）
TOPIC_USER_PROB = 0.5  # 聊用户近况概率（用户是共同中心）
HOSTILE_RELATIONS = ("敌对", "仇人", "死对头", "势不两立", "宿敌")


def _hostile(relation_type: str | None) -> bool:
    return bool(relation_type) and any(k in relation_type for k in HOSTILE_RELATIONS)


# 恋爱关系判定：is_partner 标记或关系类型含恋爱关键词（用于 AI 间互动的边界约束）
_PARTNER_KEYWORDS = ("对象", "伴侣", "恋人", "恋爱", "女朋友", "男朋友", "老婆", "老公", "妻子", "丈夫")


def _is_partner_char(char: AICharacter) -> bool:
    if getattr(char, "is_partner", None):
        return True
    rt = char.relation_type or ""
    return any(k in rt for k in _PARTNER_KEYWORDS)


def _pair_cond(a_id: int, b_id: int):
    return or_(
        (AIChat.character_a_id == a_id) & (AIChat.character_b_id == b_id),
        (AIChat.character_a_id == b_id) & (AIChat.character_b_id == a_id),
    )


async def _pair_eligible(user_id: int, a_id: int, b_id: int) -> bool:
    """每对每日 <=2 次、间隔 >=6h"""
    day_start = _beijing_day_start_utc()
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=MIN_INTERVAL_HOURS)
    async with async_session_factory() as db:
        cnt = (await db.execute(
            select(func.count()).where(
                AIChat.user_id == user_id,
                AIChat.round_seq == 0,
                _pair_cond(a_id, b_id),
                AIChat.created_at >= day_start,
            )
        )).scalar() or 0
        if cnt >= MAX_PER_DAY:
            return False
        last = (await db.execute(
            select(func.max(AIChat.created_at)).where(
                AIChat.user_id == user_id,
                AIChat.round_seq == 0,
                _pair_cond(a_id, b_id),
            )
        )).scalar()
        if last is not None and last > since:
            return False
    return True


async def collect_ai_social_events() -> list[dict]:
    """采样候选：同用户活跃角色两两配对（不同名、非敌对），限额 + 概率门控"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(AICharacter).where(AICharacter.is_active == True).order_by(AICharacter.id)  # noqa: E712
        )
        chars = result.scalars().all()
        from app.models.user import User
        urows = (await db.execute(select(User.id, User.ai_social_enabled))).all()
    social_on = {u_id for u_id, flag in urows if flag}

    by_user: dict[int, list[AICharacter]] = {}
    for c in chars:
        by_user.setdefault(c.user_id, []).append(c)

    events = []
    for uid, cs in by_user.items():
        if uid not in social_on:
            continue
        if len(cs) < 2:
            continue
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                a, b = cs[i], cs[j]
                # 同名/包含名视为同一人（如“阿明”与“阿明（外号版）”）不配对
                if a.name == b.name or a.name in b.name or b.name in a.name:
                    continue
                if _hostile(a.relation_type) or _hostile(b.relation_type):
                    continue
                if not await _pair_eligible(uid, a.id, b.id):
                    continue
                if random.random() > SAMPLE_PROBABILITY:
                    continue
                events.append({
                    "type": AI_SOCIAL_TYPE,
                    "priority": 0.5,
                    "candidate": {
                        "character_id": a.id,
                        "character_b_id": b.id,
                        "user_id": uid,
                    },
                })
    if events:
        _logger.info("AI social candidates: %d", len(events))
    return events


async def _user_recent_news(user_id: int, limit: int = 3) -> str:
    """用户近况：最近几条 user_info/event 记忆（供聊用户话题参考）"""
    async with async_session_factory() as db:
        from app.models.character import AICharacter as _AC
        _chars = (await db.execute(select(_AC).where(_AC.user_id == user_id))).scalars().all()
        names = [c.name for c in _chars if c.name]
        from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
        result = await db.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.memory_type.in_(["user_info", "event"]),
                Memory.is_archived == False,  # noqa: E712
                _active_status_clause(),
            ).order_by(Memory.created_at.desc()).limit(limit * 4)
        )
        rows = result.scalars().all()
    _tagged = [f"[记录于 {str(m.created_at)[:10]}] {(m.content or "")[:80]}" for m in rows]
    picked = _filter_cross_char_news(_tagged, names)
    return "\n".join(picked[:limit])


def _filter_cross_char_news(texts: list[str], names: list[str]) -> list[str]:
    """过滤涉及其他角色名的近况文本（2026-08-16 修复串线：用户与某角色的私事不共享给其他角色）"""
    return [t for t in texts if t and not any(n and n in t for n in names)]


async def _identity_text(char: AICharacter) -> str:
    gender = "男" if char.gender == "male" else "女" if char.gender == "female" else "未知"
    rel = char.relationship_summary or "普通朋友"
    return (
        f"角色：{char.name}（{gender}）\n"
        f"性格：{(char.personality or '友善')[:80]}\n"
        f"与用户的关系：{rel[:60]}"
    )


async def _last_first_round(user_id: int, a_id: int, b_id: int) -> str | None:
    async with async_session_factory() as db:
        r = await db.execute(
            select(AIChat.content).where(
                AIChat.user_id == user_id,
                AIChat.round_seq == 0,
                _pair_cond(a_id, b_id),
            ).order_by(AIChat.created_at.desc(), AIChat.id.desc()).limit(1)
        )
        return r.scalar_one_or_none()


async def run_ai_social(char_a_id: int, char_b_id: int, user_id: int) -> bool:
    """生成一次 AI-AI 私下对话（2-4 轮）并落库。返回是否生成。"""
    from app.agent.llm_client import chat_completion

    async with async_session_factory() as db:
        a = await db.get(AICharacter, char_a_id)
        b = await db.get(AICharacter, char_b_id)
        if not a or not b or not a.is_active or not b.is_active:
            return False
        if a.user_id != user_id or b.user_id != user_id:
            return False
        if _hostile(a.relation_type) or _hostile(b.relation_type):
            return False
        if not await _pair_eligible(user_id, char_a_id, char_b_id):
            return False
        a_name, b_name = a.name, b.name

    identity_a = await _identity_text(a)
    identity_b = await _identity_text(b)
    user_news = await _user_recent_news(user_id)
    # 用户昵称锚：让"用户"在对话中有具体身份，避免被排除在语境之外
    async with async_session_factory() as db:
        from app.models.user import User
        u = await db.get(User, user_id)
    user_name = (u.nickname or u.username) if u else "用户"
    topic_hint = "聊聊用户最近发生的事" if random.random() < TOPIC_USER_PROB else "聊聊日常琐事和共同兴趣"
    # 认知循环 v2.1：用户进行中话题（供角色聊用户近况；不注入关系温度/剧情状态，保持聊天侧语境）
    active_topics_hint = ""
    try:
        from app.agent.topic_tracker import load_active_topics_text
        async with async_session_factory() as db:
            from app.models.character import AICharacter as _AC
            _ch = await db.get(_AC, char_a_id)
        if _ch is not None and _ch.cognitive_loop_enabled:
            active_topics_hint = await load_active_topics_text(char_a_id, user_id)
    except Exception:
        pass

    a_partner, b_partner = _is_partner_char(a), _is_partner_char(b)
    if a_partner or b_partner:
        partner_name, other_name = (a_name, b_name) if a_partner else (b_name, a_name)
        boundary = (
            f"相处边界：{partner_name}是{user_name}的恋爱对象，对{user_name}专一；"
            f"{partner_name}与其他角色（包括{other_name}）保持朋友分寸，不得暧昧、调情或示爱；"
            f"{other_name}也不得与{partner_name}越界亲密。{user_name}是你们共同关心的中心。"
        )
    else:
        boundary = (
            f"相处边界：你们都是{user_name}身边的朋友角色，彼此以朋友身份相处，可以自然聊天；"
            f"朋友之间若互有好感也可以慢慢发展（包括交往），但要自然不突兀。"
            f"{user_name}是你们共同关心的中心。"
        )

    def speaker_of(seq: int) -> AICharacter:
        return a if seq % 2 == 0 else b

    def other_of(seq: int) -> AICharacter:
        return b if seq % 2 == 0 else a

    rounds = random.randint(2, 4)
    history: list[str] = []
    rows = []
    for seq in range(rounds):
        speaker = speaker_of(seq)
        other = other_of(seq)
        # v3.0 注入（2026-08-15）：当前说话人的核心记忆 + 世界状态（只注入说话人自己的，避免跨角色记忆泄露；失败静默降级）
        core_hint = ""
        world_hint = ""
        try:
            from app.memory.core import get_core_memories
            from app.events.facts import get_character_view
            _cores = await get_core_memories(speaker.id, limit=3)
            if _cores:
                core_hint = "你记得的核心信息（可自然引用）：\n" + "\n".join(
                    f"- [记录于 {str(m.created_at)[:10]}] {m.content[:60]}" for m in _cores
                )
            _world = await get_character_view(speaker.id, user_id, limit=3)
            if _world:
                world_hint = "你当前的处境（对话里可自然带过，别念数据）：\n" + _world
        except Exception:
            pass
        v3_hint = "\n".join(x for x in (core_hint, world_hint) if x)
        hint = (
            f"以下是 {a_name} 和 {b_name} 的私下对话（用户不在场，但用户是你们共同在意的人）。\n"
            f"{identity_a}\n{identity_b}\n"
            f"{boundary}\n"
            f"当前话题：{topic_hint}\n"
            f"用户近况参考：{user_news or '（暂无）'}\n"
            f"用户进行中的话题参考：{active_topics_hint or '（暂无）'}\n"
            f"{v3_hint + chr(10) if v3_hint else ''}"
            f"事实规则：推测用户的事只能说'可能/好像'，不能当成事实；没有依据的事不要编造。\n"
            f"指代规则：你是 {speaker.name}，只能用'我'自称；提到 {other.name}、{user_name} 或其他人"
            f"必须直接用名字，禁止用'他/她/你俩/这小子'等模糊指代。\n"
            f"已有对话：\n{'\n'.join(history) or '（刚开始）'}\n"
            f"现在轮到 {speaker.name} 说话。只输出 {speaker.name} 的一句话（40 字以内，口语化，"
            f"符合 {speaker.name} 的性格，可以回应{other.name}的话，不要输出名字前缀、引号或标注。）"
        )
        try:
            from app.agent.llm_client import load_character_reasoning_level
            _rl = await load_character_reasoning_level(speaker.id)
            _msgs = [
                {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
                {"role": "user", "content": hint},
            ]
            # D2-A（2026-08-18）：关闭 AI-AI 私聊深度思考（reasoning 本就被丢弃，纯浪费）——
            # 统一走挡位 1/0 的 prompt 引导分支（挡位 1 保留「先在心里简短想一下」引导）
            if _rl == 1:
                _msgs[0] = {"role": "system", "content": "先在心里简短想一下怎么说合适，然后直接输出要说的话，不要加引号和标注。"}
            text = await chat_completion(messages=_msgs, temperature=0.95, max_tokens=200, task="message")
            text = (text or "").strip().strip('"').strip("'").strip()
        except Exception as e:
            _logger.warning("AI social round %d failed: %s", seq, e)
            continue
        if not text or len(text) < 2:
            continue
        if seq == 0:
            # 首轮防重复：与上一事件首轮开头相似度过高则整次作废
            prev = await _last_first_round(user_id, char_a_id, char_b_id)
            if prev and SequenceMatcher(None, prev[:20], text[:20]).ratio() > 0.6:
                _logger.info("AI social skipped: opening too similar")
                return False
        history.append(f"{speaker.name}: {text}")
        rows.append(AIChat(
            user_id=user_id,
            character_a_id=min(char_a_id, char_b_id),
            character_b_id=max(char_a_id, char_b_id),
            speaker_id=speaker.id,
            round_seq=seq,
            content=text[:500],
        ))

    if not rows:
        return False
    async with async_session_factory() as db:
        db.add_all(rows)
        await db.commit()
    _logger.info("AI social saved: %d rounds, pair=(%d,%d)", len(rows), char_a_id, char_b_id)
    return True
