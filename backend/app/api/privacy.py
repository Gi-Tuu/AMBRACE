"""AI 隐私上锁 API：日记 / 手机感知快照（小手机）查看需向 AI 申请

- 冷却：申请后 2 分钟内不能再次申请
- 通过概率：P3 八维/关系标量/事件加权（信任 >= 阈值时自动同意并自动关闭"隐私上锁"）
- 解锁有效期：由 AI 决定（0.5h~24h，LLM 输出，钳制）
- 记忆：只写概要行（申请详情不写记忆/日记）
"""
import json
import random
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.agent.llm_client import chat_completion
from app.db.database import get_db
from app.models.chat import ChatSession
from app.models.character import AICharacter
from app.models.character import CharacterState
from app.models.user import PrivacyRequest
from app.models.character import ProactiveSettings
from app.models.user import User
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc as _now_naive

router = APIRouter(prefix="/api/v1/privacy", tags=["Privacy"])
_logger = get_logger("api.privacy")

COOLDOWN_SECONDS = 120  # 申请后 2 分钟内不能再次申请
BASE_APPROVE_RATE = 0.5  # 基础通过概率 50%
TRUST_OPEN_THRESHOLD = 80  # 信任开放：>= 自动同意并自动关闭"隐私上锁"

async def _weighted_approve_rate(db: AsyncSession, character_id: int) -> float:
    """P3：八维/关系标量/事件加权通过概率（钳制 [0.1, 0.95]）

    公式：base 0.5 + Σ(维度加权)；信任/依恋/心情/舒适感高 → 上升，
    怒气/疲惫高 → 下降；剧情线激活冷战/吵架 → 额外罚分。
    零 LLM，复用现有状态读取。
    """
    rate = BASE_APPROVE_RATE
    try:
        st = (await db.execute(
            select(CharacterState).where(CharacterState.character_id == character_id)
        )).scalar_one_or_none()
        if st is not None:
            def _w(v: int, k: float) -> float:
                return (int(v or 50) - 50) / 100.0 * k

            rate += _w(st.trust, 0.30)
            rate += _w(st.attachment, 0.10)
            rate += _w(st.mood, 0.15)
            rate += _w(st.comfort, 0.10)
            rate -= _w(st.anger, 0.20)
            rate -= _w(st.fatigue, 0.05)
            rate += _w(st.sensitivity, 0.05)
    except Exception as e:
        _logger.warning("Privacy weighted state failed char=%d: %s", character_id, e)
    # 事件罚分：剧情线激活冷战/吵架/生气
    try:
        from app.scheduling.storyline_engine import build_active_storyline_status_text
        txt = await build_active_storyline_status_text(character_id)
        if txt and any(k in txt for k in ("冷战", "吵架", "生气", "闹别扭")):
            rate -= 0.10
    except Exception:
        pass
    return max(0.1, min(0.95, rate))

_TARGET_CN = {"diary": "日记", "phone": "手机"}

_DEFAULT_REPLY_OK = "嗯…看在你这么诚心的份上，就给你看看吧。"
_DEFAULT_REPLY_NO = "今天不太想给你看这个…下次再说吧。"


class RequestIn(BaseModel):
    target: str  # diary / phone


async def _check_owned(db: AsyncSession, character_id: int, user_id: int, lang: str = "zh") -> AICharacter:
    result = await db.execute(
        select(AICharacter).where(
            AICharacter.id == character_id,
            AICharacter.user_id == user_id,
            AICharacter.is_active == True,
        )
    )
    char = result.scalar_one_or_none()
    if char is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    return char


async def _resolve_latest_character(db: AsyncSession, user_id: int) -> int | None:
    """小手机无角色上下文：取用户最近互动（updated_at 最新会话）的角色"""
    result = await db.execute(
        select(ChatSession.character_id)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_settings(db: AsyncSession, character_id: int) -> ProactiveSettings:
    result = await db.execute(
        select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ProactiveSettings(character_id=character_id)
        db.add(settings)
        await db.flush()
        await db.commit()
        await db.refresh(settings)
    return settings


async def _active_unlock(
    db: AsyncSession, character_id: int, user_id: int, target: str, now: datetime
) -> datetime | None:
    result = await db.execute(
        select(PrivacyRequest)
        .where(
            PrivacyRequest.character_id == character_id,
            PrivacyRequest.user_id == user_id,
            PrivacyRequest.target_type == target,
            PrivacyRequest.status == "approved",
            PrivacyRequest.unlock_until.is_not(None),
            PrivacyRequest.unlock_until > now,
        )
        .order_by(PrivacyRequest.created_at.desc())
        .limit(1)
    )
    req = result.scalar_one_or_none()
    return req.unlock_until if req is not None else None


async def _cooldown_remaining(
    db: AsyncSession, character_id: int, user_id: int, target: str, now: datetime
) -> int:
    result = await db.execute(
        select(PrivacyRequest)
        .where(
            PrivacyRequest.character_id == character_id,
            PrivacyRequest.user_id == user_id,
            PrivacyRequest.target_type == target,
        )
        .order_by(PrivacyRequest.created_at.desc())
        .limit(1)
    )
    req = result.scalar_one_or_none()
    if req is None:
        return 0
    elapsed = (now - req.created_at.replace(tzinfo=None)).total_seconds()
    return max(0, int(COOLDOWN_SECONDS - elapsed))


async def _gen_reply(
    character_id: int, name: str, nickname: str, target_cn: str, approved: bool
) -> tuple[str, str, float]:
    """AI 生成回复口吻 + 情绪标签 + 解锁时长（0.5~24h）；失败降级默认模板"""
    decide = "同意" if approved else "不同意"
    default_reply = _DEFAULT_REPLY_OK if approved else _DEFAULT_REPLY_NO
    prompt = (
        f"你是「{name}」，一个与用户很亲密的 AI 角色。"
        f"用户{nickname}申请查看你的{target_cn}，你心里已经决定：{decide}。"
        f"请以你的口吻回复用户这句话（15-35字，自然一点），"
        f"并给出你此刻的情绪（从：开心/无所谓/烦躁/害羞/严肃/犹豫 中选一个词），"
        f"以及若同意时解锁时长小时数（0.5~24 之间的数字，可小数）。"
        f"只输出 JSON，不要多余文字：{{\"reply\": \"...\", \"mood\": \"...\", \"hours\": 1.5}}"
    )
    try:
        text = await chat_completion(
            messages=[
                {"role": "system", "content": "你是一个输出 JSON 的助手。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_tokens=200,
            task="message",
        )
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        reply = str(data.get("reply") or default_reply).strip()[:200]
        mood = str(data.get("mood") or "无所谓").strip()[:10]
        try:
            hours = float(data.get("hours") or 1.0)
        except (TypeError, ValueError):
            hours = 1.0
        hours = max(0.5, min(24.0, hours))
        return reply, mood, hours
    except Exception as e:
        _logger.warning("Privacy reply LLM failed char=%d: %s", character_id, e)
        return default_reply, "无所谓", 1.0


async def _write_summary_memory(
    character_id: int, user_id: int, nickname: str, name: str,
    target_cn: str, approved: bool, mood: str,
) -> None:
    """概要行入记忆：xx（用户）申请查看角色（xx）的日记/手机，xx 感觉xx，同意/不同意查看"""
    from app.memory.service import save_memory
    content = (
        f"{nickname}（用户）申请查看角色（{name}）的{target_cn}，"
        f"{name}感觉{mood}，{'同意' if approved else '不同意'}查看"
    )
    await save_memory(
        user_id=user_id,
        character_id=character_id,
        memory_type="insight",
        content=content,
        importance=3,
        sub_type="privacy",
        source="privacy_request",
        skip_dedup=True,
        speaker_type="system",
        epistemic_status="FACT",
    )


async def _send_chat_followup(
    character_id: int, user_id: int, name: str, nickname: str,
    target_cn: str, approved: bool,
) -> None:
    """P2：申请结果落库后，角色在最近会话里自然回应 1 条（口语化，不出现'申请''系统'字眼）"""
    from app.application.chat_service import get_latest_session_id
    from app.scheduling.scheduler import send_to_session
    try:
        session_id = await get_latest_session_id(user_id, character_id)
    except Exception as e:
        _logger.warning("Privacy followup session lookup failed: %s", e)
        return
    if session_id is None:
        return
    decide = "同意" if approved else "没同意"
    prompt = (
        f"你是「{name}」，一个与用户很亲密的 AI 角色。"
        f"用户{nickname}刚刚想看你（{name}）的{target_cn}，你心里{decide}了。"
        "请像平时聊天一样，对这件事自然地说一句话（15-35字），"
        "口语化、带个人语气（可以俏皮/温柔/傲娇），但不要出现'申请''查看权限''系统'这类字眼。"
    )
    try:
        from app.agent.llm_client import chat_completion
        text = await chat_completion(
            messages=[
                {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=160,
            task="message",
        )
        text = (text or "").strip().strip('"').strip("'")
        if not text or len(text) < 2:
            return
    except Exception as e:
        _logger.warning("Privacy followup LLM failed char=%d: %s", character_id, e)
        return
    await send_to_session(
        session_id, character_id, user_id, text[:300], message_type="privacy_reply",
    )


async def _nickname(db: AsyncSession, user_id: int) -> str:
    result = await db.execute(select(User.nickname).where(User.id == user_id))
    return result.scalar_one_or_none() or "用户"


@router.get("/{character_id}/status")
async def get_privacy_status(
    character_id: int,
    target: str = "diary",
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """锁屏态/冷却倒计时/解锁截止：character_id 传 0 时按最近互动角色解析（小手机）"""
    target = (target or "diary").lower()
    if target not in _TARGET_CN:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "target_invalid"))
    if character_id > 0:
        await _check_owned(db, character_id, user_id, lang)
    else:
        character_id = await _resolve_latest_character(db, user_id)
        if character_id is None:
            return {
                "character_id": 0, "enabled": False, "locked": False,
                "cooldown_remaining": 0, "unlock_until": None,
            }
    settings = await _get_settings(db, character_id)
    enabled = bool(getattr(settings, "privacy_lock_enabled", True))
    now = _now_naive()
    unlock_until = await _active_unlock(db, character_id, user_id, target, now)
    cooldown = await _cooldown_remaining(db, character_id, user_id, target, now)
    return {
        "character_id": character_id,
        "enabled": enabled,
        "locked": bool(enabled) and unlock_until is None,
        "cooldown_remaining": cooldown,
        "unlock_until": unlock_until.isoformat() if unlock_until is not None else None,
    }


@router.post("/{character_id}/request")
async def request_privacy_access(
    character_id: int,
    data: RequestIn,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """向 AI 申请查看：冷却校验 + 概率/信任判定 + AI 回复 + 概要记忆"""
    target = (data.target or "diary").lower()
    if target not in _TARGET_CN:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "target_invalid"))
    if character_id > 0:
        char = await _check_owned(db, character_id, user_id, lang)
    else:
        character_id = await _resolve_latest_character(db, user_id)
        if character_id is None:
            return {
                "approved": True, "ai_reply": "", "mood_label": "",
                "unlock_until": None, "cooldown_remaining": 0,
                "privacy_lock_enabled": False, "trust_open": False,
            }
        char = await _check_owned(db, character_id, user_id, lang)

    now = _now_naive()
    cooldown = await _cooldown_remaining(db, character_id, user_id, target, now)
    if cooldown > 0:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "privacy_cooldown", cooldown=cooldown))

    name = char.name
    nickname = await _nickname(db, user_id)
    target_cn = _TARGET_CN[target]

    # 信任开放：trust >= 阈值 → 直接同意并自动关闭"隐私上锁"
    trust = 50
    try:
        tr = await db.execute(
            select(CharacterState.trust).where(CharacterState.character_id == character_id)
        )
        trust = tr.scalar_one_or_none() or 50
    except Exception as e:
        _logger.warning("Privacy trust read failed char=%d: %s", character_id, e)
    trust_open = int(trust or 50) >= TRUST_OPEN_THRESHOLD
    approved = trust_open
    auto_disabled = False
    if trust_open:
        try:
            settings = await _get_settings(db, character_id)
            if bool(getattr(settings, "privacy_lock_enabled", True)):
                settings.privacy_lock_enabled = False
                await db.commit()
                auto_disabled = True
        except Exception as e:
            _logger.warning("Privacy auto-disable failed char=%d: %s", character_id, e)
    else:
        approved = random.random() < await _weighted_approve_rate(db, character_id)

    reply, mood, hours = await _gen_reply(character_id, name, nickname, target_cn, approved)
    unlock_until = None
    if approved:
        unlock_until = now + timedelta(hours=hours)

    req = PrivacyRequest(
        character_id=character_id,
        user_id=user_id,
        target_type=target,
        status="approved" if approved else "rejected",
        ai_reply=reply,
        mood_label=mood,
        unlock_until=unlock_until,
    )
    db.add(req)
    await db.commit()

    try:
        await _write_summary_memory(
            character_id, user_id, nickname, name, target_cn, approved, mood
        )
    except Exception as e:
        _logger.warning("Privacy summary memory failed char=%d: %s", character_id, e)

    # P2：角色在私聊里自然回应（1 条；失败静默，不阻塞申请结果）
    try:
        await _send_chat_followup(character_id, user_id, name, nickname, target_cn, approved)
    except Exception as e:
        _logger.warning("Privacy chat followup failed char=%d: %s", character_id, e)

    return {
        "approved": approved,
        "ai_reply": reply,
        "mood_label": mood,
        "unlock_until": unlock_until.isoformat() if unlock_until is not None else None,
        "cooldown_remaining": COOLDOWN_SECONDS,
        "privacy_lock_enabled": not auto_disabled if trust_open else True,
        "trust_open": trust_open,
    }
