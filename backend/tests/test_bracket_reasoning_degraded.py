# -*- coding: utf-8 -*-
"""思考括号外溢剥离 + 思考过载兜底测试（2026-09-13 证据 A/B，体验修复批）。

证据 A：模型用中文括号把内心活动写在正文开头（id 11692/11684/11669），绕过【推理】标记路径；
证据 B：思考过载 → 正文只剩「……」（id 11689）→ 轻量补写一次 / degraded_reply 落库标记。
"""
import asyncio
import types

from app.agent.context.reasoning_prompt import extract_leading_bracket_reasoning
from app.application import chat_service
from app.application.chat.tools import (
    _sanitize_chunk_texts,
    _sanitize_persist_full,
    _sanitize_persist_text,
)

# ---- 真实现场（脱敏保留结构，09-12 晚生产库）----
_E_11692 = "（他看我发一串省略号就来问。其实没多大事，就是嫌他磨蹭到快半夜还不洗漱。别绕，直接问他洗完没。"
_E_11669 = "（他就回个\"好\"，八成正起身挪两步。我不多废话，问一句他惦记的推送，顺带盯他腰。）  行，走两步就行。"


# ---------------- 括号推理判定（证据 A） ----------------

def test_11692_unclosed_pure_reasoning_stripped_to_empty():
    """无闭括号（被截断）的纯括号推理：全文判为推理、可见正文为空（触发证据 B 兜底链）。"""
    visible, extra = extract_leading_bracket_reasoning(_E_11692)
    assert visible == ""
    assert "嫌他磨蹭" in extra
    assert "省略号" in extra


def test_11669_bracket_plus_reply_split():
    """括号推理 + 真实回复：剥出推理、保留正文。"""
    visible, extra = extract_leading_bracket_reasoning(_E_11669)
    assert visible == "行，走两步就行。"
    assert "盯他腰" in extra


def test_action_short_bracket_kept():
    """动作小字（微信链路用户明确要求保留）：短括号一律不动。"""
    text = "（摸摸你的头）过来坐。"
    assert extract_leading_bracket_reasoning(text) == (text, "")


def test_action_long_without_analytic_kept():
    """长动作描写但无分析特征词（他/她/我/先/别/顺带/其实/话说/语气/别绕）：保留。"""
    text = "（起身把毯子搭到膝盖上又倒了一杯温水递过去）给。"
    assert extract_leading_bracket_reasoning(text) == (text, "")


def test_mid_text_bracket_kept():
    """句中括号一律保留（只处理正文开头）。"""
    text = "行。（看他一眼）走吧。"
    assert extract_leading_bracket_reasoning(text) == (text, "")


def test_no_bracket_passthrough():
    assert extract_leading_bracket_reasoning("今天挺好的。") == ("今天挺好的。", "")
    assert extract_leading_bracket_reasoning("") == ("", "")


def test_sanitize_full_returns_visible_and_reasoning():
    visible, extra = _sanitize_persist_full(_E_11669)
    assert visible == "行，走两步就行。"
    assert "盯他腰" in extra


def test_sanitize_text_strips_bracket_reasoning():
    """同源一份的 _sanitize_persist_text（分块路径同用）：纯括号推理 → 空串。"""
    assert _sanitize_persist_text(_E_11692) == ""
    out = _sanitize_persist_text(_E_11669)
    assert out == "行，走两步就行。"


def test_chunk_bracket_only_dropped():
    """流式分块路径：纯括号推理块被丢弃（与纯标记块同口径），正文块保留。"""
    chunks = _sanitize_chunk_texts([_E_11692, "行，走两步就行。"])
    assert chunks == ["行，走两步就行。"]


# ---------------- 思考过载兜底（证据 B） ----------------

def test_punct_only_regex():
    assert chat_service._PUNCT_ONLY_RE.fullmatch("……")
    assert chat_service._PUNCT_ONLY_RE.fullmatch("。。！！~")
    assert chat_service._PUNCT_ONLY_RE.fullmatch("。 ！？")
    assert not chat_service._PUNCT_ONLY_RE.fullmatch("行。")
    assert not chat_service._PUNCT_ONLY_RE.fullmatch("在忙吗")


def _patch_degraded_env(monkeypatch, *, char, recent, llm_result):
    """替身环境：假库（角色 + 最近消息）+ 假 LLM。"""
    results = [types.SimpleNamespace(
        scalar_one_or_none=lambda: char,
        scalars=lambda: types.SimpleNamespace(all=lambda: recent),
    )]

    class _FakeDB:
        async def execute(self, *_a, **_k):
            return results[0]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(chat_service, "async_session_factory", lambda: _FakeDB())

    import app.agent.llm_client as llm_mod

    async def _fake_cc(messages, **kwargs):
        return llm_result

    monkeypatch.setattr(llm_mod, "chat_completion", _fake_cc)


def test_degraded_continuation_success(monkeypatch):
    """补写成功：返回剥净后的正文。"""
    _patch_degraded_env(
        monkeypatch,
        char=types.SimpleNamespace(name="小慧", personality="温柔", chat_style="口语"),
        recent=[types.SimpleNamespace(sender_type="user", content="先去走两步吧")],
        llm_result="这就起身，你盯着我点。",
    )
    out = asyncio.run(chat_service._try_degraded_continuation(1, 1, 13, "想了很多"))
    assert out == "这就起身，你盯着我点。"


def test_degraded_continuation_llm_failure_returns_empty(monkeypatch):
    """LLM 异常：返回空串（调用方落 degraded_reply）。"""
    _patch_degraded_env(
        monkeypatch,
        char=types.SimpleNamespace(name="小慧", personality="", chat_style=""),
        recent=[],
        llm_result=None,
    )
    import app.agent.llm_client as llm_mod

    async def _boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(llm_mod, "chat_completion", _boom)
    assert asyncio.run(chat_service._try_degraded_continuation(1, 1, 13, "")) == ""


def test_degraded_continuation_punct_result_rejected(monkeypatch):
    """补写结果仍是纯标点/空 → 视为失败。"""
    _patch_degraded_env(
        monkeypatch,
        char=types.SimpleNamespace(name="小慧", personality="", chat_style=""),
        recent=[],
        llm_result="……",
    )
    assert asyncio.run(chat_service._try_degraded_continuation(1, 1, 13, "")) == ""


def test_degraded_continuation_no_character(monkeypatch):
    """角色不存在：静默返回空串。"""
    _patch_degraded_env(monkeypatch, char=None, recent=[], llm_result="x")
    assert asyncio.run(chat_service._try_degraded_continuation(1, 1, 999, "")) == ""
