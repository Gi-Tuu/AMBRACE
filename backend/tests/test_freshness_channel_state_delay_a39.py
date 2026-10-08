# -*- coding: utf-8 -*-
"""A39 批 2b 通道 3（state_trigger_delayed）守卫：延迟睡醒后**正文必须用刚复查到的状态**。

缺陷形态：命中当场把 `state_lines` 烘成快照字符串，一路带到几分钟后 `_execute_rule_behavior` 拼 prompt；
睡醒后虽然重读了 `CharacterState`（用来判「规则是否还命中」），但说出口的内容仍是旧那份 ⇒
「我知道我现在更生气」和「我说的话」两者不一致。

四条判据：
  ① 构造点唯一（当场与睡醒后必须同一个算法，否则格式会分叉）；
  ② 睡醒后状态变了 ⇒ 传给执行函数的是**新鲜串**；
  ③ 睡醒后状态没变 ⇒ 逐字节保持原串（不许为了"看起来对"而重排格式）；
  ④ 变化要留痕（判效就数这条 INFO），且留痕不许带用户正文。
"""
import asyncio
import logging
import re
from pathlib import Path

import pytest

from app.scheduling import state_triggers as st_mod

SRC = Path(st_mod.__file__).read_text(encoding="utf-8")


class _Res:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _SeqDb:
    """按调用顺序发回预设行：第一次 select=CharacterState，第二次=ProactiveSettings。"""

    def __init__(self, rows):
        self.rows = list(rows)

    async def execute(self, *a, **k):
        return _Res(self.rows.pop(0) if self.rows else None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _St:
    def __init__(self, **kw):
        base = {k: 50 for k in st_mod._DIM_KEYS}
        base.update(kw)
        self.__dict__.update(base)


def _lines(**kw):
    return st_mod._lines_of(_St(**kw))


def test_构造点唯一():
    """八维串的拼装只许存在于 `_lines_of` 里（内容级判据，不看注释）。"""
    assert SRC.count('f"{_CN[d]}=') == 1, "出现了第二份内联拼装 ⇒ 睡醒后与当场两种写法迟早分叉"
    assert re.search(r"def _lines_of\(st\)", SRC), "唯一构造点没了"
    assert len(re.findall(r"=\s*_lines_of\(st\)", SRC)) == 2, "当场与睡醒后应各有一处调用"
    assert re.search(r"fresh_lines = _lines_of\(st\)", SRC), "睡醒后那次重取没接上唯一构造点"


def _run_delayed(monkeypatch, fresh_state, snapshot_lines):
    called = {}

    async def _exec(character_id, user_id, rule, state_lines, delay_minutes=None):
        called["lines"] = state_lines
        called["delay"] = delay_minutes
        return True

    async def _drop(*a, **k):
        called.setdefault("dropped", True)

    monkeypatch.setattr(st_mod, "_rule_hit", lambda rule, st: True)
    rule = st_mod.Rule(key="a39_probe", desc="", priority=1, cooldown_minutes=0,
                      probability=1.0, moment=False, delay=None, conditions=[])
    monkeypatch.setattr(st_mod, "_execute_rule_behavior", _exec)
    monkeypatch.setattr(st_mod, "_drop_trigger_log", _drop)
    monkeypatch.setattr(st_mod, "async_session_factory",
                        lambda *a, **k: _SeqDb([fresh_state, object()]))
    asyncio.run(st_mod._delayed_rule_behavior(7001, 7002, rule, 0.0005, snapshot_lines))
    return called


def test_睡醒后状态变了正文就用新鲜的(monkeypatch):
    snap = _lines(anger=50, mood=60)
    fresh = _St(anger=88, mood=60)
    called = _run_delayed(monkeypatch, fresh, snap)
    assert "lines" in called, "执行函数根本没被调用＝这条断言是空的"
    assert called["lines"] == _lines_of_fresh(fresh), "传进去的还是几分钟前的快照"
    assert called["lines"] != snap


def _lines_of_fresh(st):
    return st_mod._lines_of(st)


def test_状态没变就逐字节不动正文也不许留痕(monkeypatch, caplog):
    """反向钉：新鲜与快照等价时不许"顺手重写"，也不许打 refreshed 留痕。

    不留这条钉，判效读数会变成"每次都算重取过"——那把尺子就恒真、永远没有牙。
    """
    snap = _lines(anger=50, mood=60)
    with caplog.at_level(logging.INFO, logger="scheduler.state_triggers"):
        called = _run_delayed(monkeypatch, _St(anger=50, mood=60), snap)
    assert called.get("lines") == snap
    assert not [r for r in caplog.records if "state refreshed" in r.getMessage()], \
        "没变也留痕＝那条 INFO 以后数不出真变化"


def test_变化必须留痕且不含正文(monkeypatch, caplog):
    snap = _lines(anger=50)
    with caplog.at_level(logging.INFO, logger="scheduler.state_triggers"):
        _run_delayed(monkeypatch, _St(anger=91), snap)
    hit = [r for r in caplog.records if "state refreshed" in r.getMessage()]
    assert len(hit) == 1, "变化没留痕＝这条读数永远数不出来"
    assert "Delayed trigger state refreshed" in hit[0].getMessage()
