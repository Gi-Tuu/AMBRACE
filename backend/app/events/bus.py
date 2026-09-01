"""轻量进程内事件总线（async pub/sub，异常隔离）

- publish：异步广播（内部 ensure_future，不阻塞调用方；与现有异步 fire-and-forget 模式一致）
- 单个订阅者异常不影响其他订阅者与发布方
"""
from app.utils.async_tasks import spawn_background
import logging
from typing import Awaitable, Callable

_logger = logging.getLogger("events.bus")

EventHandler = Callable[[dict], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)
        _logger.info("Event subscribed: %s (%s)", event_type, getattr(handler, "__name__", handler))

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, payload: dict | None = None) -> None:
        handlers = list(self._subscribers.get(event_type, []))
        if not handlers:
            return
        for h in handlers:
            try:
                await h(payload or {})
            except Exception as e:
                _logger.warning("Event %s handler %s failed: %s", event_type, getattr(h, "__name__", h), e)

    def publish_async(self, event_type: str, payload: dict | None = None) -> None:
        spawn_background(self.publish(event_type, payload))


# 全局单例
event_bus = EventBus()


def publish(event_type: str, payload: dict | None = None) -> None:
    """便捷发布：异步广播，不阻塞调用方；无事件循环时静默降级（防御）"""
    try:
        spawn_background(event_bus.publish(event_type, payload))
    except RuntimeError:
        _logger.warning("Event publish skipped (no event loop): %s", event_type)


def subscribe(event_type: str, handler: EventHandler) -> None:
    event_bus.subscribe(event_type, handler)
