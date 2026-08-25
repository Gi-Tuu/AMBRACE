"""context 注册表：分区注入区定义（ContextSection dataclass + register_section 注册表）。

本文件只承载注册表骨架（分区元数据 + 注册表），不承载具体 builder 实现；
具体 section 实现见 ``section_mcp.py`` / ``section_memories.py``（后续步骤再接入
persona/summaries/moments/pet/phone/world/overlay 等）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

_logger = logging.getLogger("agent.context.sections")

# 分区注入目标：template=填充 SYSTEM_PROMPT_TEMPLATE 的占位槽；append=追加独立 system 块
TARGET_TEMPLATE = "template"
TARGET_APPEND = "append"

# 估算口径：2 字符 ≈ 1 token（中文保守值，与 context_builder 保持一致）
_EST_CHARS_PER_TOKEN = 2


@dataclass
class ContextSection:
    """一个上下文注入分区。

    - ``builder``：async (state, ctx) -> str，返回注入文本（空串表示跳过）。
    - ``target``：TARGET_TEMPLATE（填模板槽）/ TARGET_APPEND（追加 system 块）。
    - ``slot``：target=template 时对应的 SYSTEM_PROMPT_TEMPLATE 占位槽名（如 memories）。
    - ``quota_tokens``：0=不裁剪；>0 时按估算 token 裁剪。
    - ``order``：append 块注入顺序；template 槽无影响。
    - ``enabled``：可整体开关（如 Feature Flag）。
    """

    key: str
    builder: Callable[[dict, dict], Awaitable[str]]
    target: str = TARGET_APPEND
    slot: str | None = None
    quota_tokens: int = 0
    order: int = 100
    enabled: bool = True


_SECTIONS: list[ContextSection] = []


def register_section(section: ContextSection) -> ContextSection:
    """注册注入区（重复 key 覆盖；append 块按 order 排序）。

    注册时机为模块 import 时；`build_context` 所在包 import 这些 section 模块即可触发。
    """
    _SECTIONS[:] = [s for s in _SECTIONS if s.key != section.key]
    _SECTIONS.append(section)
    _SECTIONS.sort(key=lambda s: (s.order, s.key))
    return section


def get_sections() -> list[ContextSection]:
    """返回按 (order, key) 排序的注册分区副本（不影响内部表）。"""
    return list(_SECTIONS)


def _clip_text_to_quota(text: str, quota_tokens: int) -> str:
    """按估算 token 裁剪单块文本（纯函数）：超配额截断尾部；配额内原样返回（零行为变化）。"""
    if text is None:
        return ""
    if quota_tokens <= 0:
        return ""
    budget_chars = quota_tokens * _EST_CHARS_PER_TOKEN
    return text if len(text) <= budget_chars else text[:budget_chars]
