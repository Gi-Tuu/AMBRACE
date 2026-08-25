"""语音模式（voice_mode）上下文组装（Phase A 轻量版）

Phase A 范围：角色人设（性格/聊天风格）+ 最近 N 条消息 + 语音口语化指令。
认知循环（记忆/关系/persona 统一层）完整注入列入 Phase B
（避免每回合多表查询拖慢首字，语音场景速度优先）。
"""
from sqlalchemy import select

from app.db.database import async_session_factory
from app.utils.logger import get_logger

_logger = get_logger("voice.mode")

VOICE_MODE_INSTRUCTION = """【语音通话模式】你现在正与用户语音通话，请遵守：
1. 用口语化短句回复，单次不超过 2-3 个短句（15 秒内），一次只说一个重点；
2. 不要使用列表、编号、markdown、emoji、括号动作或心理描写；
3. 自然使用语气词（嗯、哦、诶、哎呀），像真人开口说话；
4. 先简短回应对方的话，再补一句自己的话，不说教、不啰嗦。"""

MAX_RECENT_MESSAGES = 12


async def load_character_voice_params(character_id: int) -> dict:
    """角色语音参数（TTS 合成用）：gender/voice/voice_rate/voice_pitch/name"""
    from app.models.character import AICharacter
    async with async_session_factory() as db:
        row = (await db.execute(
            select(AICharacter).where(AICharacter.id == character_id)
        )).scalar_one_or_none()
    if row is None:
        return {}
    return {
        "gender": row.gender,
        "voice": row.voice,
        "voice_rate": row.voice_rate,
        "voice_pitch": row.voice_pitch,
        "name": row.name,
    }


async def build_voice_messages(
    user_id: int, character_id: int, session_id: int, user_text: str,
    interrupted_text: str | None = None,
) -> list[dict]:
    """组装 OpenAI 消息：角色人设 + 最近消息 + voice_mode 指令 + 用户本轮语音文本

    interrupted_text: 上一轮被打断的半截回复（非 None 时注入「你没说完」提示，
    让角色自然衔接而不是重复被打断的内容）。
    """
    from app.models.character import AICharacter
    from app.models.chat_message import ChatMessage
    from app.models.user import User

    async with async_session_factory() as db:
        char = (await db.execute(
            select(AICharacter).where(AICharacter.id == character_id)
        )).scalar_one_or_none()
        user = (await db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        recent = (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(MAX_RECENT_MESSAGES)
        )).scalars().all()

    if char is None:
        return [{"role": "user", "content": user_text}]

    user_name = (user.nickname or user.username or "用户") if user else "用户"
    gender_cn = {"male": "男", "female": "女", "男": "男", "女": "女"}.get(
        (char.gender or "").strip().lower(), "未设置"
    )
    system_parts = [
        f"你是{char.name}，性别：{gender_cn}。",
    ]
    if char.personality:
        system_parts.append(f"人格：{char.personality}")
    if char.chat_style:
        system_parts.append(f"聊天风格：{char.chat_style}")
    # P0-3 语音情绪注入（2026-08-16）：读角色当前八维状态中影响语气的维度，口语化注入，不改链路行为
    try:
        from app.models.character_state import CharacterState
        async with async_session_factory() as _sdb:
            _st = (await _sdb.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
        if _st is not None:
            system_parts.append(
                f"当前情绪：心情{_st.mood}（0-100，50 中性，高=开心低=低落）、"
                f"疲惫{_st.fatigue}（高=累）、怒气{_st.anger}（高=生气）、"
                f"亲密渴望{_st.desire}（高=想亲近）。用语音自然体现当前情绪，不要生硬报数字。"
            )
    except Exception as _e:
        _logger.warning("Voice emotion inject failed: %s", _e)
    system_parts.append(VOICE_MODE_INSTRUCTION)
    system = "\n".join(system_parts)

    messages: list[dict] = [{"role": "system", "content": system}]
    history = list(reversed(recent))[-MAX_RECENT_MESSAGES:]
    for m in history:
        role = "assistant" if m.sender_type == "ai" else "user"
        content = (m.content or "").strip()
        if not content:
            continue
        if role == "assistant":
            content = f"{char.name}（语音说）：{content}"
        else:
            content = f"{user_name}（语音说）：{content}"
        messages.append({"role": role, "content": content})
    if interrupted_text:
        messages.append({
            "role": "system",
            "content": (
                "注意：你上一句回复说到一半就被对方打断了（对方听到的是："
                f"{interrupted_text}）。对方没有听完，请自然衔接对方接下来"
                "说的话，不要重复这段内容。"
            ),
        })
    messages.append({"role": "user", "content": user_text})
    return messages
