# -*- coding: utf-8 -*-
"""M1-S7 复习校准测试：日额度 flag、实质回复判定（纯函数）"""
from app.scheduling.memory_review import _is_substantive_reply, _review_daily_cap


def test_daily_cap_flag_on():
    monkey_flags = {"review_daily_plus": True}

    class _FakeAF(dict):
        pass

    import app.agent.loop as loop_mod
    original = loop_mod.AGENT_FLAGS
    loop_mod.AGENT_FLAGS = {**original, **monkey_flags}
    try:
        assert _review_daily_cap() == 4
    finally:
        loop_mod.AGENT_FLAGS = original


def test_daily_cap_flag_off_falls_back():
    import app.agent.loop as loop_mod
    original = loop_mod.AGENT_FLAGS
    loop_mod.AGENT_FLAGS = {**original, "review_daily_plus": False}
    try:
        assert _review_daily_cap() == 3
    finally:
        loop_mod.AGENT_FLAGS = original


def test_substantive_replies():
    for text in ("好的我知道了", "对，那天我们确实去了", "不用提这个了", "哈哈原来如此", "是吗？我忘了"):
        assert _is_substantive_reply(text) is True, text


def test_non_substantive_replies():
    for text in ("哦", "嗯", "哦哦", "呵呵", "。", "？？", "！", "嗯。", "  ", "", "好"):
        assert _is_substantive_reply(text) is False, text


def test_similarity_still_wins_over_filler_set():
    """相似命中与实质判定互补：非敷衍短词"好耶"算实质；纯语气词走词表排除"""
    assert _is_substantive_reply("好耶") is True
    assert _is_substantive_reply("嗯嗯") is False
