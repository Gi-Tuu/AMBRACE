"""LangGraph Agent 图节点函数 — 合并为单次 LLM 调用"""
from app.utils.logger import get_logger
from app.agent.state import AgentState
from app.agent.llm_client import chat_completion
from app.agent.context_builder import build_context
from app.agent.response_parser import parse_response, split_response

_logger = get_logger("agent.nodes")


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
    if reasoning_level == 2:
        response, reasoning = await chat_completion(
            messages=state["context_messages"],
            temperature=float(state.get("temperature", 0.8)),
            max_tokens=1300,  # 推理+正文双份（2026-08-15：推理 token 曾吃光 650 预算导致空回复）
            include_reasoning=True,
            task="chat",
            api_key=user_cfg["api_key"] if user_cfg else None,
            base_url=user_cfg["base_url"] if user_cfg else None,
            model=user_cfg.get("model") if user_cfg else None,
            provider=user_cfg.get("provider") if user_cfg else None,
        )
        state["reasoning"] = (reasoning or "").strip() or None
    else:
        response = await chat_completion(
            messages=state["context_messages"],
            temperature=float(state.get("temperature", 0.8)),
            max_tokens=900,  # 2026-08-16：650 曾截断详细回答（瘦身后回复变长），提到 900
            task="chat",
            api_key=user_cfg["api_key"] if user_cfg else None,
            base_url=user_cfg["base_url"] if user_cfg else None,
            model=user_cfg.get("model") if user_cfg else None,
            provider=user_cfg.get("provider") if user_cfg else None,
        )
        state["reasoning"] = None  # 挡位 1 时由 parse_response 解析【推理】标记填充

    # 插件系统：after_generate（插件可改写回复文本；异常隔离）
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

    # 保存记忆
    if state["new_memories"]:
        from app.memory import save_memory
        _logger.info("Saving %d new memories", len(state["new_memories"]))
        for mem in state["new_memories"]:
            try:
                # P2-02：标记路径补 speaker/epistemic（用户陈述→FACT/user；含推断词→INFERRED/character）
                _mark = (mem.get("content") or "")
                _inferred = any(w in _mark for w in ("可能", "好像", "也许", "大概", "我觉得", "猜测"))
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
                    speaker_type="character" if _inferred else "user",
                    speaker_id=state["character_id"] if _inferred else state["user_id"],
                    epistemic_status="INFERRED" if _inferred else "FACT",
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
