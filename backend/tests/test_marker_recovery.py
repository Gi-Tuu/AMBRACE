# -*- coding: utf-8 -*-
"""M2-S5 标记保底测试：parse_response 置 marker_truncated、正文尾部标记泄漏强剥、chat_service 优先补提"""
from app.agent.response_parser import parse_response


def _state(**kw):
    base = {"user_message": "hi", "character_id": 1, "user_id": 1, "session_id": 1,
            "character_info": {}, "should_update_memory": False, "new_memories": []}
    base.update(kw)
    return base


def test_marker_truncated_flag_set():
    state = parse_response("今天聊得开心。【记忆：用户喜欢咖啡", _state())
    assert state["marker_truncated"] is True
    # 正文不包含未闭合尾巴
    assert "【记忆" not in state["ai_response"]


def test_marker_truncated_flag_not_set_on_clean():
    state = parse_response("今天聊得开心。【记忆：用户喜欢咖啡】", _state())
    assert state["marker_truncated"] is False
    assert state["ai_response"] == "今天聊得开心。"  # 闭合记忆标记被正常剥离（主链路既有行为）


def test_tail_leakage_stripped_known_markers():
    """已知标记前缀的未闭合尾巴被强剥（M2-S5 泄漏兜底）"""
    for tail in ("【记忆：用户喜欢", "【状态更新：开心", "[SEARCH", "【CAL_NOTE：明天", "【MEMO：买"):
        state = parse_response(f"正文内容完好了。{tail}", _state())
        assert not state["ai_response"].endswith(("记忆", "更新", "SEARCH", "CAL_NOTE", "MEMO", "：", ":")), tail
        assert state["ai_response"].startswith("正文内容完好") or state["ai_response"] == "", tail


def test_normal_bracket_text_not_stripped():
    """非标记词的未闭合方括号（普通文本）不受强剥影响"""
    state = parse_response("这个梗来自【出处待考，反正很好笑", _state())
    # 【出处 待考 不含已知标记前缀 → 保留（仅 S11 埋点，不剥正文）
    assert "出处待考" in state["ai_response"]


def test_truncation_sets_memory_for_channel_b():
    """截断时 state 记录可被 chat_service 读取（marker_truncated 键存在且布尔）"""
    state = parse_response("好的。【推理：我觉得", _state())
    assert isinstance(state["marker_truncated"], bool) and state["marker_truncated"] is True
