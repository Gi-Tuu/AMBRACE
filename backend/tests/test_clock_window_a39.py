# -*- coding: utf-8 -*-
"""A39 批 2a：让 clock 承诺的**精确时刻**能进库（默认关＝逐字节旧行为）。

现场根因（本轮普查读码得到，不是推测）：
  ① 提示词的 INTENT 时间窗只允许 `YYYY-MM-DD~YYYY-MM-DD`、解析只 `strptime("%Y-%m-%d")`
    ⇒ 时刻信息**根本进不了库**，「明早八点」落成 `due_start=当天 00:00／due_end=当天 23:59`；
  ② `_is_date_scoped()` 用「时分=23:59」认日期型 ⇒ 上面那条被判成日期型 ⇒
    `collect_due_promises` 走"当天全天可提"，于是 A37 现网 id=160 在 19:04 就发出去了。
本文件钉的是"这条链现在通了、且关着时一个字都没变"。
"""
from datetime import datetime

import pytest

import app.memory.extractor as ex
from app.scheduling.prospective_intent import _is_date_scoped

PRECISE = "时间窗(YYYY-MM-DD~YYYY-MM-DD；用户给了具体时刻就写成 " \
          "YYYY-MM-DD HH:MM~YYYY-MM-DD HH:MM，不确定写无)"


def _intent(raw: str):
    return ex._parse_intent_line("INTENT: 明早八点叫我 | promise | %s | 无 | high" % raw)


# ───────────────────────── 一、解析侧：旧输入结果不变，新输入能带时刻

def test_只给日期时逐字节保持旧口径():
    got = _intent("2026-10-09~2026-10-10")
    assert got["due_start"] == datetime(2026, 10, 9, 0, 0)
    assert got["due_end"] == datetime(2026, 10, 10, 23, 59)
    assert _is_date_scoped(got["due_end"]) is True       # 旧行为：日期型，当天全天可提


def test_给了时刻就如实入库且不再被判成日期型():
    got = _intent("2026-10-09 08:00~2026-10-09 08:00")
    assert got["due_start"] == datetime(2026, 10, 9, 8, 0)
    assert got["due_end"] == datetime(2026, 10, 9, 8, 0)
    # 这条就是 id=160 的正解：不再等于 23:59 ⇒ 不会被"当天全天"那一档捞走
    assert _is_date_scoped(got["due_end"]) is False
    assert got["due_end"].hour == 8


def test_T分隔与带秒的写法也收():
    assert ex._parse_due("2026-10-09T08:00", end=True) == datetime(2026, 10, 9, 8, 0)
    assert ex._parse_due("2026-10-09 08:00:30", end=False) == datetime(2026, 10, 9, 8, 0, 30)


def test_坏格式仍按旧行为整对置空():
    got = _intent("明天早上~后天")
    assert got["due_start"] is None and got["due_end"] is None, "解析失败必须退回「无 due」的旧口径"


# ───────────────────────── 二、提示词侧：关着不动，开着只换一行

def test_关闸时prompt逐字节不变():
    base = "前\n" + ex._intent_time_window_spec(False) + "\n后"
    assert ex._apply_clock_precise(base, flags={}) == base
    assert ex._apply_clock_precise(base, flags={"proactive_clock_precise": False}) == base


def test_开闸时只换INTENT那一行():
    base = "前\n" + ex._intent_time_window_spec(False) + "\n后"
    out = ex._apply_clock_precise(base, flags={"proactive_clock_precise": True})
    assert PRECISE.split("时间窗")[0] in out or "HH:MM" in out
    lines = out.split("\n")
    assert lines[0] == "前" and lines[2] == "后", "不该动到别的行：%r" % out
    assert ex._intent_time_window_spec(False) not in out


def test_反向钉_规格行必须真在提示词里():
    """否则"换一行"是空操作，闸开到最后什么也没发生（静默失效最难查）。"""
    spec = ex._intent_time_window_spec(False)
    assert spec in ex.EXTRACT_PROMPT, "规格与 EXTRACT_PROMPT 失配 ⇒ 本闸形同虚设"
    assert ex._intent_time_window_spec(True) != ex._intent_time_window_spec(False)


def test_取闸失败按关处理不影响提取():
    base = ex._intent_time_window_spec(False)

    class _Boom(dict):
        def get(self, *a, **kw):
            raise RuntimeError("flag 层挂了")

    assert ex._apply_clock_precise(base, flags=_Boom()) == base


def test_默认注册表里这把闸是关的():
    from app.flags.agent_flags import AGENT_FLAGS

    assert "proactive_clock_precise" in AGENT_FLAGS
    assert AGENT_FLAGS["proactive_clock_precise"] is False, "新开闸一律默认关（A37 批 2 边界）"
