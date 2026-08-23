# -*- coding: utf-8 -*-
"""AgentAction 统一解析层 + Tool Registry 测试（Phase A，2026-08-16）"""
from datetime import datetime, timedelta, timezone

from app.agent import actions
from app.agent import tools


def test_parse_actions_识别全部标记():
    acts = actions.parse_actions(
        "a[SEARCH]猫咪吃什么[/SEARCH]b[GEN_IMAGE]猫猫吃鱼[/GEN_IMAGE]"
        "[MEMO]喂猫[/MEMO][CAL_NOTE]明天 一起看电影[/CAL_NOTE][timer:20m]【状态更新：准备睡觉】"
    )
    types = [a.action_type for a in acts]
    # 解析顺序固定：SEARCH → IMG_TEXT → GEN_IMAGE → CAL_NOTE → MEMO → TIMER → STATUS_UPDATE
    assert types == ["SEARCH", "GEN_IMAGE", "CAL_NOTE", "MEMO", "TIMER", "STATUS_UPDATE"]
    by = {a.action_type: a for a in acts}
    assert by["SEARCH"].payload["query"] == "猫咪吃什么"
    assert by["GEN_IMAGE"].payload["prompt"] == "猫猫吃鱼"
    assert by["MEMO"].payload["text"] == "喂猫"
    assert by["CAL_NOTE"].payload["text"] == "一起看电影"
    assert "timer" in by["TIMER"].payload["tag"]
    assert by["STATUS_UPDATE"].payload["text"] == "准备睡觉"


def test_parse_actions_空文本():
    assert actions.parse_actions("") == []
    assert actions.parse_actions(None) == []
    assert actions.parse_actions("纯文本没有标记") == []


def test_parse_actions_同一标记多条():
    acts = actions.parse_actions("[SEARCH]a[/SEARCH][SEARCH]b[/SEARCH]")
    assert [a.payload["query"] for a in acts] == ["a", "b"]


def test_strip_actions_剥离全部标记():
    out = actions.strip_actions("a[SEARCH]q[/SEARCH]b[GEN_IMAGE]p[/GEN_IMAGE]c[MEMO]m[/MEMO]d[timer:5m]e")
    assert out == "abcde"
    # 状态更新/记忆等 response_parser 链路标记不在此剥离
    assert actions.strip_actions("正文【状态更新：睡觉】") == "正文【状态更新：睡觉】"


def test_extract_search_无闭合兼容():
    clean, q = actions.extract_search("正文【SEARCH】测试")
    assert q == "测试"
    assert clean == "正文"


def test_extract_gen_image_含配文():
    clean, prompt, img_text = actions.extract_gen_image("[IMG_TEXT]配文[/IMG_TEXT]a[GEN_IMAGE]图[/GEN_IMAGE]b")
    assert clean == "ab"
    assert prompt == "图"
    assert img_text == "配文"


def test_extract_cal_note_今天():
    r = actions.extract_cal_note("[CAL_NOTE]今天 记得喂猫[/CAL_NOTE]")
    assert r is not None
    assert r[1] == "记得喂猫"
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    assert r[0] == today


def test_extract_memo_截断():
    assert actions.extract_memo("[MEMO]x[/MEMO]") == "x"
    assert actions.extract_memo("[MEMO][/MEMO]") is None
    assert actions.extract_memo("没有标记") is None


def test_actions_to_steps_截断长字段():
    acts = [actions.AgentAction("SEARCH", {"query": "x" * 200}, "[SEARCH]...")]
    steps = actions.actions_to_steps(acts)
    assert len(steps[0]["query"]) <= 81


def test_tool_registry_内置工具():
    names = {t.name for t in tools.list_tools()}
    assert {"search", "image_gen", "note_calendar", "note_memo", "timer", "status_update"} <= names
    assert tools.get_tool("search").idempotent is True
    assert tools.get_tool("image_gen").risk_level == "medium"
    assert tools.get_tool_by_action("GEN_IMAGE").name == "image_gen"
    assert tools.get_tool_by_action("SEARCH").action_type == "SEARCH"
    assert tools.get_tool("不存在") is None
