# -*- coding: utf-8 -*-
"""认知注入热度裁剪测试（方案 B，2026-08-16）"""
from app.agent.context_builder import (
    _trim_limits, _dedup_summary_lines, _summary_dedup_note,
    MAX_SUMMARY_INPUT_CHARS, LOW_FREQ_SUMMARY_CHARS, LOW_FREQ_WEAVE_LIMIT,
)


def test_trim_limits_高频不裁剪():
    d = _trim_limits(hot=True)
    assert d["summary_chars"] == MAX_SUMMARY_INPUT_CHARS
    assert d["weave_limit"] == 10


def test_trim_limits_低频裁剪():
    d = _trim_limits(hot=False)
    assert d["summary_chars"] == LOW_FREQ_SUMMARY_CHARS
    assert d["weave_limit"] == LOW_FREQ_WEAVE_LIMIT
    assert d["summary_chars"] < MAX_SUMMARY_INPUT_CHARS
    assert d["weave_limit"] < 10


def test_dedup_summary_lines_完全重复只留最新():
    lines = ["【08-14 概要】用户18号去杭州", "【08-15 概要】用户18号去杭州"]
    assert _dedup_summary_lines(lines) == ["【08-15 概要】用户18号去杭州"]


def test_dedup_summary_lines_不同内容保留():
    lines = ["【08-14 概要】聊了宠物", "【08-15 概要】聊了杭州行程"]
    assert len(_dedup_summary_lines(lines)) == 2


def test_summary_dedup_note():
    assert _summary_dedup_note([]) == ""
    n = _summary_dedup_note(["18号去杭州", "外面下雨"])
    assert "18号去杭州" in n
    assert "勿重复写入" in n
