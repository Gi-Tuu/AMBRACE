# -*- coding: utf-8 -*-
"""Prospective cue 线索命中注入分区（Ariadne 模块G，2026-09-04）。

用户本轮发言命中某 pending cue 的 cue_terms → 注入一条「你之前还惦记着…」提醒，
帮助模型把旧约定自然接回当前对话。确定性子串匹配，在线零 LLM；只注入、不发主动消息。
注册为 TARGET_APPEND 独立 system 块。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND

_logger = logging.getLogger("agent.context.section_prospective_cue")

_CUE_QUOTA = 260


async def prospective_cue_section(state: dict, ctx: dict) -> list[str]:
    from app.agent.loop import AGENT_FLAGS
    # 触发开关控制（写入与触发两段灰度；触发关则不提示）
    if not AGENT_FLAGS.get("prospective_intent_trigger", False):
        return []
    char_id = state.get("character_id")
    user_text = state.get("user_message", "") or ""
    if not char_id or not user_text.strip():
        return []
    try:
        from app.scheduling.prospective_intent import match_cue_intents
        hits = await match_cue_intents(char_id, user_text)
    except Exception as e:
        _logger.warning("prospective_cue inject failed char=%d: %s", char_id, e)
        return []
    if not hits:
        return []
    lines = [f"- 你之前还惦记着这件事（线索被本轮对话触发）：{r.content[:120]}" for r in hits[:3]]
    from app.agent.context_builder import _clip_text_to_quota
    text = _clip_text_to_quota(
        "【待兑现的约定/心愿（被当前话题触发，可自然提起，不要生硬复述）】\n" + "\n".join(lines),
        _CUE_QUOTA,
    )
    return [text] if text else []


register_section(ContextSection(
    key="prospective_cue", builder=prospective_cue_section, target=TARGET_APPEND,
    quota_tokens=_CUE_QUOTA, order=48,
))
