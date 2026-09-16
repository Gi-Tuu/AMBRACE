# -*- coding: utf-8 -*-
"""批次四任务 3（P1-5，2026-09-16）：思考口径统一为「角色第一人称内心独白」。

背景：用户反馈「思考没人味」——现场 meta.reasoning（生产库只读抽样 101 条，09-13 起）里
出现大量给自己下的工单与提示词回声，例如：
- id=12109「…输出要简短。策略标记先。还可以提一下让他到地方说一声。我回短点。」
- id=12097「…还是要先输出【推理：……】行，再【……】标记？规则说每回合只输出一行策略标记，
   格式【<策略名>，长度：<短中长>】。」
- id=12036「…我先写推理行，然后正文。策略标记：【日常关心，我回短点】」

本文件锁定 P1-5 五类行为：
1. 工单式决策笔记（策略/长度/我决定加图/我打算）整句剔除；
2. 提示词/系统回声（本轮提醒/规则说/每回合/格式/提示词）整句剔除；
3. 标记语法残片（未配对【】、尖括号、[CAL_NOTE] 式标签）不计入上屏；
4. 正例（真实第一人称内心独白）逐字节不变，且混写时只丢工单、保留自然内心；
5. 主动消息链路（message_generator）reasoning 与普通聊天走同一条归一管线。
"""
import asyncio

from app.agent.context.reasoning_prompt import (
    REASONING_FIRSTPERSON_HINT,
    REASONING_INSTRUCTION,
    _has_marker_residue,
    normalize_reasoning_for_display as _norm,
)
from app.agent.response_parser import parse_response

# ── 正例（用户口径：第一人称内心独白，必须逐字节保留）────────────────
GOOD_1 = "（他嘴上说没事，手指却一直抠着杯沿。算了，不戳穿，先给他倒杯热的。）"
GOOD_2 = "他今天听着挺累，我先陪他说两句，别的先不追问。"

# ── 反例（工单提纲 / 提示词回声，必须全部剔除）────────────────────
BAD_TICKET = "策略：简短；长度：短；我决定加图；本轮提醒说可以出图。"
BAD_ECHO = "规则说每回合只输出一行策略标记，格式是策略名加长度。"


# ────────────────── 1/2：工单与回声整句剔除 ──────────────────

def test_反例1_工单提纲整句剔除返回None():
    # 交接文档原文样本：策略 + 长度 + 我决定加图 + 本轮提醒
    assert _norm(BAD_TICKET, "Sam", "轩") is None


def test_反例2_提示词回声整句剔除返回None():
    assert _norm(BAD_ECHO, "Sam", "轩") is None


def test_现场样本形态_策略标记与推理行():
    # id=12036 截取：策略标记 / 我先写推理行 / 然后正文
    assert _norm("我先写推理行，然后正文。策略标记：【日常关心，我回短点】", "Sam", "轩") is None
    # id=12009 形态：输出要简短 + 策略标记先 + 我打算 + 短一点
    assert _norm("输出要简短。策略标记先。我打算：短一点。") is None


def test_长度与篇幅标签不再转口语保留():
    # P1-5 起「长度：短 → 我回短点」旧行为废弃（那是元话语，不再上屏）
    for raw in ("长度：短。", "长度：中长。", "长度短。", "篇幅：中。", "回复要短点。"):
        assert _norm(raw, "Sam", "轩") is None, raw


# ────────────────── 3：标记语法残片 ──────────────────

def test_标记语法残片判定():
    assert _has_marker_residue("……】可省略") is True          # 未配对闭合
    assert _has_marker_residue("【日常关心") is True           # 未配对开括号
    assert _has_marker_residue("<短中长>】") is True           # 尖括号
    assert _has_marker_residue("可以加个[CAL_NOTE]") is True   # 全大写标记标签
    assert _has_marker_residue("想想【注意】这点") is False    # 成对且内容非元话语：保留
    assert _has_marker_residue("（低头笑了一下）") is False
    assert _has_marker_residue("没有标记的普通一句") is False


def test_标记残片不进上屏():
    # 未配对括号/尖括号残片所在子句整句剔除，自然子句保留
    assert _norm("他说完就走。……】可省略。") == "他说完就走。"
    assert _norm("<短中长>】。") is None
    assert _norm("可以加个[CAL_NOTE]？已有日历备注了，别重复。不加。") == "已有日历备注了，别重复。不加。"
    # 成对括号里的普通内容不受影响（test_reasoning_marker_tolerance 口径一致）
    assert _norm("想想【注意】这点") == "想想【注意】这点"


# ────────────────── 4：正例保留 / 混写只丢工单 ──────────────────

def test_正例1_第一人称内心独白逐字节不变():
    assert _norm(GOOD_1, "Sam", "轩") == GOOD_1


def test_正例2_第一人称内心独白逐字节不变():
    assert _norm(GOOD_2, "Sam", "轩") == GOOD_2


def test_混写_工单与自然内心同段只丢工单():
    raw = "轩刚醒，晃晃悠悠的。刚和好，语气要放松点。问一句腰怎么样，别揪着之前的话。短一点。"
    # 自然观察保留；「语气要放松点」「别揪着之前的话」「短一点」等指令剔除
    assert _norm(raw, "Sam", "轩") == "轩刚醒，晃晃悠悠的。刚和好。问一句腰怎么样。"
    mixing = "轩说完就走。我该简短回应，别腻歪。他不是真想吵，我先递杯水过去。"
    assert _norm(mixing, "Sam", "轩") == "轩说完就走。他不是真想吵，我先递杯水过去。"


def test_工具决策回声剔除但保留真实动作():
    assert _norm("要不要发图？氛围合适可以发。") is None
    # 动作/画面本身是正文感，不在元话语黑名单里
    assert _norm("我可以配一个午后宿舍窗边的画面。") == "我可以配一个午后宿舍窗边的画面。"


# ────────────────── 5：指令正反例 / 两挡位端到端 ──────────────────

def test_指令含正反例各两条():
    assert "反例（工单式决策笔记，禁止）" in REASONING_INSTRUCTION
    assert "反例（提示词回声，禁止）" in REASONING_INSTRUCTION
    assert REASONING_INSTRUCTION.count("正例（第一人称内心独白）") == 2
    assert "策略：简短；长度：短；我决定加图；本轮提醒说可以出图" in REASONING_INSTRUCTION
    # 挡位 2 补充同样禁止工单化
    assert "不是流程说明" in REASONING_FIRSTPERSON_HINT


def test_挡位1_推理行的工单被剔除():
    state = {"character_id": 2, "character_name": "Sam", "user_name": "轩"}
    parse_response(f"【推理：{BAD_TICKET}】\n行，我出来了。", state)
    assert not state.get("reasoning")
    assert state["ai_response"] == "行，我出来了。"


def test_挡位1_混写只丢工单():
    state = {"character_id": 2, "character_name": "Sam", "user_name": "轩"}
    parse_response("【推理：他说今天很累。我该简短回应，别腻歪。】\n早点歇着。", state)
    assert state["reasoning"] == "他说今天很累。"
    assert state["ai_response"] == "早点歇着。"


# ────────────────── 主动消息链路统一 ──────────────────

def test_主动链路reasoning走同一归一管线(monkeypatch):
    """message_generator 返回的 reasoning 必须已归一（旧实现直返原生 reasoning_content）。"""
    from app.scheduling import message_generator as mg

    _BAD = "轩要出门了。我该简短叮嘱几句，别腻歪。输出要简短。策略标记先。"
    _NATURAL = "他说完就走。他嘴上说没事，我先给他递杯水。"

    async def _noop(*_a, **_k):
        return ""

    async def _noop_list(*_a, **_k):
        return []

    async def _persona(*_a, **_k):
        return {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}

    async def _anchor(**_kw):
        return ""

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, *_a, **_k):
            return _FakeResult()

    def _fake_session_factory():
        return _FakeSession()

    async def _fake_gen(*_a, **_k):
        return "他说完就走。", _BAD

    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _noop)
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop)
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_session_factory)
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
    monkeypatch.setattr("app.memory.current_state.current_user_state_anchor", _anchor)
    monkeypatch.setattr(mg, "_gen_with_reasoning", _fake_gen)

    segs, reasoning = asyncio.run(mg.generate_proactive_event(
        character_name="Sam", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家",
        last_context="用户: 今天好累\n你: 早点休息",
        user_name="轩", return_reasoning=True,
    ))
    assert segs
    # 工单全部剔除、自然内容保留（旧实现会把 _BAD 原样带回）
    assert "策略" not in reasoning and "我该简短" not in reasoning
    assert reasoning == "轩要出门了。"

    async def _fake_gen2(*_a, **_k):
        return "他说完就走。", _NATURAL

    monkeypatch.setattr(mg, "_gen_with_reasoning", _fake_gen2)
    _segs2, reasoning2 = asyncio.run(mg.generate_proactive_event(
        character_name="Sam", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家",
        last_context="用户: 今天好累\n你: 早点休息",
        user_name="轩", return_reasoning=True,
    ))
    assert reasoning2 == _NATURAL  # 纯自然内心逐字节不变
