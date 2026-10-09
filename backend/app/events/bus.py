"""轻量进程内事件总线（async pub/sub，异常隔离 + A41 投递侧「过期即丢」）

- publish：异步广播（内部 ensure_future，不阻塞调用方；与现有异步 fire-and-forget 模式一致）
- 单个订阅者异常不影响其他订阅者与发布方
- A41（审计 C43）：广播任务可能在队列里排队、且各订阅者**同任务内顺序 await**，一个慢订阅者会把
  后面的都推后 ⇒ 「什么时候轮到什么时候发」。投递前按发出时刻判一次新鲜度，到点不再投递。
"""
from app.utils.async_tasks import spawn_background
import logging
import time
from typing import Awaitable, Callable

_logger = logging.getLogger("events.bus")

EventHandler = Callable[[dict], Awaitable[None]]

# A41：默认新鲜窗沿用 events/facts.py 的瞬时状态口径（STATUS_FRESH_HOURS），本文件不新造第二个
# 新鲜度数值——「同一谓词只有一份 TTL 出处」是断点 #9 定的规矩，总线同样遵守。
# payload 的发出时刻优先取 schema.make_event 写好的 `timestamp`（＝事件构造那一瞬间，比 publish
# 更早、更严格）；缺时刻的裸 dict 事件在 publish 入口补 `emitted_at`（＝「刚发出」，不判过期），
# 这样"没有时刻"不会被当成"很旧"——丢一条事件＝订阅侧永久丢一次落库，方向必须偏保守到"发"。
_EMIT_KEYS = ("timestamp", "emitted_at", "snapshot_at")


def _default_ttl_sec() -> float:
    from app.events import facts as _facts
    return float(_facts.STATUS_FRESH_HOURS * 3600)


def _emitted_at(payload: dict) -> float | None:
    """payload 的发出时刻（epoch 秒）；取不到返回 None（不猜）。"""
    for key in _EMIT_KEYS:
        val = payload.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    return None


def _stamp_emit(payload: dict) -> dict:
    """没有发出时刻时补一个（补在副本上，不改调用方传进来的 dict）。"""
    if _emitted_at(payload) is None:
        payload = {**payload, "emitted_at": time.time()}
    return payload


def _expired(emitted: float | None, ttl_sec: float) -> bool:
    return emitted is not None and (time.time() - emitted) > ttl_sec


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

    async def publish(self, event_type: str, payload: dict | None = None,
                      *, ttl_sec: float | None = None) -> None:
        handlers = list(self._subscribers.get(event_type, []))
        if not handlers:
            return
        msg = _stamp_emit(dict(payload or {}))
        ttl = _default_ttl_sec() if ttl_sec is None else float(ttl_sec)
        emitted = _emitted_at(msg)
        for h in handlers:
            # 逐个订阅者投递前重判：前面的 handler 阻塞多久都不该把过期事件继续往下发
            if _expired(emitted, ttl):
                _logger.info("Event %s dropped: 迟于 ttl（发出后 %.0fs > %.0fs）(A41)",
                             event_type, time.time() - (emitted or 0.0), ttl)
                return
            try:
                await h(msg)
            except Exception as e:
                _logger.warning("Event %s handler %s failed: %s", event_type, getattr(h, "__name__", h), e)

    def publish_async(self, event_type: str, payload: dict | None = None,
                      *, ttl_sec: float | None = None) -> None:
        spawn_background(self.publish(event_type, payload, ttl_sec=ttl_sec))


# 全局单例
event_bus = EventBus()


def publish(event_type: str, payload: dict | None = None, *, ttl_sec: float | None = None) -> None:
    """便捷发布：异步广播，不阻塞调用方；无事件循环时静默降级（防御）"""
    try:
        spawn_background(event_bus.publish(event_type, payload, ttl_sec=ttl_sec))
    except RuntimeError:
        _logger.warning("Event publish skipped (no event loop): %s", event_type)


def subscribe(event_type: str, handler: EventHandler) -> None:
    event_bus.subscribe(event_type, handler)
