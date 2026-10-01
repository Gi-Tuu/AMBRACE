"""context 注册表：分区注入区定义（ContextSection dataclass + register_section 注册表）。

本文件只承载注册表骨架（分区元数据 + 注册表），不承载具体 builder 实现；
具体 section 实现见 ``section_mcp.py`` / ``section_memories.py``（后续步骤再接入
persona/summaries/moments/pet/phone/world/overlay 等）。

顺序约定：见 docs/context-order-convention.md（状态/条件前置、素材居中、诉求最后）。
注意 ``order`` 只决定本表内 builder 的**执行**顺序：**append 块的真实落位**由
``legacy.py`` 的 ``if _sv and "<key>" in _sv`` 链决定，template 槽的落位由
``SYSTEM_PROMPT_TEMPLATE`` 字面量决定——改 order 不等于改注入顺序。
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
    - ``order``：builder 执行顺序（(order, key) 兜底）；不决定注入落位（见文件头 docstring
      与 docs/context-order-convention.md 的取号规则与三条红线）。
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


# ── 批 4 M2-b1（2026-10-01）：念头池聊天侧 append 分区 ──────────────────────────
# 取号照 docs/context-order-convention.md §3.2：50–59（append 素材段）已占满，
# 启保留空档 70–79 段的 70；不移动任何既有 order（红线③）。
# order 只决定 builder 执行序，真实落位由 context_builder._inject_thought_pool_block
# 在装配尾部插到 continue_payload 之前（红线②：诉求/指令恒最后）。
# 纪律：flag thought_pool_v1 关或角色未命中灰度 ⇒ 首行即返回空串，**一次 SQL 都不发**；
# 结果缓存进 state，供注册表路径与 context_builder 后处理共用（避免双查）。

_THOUGHT_POOL_ORDER = 70
_THOUGHT_POOL_STATE_KEY = "_thought_pool_block"


async def thought_pool_section(state: dict, ctx: dict) -> str:
    """念头池聊天侧注入文本（素材，不是规则）。flag/灰度关 ⇒ 空串（零 SQL、零行为变化）。

    注入体**不写元叙述**（无「你有 N 条念头」类实现概念，设计 §8 不做清单第 7 条），
    只写那一件事本身。结果缓存进 ``state[_THOUGHT_POOL_STATE_KEY]``：注册表 ``_run_sections``
    与 ``context_builder`` 装配后处理共用同一次取数，绝不双查。
    """
    cached = state.get(_THOUGHT_POOL_STATE_KEY)
    if cached is not None:
        return cached if isinstance(cached, str) else ""
    text = ""
    try:
        from app.application.thought_pool_service import (
            thought_pool_v1_allowed, fetch_one_thought, build_injection_text,
        )
        char_id = state.get("character_id")
        if thought_pool_v1_allowed(char_id):
            from app.db.database import async_session_factory
            async with async_session_factory() as db:
                th = await fetch_one_thought(db, char_id, state.get("user_id"))
            text = build_injection_text(th)
    except Exception as e:  # 生效层异常绝不拖垮上下文装配
        _logger.warning("thought_pool section failed: %s", e)
        text = ""
    state[_THOUGHT_POOL_STATE_KEY] = text
    return text


register_section(ContextSection(
    key="thought_pool",
    builder=thought_pool_section,
    target=TARGET_APPEND,
    order=_THOUGHT_POOL_ORDER,
))


def _clip_text_to_quota(text: str, quota_tokens: int) -> str:
    """按估算 token 裁剪单块文本（纯函数）：超配额截断尾部；配额内原样返回（零行为变化）。"""
    if text is None:
        return ""
    if quota_tokens <= 0:
        return ""
    budget_chars = quota_tokens * _EST_CHARS_PER_TOKEN
    return text if len(text) <= budget_chars else text[:budget_chars]
