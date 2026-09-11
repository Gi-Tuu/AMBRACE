# -*- coding: utf-8 -*-
"""思考过程第一人称化（2026-09-10）：上屏归一 + 穿帮剔除 + 两挡位接入 + 指令注入。

覆盖 P0 全部行为点：
- 自称归一（名字+自称谓语 → 我，大小写不敏感）与昵称替换（用户 → 昵称/你）
- 不误伤（第三方同名实体、单字名/代词名跳过）
- 穿帮剔除（系统注入/技术字段/数值计量整句删，正常判断保留）
- 自我编排标签（策略：/长度： 前缀剥离、长度转口语）
- 挡位 2（nodes.generate_response：raw_reasoning 留底、reasoning 归一）
- 挡位 1（response_parser.parse_response：【推理】行同一管线，名字缺失不报错）
- 注入条数（0 关=0 条 / 1=1 条含名字与昵称 / 2=2 条）
"""
import asyncio
import re

from app.agent import nodes
from app.agent.context.reasoning_prompt import (
    normalize_reasoning_for_display as _norm,
    reasoning_instructions_for,
)
from app.agent.context.section_overlay import reasoning_instruction_section
from app.agent.response_parser import parse_response

# 现场样本（生产库 msg 11506 挡位 2 原生 reasoning 的问题形态）
SAMPLE = "用户说回来了，sam应该回应，sam可以把肉盛出来，sam先问他吃没。"


# ── 人称归一（自称侧 + 称对方侧）────────────────────────────────────

def test_自称归一_名字换我_用户换昵称():
    out = _norm(SAMPLE, "Sam", "轩")
    assert out is not None
    assert "轩说回来了" in out
    assert "我应该回应" in out
    assert "我可以把肉盛出来" in out
    assert "我先问" in out
    assert "sam" not in out.lower()   # 不再出现名字自称
    assert "用户" not in out


def test_昵称兜底_空昵称与用户二字都退回你():
    for nick in (None, "", "   ", "用户"):
        out = _norm(SAMPLE, "Sam", nick)
        assert out is not None
        assert out.startswith("你说回来了")
        assert "用户" not in out


def test_不误伤_第三方同名实体不被换成我():
    out = _norm("Sam 学长也在，我先打个招呼。", "Sam", "轩")
    assert out == "Sam 学长也在，我先打个招呼。"


def test_不误伤_单字名与代词名跳过自称替换():
    assert _norm("小明也来了，我等他。", "明", "轩") == "小明也来了，我等他。"
    assert _norm("他也来了。", "他", "轩") == "他也来了。"


def test_名字自称的心里嘴上与自己形态一并归一():
    out = _norm("sam心里有点慌，sam自己也知道。", "Sam", "轩")
    assert out == "我心里有点慌，我自己也知道。"


# ── 穿帮剔除 / 标签处理 ──────────────────────────────────────────

def test_穿帮剔除_系统注入与数值技术字段整句删除():
    raw = (
        "天气注入说是晴，但之前上下文说是下雨，以当前注入为准。"
        "兔子饱食度0%，先提一下。"
        "GEN_IMAGE 锅里的红烧肉。"
        "max_tokens 只剩一点。"
        "他今天很累，我先共情、回短点。"
    )
    out = _norm(raw, "Sam", "轩")
    # 新语义：只丢含穿帮词的最小子句，其余子句保留（「、」按取舍统一成「，」）
    assert out == "先提一下。他今天很累，我先共情，回短点。"
    for leak in ("天气注入", "注入为准", "饱食度", "0%", "GEN_IMAGE", "max_tokens"):
        assert leak not in out


def test_标签处理_策略与长度前缀剥离且语义保留():
    out = _norm("策略：简短回应；长度：短。", "Sam", "轩")
    assert out is not None
    assert "策略：" not in out and "长度：" not in out
    assert "简短回应" in out
    assert "我回短点" in out


def test_空输入与全穿帮返回None():
    assert _norm(None, "Sam", "轩") is None
    assert _norm("   ", "Sam", "轩") is None
    assert _norm("天气注入说是晴。", "Sam", "轩") is None


# ── P1-1 回归：逗号长句只丢穿帮子句，不整条清空 ────────────────────

def test_穿帮剔除_夹带穿帮词的长句不整条清空():
    raw = "他刚回来，肉也焖好了，天气注入说是晴以当前注入为准，我先把肉盛出来问他吃没。"
    out = _norm(raw, "Sam", "轩")
    assert out is not None
    assert "肉也焖好了" in out
    assert "我先把肉盛出来" in out
    assert "天气注入" not in out
    assert "以当前注入为准" not in out


def test_穿帮剔除_夹带数值只丢数值子句():
    raw = "可以提团子饿了，兔子饱食度0%，我顺口问一句。"
    out = _norm(raw, "Sam", "轩")
    assert out is not None
    assert "团子饿了" in out
    assert "我顺口问一句" in out
    assert "0%" not in out
    assert "饱食度" not in out


def test_穿帮剔除_全句皆穿帮仍返回None():
    assert _norm("天气注入说是晴，以当前注入为准。", "Sam", "轩") is None


# ── P1-2 回归：长度标签交替顺序 ─────────────────────────────────

def test_长度标签_中长不残留单字长():
    out = _norm("长度：中长。", "Sam", "轩")
    assert out == "我回长一点。"
    assert "我正常回长" not in out
    assert "长度" not in out


# ── P1-3 回归：段间不用半角空格拼接 ─────────────────────────────

def test_上屏排版_句末符后不跟半角空格():
    out = _norm("他会累。 我先陪他说。 回短点。", "Sam", "轩")
    assert out == "他会累。我先陪他说。回短点。"
    assert re.search(r"[。！？!?][ 　]", out) is None


def test_已知取舍_顿号分号归一为逗号且连续句末符只留一个():
    # 取舍 1：段内重拼把「、」「；」统一成「，」（只改标点形态、不改语义）
    assert _norm("他今天很累，我先共情、回短点。", "Sam", "轩") == "他今天很累，我先共情，回短点。"
    assert _norm("策略：简短回应；长度：短。", "Sam", "轩") == "简短回应，我回短点。"
    # 取舍 2：连续句末符只保留第一个，后一个标点丢弃
    assert _norm("好。！那我回了。", "Sam", "轩") == "好。那我回了。"


def test_名字昵称缺失时只做脱敏不报错():
    # 名字/昵称为 None：跳过自称替换（昵称缺失仍按「你」兜底），穿帮子句照常剔除
    out = _norm("用户说累了，饱食度0%。他想早点睡，我陪着。", None, None)
    assert out == "你说累了。他想早点睡，我陪着。"   # 只删含饱食度/百分比的最小子句
    out2 = _norm("用户说累了。他想早点睡，我陪着。", None, None)
    assert out2 == "你说累了。他想早点睡，我陪着。"


# ── 挡位 2（nodes.generate_response）────────────────────────────

def test_挡位2_raw_reasoning留底而reasoning已归一(monkeypatch):
    async def _non_stream(**kw):
        return "正文回复", SAMPLE

    async def _get_cfg(user_id):
        return None

    monkeypatch.setattr(nodes, "chat_completion", _non_stream)
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _get_cfg)
    monkeypatch.setattr(nodes, "_has_after_generate_hook", lambda: False)

    state = {
        "context_messages": [{"role": "user", "content": "hi"}],
        "emotional_state": "", "temperature": 0.8,
        "reasoning_level": 2, "user_id": 1, "character_id": 2,
        "ai_response": "", "user_message": "hi", "session_id": 1,
        "new_memories": [], "skip_memory_save": True,
        "character_name": "Sam", "user_name": "轩",
    }
    out = asyncio.run(nodes.generate_response(state))

    assert out["raw_reasoning"] == SAMPLE          # 原始思考仅后台留底
    assert out["reasoning"] != SAMPLE              # 上屏字段已归一
    assert "我应该回应" in out["reasoning"]
    assert "sam" not in out["reasoning"].lower()
    assert "用户" not in out["reasoning"]


# ── 挡位 1（response_parser.parse_response）──────────────────────

def test_挡位1_推理行走同一归一管线():
    state = {"character_id": 2, "character_name": "Sam", "user_name": "轩"}
    parse_response(f"【推理：{SAMPLE}】\n好啦我盛出来了。", state)
    assert "我应该回应" in state["reasoning"]
    assert "轩说回来了" in state["reasoning"]
    assert "用户" not in state["reasoning"]
    assert state["ai_response"] == "好啦我盛出来了。"


def test_挡位1_名字昵称缺失不报错():
    state = {"character_id": 2}
    parse_response("【推理：用户说累了，我先安慰一句。】\n先休息会儿。", state)
    assert state["reasoning"] == "你说累了，我先安慰一句。"


# ── 指令注入（挡位 0/1/2 条数与内容）────────────────────────────

def test_注入条数_0关1条2两条():
    assert reasoning_instructions_for(0, name="Sam", user="轩") == []
    one = reasoning_instructions_for(1, name="Sam", user="轩")
    two = reasoning_instructions_for(2, name="Sam", user="轩")
    assert len(one) == 1 and len(two) == 2
    assert "【内心活动指令】" in one[0]
    assert "Sam" in one[0] and "轩" in one[0]
    assert two[0] == one[0]
    assert "轩" in two[1] and "第一人称" in two[1]


def test_注入_名字昵称缺失时用占位兜底():
    one = reasoning_instructions_for(1)
    assert len(one) == 1
    assert "（你的名字）" in one[0] and "你/TA" in one[0]


def test_注入分区_挡位1与2都注入_挡位0不注入():
    def _run(level):
        state = {"reasoning_level": level, "character_name": "Sam", "user_name": "轩",
                 "character_id": 2, "user_id": 1}
        return asyncio.run(reasoning_instruction_section(state, {}))

    assert _run(0) == []
    assert len(_run(1)) == 1
    assert len(_run(2)) == 2
    assert "Sam" in _run(1)[0] and "轩" in _run(1)[0]


# ── P3-6（2026-09-11）：上屏归一后相邻重复短句去重 ────────────────────

def test_去重_相邻相同短句合并():
    # 模型正文自写一遍 + 长度标签转换 → 相邻重复短句应合并为一句
    out = _norm("我回短点。我回短点。", "Sam", "轩")
    assert out == "我回短点。"


def test_去重_长度标签转换与正文重复合并():
    # 「长度：短」→「我回短点」，叠加模型正文「我回短点。」→ 归一后合并为单句
    out = _norm("长度：短。我回短点。", "Sam", "轩")
    assert out == "我回短点。"


def test_去重_两个不同短句不动():
    out = _norm("他回来了。她出去了。", "Sam", "轩")
    assert out == "他回来了。她出去了。"


def test_去重_长句重复不动():
    # 长句（>12 字）即便相邻重复也不去重
    long = "今天天气真不错我们一起去散步吧。"
    out = _norm(long + long, "Sam", "轩")
    assert out == long + long
