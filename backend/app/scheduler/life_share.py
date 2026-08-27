"""活动完成自然分享（#63 机制4，Flag：life_share_enabled）。

`life.activity_completed` 事件订阅者（照 events/handlers.py `_on_activity_completed` 模式注册）：
AI 做完一件事（创作/浏览/学习/反思）后按概率 + 亲密度自然提一句，而不是纯随机节律。

- 概率：create 0.3 / browse 0.15 / learn 0.12 / reflect 0.05；rest/organize/social_prepare 0；
- 亲密度 `(trust+attachment)/200` → 0.7x~1.3x 调整；fatigue>75 不发；
- 必须复用 arbiter 门控（is_dnd_now / is_user_active / unreplied_cooldown_active）；
- ProactiveTriggerLog(trigger_type="life_share") 配额：每角色每 6h ≤1、每日 ≤1；
- 生成后按自然度评分做一次低分重试/跳过（复用 message_generator.score_naturalness）。
失败静默，不阻塞活动主链路。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.database import async_session_factory
from app.models.character_state import CharacterState
from app.models.proactive_settings import ProactiveTriggerLog
from app.utils.logger import get_logger

_logger = get_logger("scheduler.life_share")

# v1 保守概率（按活动类型）
_PROB_BY_TYPE = {
    "create": 0.3,
    "browse": 0.15,
    "learn": 0.12,
    "reflect": 0.05,
    # 内部行为不打扰
    "rest": 0.0,
    "organize": 0.0,
    "social_prepare": 0.0,
}
_FATIGUE_LIMIT = 75.0
_TRIGGER_TYPE = "life_share"
_QUOTA_6H = timedelta(hours=6)
_QUOTA_24H = timedelta(hours=24)

# 自然度阈值（复用 #28 ① 的评分）
_RETRY_THRESHOLD = 0.45
_SKIP_THRESHOLD = 0.20


def share_probability(activity_type: str) -> float:
    """按活动类型取基础分享概率（纯函数，可测）。"""
    return _PROB_BY_TYPE.get((activity_type or "").strip(), 0.0)


def intimacy_multiplier(trust: float, attachment: float) -> float:
    """亲密度 `(trust+attachment)/200` → 0.7x~1.3x（纯函数，可测）。"""
    t = max(0.0, min(100.0, float(50 if trust is None else trust)))
    a = max(0.0, min(100.0, float(50 if attachment is None else attachment)))
    m = (t + a) / 200.0  # 0..1
    return max(0.7, min(1.3, 0.7 + m * 0.6))


def should_share(base_prob: float, intimacy: float, fatigue: float, rng=None) -> tuple[bool, float]:
    """概率门控纯函数：返回 (是否分享, 修正后概率)。fatigue>75 拦截。"""
    if base_prob <= 0:
        return False, 0.0
    if float(fatigue or 0) > _FATIGUE_LIMIT:
        return False, 0.0
    p = max(0.0, min(1.0, base_prob * intimacy))
    r = rng or random
    return (r.random() < p), p


async def _quota_ok(db, character_id: int) -> bool:
    """每角色每 6h ≤1、每日 ≤1（ProactiveTriggerLog trigger_type=life_share 计数）。"""
    since_6h = datetime.now(timezone.utc) - _QUOTA_6H
    since_24h = datetime.now(timezone.utc) - _QUOTA_24H
    for since in (since_6h, since_24h):
        cnt = (await db.execute(
            select(func.count()).where(
                ProactiveTriggerLog.character_id == character_id,
                ProactiveTriggerLog.trigger_type == _TRIGGER_TYPE,
                ProactiveTriggerLog.decision == "approved",
                ProactiveTriggerLog.created_at >= since,
            )
        )).scalar() or 0
        if cnt >= 1:
            return False
    return True


async def _log_approved(db, *, character_id: int, user_id: int, reason: str = "") -> None:
    db.add(ProactiveTriggerLog(
        character_id=character_id, user_id=user_id,
        trigger_type=_TRIGGER_TYPE, trigger_reason=str(reason)[:200] or None,
        priority=5, decision="approved",
    ))


def _naturalness_flag() -> bool:
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("proactive_naturalness_score", True))
    except Exception:
        return True


async def _generate_share(character_id: int, user_id: int, activity_type: str, summary: str, retry: bool = False) -> str:
    """LLM 生成 1 句口语化分享；注入最近聊天摘要避免突兀换话题。"""
    from app.agent.llm_client import chat_completion
    from app.models.character import AICharacter

    name, personality = f"角色{character_id}", "友善"
    try:
        async with async_session_factory() as db:
            char = await db.get(AICharacter, character_id)
            if char:
                name, personality = char.name, (char.personality or "友善")
    except Exception:
        pass

    recent_ctx = ""
    try:
        from app.services.chat_service import get_latest_session_id
        from app.scheduler.triggers import get_last_messages
        sid = await get_latest_session_id(user_id, character_id)
        if sid:
            recent_ctx = (await get_last_messages(sid, limit=5)) or ""
    except Exception:
        recent_ctx = ""

    hint = (
        f"你是{name}，性格{personality}。你刚做了一件小事，想随口自然提一句。\n"
        + (f"你们最近在聊：\n{recent_ctx}\n" if recent_ctx else "")
        + f"你刚完成：{activity_type}（{summary[:80]}）。\n"
        + "用 1 句口语化的话自然带出这件事，像对亲近的人随口分享，别用通知口吻，别加任何标注/引号。"
        + ("注意：上次那样太生硬了，这次更口语、更随意一点。" if retry else "")
    )
    raw = await chat_completion(
        messages=[{"role": "system", "content": "直接输出一句随口的分享。"},
                  {"role": "user", "content": hint}],
        temperature=0.9, max_tokens=128, task="life_share", user_id=user_id,
    )
    return (raw or "").strip().strip('"').strip("'")


async def on_activity_completed(payload: dict) -> None:
    """life.activity_completed 订阅者：按概率+亲密度+arbiter 门控自然分享。失败静默。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("life_share_enabled", False):
            return
        _d = payload.get("data") or payload
        activity_type = str(_d.get("activity_type") or "")
        character_id = _d.get("character_id")
        user_id = _d.get("user_id")
        summary = str(_d.get("summary") or "")[:200]
        if not character_id or not user_id:
            return
        base_prob = share_probability(activity_type)
        if base_prob <= 0:
            return

        async with async_session_factory() as db:
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
            trust = float(getattr(st, "trust", 50) or 50) if st else 50.0
            attachment = float(getattr(st, "attachment", 50) or 50) if st else 50.0
            fatigue = float(getattr(st, "fatigue", 50) or 50) if st else 50.0
            if not await _quota_ok(db, character_id):
                _logger.info("life_share skip char=%d quota", character_id)
                return

        intimacy = intimacy_multiplier(trust, attachment)
        ok, prob = should_share(base_prob, intimacy, fatigue)
        if not ok:
            return

        # arbiter 门控（复用，防骚扰）
        from app.scheduler.arbiter import is_dnd_now, is_user_active, unreplied_cooldown_active
        cn_now = datetime.now(timezone(timedelta(hours=8)))
        if await is_dnd_now(character_id, cn_now):
            _logger.info("life_share skip char=%d dnd", character_id)
            return
        if await is_user_active(character_id, user_id):
            _logger.info("life_share skip char=%d user active", character_id)
            return
        if await unreplied_cooldown_active(character_id, user_id):
            _logger.info("life_share skip char=%d unreplied cooldown", character_id)
            return

        # 生成 + 自然度低分重试/跳过
        text = await _generate_share(character_id, user_id, activity_type, summary)
        if not text:
            return
        if _naturalness_flag():
            from app.scheduler.message_generator import score_naturalness
            if score_naturalness(text) < _RETRY_THRESHOLD:
                text2 = await _generate_share(character_id, user_id, activity_type, summary, retry=True)
                if text2 and score_naturalness(text2) >= score_naturalness(text):
                    text = text2
            if score_naturalness(text) < _SKIP_THRESHOLD:
                _logger.info("life_share skip char=%d low naturalness", character_id)
                return

        # 发送（state_triggers 成熟做法）
        from app.services.chat_service import get_latest_session_id
        from app.scheduler.scheduler import send_to_session
        session_id = await get_latest_session_id(user_id, character_id)
        if session_id is None:
            return
        # 配额落库 + 发送（原子提交失败静默不阻塞）
        async with async_session_factory() as db:
            await _log_approved(db, character_id=character_id, user_id=user_id, reason=f"{activity_type}:{summary[:60]}")
            await db.commit()
        await send_to_session(session_id, character_id, user_id, text, message_type="life_share")
        _logger.info("life_share sent char=%d act=%s prob=%.2f", character_id, activity_type, prob)
    except Exception as e:
        _logger.warning("life_share on_activity_completed failed: %s", e)


def register_with(handlers) -> None:
    """在 events/handlers.register_builtin_handlers 里注册本订阅者。"""
    from app.events.bus import event_bus
    if on_activity_completed not in event_bus._subscribers.get("life.activity_completed", []):
        event_bus.subscribe("life.activity_completed", on_activity_completed)
