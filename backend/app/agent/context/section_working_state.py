# -*- coding: utf-8 -*-
"""工作记忆注入分区（M3-b，2026-09-07，docs/设计_M3工作记忆_20260901.md §4.1）。

M3-a（``working_state_enabled``）已开并在攒数据；本分区是 M3-b 的**注入段**：把该角色
最新一条 working_state 行（三桶：进行中/未决问题/近期关系）渲染为独立 system 块注入上下文。

灰度口径（小流量，默认只 char13）：
- 全量开关 ``working_state_inject``（AGENT_FLAGS，默认 False）：开=所有角色注入（后续扩量/热回滚用）；
- 否则仅角色白名单 ``WORKING_STATE_INJECT_GRAY_CHARS`` 内且命中比例桶的角色才注入；
- 其余角色**恒不注入**（零行为变化，与未实现 M3-b 完全一致）。
比例分桶按会话稳定（同一 session 恒定同组，便于观察效果而非逐轮抖动）。

无活跃 working_state 行 → section 整体省略（不输出空标记）。
"""
from __future__ import annotations

import hashlib
import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND

_logger = logging.getLogger("agent.context.section_working_state")

_WS_QUOTA_TOKENS = 300  # 设计 §4.1：≤300 token，超限按桶优先级裁尾

# 小流量灰度角色白名单（2026-09-07：仅 char13——当前唯一有 working_state 行的活跃角色）
WORKING_STATE_INJECT_GRAY_CHARS = frozenset({13})
# 小流量比例（10–20% 区间取中）
WORKING_STATE_INJECT_RATIO = 1.0  # 2026-09-11 扩量：白名单内（当前仅 char13）全量注入，先拿到注入证据；其余角色仍恒不注入

# 桶渲染顺序 = 桶优先级（ongoing > open_questions > relationship_notes，设计 §4.1）
# 身份键与 app/memory/working_state.py 的 IDENTITY_KEY 同源（此处本地声明，避免 import 记忆包）
_BUCKET_RENDER = (
    ("ongoing", "正在进行", "topic"),
    ("open_questions", "悬而未决", "question"),
    ("relationship_notes", "近期关系", "note"),
)
_PER_BUCKET_MAX = 3


def _bucket_0_999(key: str) -> int:
    """稳定分桶：同一 key 恒定落在同一 0-999 桶（md5，跨进程/重启一致）。"""
    return int(hashlib.md5(str(key).encode("utf-8")).hexdigest()[:8], 16) % 1000


def traffic_hit(key: str, ratio: float = WORKING_STATE_INJECT_RATIO) -> bool:
    """确定性小流量命中：桶号 < ratio*1000 即命中（纯函数）。

    ratio<=0 → 恒 False；ratio>=1 → 恒 True（全量）。
    """
    if ratio <= 0:
        return False
    if ratio >= 1:
        return True
    return _bucket_0_999(key) < int(round(ratio * 1000))


def inject_allowed(character_id, session_id=None, *, flags=None) -> bool:
    """是否允许注入工作记忆（纯函数，flags 默认读 AGENT_FLAGS）。

    - ``working_state_inject`` 全量开关开 → 任意角色均允许；
    - 否则：角色在灰度白名单内 **且** 命中会话比例桶才允许；
    - 其余（含 character_id 为空/非白名单/未命中）→ False。
    """
    if character_id is None:
        return False
    if flags is None:
        from app.agent.loop import AGENT_FLAGS
        flags = AGENT_FLAGS
    if flags.get("working_state_inject", False):
        return True
    try:
        cid = int(character_id)
    except (TypeError, ValueError):
        return False
    if cid not in WORKING_STATE_INJECT_GRAY_CHARS:
        return False
    return traffic_hit(f"{cid}:{session_id if session_id is not None else 0}")


def _entry_text(item: dict, identity_key: str) -> str:
    if not isinstance(item, dict):
        return ""
    ident = str(item.get(identity_key) or "").strip()
    if not ident:
        return ""
    detail = str(item.get("detail") or "").strip()
    return f"{ident}（{detail}）" if detail else ident


def render_working_state(state: dict | None) -> str:
    """三桶 → 一行式短句文本（纯函数）；空/非法输入返回空串。"""
    if not isinstance(state, dict):
        return ""
    lines: list[str] = []
    for bucket, label, identity_key in _BUCKET_RENDER:
        items = state.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items[:_PER_BUCKET_MAX]:
            text = _entry_text(item, identity_key)
            if text:
                lines.append(f"- {label}：{text}")
    return "\n".join(lines)


async def working_state_section(state: dict, ctx: dict) -> list[str]:
    char_id = state.get("character_id")
    user_id = state.get("user_id", 1)
    if not char_id:
        return []
    if not inject_allowed(char_id, state.get("session_id")):
        return []  # 非灰度角色/未命中比例：零行为变化
    try:
        from app.db.database import async_session_factory
        from app.application.working_state_service import get_latest
        import json

        async with async_session_factory() as db:
            row = await get_latest(db, user_id, char_id)
        if row is None or not row.content:
            return []
        try:
            parsed = json.loads(row.content)
        except Exception:
            return []
        body = render_working_state(parsed)
    except Exception as e:
        _logger.warning("working_state inject failed char=%d: %s", char_id, e)
        return []  # 失败静默，绝不阻塞主回复
    if not body:
        return []
    try:
        from app.memory.observability import obs_event
        obs_event(char_id, "working_state_injected",
                  {"chars": len(body), "row_id": row.id})
    except Exception:
        pass
    from app.agent.context_builder import _clip_text_to_quota
    text = _clip_text_to_quota(
        "【工作记忆（正在发生的事，作当下语境自然承接，不要生硬复述）】\n" + body,
        _WS_QUOTA_TOKENS,
    )
    return [text] if text else []


register_section(ContextSection(
    key="working_state", builder=working_state_section, target=TARGET_APPEND,
    quota_tokens=_WS_QUOTA_TOKENS, order=20,  # 设计 §4.1 priority 1：早于 mcp(30)/记忆(39+)
))
