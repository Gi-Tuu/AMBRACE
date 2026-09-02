# -*- coding: utf-8 -*-
"""AMBRACE 3.10 —— arbiter 事件源 TriggerSource 化：sources registry / base 单测。

作为 arbiter 事件源重构的「结构等价」锚点之一：
- all_sources() 注册顺序 = run_tick 合并候选顺序（逐项锁定，勿改序）；
- TriggerItem 与 arbiter 候选 dict 逐字节往返；
- 包装型源（原模块采集函数）经 TriggerSource 适配后输出与原模块一致；
- run_tick 统一遍历 all_sources() 时，单源失败不拖垮仲裁（try/except）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio

import pytest

from app.scheduling import arbiter
from app.scheduling.sources import (
    SourceContext, TriggerItem, TriggerSource, all_sources, get_source, register_source,
    set_source, unregister_source, to_item_dict,
)

# run_tick 合并候选的注册顺序（勿改序；与 arbiter.run_tick 逐项对应）
EXPECTED_SOURCE_ORDER = [
    "timer", "special", "rhythm", "state_trigger", "motivation",
    "memory_review", "memory_review_contextual", "emotion_care",
    "pet_remind", "ai_care", "ai_adopt", "pet_visit",
    "ai_social", "group_active", "plugin", "unfinished_topic", "life_regression",
]


# ---------------- registry ----------------

def test_all_sources_order_matches_run_tick_merge_order():
    """all_sources() 必须按 run_tick 合并候选的顺序返回（结构等价的核心约束）。"""
    assert [s.name for s in all_sources()] == EXPECTED_SOURCE_ORDER
    assert len(all_sources()) == len(EXPECTED_SOURCE_ORDER)


def test_get_source_by_name():
    for name in EXPECTED_SOURCE_ORDER:
        assert get_source(name).name == name


def test_register_source_decorator_registers_instance():
    @register_source(name="unit_test_src")
    class _Src:
        name = "unit_test_src"

        async def collect(self, ctx):
            return []

        def quota(self, ctx):
            return 1

    assert get_source("unit_test_src") is not None
    assert get_source("unit_test_src").name == "unit_test_src"
    # 清理（只移除本用例注入的源，避免污染其它用例对真实注册表的依赖）
    unregister_source("unit_test_src")


def test_register_source_missing_name_raises():
    with pytest.raises(ValueError):
        @register_source
        class _Bad:
            async def collect(self, ctx):
                return []

            def quota(self, ctx):
                return 1


def test_set_source_inject_and_get():
    class _Fake:
        name = "injected_src"

        async def collect(self, ctx):
            return [TriggerItem(type="fake", priority=1, candidate={"a": 1})]

        def quota(self, ctx):
            return 5

    set_source("injected_src", _Fake())
    assert get_source("injected_src").name == "injected_src"
    set_source("injected_src", get_source("timer"))  # 还原/替换


# ---------------- TriggerItem ----------------

def test_trigger_item_roundtrip_candidate():
    ti = TriggerItem(type="proactive_chat", priority=1, candidate={"character_id": 1, "user_id": 2})
    d = ti.to_dict()
    assert d == {"type": "proactive_chat", "priority": 1, "candidate": {"character_id": 1, "user_id": 2}}
    assert TriggerItem.from_dict(d).to_dict() == d


def test_trigger_item_roundtrip_event():
    # timer 源：候选用 event（ORM 对象），candidate 缺失
    event = object()
    ti = TriggerItem(type="timer", priority=4, event=event)
    d = ti.to_dict()
    assert d == {"type": "timer", "priority": 4, "event": event}
    assert TriggerItem.from_dict(d).to_dict() is not None


def test_trigger_item_motivation_present_only_when_set():
    # 无 motivation 时 to_dict 不应带 motivation 键（与旧采集 dict 一致）
    ti = TriggerItem(type="motivation", priority=1, candidate={"character_id": 1}, motivation=0.8)
    assert ti.to_dict()["motivation"] == 0.8
    ti2 = TriggerItem(type="x", priority=1, candidate={"a": 1})
    assert "motivation" not in ti2.to_dict()


def test_to_item_dict_accepts_native_dict():
    assert to_item_dict({"type": "x", "priority": 1, "candidate": {}}) == {"type": "x", "priority": 1, "candidate": {}}


# ---------------- Protocol ----------------

def test_sources_satisfy_protocol():
    for src in all_sources():
        assert isinstance(src, TriggerSource)
        assert src.name
        assert callable(src.collect) and callable(src.quota)
        assert isinstance(src.quota(SourceContext()), int)


# ---------------- 包装型源：适配层透明（输出与原采集函数一致） ----------------

def test_wrapped_source_adapter_matches_module_collect(monkeypatch):
    """memory_review 源：collect() 输出 == 原 collect_review_events()（逐字节等价）。"""
    import app.scheduling.memory_review as review_mod

    canned = [
        {"type": "memory_review", "priority": 1, "candidate": {"character_id": 1, "user_id": 2, "memory_id": 9}},
    ]

    async def _fake_collect():
        return canned

    monkeypatch.setattr(review_mod, "collect_review_events", _fake_collect)

    src = get_source("memory_review")
    out = [ti.to_dict() for ti in asyncio.run(src.collect(SourceContext()))]
    assert out == canned


def test_wrapped_source_adapter_pet_matches_module(monkeypatch):
    """pet_remind 源：collect() 输出 == 原 collect_pet_events()（逐字节等价）。"""
    import app.scheduling.pet_care as pet_mod

    canned = [
        {"type": "pet_remind", "priority": 1, "candidate": {"character_id": 1, "user_id": 2, "pet_id": 3}},
    ]

    async def _fake_collect():
        return canned

    monkeypatch.setattr(pet_mod, "collect_pet_events", _fake_collect)

    src = get_source("pet_remind")
    out = [ti.to_dict() for ti in asyncio.run(src.collect(SourceContext()))]
    assert out == canned


# ---------------- run_tick：单源失败不拖垮仲裁 ----------------

def test_run_tick_single_source_failure_does_not_crash(monkeypatch):
    """run_tick 统一遍历 all_sources()：某源 collect 抛异常 → 跳过并继续其它源。"""

    async def _fake_decay():
        return None

    class _BoomSource:
        name = "boom"

        async def collect(self, ctx):
            raise RuntimeError("boom")

        def quota(self, ctx):
            return 1

    class _GoodSource:
        name = "good"

        async def collect(self, ctx):
            return [TriggerItem(type="proactive_chat", priority=1, candidate={"character_id": 1, "user_id": 1})]

        def quota(self, ctx):
            return 1

    async def _motivation(cid):
        return 0.0

    async def _execute(item):
        return True

    async def _log(item, ok):
        return None

    async def _trace(item, ok, ms):
        return None

    monkeypatch.setattr("app.domain.relationship.decay.run_relationship_decay", _fake_decay)
    monkeypatch.setattr(arbiter, "all_sources", lambda: [_BoomSource(), _GoodSource()])
    monkeypatch.setattr(arbiter, "_compute_motivation", _motivation)
    monkeypatch.setattr(arbiter, "_execute", _execute)
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _log)
    monkeypatch.setattr(arbiter, "_trace_scheduler_task", _trace)

    executed = asyncio.run(arbiter.run_tick())
    # boom 源失败被跳过；good 源候选执行成功
    assert executed == ["proactive_chat(char=1)"]
