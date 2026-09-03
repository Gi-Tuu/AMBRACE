"""聊天服务：管理消息收发，集成 Agent"""
from app.utils.logger import get_logger
from sqlalchemy import select, func, and_, or_
from app.db.database import async_session_factory
import asyncio
import json
from app.utils.async_tasks import spawn_background
import time
from datetime import datetime, timezone

from app.agent import actions as _agent_actions

_logger = get_logger("services.chat")
from app.models.chat import ChatSession
from app.models.chat import ChatMessage
from app.models.character import AICharacter
from app.agent.graph import agent
from app.memory import add_chat_memory_extraction
from app.memory.extractor import SELF_STATEMENT_MAX_LEN
from app.application.chat.tools import (
    _extract_gen_image,
    _extract_search,
    _search_throttle,
    _search_inject_enabled,
    _polish_search_query as _polish_search_query,
    _run_web_search,
    _extract_cal_note,
    _extract_memo,
    _save_calendar_note as _save_calendar_note,
    _save_memo_note as _save_memo_note,
    _execute_note_tool as _execute_note_tool,
    _save_phone_desktop_notes,
    _gen_image_flow,
)
from app.application.chat.io import (
    _append_ai_image_message as _append_ai_image_message,
    _append_ai_text_message as _append_ai_text_message,
    _push_ws_ai_message as _push_ws_ai_message,
    _push_user_notify as _push_user_notify,
)
from app.application.chat.streaming import (
    _assemble_chunk_meta as _assemble_chunk_meta,
    _update_chunk_meta as _update_chunk_meta,
    _delete_chunks as _delete_chunks,
    _synthesize_chunks_tts as _synthesize_chunks_tts,
    _backfill_stream_tts_meta as _backfill_stream_tts_meta,
    _persist_ai_chunks as _persist_ai_chunks,
    send_and_receive_stream as send_and_receive_stream,
)


async def get_latest_session_id(user_id: int | None, character_id: int) -> int | None:
    """选择该角色真正活跃的会话：按最新一条消息时间排序（无消息回退创建时间/ID）。

    历史坑：mark_session_read 联动标记已读曾触发 ORM onupdate 污染 updated_at，
    同角色多会话 updated_at 相同时按 updated_at 排序会选错会话（表现为聊天记录"只剩早期"）。
    现在统一以消息时间为准。
    """
    conds = [
        ChatSession.character_id == character_id,
        ChatSession.is_active == True,
    ]
    if user_id is not None:
        conds.append(ChatSession.user_id == user_id)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChatSession.id, func.max(ChatMessage.created_at).label("last_msg_at"))
            .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(*conds)
            .group_by(ChatSession.id)
            .order_by(
                func.coalesce(func.max(ChatMessage.created_at), ChatSession.created_at).desc(),
                ChatSession.id.desc(),
            )
            .limit(1)
        )
        row = result.first()
        return row[0] if row else None


async def create_session(user_id: int, character_id: int) -> dict:
    """获取或创建聊天会话，优先复用最新活跃会话（按最新消息时间选会话）"""
    existing_id = await get_latest_session_id(user_id, character_id)
    if existing_id:
        _logger.debug("Reusing existing session: id=%d", existing_id)
        return {"id": existing_id, "character_id": character_id, "greeting": None}

    async with async_session_factory() as db:

        session = ChatSession(user_id=user_id, character_id=character_id)
        db.add(session)
        await db.flush()
        await db.refresh(session)

        result = await db.execute(
            select(AICharacter).where(AICharacter.id == character_id)
        )
        char = result.scalar_one_or_none()
        greeting = char.greeting_message if char and char.greeting_message else ""

        if greeting:
            greeting_msg = ChatMessage(
                session_id=session.id, sender_type="ai", content=greeting,
            )
            db.add(greeting_msg)
            await db.flush()
            await db.commit()
        await db.commit()
        _logger.info("New session created: id=%d char=%d greeting=%s", session.id, character_id, bool(greeting))
        return {"id": session.id, "character_id": character_id, "greeting": greeting}


async def _save_bio_update(character_id: int, bio_text: str, user_id: int):
    """保存自述更新到角色表（写入独立自述字段，不覆盖用户提供的背景信息 bio）"""
    if not bio_text:
        return
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if char:
                char.self_statement = bio_text[:SELF_STATEMENT_MAX_LEN]
                await db.flush()
                await db.commit()
                _logger.info("Bio updated for character %d: %.60s", character_id, bio_text)
    except Exception as e:
        _logger.warning("Bio update failed: %s", e)


# 进程内去重：已生成过初始自述的角色不再重复触发
_initial_bio_done: set[int] = set()


async def _generate_initial_bio(character_id: int, user_id: int) -> None:
    """角色无自述时，用 LLM 依据人格/风格/关系生成初始自述（异步、失败静默）"""
    if character_id in _initial_bio_done:
        return
    try:
        from app.agent.llm_client import chat_completion, get_user_llm_config
        async with async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if not char or (char.self_statement and char.self_statement.strip()):
                _initial_bio_done.add(character_id)
                return
            cfg = await get_user_llm_config(user_id)
            prompt = (
                "你是角色设定撰写助手。请以第一人称写一段简短自述（60 字以内），"
                "说明这个角色是谁、性格特点、与用户的关系。只输出自述正文，不要任何前缀或解释。\n"
                f"角色名：{char.name or 'AI'}\n"
                f"人格：{char.personality or '暂无'}\n"
                f"聊天风格：{char.chat_style or '暂无'}\n"
                f"与用户的关系：{char.relationship_summary or '普通朋友'}（类型：{char.relation_type or '朋友'}）"
            )
            text = (await chat_completion(
                [{"role": "user", "content": prompt}],
                max_tokens=200, task="card", **(cfg or {}),
            ) or "").strip().strip('\"「」”')
            if text:
                char.self_statement = text[:SELF_STATEMENT_MAX_LEN]
                await db.flush()
                await db.commit()
                _logger.info("Initial bio generated for character %d: %.60s", character_id, text)
            _initial_bio_done.add(character_id)
    except Exception as e:
        _logger.warning("Initial bio generation failed char=%d: %s", character_id, e)


async def _save_status_update(character_id: int, status_text: str, user_id: int):
    """保存当前状态更新到角色表，同时存入记忆库"""
    if not status_text:
        return
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(AICharacter).where(AICharacter.id == character_id))
            char = result.scalar_one_or_none()
            if char:
                char.current_status = status_text[:200]
                await db.flush()
                await db.commit()
                _logger.info("Status updated for character %d: %.60s", character_id, status_text)
        # 同时存入记忆
        try:
            from app.memory import save_memory
            await save_memory(
                user_id=user_id,
                character_id=character_id,
                memory_type="insight",
                content=f"状态更新: {status_text[:200]}",
                importance=2,
                sub_type="status",
                source="status",
                speaker_type="character", speaker_id=character_id,
                epistemic_status="FACT",
            )
        except Exception as e:
            _logger.warning("Failed to save status as memory: %s", e)
        # 世界状态折叠（P4）：状态更新 → 当前世界事实（失败静默）
        try:
            from app.events.facts import fold_status_update
            await fold_status_update(character_id, user_id, status_text)
        except Exception as e:
            _logger.warning("World fact fold status failed: %s", e)
    except Exception as e:
        _logger.warning("Status update failed: %s", e)


def _trigger_state_eval(character_id: int, user_id: int, user_msg: str, ai_response: str, status_update):
    """异步触发八维状态评估（fire-and-forget，失败不影响聊天）"""
    try:
        from app.application.character_state_service import update_character_states
        spawn_background(update_character_states(
            character_id, user_id,
            user_msg or "", ai_response or "",
            status_update or "",
        ))
    except Exception as e:
                _logger.warning("State eval trigger failed: %s", e)


async def _bump_relationship(character_id: int, user_id: int) -> None:
    """认知循环 v2.1：用户主动发消息 → 关系标量小幅上升（信任+2、依恋+1，封顶 100；失败静默）"""
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character import CharacterState
        async with async_session_factory() as db:
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
            if st is None:
                return
            changed = False
            if int(st.trust or 50) < 100:
                st.trust = min(100, int(st.trust or 50) + 2)
                changed = True
            if int(st.attachment or 50) < 100:
                st.attachment = min(100, int(st.attachment or 50) + 1)
                changed = True
            if changed:
                await db.commit()
                _logger.info("Relationship bump char=%d trust=%d attachment=%d",
                             character_id, st.trust, st.attachment)
    except Exception as e:
        _logger.warning("Relationship bump failed char=%d: %s", character_id, e)


async def _load_reasoning_level(character_id: int) -> int:
    """读取角色「思考过程」挡位：0=关闭 / 1=简单思考 / 2=深度思考"""
    try:
        from app.models.character import ProactiveSettings
        async with async_session_factory() as db:
            row = (await db.execute(
                select(ProactiveSettings.reasoning_level)
                .where(ProactiveSettings.character_id == character_id)
            )).scalar_one_or_none()
        return int(row or 0)
    except Exception as e:
        _logger.warning("Load reasoning_level failed char=%d: %s", character_id, e)
        return 0


async def _cold_war_block(character_id: int, user_id: int, user_msg: str) -> bool:
    """冷战拦截（v3）：角色生气冷战期，用户发消息不回复；哄好关键词可提前解除。

    返回 True = 拦截（不生成 AI 回复）；False = 正常回复（无冷战或已哄好/自动恢复）。
    """
    try:
        # v5-B：吃醋/疲惫剧情线的用户哄/安慰通道（落和好/恢复节点，不拦截回复）
        from app.scheduling.storyline_engine import maybe_resolve_storyline_by_message
        await maybe_resolve_storyline_by_message(character_id, user_id, user_msg)
        from app.scheduling.state_triggers import check_cold_war, resolve_cold_war_by_message
        if await check_cold_war(character_id, user_id):
            _r = await resolve_cold_war_by_message(character_id, user_id, user_msg)
            if _r == 1:
                _logger.info("Cold war resolved char=%d by user message, replying normally", character_id)
                return False
            # v5 增强 ①：敷衍道歉 → 角色更冷回应（每剧情线一次，防重）
            if _r == 4:
                from app.scheduling.storyline_engine import run_dismissive_cold_reply
                try:
                    await run_dismissive_cold_reply(character_id, user_id)
                except Exception as _e:
                    _logger.warning("Dismissive cold reply call failed char=%d: %s", character_id, _e)
            # v5 增强 ③：关系恶化支线——用户持续敷衍（>=2 次）或冷战超长（>=6h）且占有维高
            try:
                from app.scheduling.storyline_engine import run_deteriorate_arc
                from app.scheduling.state_triggers import cold_war_deteriorate_triggered
                if await cold_war_deteriorate_triggered(character_id, user_id):
                    await run_deteriorate_arc(character_id, user_id)
            except Exception as _e2:
                _logger.warning("Deteriorate arc call failed char=%d: %s", character_id, _e2)
            _logger.info("Cold war block char=%d (no reply, level=%d)", character_id, _r)
            return True
    except Exception as e:
        _logger.warning("Cold war check failed char=%d: %s", character_id, e)
    return False


async def _persist_user_message(
    session_id: int, user_id: int, character_id: int, content: str,
    quote: dict | None = None, save_user_message: bool = True,
    shared_memory: bool = False,
) -> tuple[int | None, dict | None]:
    """用户消息落库，返回 (user_msg_id, user_msg_info)。

    save_user_message=False 跳过落库；shared_memory=True 触发 Shared Memory 标记（chunked）。
    """
    user_msg_id = None
    user_msg_info = None
    if not save_user_message:
        return user_msg_id, user_msg_info
    async with async_session_factory() as db:
        _q = None
        if quote and isinstance(quote, dict):
            _q = json.dumps({"quote": quote}, ensure_ascii=False)
        um = ChatMessage(session_id=session_id, sender_type="user", content=content,
                         extra_meta=_q)
        db.add(um)
        await db.flush()
        user_msg_id = um.id
        # P-fix（2026-08-31）：SSE 流式路径经本函数落用户消息时补刷新 chat_sessions.updated_at，
        # 与 chunked/主动路径一致（agent 落库处同样写 updated_at=now naive UTC）。
        _sess = (await db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )).scalar_one_or_none()
        if _sess:
            _sess.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        user_msg_info = {
            "id": um.id, "session_id": session_id, "sender_type": "user",
            "content": um.content, "created_at": um.created_at.isoformat(),
            "extra_meta": um.extra_meta,
        }
    # Shared Memory（Phase C，2026-08-14）：用户消息含“记住/第一次/纪念日”等标记意图 → 异步创建共同经历
    if shared_memory:
        try:

            async def _mark_shared():
                try:
                    from app.memory.shared_events import maybe_create_shared_event
                    async with async_session_factory() as _db2:
                        await maybe_create_shared_event(_db2, user_id, character_id, content)
                except Exception as _e:
                    _logger.warning("shared event mark failed: %s", _e)

            spawn_background(_mark_shared())
        except Exception:
            pass
    return user_msg_id, user_msg_info


async def _resolve_emotional_state(character_id: int, snapshot: dict | None = None) -> str:
    """P2-1：取角色八维状态 → 推出 TTS 情感标签（供 final_state 使用）。

    M1-S10：可传入本轮快照（character_states_snapshot）免重复查库；无快照回退自行查询。
    失败/无状态/异常一律返回空串（零行为变化，不抛断主链路）。
    """
    try:
        _cs = snapshot
        if _cs is None:
            from app.application.character_state_service import get_character_states
            _cs = await get_character_states(character_id)
        from app.domain.emotion.model import emotion_from_character_states
        return emotion_from_character_states(_cs) or ""
    except Exception as e:
        _logger.warning("Emotion state resolve failed char=%d: %s", character_id, e)
        return ""


async def _run_agent_core(
    session_id: int, user_id: int, character_id: int, content: str,
    lang: str, user_msg_id: int | None,
    *,
    user_timer: bool = False,
    search_loop: bool = False,
    run_chat_task: bool = False,
    trace_route: str = "chunked",
    stream_sink=None,
    tts: bool = False,
    stream_tts_ctx: dict | None = None,
    reply_delay: bool = False,
) -> dict | None:
    """公共 Agent 主流程（双路径收敛 #45，以 chunked 版逻辑为基准）。

    stream_sink 非空时注入 LangGraph state 走真流式生成（见 nodes.generate_response 的
    stream 分支）；流式块/增量经 sink 推给调用方。返回 None 表示冷战拦截；成功返回
    final_state/final_text/gen_prompt/img_text/cal_note_text/memo_text（+ streamed/stream_blocks）。
    """
    # 冷战拦截（v3）：不生成回复
    if await _cold_war_block(character_id, user_id, content):
        return None

    # #63 机制5：用户安慰词 → 最高权重心事减重（flag 开才生效，失败静默）
    try:
        from app.life.preoccupations import has_comfort_word
        if has_comfort_word(content):
            from app.agent.loop import AGENT_FLAGS
            if AGENT_FLAGS.get("preoccupation_enabled", False):
                from app.life.preoccupations import soften_by_comfort_words
                async with async_session_factory() as _pdb:
                    await soften_by_comfort_words(
                        _pdb, user_id=user_id, character_id=character_id, content=content,
                    )
                    await _pdb.commit()
    except Exception:
        pass

    # 主动到期复习成功判定（P1）：用户回复与 24h 内复习消息弱相关 → 强化（异步不阻塞）
    try:
        from app.scheduling.memory_review import maybe_review_success
        spawn_background(maybe_review_success(user_id, character_id, content))
    except Exception:
        pass

    # AI 情绪关怀：检测用户低落情绪 → 登记延迟主动关心任务（异步不阻塞）
    try:
        from app.domain.emotion.model import detect_user_emotion
        if "低落" in detect_user_emotion(content):
            from app.domain.emotion.care import register_care_task
            spawn_background(register_care_task(user_id, character_id, content))
    except Exception:
        pass

    # 用户时间承诺解析（用户说"我去洗澡20分钟回来"等 → 创建定时事件，到点 AI 主动问；2026-08-14 修复）
    if user_timer:
        try:
            from app.scheduling.promise_parser import extract_timer
            from app.scheduling.promise_service import create_event
            user_timer_info = extract_timer(
                content, user_id=user_id, character_id=character_id,
                session_id=session_id, source_message_id=user_msg_id, sender="user",
            )
            if user_timer_info:
                await create_event(user_timer_info)
        except Exception as e:
            _logger.warning("User timer parse failed: %s", e)

    # M1-S10（2026-08-31）：一轮只查一次八维状态——回复延迟/情感标签/上下文 life_share 复用同一快照
    # （原路径每轮最多 3 次查询：延迟 1 + 情感 1 + life_share trust 1；失败 None 走各处兜底）
    try:
        from app.application.character_state_service import get_character_states as _get_states_once
        _cs_snapshot = await _get_states_once(character_id)
    except Exception:
        _cs_snapshot = None

    initial_state = {
        "user_message": content, "character_id": character_id,
        "user_id": user_id, "session_id": session_id, "intent": "",
        "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "",
        "should_update_memory": False, "new_memories": [], "emotional_state": "",
        "bio_update": None, "status_update": None,
        "source_id": user_msg_id,
        "lang": lang,
        "reasoning_level": await _load_reasoning_level(character_id),
        "tools_used": [],
        "stream_sink": stream_sink,
        "tts": tts,
        "voice_params": (stream_tts_ctx or {}).get("voice_params", {}) if stream_tts_ctx else {},
        "tts_subdir": (stream_tts_ctx or {}).get("tts_subdir") if stream_tts_ctx else None,
        "block_sink": (stream_tts_ctx or {}).get("block_sink") if stream_tts_ctx else None,
        "character_states_snapshot": _cs_snapshot,
    }

    _t0 = time.monotonic()
    # #63 机制2：用户主动消息的动态回复延迟（flag 开才生效；voice/tts 跳过；冷战已在上层拦截）
    if reply_delay and not tts:
        try:
            from app.agent.loop import AGENT_FLAGS
            if AGENT_FLAGS.get("reply_delay_enabled", False) and _cs_snapshot is not None:
                from app.utils.reply_delay import calc_typing_delay, estimate_response_chars
                _st = _cs_snapshot  # M1-S10：复用本轮快照，不再单独查库
                _delay = calc_typing_delay(
                    estimate_response_chars(len(content)),
                    mood=_st.get("mood") or 50,
                    fatigue=_st.get("fatigue") or 50,
                    anger=_st.get("anger") or 50,
                    is_short_reply=len(content) <= 6,
                )
                if stream_sink is not None:
                    await stream_sink("typing", {"is_typing": True, "delay": _delay})
                await asyncio.sleep(_delay)
        except Exception:
            pass  # 失败静默，不阻塞回复

    # P2-1 情感语音闭环（#71）：回复前用角色八维状态推出 TTS 情感标签写入 emotional_state，
    # 供流式逐句合成 / split_response / TTS emotion 共用；失败/异常保持空串（零行为变化）。
    # M1-S10：复用本轮快照（无独立查库）；快照缺失时回退原自行查询。
    initial_state["emotional_state"] = await _resolve_emotional_state(character_id, snapshot=_cs_snapshot)

    final_state = await agent.ainvoke(initial_state)

    # 真流式标记 / 标记元数据源文本（流式时原始响应含全部标记，用于 trace/生图/日历/备忘提取）
    _is_stream = bool(final_state.get("streamed"))
    _source_text = final_state.get("raw_response") if _is_stream else (final_state.get("ai_response") or "")

    # Task Trace（Phase A，2026-08-16）：快照原始动作（标记未剥离前），流程结束统一落库（先只写不读）
    _trace_actions = _agent_actions.parse_actions(_source_text)
    _trace_llm_calls = 1
    _trace_searched = False

    full_text = final_state.get("ai_response") or ""
    _loop_steps: list[dict] = []

    # Ariadne 模块 B（2026-09-04）：记忆二跳（非流式；记忆是内生信息，优先于外网搜索）。
    # flag memory_recall_second_hop 默认关=循环内只剥离 [RECALL] 标记（零行为变化）；流式路径只剥离不中途二跳
    #（与 SEARCH/MCP 同策略：流式输出已定型，再决策需额外推送通道，沿用既有延期结论）。
    if not _is_stream:
        try:
            from app.agent import loop as _agent_loop

            async def _recall_gate() -> bool:
                """角色关了记忆 v2 则不开放二跳（只在出现 [RECALL] 标记后才查询，常态零开销）"""
                async with async_session_factory() as _gdb:
                    _gchar = (await _gdb.execute(
                        select(AICharacter.memory_v2_enabled).where(AICharacter.id == character_id)
                    )).scalar_one_or_none()
                return bool(_gchar)

            # F-3（2026-09-04）：透传用户时区分钟偏移给二跳解析（「时间=YYYY-MM」绝对自然月
            # 不受影响）。仅当二跳 flag 开才取偏移（默认关=零额外查询，避免常态下多一次 DB 读）。
            _recall_tz = None
            if bool(_agent_loop.AGENT_FLAGS.get("memory_recall_second_hop", False)):
                from app.utils.usertz import get_user_tz_offset_min
                _recall_tz = await get_user_tz_offset_min(user_id)
            final_state, _recall_steps = await _agent_loop.run_recall_loop(
                final_state,
                user_id=user_id,
                character_id=character_id,
                gate=_recall_gate,
                tz_offset_min=_recall_tz,
            )
            if _recall_steps:
                # 二跳固定至多 1 次再生成（有 RECALL 步骤即 +1 次 LLM 调用，与 SEARCH 计数口径一致）
                _trace_llm_calls = (_trace_llm_calls or 1) + 1
        except Exception as e:
            _logger.warning("AI recall second-hop failed: %s", e)
            try:
                from app.agent.actions import extract_recall as _extract_recall_ns
                final_state["ai_response"] = _extract_recall_ns(final_state.get("ai_response") or "")[0]
            except Exception:
                pass
        full_text = final_state.get("ai_response") or ""

    if search_loop:
        # AI 自主搜索（Phase B，2026-08-16）：受控 Loop（decide→execute→observe→条件再决策；最多 2 次搜索/3 次 LLM）
        try:
            from app.agent import loop as _agent_loop

            async def _save_browser_history(_char_id: int, _query: str) -> None:
                """搜索成功落小手机浏览记录（角色记得自己搜过；同词刷新时间）"""
                from app.models.device import BrowserHistory
                async with async_session_factory() as _db:
                    _ex = (await _db.execute(
                        select(BrowserHistory).where(
                            BrowserHistory.character_id == _char_id,
                            BrowserHistory.query == _query[:200],
                        )
                    )).scalar_one_or_none()
                    if _ex is not None:
                        _ex.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    else:
                        _db.add(BrowserHistory(character_id=_char_id, query=_query[:200]))
                    await _db.commit()

            final_state, _loop_steps = await _agent_loop.run_search_loop(
                final_state,
                user_id=user_id,
                character_id=character_id,
                run_search=_run_web_search,
                throttle=_search_throttle,
                inject_enabled=_search_inject_enabled,
                save_history=_save_browser_history,
            )
            _trace_searched = bool(_loop_steps)
            if _loop_steps:
                # 只有搜索成功才触发再决策（regen），失败轮次不计入 LLM 调用
                _trace_llm_calls = 1 + sum(1 for _s in _loop_steps if _s.get("ok"))
        except Exception as e:
            _logger.warning("AI web search loop failed: %s", e)
            try:
                final_state["ai_response"] = _extract_search(final_state.get("ai_response", ""))[0]
            except Exception:
                pass
        full_text = final_state.get("ai_response") or ""
    else:
        # AI 自主搜索（流式路径暂不触发，仅剥离标记兜底，避免打断流式输出）
        try:
            full_text = _extract_search(full_text)[0]
        except Exception:
            pass
        # Ariadne 模块 B：流式路径 [RECALL] 同策略——仅剥离标记兜底
        try:
            from app.agent.actions import extract_recall as _extract_recall_s
            full_text = _extract_recall_s(full_text)[0]
        except Exception:
            pass

    # MCP 工具执行（Phase 2）：LLM 输出 mcp.* 标记 → ToolRunner.execute + 再决策。
    # 仅在 context_builder 注入过 MCP 工具声明后才可能触发（默认无 mcp.* 标记 → 零行为变化）。
    # Phase 3（2026-08-27）评估：SSE 真流式路径仍不触发 run_mcp_tool_stage（与搜索一致），
    # 仅兜底剥离标记。原因：流式输出已推送给客户端（stream_blocks/stream_saved 已定型），
    # 此时再跑 MCP 工具 + _regen2 再决策会产出第二条回复但原 SSE 连接已结束，需额外推送通道，
    # 且会改变严格断言流式片段的现有测试契约。留待 Phase 4 单独接入后推送通道。
    # Phase 4（2026-08-28）评估结论：仍延期。真流式路径（_is_stream）若要执行 MCP 工具循环，
    # 需要 (a) 从 raw_response（非已剥离的 ai_response）解析 mcp.* 标记；
    # (b) 再决策 _regen2 会再次走流式（state 仍带 stream_sink），导致 delta 二次推送、
    #    stream_blocks/raw_response 被第二条回复覆盖，需额外拼接两段块/原始文本；
    # (c) tts 路径（block_sink 实时落库 + 逐句 TTS）在首条回复时已消费完毕，第二条回复需
    #    重开 TTS 流水线并再次落库，改动显著；且会改变 test_chat_stream 严格断言的事件序列。
    # A1（#59 流式路径 MCP 工具循环）：接入见 streaming.py send_and_receive_stream —— 流式路径
    #   从 raw_response 解析 mcp.* 标记并用 run_stream_mcp_tool_stage 执行，工具结果经独立流尾
    #   事件 tool_result 推给前端；为避免再决策的流式冲突（delta 二次推送/stream_blocks 覆盖/
    #   TTS 流水线已消费），流式路径不做二次 LLM 再决策（非流式保留原再决策行为）。
    if not _is_stream:
        try:
            from app.agent.mcp_tools import run_mcp_tool_stage
            _mcp_src = full_text or (final_state.get("ai_response") or "")
            _has_mcp = any(
                a.action_type.startswith("mcp.")
                for a in _agent_actions.parse_actions(_mcp_src)
            )
            if _has_mcp and await run_mcp_tool_stage(
                final_state, _loop_steps,
                user_id=user_id, character_id=character_id, session_id=session_id,
            ):
                from app.agent.nodes import generate_response as _regen2
                final_state = await _regen2(final_state)
                full_text = final_state.get("ai_response") or ""
                # 再决策输出如仍含 mcp.* 标记（内部调用标签）则剥离，避免泄漏到展示正文
                from app.agent.actions import _MCP_TOOL_RE as _mcp_re
                full_text = _mcp_re.sub("", full_text).strip()
        except Exception as e:
            _logger.warning("MCP tool stage failed: %s", e)
            try:
                from app.agent.actions import strip_actions as _strip_mcp
                full_text = _strip_mcp(full_text or "")
            except Exception:
                pass

    # 生图标记提取（聊天内AI发图）：清理标记后落库，异步生图
    # 提取源：流式用原始响应（展示文本已剥全标记）/ 非流式沿用 full_text（与旧行为一致），
    # 仅非流式回写 full_text，且从「已处理展示文本」剥离（避免经源文本回写时把已剥离的 SEARCH 等
    # 标记重新带回来）
    _marker_src = _source_text if _is_stream else full_text
    clean_text, gen_prompt, img_text = _extract_gen_image(_marker_src)
    if gen_prompt and not _is_stream:
        full_text = _extract_gen_image(full_text)[0]

    # Task Trace（Phase A）：写 trace（先只写不读；失败静默）
    try:
        from app.agent import trace as _trace
        _trace.enqueue_task_log(
            task_id=_trace.new_task_id(),
            character_id=character_id,
            user_id=user_id,
            session_id=session_id,
            trigger="chat",
            route=("search_loop" if _trace_searched else "direct") if search_loop else trace_route,
            steps_json=json.dumps(_agent_actions.actions_to_steps(_trace_actions) + _loop_steps, ensure_ascii=False),
            llm_calls=_trace_llm_calls,
            tool_calls=len(_trace_actions) + len(_loop_steps),
            latency_ms=int((time.monotonic() - _t0) * 1000),
            status="ok",
        )
    except Exception as _te:
        _logger.warning("Task trace failed: %s", _te)

    # Phase H：工具轮次任务化（≥1 个明确工具/备忘动作 → agent_tasks 任务记录；只记不改行为，失败静默；仅 HTTP 路径）
    if run_chat_task:
        try:
            from app.agent.task_engine import run_chat_task as _run_chat_task
            _all_steps = _agent_actions.actions_to_steps(_trace_actions) + _loop_steps
            if len(_all_steps) >= 1:
                spawn_background(_run_chat_task(
                    character_id, user_id, session_id, content,
                    _all_steps, full_text, True,
                ))
        except Exception:
            pass

    # 日历/备忘录标记提取（Phase F 收口，2026-08-16）：提取结果供 chunked extra_meta 前端小字展示；
    # 落库统一走 _save_phone_desktop_notes（→ execute_tool，去重/署名），文本用原始含标记版本
    _cal_note_text = None
    _memo_text = None
    _marker_src2 = _source_text if _is_stream else full_text
    try:
        _cal = _extract_cal_note(_marker_src2)
        if _cal:
            _cal_note_text = _cal[1]
    except Exception:
        pass
    try:
        _memo_text = _extract_memo(_marker_src2)
    except Exception:
        _memo_text = None
    try:
        spawn_background(_save_phone_desktop_notes(character_id, _marker_src2))
    except Exception:
        pass

    # 定时承诺解析（AI 侧）：检测 [timer:xx] 或"洗n分钟澡"等时间承诺 → 创建定时事件；
    # HTTP 路径额外剥离全部动作标记（记忆/自述/状态等），流式路径仅剥离定时标签
    try:
        from app.scheduling.promise_parser import extract_timer, strip_timer_tag
        from app.scheduling.promise_service import create_event
        timer_info = extract_timer(
            (_source_text if _is_stream else full_text),
            user_id=user_id, character_id=character_id,
            session_id=session_id, source_message_id=user_msg_id, sender="ai",
        )
        if search_loop:
            from app.agent.actions import strip_actions as _strip_actions
            full_text = strip_timer_tag(_strip_actions(full_text) or "") or ""
        else:
            full_text = strip_timer_tag(full_text)
        if timer_info:
            await create_event(timer_info)
    except Exception as e:
        _logger.warning("AI tag strip failed: %s", e)
    final_state["ai_response"] = full_text

    return {
        "final_state": final_state,
        "final_text": full_text,
        "gen_prompt": gen_prompt,
        "img_text": img_text,
        "cal_note_text": _cal_note_text,
        "memo_text": _memo_text,
        "streamed": _is_stream,
        "stream_blocks": final_state.get("stream_blocks") or [],
        "stream_saved": final_state.get("stream_saved") or [],
    }


def _schedule_reliability_fact_check(character_id: int, user_id: int, content: str, final_text: str) -> None:
    """P5：记忆可靠度信号（确认/纠正，$0 规则）+ 异步事实核查（节流，失败静默）。

    G-P2-1（2026-08-18）：HTTP 与 WS chunked 双路径共用的公共调用（纯异步 fire-and-forget，
    不阻塞推送；可靠性/事实核查结果只写元数据，不影响已推送文本）。
    """
    try:
        from app.memory.reliability import schedule_feedback_processing
        schedule_feedback_processing(character_id, user_id, content, final_text)
    except Exception:
        pass
    try:
        from app.memory.fact_check import schedule_fact_check
        schedule_fact_check(character_id, user_id, content, final_text)
    except Exception:
        pass


async def _run_post_processing(
    session_id: int, user_id: int, character_id: int, content: str,
    final_state: dict, final_text: str, ai_message_id: int | None,
    user_msg_id: int | None,
    *,
    reliability: bool = False,
    gen_prompt: str | None = None,
    img_text: str | None = None,
) -> None:
    """AI 回复落库后的公共收尾（反思/自述/状态/记忆/话题/复习/关系/状态评估/可靠度/生图；失败静默）。"""
    # 认知循环 v2.1：反思结果落库（节点已计算，拿第一条 AI 消息 id 记录；失败静默）
    try:
        from app.agent.reflection import persist_reflection
        if final_state.get("reflection_result") and ai_message_id:
            await persist_reflection(character_id, user_id, ai_message_id, final_state["reflection_result"])
    except Exception as e:
        _logger.warning("Reflection persist failed: %s", e)

    await _save_bio_update(character_id, final_state.get("bio_update"), user_id)
    await _save_status_update(character_id, final_state.get("status_update"), user_id)
    spawn_background(_generate_initial_bio(character_id, user_id))

    # 触发记忆提取
    spawn_background(add_chat_memory_extraction(
        session_id, character_id, user_id, content, final_text,
        source_id=user_msg_id,
    ))

    # M2-S5（2026-08-31）：标记截断保底——A 通道标记被 max_tokens 截断（尾部未闭合标记）时，
    # 本条源消息立即补走一次通道 B 提取（不等凑批；save_memory 写侧查重防重复），并从批量队列
    # 移除该源防重复提取。flag marker_recovery 关=仅批量补提（现状）。
    if final_state.get("marker_truncated") and user_msg_id:
        try:
            from app.agent.loop import AGENT_FLAGS as _af
            if _af.get("marker_recovery", True):
                async def _priority_extract():
                    try:
                        from app.memory.extractor import extract_single, _pending_remove_uid
                        await extract_single(
                            session_id, character_id, user_id, content, final_text,
                            source_id=user_msg_id,
                        )
                        _pending_remove_uid(session_id, user_msg_id)
                    except Exception as _pe:
                        _logger.warning("Priority extraction failed src=%s: %s", user_msg_id, _pe)
                spawn_background(_priority_extract())
        except Exception:
            pass

    # 认知循环 v2.1：对话话题追踪（本地提取+节流；失败静默）
    try:
        from app.agent.topic_tracker import maybe_extract_topics
        spawn_background(maybe_extract_topics(
            character_id, user_id, content, final_text,
            perception=final_state.get("perception"),
        ))
    except Exception:
        pass

    # Life Loop v1.1（2026-08-26）：聊天→生活意图提取（本地规则，失败静默；不阻塞回复）
    try:
        from app.agent.loop import AGENT_FLAGS
        if AGENT_FLAGS.get("life_chat_driven_enabled", False):
            from app.life.chat_intent import extract_life_intent
            spawn_background(extract_life_intent(character_id, user_id, content))
    except Exception:
        pass

    # Life Loop v1.1（2026-08-26）：刷新用户在场时间（life_state.last_user_interaction_at）
    try:
        from app.life.life_state import get_life_state

        async def _bump_user_presence(character_id: int = character_id):
            async with async_session_factory() as db:
                st = await get_life_state(db, character_id)
                st.last_user_interaction_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db.commit()

        spawn_background(_bump_user_presence())
    except Exception:
        pass

    # 记忆架构 v2.1 Phase 4b：情境驱动复习——感知 deep/emotion 或命中进行中目标 → 入队候选（异步，失败静默）
    try:
        from app.scheduling.memory_review import queue_contextual_review_for
        spawn_background(queue_contextual_review_for(
            character_id, user_id, content, final_state.get("perception"),
        ))
    except Exception:
        pass

    # 认知循环 v2.1：用户发消息 → 关系标量互动加分（bump，失败静默）
    spawn_background(_bump_relationship(character_id, user_id))

    # 认知循环 v2.1：话题完成/搁置自动切换（本地零 LLM，失败静默）
    try:
        from app.agent.topic_tracker import update_topic_resolution
        spawn_background(update_topic_resolution(character_id, user_id, content))
    except Exception:
        pass

    # 异步评估八维可视化状态（10 分钟节流，不阻塞回复）
    _trigger_state_eval(character_id, user_id, content, final_text, final_state.get("status_update"))

    # P5：记忆可靠度信号（确认/纠正，$0 规则）+ 异步事实核查（节流，失败静默；G-P2-1：HTTP 与 chunked 共用）
    if reliability:
        _schedule_reliability_fact_check(character_id, user_id, content, final_text)

    # 生图：用户要求画图时异步生成并追加图片消息（开关在 context 注入层控制标记输出）
    if gen_prompt:
        spawn_background(_gen_image_flow(user_id, character_id, session_id, gen_prompt, img_text))

    # M3-a（2026-09-01）：工作记忆评估——turn 结束异步触发（flag 关/fail-open/30min 节流，
    # docs/设计_M3工作记忆_20260901.md §3；P1-2：实现前核验 _run_agent_core 收尾存在 ✓）
    try:
        from app.application.working_state_service import maybe_evaluate_working_state
        spawn_background(maybe_evaluate_working_state(
            user_id=user_id, character_id=character_id, session_id=session_id,
            user_text=content, ai_text=final_text,
        ))
    except Exception:
        pass


async def send_and_receive(
    session_id: int, user_id: int, character_id: int, content: str,
    lang: str = "zh", quote: dict | None = None, reply_delay: bool = True,
) -> dict:
    """发送用户消息 → Agent 处理 → 返回 AI 回复"""
    # 用户消息落库（HTTP 路径：无开关，始终落库；无 user_msg_info/Shared Memory）
    user_msg_id, _ = await _persist_user_message(
        session_id, user_id, character_id, content,
        quote=quote, save_user_message=True, shared_memory=False,
    )

    # 公共 Agent 主流程（HTTP 专属：用户定时承诺 / 自主搜索 Loop / 多工具任务化）
    core = await _run_agent_core(
        session_id, user_id, character_id, content, lang, user_msg_id,
        user_timer=True, search_loop=True, run_chat_task=True, reply_delay=reply_delay,
    )
    if core is None:
        return {"ai_message": None, "memories_updated": False, "cold_war": True}

    final_state = core["final_state"]
    final_text = core["final_text"]
    gen_prompt = core["gen_prompt"]
    img_text = core["img_text"]

    # 组装 AI 消息 extra_meta：思考过程 + 调用能力（生图/扩展等）
    _meta = {}
    _reasoning = (final_state.get("reasoning") or "").strip()
    if _reasoning:
        _meta["reasoning"] = _reasoning
    _tools = list(final_state.get("tools_used") or [])
    if gen_prompt:
        _tools.append("生图")
    if _tools:
        _meta["tools"] = _tools
    # 状态更新附到气泡（前端小字显示，2026-08-14）
    _st = (final_state.get("status_update") or "").strip()
    if _st:
        _meta["status_update"] = _st
    _ai_meta = json.dumps(_meta, ensure_ascii=False) if _meta else None

    # AI 回复落库（HTTP 路径：单条 ChatMessage）
    async with async_session_factory() as db:
        ai_msg = ChatMessage(
            session_id=session_id, sender_type="ai", content=final_text,
            extra_meta=_ai_meta,
        )
        db.add(ai_msg)
        await db.flush()
        await db.commit()
        await db.refresh(ai_msg)

    await _push_user_notify(user_id, session_id, character_id, ai_msg.content)

    # 公共收尾（HTTP 专属：可靠度信号 + 异步事实核查）
    await _run_post_processing(
        session_id, user_id, character_id, content,
        final_state, final_text, ai_msg.id, user_msg_id,
        reliability=True,
        gen_prompt=gen_prompt, img_text=img_text,
    )

    _logger.info("AI response saved: msg_id=%d len=%d", ai_msg.id, len(ai_msg.content))
    return {
        "ai_message": {
            "id": ai_msg.id, "session_id": session_id, "sender_type": "ai",
            "content": ai_msg.content, "created_at": ai_msg.created_at.isoformat(),
            "extra_meta": ai_msg.extra_meta,
        },
        "memories_updated": final_state.get("should_update_memory", False),
    }


async def send_and_receive_chunked(
    session_id: int, user_id: int, character_id: int, content: str,
    save_user_message: bool = True, lang: str = "zh", tts: bool = False,
    quote: dict | None = None,
    extra_capabilities: list[str] | None = None,
    reply_delay: bool = True,
) -> dict:
    """发送用户消息 -> Agent处理 -> 拆分回复 -> 保存每条块

    extra_capabilities: 外部链路标记的能力（如识图/文档问答），合并进 AI 回复的调用能力列表
    """
    # 用户消息落库（chunked 路径：save_user_message 开关 + user_msg_info 回传 + Shared Memory）
    user_msg_id, user_msg_info = await _persist_user_message(
        session_id, user_id, character_id, content,
        quote=quote, save_user_message=save_user_message, shared_memory=True,
    )

    # 公共 Agent 主流程（流式路径：不触发搜索，仅剥离标记兜底）
    core = await _run_agent_core(
        session_id, user_id, character_id, content, lang, user_msg_id,
        reply_delay=reply_delay,
    )
    if core is None:
        return {"chunks": [], "memories_updated": False, "cold_war": True}

    final_state = core["final_state"]
    full_text = core["final_text"]
    gen_prompt = core["gen_prompt"]
    img_text = core["img_text"]
    _cal_note_text = core["cal_note_text"]
    _memo_text = core["memo_text"]

    from app.agent.nodes import split_response
    chunks = split_response(full_text, final_state.get("emotional_state", ""))

    # AI 语音回复（TTS，仅语音对话场景）：edge-tts 云端免费，失败静默降级为纯文字
    tts_url = None
    if tts and full_text and chunks:
        try:
            gender = None
            voice = None
            voice_rate = None
            voice_pitch = None
            async with async_session_factory() as db:
                row = (await db.execute(
                    select(AICharacter.gender, AICharacter.voice, AICharacter.voice_rate, AICharacter.voice_pitch)
                    .where(AICharacter.id == character_id)
                )).first()
                if row:
                    gender, voice, voice_rate, voice_pitch = row
            from app.application.tts_service import synthesize
            # Phase 0 P0：从 final_state 情绪标记（emotional_state）取 emotion（无则 None）
            tts_url = await synthesize(
                full_text, str(session_id),
                gender=gender, voice=voice, voice_rate=voice_rate, voice_pitch=voice_pitch,
                user_id=user_id, emotion=final_state.get("emotional_state") or None,
            )
        except Exception as e:
            _logger.warning("TTS synthesis failed: %s", e)
            tts_url = None

    import json as _json
    # 首条块携带思考过程与调用能力（识图/文档/生图/语音回复/扩展）
    _meta = {}
    _reasoning = (final_state.get("reasoning") or "").strip()
    if _reasoning:
        _meta["reasoning"] = _reasoning
    _tools = list(final_state.get("tools_used") or [])
    if gen_prompt:
        _tools.append("生图")
    if tts:
        _tools.append("语音回复")
    for _cap in (extra_capabilities or []):
        if _cap not in _tools:
            _tools.append(_cap)
    if _tools:
        _meta["tools"] = _tools
    saved_chunks = []
    async with async_session_factory() as db:
        for idx, chunk in enumerate(chunks):
            meta = dict(_meta) if idx == 0 else None
            if idx == 0 and tts_url:
                meta = meta or {}
                meta["tts"] = {"url": tts_url}
            # 状态更新/日历备注/备忘附到最后一个气泡（前端小字显示，2026-08-14）
            if idx == len(chunks) - 1:
                _st = (final_state.get("status_update") or "").strip()
                if _st:
                    meta = meta or {}
                    meta["status_update"] = _st
                if _cal_note_text:
                    meta = meta or {}
                    meta["cal_note"] = _cal_note_text
                if _memo_text:
                    meta = meta or {}
                    meta["memo"] = _memo_text
            m = ChatMessage(session_id=session_id, sender_type="ai", content=chunk,
                            extra_meta=_json.dumps(meta, ensure_ascii=False) if meta else None)
            db.add(m)
            await db.flush()
            await db.refresh(m)
            chunk_item = {
                "id": m.id, "session_id": session_id, "sender_type": "ai",
                "content": m.content, "created_at": m.created_at.isoformat(),
                "extra_meta": m.extra_meta,
            }
            if idx == 0 and tts_url:
                chunk_item["tts_url"] = tts_url
            saved_chunks.append(chunk_item)
        await db.commit()

    await _push_user_notify(user_id, session_id, character_id, full_text)

    # 公共收尾（流式路径：G-P2-1，可靠度/事实核查与 HTTP 同一入口、同一参数；纯异步调度不阻塞推送）
    await _run_post_processing(
        session_id, user_id, character_id, content,
        final_state, full_text, saved_chunks[0]["id"] if saved_chunks else None, user_msg_id,
        reliability=True,
        gen_prompt=gen_prompt, img_text=img_text,
    )

    _logger.info("Chunked: %d chunks from %d chars", len(saved_chunks), len(full_text))
    return {"chunks": saved_chunks, "memories_updated": final_state.get("should_update_memory", False),
            "user_message": user_msg_info}


async def continue_chat(
    session_id: int, user_id: int, character_id: int, last_message_id: int,
    lang: str = "zh",
) -> dict:
    # 冷战拦截（v3）：继续对话也属于"发消息"，冷战期不回复
    if await _cold_war_block(character_id, user_id, ""):
        return {"chunks": [], "memories_updated": False, "cold_war": True}
    """AI 连续回复：复用完整 Agent 流程（角色卡/记忆/朋友圈注入），以角色身份延续上一句话"""
    import re as _re

    # 取上一条 AI 消息内容（优先 last_message_id，回退会话最后一条 AI 消息），供检索与延续指令使用
    last_ai_content = ""
    try:
        async with async_session_factory() as db:
            msg_row = None
            if last_message_id and last_message_id > 0:
                result = await db.execute(
                    select(ChatMessage).where(
                        ChatMessage.id == last_message_id,
                        ChatMessage.session_id == session_id,
                    )
                )
                msg_row = result.scalar_one_or_none()
            if msg_row is None or not (msg_row.content or "").strip():
                result = await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.sender_type == "ai",
                    )
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(1)
                )
                msg_row = result.scalar_one_or_none()
            if msg_row and (msg_row.content or "").strip():
                last_ai_content = msg_row.content[:500]
    except Exception as _e:
        _logger.warning("Continue load last ai message failed: %s", _e)

    initial_state = {
        # 用户位只放占位（无新输入）；真正的继续指令由 context_builder 注入 system 区
        "user_message": "（用户没有说话，等你继续）",
        "continue_payload": {
            "last_ai_content": last_ai_content,
        },
        "character_id": character_id,
        "user_id": user_id,
        "session_id": session_id,
        "intent": "",
        "retrieved_memories": [],
        "context_messages": [],
        "character_info": {},
        "ai_response": "",
        "should_update_memory": False,
        "new_memories": [],
        "emotional_state": "",
        "bio_update": None,
        "status_update": None,
        "source_id": None,
        "lang": lang,
        "reasoning_level": await _load_reasoning_level(character_id),
        "tools_used": [],
    }
    final_state = await agent.ainvoke(initial_state)
    full_text = (final_state.get("ai_response") or "").strip()
    if not full_text:
        full_text = "……"

    # 清理可能残留的标记（记忆/自述/状态），避免出现在聊天内容里
    full_text = _re.sub(
        r"\s*[\[【]\s*(?:记忆|自述更新|自述删除|状态更新)\s*[：:].*?[\]】]\s*",
        "", full_text,
    ).strip()

    # 定时承诺解析（继续时若承诺了时间，同样生效）
    try:
        from app.scheduling.promise_parser import extract_timer, strip_timer_tag
        timer_info = extract_timer(
            full_text, user_id=user_id, character_id=character_id,
            session_id=session_id, source_message_id=None, sender="ai",
        )
        full_text = strip_timer_tag(full_text)
        if timer_info:
            from app.scheduling.promise_service import create_event
            await create_event(timer_info)
    except Exception as e:
        _logger.warning("Continue timer parse failed: %s", e)

    # 自述/状态更新落库
    try:
        await _save_bio_update(character_id, final_state.get("bio_update"), user_id)
        await _save_status_update(character_id, final_state.get("status_update"), user_id)
    except Exception as e:
        _logger.warning("Continue bio/status update failed: %s", e)

    # 保存消息 + 更新会话时间戳（携带思考过程/调用能力）
    _cmeta = {}
    _creasoning = (final_state.get("reasoning") or "").strip()
    if _creasoning:
        _cmeta["reasoning"] = _creasoning
    _ctools = list(final_state.get("tools_used") or [])
    if _ctools:
        _cmeta["tools"] = _ctools
    _cai_meta = json.dumps(_cmeta, ensure_ascii=False) if _cmeta else None
    async with async_session_factory() as db:
        ai_msg = ChatMessage(session_id=session_id, sender_type="ai", content=full_text,
                             extra_meta=_cai_meta)
        db.add(ai_msg)
        await db.flush()
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session:
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)  # 2026-08-16 审计：与库内 naive UTC 一致
        await db.commit()
        await db.refresh(ai_msg)

    _logger.info("Continue chat: session=%d msg_id=%d", session_id, ai_msg.id)
    return {
        "id": ai_msg.id, "session_id": session_id, "sender_type": "ai",
        "content": ai_msg.content, "created_at": ai_msg.created_at.isoformat(),
        "extra_meta": ai_msg.extra_meta,
    }


async def get_owned_session(db, session_id: int, user_id: int):
    """按用户归属获取会话，不存在或非本人返回 None（api/chat.py 公共依赖）"""
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session or session.user_id != user_id or not session.is_active:
        return None
    return session


async def get_unread_counts(db, user_id: int) -> list[dict]:
    """每个角色的未读 AI 消息数（一条 GROUP BY 查询，替代逐会话 COUNT 的 N+1）"""
    result = await db.execute(
        select(
            ChatSession.id,
            ChatSession.character_id,
            func.count(ChatMessage.id).label("cnt"),
        )
        .outerjoin(
            ChatMessage,
            and_(
                ChatMessage.session_id == ChatSession.id,
                ChatMessage.sender_type == "ai",
                or_(
                    ChatSession.last_read_at.is_(None),
                    ChatMessage.created_at > ChatSession.last_read_at,
                ),
            ),
        )
        .where(ChatSession.user_id == user_id, ChatSession.is_active == True)
        .group_by(ChatSession.id, ChatSession.character_id)
    )
    rows = result.all()
    return [
        {"session_id": r.id, "character_id": r.character_id, "count": r.cnt}
        for r in rows
        if r.cnt > 0
    ]


async def mark_session_read(db, session_id: int, user_id: int) -> bool:
    """标记会话已读（联动同角色其他活跃会话，避免残留未读）；会话不存在返回 False。

    历史遗留：同一角色可能残留多个活跃会话，点进聊天页时一并标记已读。
    用原生 SQL 更新，避免 SQLAlchemy ORM 会话自动附加 updated_at=onupdate（CURRENT_TIMESTAMP）
    污染 updated_at，导致"按最新会话复用"选错（聊天记录只剩早期的问题）。
    """
    from sqlalchemy import text

    session = await get_owned_session(db, session_id, user_id)
    if session is None:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # 库内统一 naive UTC
    await db.execute(
        text(
            "UPDATE chat_sessions SET last_read_at = :t "
            "WHERE user_id = :u AND character_id = :c AND is_active = 1"
        ),
        {"t": now, "u": session.user_id, "c": session.character_id},
    )
    await db.commit()
    return True
