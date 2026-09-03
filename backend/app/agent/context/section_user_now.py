# -*- coding: utf-8 -*-
"""「用户最新状态」跨角色权威分区（§20.6，2026-09-04 落地）。

确定性供给（不走向量）：所有角色共享用户级事实（location/job/relationship/living/goal_state/health），
在提示词层面声明「与旧记忆冲突时以此为准」。flag ``global_user_facts`` 关闭时返回空（零行为变化）。
注册为 TARGET_APPEND 独立 system 块（不动模板槽，与 world_facts 槽不抢位）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND

_logger = logging.getLogger("agent.context.section_user_now")

_USER_NOW_QUOTA_TOKENS = 300  # 独立正配额（2 字符≈1 token）
_HEADER = "【用户最新状态（跨角色权威；与下方会衰减的旧记忆冲突时以此为准）】"


async def user_now_section(state: dict, ctx: dict) -> list[str]:
    from app.agent.loop import AGENT_FLAGS
    if not AGENT_FLAGS.get("global_user_facts", False):
        return []  # 默认关：零行为变化
    user_id = state.get("user_id", 1)
    try:
        from app.memory.user_facts import build_user_now_text
        text = await build_user_now_text(user_id)
    except Exception as e:
        _logger.warning("user_now inject failed user=%s: %s", user_id, e)
        return []  # 失败静默，绝不阻塞主回复
    if not text or text.strip() == "无":
        return []
    from app.agent.context_builder import _clip_text_to_quota
    finalized = _clip_text_to_quota(_HEADER + "\n" + text, _USER_NOW_QUOTA_TOKENS)
    return [finalized]


register_section(ContextSection(
    key="user_now", builder=user_now_section, target=TARGET_APPEND,
    quota_tokens=_USER_NOW_QUOTA_TOKENS, order=44,  # 紧邻 world_facts(45) 之前；memories(40) 之后
))
