# -*- coding: utf-8 -*-
"""动作式记忆「说了记却没落动作」留痕（RC1 A 观测；2026-11-13 起常开）。

**与 flag 解耦**：无论 `memory_action_first_v1` 开关如何，每条「说了记」的回复都留一行 INFO
（missing=0/1）；分母＝全部「说了记」回复、分子＝missing=1。这样才能在关/开两档采集真 A/B 基线。
只写一行日志、fire-and-forget、失败静默，不改任何业务行为。
"""
from __future__ import annotations

from app.utils.logger import get_logger

_logger = get_logger("services.chat_audit")

_SAID_WORDS = ("记下", "记上", "记牢", "记住了", "记好了")


def observe_action_missing(character_id, source_text: str, actions: list) -> None:

    try:
        text = source_text or ""
        if not any(w in text for w in _SAID_WORDS):
            return
        has_action = any(a.action_type in ("MEMO", "CAL_NOTE") for a in actions)
        has_mem = ("【记忆" in text) or ("[记忆" in text)
        _logger.info("action_first_audit char=%s missing=%s acts=%d mem=%s",
                     character_id, (not has_action and not has_mem), len(actions), has_mem)
    except Exception:  # noqa: BLE001 —— 观测失败不影响主链路
        return
