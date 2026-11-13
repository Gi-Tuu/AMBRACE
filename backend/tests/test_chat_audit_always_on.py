# -*- coding: utf-8 -*-
"""动作留痕合同：`action_first_audit` 与 flag 解耦（2026-11-13 起常开）。

判据：无论 `memory_action_first_v1` 开关与否，凡「说了记」的回复都留一行 INFO（missing=0/1）；
无「说了记」措辞的回复不留行。
"""
from __future__ import annotations

import logging


def _line_actions():
    class _A:
        def __init__(self, t):
            self.action_type = t

    return [_A("MEMO")]


def test_audit_logs_when_flag_off(caplog):
    from app.application import chat_audit
    from app.flags.agent_flags import AGENT_FLAGS

    caplog.set_level(logging.INFO, logger="services.chat_audit")
    AGENT_FLAGS["memory_action_first_v1"] = False
    chat_audit.observe_action_missing(1, "好，记下了，明天提醒你", [])
    assert any("action_first_audit" in r.getMessage() for r in caplog.records), [r.getMessage() for r in caplog.records]
    AGENT_FLAGS["memory_action_first_v1"] = True


def test_audit_missing_numerator(caplog):
    from app.application import chat_audit

    caplog.set_level(logging.INFO, logger="services.chat_audit")
    chat_audit.observe_action_missing(1, "好，我记牢了", _line_actions())
    msgs = [r.getMessage() for r in caplog.records if "action_first_audit" in r.getMessage()]
    assert msgs and "missing=False" in msgs[-1], msgs


def test_audit_skips_replies_without_remember_words(caplog):
    from app.application import chat_audit

    caplog.set_level(logging.INFO, logger="services.chat_audit")
    chat_audit.observe_action_missing(1, "今天天气不错，出去走走吧", [])
    assert not [r for r in caplog.records if "action_first_audit" in r.getMessage()]
