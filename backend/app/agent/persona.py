"""人格上下文统一层（认知架构 v2.1 Phase 3）：聊天主链路与主动消息共用。

assemble_persona_context 组装与聊天历史无关的人格块：
关系/当前状态/关系温度/八维感受/剧情回忆/剧情进行中/最近情绪事件/进行中话题。
context_builder 与 scheduler.message_generator 均调用本模块，保证主动/被动同人格。

社交交互层 v2（2026-08-10）：新增 platform 参数（默认 app）；公开平台（外部渠道）按
platform_profiles 档案做公开裁剪（不注入身份画像/关系温度/私人记忆），App 主链路行为不变。
"""
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from app.db.database import async_session_factory
from app.memory.format import format_memory_line  # X-1（2026-08-18）：记忆注入行公共格式化
from app.utils.logger import get_logger

_logger = get_logger("agent.persona")



async def _load_platform_profile(platform: str) -> dict | None:
    """读取平台档案（platform_profiles 表）；异常/未配置静默降级为 None（保持 App 全量行为）"""
    if not platform:
        return None
    try:
        from app.models.social import PlatformProfile
        async with async_session_factory() as db:
            row = (await db.execute(
                select(PlatformProfile).where(PlatformProfile.platform == platform)
            )).scalars().first()
        if row is None:
            return None
        return {
            "visibility": row.visibility or "private",
            "relationship_level": row.relationship_level or "general",
            "memory_access": row.memory_access or "full",
            "tone": row.tone or "private",
            "content_style": row.content_style or "",
            "enabled": bool(row.enabled),
        }
    except Exception as e:
        _logger.warning("Persona: platform profile failed: %s", e)
        return None


def _build_platform_profile_text(platform: str, profile: dict | None, public: bool) -> str:
    """公开平台表达约束文本（App 私有平台返回空串）"""
    if not public or profile is None:
        return ""
    tone_cn = {
        "social": "社交化、友好",
        "creative": "有创意、有个性",
        "private": "私密、亲近",
    }.get(profile.get("tone") or "social", "社交化")
    return (
        f"你现在身处公开平台（{platform}），面对的是陌生人与粉丝，不是用户本人。"
        f"请保持{tone_cn}的表达，不暴露与用户的私密关系、用户身份画像与关系温度，"
        "不要代替账号主人以他的第一人称叙述其个人经历。"
    )


async def assemble_persona_context(character_id: int, user_id: int, platform: str = "app") -> dict:
    """人格上下文块（聊天历史无关部分）；任何单块失败静默降级为默认值"""
    # 平台档案（Module A）：platform_profiles 决定公开裁剪（app=private 全量；外部渠道=public 受限）
    platform_profile = await _load_platform_profile(platform)
    public = platform != "app" and bool(
        platform_profile and platform_profile.get("enabled") and platform_profile.get("visibility") == "public"
    )
    memory_limited = public and platform_profile.get("memory_access") == "limited"

    from app.models.character import AICharacter
    async with async_session_factory() as db:
        char = (await db.execute(
            select(AICharacter).where(AICharacter.id == character_id)
        )).scalar_one_or_none()

    relationship = (char.relationship_summary or "普通朋友") if char else "普通朋友"
    current_status = (char.current_status or "你们正在聊天") if char else "你们正在聊天"
    cognitive = bool(char and char.cognitive_loop_enabled)
    memory_v2 = bool(char and char.memory_v2_enabled)

    # 剧情回忆（v5-C）：近 3 天剧情线摘要 + 关系温度（C-1），角色可自然提起"昨天那事"
    storyline_recall = "无"
    try:
        from app.scheduling.storyline_engine import build_storyline_recall_text, build_relationship_temperature_text
        recall = await build_storyline_recall_text(character_id, user_id)
        temp = await build_relationship_temperature_text(character_id, user_id)
        if recall:
            storyline_recall = recall
            if temp:
                storyline_recall += "\n" + temp
        elif temp:
            storyline_recall = temp
    except Exception as e:
        _logger.warning("Persona: storyline recall failed: %s", e)

    # 剧情线进行中状态（P1-1）：冷战/吃醋/疲惫激活时注入
    storyline_status = "无"
    try:
        from app.scheduling.storyline_engine import build_active_storyline_status_text
        st_txt = await build_active_storyline_status_text(character_id)
        if st_txt:
            storyline_status = st_txt
    except Exception as e:
        _logger.warning("Persona: storyline status failed: %s", e)

    # 八维感受（P1-1）：查 character_state 压缩成一行注入
    character_feelings = "无"
    try:
        from app.models.character import CharacterState
        from app.application.character_state_service import DIMENSIONS as _DIMS
        async with async_session_factory() as db:
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
        if st is not None:
            parts = [f"{_cn}{getattr(st, _k)}" for _k, _cn, _ in _DIMS]
            character_feelings = "、".join(parts)
    except Exception as e:
        _logger.warning("Persona: feelings failed: %s", e)

    # 最近情绪事件记忆（P2-2）：近 7 天最近一条 sub_type=emotion，让"昨天的事"可检索
    recent_emotion = "无"
    try:
        from app.models.memory import Memory
        from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
        async with async_session_factory() as db:
            emo_mem = (await db.execute(
                select(Memory)
                .where(
                    Memory.character_id == character_id,
                    Memory.sub_type == "emotion",
                    Memory.is_archived == False,
                    Memory.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7),
                    _active_status_clause(),
                )
                .order_by(Memory.created_at.desc())
                .limit(1)
            )).scalars().first()
        if emo_mem is not None and emo_mem.content:
            # X-1（2026-08-18）：与主链路共用公共格式化函数（prefix="" 保留本注入点无行首前缀的既有格式；max_len=150）
            recent_emotion = format_memory_line(
                {"content": emo_mem.content, "created_at": emo_mem.created_at},
                prefix="", max_len=150,
            )
    except Exception as e:
        _logger.warning("Persona: recent emotion failed: %s", e)

    # 关系标量（信任/依恋/好奇；认知开关开启时注入）
    relationship_state = ""
    if cognitive and not public:
        try:
            from app.models.character import CharacterState
            async with async_session_factory() as db:
                st = (await db.execute(
                    select(CharacterState).where(CharacterState.character_id == character_id)
                )).scalar_one_or_none()
            if st is not None:
                _rparts = []
                for _k, _cn in (("trust", "信任"), ("attachment", "依恋"), ("curiosity", "好奇")):
                    _v = getattr(st, _k, None)
                    if _v is not None:
                        _rparts.append(f"{_cn}{int(_v)}")
                if _rparts:
                    relationship_state = "关系温度（自然体现，别念数据）：" + "、".join(_rparts)
        except Exception as e:
            _logger.warning("Persona: relationship state failed: %s", e)

    # 身份画像（记忆架构 v2.1 Phase 5）：开关开启时注入 sub_type=identity 置顶记忆
    identity_profile = ""
    if memory_v2 and not memory_limited:
        try:
            from app.models.memory import Memory
            from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
            async with async_session_factory() as db:
                ident = (await db.execute(
                    select(Memory)
                    .where(
                        Memory.character_id == character_id,
                        Memory.sub_type == "identity",
                        Memory.is_pinned == True,
                        Memory.is_archived == False,
                        _active_status_clause(),
                    )
                    .order_by(Memory.updated_at.desc())
                    .limit(1)
                )).scalars().first()
            if ident is not None and ident.content:
                identity_profile = ident.content[:200]
        except Exception as e:
            _logger.warning("Persona: identity profile failed: %s", e)

    # 进行中话题（认知开关开启时注入）
    active_topics = ""
    if cognitive:
        try:
            from app.agent.topic_tracker import load_active_topics_text
            active_topics = await load_active_topics_text(character_id, user_id)
        except Exception as e:
            _logger.warning("Persona: active topics failed: %s", e)

    return {
        "relationship": relationship,
        "current_status": current_status,
        "identity_profile": identity_profile,
        "relationship_state": relationship_state,
        "character_feelings": character_feelings,
        "storyline_recall": storyline_recall,
        "storyline_status": storyline_status,
        "recent_emotion": recent_emotion,
        "active_topics": active_topics,
        "cognitive": cognitive,
        "public": public,
        "platform_profile_text": _build_platform_profile_text(platform, platform_profile, public),
    }


async def build_active_channel_persona(character_id: int, user_id: int) -> str:
    """主动通道注入块：关系温度 / 剧情状态 / 进行中话题（认知开关关闭时返回空串）。

    供 memory_review / emotion_care / pet_care / ai_social 等主动通道生成消息时注入，
    保证主动消息与聊天主链路同人格、可互相衔接。
    """
    try:
        p = await assemble_persona_context(character_id, user_id)
    except Exception as e:
        _logger.warning("Persona: active channel block failed: %s", e)
        return ""
    if not p.get("cognitive"):
        return ""
    parts = []
    if p.get("relationship_state"):
        parts.append(p["relationship_state"])
    if p.get("storyline_status") and p["storyline_status"] != "无":
        parts.append(p["storyline_status"])
    if p.get("active_topics"):
        parts.append("你们进行中的话题（优先承接进行中的话题，别生硬）：\n" + p["active_topics"])
    return "\n".join(parts)
