# -*- coding: utf-8 -*-
"""wechat_text 微信出口净文（L2）纯函数测试。

覆盖：
- 空/纯空白输入（原样返回空、无剥离、不截断）；
- 普通文本原样通过；
- 超过 _MAX_LEN 的截断（补 …，长度 ≤ 上限）；
- 自定义 max_len 截断；
- 未闭合/漏网结构化标记前缀剥离（尾部未闭合【推理、全串已知前缀）；
- 已闭合结构化标记剥离（【状态更新】【SEARCH】…）；
- 括号保留（用户拍板）：动作/解释性括号不删、不转换；
- 尾部未闭合括号截断清理；
- 空白/换行折叠；
- 审计字段（stripped / truncated / original_len）。
"""
import pathlib
import sys

import pytest

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"


def _load():
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    if "wechat_text" not in sys.modules:
        import importlib  # noqa: PLC0415
        importlib.import_module("wechat_text")
    return sys.modules["wechat_text"]


@pytest.fixture()
def wt():
    return _load()


# ------------------------------------------------------------------ 空/普通
def test_empty_and_whitespace(wt):
    assert wt.clean_wechat_text("") == {"text": "", "stripped": [], "truncated": False, "original_len": 0}
    r = wt.clean_wechat_text("   \n\t ")
    assert r["text"] == "" and r["stripped"] == [] and r["truncated"] is False
    assert r["original_len"] == 6


def test_plain_text_unchanged(wt):
    r = wt.clean_wechat_text("你好呀，今天过得怎么样？")
    assert r["text"] == "你好呀，今天过得怎么样？"
    assert r["stripped"] == [] and r["truncated"] is False


# ------------------------------------------------------------------ 截断
def test_truncate_over_default_max(wt):
    r = wt.clean_wechat_text("字" * 600)
    assert r["truncated"] is True
    assert len(r["text"]) <= wt._MAX_LEN
    assert r["text"].endswith("…")


def test_truncate_custom_max(wt):
    r = wt.clean_wechat_text("a" * 10, max_len=5)
    assert r["truncated"] is True
    assert r["text"] == "aaaa…"
    assert len(r["text"]) == 5


def test_not_truncated_within_limit(wt):
    r = wt.clean_wechat_text("短" * (wt._MAX_LEN - 1))
    assert r["truncated"] is False
    assert r["text"] == "短" * (wt._MAX_LEN - 1)


# ------------------------------------------------------------------ 未闭合/漏网标记
def test_unclosed_marker_tail_stripped(wt):
    r = wt.clean_wechat_text("今天很开心【推理：因为跟喜欢的人待在一起")
    assert r["text"] == "今天很开心"
    assert "unclosed_markers" in r["stripped"]


def test_unclosed_marker_prefix_via_prefix_regex(wt):
    # 尾部未闭合但 keywords 不在 response_parser 尾部正则列表中（如 RECALL）→ 全串前缀兜底剥离
    r = wt.clean_wechat_text("先这样吧[RECALL：上次聊的")
    assert r["text"] == "先这样吧"
    assert "unclosed_markers" in r["stripped"]


def test_closed_marker_stripped(wt):
    r = wt.clean_wechat_text("【状态更新：我努力了一天】吃饭啦")
    assert r["text"] == "吃饭啦"
    assert "structured_markers" in r["stripped"]


def test_action_marker_stripped(wt):
    r = wt.clean_wechat_text("[SEARCH]天气如何[/SEARCH]今天天气不错")
    assert r["text"] == "今天天气不错"
    assert "structured_markers" in r["stripped"]


# ------------------------------------------------------------------ 括号保留（用户拍板：不删、不转换）
def test_parenthetical_action_preserved(wt):
    # 纯动作短记默认不做自然化转写（留给 L1 生成约束治本），括号整体保留
    r = wt.clean_wechat_text("今天很开心（摸摸头）")
    assert r["text"] == "今天很开心（摸摸头）"
    assert "structured_markers" not in r["stripped"]


def test_explanatory_note_preserved(wt):
    # 解释性/注释性括号予以保留，不作剥离
    r = wt.clean_wechat_text("这句话是重点（注意这里）")
    assert r["text"] == "这句话是重点（注意这里）"
    # 非已知标记关键字的【…】解释性文本同样保留
    r2 = wt.clean_wechat_text("好呀【这句是补充说明】")
    assert r2["text"] == "好呀【这句是补充说明】"


def test_trailing_unclosed_bracket_trimmed(wt):
    r = wt.clean_wechat_text("今天很开心（笑")
    assert r["text"] == "今天很开心"
    assert "unclosed_bracket_tail" in r["stripped"]


# ------------------------------------------------------------------ 空白折叠 / 审计
def test_whitespace_collapsed(wt):
    r = wt.clean_wechat_text("a   b\n\n\nc")
    assert r["text"] == "a b\nc"


def test_original_len_reported(wt):
    assert wt.clean_wechat_text("abc")["original_len"] == 3
    assert wt.clean_wechat_text("")["original_len"] == 0


def test_mixed_structured_and_truncated(wt):
    r = wt.clean_wechat_text("[SEARCH]q[/SEARCH]" + "字" * 600)
    assert r["truncated"] is True
    assert "structured_markers" in r["stripped"]
    assert r["text"].endswith("…")
