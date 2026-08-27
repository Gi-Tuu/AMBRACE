"""AI 玩家决策：组装可见上下文 → LLM 输出动作+发言。

关键：prompt 中绝不出现其他玩家 hidden 信息。
LLM 调用走 app.agent.llm_client.chat_completion（task=game，temperature 0.85，
max_tokens 300）。本函数只负责 LLM 决策 + JSON 解析；动作校验与 apply 统一由
调度方 _resume_ai_turns 负责。LLM 失败/解析失败用引擎的随机合法动作兜底，不阻塞游戏。
"""
from __future__ import annotations

import json

from app.games.base import GameContext
from app.utils.logger import get_logger

_logger = get_logger("games.ai_player")


async def ai_decide(engine, seat: int) -> dict:
    """让 AI 玩家决策。返回 {"action": "...", "content": "...", "payload": {...}}。

    只负责 LLM 决策 + JSON 解析，不校验/不 apply——动作校验与 apply 统一由
    调度方 _resume_ai_turns 负责，避免对同一引擎实例二次 apply 造成双重效果。
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

    # LLM 解析成功直接返回 decision；失败/解析失败用引擎的随机合法动作兜底（不阻塞游戏）
    if decision:
        return decision
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

只输出 JSON：{_action_schema(expected)}"""


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
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        return None
