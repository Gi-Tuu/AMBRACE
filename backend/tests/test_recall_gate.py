# -*- coding: utf-8 -*-
"""A4 批 2 / T4（2026-09-27）：召回门纯函数（P0，只算不拦）。

覆盖：必须检索的四类高置信判据、明确跳过的寒暄/符号、以及「其余一律放行」的保守口径。
"""
from app.memory.recall_gate import (
    REASON_CONTINUE,
    REASON_DEFAULT_PASS,
    REASON_EXTRA_QUERIES,
    REASON_LOOKUP_HINT,
    REASON_SMALL_TALK,
    REASON_SUBSTANTIVE,
    REASON_TIME_PHRASE,
    decide_retrieval,
    is_small_talk,
    strip_noise,
)


def test_继续指令与追加查询必须检索():
    assert decide_retrieval("嗯", is_continue=True).reason == REASON_CONTINUE
    assert decide_retrieval("嗯", has_extra_queries=True).reason == REASON_EXTRA_QUERIES


def test_时间短语必须检索():
    d = decide_retrieval("嗯嗯", has_time_phrase=True)
    assert d.retrieve is True and d.reason == REASON_TIME_PHRASE and d.confidence == "high"


def test_问记忆线索必须检索():
    for t in ("你还记得那次吗", "上次说的那个事", "我们之前聊过什么", "那是什么时候"):
        d = decide_retrieval(t)
        assert d.retrieve is True
        assert d.reason in (REASON_LOOKUP_HINT, REASON_SUBSTANTIVE)


def test_长句一律检索():
    d = decide_retrieval("今天我去了一个新的地方感觉还挺不错的")
    assert d.retrieve is True and d.reason == REASON_SUBSTANTIVE


def test_纯寒暄与符号明确跳过():
    for t in ("嗯", "哈哈", "晚安", "谢谢", "好的", "😂😂", "。。。", "  "):
        d = decide_retrieval(t)
        assert d.retrieve is False, t
        assert d.reason == REASON_SMALL_TALK and d.confidence == "high"


def test_其余一律放行_保守():
    d = decide_retrieval("在干嘛呢")
    assert d.retrieve is True
    assert d.reason in (REASON_LOOKUP_HINT, REASON_DEFAULT_PASS)


def test_辅助函数():
    assert strip_noise("  嗯！！ ") == "嗯"
    assert is_small_talk("晚安～") is True
    assert is_small_talk("你还记得吗") is False
