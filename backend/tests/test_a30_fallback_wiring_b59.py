# -*- coding: utf-8 -*-
"""批 C 守卫：兜底接线（`chat/tools.py`）＋两把默认关的闸。

要咬的是**行为**不是形状（第 ⑧ 类）：影子档必须一个字都不写库、实落档必须用用户原话、
模型已经落了标记就绝不双写、周期约定没有那个日期就不落库、兜底自己炸了也不能拖主链路。
零 LLM、零计费、零生产库（全部走 pytest 沙箱）。
"""
import asyncio
from pathlib import Path

import pytest

from app.agent import loop as _loop
from app.application.chat import tools as T

SHADOW, GATE = "memory_fallback_shadow", "memory_fallback_gate"
USER = "下个月28号提醒我对一下账"
CHAT_SERVICE = Path(T.__file__).resolve().parents[1] / "chat_service.py"


class _Spy:
    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    async def __call__(self, tool_name, payload, character_id):
        self.calls.append((tool_name, dict(payload or {})))
        if self.boom:
            raise AssertionError("影子档/不该写的情况下写了库")


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, SHADOW, False)
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, False)
    monkeypatch.setattr(T._logger, "info", lambda *a, **k: None)
    yield


def _run(user_text=USER, cal=False, memo=False, character_id=7):
    return asyncio.run(T._maybe_fallback_notes(character_id, user_text, cal, memo, "小拥"))


# ── 两档闸的边界 ──
def test_两把闸都关时整条链路早退且一次调用都不发生(monkeypatch):
    spy = _Spy(boom=True)
    monkeypatch.setattr(T, "_execute_note_tool", spy)
    logs = []
    monkeypatch.setattr(T._logger, "info", lambda *a, **k: logs.append(a))
    assert _run() == "off"
    assert spy.calls == [] and logs == []


def test_影子档只打留痕一个字都不写库(monkeypatch):
    spy = _Spy(boom=True)
    logs = []
    monkeypatch.setattr(T, "_execute_note_tool", spy)
    monkeypatch.setattr(T._logger, "info", lambda *a, **k: logs.append(a[0] % a[1:]))
    monkeypatch.setitem(_loop.AGENT_FLAGS, SHADOW, True)
    assert _run() == "shadow"
    assert spy.calls == []                       # ← 反向钉：影子顺手写库就红
    assert len(logs) == 1 and "route=memory_fallback" in logs[0]
    assert USER in logs[0]                       # 留痕里看得见"本来会补什么"


def test_实落档才写库且正文取用户原话(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(T, "_execute_note_tool", spy)
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, True)
    assert _run() == "written"
    name, payload = spy.calls[0]
    assert name == "note_calendar"
    assert payload["text"] and payload["text"] in USER        # 不造句
    assert payload["date"] == "2026-11-28"                    # 跟锚点走，不跟本机日历瞎猜
    assert payload["author"] == "小拥"


def test_模型已经落了标记就不再补第二条(monkeypatch):
    spy = _Spy(boom=True)
    monkeypatch.setattr(T, "_execute_note_tool", spy)
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, True)
    assert _run(cal=True) == "skipped"
    assert spy.calls == []                       # 双写＝同一件事在日历里出现两遍


def test_周期约定没有那个日期于是不落库(monkeypatch):
    spy = _Spy(boom=True)
    monkeypatch.setattr(T, "_execute_note_tool", spy)
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, True)
    assert _run(user_text="以后每到画室带课那天，别约我下午的事") == "skipped"
    assert spy.calls == []


def test_本轮没有下令时什么都不补(monkeypatch):
    spy = _Spy(boom=True)
    monkeypatch.setattr(T, "_execute_note_tool", spy)
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, True)
    assert _run(user_text="今天天气不错") == "skipped"
    assert spy.calls == []


def test_闸字典缺键时按关处理(monkeypatch):
    monkeypatch.setattr(T, "_fallback_flags", lambda: (False, False))
    assert _run() == "off"


def test_兜底内部异常不冒到主链路(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("兜底自己坏了")

    warns = []
    monkeypatch.setattr(T, "_maybe_fallback_notes", boom)
    monkeypatch.setattr(T._logger, "warning", lambda *a, **k: warns.append(str(a)))
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, True)
    asyncio.run(T._save_phone_desktop_notes(7, "好呀", USER))
    assert any("memory fallback skipped" in w for w in warns), warns


# ── 接线本身：拿不到本轮用户原话＝兜底在生产里是台空转的机器 ──
def test_主链路把本轮用户原话传进了落库口():
    src = CHAT_SERVICE.read_text(encoding="utf-8-sig")
    assert "_save_phone_desktop_notes(character_id, _marker_src2, content)" in src, \
        "调用点没带本轮用户原话 ⇒ 兜底在生产里永远拿不到原料"


def test_两把新闸都已登记且默认关():
    from app.application.flag_catalog import FLAG_CATALOG, meta_for
    assert len(FLAG_CATALOG) == len(_loop.AGENT_FLAGS), "目录与字典数量不一致"
    for k in (SHADOW, GATE):
        assert k in FLAG_CATALOG, "%s 没进目录＝清单里看不到" % k
        assert meta_for(k)["group"] == "memory"
        assert _loop.AGENT_FLAGS.get(k) is False, "%s 默认必须是关" % k
