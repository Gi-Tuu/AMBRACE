"""LangGraph Agent 图节点函数 — 合并为单次 LLM 调用"""
import asyncio

from app.utils.logger import get_logger
from app.agent.state import AgentState
from app.agent.llm_client import chat_completion, chat_completion_stream
from app.agent.context_builder import build_context
from app.agent.response_parser import parse_response, split_response, IncrementalResponseChunker
from app.memory.speaker import resolve_speaker_from_content  # X-2（2026-08-18）：统一归属判定公共函数

_logger = get_logger("agent.nodes")


def _has_after_generate_hook() -> bool:
    """是否存在启用中的插件注册了 after_generate 改写钩子。

    存在时主私聊链路回退非流式（避免改写后与已推送流不一致）。
    """
    try:
        from app.plugins.registry import _loaded, _enabled
        for name, entry in _loaded.items():
            if entry.get("hooks", {}).get("after_generate") and _enabled.get(name, False):
                return True
    except Exception:
        pass
    return False


async def retrieve_memories(state: AgentState) -> AgentState:
    """检索相关记忆（向量检索 + 关键词）；命中即视为一次复习（艾宾浩斯强化，24h 防抖）"""
    from app.memory import search_memories
    from app.memory.constants import REINFORCE_FACTOR_RETRIEVE, REINFORCE_DEBOUNCE_HOURS

    # 认知循环 v2.1：感知派生查询（话题/情绪）参与多路召回，提升相关性
    queries = None
    _perception = state.get("perception") or {}
    try:
        if _perception:
            from app.agent.perception import topic_cn
            extra = []
            _topic = _perception.get("topic") or ""
            if _topic and _topic != "other":
                extra.append(topic_cn(_topic))
            _emo_query = {
                "sad": "低落难过", "happy": "开心高兴", "excited": "激动兴奋",
                "confused": "困惑", "venting": "倾诉烦恼",
            }.get(_perception.get("emotion_label") or "")
            if _emo_query:
                extra.append(_emo_query)
            # 记忆架构 v2.1 Phase 4a：目标/未完成路（进行中目标话题 + follow_up 话题）
            try:
                from app.agent.topic_tracker import load_active_goal_queries
                extra.extend(await load_active_goal_queries(state["character_id"], state.get("user_id", 1)))
            except Exception as _e:
                _logger.warning("Goal query build failed: %s", _e)
            queries = extra or None
    except Exception as e:
        _logger.warning("Perception query build failed: %s", e)
    # 继续指令场景：用上一条 AI 消息内容检索（保持一致性，而非无意义的指令文本）
    _cont = state.get("continue_payload") or {}
    _last_ai = (_cont.get("last_ai_content") or "").strip()
    _query = _last_ai if _last_ai else state["user_message"]
    memories = await search_memories(
        character_id=state["character_id"],
        query=_query,
        limit=3,
        queries=queries,
        trace_meta={
            "user_id": state.get("user_id"),
            "session_id": state.get("session_id"),
            "task_id": state.get("task_id"),
        },
    )
    state["retrieved_memories"] = memories
    if memories:
        try:
            from app.memory.service import reinforce_memories
            await reinforce_memories(
                [m["id"] for m in memories],
                factor=REINFORCE_FACTOR_RETRIEVE,
                debounce_hours=REINFORCE_DEBOUNCE_HOURS,
            )
        except Exception as e:
            _logger.warning("Memory reinforce failed: %s", e)
    return state


async def _synth_stream_block(text: str, state: AgentState) -> str | None:
    """流式路径逐句合成一条语音（复用 tts_service.synthesize，百炼优先/edge 兜底），失败返回 None。"""
    try:
        from app.services.tts_service import synthesize
        params = state.get("voice_params") or {}
        return await synthesize(
            text, subdir=state.get("tts_subdir") or "stream",
            gender=params.get("gender"), voice=params.get("voice"),
            voice_rate=params.get("voice_rate"), voice_pitch=params.get("voice_pitch"),
            user_id=state.get("user_id"),
        )
    except Exception as e:
        _logger.warning("Stream TTS block failed: %s", e)
        return None


async def _stream_generate(state: AgentState, user_cfg: dict | None, stream_sink) -> str:
    """流式生成：边调 chat_completion_stream 边推 delta 增量事件，返回累积原始文本。

    - delta 事件：本次增量展示文本（剥离标记后），供前端打字机；
    - 语义块按句子/情绪边界用 IncrementalResponseChunker 边收边切，收集到
      state["stream_blocks"]，由服务层（拿到完整 final_state 后）组装 extra_meta 再落库/推送；
    - tts 路径（state["tts"] 且 state["block_sink"] 非空）：流水线消费端逐句合成语音并
      实时落库 + 推 block（带 tts_url），LLM 不被逐句合成阻塞；
    - 结束 flush 末块；stream 异常直接上抛（由服务层回退非流式）。
    同时写 state["raw_response"] / state["stream_blocks"] / state["stream_display"] / state["stream_saved"]。
    """

    chunker = IncrementalResponseChunker(state.get("emotional_state", ""))
    block_sink = state.get("block_sink")
    if state.get("tts") and block_sink:
        return await _stream_generate_tts(state, user_cfg, stream_sink, chunker, block_sink)

    raw: list[str] = []
    blocks: list[str] = []
    try:
        async for piece in chat_completion_stream(
            messages=state["context_messages"],
            temperature=float(state.get("temperature") or 0.8),
            max_tokens=900,  # 与正文挡位一致（深度思考挡已提前回退非流式）
            task="chat",
            api_key=user_cfg["api_key"] if user_cfg else None,
            base_url=user_cfg["base_url"] if user_cfg else None,
            model=user_cfg.get("model") if user_cfg else None,
            provider=user_cfg.get("provider") if user_cfg else None,
        ):
            raw.append(piece)
            blocks.extend(chunker.feed(piece))
            if chunker.disp_delta:
                await stream_sink("delta", {"text": chunker.disp_delta})
        final_clean = chunker.clean_text
        blocks.extend(chunker.flush())
        full = "".join(raw)
        state["raw_response"] = full
        state["stream_blocks"] = blocks
        state["stream_display"] = final_clean or "".join(blocks)
        state["stream_saved"] = []
        return full
    except Exception:
        _logger.warning("Stream generation failed, re-raise for non-stream fallback")
        raise


async def _stream_generate_tts(state, user_cfg, stream_sink, chunker, block_sink) -> str:
    """流式 + 逐句 TTS 流水线：LLM producer 喂句子到队列，consumer 逐句合成→实时落库→推 block。"""
    raw: list[str] = []
    blocks: list[str] = []
    saved: list[dict] = []
    index = 0
    queue: asyncio.Queue = asyncio.Queue()

    async def consumer() -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            idx, text = item
            url = await _synth_stream_block(text, state)
            chunk = await block_sink(idx, text, url)
            saved.append(chunk)
            await stream_sink("block", {"index": idx, **chunk})

    consumer_task = asyncio.create_task(consumer())
    try:
        async for piece in chat_completion_stream(
            messages=state["context_messages"],
            temperature=float(state.get("temperature") or 0.8),
            max_tokens=900,
            task="chat",
            api_key=user_cfg["api_key"] if user_cfg else None,
            base_url=user_cfg["base_url"] if user_cfg else None,
            model=user_cfg.get("model") if user_cfg else None,
            provider=user_cfg.get("provider") if user_cfg else None,
        ):
            raw.append(piece)
            new_blocks = chunker.feed(piece)
            blocks.extend(new_blocks)
            if chunker.disp_delta:
                await stream_sink("delta", {"text": chunker.disp_delta})
            for blk in new_blocks:
                await queue.put((index, blk))
                index += 1
        final_clean = chunker.clean_text
        tail = chunker.flush()
        blocks.extend(tail)
        for blk in tail:
            await queue.put((index, blk))
            index += 1
        full = "".join(raw)
        await queue.put(None)
        try:
            await consumer_task
        except BaseException as e:
            _logger.warning("TTS consumer died mid-stream: %s", e)
        state["raw_response"] = full
        state["stream_blocks"] = blocks
        state["stream_display"] = final_clean or "".join(blocks)
        state["stream_saved"] = saved
        return full
    except Exception:
        _logger.warning("Stream generation failed, re-raise for non-stream fallback")
        raise
    finally:
        # P2-A：客户端断开时 producer 被 CancelledError 取消（BaseException，except Exception 不捕获），
        # 必须在这里兜底取消 consumer，否则 consumer 永久卡在 queue.get() 泄漏。
        if not consumer_task.done():
            consumer_task.cancel()
        try:
            await asyncio.wait_for(consumer_task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


async def generate_response(state: AgentState) -> AgentState:
    """调用 LLM 生成回复，并提取记忆标记（支持用户级 BYOK 配置覆盖）"""
    from app.agent.llm_client import get_user_llm_config
    user_cfg = await get_user_llm_config(state.get("user_id"))

    # 插件系统：before_generate（生成前可追加上下文/改写消息；异常隔离）
    try:
        from app.plugins.registry import run_hook
        await run_hook("before_generate", {
            "user_id": state.get("user_id", 1),
            "character_id": state.get("character_id"),
            "session_id": state.get("session_id"),
            "user_message": state.get("user_message", ""),
            "context_messages": state["context_messages"],
        })
    except Exception:
        pass

    # 思考过程三挡：0=关闭（无推理）；1=简单思考（context_builder 注入【推理】指令，
    # parse_response 解析）；2=深度思考（开启 LLM thinking，reasoning_content 透传）
    reasoning_level = int(state.get("reasoning_level", 0) or 0)
    # 真流式（SSE）生成：注入 stream_sink 且非深度思考、无 after_generate 改写钩子时走流式。
    # 流式异常直接上抛，由服务层回退非流式 chunked。
    stream_sink = state.get("stream_sink")
    can_stream = stream_sink is not None and reasoning_level != 2 and not _has_after_generate_hook()
    if can_stream:
        response = await _stream_generate(state, user_cfg, stream_sink)
        state["reasoning"] = None  # 流式链路：推理走【推理】标记，由 parse_response 填充
        state["streamed"] = True
    elif reasoning_level == 2:
        response, reasoning = await chat_completion(
            messages=state["context_messages"],
            temperature=float(state.get("temperature") or 0.8),
            max_tokens=1300,  # 推理+正文双份（2026-08-15：推理 token 曾吃光 650 预算导致空回复）
            include_reasoning=True,
            task="chat",
            api_key=user_cfg["api_key"] if user_cfg else None,
            base_url=user_cfg["base_url"] if user_cfg else None,
            model=user_cfg.get("model") if user_cfg else None,
            provider=user_cfg.get("provider") if user_cfg else None,
        )
        state["reasoning"] = (reasoning or "").strip() or None
        state["streamed"] = False
    else:
        response = await chat_completion(
            messages=state["context_messages"],
            temperature=float(state.get("temperature") or 0.8),
            max_tokens=900,  # 2026-08-16：650 曾截断详细回答（瘦身后回复变长），提到 900
            task="chat",
            api_key=user_cfg["api_key"] if user_cfg else None,
            base_url=user_cfg["base_url"] if user_cfg else None,
            model=user_cfg.get("model") if user_cfg else None,
            provider=user_cfg.get("provider") if user_cfg else None,
        )
        state["reasoning"] = None  # 挡位 1 时由 parse_response 解析【推理】标记填充
        state["streamed"] = False

    # 插件系统：after_generate（插件可改写回复文本；异常隔离）
    # 真流式时跳过：若存在启用中的 after_generate 钩子，上面已回退非流式（can_stream=False）。
    if not state.get("streamed"):
        try:
            from app.plugins.registry import run_hook
            _ctx = {"reply_text": response}
            await run_hook("after_generate", _ctx)
            _new = _ctx.get("reply_text")
            if isinstance(_new, str) and _new.strip():
                response = _new
        except Exception:
            pass

    # 调用能力：启用中的插件视为「扩展」能力（参与生成前后钩子）
    try:
        from app.plugins.registry import list_plugins
        enabled_plugins = [p.get("name") for p in list_plugins() if p.get("enabled")]
        state["tools_used"] = [f"扩展：{'、'.join(enabled_plugins)}"] if enabled_plugins else []
    except Exception:
        state["tools_used"] = []

    # 解析回复（提取记忆/自述/状态更新）
    state = parse_response(response, state)
    # 真流式：还原为「剥离全部标记」的展示层正文（parse_response 只剥记忆/自述/状态，
    # 此处覆盖为流式切块用的干净展示文本，保证流式块与落库正文一致）
    if state.get("streamed"):
        state["ai_response"] = state.get("stream_display") or state["ai_response"]

    # 保存记忆（Phase E：skip_memory_save=True 时跳过——社交短回复（如抖音 hint）不落记忆，防机器生成内容污染）
    if state["new_memories"] and not state.get("skip_memory_save"):
        from app.memory import save_memory
        _logger.info("Saving %d new memories", len(state["new_memories"]))
        for mem in state["new_memories"]:
            try:
                # P2-02：标记路径补 speaker/epistemic（用户陈述→FACT/user；含推断词→INFERRED/character）
                # X-2（2026-08-18）：判定收敛至公共函数 resolve_speaker_from_content（与提取端同一套规则）
                _spk_type, _spk_id, _epi = resolve_speaker_from_content(
                    mem.get("content") or "",
                    state.get("user_message") or "",
                    state.get("ai_response") or "",
                    state["user_id"],
                    state["character_id"],
                )
                await save_memory(
                    user_id=state["user_id"],
                    character_id=state["character_id"],
                    memory_type=mem["type"],
                    title=mem.get("title", ""),
                    content=mem["content"],
                    importance=mem.get("importance", 2),
                    source="chat",
                    sub_type=mem.get("sub_type"),
                    source_id=state.get("source_id"),
                    speaker_type=_spk_type,
                    speaker_id=_spk_id,
                    epistemic_status=_epi,
                )
            except Exception as e:
                _logger.warning("\u4fdd\u5b58\u8bb0\u5fc6\u5931\u8d25: %s", e)

    return state


__all__ = ["perceive", "retrieve_memories", "build_context", "generate_response", "reflect", "split_response"]


async def perceive(state: AgentState) -> AgentState:
    """认知循环 v2.1 感知节点：读取角色开关，开启时执行本地感知（零 LLM）。

    开关读取失败/关闭时静默降级（state 保持默认值，走旧链路）。
    """
    state["cognitive_loop_enabled"] = False
    state["perception"] = None
    # 继续指令场景：不执行意图感知（指令文本会误导意图分类与长度提示）
    if state.get("continue_payload"):
        return state
    try:
        from sqlalchemy import select as _select
        from app.db.database import async_session_factory
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            row = (await db.execute(
                _select(AICharacter.cognitive_loop_enabled)
                .where(AICharacter.id == state["character_id"])
            )).scalar_one_or_none()
        state["cognitive_loop_enabled"] = bool(row)
    except Exception as e:
        _logger.warning("Cognitive switch load failed: %s", e)
        return state
    if not state["cognitive_loop_enabled"]:
        return state
    try:
        from app.agent.perception import perceive as _perceive
        state["perception"] = _perceive(state.get("user_message") or "")
    except Exception as e:
        _logger.warning("Perception failed: %s", e)
    return state


async def reflect(state: AgentState) -> AgentState:
    """认知循环 v2.1 反思节点：概率+高风险触发本地自查（零 LLM，不重生成）。

    结果写入 state["reflection_result"]，由 chat_service 拿到 AI 消息 id 后
    调用 reflection.persist_reflection 落库（不阻塞回复）。
    """
    state["reflection_result"] = None
    if not state.get("cognitive_loop_enabled"):
        return state
    try:
        from app.agent.reflection import evaluate_reflection
        state["reflection_result"] = await evaluate_reflection(state)
    except Exception as e:
        _logger.warning("Reflection evaluate failed: %s", e)
    return state
