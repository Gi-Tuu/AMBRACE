"""Desired C9b contracts; failures include capability gaps and aggregation-policy conflicts."""
import asyncio

from _plan_completion_fixture_c9b import c9b_world as c9b_world
from datetime import timedelta

import pytest

from _plan_completion_fixture_c9b import ARRIVAL_PLAN


def test_other_character_is_not_settled(c9b_world):
    async def case():
        w = c9b_world
        own = await w.plan()
        other = await w.plan("用户到地方后校对设备B标签", character_id=202, session_id=303)
        assert await w.signal() == [own]
        assert (await w.state(other))["status"] == "pending"
    asyncio.run(case())


def test_shared_character_other_user_is_not_settled(c9b_world):
    async def case():
        w = c9b_world
        own = await w.plan()
        other = await w.plan("用户到地方后校对设备B标签", user_id=102, session_id=305,
                             due=w.due + timedelta(days=1))
        assert own != other
        await w.signal()
        assert (await w.state(other))["status"] == "pending", "Character sharing is not shared task completion"
    asyncio.run(case())


def test_unbound_other_session_future_task_is_not_settled(c9b_world):
    async def case():
        w = c9b_world
        own = await w.plan()
        other = await w.plan("用户明天到地方后校对设备E标签", session_id=302,
                             due=w.due + timedelta(days=1))
        assert own != other
        await w.signal()
        assert (await w.state(other))["status"] == "pending", "Without target evidence a separate future task stays open"
    asyncio.run(case())


def test_identical_text_other_user_keeps_its_own_identity(c9b_world):
    async def case():
        w = c9b_world
        own = await w.plan()
        other = await w.plan(ARRIVAL_PLAN, user_id=102, session_id=305)
        assert own != other, "Equal text and a shared character must not merge different users' tasks"
        assert (await w.state(other))["user_id"] == 102
    asyncio.run(case())


def test_distinct_source_sessions_are_not_implicitly_same_event(c9b_world):
    async def case():
        w = c9b_world
        first = await w.plan("用户到地方后记录设备A序列号")
        second = await w.plan("用户到地方后复核设备F的合格证", session_id=302)
        assert first != second, "A common arrival trigger does not prove two independent tasks are one event"
    asyncio.run(case())


@pytest.mark.parametrize("text", [
    "如果我到了，才会启动检测流程", "我还没到门口，先记录设备K外壳编号",
    "文档里的示例短句写着：我到了", "测试员乙发来消息：“我到了质检台。”我还在仓库，尚未出发。",
])
def test_non_asserted_or_other_actor_signal_does_not_settle(c9b_world, text):
    async def case():
        w = c9b_world
        ident = await w.plan()
        assert await w.signal(text) == [], "Hypothetical, negated, quoted or third-person evidence is insufficient"
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_partial_completion_preserves_unfinished_obligation(c9b_world):
    async def case():
        w = c9b_world
        ident = await w.plan("用户到地方后记录设备A铭牌；还需要完成设备H的绝缘校验")
        await w.signal()
        assert (await w.state(ident))["status"] == "pending", "One component event must not discharge a compound obligation"
    asyncio.run(case())


def test_unrelated_clock_task_is_not_closed_by_arrival(c9b_world):
    async def case():
        w = c9b_world
        ident = await w.plan("用户计划校对文档J第三页")
        assert await w.signal() == []
        assert (await w.state(ident))["status"] == "pending"
        assert ident in await w.candidates()
    asyncio.run(case())


def test_ambiguous_signal_without_bound_target_keeps_plan(c9b_world):
    async def case():
        w = c9b_world
        ident = await w.plan()
        assert await w.signal("处理好了") == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_independent_family_task_is_not_settled(c9b_world):
    async def case():
        w = c9b_world
        own = await w.plan()
        outsider = await w.plan("用户到达校准台后扫描设备Z", user_id=103, character_id=203, session_id=304)
        assert own != outsider
        await w.signal()
        assert (await w.state(outsider))["status"] == "pending"
    asyncio.run(case())
