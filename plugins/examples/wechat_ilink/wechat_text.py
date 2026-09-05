# -*- coding: utf-8 -*-
"""微信消息出口净文（纯函数，无 IO）——L2 出口净文管线。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

把 bridge_relay_impl 返回给 openclaw 的整段文本做一次「可直接读」收敛：

1. 剥仍未闭合/漏网的结构化标记：复用 ``app.agent.response_parser`` 的
   ``strip_stream_display``（已闭合的 记忆/自述/状态/策略/推理 + SEARCH/GEN_IMAGE/…/MCP）与
   ``strip_unclosed_markers``（尾部未闭合），并加一层「全串已知标记前缀」兜底（允许未闭合到行尾）；
2. 不删括号（2026-09-05 用户拍板：括号是解释性表达，予以保留），仅兜底畸形——
   尾部未闭合括号/方括号截断清理、折叠多余空白；
3. 折叠多余空白/换行，正文 ≤ ``_MAX_LEN`` 硬上限（超出截断并补 …）；
4. 返回 ``{text, stripped:[...], truncated:bool, original_len:int}`` 供审计。

本文件只含纯函数、无 IO；任何 ``app.agent`` 依赖都在函数内惰性 import，
以便在任意环境下独立加载、测试里注入 mock。
"""
from __future__ import annotations

import re

# 已知结构化标记名称（与 app.agent.actions._STRIP_PATTERNS / response_parser 兜底正则同源收敛）。
# 仅匹配「紧跟 [ 或 【 的已知关键字」，普通解释性括号（（）/()）与其它【…】文本不受影响。
_MARKER_NAMES = (
    "SEARCH|RECALL|GEN_IMAGE|IMG_TEXT|CAL_NOTE|MEMO"
    "|TIMER|计时器|策略|推理|记忆|自述更新|自述删除|自述|状态更新|mcp\\.[A-Za-z0-9_.-]+"
)
_MARKER_PREFIX_RE = re.compile(
    rf"[\[【]\s*(?:{_MARKER_NAMES})[^\]】]*[\]】]?",
    re.IGNORECASE,
)

# 开/闭括号配对：用于「尾部未闭合括号」的截断清理（与 response_parser._OPEN_MAP 同源）。
_OPEN_MAP = {"【": "】", "[": "]", "（": "）", "(": ")"}

_MAX_LEN = 500  # 正文硬上限（字）。
_MAX_STRIPPED = 8  # stripped 审计列表最多保留条目数，防异常长列表。


def _last_unclosed_open(text: str) -> int:
    """返回最后一个未闭合开括号的位置（``【`` ``[`` ``（`` ``(``）；全闭合返回 len(text)。"""
    stack: list[tuple[int, str]] = []
    for i, ch in enumerate(text):
        if ch in _OPEN_MAP:
            stack.append((i, ch))
        elif ch in _OPEN_MAP.values():
            if stack and _OPEN_MAP[stack[-1][1]] == ch:
                stack.pop()
    if stack:
        return stack[-1][0]
    return len(text)


def _collapse_ws(text: str) -> str:
    """折叠多余空白/换行：连续空格/全角空格→1，行首尾空白去除，连续空行→1。"""
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"[ \t\u3000]*\n[ \t\u3000]*", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _truncate(text: str, max_len: int) -> tuple[str, bool]:
    """硬上限截断（超出补 …），返回 (text, truncated)。结果长度 ≤ max_len。"""
    if len(text) <= max_len:
        return text, False
    if max_len <= 1:
        return text[:max_len], True
    return text[: max_len - 1].rstrip() + "…", True


def _add_stripped(stripped: list[str], tag: str) -> None:
    """向审计列表追加去重条目，超长截断。"""
    if tag and tag not in stripped and len(stripped) < _MAX_STRIPPED:
        stripped.append(tag)


def clean_wechat_text(raw, max_len: int = _MAX_LEN) -> dict:
    """微信出口净文（纯函数，无 IO）。

    返回 ``{"text": str, "stripped": [str...], "truncated": bool, "original_len": int}``：
    - ``text``：净文后的可直接读文本（空输入返回空串）；
    - ``stripped``：本次剥离了什么（类别标签），供审计；
    - ``truncated``：是否触发超过 ``max_len`` 的字数硬截断；
    - ``original_len``：原始文本长度（字符）。
    """
    raw = "" if raw is None else str(raw)
    original_len = len(raw)
    if not raw.strip():
        return {"text": "", "stripped": [], "truncated": False, "original_len": original_len}

    stripped: list[str] = []
    text = raw

    # 1) 已闭合结构化标记（记忆/自述/状态/策略/推理 + SEARCH/GEN_IMAGE/…/MCP）
    try:
        from app.agent.response_parser import strip_stream_display
        cleaned = strip_stream_display(text)
        if cleaned != text:
            _add_stripped(stripped, "structured_markers")
            text = cleaned
    except Exception:  # noqa: BLE001 - 净文绝不因依赖缺失而抛错
        pass

    # 2) 未闭合/漏网结构化标记：尾部强剥（response_parser 兜底正则）+ 全串前缀兜底
    try:
        from app.agent.response_parser import strip_unclosed_markers
        tail = strip_unclosed_markers(text)
        if tail != text:
            _add_stripped(stripped, "unclosed_markers")
            text = tail
    except Exception:  # noqa: BLE001
        pass
    prefix = _MARKER_PREFIX_RE.sub("", text)
    if prefix != text:
        _add_stripped(stripped, "unclosed_markers")
        text = prefix

    # 3) 尾部未闭合括号/方括号清理（防闪退式畸形文本；括号本体保留）
    idx = _last_unclosed_open(text)
    if idx < len(text):
        _add_stripped(stripped, "unclosed_bracket_tail")
        text = text[:idx].rstrip()

    # 4) 折叠多余空白/换行
    text = _collapse_ws(text)

    # 5) 硬上限截断
    text, truncated = _truncate(text, max_len)

    return {
        "text": text,
        "stripped": stripped,
        "truncated": truncated,
        "original_len": original_len,
    }


__all__ = ["clean_wechat_text", "_MAX_LEN"]
