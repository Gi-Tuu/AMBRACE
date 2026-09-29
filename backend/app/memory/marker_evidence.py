# -*- coding: utf-8 -*-
"""标记记忆「用户依据校验」纯判据（2026-09-29；零 IO、零业务依赖、可单测）。

背景（方案《AMBRACE_小方案_模型自写记忆FACT口径_v1_20260929》）：模型自写的
``【记忆：…】/【状态更新】`` 走标记路径落库时，归属与认知状态由
``app/memory/speaker.py::resolve_speaker_from_content`` 按措辞推断；其中「无主语 +
本轮有用户消息」会判成 ``user/FACT``，于是模型自己的推断、复述感知甚至编造的内容
都会以「用户说过的事实」进长期记忆。本模块提供一道**确定性、零 LLM** 的字面判据：
这条标记记忆的正文，在**本轮用户消息**里能不能找到依据。

**影子期判据（务必看清边界）**：结果只用于统计与留痕——挂点 ``app/agent/nodes.py``
的标记写入循环命中「无依据」时只写一条回执 + 一条 INFO 日志，**不改 ``_epi``、不改
speaker、不拒收、不删条**。强约束档（无依据时降级为 character/INFERRED）是下一批的事，
届时另键或同键升级并另行公告。

判据口径（**宁漏判不误判：拿不准一律判「有依据」＝返回 False**）：

1. 两侧同一套归一化：NFKC（全角→半角、兼容字符分解）+ 小写 + 只保留汉字/字母/数字
   （空白、标点、emoji 等一律剔除）；
2. 取归一化 content 的所有**连续 2 字片段**（它是「≥2 字片段」里最短的一档：存在更长的
   公共片段必然存在公共 2 字片段，故二者等价，而 2 字档命中最宽松）；
3. 只要有任一 2 字片段同时出现在归一化 user_msg 里 ⇒ 判「有依据」（返回 False）；
4. content 为空 / user_msg 为空 / 归一化后不足 2 字 / 非字符串 / 超长（> ``_MAX_SCAN_CHARS``）
   / 任何内部异常 ⇒ 一律 False（不降级、不抛出）。
"""
from __future__ import annotations

import re
import unicodedata

# 最短可比片段长度（2 字＝汉字/字母数字串里最宽松的档）
_MIN_PIECE = 2

# 单侧扫描上限：超过视为「读不准」，直接判有依据（影子期宁可漏判，也不做半截比对）
_MAX_SCAN_CHARS = 8000

# 归一化后只保留汉字 / 小写字母 / 数字
_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _normalize(text: str) -> str:
    """NFKC + 小写 + 剔除空白标点等非汉字字母数字字符。"""
    return _KEEP_RE.sub("", unicodedata.normalize("NFKC", text).lower())


def _pieces(norm: str) -> set[str]:
    """归一化文本的所有连续 2 字片段（长度 <2 时为空集）。"""
    return {norm[i:i + _MIN_PIECE] for i in range(len(norm) - _MIN_PIECE + 1)}


def user_evidence_absent(content: str, user_msg: str) -> bool:
    """这条模型自写的标记记忆，在本轮用户消息里**找不到依据**吗？

    返回 True＝无依据（影子期只留痕）；False＝有依据或拿不准（一律按旧行为）。
    不 mutate 入参，不抛异常。
    """
    try:
        if not isinstance(content, str) or not isinstance(user_msg, str):
            return False
        c_norm = _normalize(content)
        u_norm = _normalize(user_msg)
        # 任一侧太短（凑不出 2 字片段）或超长 ⇒ 拿不准 ⇒ 判「有依据」
        if len(c_norm) < _MIN_PIECE or len(u_norm) < _MIN_PIECE:
            return False
        if len(c_norm) > _MAX_SCAN_CHARS or len(u_norm) > _MAX_SCAN_CHARS:
            return False
        return not (_pieces(c_norm) & _pieces(u_norm))
    except Exception:
        return False


__all__ = ["user_evidence_absent"]
