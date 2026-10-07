"""聊天服务：管理消息收发，集成 Agent"""
from app.utils.logger import get_logger
from sqlalchemy import select  # func/and_/or_ 随 A20 批 5 第一刀迁至 chat_store（本模块已无引用）
from app.db.database import async_session_factory
import asyncio
import json
import re
from app.utils.async_tasks import spawn_background
from app.utils.llm_channel import reset_channel, set_channel
import time
from datetime import datetime, timezone

from app.agent import actions as _agent_actions

_logger = get_logger("services.chat")
from app.models.chat import ChatSession
from app.models.chat import ChatMessage
from app.models.character import AICharacter
from app.agent.graph import agent
from app.memory import add_chat_memory_extraction  # noqa: F401 —— chat_tooling._run_post_processing 经 _cs 回指本名（tests 按 chat_service 路径打桩），此处必须留着
from app.memory.extractor import SELF_STATEMENT_MAX_LEN  # noqa: F401 —— chat_settlement 经 _cs 回指本常量，此处必须留着
from app.application.chat.tools import (
    _extract_gen_image,
    _sanitize_persist_full,
    _sanitize_chunk_texts,
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
    _gen_image_flow,  # noqa: F401 —— chat_tooling 经 _cs 回指（1 处桩 + api/permissions 按 chat_service 路径 import），此处必须留着
)
from app.application.chat.io import (
    _append_ai_image_message as _append_ai_image_message,
    _append_ai_text_message as _append_ai_text_message,
    _push_ws_ai_message as _push_ws_ai_message,
    _push_user_notify as _push_user_notify,
)
from app.events.store import append_domain_event
from app.events.types import EventType as _ET
from app.application.chat.streaming import (
    _assemble_chunk_meta as _assemble_chunk_meta,
    _update_chunk_meta as _update_chunk_meta,
    _delete_chunks as _delete_chunks,
    _synthesize_chunks_tts as _synthesize_chunks_tts,
    _backfill_stream_tts_meta as _backfill_stream_tts_meta,
    _persist_ai_chunks as _persist_ai_chunks,
    send_and_receive_stream as send_and_receive_stream,
)


# A20 批 5 第一刀（2026-10-02）：会话与落库下沉 chat_store，此处具名重导出。
# ⚠ 库内 6 个模块按 from app.application.chat_service import … 取这些名字；tests 还有字符串路径打桩
#   （"app.application.chat_service.get_latest_session_id"）。删任何一行都会让它们失效。
from app.application.chat_store import (  # noqa: F401
    create_session, get_latest_session_id, get_owned_session, get_unread_counts,
    _persist_user_message, mark_session_read,
)


# A20 批 5 第二刀（2026-10-02）：结算组下沉 chat_settlement，此处具名重导出。
from app.application.chat_settlement import (  # noqa: F401
    _bump_relationship, _generate_initial_bio, _release_drive_on_reply, _save_bio_update,
    _save_status_update, _settle_relational_drive, _settle_thought_pool_turn, _trigger_state_eval,
)


# A20 批 5 第三刀（2026-10-02）：工具与降级下沉 chat_tooling，此处具名重导出。
from app.application.chat_tooling import (  # noqa: F401
    _cold_war_block, _load_reasoning_level, _merge_bracket_reasoning, _resolve_emotional_state,
    _run_post_processing, _schedule_reliability_fact_check, _try_degraded_continuation,
)


# ---- 思考过载兜底（2026-09-13 证据B）：可见正文空/纯标点 + 长推理/截断 → 轻量补写一次 ----
_DEGRADED_REASONING_MIN = 300          # 思考字数阈值（≥ 视为「思考过载」）
_DEGRADED_CONTINUATION_TIMEOUT_S = 25.0
# 纯标点正文（……/。/！/？/~ 等；证据 B 现场即 content="……"）
_PUNCT_ONLY_RE = re.compile(r"^[\s。．.!！?？～~…，,、；;：:·—－\-]+$")


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
    channel_hint: str | None = None,
) -> dict | None:
    """公共 Agent 主流程（双路径收敛 #45，以 chunked 版逻辑为基准）。

    stream_sink 非空时注入 LangGraph state 走真流式生成（见 nodes.generate_response 的
    stream 分支）；流式块/增量经 sink 推给调用方。返回 None 表示冷战拦截；成功返回
    final_state/final_text/gen_prompt/img_text/cal_note_text/memo_text（+ streamed/stream_blocks）。
    """
    # 冷战拦截（v3）：不生成回复
    if await _cold_war_block(character_id, user_id, content):
        return None

    # A32（2026-10-07）：用户消息落库后、生成 AI 回复前——先做「事件已兑现 → 承诺静默关闭」的
    # 确定性结算（到家 / 吃药类信号命中则置 discharged，不发消息、不调模型、不写主动消息日志）。
    # 与 run_prospective_due 的 fire 前闸门同口径，本处是「事件发生当时」的第一道，闸门是双保险。
    # 失败静默：结算不了不影响本轮回复（宁可漏关，不可误关）。
    try:
        from app.scheduling.prospective_intent import settle_promises_on_user_message
        await settle_promises_on_user_message(character_id, session_id, content)
    except Exception as e:
        _logger.warning("prospective settle on user message failed: %s", e)

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
            from app.application.emotion_care_ports import production_care_ports
            from app.domain.emotion.care import register_care_task
            spawn_background(register_care_task(user_id, character_id, content,
                                                ports=production_care_ports))
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

    # A28-S4：本链不再自己拼 initial_state——统一走 Runtime 的唯一构造器。
    # 函数内现取（搬家四律 R2）：打桩 `app.agent.runtime._build_initial_state` 仍能生效。
    from app.agent.runtime import _build_initial_state as _mk_state

    initial_state = _mk_state(
        character_id=character_id, user_id=user_id, session_id=session_id,
        user_message=content, lang=lang,
        reasoning_level=await _load_reasoning_level(character_id),
        save_memory=True,                      # 主聊天轮：照常落记忆（旧字面量里没有 skip_memory_save 键）
        source_id=user_msg_id,
        channel_hint=channel_hint,
        stream_sink=stream_sink,
        tts=tts,
        voice_params=(stream_tts_ctx or {}).get("voice_params", {}) if stream_tts_ctx else {},
        tts_subdir=(stream_tts_ctx or {}).get("tts_subdir") if stream_tts_ctx else None,
        block_sink=(stream_tts_ctx or {}).get("block_sink") if stream_tts_ctx else None,
        character_states_snapshot=_cs_snapshot,
    )

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
                initiator="user",  # S1 发起方口径：聊天回合＝用户请求，必须回复（不许静默）
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
    # P0'（2026-09-10）：非流式直接用 clean_text 回写展示文本——原实现只在 gen_prompt 命中时
    # 回写，模型漏写闭合标签且只给了 [IMG_TEXT] 时会残留标记（现场 11521）。
    # 流式展示文本由 chunker 剥离（strip_stream_display→strip_actions，已容错无闭合标签），
    # 统一兜底清洗在本函数末尾（CAL_NOTE/MEMO 提取之后，避免影响提取源）。
    if not _is_stream and (gen_prompt or img_text):
        full_text = clean_text

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
        from app.scheduling.promise_parser import extract_ai_timer, strip_timer_tag
        from app.scheduling.promise_service import create_event
        # F1b（2026-09-08）：AI 侧承诺 source 只能绑 AI 消息（此处尚未落库 → None），
        # 不再传 user_msg_id（曾致 #33 事件 source 归属成用户消息）；F1a 见 extract_ai_timer
        timer_info = extract_ai_timer(
            (_source_text if _is_stream else full_text),
            user_id=user_id, character_id=character_id,
            session_id=session_id,
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

    # P0'（2026-09-10）：展示/落库文本零标记兜底——放在 CAL_NOTE/MEMO/timer 提取之后，
    # 提取源不受影响；此后 full_text 进落库（HTTP 单条 / chunked 分块 / SSE done）与推送，
    # 任何漏网标记（含模型漏写闭合标签的 [GEN_IMAGE]/[IMG_TEXT]）在此统一剥净。
    # 证据A（2026-09-13）：剥「正文开头的中文括号内心活动」→ 并入 reasoning 走既有上屏管线
    # P1-6（2026-09-16）：并入时与既有 reasoning 合并后统一过 normalize_reasoning_for_display，
    # 修掉「归一之后才追加」的旁路（元话语不再随该片段漏出）
    _clean_final, _bracket_reasoning = _sanitize_persist_full(full_text)
    if _bracket_reasoning:
        _merged_bracket = _merge_bracket_reasoning(final_state, _bracket_reasoning)
        final_state["reasoning_bracket_stripped"] = True
        _logger.info("reasoning_bracket_stripped merged=%s len=%s preview=%s",
                     _merged_bracket, len(_bracket_reasoning), _bracket_reasoning[:40])
    if _clean_final != (full_text or "").strip():
        _logger.warning("Final text had marker residue, sanitized: %s", (full_text or "")[:80])
    full_text = _clean_final

    # 证据B（2026-09-13）：思考过载兜底——可见正文为空/纯标点 且（长推理 ≥300 字或标记截断）
    _vis = full_text.strip()
    if not _vis or _PUNCT_ONLY_RE.fullmatch(_vis):
        _reasoning_now = (final_state.get("reasoning") or "").strip()
        if final_state.get("marker_truncated") or len(_reasoning_now) >= _DEGRADED_REASONING_MIN:
            _degraded_reason = "empty" if not _vis else "punct_only"
            _continuation = await _try_degraded_continuation(
                session_id, user_id, character_id, _reasoning_now)
            if _continuation:
                full_text = _continuation
                _logger.info("reply_degraded recovered reason=%s len=%s",
                             _degraded_reason, len(_continuation))
            else:
                final_state["degraded_reply"] = True
                _logger.info("reply_degraded reason=%s reasoning_len=%s",
                             _degraded_reason, len(_reasoning_now))
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


async def send_and_receive(
    session_id: int, user_id: int, character_id: int, content: str,
    lang: str = "zh", quote: dict | None = None, reply_delay: bool = True,
    channel: str | None = None,
) -> dict:
    """发送用户消息 → Agent 处理 → 返回 AI 回复。

    channel 渠道来源标记（任务 A）：值域 ``wechat_ilink``（微信桥）或 App 不传（None）。
    非空时：用户消息 extra_meta 挂 ``channel``；AI 消息 extra_meta 挂 ``channel``；并把
    channel_hint 传进 Agent 注入提示（仅进 LLM 上下文，不落库/不进记忆）。
    """
    # 用户消息落库（HTTP 路径：无开关，始终落库；无 user_msg_info/Shared Memory）
    user_msg_id, _ = await _persist_user_message(
        session_id, user_id, character_id, content,
        quote=quote, save_user_message=True, shared_memory=False,
        channel=channel,
    )

    # 公共 Agent 主流程（HTTP 专属：用户定时承诺 / 自主搜索 Loop / 多工具任务化）
    # T6-M2 渠道归因：本函数是唯一带 channel 参数的对话入口（微信桥传 wechat_ilink）。
    # 值进 contextvar，由 llm_client._record_usage_async 这个唯一读取点落到用量行；
    # try/finally 必清——微信桥是常驻轮询 task，不清会把后续无关轮次的用量也染成 wechat。
    # App 主链路不传 channel（恒 None），渠道由 API 入口侧设定，这里不做二次覆盖。
    _ch_token = set_channel(channel) if channel else None
    try:
        core = await _run_agent_core(
            session_id, user_id, character_id, content, lang, user_msg_id,
            user_timer=True, search_loop=True, run_chat_task=True, reply_delay=reply_delay,
            channel_hint=channel,
        )
    finally:
        reset_channel(_ch_token)
    if core is None:
        return {"ai_message": None, "memories_updated": False, "cold_war": True}

    final_state = core["final_state"]
    final_text = core["final_text"]
    gen_prompt = core["gen_prompt"]
    img_text = core["img_text"]

    # 组装 AI 消息 extra_meta：思考过程 + 调用能力（生图/扩展等）+ 渠道来源标记（任务 A）
    _meta = {}
    if channel:
        _meta["channel"] = channel
    _reasoning = (final_state.get("reasoning") or "").strip()
    if _reasoning:
        _meta["reasoning"] = _reasoning
    if final_state.get("degraded_reply"):
        _meta["degraded_reply"] = True
    _tools = list(final_state.get("tools_used") or [])
    if gen_prompt:
        _tools.append("生图")
    # R6（2026-09-09）：统一去重 + 上限（标签已在 ability_labels 归一为中文）
    try:
        from app.agent.ability_labels import normalize_tool_list
        _tools = normalize_tool_list(_tools)
    except Exception:
        pass
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

    if final_state.get("reasoning_bracket_stripped"):
        _logger.info("reasoning_bracket_stripped msg_id=%s", ai_msg.id)

    # 3.10 事件流水（P0）：AI 单条回复（HTTP 路径）
    await append_domain_event(
        _ET.CHAT_MESSAGE_SENT.value, "chat_session", session_id,
        entity_type="chat_message", entity_id=ai_msg.id,
        actor_type="ai", actor_id=character_id,
        payload={"sender_type": "ai", "route": "http", "content": ai_msg.content},
        idempotency_key=f"chat.message_sent:chat_message:{ai_msg.id}",
        origin="ai_message",
    )

    await _push_user_notify(user_id, session_id, character_id, ai_msg.content)

    # 公共收尾（HTTP 专属：可靠度信号 + 异步事实核查）
    await _run_post_processing(
        session_id, user_id, character_id, content,
        final_state, final_text, ai_msg.id, user_msg_id,
        reliability=True,
        gen_prompt=gen_prompt, img_text=img_text,
    )

    # 3.10 事件流水（P0）：一轮 user→ai 清算（幂等键绑 user_msg_id，三路径只落一条）
    await append_domain_event(
        _ET.CHAT_TURN_COMPLETED.value, "chat_session", session_id,
        actor_type="system",
        payload={"user_message_id": user_msg_id, "ai_message_ids": [ai_msg.id],
                 "route": "http", "block_count": 1},
        idempotency_key=f"chat.turn_completed:{session_id}:{user_msg_id}",
        origin="ai_message",
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
    route: str = "ws_chunk",
) -> dict:
    """发送用户消息 -> Agent处理 -> 拆分回复 -> 保存每条块

    extra_capabilities: 外部链路标记的能力（如识图/文档问答），合并进 AI 回复的调用能力列表
    （任务 A，2026-09-04）本轮不动（微信桥走 send_and_receive，不走本函数）。将来如需渠道
    来源标记，可对称扩展 channel 参数并透传给 _persist_user_message / AI 消息 _meta / channel_hint。
    route（F-12，v3.4.6 审查）：本轮事件 route 口径（默认 ws_chunk；SSE 回退传 sse_fallback、
    散点入口传 image/file/emoji/batch 等），只影响事件 payload 标注，幂等键不变。
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
    # P0'（2026-09-10）：源文本已在 _run_agent_core 末尾剥净，这里逐块兜底（纯标记块直接丢弃，
    # 禁止 [GEN_IMAGE] 段落作为独立消息落库）；full_text 本就为空时保持原行为不动
    _chunks_raw = split_response(full_text, final_state.get("emotional_state", ""))
    chunks = _sanitize_chunk_texts(_chunks_raw) if full_text else _chunks_raw

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
            from app.application.tts_service import resolve_cloud_voice, synthesize
            # Phase 0 P0：从 final_state 情绪标记（emotional_state）取 emotion（无则 None）
            tts_url = await synthesize(
                full_text, str(session_id),
                gender=gender, voice=voice, voice_rate=voice_rate, voice_pitch=voice_pitch,
                user_id=user_id, emotion=final_state.get("emotional_state") or None,
                tts_voice=resolve_cloud_voice(voice),
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
    if final_state.get("degraded_reply"):
        _meta["degraded_reply"] = True
    _tools = list(final_state.get("tools_used") or [])
    if gen_prompt:
        _tools.append("生图")
    if tts:
        _tools.append("语音回复")
    for _cap in (extra_capabilities or []):
        if _cap not in _tools:
            _tools.append(_cap)
    # R6（2026-09-09）：统一去重 + 上限
    try:
        from app.agent.ability_labels import normalize_tool_list
        _tools = normalize_tool_list(_tools)
    except Exception:
        pass
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

    # 3.10 事件流水（P0）：AI 分块回复（commit 成功后逐块落事件；route=调用入口口径，F-12）
    _chunk_ai_ids = [(c["id"], c.get("content") or "") for c in saved_chunks]
    for _mid, _txt in _chunk_ai_ids:
        await append_domain_event(
            _ET.CHAT_MESSAGE_SENT.value, "chat_session", session_id,
            entity_type="chat_message", entity_id=_mid,
            actor_type="ai", actor_id=character_id,
            payload={"sender_type": "ai", "route": route, "content": _txt},
            idempotency_key=f"chat.message_sent:chat_message:{_mid}",
            origin="ai_message",
        )

    await _push_user_notify(user_id, session_id, character_id, full_text)

    # 公共收尾（流式路径：G-P2-1，可靠度/事实核查与 HTTP 同一入口、同一参数；纯异步调度不阻塞推送）
    await _run_post_processing(
        session_id, user_id, character_id, content,
        final_state, full_text, saved_chunks[0]["id"] if saved_chunks else None, user_msg_id,
        reliability=True,
        gen_prompt=gen_prompt, img_text=img_text,
    )

    # 3.10 事件流水（P0）：一轮清算（与 SSE 路径共用幂等键 → 回退也只落一条；route=调用入口口径，F-12）
    await append_domain_event(
        _ET.CHAT_TURN_COMPLETED.value, "chat_session", session_id,
        actor_type="system",
        payload={"user_message_id": user_msg_id, "ai_message_ids": [i for i, _ in _chunk_ai_ids],
                 "route": route, "block_count": len(_chunk_ai_ids)},
        idempotency_key=f"chat.turn_completed:{session_id}:{user_msg_id}",
        origin="ai_message",
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

    # A28-S4：继续指令也走同一个构造器（原先这里自己拼了第三套 state，
    # 与主聊天那套的差异全靠"恰好没用到"维持）
    from app.agent.runtime import _build_initial_state as _mk_state

    initial_state = _mk_state(
        character_id=character_id, user_id=user_id, session_id=session_id,
        # 用户位只放占位（无新输入）；真正的继续指令由 context_builder 注入 system 区
        user_message="（用户没有说话，等你继续）",
        continue_payload={"last_ai_content": last_ai_content},
        lang=lang,
        reasoning_level=await _load_reasoning_level(character_id),
        save_memory=True,
    )
    final_state = await agent.ainvoke(initial_state)
    full_text = (final_state.get("ai_response") or "").strip()
    if not full_text:
        full_text = "……"
        # 2026-09-13：空正文兜底也要打降级标记，客户端才会显示灰字提示（此前只有主链路打）
        final_state["degraded_reply"] = True

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
    if final_state.get("degraded_reply"):
        _cmeta["degraded_reply"] = True
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

    # 3.10 事件流水（P0）：AI 连续回复（不发 turn_completed——无新用户消息，保持「一轮一清算」）
    await append_domain_event(
        _ET.CHAT_MESSAGE_SENT.value, "chat_session", session_id,
        entity_type="chat_message", entity_id=ai_msg.id,
        actor_type="ai", actor_id=character_id,
        payload={"sender_type": "ai", "route": "continue", "content": full_text},
        idempotency_key=f"chat.message_sent:chat_message:{ai_msg.id}",
        origin="ai_message",
    )

    _logger.info("Continue chat: session=%d msg_id=%d", session_id, ai_msg.id)
    return {
        "id": ai_msg.id, "session_id": session_id, "sender_type": "ai",
        "content": ai_msg.content, "created_at": ai_msg.created_at.isoformat(),
        "extra_meta": ai_msg.extra_meta,
    }
