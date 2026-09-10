# -*- coding: utf-8 -*-
"""T3/C1 记忆注入行时态标注回归（v3.4.6 第三轮，2026-09-10）。

背景：format_memory_line 被 6+ 处共用（主聊天分区/#70 分层/主动消息/persona/shared），
原无时态标注，旧「去长沙」plan/「在长沙」episodic 被当现状续写（Sam 旧记忆窜回漏网通道）。
本次：tense.py 兼容 dict（_g 取值器）+ format_memory_line 在 [记录于] 后插时态标签
（flag memory_line_tense_tag 默认开）。本文件验证时态标签与极简 dict 不受误伤。
"""
from datetime import datetime

from app.memory.format import format_memory_line


def test_plan_已过期_旧安排标签():
    # 8-25 计划「要去长沙出差」，valid_to 落在过去 → 8-31 渲染标记为旧安排·已过期
    line = format_memory_line({
        "content": "要去长沙出差",
        "created_at": datetime(2026, 8, 25),
        "sub_type": "plan",
        "memory_type": "event",
        "valid_to": datetime(2026, 8, 26),
    })
    assert "［旧安排·已过期］" in line
    assert "［计划］" not in line


def test_plan_未过期_计划标签():
    # 有效期在未来 → 未过期计划
    line = format_memory_line({
        "content": "要去南京出差",
        "created_at": datetime(2026, 8, 25),
        "sub_type": "plan",
        "memory_type": "event",
        "valid_to": datetime(2027, 8, 26),
    })
    assert "［计划］ " in line
    assert "［旧安排·已过期］" not in line


def test_event_往事标签():
    # mtype=event 且无计划标记 → 往事
    line = format_memory_line({
        "content": "用户去过长沙回来了",
        "created_at": datetime(2026, 8, 28),
        "memory_type": "event",
    })
    assert "［往事］" in line


def test_transient_当时状态标签():
    line = format_memory_line({
        "content": "用户状态更新：有点累",
        "created_at": datetime(2026, 8, 28),
        "sub_type": "status",
    })
    assert "［当时状态］" in line


def test_shared_events_极简dict_不加标签():
    # shared_events.recall_text 传的只有 content/created_at → 判 enduring，不加时态标签（不误伤）
    line = format_memory_line({
        "content": "用户和角色第一次一起看海",
        "created_at": datetime(2026, 8, 1),
    }, max_len=120)
    assert line == "- [记录于 2026-08-01] 用户和角色第一次一起看海"
    assert "［往事］" not in line and "［计划］" not in line and "［当时状态］" not in line


def test_enduring_恒久记忆_不加标签():
    line = format_memory_line({
        "content": "用户喜欢喝美式咖啡",
        "created_at": datetime(2026, 8, 1),
        "epistemic_status": "FACT",
    })
    assert line == "- [记录于 2026-08-01] 用户喜欢喝美式咖啡"
    assert "［往事］" not in line and "［计划］" not in line


def test_flag关_回旧行():
    """flag 关（memory_line_tense_tag=False）→ 无时态标签，逐字节回旧链路。"""
    import app.agent.loop as loop_mod
    from unittest.mock import patch
    with patch.dict(loop_mod.AGENT_FLAGS, {"memory_line_tense_tag": False}):
        line = format_memory_line({
            "content": "要去长沙出差",
            "created_at": datetime(2026, 8, 25),
            "sub_type": "plan",
            "memory_type": "event",
            "valid_to": datetime(2026, 8, 26),
        })
    assert "［" not in line  # 无任何全角方括号时态标签
    assert "要去长沙出差" in line


def test_shared_events_含计划词仍标往事不判plan():
    """I4（第四轮）：共享事件恒为已发生的共同经历 → tense_hint="episodic" 显式压过
    classify_tense 的 PLAN_MARKERS 兜底（第 4 步在 mtype 之前、且不看 mtype）。

    文本「本来打算…最后一起去看了」是陈述语气、无完成信号、无疑问/否定/引用守卫，
    若无 hint 会被第 4 步误判 plan；补 memory_type=event 也压不住，必须标［往事］。
    """
    line = format_memory_line(
        {"content": "两人原本打算去看的那场展最后一起去看了",
         "created_at": datetime(2026, 8, 1), "memory_type": "event"},
        max_len=120, tense_hint="episodic",
    )
    assert "［往事］" in line
    assert "［计划］" not in line and "［旧安排·已过期］" not in line
