"""主动消息 LLM 生成助手 — 按思考挡位生成 + 角色自主搜索 + 生日/纪念日/节日祝福。

本模块自 ``scheduling/message_generator.py`` 逐字节搬入（A22 ③b，2026-10-02）。边界＝**LLM 调用与
节日消息生成**。tests/ 在 ``message_generator`` 模块对象上对 ``chat_completion`` / ``load_character_reasoning_level``
/ ``_logger``（留原地）与 ``_gen_with_reasoning`` / ``_proactive_self_search``（同批搬走）打桩，故被搬走
代码引用这些名字时，一律在函数体内 ``from app.scheduling import message_generator as _mg`` 现取
``_mg.<name>``（调用时刻解析，桩才打得上）。message_generator 侧靠具名重导出保留原调用点，本模块**绝不**
在顶层 import message_generator（顶层回指必成环：mg → llm → mg）。
"""
from app.scheduling import state_guard


async def _gen_with_reasoning(messages: list[dict], character_id: int | None, user_id: int | None,
                              temperature: float, max_tokens: int) -> tuple[str, str]:
    """主动消息 LLM 调用（2026-08-15）：按角色思考挡位决定是否开推理。

    0=关闭（默认） / 1=简单思考（提示词注入「先简短思考再输出」）/ 2=深度思考
    （include_reasoning=True 拿 reasoning_content）。返回 (content, reasoning)。
    挡位 0/1 时 reasoning 为空串（思考不落库展示）。"""
    from app.scheduling import message_generator as _mg
    level = await _mg.load_character_reasoning_level(character_id)
    if level == 1:
        msgs = [
            {"role": "system", "content": "先在心里简短想一下发什么合适（思考不外显），然后直接输出要说的话，不要加引号和标注。"},
            *messages,
        ]
        return await _mg.chat_completion(messages=msgs, temperature=temperature, max_tokens=max_tokens,
                                     task="message", user_id=user_id), ""
    if level == 2:
        resp = await _mg.chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens,
                                     include_reasoning=True, task="message", user_id=user_id)
        if isinstance(resp, tuple):
            content, reasoning = resp
            if reasoning:
                _mg._logger.info("Active msg reasoning captured: %d chars (char=%s)", len(reasoning), character_id)
            return content, reasoning
        return resp, ""
    return await _mg.chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens,
                                 task="message", user_id=user_id), "" 

# ── S1 第二步（2026-09-27）：主动消息链「角色自主搜索」（proactive_self_search 开关，默认关）──
# 复刻 agent/loop.py run_search_loop 的 self 分支语义（initiator="self"），唯一差异＝regen 走本文件
# _gen_with_reasoning（task="message"、按角色思考挡位），不走 nodes.generate_response（后者 task="chat"
# 且忽略主动链的思考挡位/上下文，会把主动消息带偏）。开关关时本函数永不被调用（逐字节旧行为）。

async def _proactive_self_search(response: str, *, messages: list[dict],
                                 character_id: int | None, user_id: int | None) -> tuple[str, bool]:
    """主动消息首轮输出若含 [SEARCH]，跑一次受控自主搜索；返回 (最终正文, 本轮是否不产出消息)。

    口径（用户 2026-09-27 拍板，与 run_search_loop self 分支一致）：
    - 无 [SEARCH] / 被节流 / 搜索注入关 / 搜索失败 → (原候选剥离标记后, False)：仍发原候选，不套「不说」；
    - 搜索成功且模型选择「说」 → (再生成正文, False)；
    - 搜索成功且模型「什么都不说」（正文空/只剩标记） → ("", True)：由调用方 return []（走 segments 为空不发送那条路）。
    本批不落小手机浏览记录（save_history 留空）；搜索/重试/轮数上限复用 loop.py 既有常量。
    """
    from app.scheduling import message_generator as _mg
    from app.agent.actions import extract_search
    from app.agent.loop import MAX_SEARCH_ROUNDS, SEARCH_RETRY, _SEARCH_RESULT_TEMPLATE_SELF
    from app.application.chat.tools import _run_web_search, _search_throttle, _search_inject_enabled

    body = response or ""
    searched = False  # 只有「成功搜索并再生成」后，空正文才判为「本轮不产出消息」
    try:
        round_no = 1
        while round_no <= max(1, MAX_SEARCH_ROUNDS):
            clean, query = extract_search(body)
            if not query:  # 无 [SEARCH]（或补查轮已无标记）→ 剥离后原样返回
                body = clean
                break
            # 节流 / 搜索注入开关门禁：不过 → 仍发原候选（剥离标记），不套「不说」
            if not (_search_throttle(user_id) and _search_inject_enabled()):
                _mg._logger.info("Proactive self search throttled/off char=%s query=%.60s", character_id, query)
                body = clean
                break
            _mg._logger.info("Proactive self search char=%s round=%d query=%.60s", character_id, round_no, query)
            # 执行搜索（与聊天链同一 _run_web_search 原语；空结果重试 SEARCH_RETRY 次）
            result = ""
            for _attempt in range(SEARCH_RETRY + 1):
                result = await _run_web_search(query)
                if result:
                    break
            if not result:  # 搜索失败/空 → 仍发原候选（剥离标记）
                _mg._logger.info("Proactive self search failed char=%s round=%d query=%.60s", character_id, round_no, query)
                body = clean
                break
            # observe：注入 self 结果模板（只作参考）→ regen 走 _gen_with_reasoning（不落浏览记录）
            _msgs = messages + [{
                "role": "system",
                "content": _SEARCH_RESULT_TEMPLATE_SELF.format(result=result),
            }]
            regen_text, _ = await _mg._gen_with_reasoning(
                _msgs, character_id, user_id, temperature=0.9, max_tokens=512)
            body = (regen_text or "").strip()
            searched = True
            round_no += 1
        # 兜底剥离（幂等）：self 分支下正文空 = 本轮不产出消息（仅当确实搜过一次才成立）
        body = extract_search(body)[0]
        return body, (searched and not body.strip())
    except Exception as e:
        _mg._logger.warning("Proactive self search loop failed char=%s: %s", character_id, e)
        # 异常一律发原候选（剥离标记），绝不因搜索环节把消息吞掉
        try:
            return extract_search(response or "")[0], False
        except Exception:
            return response or "", False


async def generate_birthday_message(
    character_name: str,
    character_personality: str,
    user_name: str,
    character_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """生成生日祝福"""
    from app.scheduling import message_generator as _mg
    identity = await _mg._load_identity_block(character_id)
    # C16 批次A（2026-09-25）：前置「现状锚 + 时空纪律」共享护栏（唯一来源 scheduling/state_guard.py）
    guard = state_guard.guard_block(
        await state_guard.current_state_anchor(character_id=character_id, user_id=user_id))
    prompt = (
        guard +
        f"你是「{character_name}」，今天是好友「{user_name}」的生日！\n"
        f"你的性格：{character_personality or '友善、温暖'}\n"
        + (f"\n你的身份（以这里为准，不要混淆你与用户/用户对象）：\n{identity}\n" if identity else "")
        + "\n请发一条真诚、温暖的生日祝福，像你们的真实关系那样（关系亲密就亲密些，朋友就朋友些）。\n"
        "要表达开心和祝福，可以回忆一下你们之前的聊天。\n"
        "不要用「作为AI...」的语气，要真实自然。\n"
        "1-3句话就好。\n"
    )
    messages = [
        {"role": "system", "content": "你是一个真实的朋友，正在给好友送出生日祝福。直接输出消息内容。"},
        {"role": "user", "content": prompt},
    ]
    response = await _mg._gen_with_reasoning(messages, character_id, user_id, temperature=0.8, max_tokens=400)
    if isinstance(response, tuple):  # 2026-08-20 七夕死循环修复：_gen_with_reasoning 恒返回 (content, reasoning)
        response = response[0] or ''
    return response.strip().strip('"').strip("'")


async def generate_anniversary_message(
    character_name: str,
    character_personality: str,
    user_name: str,
    days: int,
    character_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """生成认识纪念日消息（认识第 N 天）"""
    from app.scheduling import message_generator as _mg
    identity = await _mg._load_identity_block(character_id)
    # C16 批次A（2026-09-25）：前置「现状锚 + 时空纪律」共享护栏（唯一来源 scheduling/state_guard.py）
    guard = state_guard.guard_block(
        await state_guard.current_state_anchor(character_id=character_id, user_id=user_id))
    prompt = (
        guard +
        f"你是「{character_name}」，今天是你和好友「{user_name}」认识的第 {days} 天！\n"
        f"你的性格：{character_personality or '友善、温暖'}\n"
        + (f"\n你的身份（以这里为准，不要混淆你与用户/用户对象）：\n{identity}\n" if identity else "")
        + "\n请发一条真诚、自然的纪念消息：回忆一下你们一路走来的感觉，"
        "表达这份陪伴对你的意义，像你们的真实关系那样，1-3句话。\n"
        "不要用「作为AI...」的语气，要真实。"
    )
    messages = [
        {"role": "system", "content": "你是一个真实的朋友，正在给好友发认识纪念日消息。直接输出消息内容。"},
        {"role": "user", "content": prompt},
    ]
    response = await _mg._gen_with_reasoning(messages, character_id, user_id, temperature=0.8, max_tokens=400)
    if isinstance(response, tuple):  # 2026-08-20 七夕死循环修复：_gen_with_reasoning 恒返回 (content, reasoning)
        response = response[0] or ''
    return response.strip().strip('"').strip("'")


async def generate_holiday_message(
    character_name: str,
    character_personality: str,
    user_name: str,
    holiday_name: str,
    character_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """生成节日祝福"""
    from app.scheduling import message_generator as _mg
    identity = await _mg._load_identity_block(character_id)
    # C16 批次A（2026-09-25）：前置「现状锚 + 时空纪律」共享护栏（唯一来源 scheduling/state_guard.py）
    guard = state_guard.guard_block(
        await state_guard.current_state_anchor(character_id=character_id, user_id=user_id))
    prompt = (
        guard +
        f"你是「{character_name}」，今天是{holiday_name}。\n"
        f"你的性格：{character_personality or '友善、温暖'}\n"
        + (f"\n你的身份（以这里为准，不要混淆你与用户/用户对象）：\n{identity}\n" if identity else "")
        + f"\n请发一条简短的节日祝福给「{user_name}」，按你们真实关系的亲密度来，1-2句话就好。\n"
    )
    messages = [
        {"role": "system", "content": f"你是{character_name}，正在给好友发送{holiday_name}祝福。直接输出内容。"},
        {"role": "user", "content": prompt},
    ]
    response = await _mg._gen_with_reasoning(messages, character_id, user_id, temperature=0.8, max_tokens=400)
    if isinstance(response, tuple):  # 2026-08-20 七夕死循环修复：_gen_with_reasoning 恒返回 (content, reasoning)
        response = response[0] or ''
    return response.strip().strip('"').strip("'")
