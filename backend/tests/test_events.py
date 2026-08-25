"""Event Bus 单测（演进规划 v2 Phase A，2026-08-14）：
publish/subscribe 广播、异常隔离、事件类型、内置订阅者注册幂等。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行）
"""
import asyncio

from app.events.bus import EventBus
from app.events.handlers import register_builtin_handlers
from app.events.types import EventType


def test_publish_broadcasts_to_subscribers():
    async def run():
        bus = EventBus()
        got = []

        async def h1(payload):
            got.append(("h1", payload.get("n")))

        async def h2(payload):
            got.append(("h2", payload.get("n")))

        bus.subscribe("test.evt", h1)
        bus.subscribe("test.evt", h2)
        await bus.publish("test.evt", {"n": 42})
        return got

    assert asyncio.run(run()) == [("h1", 42), ("h2", 42)]


def test_handler_exception_is_isolated():
    async def run():
        bus = EventBus()
        got = []

        async def bad(payload):
            raise RuntimeError("boom")

        async def good(payload):
            got.append(payload.get("n"))

        bus.subscribe("test.err", bad)
        bus.subscribe("test.err", good)
        await bus.publish("test.err", {"n": 1})
        return got

    assert asyncio.run(run()) == [1]  # bad 异常不影响 good


def test_no_subscriber_is_noop():
    async def run():
        await EventBus().publish("test.nobody", {"n": 1})

    asyncio.run(run())  # 不应抛


def test_event_types_are_strings():
    assert EventType.MEMORY_WRITTEN == "memory.written"
    assert EventType.LIFE_ACTIVITY_COMPLETED == "life.activity_completed"
    assert EventType.LIFE_MOMENT_PUBLISHED == "life.moment_published"


def test_register_builtin_handlers_idempotent():
    from app.events.bus import event_bus as eb

    def counts():
        return {
            et: len(eb._subscribers.get(et, [])) for et in ("memory.written", "life.activity_completed")
        }

    register_builtin_handlers()
    after1 = counts()
    register_builtin_handlers()
    after2 = counts()
    assert after1 == {"memory.written": 1, "life.activity_completed": 1}
    assert after2 == after1  # 幂等：重复注册不累积
