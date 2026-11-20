import asyncio

from app.events.bus import EventBus


def test_event_bus_records_handler_failures():
    bus = EventBus()

    async def _boom(_payload):
        raise RuntimeError("synthetic")

    bus.subscribe("test.event", _boom)
    asyncio.run(bus.publish("test.event", {"x": 1}))
    assert bus.failure_snapshot() == {"test.event": 1}
