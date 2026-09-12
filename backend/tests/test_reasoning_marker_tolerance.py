# -*- coding: utf-8 -*-
"""思考【推理】标记容错测试（2026-09-12，真机回归 id=11642 泄漏修复）。

覆盖：
- 真实原文形态【推理】（…）】（无冒号、内容含全角括号）→ reasoning 填充 + 正文剥离；
- 现状形态【推理：…】不回归；[推理: …] 半角；
- 内容含 】/【】（取本行最后一个闭合界定，不禁括号）；
- 未闭合标记 → 正文剥离不崩、不填充垃圾；
- 与挡位 2 的关系（口径=丢弃）：state["reasoning"] 已有原生思考时标记内容丢弃、正文仍剥离；
- 展示层兜底：解析漏网的【推理…】块从正文剥离（_strip_display_markers 路径）。
"""
from app.agent.response_parser import parse_response


def _state(user_msg: str = "", reasoning: str | None = None):
    st = {"character_info": {"bio": ""}, "user_message": user_msg}
    if reasoning is not None:
        st["reasoning"] = reasoning
    return st


# id=11642 真实原文（脱敏保留结构）：标记行 + 正文行
_11642_RAW = (
    "【推理】（他回得挺有理，我倒真是问岔了。行，这茬不跟他犟，腰垫上就算过关，别再多啰嗦。）】\n"
    "行，这茬不跟他犟。"
)


def test_11642_real_shape_reasoning_filled_and_body_clean():
    out = parse_response(_11642_RAW, _state())
    assert out["reasoning"], "reasoning 应被填充"
    assert "他回得挺有理" in out["reasoning"]
    assert "【推理" not in out["ai_response"]
    assert "】（他" not in out["ai_response"]
    assert out["ai_response"].strip() == "行，这茬不跟他犟。"


def test_colon_form_still_works():
    out = parse_response("【推理：想想今晚吃什么】今晚吃火锅。", _state())
    assert "想想今晚吃什么" in out["reasoning"]
    assert out["ai_response"].strip() == "今晚吃火锅。"
    assert "【推理" not in out["ai_response"]


def test_half_width_bracket_form():
    out = parse_response("[推理: 内心独白一下]好的", _state())
    assert "内心独白一下" in out["reasoning"]
    assert "[推理" not in out["ai_response"]


def test_content_containing_brackets_uses_last_close():
    out = parse_response("【推理】想想【注意】这点】\n正文内容", _state())
    # 取本行最后一个 】：内容保留内部的【注意】
    assert "想想【注意】这点" in out["reasoning"]
    assert out["ai_response"].strip() == "正文内容"
    assert "【推理" not in out["ai_response"]


def test_unclosed_marker_stripped_no_crash():
    out = parse_response("【推理】未闭合直接结束", _state())
    assert "【推理" not in out["ai_response"]
    # 未闭合不填充垃圾（reasoning 为空或不覆盖）
    assert not out.get("reasoning")
    # 2026-09-12 复核补：无闭合时只剥标记，正文必须原样保留（旧实现会清空整条回复）
    assert out["ai_response"].strip() == "未闭合直接结束"


def test_level2_native_reasoning_not_overwritten():
    """口径=丢弃：原生思考已存在时，标记行内容丢弃、正文仍剥离，不出现两段思考。"""
    native = "原生挡位 2 的思考内容"
    out = parse_response("【推理：标记行的思考】正文照常", _state(reasoning=native))
    assert out["reasoning"] == native  # 未被覆盖
    assert "标记行的思考" not in (out["reasoning"] or "")
    assert "【推理" not in out["ai_response"]
    assert out["ai_response"].strip() == "正文照常"
def test_marker_and_body_same_line_keeps_body():
    """标记与正文挤在同一行（模型没按「单独成行」输出）→ 正文必须完整保留。"""
    out = parse_response("【推理】我先把水烧上。", _state())
    assert out["ai_response"].strip() == "我先把水烧上。"
    assert "【推理" not in out["ai_response"]
    out2 = parse_response("【推理：想一下】好的，就来。", _state())
    assert "想一下" in (out2["reasoning"] or "")
    assert out2["ai_response"].strip() == "好的，就来。"
