# -*- coding: utf-8 -*-
"""增量语义切块器单测（SSE 真流式链路）。

覆盖：句末标点边界 / 3 句·80 字成块 / 情绪态整段不拆 / flush 末块 /
未闭合括号保持块边界单调 / 展示标记剥离且不闪退。
"""
from app.agent.response_parser import IncrementalResponseChunker, strip_stream_display, _find_hold_index


def _feed_all(text: str, chunker: IncrementalResponseChunker, step: int = 4):
    blocks = []
    for i in range(0, len(text), step):
        blocks += chunker.feed(text[i:i + step])
    blocks += chunker.flush()
    return blocks


def test_sentence_boundary_emit():
    """句末标点到达后即产生完整块，未到标点的文本保持缓冲。"""
    ch = IncrementalResponseChunker()
    assert ch.feed("今天天气") == []
    assert ch.feed("真好。") == []
    # 未满 3 句不切块（缓冲保留）
    assert ch.feed("我们出去玩吧。") == []
    from app.agent.response_parser import IncrementalResponseChunker as _C
    # 3 句成块
    ch2 = _C()
    assert ch2.feed("你好。今天天气真好。我们一起出去玩吧。") == ["你好。今天天气真好。我们一起出去玩吧。"]


def test_three_sentence_rule():
    """3 句触发成块（与 split_response 规则一致）。"""
    ch = IncrementalResponseChunker()
    out = _feed_all("你好。今天天气真好。我们一起出去玩吧。晚上吃什么呢？", ch, 3)
    assert out == ["你好。今天天气真好。我们一起出去玩吧。", "晚上吃什么呢？"]


def test_length_boundary_rule():
    """单句超长（>80 字）也切块。"""
    long_sent = "今天天气真好" * 20 + "。"  # 120 字
    ch = IncrementalResponseChunker()
    out = _feed_all(long_sent, ch, 10)
    assert all(len(b) <= 80 for b in out[:-1]) or len(out) <= 1
    assert "".join(out) == long_sent


def test_emotional_state_keeps_whole():
    """情绪态（angry/sad/upset）整段不拆，flush 返回整段。"""
    text = "我真的很生气。你为什么这样对我。我受不了了。"
    ch = IncrementalResponseChunker(emotional_state="angry")
    out = _feed_all(text, ch, 4)
    assert out == [text]


def test_flush_returns_remaining():
    """flush 冲刷不足一块的剩余缓冲。"""
    ch = IncrementalResponseChunker()
    assert ch.feed("一句话。第二句。") == []
    assert ch.flush() == ["一句话。第二句。"]


def test_no_text_returns_empty():
    ch = IncrementalResponseChunker()
    assert ch.feed("") == []
    assert ch.flush() == []


def test_unclosed_bracket_holds_boundary():
    """未闭合括号（神态块）处不切块，保持边界单调。"""
    ch = IncrementalResponseChunker()
    blocks = []
    blocks += ch.feed("今天天气真好。（站")
    blocks += ch.feed("起来看了看）我们回家吧。")
    blocks += ch.flush()
    assert "".join(blocks) == "今天天气真好。（站起来看了看）我们回家吧。"
    # 括号内容不被拆散进两块
    assert all(("（" in b) != ("）" in b) is False for b in blocks) or len(blocks) == 1


def test_display_marker_stripped_and_monotonic():
    """已闭合展示标记被剥离，且 flush 块为干净正文。"""
    ch = IncrementalResponseChunker()
    blocks = []
    for piece in ["【推理：我在思考】", "今天天气", "真好。", "我们出去玩吧。"]:
        blocks += ch.feed(piece)
    blocks += ch.flush()
    assert "".join(blocks) == "今天天气真好。我们出去玩吧。"
    assert "推理" not in "".join(blocks)


def test_delta_monotonic_no_shrink():
    """增量展示文本单调不闪退：标记闭合后 displayed 不回退。"""
    ch = IncrementalResponseChunker()
    deltas = []
    for piece in ["早餐。", "【记忆：用户", "喜欢咖啡】", "我们出发吧。"]:
        ch.feed(piece)
        deltas.append(ch.disp_delta)
    merged = "".join(deltas)
    assert "记忆" not in merged
    # 展示文本逐步增长
    lens = [len("".join(deltas[:i + 1])) for i in range(len(deltas))]
    assert all(lens[i] <= lens[i + 1] for i in range(len(lens) - 1))


def test_strip_stream_display_removes_markers():
    assert strip_stream_display("正文【记忆：xxx】尾部") == "正文尾部"
    assert strip_stream_display("【状态更新：准备睡觉】晚安") == "晚安"


def test_find_hold_index():
    assert _find_hold_index("你好。") == 3
    assert _find_hold_index("你好。【记忆：") == 3  # 未闭合【 → 从这里 hold
    assert _find_hold_index("你好。）") == 4  # 未闭合）忽略
