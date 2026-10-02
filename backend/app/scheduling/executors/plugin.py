"""插件/渠道主动候选执行器 — plugin（A20 批 4b）

自 ``arbiter._execute`` 逐字节搬入的 plugin 分支（2026-10-02），连同它调用的统一 Runtime 薄封装
``_plugin_proactive_runtime``。三条出口口径不变：① candidate 带 ``action`` → 交插件内部执行
（额度/违禁词/确认流都在插件里）；② ``agent_loop_social`` 开 → 走统一 Runtime；③ 关 → 旧裸生成
分支（C16 批次B 的「现状锚 + 时空纪律」护栏照原样前置）。任何一条失败都 ``return False``，
不落半句话。

机械改写只有两处：分支体缩进归零，取库由 ``async_session_factory`` 改为 ``g.session_factory()``
（会话工厂必须经 ``GateBundle`` 现取，tests/ 有 21 处按该名在 arbiter 上打桩，本模块自 import
会让桩静默失效去查真库）。``run_plugin_action`` / ``resolve_flag`` / ``chat_completion`` /
``state_guard`` / ``scheduler`` / ``app.agent.runtime`` 原本就是分支体内的局部 import（tests/
打的是各自模块属性），照原样留在函数体内，**不得上提顶层**。

``_plugin_proactive_runtime`` 在本模块定义、arbiter 侧按名重导出（tests/test_phase_e、
tests/test_d2_df、tests/test_proactive_strategy_pack 按 ``arbiter._plugin_proactive_runtime``
直接调用）；分支里调用它用的是**本模块的裸名**。logger 名故意保留 ``scheduler.arbiter``（D-1）。
"""
from app.models.character import AICharacter
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import handler
from app.utils.logger import get_logger

_logger = get_logger("scheduler.arbiter")


@handler("plugin")
async def run_plugin_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 插件主动候选（Phase 3：如渠道新动态提及）：hint 由插件提供，LLM 生成自然消息后发送；限额在插件内部
    # 插件自定义 action（社交交互层 v2：渠道评论回复走插件内部执行，保留额度/违禁词/确认流）
    _action = candidate.get("action")
    if _action:
        from app.plugins.registry import run_plugin_action
        _ok = await run_plugin_action(
            candidate.get("plugin", ""), _action, candidate,
            user_id=candidate.get("user_id"),
        )
        if _ok:
            _logger.info("Plugin action executed plugin=%s action=%s", candidate.get("plugin", ""), _action)
        return _ok
    from app.scheduling import scheduler as engine2
    hint = str(candidate.get("hint") or "")
    session_id = candidate.get("session_id")
    if not session_id or not hint:
        return False
    # Phase E（2026-08-18）：渠道/插件主动候选走统一 Runtime（Feature Flag agent_loop_social，X5 按渠道语义改名）。
    # 开=经 app/agent/runtime.py 薄封装：build_context 注入世界认知（知识不串线），hint 不落记忆；
    # 生成失败返回 False（与旧链路失败语义一致），各平台可独立回退。
    # batch G：按账号解析（缺 user_id 回落全局值，fail-open）
    from app.application.flag_service import resolve_flag
    if await resolve_flag("agent_loop_social", candidate.get("user_id")):
        return await _plugin_proactive_runtime(char_id, candidate, session_id, hint)
    async with g.session_factory() as db:
        char = await db.get(AICharacter, char_id)
    char_name = char.name if char else "我"
    from app.agent.llm_client import chat_completion
    # C16 批次B：旧裸生成分支接「现状锚 + 时空纪律」共享护栏（前置到 user prompt，
    # 原话术逐字保留；state_guard 内部 fail-open，取锚失败也只降级为纯纪律段，不抛断）
    from app.scheduling import state_guard
    _guard_block = state_guard.guard_block(
        await state_guard.current_state_anchor(character_id=char_id, user_id=candidate.get("user_id")))
    try:
        content = await chat_completion(
            messages=[
                {"role": "system", "content": "直接输出内容，不要加引号和标注。"},
                {"role": "user", "content": (
                    f"{_guard_block}"
                    f"你是{char_name}，{hint}，"
                    "请像朋友一样自然地用 1-2 句话提起这件事（不要提平台名、不要提'AI'、不要加话题标签）。"
                )},
            ],
            temperature=0.85, max_tokens=256, task="message",
        )
        content = (content or "").strip().strip('"').strip("'")
    except Exception as e:
        _logger.warning("Plugin proactive generation failed: %s", e)
        return False
    if len(content) < 2:
        return False
    # X6（2026-09-16）：策略候选声明的落库口径（内核白名单校验过）→ 供内核去重/统计；
    #   普通插件候选无该键 → message_type 仍为 "plugin"（逐字节旧行为）。
    await engine2.send_to_session(
        session_id, char_id, candidate["user_id"], content,
        message_type=candidate.get("message_type") or "plugin",
        holiday_name=candidate.get("holiday_name"),
    )
    _logger.info("Plugin proactive sent char=%d plugin=%s", char_id, candidate.get("plugin", ""))
    return True


async def _plugin_proactive_runtime(char_id: int, candidate: dict, session_id: int, hint: str) -> bool:
    """插件主动候选 → 统一 Runtime（Phase E，Feature Flag agent_loop_social，X5 按渠道语义改名）。

    - 经 app/agent/runtime.py 薄封装：build_context 注入世界认知（角色自己的记忆/状态，知识不串线）；
    - hint（渠道新动态等）作为平台公开上下文注入；save_memory=False 防机器生成文本污染记忆；
    - 生成自然消息 → 剥离动作标记 → send_to_session（与旧链路同一发送接口，message_type=plugin）。
    """
    from app.agent import runtime as _runtime
    from app.scheduling import scheduler as engine2
    # F2（2026-08-18）：渠道/插件 hint 短回复同样复用轻量上下文 Flag（默认关=全量 build_context 零变化）
    # batch G：按账号解析（缺 user_id 回落全局值，fail-open）
    from app.application.flag_service import resolve_flag
    light_context = await resolve_flag("agent_social_light_context", candidate.get("user_id"))
    # X6（2026-09-16）：策略候选（节日/生日/纪念日等）用中性提示语，不当作「外部平台动态」；
    #   普通插件候选不带 strategy 键 → 文案逐字节不变。
    _is_strategy = bool(candidate.get("strategy"))
    _lead = "【今日提醒】" if _is_strategy else "【外部动态】你在外部平台看到一条新动态："
    res = await _runtime.run_social_reply(
        character_id=char_id,
        user_id=candidate.get("user_id"),
        session_id=session_id,
        user_message=str(hint)[:500],
        extra_system=[{
            "role": "system",
            "content": (
                f"{_lead}{hint}。"
                "请像朋友一样自然地用 1-2 句话提起这件事（不要提平台名、不要提'AI'、不要加话题标签、"
                "不要输出任何动作标记）。"
            ),
        }],
        lang="zh",
        max_text=256,
        save_memory=False,
        light_context=light_context,  # F2（2026-08-18）：Flag 控制渠道/插件轻量上下文
    )
    content = (res.get("text") or "").strip()
    if res.get("status") != "ok" or len(content) < 2:
        _logger.warning("Plugin proactive runtime failed char=%d plugin=%s", char_id, candidate.get("plugin", ""))
        return False
    await engine2.send_to_session(
        session_id, char_id, candidate["user_id"], content,
        # X6（2026-09-16）：策略候选按声明口径落库（供内核去重/统计）；普通候选仍是 plugin。
        message_type=candidate.get("message_type") or "plugin",
        holiday_name=candidate.get("holiday_name"),
    )
    _logger.info("Plugin proactive sent (runtime) char=%d plugin=%s", char_id, candidate.get("plugin", ""))
    return True
