"""工具与降级层：思考挡位、冷战拦截、情绪状态、降级续写、括号推理合并、可靠度核查调度、回合后处理。

本模块自 ``application/chat_service.py`` 逐字节搬入（A20 批 5 第三刀，2026-10-02）。
边界＝**工具与降级续写、括号推理合并、冷处理拦截、情绪状态解析、可靠性事实核查调度、回合后处理**；
不解析会话（chat_store）、不发消息（chat.io / streaming）、不做结算（chat_settlement）。

机械改写只有两类（判定/文案/返回值一字未改）：

1. 缩进（原样为 0，未动）。
2. 被 tests/ 按 chat_service 打桩的名字、本组兄弟互调、以及本组用到的 chat_service 模块级常量，
   改走 ``_cs.<name>``（函数体内 ``from app.application import chat_service as _cs``，**不得提到顶层**——顶层会成环）：
   ``async_session_factory``（``setattr(chat_service, "async_session_factory", …)`` 实测 18 处）、
   ``spawn_background``（4 处）、``add_chat_memory_extraction``（1 处）、第二刀重导出的结算八名
   （``_save_bio_update`` / ``_save_status_update`` / ``_generate_initial_bio`` / ``_bump_relationship`` /
   ``_settle_relational_drive`` / ``_release_drive_on_reply`` / ``_settle_thought_pool_turn`` /
   ``_trigger_state_eval``，实测 4 处按 chat_service 路径打桩）、``_gen_image_flow``（1 处桩，
   ``app/api/permissions.py`` 也按 chat_service 路径 import 它）、以及两个常量
   ``_PUNCT_ONLY_RE`` / ``_DEGRADED_CONTINUATION_TIMEOUT_S``（``_run_agent_core`` 也在用 ⇒ 留在
   chat_service，本模块只回指取）。兄弟调用实测一处：
   ``_run_post_processing``→``_schedule_reliability_fact_check``。
   本模块**不得**具名 import 上述名字，否则桩静默失效 ⇒ 退化成真查库 / 真起后台任务 / 真生图。

``_run_agent_core``（实测 17 处桩）**留在 chat_service**，本组 7 个函数对它没有任何调用点
（``_run_post_processing`` 里那条只是注释引用），模块级 ``agent`` 同样只被 ``_run_agent_core`` 使用
⇒ 本模块既不出现 ``_cs._run_agent_core``，也不绑定 ``agent``；那 17 处桩依赖的是三个对外端点在
chat_service 自身命名空间解析裸名，搬家没有碰这条边。

其余依赖（模型/SQLAlchemy/标准库/chat.tools 纯函数）按原模块原名直接 import；logger 名沿用
``services.chat``（D-1 口径）。chat_service 侧保留 7 个具名重导出，tests 的打桩面零迁移。
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.application.chat.tools import _sanitize_persist_full
from app.utils.logger import get_logger

_logger = get_logger("services.chat")


async def _load_reasoning_level(character_id: int) -> int:
    """读取角色「思考过程」挡位：0=关闭 / 1=简单思考 / 2=深度思考"""
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    try:
        from app.models.character import ProactiveSettings
        async with _cs.async_session_factory() as db:
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


async def _try_degraded_continuation(
    session_id: int, user_id: int, character_id: int, reasoning_text: str,
) -> str:
    """思考过载兜底：轻量补写一次——人设 + 最近对话 +「把想好的直接说出来」。

    限 1 次、带超时；任何失败返回空串（调用方落 degraded_reply 标记 + reply_degraded 埋点）。
    补写结果本身为空/纯标点/剥括号推理后为空 → 同样视为失败。
    """
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    try:
        from sqlalchemy import select as _select
        from app.models.character import AICharacter as _AIChar
        from app.models.chat import ChatMessage as _CM

        async with _cs.async_session_factory() as db:
            char = (await db.execute(
                _select(_AIChar).where(_AIChar.id == character_id)
            )).scalar_one_or_none()
            recent = (await db.execute(
                _select(_CM).where(_CM.session_id == session_id)
                .order_by(_CM.created_at.desc(), _CM.id.desc()).limit(10)
            )).scalars().all()
        if char is None:
            return ""
        system = f"你是{char.name}。"
        if (char.personality or "").strip():
            system += f"人设：{char.personality.strip()}"
        if (char.chat_style or "").strip():
            system += f"\n聊天风格：{char.chat_style.strip()}"
        messages: list[dict] = [{"role": "system", "content": system}]
        for m in reversed(recent):
            c = (m.content or "").strip()
            if not c:
                continue
            messages.append({"role": "assistant" if m.sender_type == "ai" else "user", "content": c})
        messages.append({"role": "system", "content": (
            "你刚才想得太多，没有把话说出口。请把刚才想好的内容，用一两句话自然地直接说给对方，"
            f"保持{char.name}平时的语气；不要解释你想了什么、不要再写任何思考过程、"
            "不要用括号包裹内心活动，直接给正文。")})
        from app.agent.llm_client import chat_completion as _cc

        out = await asyncio.wait_for(
            # A4 批 5 / T6 M1 收口（2026-09-27）：补 task 归因 —— 本处经别名 `from ... import chat_completion as _cc`
            # 调用，绕过了「按 ast.Name 匹配」的记账契约测试（tests/test_audit_batch3.py），导致这条降级续写
            # 的用量落库 task=NULL、读端只能落 (untagged)。补上与其他对话调用一致的 task="chat"。
            _cc(messages, user_id=user_id, character_id=character_id, task="chat"),
            timeout=_cs._DEGRADED_CONTINUATION_TIMEOUT_S,
        )
        text = (out or "").strip() if isinstance(out, str) else ""
        if not text or _cs._PUNCT_ONLY_RE.fullmatch(text):
            return ""
        visible, _extra = _sanitize_persist_full(text)
        return visible
    except Exception as e:  # noqa: BLE001 - 兜底路径任何失败都静默降级
        _logger.warning("Degraded continuation failed: %s", e)
        return ""


def _merge_bracket_reasoning(final_state: dict, bracket_reasoning: str) -> bool:
    """把「正文开头中文括号内心活动」并入 final_state["reasoning"]，统一走同一归一管线。

    P1-6（2026-09-16）：原实现是在既有 reasoning 已归一后直接字符串拼接（证据 A 括号推理），
    该片段因此绕过 normalize_reasoning_for_display——「用户」/名字自称/策略·长度·我决定加图
    这类元话语仍可能上屏。现改为：片段与既有思考合并后整段过一遍归一（全链路唯一出口）。
    归一后为空则不写入（返回是否写入），既有 reasoning 不被清空。
    """
    if not bracket_reasoning:
        return False
    from app.agent.context.reasoning_prompt import normalize_reasoning_for_display
    _prev = (final_state.get("reasoning") or "").strip()
    _merged = f"{_prev}\n{bracket_reasoning}" if _prev else bracket_reasoning
    _normalized = normalize_reasoning_for_display(
        _merged, final_state.get("character_name"), final_state.get("user_name"))
    if not _normalized:
        return False
    final_state["reasoning"] = _normalized
    return True


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
    from app.application import chat_service as _cs  # 被桩依赖与本组常量在 chat_service 命名空间现取
    # 认知循环 v2.1：反思结果落库（节点已计算，拿第一条 AI 消息 id 记录；失败静默）
    try:
        from app.agent.reflection import persist_reflection
        if final_state.get("reflection_result") and ai_message_id:
            await persist_reflection(character_id, user_id, ai_message_id, final_state["reflection_result"])
    except Exception as e:
        _logger.warning("Reflection persist failed: %s", e)

    await _cs._save_bio_update(character_id, final_state.get("bio_update"), user_id)
    await _cs._save_status_update(character_id, final_state.get("status_update"), user_id)
    _cs.spawn_background(_cs._generate_initial_bio(character_id, user_id))

    # 触发记忆提取
    _cs.spawn_background(_cs.add_chat_memory_extraction(
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
                _cs.spawn_background(_priority_extract())
        except Exception:
            pass

    # 认知循环 v2.1：对话话题追踪（本地提取+节流；失败静默）
    try:
        from app.agent.topic_tracker import maybe_extract_topics
        _cs.spawn_background(maybe_extract_topics(
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
            _cs.spawn_background(extract_life_intent(character_id, user_id, content))
    except Exception:
        pass

    # Life Loop v1.1（2026-08-26）：刷新用户在场时间（life_state.last_user_interaction_at）
    try:
        from app.life.life_state import get_life_state

        async def _bump_user_presence(character_id: int = character_id):
            async with _cs.async_session_factory() as db:
                st = await get_life_state(db, character_id)
                st.last_user_interaction_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db.commit()

        _cs.spawn_background(_bump_user_presence())
    except Exception:
        pass

    # 记忆架构 v2.1 Phase 4b：情境驱动复习——感知 deep/emotion 或命中进行中目标 → 入队候选（异步，失败静默）
    try:
        from app.scheduling.memory_review import queue_contextual_review_for
        _cs.spawn_background(queue_contextual_review_for(
            character_id, user_id, content, final_state.get("perception"),
        ))
    except Exception:
        pass

    # 认知循环 v2.1：用户发消息 → 关系标量互动加分（bump，失败静默）
    _cs.spawn_background(_cs._bump_relationship(character_id, user_id))

    # A4 批3 M1b2（影子态）：回合末结算一次关系驱力水位（只记账，不改回复；失败静默）
    _cs.spawn_background(_cs._settle_relational_drive(character_id, user_id))
    # A4 批 3 / T1 M2a（2026-10-01）：回合末按「最近一条未接住的主动消息」全额释放（flag 关 ⇒ 零 SQL、失败静默）
    _cs.spawn_background(_cs._release_drive_on_reply(character_id, user_id, session_id))

    # 批 4 M1-挂点（2026-10-01）：回合末扫本会话 F4/F5 抽念头入池（flag 关＝零调用零查询；失败静默）
    _cs.spawn_background(_cs._settle_thought_pool_turn(character_id, user_id, session_id))

    # 认知循环 v2.1：话题完成/搁置自动切换（本地零 LLM，失败静默）
    try:
        from app.agent.topic_tracker import update_topic_resolution
        _cs.spawn_background(update_topic_resolution(character_id, user_id, content))
    except Exception:
        pass

    # 异步评估八维可视化状态（10 分钟节流，不阻塞回复）
    _cs._trigger_state_eval(character_id, user_id, content, final_text, final_state.get("status_update"))

    # P5：记忆可靠度信号（确认/纠正，$0 规则）+ 异步事实核查（节流，失败静默；G-P2-1：HTTP 与 chunked 共用）
    if reliability:
        _cs._schedule_reliability_fact_check(character_id, user_id, content, final_text)

    # 生图：用户要求画图时异步生成并追加图片消息（开关在 context 注入层控制标记输出）
    if gen_prompt:
        _cs.spawn_background(_cs._gen_image_flow(user_id, character_id, session_id, gen_prompt, img_text))

    # M3-a（2026-09-01）：工作记忆评估——turn 结束异步触发（flag 关/fail-open/30min 节流，
    # docs/archive/architecture/设计_M3工作记忆_20260901.md §3；P1-2：实现前核验 _run_agent_core 收尾存在 ✓）
    try:
        from app.application.working_state_service import maybe_evaluate_working_state
        _cs.spawn_background(maybe_evaluate_working_state(
            user_id=user_id, character_id=character_id, session_id=session_id,
            user_text=content, ai_text=final_text,
        ))
    except Exception:
        pass
