# -*- coding: utf-8 -*-
"""Curated Knowledge 注入分区（Ariadne 模块F，方案A，2026-09-04）。

确定性供给（不走向量、不衰减）：constraint 无条件在场，其余长期知识按核心 TopN +
当前用户文本触发键命中优先。flag ``curated_knowledge`` 关闭时返回空串（零行为变化）。
注册为 TARGET_APPEND 独立 system 块（不动模板槽、不与 world_facts 槽抢位）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND

_logger = logging.getLogger("agent.context.section_curated")

_CURATED_QUOTA_TOKENS = 700  # 独立正配额（2 字符≈1 token）
# 渲染顺序（铁律最前）
_ORDER = ["constraint", "fact", "preference_profile", "relationship_baseline"]
_HEADERS = {
    "constraint": "【必须遵守的人格铁律/硬约束】",
    "fact": "【关于用户与世界的稳定事实】",
    "preference_profile": "【用户长期偏好画像】",
    "relationship_baseline": "【你们的关系基线】",
}


async def curated_knowledge_section(state: dict, ctx: dict) -> list[str]:
    from app.agent.loop import AGENT_FLAGS
    if not AGENT_FLAGS.get("curated_knowledge", False):
        return []  # 默认关：零行为变化
    char_id = state.get("character_id")
    user_id = state.get("user_id", 1)
    if not char_id:
        return []
    try:
        from app.events.facts import get_curated_facts, curated_line
        grouped = await get_curated_facts(
            character_id=char_id, user_id=user_id,
            viewer_type="character", viewer_id=char_id,
            user_text=state.get("user_message", "") or "",
        )
    except Exception as e:
        _logger.warning("curated_knowledge inject failed char=%d: %s", char_id, e)
        return []  # 失败静默，绝不阻塞主回复

    blocks: list[str] = []
    for kind in _ORDER:
        rows = grouped.get(kind) or []
        if not rows:
            continue
        lines = [curated_line(r) for r in rows]
        blocks.append(_HEADERS[kind] + "\n" + "\n".join(lines))
    if not blocks:
        return []

    from app.agent.context_builder import _clip_text_to_quota
    # 引导语：与情景记忆区分，要求模型把这些当作稳定前提而非临时情绪
    # 裁剪应用到「标题 + 正文」整体，保证独立正配额真实生效（标题一起计入）
    finalized = "【编纂知识层（长期稳定，优先于模糊印象；与下方会衰减的情景记忆冲突时以此为准）】\n" + "\n".join(blocks)
    finalized = _clip_text_to_quota(finalized, _CURATED_QUOTA_TOKENS)
    return [finalized]


register_section(ContextSection(
    key="curated_knowledge", builder=curated_knowledge_section, target=TARGET_APPEND,
    quota_tokens=_CURATED_QUOTA_TOKENS, order=46,  # 紧邻 world_facts(order45) 之后
))
