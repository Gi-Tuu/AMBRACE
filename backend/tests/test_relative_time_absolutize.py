# -*- coding: utf-8 -*-
"""Q2（2026-09-25）：现状 trace「未完成计划」注入相对时间词的失真修复。

背景：``prospective_intents.content`` 是当初写计划时的话（实例：「我承诺明天替用户喂芒芒，
让用户躺着休息」），two-pass 重读把它原样注入时「明天」早已失真 ⇒ 以该行的 created_at
（缺失则 updated_at）为基准日换算成绝对日期（北京日界，M月D日）。

全部为纯函数用例：dict / SimpleNamespace 造假行，不连库、不起服务。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.scheduling import state_trace as st
from app.utils.relative_time import absolutize_relative_dates

# 库内 naive UTC 口径：2026-09-19 05:00 UTC ＝ 北京 2026-09-19 13:00（周六），基准日 9月19日
_BASE = datetime(2026, 9, 19, 5, 0, 0)
# 派单实例基准日：2026-09-23 写下承诺 ⇒「明天」＝9月24日
_BASE_0923 = datetime(2026, 9, 23, 5, 0, 0)


# ────────────────────────── ① 实例句 + 北京日界 ──────────────────────────

def test_实例句明天换成绝对日期():
    """09-23 写下的「明天」注入时必须是 9月24日（该实例的真实基准日）。"""
    text = "我承诺明天替用户喂芒芒，让用户躺着休息"
    assert absolutize_relative_dates(text, _BASE_0923) == \
        "我承诺9月24日替用户喂芒芒，让用户躺着休息"


def test_同一句以0919为基准日时明天是9月20日():
    """+1 天是相对**基准日**的算术：北京 09-19 13:00 ⇒ 明天＝9月20日（9月24日 对应 09-23 基准）。"""
    assert absolutize_relative_dates("我承诺明天替用户喂芒芒", _BASE) == "我承诺9月20日替用户喂芒芒"


def test_北京日界_utc晚间已跨到北京次日():
    """UTC 09-19 20:00 ＝ 北京 09-20 04:00 ⇒ 基准日按北京算，明天＝9月21日。"""
    assert absolutize_relative_dates("明天见", datetime(2026, 9, 19, 20, 0, 0)) == "9月21日见"


# ────────────────────────── ② 逐词替换表 ──────────────────────────

@pytest.mark.parametrize("word,expect", [
    ("大前天", "9月16日"), ("前天", "9月17日"), ("昨天", "9月18日"), ("昨日", "9月18日"),
    ("今天", "9月19日"), ("今日", "9月19日"), ("本日", "9月19日"),
    ("明天", "9月20日"), ("明日", "9月20日"), ("后天", "9月21日"), ("大后天", "9月22日"),
])
def test_相对日逐词替换(word, expect):
    assert absolutize_relative_dates(f"你说{word}见面", _BASE) == f"你说{expect}见面"


def test_跨年跨月进位():
    assert absolutize_relative_dates("明天见", datetime(2026, 12, 31, 5, 0, 0)) == "1月1日见"
    assert absolutize_relative_dates("昨天见", datetime(2026, 1, 1, 5, 0, 0)) == "12月31日见"


def test_多处出现逐处替换且不留残词():
    out = absolutize_relative_dates("明天来，明天再说，后天一定来", _BASE)
    assert out == "9月20日来，9月20日再说，9月21日一定来"
    assert out and "明天" not in out and "后天" not in out, "不得产生空串或残缺词"


def test_长词优先_大前天前天同现各归各位():
    assert absolutize_relative_dates("大前天和前天的区别", _BASE) == "9月16日和9月17日的区别"
    assert absolutize_relative_dates("大后天与后天", _BASE) == "9月22日与9月21日"


# ────────────────────────── ③ 周锚点 ──────────────────────────

@pytest.mark.parametrize("word,expect", [
    ("下周三", "9月23日"),   # 基准日所在周周一＝9月14日 ⇒ 下周＝9月21日起，周三＝9月23日
    ("下周一", "9月21日"),
    ("本周一", "9月14日"),
    ("这周五", "9月18日"),
    ("本周日", "9月20日"),   # 周日＝该周最后一天
    ("下周天", "9月27日"),
    ("这周末", "9月19日"),   # 该周周六（基准日 9月19日本身就是周六）
    ("下周末", "9月26日"),
    ("本周末", "9月19日"),
])
def test_周锚点替换(word, expect):
    assert absolutize_relative_dates(f"{word}一起喂猫", _BASE) == f"{expect}一起喂猫"


# ────────────────────────── ④ 误切屏蔽 ──────────────────────────

@pytest.mark.parametrize("raw", [
    "前几天你说过这话",
    "前些天忙忘了",
    "这几天还好",
    "以后天气转凉注意腰",
])
def test_屏蔽词原样不动(raw):
    assert absolutize_relative_dates(raw, _BASE) == raw


# ────────────────────────── ⑤ 边界：fail-open ──────────────────────────

def test_base为None原样返回():
    assert absolutize_relative_dates("我承诺明天替用户喂芒芒", None) == "我承诺明天替用户喂芒芒"


def test_空文本返回空串():
    assert absolutize_relative_dates("", _BASE) == ""


def test_无相对词原样返回():
    raw = "答应过替用户喂芒芒"
    assert absolutize_relative_dates(raw, _BASE) == raw


@pytest.mark.parametrize("bad_base", [
    "不是日期", object(), 123, {"a": 1},
])
def test_基准日不可解析原样返回(bad_base):
    """脏值一律收敛成原文（宁漏不编），绝不抛异常。"""
    assert absolutize_relative_dates("明天见", bad_base) == "明天见"


def test_iso串与date对象也可作基准():
    assert absolutize_relative_dates("明天见", "2026-09-19 05:00:00") == "9月20日见"
    assert absolutize_relative_dates("明天见", date(2026, 9, 19)) == "9月20日见"


# ────────────────────────── ⑥ state_trace 接线 ──────────────────────────

def test_intent_line按created_at换算():
    row = {"content": "我承诺明天替用户喂芒芒", "created_at": _BASE_0923}
    assert st.intent_line(row) == "- 我承诺9月24日替用户喂芒芒"


def test_intent_line无created_at时退到updated_at():
    row = {"content": "下周三陪用户复查", "updated_at": _BASE}
    assert st.intent_line(row) == "- 9月23日陪用户复查"


def test_intent_line两个时间戳都没有则原样():
    assert st.intent_line({"content": "我承诺明天替用户喂芒芒"}) == "- 我承诺明天替用户喂芒芒"
    assert st.intent_line({"content": "明天见", "created_at": None, "updated_at": None}) == "- 明天见"


def test_intent_line脏时间戳不炸主链路():
    from types import SimpleNamespace
    row = SimpleNamespace(content="明天见", created_at=object(), updated_at=None)
    assert st.intent_line(row) == "- 明天见", "换算失败必须收敛成原文"


def test_intent_line空内容仍返回空串():
    assert st.intent_line({"content": "   ", "created_at": _BASE}) == ""


def test_接线后单条计划的分区结构不变():
    """只有一条 intent 时：段头仍是「· 未完成计划」、行前缀仍是「- 」、总头不动。"""
    out = st.render_state_trace(
        intent_lines=[st.intent_line({"content": "我承诺明天替用户喂芒芒", "created_at": _BASE_0923})])
    lines = out.splitlines()
    assert lines[0].startswith("【当前现状速读】")
    assert lines[1] == st._SEC_INTENTS, "段头文案与位置不得改变"
    assert lines[2].startswith("- "), f"行前缀必须是「- 」：{lines[2]}"
    assert lines[2] == "- 我承诺9月24日替用户喂芒芒"
    assert st._SEC_FACTS not in out and st._SEC_SLOTS not in out, "空分区仍整段省略"


def test_接线不改单行与总长上限():
    long_row = {"content": "明天" + "连" * 300, "created_at": _BASE}
    out = st.render_state_trace(intent_lines=[st.intent_line(long_row)] * 40)
    body = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert body and all(len(ln) <= st.TRACE_LINE_CHARS for ln in body), \
        f"单行必须 ≤{st.TRACE_LINE_CHARS}：{[len(x) for x in body]}"
    assert out and len(out) <= st.TRACE_TOTAL_CHARS, f"总长必须 ≤{st.TRACE_TOTAL_CHARS}：{len(out)}"
