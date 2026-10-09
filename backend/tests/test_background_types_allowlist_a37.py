# -*- coding: utf-8 -*-
"""A37 批 3 守卫（审计 §4 批 3 · 守卫①）：后台型豁免名单**只许是那三个键**，且豁免只到速率为止。

钉四件事：

① ``BACKGROUND_TYPES`` 逐字节等于钉死的三元组——要加第四个键，必须先改本测试并写清理由，
   不许"顺手"把新通道塞进豁免名单（审计 I11 的现网病灶就是豁免范围一路自己长大）；
② 名单与 ``@handler`` 分组同源：三个键都有执行器，不存在"豁免了闸却没人执行"的悬空键；
③ 豁免范围＝速率/活跃闸（``pre_gates`` 对后台键返回 ``None`` 放行），不许反过来把后台型也收紧；
④ 反向钉：后台型**不得豁免「新事件冲突」**（不变量 I11）。豁免的是"这条通道不占私信额度"，
   不是"它可以拿旧现状照发"。所以两个落库型后台通道（group_active / ai_social）自己必须带
   落库前复检，且复检调用**排在写库之前**（源码顺序钉）。

名单只有一处定义（registry）也被钉住：再出现第二份副本，①②③④ 都会失去意义。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app.scheduling.executors import pre_gates
from app.scheduling.executors.registry import BACKGROUND_TYPES, HANDLERS

APP = Path(__file__).resolve().parents[1] / "app"

# ① 钉死的三元组（顺序也钉：名单是"分组依据"，重排会让读代码的人以为有优先级）
PINNED = ("ai_social", "group_active", "pet_visit")


def _item(etype: str) -> dict:
    return {"type": etype, "candidate": {"character_id": 71, "user_id": 72}}


def _bundle(hits: str = "", calls: list | None = None):
    """按字段名造 GateBundle：``hits`` 里列出的闸返回"拦"，其余放行，并记录被调顺序。"""
    calls = calls if calls is not None else []

    def _async(name, ret):
        async def _f(*_a, **_k):
            calls.append(name)
            return ret
        return _f

    def _sync(name):
        def _f(*_a, **_k):
            calls.append(name)
        return _f

    def _truth(name, ret):
        async def _f(*_a, **_k):
            calls.append(name)
            return ret if name in hits.split(",") else None
        return _f

    from app.scheduling.executors.context import GateBundle
    return GateBundle(
        is_dnd_now=_truth("is_dnd_now", "dnd"),
        has_user_said_sleep=_async("has_user_said_sleep", False),
        is_user_active=_truth("is_user_active", True),
        hourly_active=_async("hourly_active", 0 if "hourly" not in hits else 999),
        pacing_gate=_truth("pacing_gate", "pacing"),
        mark_gate=_sync("mark_gate"),
        session_factory=lambda: None,
        app_day_start=lambda: None,
    )


def test_名单就是那三个键():
    assert tuple(BACKGROUND_TYPES) == PINNED, (
        "BACKGROUND_TYPES 变了：豁免范围只能显式改这里，并在审计 I11 下写清理由；"
        "新增键必须同时补『该通道自己判新事件冲突』的落库前复检")
    assert isinstance(BACKGROUND_TYPES, tuple), "名单必须是不可变元组（防止运行期被 append）"
    assert len(set(BACKGROUND_TYPES)) == len(BACKGROUND_TYPES), "名单里有重复键"


def test_名单只有一处定义():
    """第二份副本＝第一份钉住的东西不再成立（registry 改了就没人再红）。"""
    owners = []
    for p in APP.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        if re.search(r"^BACKGROUND_TYPES\s*=", src, re.M):
            owners.append(str(p.relative_to(APP.parent)))
    norm = [o.replace("\\", "/") for o in owners]
    assert norm == ["app/scheduling/executors/registry.py"], f"BACKGROUND_TYPES 出现第二份定义：{norm}"


@pytest.mark.parametrize("etype", PINNED)
def test_后台键都有执行器(etype):
    assert etype in HANDLERS, f"{etype} 在豁免名单里却没有执行器（＝悬空豁免）"


def test_后台键豁免速率闸_非后台键不豁免():
    """③ 豁免确实只作用在"速率/活跃"这几道上：同样打满额度，后台键放行、普通键拦下。"""
    assert asyncio.run(pre_gates(_item("ai_social"), "ai_social", _bundle("is_user_active,hourly_active"))) is None
    assert asyncio.run(pre_gates(_item("group_active"), "group_active",
                                 _bundle("is_user_active,hourly_active"))) is None
    assert asyncio.run(pre_gates(_item("pet_visit"), "pet_visit",
                                 _bundle("is_user_active,hourly_active"))) is None
    # 对照：同一个"额度已满 + 用户正在聊"的现状，非后台键必须被拦
    assert asyncio.run(pre_gates(_item("greeting"), "greeting",
                                 _bundle("is_user_active"))) is False
    assert asyncio.run(pre_gates(_item("life_regression"), "life_regression",
                                 _bundle("hourly_active"))) is False


def test_豁免名单不得豁免新事件冲突():
    """④ 反向钉（I11）：两个落库型后台通道**自己**必须带落库前复检，且排在写库之前。

    这里只判"存在且顺序对"，行为细节归 ``test_gates_batch3_a40.py``。
    """
    for rel in ("scheduling/group_active.py", "scheduling/ai_social.py"):
        src = (APP / rel).read_text(encoding="utf-8")
        assert "async def _pre_land_conflict" in src, f"{rel} 没有落库前复检＝后台型把新事件冲突一起豁免了"
        i_check = src.index("await _pre_land_conflict(")
        i_write = src.index("db.add_all(") if "db.add_all(" in src else src.index("db.add(ChatGroupMessage(")
        assert i_check < i_write, f"{rel} 复检排在写库之后＝已经落了库再说不发"


def test_后台键仍不得豁免免打扰():
    """豁免只豁免速率，不豁免 DND/睡眠（这条是现状复核，防止后来者把整段 pre_gates 跳过）。"""
    calls: list[str] = []
    assert asyncio.run(pre_gates(_item("ai_social"), "ai_social", _bundle("is_dnd_now", calls))) is False
    assert "is_dnd_now" in calls, "后台键连免打扰都不判了＝豁免范围被悄悄扩大"
