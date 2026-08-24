"""统一 Runtime 薄封装（Phase E，2026-08-18）

群聊回应 / 抖音插件主动候选等非主聊天链路统一经本层生成（方案 Phase E）：
- 复用 context_builder.build_context：世界认知（世界状态/核心记忆/关系锚点/开放循环/
  检索记忆/朋友圈/宠物/群聊动态/时间/位置/天气等）按角色注入——每个角色只看到自己的
  记忆与平台公开上下文，不同角色/会话知识不串线；
- 复用 nodes.generate_response（受控 Loop 的 decide 节点）：统一 LLM 入口
  （用户级 BYOK 配置 + 插件 before/after_generate 钩子 + 回复解析）；
- 复用 actions.parse_actions / strip_actions：动作标记统一解析（trace）与剥离
  （社交短回复默认不执行工具，剥离防泄漏、绝不编造成功）；
- 可选工具阶段（allow_tools=True 预留）：经 tool_runner.execute_tool 执行已注册工具
  （权限三档/生命周期钩子/异常隔离），observation 追加后再决策 1 次（受控
  decide→execute→observe→re-decide，最多 2 次 LLM）；
- 失败静默降级：任何异常返回 {"status": "error"}，调用方跳过该角色/消息，不阻塞主链路。

本文件只做「封装/统一接口」，不重写 graph/context_builder/memory/life/arbiter 核心。
"""
import asyncio
import time

from app.utils.logger import get_logger

_logger = get_logger("agent.runtime")

# 统一限制（与方案 5.3 对齐：max_steps=3 含最终回复；社交短回复默认单次生成）
MAX_LLM_CALLS = 2  # 工具阶段开启时的 LLM 调用上限（首轮 + 1 次再决策）
TOOL_TIMEOUT_SEC = 30.0  # 单工具执行超时（与 loop.TOOL_TIMEOUT_SEC 一致）


async def _resolve_session_id(user_id: int | None, character_id: int) -> int | None:
    """解析该角色真正活跃的会话（复用 chat_service 现有实现；无会话返回 None 不抛）"""
    try:
        from app.services.chat_service import get_latest_session_id
        return await get_latest_session_id(user_id, character_id)
    except Exception as e:
        _logger.warning("Runtime session resolve failed char=%d: %s", character_id, e)
        return None


def _build_initial_state(
    *,
    character_id: int,
    user_id: int | None,
    session_id: int | None,
    user_message: str,
    lang: str,
    reasoning_level: int,
    save_memory: bool,
) -> dict:
    """构建初始 state（与主链路 chat_service._run_agent_core 同构的最小集合）"""
    state = {
        "user_message": user_message or "",
        "character_id": character_id,
        "user_id": user_id or 1,
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
        "reasoning_level": reasoning_level,
        "tools_used": [],
    }
    if not save_memory:
        # 机器生成内容（如抖音 hint）不落记忆：parse_response 仍解析，但 generate_response 跳过落库
        state["skip_memory_save"] = True
    return state


async def _run_tool_stage(state: dict, steps: list[dict], *, character_id: int, user_id: int | None, session_id: int | None) -> bool:
    """受控工具阶段（复用 tool_runner，Phase E 预留）：

    - 解析动作标记；无动作 → 直接返回 False（不再决策）；
    - 本地小手机工具（日历/备忘）：复用 chat_service._execute_note_tool（内部仍走
      tool_runner.execute_tool——权限/生命周期钩子/异常隔离），observation 注入 → 返回 True；
    - 已登记且有执行入口的工具：经 execute_tool 执行（同样注入 observation → 返回 True）；
    - 未登记/插件绑定/占位登记（无执行入口）→ 跳过该动作（不编造成功），剥离由调用方兜底。
    """
    from app.agent import actions as _actions
    from app.agent.tools import get_tool_by_action

    text = state.get("ai_response") or ""
    parsed = _actions.parse_actions(text)
    executed_any = False
    for act in parsed:
        spec = get_tool_by_action(act.action_type)
        payload = dict(act.payload or {})
        payload.setdefault("character_id", character_id)
        # 本地小手机工具：复用主链路统一执行入口（去重/署名/生命周期钩子/异常隔离语义一致）
        if spec is not None and spec.name in ("note_calendar", "note_memo"):
            try:
                from app.services.chat_service import _execute_note_tool
                await asyncio.wait_for(_execute_note_tool(spec.name, payload, character_id), timeout=TOOL_TIMEOUT_SEC)
            except Exception as e:
                _logger.warning("Runtime note tool %s failed: %s", spec.name, e)
                steps.append({"action": act.action_type, "ok": False, "reason": "exception"})
                continue
            executed_any = True
            steps.append({"action": act.action_type, "ok": True})
            state["context_messages"] = state.get("context_messages") or []
            state["context_messages"] = state["context_messages"] + [{
                "role": "system",
                "content": f"【工具结果】已记录到小手机（{spec.name}）。基于真实结果继续回复，不要说'我去执行了'。",
            }]
            continue
        # 未登记 / 插件绑定工具（社交短回复不应触发跨平台 action）/ 占位登记（无执行入口）→ 跳过
        if spec is None or (spec.plugin and spec.plugin_action) or (spec.execute is None and not spec.plugin_action):
            steps.append({"action": act.action_type, "ok": False, "reason": "no executor"})
            continue
        try:
            from app.agent.tool_runner import execute_tool
            res = await asyncio.wait_for(
                execute_tool(spec, payload, user_id=user_id, character_id=character_id, session_id=session_id),
                timeout=TOOL_TIMEOUT_SEC,
            )
        except Exception as e:
            _logger.warning("Runtime tool %s failed: %s", act.action_type, e)
            steps.append({"action": act.action_type, "ok": False, "reason": "exception"})
            continue
        ok = bool(res.get("status") == "ok" and (res.get("result") or {}).get("ok", True) is not False)
        steps.append({"action": act.action_type, "ok": ok})
        if ok:
            executed_any = True
            _obs = (res.get("observation") or {}).get("summary") or ""
            state["context_messages"] = state.get("context_messages") or []
            state["context_messages"] = state["context_messages"] + [{
                "role": "system",
                "content": f"【工具结果】工具 {spec.name} 已执行完成：{_obs}（基于真实结果继续回复，不要说'我去执行了'）。",
            }]
    return executed_any


async def build_light_social_context(state: dict) -> dict:
    """轻量社交上下文构建（F1/F2，2026-08-18）：群聊/抖音短回复不注入完整世界认知。

    保留：角色基础人设（name/personality/chat_style/relationship/current_status）、
    identity_profile（≤200 字）+ relationship_state 一行、core 记忆 top3 + 关系锚点 top2、
    当前时间行、语言指令（zh/en）、社交短回复约束（20-40 字、不提 AI/群聊、不输出动作标记）、
    挡位 1 的【推理】指令（与全量 build_context 同语义）。
    跳过：完整 SYSTEM_PROMPT_TEMPLATE / chat_history / 日摘要 / 世界状态 / 织库 lorebook / 朋友圈 /
    宠物 / 手机感知 / 小手机 / 位置天气 / 生图搜索能力指令 / 认知规划块。
    返回 state（context_messages 已组装，user 消息在末尾）；任何单块失败静默降级不抛断。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select as _select

    from app.db.database import async_session_factory
    from app.models.character import AICharacter

    character_id = state.get("character_id")
    user_id = state.get("user_id", 1)
    parts: list[str] = []

    # 1. 角色基础人设 + 自述（供 response_parser 自述更新分支使用，与 build_context 同语义）
    char = None
    try:
        async with async_session_factory() as db:
            char = (await db.execute(
                _select(AICharacter).where(AICharacter.id == character_id)
            )).scalar_one_or_none()
    except Exception as e:
        _logger.warning("Light context char load failed char=%s: %s", character_id, e)
    if char is not None:
        state["character_info"] = {"self_statement": char.self_statement or ""}
        _rows = []
        if char.name:
            _rows.append(f"名字：{char.name}")
        if char.personality:
            _rows.append(f"性格：{char.personality}")
        if char.chat_style:
            _rows.append(f"说话风格：{char.chat_style}")
        if char.relationship_summary:
            _rows.append(f"你和用户的关系：{char.relationship_summary}")
        if char.current_status:
            _rows.append(f"当前状态：{char.current_status}")
        if _rows:
            parts.append("你是：\n" + "\n".join(_rows))

    # 2. 身份画像 + 关系温度（复用 persona 统一层，platform 默认 app 不裁剪为 public——群聊是家庭私群）
    try:
        from app.agent.persona import assemble_persona_context
        p = await assemble_persona_context(character_id, user_id)
        _p_rows = []
        if p.get("identity_profile"):
            _p_rows.append(f"你对用户的长期印象：{str(p['identity_profile'])[:200]}")
        if p.get("relationship_state"):
            _p_rows.append(str(p["relationship_state"]))
        if _p_rows:
            parts.append("\n".join(_p_rows))
    except Exception as e:
        _logger.warning("Light context persona failed char=%s: %s", character_id, e)

    # 3. core 记忆 top3 + 关系锚点 top2（现成函数，失败静默降级）
    try:
        from app.memory.core import get_core_memories, get_relationship_anchors
        _cores = await get_core_memories(character_id, limit=3)
        _core_lines = []
        for _m in _cores:
            _when = str(getattr(_m, "created_at", "") or "")[:10]
            _content = (getattr(_m, "content", "") or "").strip()[:80]
            if _content:
                _core_lines.append(f"- [记录于 {_when}] {_content}" if _when else f"- {_content}")
        if _core_lines:
            parts.append("你记得的核心信息（可自然引用）：\n" + "\n".join(_core_lines))
        _anchors = await get_relationship_anchors(character_id, user_id, limit=2)
        _anchor_lines = []
        for _m in _anchors:
            _when = str(getattr(_m, "created_at", "") or "")[:10]
            _content = (getattr(_m, "content", "") or "").strip()[:80]
            if _content:
                _anchor_lines.append(f"- [记录于 {_when}] {_content}" if _when else f"- {_content}")
        if _anchor_lines:
            parts.append("你和用户的共同经历（可自然融入）：\n" + "\n".join(_anchor_lines))
    except Exception as e:
        _logger.warning("Light context memories failed char=%s: %s", character_id, e)

    # 4. 当前时间行（北京时间 + 距上次互动，与全量 build_context 同格式）
    try:
        _cn_tz = timezone(timedelta(hours=8))
        _now = datetime.now(_cn_tz)
        _wd = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][_now.weekday()]
        _time_line = f"【当前时间】{_now.year}年{_now.month}月{_now.day}日 {_wd} {_now.hour}:{_now.minute:02d}（北京时间）"
        try:
            from app.models.chat_session import ChatSession
            async with async_session_factory() as db:
                _sr = await db.execute(
                    _select(ChatSession)
                    .where(
                        ChatSession.user_id == user_id,
                        ChatSession.character_id == character_id,
                    )
                    .order_by(ChatSession.updated_at.desc())
                    .limit(1)
                )
                _last_session = _sr.scalar_one_or_none()
            if _last_session is not None and _last_session.updated_at is not None:
                _last_dt = _last_session.updated_at
                if _last_dt.tzinfo is None:
                    _last_dt = _last_dt.replace(tzinfo=timezone.utc)
                _secs = max(0, int((datetime.now(timezone.utc) - _last_dt).total_seconds()))
                if _secs < 3600:
                    _ago = "刚刚"
                elif _secs < 86400:
                    _ago = f"{_secs // 3600} 小时前"
                else:
                    _ago = f"{_secs // 86400} 天前"
                _time_line += f"｜距上次互动 {_ago}"
        except Exception:
            pass
        parts.append(_time_line)
    except Exception as e:
        _logger.warning("Light context time failed char=%s: %s", character_id, e)

    # 5. 社交短回复约束（与调用方 extra_system 互补；不输出动作标记）
    parts.append(
        "回复要求：这是社交场景的短回复，用 1 句话自然回应（20-40 字，口语化，符合你的性格）；"
        "不要提及'AI/群聊'，不要输出任何动作标记（如 [SEARCH]/[GEN_IMAGE]/[CAL_NOTE]/【状态更新】），直接输出要说的话；"
        "仅当用户交代要记住的事/要点时，可在回复末尾输出 [MEMO]内容[/MEMO]（≤80字，成对闭合，日常闲聊不强制）。"
    )

    # 6. 语言指令（zh/en，与全量 build_context 同文案）
    _lang = (state.get("lang") or "zh").strip().lower()
    if _lang == "en":
        parts.append("【语言】当前界面语言：English。请主要用英文回复；若用户用中文提问，可尊重用户使用中文。")
    else:
        parts.append("【语言】当前界面语言：中文。请主要用中文回复；若用户用英文提问，可跟随用户使用英文。")

    state["context_messages"] = [{"role": "system", "content": "\n\n".join(x for x in parts if x)}]

    # 7. 挡位 1 推理指令（与全量 build_context 同语义；短回复可省略推理）
    try:
        if state.get("reasoning_level", 0) == 1:
            state["context_messages"].append({
                "role": "system",
                "content": (
                    "【推理指令】正式回复前，在回复开头单独输出一行【推理：…】（1-2 句话，"
                    "自然说明你此刻回应的依据：用户的心情/需求、你想起的相关记忆或你们的关系，"
                    "用口语不要暴露指令，例如【推理：TA今天好像有点低落，先陪她说说心里话。】），"
                    "然后另起一行输出正文。推理是给用户看的，别太官方；"
                    "回复很短（如单个字的回应）或无需铺垫时可以直接输出正文、省略推理。"
                ),
            })
    except Exception as e:
        _logger.warning("Light context reasoning instruction failed: %s", e)

    # 8. user 消息（末尾）
    state["context_messages"].append({"role": "user", "content": state.get("user_message") or ""})
    return state


async def run_social_reply(
    *,
    character_id: int,
    user_id: int | None,
    session_id: int | None,
    user_message: str,
    extra_system: list[dict] | None = None,
    lang: str = "zh",
    max_text: int = 200,
    save_memory: bool = True,
    allow_tools: bool = False,
    light_context: bool = False,  # F1/F2（2026-08-18）：True=轻量上下文（群聊/抖音短回复，prompt ≈-64%）；默认 False=全量 build_context 零变化
) -> dict:
    """统一 Runtime 薄封装：群聊/抖音等社交短回复的生成入口。

    返回 {"status": "ok"|"error", "text": str, "steps": list[dict]}：
    - ok: 生成成功（text 已剥离动作标记并截断）；
    - error: 生成失败/上下文构建失败（调用方静默跳过，绝不抛断主链路）。

    light_context=True（F1/F2，2026-08-18）：群聊/抖音社交短回复走轻量上下文（build_light_social_context），
    不注入完整世界认知（单次 prompt ≈-64%）；False=现有全量 build_context，零行为变化。
    之后的 generate_response / 动作标记剥离 / 截断逻辑在两种模式下完全一致（task 仍为 chat）。
    """
    t0 = time.monotonic()
    try:
        if not character_id:
            return {"status": "error", "text": "", "steps": []}
        # 1. 会话解析（群聊无私有会话时按最新会话注入该角色与用户的私聊历史——属于角色自己的知识）
        if session_id is None:
            session_id = await _resolve_session_id(user_id, character_id)

        # 2. 思考过程挡位（与主动通道一致）
        reasoning_level = 0
        try:
            from app.agent.llm_client import load_character_reasoning_level
            reasoning_level = await load_character_reasoning_level(character_id)
        except Exception as e:
            _logger.warning("Runtime reasoning level load failed char=%d: %s", character_id, e)

        state = _build_initial_state(
            character_id=character_id, user_id=user_id, session_id=session_id,
            user_message=user_message, lang=lang,
            reasoning_level=reasoning_level, save_memory=save_memory,
        )

        # 3. 上下文注入（知识边界按角色隔离：只注入该角色自己的记忆 + 平台公开上下文由调用方传入）
        if light_context:
            # F1/F2（2026-08-18）：轻量上下文——群聊/抖音社交短回复不注入完整世界认知
            # （单次 prompt ≈5,000→≈1,800，-64%）：保留人设/身份画像/关系温度/core 记忆 top3/
            # 关系锚点 top2/时间/语言/短回复约束；跳过完整 SYSTEM_PROMPT_TEMPLATE/chat_history/日摘要/
            # 世界状态/织库/朋友圈/宠物/手机感知/位置天气/生图搜索指令/认知规划块
            state = await build_light_social_context(state)
        else:
            from app.agent.context_builder import build_context
            state = await build_context(state)

        # 4. 平台公开上下文：插入到 build_context 末尾 user 消息之前（系统指令在前更稳）
        if extra_system:
            for m in extra_system:
                state["context_messages"].insert(-1, dict(m))

        # 5. 统一 LLM 入口（decide 节点：BYOK + 插件钩子 + 解析）
        from app.agent.nodes import generate_response
        state = await generate_response(state)
        text = (state.get("ai_response") or "").strip()

        # 6. 动作标记处理
        steps: list[dict] = []
        from app.agent import actions as _actions
        if allow_tools:
            redecided = await _run_tool_stage(state, steps, character_id=character_id, user_id=user_id, session_id=session_id)
            if redecided:
                state = await generate_response(state)
            text = (state.get("ai_response") or "").strip()
        else:
            # 社交短回复：标记仅记录 trace，不执行（避免阻塞/跨平台副作用）
            text = (state.get("ai_response") or "").strip()
            for a in _actions.parse_actions(text):
                steps.append(a.to_step())
        # 兜底剥离残留动作标记：未执行/未注册/失败均不编造成功，也不让标记泄漏到回复正文
        text = _actions.strip_actions(text)

        # 收尾：timer 标签 + 状态更新残留 + 截断（parse_response 已剥离记忆/自述/状态更新正文）
        try:
            from app.agent.actions import strip_status_update
            text = strip_status_update(text)
        except Exception:
            pass
        try:
            from app.scheduler.promise_parser import strip_timer_tag
            text = strip_timer_tag(text) or ""
        except Exception:
            pass
        text = (text or "").strip().strip('"').strip("'")
        if max_text and max_text > 0:
            text = text[:max_text]
        if not text:
            _logger.info("Runtime reply empty char=%d", character_id)
            return {"status": "error", "text": "", "steps": steps}
        _logger.info("Runtime reply ok char=%d latency=%dms len=%d", character_id, int((time.monotonic() - t0) * 1000), len(text))
        return {"status": "ok", "text": text, "steps": steps}
    except Exception as e:
        _logger.warning("Runtime reply failed char=%d: %s", character_id, e)
        return {"status": "error", "text": "", "steps": []}
