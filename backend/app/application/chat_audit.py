# -*- coding: utf-8 -*-
"""RC1 A 方案观测（2026-11-12）：动作式记忆「说了记却没落动作」留痕。

只在 flag `memory_action_first_v1` 开时生效；每条「说了记」的回复留一行 INFO（missing=0/1），
分母＝全部「说了记」回复、分子＝missing=1，灰度期可直接算比例。关档零行为。
"""
from __future__ import annotations

from app.utils.logger import get_logger

_logger = get_logger("services.chat_audit")

_SAID_WORDS = ("记下", "记上", "记牢", "记住了", "记好了")


def observe_action_missing(character_id, source_text: str, actions: list) -> None:
    from app.flags.agent_flags import AGENT_FLAGS

    try:
        if not bool(AGENT_FLAGS.get("memory_action_first_v1", False)):
            return
        text = source_text or ""
        if not any(w in text for w in _SAID_WORDS):
            return
        has_action = any(a.action_type in ("MEMO", "CAL_NOTE") for a in actions)
        has_mem = ("【记忆" in text) or ("[记忆" in text)
        _logger.info("action_first_audit char=%s missing=%s acts=%d mem=%s",
                     character_id, (not has_action and not has_mem), len(actions), has_mem)
    except Exception:  # noqa: BLE001 —— 观测失败不影响主链路
        return
