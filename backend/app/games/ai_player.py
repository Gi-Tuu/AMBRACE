"""AI 玩家决策：组装可见上下文 → LLM 输出动作+发言。

关键：prompt 中绝不出现其他玩家 hidden 信息。
LLM 调用走 app.agent.llm_client.chat_completion（task=game，temperature 0.85，
max_tokens 300）。本函数只负责 LLM 决策 + JSON 解析 + 轻量动作合法性校验（P2-1）；
规则判定与 apply 统一由调度方 _resume_ai_turns 负责。LLM 失败/解析失败/动作不合法
一律用引擎的随机合法动作兜底，不阻塞游戏。
"""
from __future__ import annotations

import json

from app.games.base import GameContext
from app.utils.logger import get_logger

_logger = get_logger("games.ai_player")

# P2-1（2026-09-18）：期望动作 → 该期望下引擎同样接受的替代动作（只登记引擎 apply_action
# 实际放行的分支，不改引擎规则）。用于把"为空/非字符串/与当前阶段不符"的 LLM 输出拦在
# 调度方之前直接走 fallback，避免明显非法动作被送进 apply（进而双 apply 失败 → 静默卡死）。
_ALT_ACTIONS: dict[str, tuple[str, ...]] = {
    "follow_or_challenge": ("declare", "challenge"),   # liars_bar：跟牌或质疑
    "answer_truth": ("penalty",),                      # truth_or_dare：真心话可选接受惩罚
    "complete_dare": ("penalty",),                     # truth_or_dare：大冒险可选接受惩罚
    "ask_soup": ("guess_soup",),                       # turtle_soup：可随时直接猜真相
    "guess_soup": ("ask_soup",),                       # turtle_soup：也可继续提问
    "ask": ("guess",),                                 # twenty_q：可随时直接猜词
    "guess": ("ask",),                                 # twenty_q：也可继续提问
}

_SURRENDER = "surrender"  # 与具体游戏解耦的通用动作，任何阶段都合法（由调度方分流结算）


def _action_valid(engine_expected: str, action) -> bool:
    """轻量合法性判定（不查引擎规则，只看「与当前期望是否相符」）：

    - 非字符串 / 空串 → 非法；
    - surrender → 合法（通用动作）；
    - 等于 expected_action → 合法；
    - 属于该 expected 下引擎同样放行的替代动作 → 合法；
    - 其余（阶段不符/动作集外）→ 非法，调用方直接走 fallback_action。
    """
    if not isinstance(action, str) or not action.strip():
        return False
    action = action.strip()
    if action == _SURRENDER or action == engine_expected:
        return True
    return action in _ALT_ACTIONS.get(engine_expected, ())


async def ai_decide(engine, seat: int) -> dict:
    """让 AI 玩家决策。返回 {"action": "...", "content": "...", "payload": {...}}。

    只负责 LLM 决策 + JSON 解析 + P2-1 轻量校验（action 是否与当前 expected_action 相符）；
    不做规则判定、不 apply——真正的动作校验与 apply 统一由调度方 _resume_ai_turns 负责，
    避免对同一引擎实例二次 apply 造成双重效果。
    """
    ctx: GameContext = engine.build_ai_prompt(seat)
    expected = engine.expected_action(seat)

    prompt = _build_prompt(ctx, expected)
    decision = None
    try:
        from app.agent.llm_client import chat_completion
        raw = await chat_completion(
            messages=[
                {"role": "system", "content": "你是输出 JSON 的助手，直接输出 JSON，不要多余文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,
            max_tokens=300,
            task="game",
            user_id=engine.session.user_id,
        )
        decision = _parse_json(raw)
    except Exception as e:
        _logger.warning("ai_decide LLM failed seat=%d: %s", seat, e)
        decision = None

    # P2-1：解析成功也要过轻量校验——action 为空/非字符串/与当前阶段不符时弃用，
    # 直接走引擎兜底动作，避免把明显非法动作送到调度方（进而双 apply 失败卡死）。
    if isinstance(decision, dict) and _action_valid(expected, decision.get("action")):
        # P3-①（2026-09-19）：校验内部用的是 strip 后的值，这里把清洗值写回 decision——
        # 调度方 apply_action 走精确成员匹配，" kill " 这类带首尾空白的输出会过校验却在
        # 引擎被判非法（多一次无效 apply）。判定逻辑不变，非 str / 空串仍走 fallback。
        decision["action"] = decision["action"].strip()
        return decision
    if decision:
        _logger.warning(
            "ai_decide action=%r rejected (expected=%s seat=%d), use fallback_action",
            decision.get("action") if isinstance(decision, dict) else type(decision).__name__,
            expected, seat,
        )
    # 兜底：引擎提供默认合法动作
    return await engine.fallback_action(seat)


def _build_prompt(ctx: GameContext, expected: str) -> str:
    me = ctx.my_view
    others = "\n".join(
        f"- {p['seat']}号 {p['name']}（{'存活' if p.get('alive') else '已淘汰'}）"
        for p in ctx.players_public if p.get("seat") != me.seat
    ) or "（暂无）"
    events = "\n".join(
        f"[{e.get('phase', '')}] {e.get('content', '')}" for e in ctx.public_events[-15:]
    ) or "（游戏刚开始）"

    surrender_example = '{"action": "surrender", "content": "我认输", "payload": {}}'

    return f"""你正在玩「{ctx.game_type}」。

游戏规则（你需要知道的）：
{ctx.rules_summary}

当前是第 {ctx.round} 轮，阶段：{ctx.phase}。
现在轮到你（{me.seat}号 {me.name}）{_action_hint(expected)}。

其他玩家：
{others}

你的身份/手牌（只有你知道，绝不能直接告诉别人）：
{json.dumps(me.private, ensure_ascii=False)}

你的公开信息：角色={me.role}，存活={me.alive}

游戏至今的公开记录：
{events}

你的性格：{ctx.my_persona.get('personality', '自然')}
你的说话风格：{ctx.my_persona.get('chat_style', '口语化')}

要求：
1. 严格按规则行动，不要做规则外的事；
2. 说话符合你的性格，20-50字；
3. 你只知道上面"公开记录"里的信息和你自己的手牌，不知道别人的身份/手牌；
4. 不要编造公开记录里没有发生的事。
5. 只有在你确实无法继续、或用户明确表示不想玩时，才输出 {surrender_example}；正常情况下不要投降。
6. 只输出 JSON：{_action_schema(expected)}"""


def _action_hint(expected: str) -> str:
    return {
        "describe": "用一句话描述你的词语（不能直接说出那个词）",
        "vote": "投票淘汰你认为是卧底的人",
        "choose": "选择真心话或大冒险",
        "give_truth": "给对方出一道真心话问题",
        "give_dare": "给对方出一个大冒险任务",
        "answer_truth": "回答对方的真心话问题",
        "complete_dare": "完成对方给的大冒险任务",
        "ask": "问一个是非问句（是/否/可能/不确定）",
        "answer": "回答是/否/可能/不确定",
        "guess": "猜对方想的词是什么",
        "kill": "选择你要刀杀的玩家（狼人夜间行动，只能刀非狼的存活玩家）",
        "check": "查验一个玩家是否为狼人（预言家夜间行动）",
        "speak": "说一段发言（狼人杀白天）",
        "declare": "出一张牌并声明一个数字（1-10，不能小于上家声明）",
        "follow_or_challenge": "跟牌（出一张牌并声明≥当前数字）或质疑（翻开上一张牌）",
        "challenge": "质疑上一家的声明",
        "ask_soup": "问一个是非问句（主持人答 是/否/可能/无关/不知道）",
        "answer_soup": "回答 是/否/可能/无关/不知道",
        "guess_soup": "直接说出你猜的真相",
    }.get(expected, "行动")


def _action_schema(expected: str) -> str:
    if expected == "vote":
        return '{"action": "vote", "content": "我投X号，因为...", "payload": {"target_seat": 2}}'
    if expected == "describe":
        return '{"action": "describe", "content": "你的描述（一句话）", "payload": {}}'
    if expected == "choose":
        return '{"action": "choose", "content": "我选真心话", "payload": {"choice": "truth"}}'
    if expected == "give_truth":
        return '{"action": "give_truth", "content": "你的问题？", "payload": {}}'
    if expected == "give_dare":
        return '{"action": "give_dare", "content": "任务内容", "payload": {}}'
    if expected == "answer_truth":
        return '{"action": "answer_truth", "content": "你的回答", "payload": {}}'
    if expected == "complete_dare":
        return '{"action": "complete_dare", "content": "完成任务的描述", "payload": {}}'
    if expected == "ask":
        return '{"action": "ask", "content": "你的问题？", "payload": {}}'
    if expected == "answer":
        return '{"action": "answer", "content": "是", "payload": {"answer": "yes"}}'
    if expected == "guess":
        return '{"action": "guess", "content": "我猜是...", "payload": {"word": "..."}}'
    if expected == "kill":
        return '{"action": "kill", "content": "今晚刀X号", "payload": {"target_seat": 2}}'
    if expected == "check":
        return '{"action": "check", "content": "我查验X号", "payload": {"target_seat": 2}}'
    if expected == "speak":
        return '{"action": "speak", "content": "你的发言", "payload": {}}'
    if expected == "declare":
        return '{"action": "declare", "content": "", "payload": {"number": 7}}'
    if expected == "follow_or_challenge":
        return '{"action": "declare", "content": "", "payload": {"number": 7}}  # 或质疑：{"action":"challenge","payload":{}}'
    if expected == "challenge":
        return '{"action": "challenge", "content": "我要质疑上一家", "payload": {}}'
    if expected == "ask_soup":
        return '{"action": "ask_soup", "content": "你的问题？", "payload": {}}'
    if expected == "answer_soup":
        return '{"action": "answer_soup", "content": "", "payload": {"answer": "possible"}}'
    if expected == "guess_soup":
        return '{"action": "guess_soup", "content": "我猜真相是...", "payload": {"word": "..."}}'
    return '{"action": "...", "content": "...", "payload": {}}'


def _parse_json(raw: str) -> dict | None:
    """C1（v3.4.4 审查）：容忍 ``` json（标签前空格）/ ```JSON（大写）围栏 + 前后自然语言噪声；
    取首个 { 到末个 } 之间的内容再解析，失败返回 None（调用方回退随机合法动作）。"""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw[:4].lower() == "json":
            raw = raw[4:].lstrip()
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        raw = raw[i:j + 1]
    try:
        return json.loads(raw)
    except Exception:
        return None
