"""AI 角色管理 API"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db, async_session_factory
from app.models.character import AICharacter
from app.utils.logger import get_logger
from app.schemas.character import (
    CharacterCreate,
    CharacterUpdate,
    CharacterResponse,
    CharacterListResponse,
)
# 状态口径统一（2026-08-23）：agent_task_logs 的 status 历史杂化（ok/degraded/error/blocked/done/failed/partial），
# 展示侧统一用 classify 归一为 success/failed/partial/blocked，避免「blocked=限额/条件拦截」被误判为失败。
from app.agent.status import classify as _classify_status

router = APIRouter(prefix="/api/v1/characters", tags=["Characters"])
_logger = get_logger("api.characters")


async def _get_owned_character(db: AsyncSession, character_id: int, user_id: int, lang: str = "zh"):
    """按用户归属获取角色，不存在或非本人返回 404"""
    result = await db.execute(
        select(AICharacter).where(
            AICharacter.id == character_id,
            AICharacter.user_id == user_id,
        )
    )
    char = result.scalar_one_or_none()
    if not char:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    return char


# ---- 任务记录兜底（2026-08-23）：无正式 AgentTask 时用 AgentTaskLog 生成任务化摘要（只读展示）----

def _log_to_goal(steps_json: str | None, fallback: str) -> str:
    """从 AgentTaskLog steps_json 提取可读目标（任务化摘要）；无法解析时用 fallback"""
    if not steps_json:
        return fallback
    import json as _j
    try:
        steps = _j.loads(steps_json)
    except Exception:
        return fallback
    if isinstance(steps, list):
        names: list[str] = []
        for s in steps:
            if isinstance(s, dict):
                a = s.get("action") or s.get("route") or s.get("type")
                if a and str(a) not in names:
                    names.append(str(a))
        if names:
            return " / ".join(names)[:80]
        for s in steps:
            if isinstance(s, dict) and s:
                return ", ".join(f"{k}: {str(v)[:20]}" for k, v in list(s.items())[:3])[:80]
    elif isinstance(steps, dict):
        q = steps.get("query")
        if q:
            return f"记忆召回：{str(q)[:50]}"
    return fallback


async def _summarize_task_logs(character_id: int, db: AsyncSession) -> list[dict]:
    """无正式任务时，从最近 AgentTaskLog（排除 blocked 噪音）生成任务化摘要列表"""
    from app.models.agent_task_log import AgentTaskLog
    _wanted = ("chat", "scheduler", "group_chat", "image_gen", "reflection")
    logs = (await db.execute(
        select(AgentTaskLog)
        .where(
            AgentTaskLog.character_id == character_id,
            AgentTaskLog.status != "blocked",
        )
        .order_by(AgentTaskLog.id.desc())
        .limit(25)
    )).scalars().all()
    out = []
    for lg in logs:
        if lg.trigger not in _wanted:
            continue
        out.append({
            "trigger": lg.trigger or "log",
            "goal": _log_to_goal(lg.steps_json, lg.trigger or "工具执行"),
            # 状态口径统一（2026-08-23）：blocked=限额/条件拦截（非失败）、partial=部分成功，
            # 均不再被粗暴归为 failed；success/failed/partial/blocked 与前端/成功率统计一致。
            "status": _classify_status(lg.status),
            "progress": lg.steps_json,
            "result": None,
            "created_at": lg.created_at.isoformat() if lg.created_at else None,
        })
    return out


def _build_system_prompt(name: str, personality: str | None, chat_style: str | None, appearance: str | None = None, gender: str | None = None) -> str:
    """根据角色配置生成系统提示词"""
    parts = [f"你是一个名叫{name}的AI好友。"]
    if personality:
        parts.append(f"\n你的性格特点：{personality}")
    if chat_style:
        parts.append(f"\n你的聊天风格：{chat_style}")
    if appearance:
        parts.append(f"\n你的样貌：{appearance}")
    if gender:
        parts.append(f"\n你的性别：{gender}")
    parts.append("\n\n用自然、口语化的方式交流，保持回复简洁（2-4句话）。")
    parts.append("记住用户的个人信息并在合适的时候提起。")
    parts.append("关心用户的情绪状态，像真正的朋友一样回应。")
    return "".join(parts)


@router.post("", response_model=CharacterResponse, status_code=status.HTTP_201_CREATED)
async def create_character(data: CharacterCreate, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """创建新 AI 角色"""
    character = AICharacter(
        user_id=user_id,
        name=data.name,
        personality=data.personality,
        chat_style=data.chat_style,
        greeting_message=data.greeting_message,
        avatar_url=data.avatar_url,
        height=data.height,
        weight=data.weight,
        gender=data.gender,
        voice=data.voice,
        voice_rate=data.voice_rate,
        voice_pitch=data.voice_pitch,
        timezone_offset=data.timezone_offset,
        appearance=data.appearance,
        bio=data.bio,
        talkativeness=data.talkativeness,
        talkativeness_locked=data.talkativeness_locked,
        system_prompt=_build_system_prompt(
            name=data.name,
            personality=data.personality,
            chat_style=data.chat_style,
            appearance=data.appearance,
            gender=data.gender,
        ),
    )
    db.add(character)
    await db.flush()
    await db.refresh(character)
    _logger.info("Created character: id=%d name=%s", character.id, character.name)
    return character


async def _generate_greeting_text(char: AICharacter, user_id: int | None) -> str:
    """依据角色 personality/bio/chat_style 用 LLM 生成一句符合人设的开场白（含 BYOK）。

    复用统一 LLM 客户层（app/agent/llm_client.chat_completion，用户级 BYOK > 服务器级 DB > .env
    三级回退，与主动通道任务一致）。失败/无 key 时由调用方捕获返回空串，不破坏创建流程。
    """
    from app.agent.llm_client import chat_completion
    identity = "；".join(x for x in [
        f"名字：{char.name}",
        f"性格：{char.personality}" if char.personality else None,
        f"背景：{char.bio}" if char.bio else None,
        f"聊天风格：{char.chat_style}" if char.chat_style else None,
    ] if x)
    prompt = (
        f"你正在为角色生成一句开场白（作为 TA 与用户第一次聊天时的第一句话）。\n"
        f"角色信息：{identity}\n\n"
        "要求：一句话，符合角色人设与聊天风格，自然口语化，15-40 字，"
        "不要提'AI/角色/开场白'，不要加引号或前缀。只输出这一句话本身。"
    )
    text = await chat_completion(
        messages=[
            {"role": "system", "content": "你是一个生成角色开场白的助手，直接输出一句台词，不要多余文字。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.9,
        max_tokens=120,
        task="message",
        user_id=user_id,
    )
    return (text or "").strip().strip('"“”\' ')[:200]


@router.post("/{character_id}/generate-greeting")
async def generate_greeting(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """为角色生成一句符合人设的开场白（LLM），写回 greeting_message（主账号/本人）。

    一次性调用（仅创建后可触发，编辑已有角色只需保留字段）；依赖 persona/personality/bio/chat_style。
    失败/未配置 LLM / 无结果 → 静默返回空串，不落库不报错（前端可稍后手触发）。
    """
    char = await _get_owned_character(db, character_id, user_id, lang)
    greeting = ""
    try:
        greeting = await _generate_greeting_text(char, user_id)
    except Exception as e:
        _logger.warning("generate_greeting failed char=%d: %s", character_id, e)
    if greeting:
        char.greeting_message = greeting
        await db.flush()
        await db.refresh(char)
        await db.commit()
    return {
        "character_id": character_id,
        "greeting_message": char.greeting_message or greeting,
    }


@router.get("", response_model=CharacterListResponse)
async def list_characters(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """获取当前用户的 AI 角色列表"""
    result = await db.execute(
        select(AICharacter).where(
            AICharacter.user_id == user_id,
            AICharacter.is_active == True,
        )
    )
    characters = result.scalars().all()
    _logger.info("List characters: count=%d", len(characters))
    return CharacterListResponse(
        characters=[CharacterResponse.model_validate(c) for c in characters],
        total=len(characters),
    )


@router.get("/{character_id}/states")
async def get_character_states(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取角色八维可视化状态（心情/体温/性欲/占有欲/疲惫感/敏感度/舒适感/怒气值，0-100）"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.services.character_state_service import get_character_states as _get_states
    return await _get_states(character_id)


@router.get("/{character_id}", response_model=CharacterResponse)
async def get_character(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取单个 AI 角色详情"""
    result = await db.execute(
        select(AICharacter).where(
            AICharacter.id == character_id,
            AICharacter.user_id == user_id,
        )
    )
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    return character


@router.put("/{character_id}", response_model=CharacterResponse)
async def update_character(
    character_id: int,
    data: CharacterUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """修改 AI 角色信息"""
    await _get_owned_character(db, character_id, user_id, lang)
    result = await db.execute(
        select(AICharacter).where(AICharacter.id == character_id)
    )
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(character, field, value)

    # 如果名称/人格/风格变了，自动更新 system_prompt
    if any(f in update_data for f in ("name", "personality", "chat_style", "appearance", "gender")):
        character.system_prompt = _build_system_prompt(
            name=update_data.get("name", character.name),
            personality=update_data.get("personality", character.personality),
            chat_style=update_data.get("chat_style", character.chat_style),
            appearance=update_data.get("appearance", character.appearance),
            gender=update_data.get("gender", character.gender),
        )

    await db.flush()
    await db.refresh(character)
    _logger.info("Created character: id=%d name=%s", character.id, character.name)
    return character


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除 AI 角色（硬删除：清空全部关联数据 + 删除角色行，用户要求"删除角色=完全清除"）"""
    await _get_owned_character(db, character_id, user_id, lang)
    from sqlalchemy import delete as sa_delete
    from app.models.memory import Memory
    from app.models.diary import AIDiary
    from app.models.moment import AIMoment, MomentLike, MomentAILike, MomentComment
    from app.models.proactive_settings import ProactiveSettings
    from app.models.scheduled_event import ScheduledEvent
    from app.models.proactive_storyline import ProactiveStorylineItem

    result = await db.execute(
        select(AICharacter).where(AICharacter.id == character_id)
    )
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    # 级联清理（全部关联数据；聊天记录等一律清除）
    await db.execute(sa_delete(MomentComment).where(
        MomentComment.sender_type == "ai", MomentComment.sender_id == character_id))
    moment_ids = (await db.execute(
        select(AIMoment.id).where(AIMoment.character_id == character_id))).scalars().all()
    if moment_ids:
        await db.execute(sa_delete(MomentLike).where(MomentLike.moment_id.in_(moment_ids)))
        await db.execute(sa_delete(MomentAILike).where(MomentAILike.moment_id.in_(moment_ids)))
        await db.execute(sa_delete(MomentComment).where(MomentComment.moment_id.in_(moment_ids)))
        await db.execute(sa_delete(AIMoment).where(AIMoment.character_id == character_id))
    # 2026-08-13：其他角色对被删角色的记忆保留，标记"离开" + 生成【xxx离开了】记忆（避免割裂感，随遗忘机制自然淡去）
    char_name = (character.name or "").strip()
    hit_char_ids: list = []
    if char_name:
        try:
            # 重名处理：其他同名角色（id 不同）的记忆不参与匹配，避免误判为提到被删角色
            same_name_ids = set((await db.execute(
                select(AICharacter.id).where(
                    AICharacter.user_id == user_id,
                    AICharacter.id != character_id,
                    AICharacter.name == char_name,
                )
            )).scalars().all())
            stmt = select(Memory).where(
                Memory.user_id == user_id,
                Memory.character_id != character_id,
                Memory.is_archived == False,
            )
            if same_name_ids:
                stmt = stmt.where(Memory.character_id.notin_(same_name_ids))
            other_memories = (await db.execute(stmt)).scalars().all()
            hit_memories = [m for m in other_memories if char_name in (m.content or "")]
            hit_char_ids = sorted({m.character_id for m in hit_memories})
            # 标记携带角色 id（名字#id）：重名先后离开也能分别记录/去重
            mark = f"{char_name}#{character_id}" if character_id else char_name
            for m in hit_memories:
                names = [n.strip() for n in (m.departed_names or "").split(",") if n.strip()]
                if not any(n == mark or n == char_name for n in names):
                    names.append(mark)
                m.departed_names = ",".join(names)[:255]
        except Exception as _e:
            _logger.warning("Departure marking failed char=%d: %s", character_id, _e)
    # 记忆：先清向量库（按角色），再删记录（仅删被删角色自己的记忆；其他角色的记忆保留）
    from app.db.vector_store import delete_memory_vectors_by_character
    await delete_memory_vectors_by_character(character_id)
    await db.execute(sa_delete(Memory).where(Memory.character_id == character_id))
    await db.execute(sa_delete(AIDiary).where(AIDiary.character_id == character_id))
    await db.execute(sa_delete(ProactiveSettings).where(ProactiveSettings.character_id == character_id))
    await db.execute(sa_delete(ScheduledEvent).where(ScheduledEvent.character_id == character_id))
    await db.execute(sa_delete(ProactiveStorylineItem).where(ProactiveStorylineItem.character_id == character_id))

    # 聊天记录/会话/日摘要/提取记录/主动消息日志（用户要求：删除角色=清除其全部内容）
    from app.models.chat_session import ChatSession
    from app.models.chat_message import ChatMessage
    from app.models.daily_summary import DailySummary
    from app.models.processed_extraction import ProcessedExtraction
    from app.models.proactive_settings import ProactiveMessageLog

    session_ids = list((await db.execute(
        select(ChatSession.id).where(ChatSession.character_id == character_id))).scalars().all())
    if session_ids:
        # 先删提取记录（依赖消息 id 子查询），再删消息/摘要/会话
        await db.execute(sa_delete(ProcessedExtraction).where(
            ProcessedExtraction.user_message_id.in_(
                select(ChatMessage.id).where(ChatMessage.session_id.in_(session_ids)))))
        await db.execute(sa_delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
        await db.execute(sa_delete(DailySummary).where(DailySummary.session_id.in_(session_ids)))
        await db.execute(sa_delete(ChatSession).where(ChatSession.character_id == character_id))
    await db.execute(sa_delete(ProactiveMessageLog).where(ProactiveMessageLog.character_id == character_id))

    # 状态八维 / AI 间私聊 / 时光页大事记（补漏，随角色彻底清除）
    from app.models.character_state import CharacterState
    await db.execute(sa_delete(CharacterState).where(CharacterState.character_id == character_id))
    from app.models.ai_chat import AIChat
    await db.execute(sa_delete(AIChat).where(
        or_(AIChat.character_a_id == character_id, AIChat.character_b_id == character_id)))
    from app.models.timeline_event import TimelineEvent
    await db.execute(sa_delete(TimelineEvent).where(TimelineEvent.character_id == character_id))

    # 硬删除角色行本身（删除角色 = 完全清除，前端不再需要 is_active 过滤）
    await db.execute(sa_delete(AICharacter).where(AICharacter.id == character_id))

    await db.flush()
    _logger.info("Deleted character: id=%d name=%s", character.id, character.name)

    # 2026-08-13：删除提交后，为相关角色生成【xxx离开了】记忆（须提交后再写，避免 SQLite 锁冲突）
    if char_name and hit_char_ids:
        try:
            await db.commit()
        except Exception as _e:
            _logger.warning("Delete commit failed char=%d: %s", character_id, _e)
        from app.memory import save_memory as _save_departure
        for oid in hit_char_ids:
            try:
                await _save_departure(
                    user_id=user_id, character_id=oid,
                    memory_type="event", sub_type="departure",
                    content=f"【{char_name}离开了】{char_name}已经离开了，不再与你互动。",
                    title=f"{char_name}离开了",
                    importance=4, source="system", skip_dedup=True,
                )
            except Exception as _e:
                _logger.warning("Departure memory failed char=%d: %s", oid, _e)


@router.get("/{character_id}/emotion-timeline")
async def get_emotion_timeline(
    character_id: int,
    days: int = 7,
    dimension: str | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """状态情绪记忆时间线（只读，零 LLM）：情绪记忆 + 状态触发日志 + 剧情线事件三源合并，按时间倒序"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.services.emotion_timeline_service import get_emotion_timeline as _get_timeline
    return await _get_timeline(character_id, days=days, dimension=dimension)

@router.get("/{character_id}/agent-mind")
async def get_agent_mind(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """AI 内心世界（Phase J/P1，2026-08-16）：最近复盘 + 任务记录 + 工具使用轨迹"""
    await _get_owned_character(db, character_id, user_id, "zh")
    out = {"reflection": None, "tasks": [], "tool_logs": []}
    try:
        from app.models.memory import Memory
        _mr = (await db.execute(
            select(Memory)
            .where(Memory.character_id == character_id, Memory.memory_type == "ai_reflection")
            .order_by(Memory.id.desc())
            .limit(1)
        )).scalar_one_or_none()
        if _mr and _mr.content:
            out["reflection"] = {
                "content": _mr.content,
                "created_at": _mr.created_at.isoformat() if _mr.created_at else None,
            }
    except Exception:
        pass
    try:
        from app.models.agent_task import AgentTask
        rows = (await db.execute(
            select(AgentTask)
            .where(AgentTask.character_id == character_id)
            .order_by(AgentTask.id.desc())
            .limit(25)
        )).scalars().all()
        out["tasks"] = [{
            "trigger": r.trigger, "goal": r.goal, "status": r.status,
            "progress": r.progress_json, "result": r.result_json,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
        # 任务记录兜底（2026-08-23）：无正式任务时展示 AgentTaskLog 任务化摘要（只读，不改行为）
        if not out["tasks"]:
            try:
                out["tasks"] = await _summarize_task_logs(character_id, db)
            except Exception:
                pass
    except Exception:
        pass
    try:
        from app.models.agent_task_log import AgentTaskLog
        logs = (await db.execute(
            select(AgentTaskLog)
            .where(AgentTaskLog.character_id == character_id)
            .order_by(AgentTaskLog.id.desc())
            .limit(25)
        )).scalars().all()
        out["tool_logs"] = [{
            "trigger": r.trigger, "route": r.route,
            # 状态口径统一（2026-08-23）：展示侧归一为 success/failed/partial/blocked；
            # status_raw 保留原始库值（ok/error/blocked/degraded...）供排查。blocked≠失败。
            "status": _classify_status(r.status),
            "status_raw": r.status,
            "steps": r.steps_json, "latency_ms": r.latency_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in logs]
    except Exception:
        pass
    # P2-4 记忆召回可观测（2026-08-16）：最近 memory_search trace 汇总 + 明细（数据源 P0-2，只读）
    try:
        from app.models.agent_task_log import AgentTaskLog
        import json as _ms_json
        _ms_rows = (await db.execute(
            select(AgentTaskLog)
            .where(AgentTaskLog.character_id == character_id, AgentTaskLog.trigger == "memory_search")
            .order_by(AgentTaskLog.id.desc())
            .limit(50)
        )).scalars().all()
        _ms_list = []
        for _r in _ms_rows:
            _steps = {}
            try:
                _steps = _ms_json.loads(_r.steps_json or "{}")
            except Exception:
                pass
            _ms_list.append({
                "route": _r.route,
                "query": str(_steps.get("query") or "")[:60],
                # P2-4 语义修正：hit_count=召回候选命中数，returned=实际返回条数（旧日志无 returned 回退 hit_count）
                "hit_count": int(_steps.get("hit_count") or 0),
                "returned": int(_steps.get("returned") or _steps.get("hit_count") or 0),
                "latency_ms": _r.latency_ms,
                "created_at": _r.created_at.isoformat() if _r.created_at else None,
            })
        _ms_hit = sum(1 for s in _ms_list if s["hit_count"] > 0)
        out["memory_search"] = {
            "total": len(_ms_list),
            "hit": _ms_hit,
            "miss": len(_ms_list) - _ms_hit,
            "avg_latency_ms": int(sum(s["latency_ms"] or 0 for s in _ms_list) / len(_ms_list)) if _ms_list else 0,
            "recent": _ms_list[:25],
        }
    except Exception:
        pass
    # P0-4 运行笔记（2026-08-16）：身份画像 + 置顶摘要只读聚合（零 LLM，失败静默）
    notes = {"identity": None, "pinned": []}
    try:
        from sqlalchemy import or_ as _or_
        _id_row = (await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.memory_type == "user_info",
                Memory.sub_type == "identity",
                Memory.is_pinned == True,
                Memory.is_archived == False,
            )
            .order_by(Memory.id.desc())
            .limit(1)
        )).scalar_one_or_none()
        if _id_row and _id_row.content:
            notes["identity"] = {
                "content": _id_row.content,
                "updated_at": (_id_row.updated_at or _id_row.created_at).isoformat()
                if (_id_row.updated_at or _id_row.created_at) else None,
            }
        from app.memory.constants import _TYPE_CN as _TCN
        _pin_rows = (await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_pinned == True,
                Memory.is_archived == False,
                Memory.memory_type != "ai_reflection",
                _or_(Memory.sub_type.is_(None), Memory.sub_type != "identity"),
            )
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(25)
        )).scalars().all()
        for r in _pin_rows:
            if not r.content:
                continue
            notes["pinned"].append({
                "memory_type": r.memory_type,
                "label": _TCN.get(r.memory_type, r.memory_type),
                "content": r.content[:200],
                "updated_at": (r.updated_at or r.created_at).isoformat()
                if (r.updated_at or r.created_at) else None,
            })
    except Exception:
        pass
    out["running_notes"] = notes
    return out


@router.get("/{character_id}/lorebook")
async def list_lorebook(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """Lorebook 条目列表（P1-2）：角色拥有的关键词触发设定"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.models.lorebook_entry import LorebookEntry
    async with async_session_factory() as db2:
        rows = (await db2.execute(
            select(LorebookEntry).where(LorebookEntry.character_id == character_id)
            .order_by(LorebookEntry.updated_at.desc(), LorebookEntry.id.desc())
        )).scalars().all()
    return {"items": [
        {
            "id": r.id, "title": r.title, "content": r.content,
            "keywords": r.keywords, "exclude_keywords": r.exclude_keywords,
            "active": bool(r.active),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        } for r in rows
    ]}


class _LorebookUpsert(BaseModel):
    title: str
    content: str
    keywords: list[str] = []
    exclude_keywords: list[str] = []
    active: bool = True
    # L2 触发式注入进阶（核心版）：默认值保证向后兼容（is_regex=False/probability=100/
    # inclusion_group=''/sticky_rounds=0/cooldown_rounds=0）。
    is_regex: bool = False
    probability: int = 100
    inclusion_group: str = ""
    sticky_rounds: int = 0
    cooldown_rounds: int = 0


@router.post("/{character_id}/lorebook")
async def create_lorebook(
    character_id: int, data: _LorebookUpsert,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """新建 Lorebook 条目（P1-2；P2-5 Journal 化：每角色条目上限防失控）"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.models.lorebook_entry import LorebookEntry
    from sqlalchemy import func as _func
    import json as _json
    async with async_session_factory() as _cnt_db:
        _cnt = (await _cnt_db.execute(
            select(_func.count()).select_from(LorebookEntry).where(LorebookEntry.character_id == character_id)
        )).scalar() or 0
    if int(_cnt) >= 50:
        raise HTTPException(status_code=400, detail="lorebook limit 50 reached")
    entry = LorebookEntry(
        user_id=user_id, character_id=character_id,
        title=data.title.strip()[:50], content=data.content.strip(),
        keywords=_json.dumps([str(k).strip() for k in data.keywords if str(k).strip()], ensure_ascii=False),
        exclude_keywords=_json.dumps([str(k).strip() for k in data.exclude_keywords if str(k).strip()], ensure_ascii=False),
        active=data.active,
        is_regex=bool(data.is_regex),
        probability=max(0, min(100, int(data.probability or 100))),
        inclusion_group=(data.inclusion_group or "").strip()[:50],
        sticky_rounds=max(0, int(data.sticky_rounds or 0)),
        cooldown_rounds=max(0, int(data.cooldown_rounds or 0)),
    )
    async with async_session_factory() as db2:
        db2.add(entry)
        await db2.commit()
        await db2.refresh(entry)
    return {"ok": True, "id": entry.id}


@router.put("/{character_id}/lorebook/{entry_id}")
async def update_lorebook(
    character_id: int, entry_id: int, data: _LorebookUpsert,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """更新 Lorebook 条目（P1-2）"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.models.lorebook_entry import LorebookEntry
    import json as _json
    async with async_session_factory() as db2:
        entry = await db2.get(LorebookEntry, entry_id)
        if entry is None or entry.character_id != character_id or entry.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        entry.title = data.title.strip()[:50]
        entry.content = data.content.strip()
        entry.keywords = _json.dumps([str(k).strip() for k in data.keywords if str(k).strip()], ensure_ascii=False)
        entry.exclude_keywords = _json.dumps([str(k).strip() for k in data.exclude_keywords if str(k).strip()], ensure_ascii=False)
        entry.active = data.active
        entry.is_regex = bool(data.is_regex)
        entry.probability = max(0, min(100, int(data.probability or 100)))
        entry.inclusion_group = (data.inclusion_group or "").strip()[:50]
        entry.sticky_rounds = max(0, int(data.sticky_rounds or 0))
        entry.cooldown_rounds = max(0, int(data.cooldown_rounds or 0))
        await db2.commit()
    return {"ok": True, "id": entry_id}


@router.delete("/{character_id}/lorebook/{entry_id}")
async def delete_lorebook(
    character_id: int, entry_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除 Lorebook 条目（P1-2）"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.models.lorebook_entry import LorebookEntry
    async with async_session_factory() as db2:
        entry = await db2.get(LorebookEntry, entry_id)
        if entry is None or entry.character_id != character_id or entry.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        await db2.delete(entry)
        await db2.commit()
    return {"ok": True}


@router.get("/{character_id}/world-facts")
async def list_world_facts(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """世界事实列表（P1-3）：活跃事实，含作者与权威标记（用户定义的不可动摇设定优先展示）"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.models.world_fact import WorldFact
    async with async_session_factory() as db2:
        rows = (await db2.execute(
            select(WorldFact)
            .where(WorldFact.character_id == character_id, WorldFact.user_id == user_id, WorldFact.status == "active")
            .order_by(WorldFact.is_authoritative.desc(), WorldFact.asserted_at.desc())
            .limit(50)
        )).scalars().all()
    return {"items": [
        {
            "id": r.id, "subject_type": r.subject_type, "subject_id": r.subject_id,
            "predicate": r.predicate, "object_value": r.object_value,
            "author": r.author, "is_authoritative": bool(r.is_authoritative),
            "epistemic_status": r.epistemic_status,
            "asserted_at": r.asserted_at.isoformat() if r.asserted_at else None,
        } for r in rows
    ]}


class _WorldFactCreate(BaseModel):
    content: str
    predicate: str = "setting"


@router.post("/{character_id}/world-facts")
async def create_world_fact(
    character_id: int, data: _WorldFactCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """创建用户定义的权威世界设定（P1-3）：不可动摇事实，AI 推断不能覆盖"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.events.facts import assert_fact
    text = (data.content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "character_not_found"))
    predicate = (data.predicate or "setting").strip()[:20] or "setting"
    fid = await assert_fact(
        subject_type="character", subject_id=character_id, predicate=predicate,
        object_value=text, user_id=user_id, character_id=character_id,
        audience=[("user", user_id), ("char", character_id)],
        epistemic_status="FACT", confidence=1.0,
        source="user_setting", author="user", is_authoritative=True,
        ttl_minutes=None,
    )
    if fid is None:
        raise HTTPException(status_code=500, detail=tr_lang(lang, "character_not_found"))
    return {"ok": True, "id": fid}


@router.delete("/{character_id}/world-facts/{fact_id}")
async def delete_world_fact(
    character_id: int, fact_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除世界事实（P1-3）：仅用户自己创建的权威设定可删（系统/聊天折叠事实不可删，防误操作）"""
    await _get_owned_character(db, character_id, user_id, lang)
    from app.models.world_fact import WorldFact
    async with async_session_factory() as db2:
        f = await db2.get(WorldFact, fact_id)
        if f is None or f.character_id != character_id or f.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        if f.author != "user":
            raise HTTPException(status_code=403, detail=tr_lang(lang, "character_not_found"))
        f.status = "expired"
        await db2.commit()
    return {"ok": True}


@router.get("/{character_id}/state-history")
async def get_state_history(
    character_id: int,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """八维状态历史快照（Phase 2）：每次聊天评估后的状态，按时间倒序，供情绪曲线/蛛网对比"""
    await _get_owned_character(db, character_id, user_id, lang)
    from datetime import timedelta
    from app.models.character_state_history import CharacterStateHistory
    from sqlalchemy import select
    days = max(1, min(int(days or 30), 180))
    start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(CharacterStateHistory)
            .where(CharacterStateHistory.character_id == character_id, CharacterStateHistory.created_at >= start)
            .order_by(CharacterStateHistory.created_at.desc())
            .limit(20)  # 只保留最近 20 次快照（趋势图/蛛网对比）
        )).scalars().all()
    keys = ("mood", "body_temp", "desire", "possessiveness", "fatigue", "sensitivity", "comfort", "anger")
    return {
        "character_id": character_id,
        "days": days,
        "points": [
            {
                "id": r.id, "source": r.source,
                "values": {k: getattr(r, k) for k in keys},
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
