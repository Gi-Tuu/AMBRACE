# -*- coding: utf-8 -*-
"""切分括号配平回归（2026-09-15 真机缺陷：开头的括号推理段被气泡切开）。

现场（生产库 session 11 / character 13 = sam，只读实查）：
11692→11693、11984→11985、12005→12006、12022→12023 四对相邻 AI 消息呈现
「上一条多开括号 + 下一条多闭括号」——切分在括号中间落刀，而括号推理的剥离发生在
切分之后逐块进行（extract_leading_bracket_reasoning 只认「开头一个完整括号段」），
被切两半后两边都不匹配 → 推理原文连着括号一起上屏。

断言：
(a) 每个切分块内部括号配平；
(b) 开头括号推理被剥离、不产生「只剩 ）」的块；
(c) 既有行为不回归——表情行仍单独成块、情绪态整段不拆、3 句/80 字规则保持。
"""
from app.agent.response_parser import (
    IncrementalResponseChunker,
    _bracket_depth,
    _split_by_stage_blocks,
    split_response,
)
from app.application.chat.tools import _sanitize_chunk_texts

# ---- 真实现场（生产库相邻两条 AI 消息按落库顺序拼回整段）----
_SPLIT_PAIRS = {
    "11692_11693": (
        "（他看我发一串省略号就来问。其实没多大事，就是嫌他磨蹭到快半夜还不洗漱。别绕，直接问他洗完没。",
        "）\n\n没什么。看你还在线。洗漱了没？",
    ),
    "11984_11985": (
        "嗯，睡吧。粥我搁着温，醒了再吃。（把床头那盏灯调暗了点，手在你腰后垫的被角上按了按，没多说话。",
        "）",
    ),
    "12005_12006": (
        "（他嘴上嫌我操心，走的时候还留个mua。行吧，稿子要紧，我不多话。到点喊他就够，芒芒那边我刚添过粮。",
        "顺手拍张眼前的给他看，省得多说。）\n\n嗯。到点我喊你，别装听不见。",
    ),
    "12022_12023": (
        "（快十二点了，他一句晚安就收尾。行，不揪他熬夜了，明天还有宣讲。粥的事得提一句，腰也得提，但别啰嗦。",
        "）\n\n粥喝了没。没喝就别空着睡，明天还要站着讲话。",
    ),
}
FULL_TEXTS = {k: a + b for k, (a, b) in _SPLIT_PAIRS.items()}


def _chunker_blocks(text: str, step: int = 3) -> list[str]:
    """喂完整条文本（模拟流式增量）+ flush，返回语义块。"""
    ch = IncrementalResponseChunker()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += ch.feed(text[i:i + step])
    out += ch.flush()
    return out


def _assert_balanced(chunks: list[str], tag: str) -> None:
    for c in chunks:
        assert _bracket_depth(0, c) == 0, (tag, c)
        assert c.strip() not in ("）", ")"), (tag, c)


# ---------------- (a) 每个切分块内部括号配平 ----------------

def test_split_response_chunks_balanced():
    for name, text in FULL_TEXTS.items():
        chunks = split_response(text)
        assert chunks, name
        _assert_balanced(chunks, f"split_response/{name}")


def test_chunker_blocks_balanced():
    for name, text in FULL_TEXTS.items():
        chunks = _chunker_blocks(text)
        assert chunks, name
        _assert_balanced(chunks, f"chunker/{name}")


def test_stage_block_inside_unclosed_bracket_is_not_split_point():
    """外层括号未闭合时，内层「（动作）」不再被当成气泡分开点。"""
    text = "（他还在想…（摸摸你的头）…先别说）睡吧。"
    out = _split_by_stage_blocks(text)
    assert len(out) == 1, out
    assert _bracket_depth(0, out[0]) == 0


# ---------------- (b) 开头括号推理被剥离、无「只剩 ）」块 ----------------

def test_no_dangling_close_bracket_block():
    """分块 + 落库清洗后：没有只剩「）」的块，也没有以「）」开头的块。"""
    for name, text in FULL_TEXTS.items():
        for chunks in (split_response(text), _chunker_blocks(text)):
            cleaned = _sanitize_chunk_texts(list(chunks))
            assert cleaned, (name, chunks)
            for c in cleaned:
                assert c.strip() not in ("）", ")"), (name, c)
                assert not c.lstrip().startswith("）"), (name, c)


def test_leading_bracket_reasoning_stripped_before_split():
    """先剥后切：整段开头的括号推理不进切分，可见正文只剩真实回复。"""
    text = FULL_TEXTS["11692_11693"]
    assert split_response(text) == ["没什么。看你还在线。洗漱了没？"]


def test_leading_bracket_reasoning_gone_after_sanitize():
    for name in ("11692_11693",):
        text = FULL_TEXTS[name]
        for chunks in (split_response(text), _chunker_blocks(text)):
            visible = "".join(_sanitize_chunk_texts(list(chunks)))
            assert "省略号" not in visible, (name, visible)
            assert "（" not in visible and "）" not in visible, (name, visible)
            assert "洗漱了没" in visible, (name, visible)


def test_action_bracket_kept_when_not_reasoning():
    """09-15 动作保护口径不变：判为动作描写的括号段仍保留为正文（只是不被切开）。"""
    text = FULL_TEXTS["11984_11985"]
    visible = "".join(_sanitize_chunk_texts(split_response(text)))
    assert "把床头那盏灯调暗了点" in visible
    assert visible.count("（") == visible.count("）") == 1


# ---------------- (c) 既有行为不回归 ----------------

def test_emoji_line_still_separate_block():
    text = "今天挺好的。\n😹 猫猫笑哭"
    assert split_response(text) == ["今天挺好的。", "😹 猫猫笑哭"]


def test_emotional_state_not_split():
    text = "我真的很生气。你为什么这样对我。我受不了了。"
    assert split_response(text, "angry") == [text]


def test_three_sentence_rule_intact():
    assert split_response("一。二。三。四。") == ["一。二。三。", "四。"]


def test_length_rule_intact():
    long_one = "今天天气真好" * 18 + "。"  # 109 字 → 单句超 80 也成块
    assert split_response(long_one) == [long_one]


def test_stage_block_split_point_intact():
    """动作小字仍按原设计作为气泡分开点（末尾括号归属前一段）。"""
    assert split_response("好啦。（摸摸你的头）睡吧。") == ["好啦。（摸摸你的头）", "睡吧。"]
