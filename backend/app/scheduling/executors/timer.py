"""定时承诺兑现执行器 — run_timer（A20 批 3b，方案 §2.3）

本模块自 ``app/scheduling/arbiter.py`` 的 ``_execute`` timer 分支与三个 timer 专用 helper
（``_build_timer_hint`` / ``_build_timer_hint_legacy`` / ``_timer_current_anchor``）逐字节搬入
（A20 批 3b，2026-10-02）。边界＝**只兑现一条到期定时承诺**：每小时限额 → L2 闭环检查 →
L3 主题熔断 → 三套话术 → 生成 → 发送 → ``mark_fired``。

⚠ 命门语义（搬动时一条都不许改）：**每小时限额命中只 ``return False``，既不移除事件也不
mark_fired**（承诺保留 pending 等下轮兑现）。这是 2026-08-16 P1 修复的结果，顺手「清理」成
移除会静默吞掉承诺。

依赖注入沿用 guards 的规矩（方案 §1 R2/R3）：``get_hourly_active_count`` 与
``async_session_factory`` 在 tests/ 里分别有 8 / 13 处 ``monkeypatch.setattr(arbiter, …)``，
**本模块不得 import 它们**——解析点一旦落到本模块，那些桩不会红，而是静默绕过打桩去查真库。
故限额判定走 ``g.hourly_active``、取库走 ``g.session_factory``；``_timer_current_anchor`` 是
纯函数式 helper，会话工厂由调用点显式传入（保持经 arbiter 现取语义）。

函数内的局部 import（``llm_client`` / ``promise_service`` / ``proactive_topic_guard`` /
``scheduler``）**原样留在函数内**：tests/ 打的是各自模块属性，提到顶层既会让桩失效，也会引入
scheduler ↔ executors 的循环 import。

logger 名故意保留旧名 ``scheduler.arbiter``（D-1 已定）：台账与排障都按该关键字检索日志。
"""
from datetime import datetime

from sqlalchemy import select

# 常量无人打桩，直接 import（与 guards 同口径）
from app.domain.proactivity.decision import MAX_PER_HOUR
from app.models.chat import ChatMessage
from app.models.character import AICharacter
from app.scheduling.executors.context import GateBundle, agent_flag_on
from app.utils.logger import get_logger

_logger = get_logger("scheduler.arbiter")


async def run_timer(item: dict, g: GateBundle) -> bool:
    """定时承诺兑现（A20 批 3b 自 arbiter._execute 的 timer 分支逐字节搬入）。"""
    from app.scheduling import scheduler as engine
    event = item["event"]
    char_id = event.character_id
    # 每小时保护
    if await g.hourly_active(char_id) >= MAX_PER_HOUR:
        _logger.info("Timer event char=%d skipped: hourly limit", char_id)
        # P1 修复（2026-08-16）：限额命中不移除事件，保留 pending 待下轮兑现（原逻辑先 mark_fired 导致承诺被静默吞掉）
        return False

    # 生成兑现消息
    async with g.session_factory() as db:
        char = await db.get(AICharacter, char_id)
    char_name = char.name if char else "我"
    from app.agent.llm_client import chat_completion
    owner = getattr(event, "owner", "ai") or "ai"
    hint_text = (event.content_hint or "").strip()
    event_kind = getattr(event, "event_type", "back") or "back"
    # 陪伴主动线（2026-08-30）：ready 承诺到点且 owner=user 时，若用户在承诺之后
    # 已主动说了结果（如"开完了/吃完了"），不再重复询问，直接标记兑现。
    # fail-open：本段任何异常只打日志并继续走原生成消息流程，绝不让承诺丢失。
    # L2/L3（2026-09-09 主体归属治理）：两个 flag 均默认关；关=逐字节走旧路径。
    _topic_guard_on = agent_flag_on("proactive_topic_guard")
    _render_fix_on = agent_flag_on("timer_render_subject_fix")
    _settled_check = (
        event_kind == "ready" and (owner == "user" or _topic_guard_on)
        and event.session_id and event.source_message_id
    )
    if _settled_check:
        try:
            from app.scheduling.promise_parser import ready_result_seen
            async with g.session_factory() as _db:
                _rows = (await _db.execute(
                    select(ChatMessage.content)
                    .where(
                        ChatMessage.session_id == event.session_id,
                        ChatMessage.sender_type == "user",
                        ChatMessage.id > event.source_message_id,
                    )
                    .order_by(ChatMessage.id.desc()).limit(5)
                )).all()
            _texts = [r[0] for r in reversed(_rows) if r[0]]
            if _texts and ready_result_seen(_texts, hint_text):
                from app.scheduling.promise_service import mark_fired
                await mark_fired(event.id)
                _logger.info("Timer ready event %d skipped: user already reported result", event.id)
                return True
            # L2：闭环检查扩到 owner=ai，并纳入「离场/婉拒」词（吃过面了/去上课了/不用了…）——
            # 用户已对该主题收口，到点就不再催（owner=ai 的 ready 此前完全不查）。
            if _topic_guard_on and _texts:
                try:
                    from app.scheduling.proactive_topic_guard import (
                        topic_bucket as _topic_bucket, topic_closed_by_user as _topic_closed,
                    )
                    if _topic_closed(_texts, _topic_bucket(hint_text)):
                        from app.scheduling.promise_service import mark_fired
                        await mark_fired(event.id)
                        _logger.info(
                            "Timer ready event %d settled without message (topic closed by user)", event.id,
                        )
                        return True
                except Exception as _close_err:
                    _logger.warning("Timer topic-closed check failed (fail-open): %s", _close_err)
        except Exception as e:
            _logger.warning("Ready result skip check failed (fail-open): %s", e)

    # L3：timer 主题熔断——近窗同主题主动消息已达上限 → 兑现（mark_fired）但不补发，承诺不丢
    if _topic_guard_on:
        try:
            from app.scheduling.proactive_topic_guard import should_suppress
            _sup, _reason = await should_suppress(char_id, hint_text or event_kind)
            if _sup:
                from app.scheduling.promise_service import mark_fired
                await mark_fired(event.id)
                _logger.info("Timer %d suppressed by topic guard: %s", event.id, _reason)
                return True
        except Exception as _guard_err:
            _logger.warning("Timer topic guard fail-open %d: %s", event.id, _guard_err)
    # L2：按 (owner, event_type, 自理/为用户) 三套话术；flag 关时走 legacy（逐字节等价）
    if _render_fix_on:
        hint = _build_timer_hint(char_name, owner, event_kind, hint_text)
    else:
        hint = _build_timer_hint_legacy(char_name, owner, event_kind, hint_text)
    # L2：最小现状锚点（零 LLM 组装；flag 开才附加，常态不多查库）
    anchor = ""
    if _render_fix_on:
        try:
            anchor = await _timer_current_anchor(event, g.session_factory)
        except Exception as _anchor_err:
            _logger.warning("Timer anchor fail-open %d: %s", event.id, _anchor_err)
            anchor = ""
    from app.agent.llm_client import load_character_reasoning_level
    _timer_reasoning = ""  # D2-B（2026-08-18）：定时承诺关闭深度思考，恒为空串（extra_meta 不再带 reasoning）
    try:
        _rl = await load_character_reasoning_level(char_id)
        # D2-B（2026-08-18）：定时承诺关闭深度思考——统一走挡位 1/0 的 prompt 引导分支
        # （挡位 1 保留「先在心里简短想一下」引导；_timer_reasoning 恒为空串，extra_meta 不再带 reasoning）
        _msgs = [{"role": "system", "content": "直接输出内容，不要加引号和标注。"},
                 {"role": "user", "content": hint}]
        if _rl == 1:
            _msgs[0] = {"role": "system", "content": "先在心里简短想一下，然后直接输出内容，不要加引号和标注。"}
        # L2：把当下现状（时间/最近对话/生活相位）拼进 system，给模型判断是否该打扰
        if _render_fix_on and anchor:
            _msgs[0] = {
                "role": "system",
                "content": (_msgs[0]["content"] + "\n" + anchor
                            + "\n若结合现状判断此刻不该打扰用户（用户已吃过/已在上课/已离场/已婉拒），只输出 __SKIP__。"),
            }
        content = await chat_completion(messages=_msgs, temperature=0.8, max_tokens=256, task="message")
        content = (content or "").strip().strip('"').strip("'")
    except Exception as e:
        _logger.warning("Timer message generation failed: %s", e)
        content = "我回来啦！"
    # L2 零 LLM 输出闸门：模型判断不该打扰 → 兑现不发
    if _render_fix_on and content.upper().startswith("__SKIP__"):
        from app.scheduling.promise_service import mark_fired
        await mark_fired(event.id)
        _logger.info("Timer %d skipped via __SKIP__", event.id)
        return True
    if not content or len(content) < 2:
        content = "我回来啦！"

    _timer_extra = None
    if _timer_reasoning:
        import json as _json
        _timer_extra = _json.dumps({"reasoning": _timer_reasoning}, ensure_ascii=False)
    await engine.send_to_session(
        event.session_id, event.character_id, event.user_id,
        content, message_type="timer",
        extra_meta=_timer_extra,
    )
    from app.scheduling.promise_service import mark_fired
    await mark_fired(event.id)
    return True


def _build_timer_hint(char_name: str, owner: str, event_kind: str, hint_text: str) -> str:
    """L2（2026-09-09）：定时承诺到期话术，按 (owner, event_type) 三套。

    核心纠偏：AI 自己去做的事（吃饭/洗澡/开会/睡觉）到点是**自述回来**，绝不招呼用户；
    owner=ai 且 ready 只用于「为用户做、好了叫 TA」的场景，且必须用 hint 里的真实事物，
    **不再写死'粥好了'这个 few-shot 例子**（原模板把所有 ready 都往粥上带）。
    """
    ht = (hint_text or "").strip()
    if event_kind == "ready":
        if owner == "user":
            return (
                f"你是{char_name}。之前用户对你说过"
                + (f"「{ht}」" if ht else "要去做某件事")
                + "，并承诺了大概的时间，现在时间到了。请自然地关心地问一句：TA 是不是弄好了/好了吗"
                  "（1句话，像朋友一样）。不要替用户说'好了'。"
            )
        # owner=ai 且 ready：只用于「你为用户准备的东西好了」
        return (
            f"你是{char_name}，之前你为用户准备"
            + (f"「{ht}」" if ht else "某件事")
            + "，并说好到点告诉 TA，现在时间到了。请用 1 句话自然地告诉用户这件事完成了"
              "（像朋友一样）。必须只描述你为 TA 准备的这件事本身、用上面提到的真实事物，"
              "不要凭空换成粥/饭/其他食物；不要催促、不要连用'快来/趁热/别凉了'；只说一次。"
        )
    if owner == "user":
        return (
            f"你是{char_name}。之前用户对你说过"
            + (f"「{ht}」" if ht else "要去做某件事")
            + "，并承诺了大概的时间，现在时间到了。请自然地关心地问一句：TA 是不是回来了/做完了"
              "（1句话，像朋友一样）。不要替用户说'你回来了'。"
        )
    # owner=ai + back：AI 自己去做自己的事，到点自述回来——不招呼用户
    return (
        f"你是{char_name}，之前你说要去做自己的事"
        + (f"「{ht}」" if ht else "（去忙一下）")
        + "，说好之后回来，现在时间到了。请用第一人称、1 句话自然地告诉用户你回来了/这件事做完了"
          "（例如'我吃完回来了''我忙完了，回来啦'）。"
          "这件事是你自己去做的、不是给用户做的：禁止招呼用户去做什么，"
          "禁止出现'好了快来吃/快来/趁热/给你留了'这类把用户当受益方的措辞。"
    )


def _build_timer_hint_legacy(char_name: str, owner: str, event_kind: str, hint_text: str) -> str:
    """旧话术（flag timer_render_subject_fix 关时使用，保持上线前行为，零变化）。"""
    ht = (hint_text or "").strip()
    if event_kind == "ready":
        if owner == "user":
            return (
                f"你是{char_name}。之前用户对你说过"
                + (f"「{ht}」" if ht else "要去做某件事")
                + "，并承诺了大概的时间，现在时间到了。请自然地关心地问一句：他/她是不是弄好了/好了吗（1句话，像朋友一样）。不要替用户说'好了'。"
            )
        return (
            f"你是{char_name}，之前你和用户说"
            + (f"「{ht}」" if ht else "要弄好某件事")
            + "，现在时间到了。请自然地告诉用户你答应弄好的事完成了（比如'粥好了'，1句话，像朋友一样）。"
        )
    if owner == "user":
        return (
            f"你是{char_name}。之前用户对你说过"
            + (f"「{ht}」" if ht else "要去做某件事")
            + "，并承诺了大概的时间，现在时间到了。请自然地关心地问一句：他/她是不是回来了/做完了（1句话，像朋友一样）。不要替他/她说'你回来了'。"
        )
    return (
        f"你是{char_name}，之前你和用户说"
        + (f"「{ht}」" if ht else "要去办点事")
        + "，现在时间到了。请自然地告诉用户你回来了/做完了（1句话，像朋友一样）。"
    )


async def _timer_current_anchor(event, session_factory) -> str:
    """L2：零 LLM 组装「当下现状」锚点——当前北京时间 + 最近几句对话 + AI 生活相位/位置。

    任一段读不到就跳过该行（不报错、不阻塞）；整体异常由调用方 fail-open。
    """
    from datetime import timedelta as _td
    from datetime import timezone as _tz

    bj = datetime.now(_tz(_td(hours=8))).strftime("%Y-%m-%d %H:%M")
    lines = [f"现在是北京时间 {bj}。"]
    try:
        async with session_factory() as db:
            rows = (await db.execute(
                select(ChatMessage.content, ChatMessage.sender_type)
                .where(ChatMessage.session_id == event.session_id)
                .order_by(ChatMessage.id.desc()).limit(6)
            )).all()
        recent = [f"{'用户' if r[1] == 'user' else '你'}：{r[0]}" for r in reversed(rows) if r[0]]
        if recent:
            lines.append("最近几句对话（判断用户是否已吃过/已在上课/已离场）：\n" + "\n".join(recent[-3:]))
    except Exception:
        pass
    try:
        from app.life.life_state import get_life_state
        async with session_factory() as db:
            st = await get_life_state(db, event.character_id)
        if st is not None:
            lines.append(f"你当前的生活状态：phase={getattr(st, 'phase', '')}，位置={getattr(st, 'location', '')}"
                         f"/{getattr(st, 'current_room', '') or ''}。")
    except Exception:
        pass
    # 2026-09-17 批次二任务2.2：并列组装「用户权威现状」（共享 location + 已启用低敏槽 +
    # User 已授权城市），与 AI 自己的 life_states 同处一个 section——低活跃朋友角色不再只靠
    # 各自记忆里的旧位置碎片（用户 8 月底已回湛江，AI 仍在 9 月反复「你在长沙」）。
    try:
        async with session_factory() as db:
            _ch = await db.get(AICharacter, event.character_id)
        _uid = getattr(_ch, "user_id", None)
        if _uid:
            from app.memory.current_state import current_user_state_anchor
            _user_anchor = await current_user_state_anchor(
                character_id=event.character_id, user_id=_uid, include_profile_location=True)
            if _user_anchor:
                lines.append(_user_anchor.strip())
    except Exception:
        pass
    return "\n".join(lines)
