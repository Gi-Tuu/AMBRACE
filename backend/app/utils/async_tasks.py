"""后台协程统一调度：持强引用防 GC，异常落日志，可选优雅收尾。"""
import asyncio

from app.utils.logger import get_logger

_logger = get_logger("async_tasks")
_BG_TASKS: set[asyncio.Task] = set()


def spawn_background(coro, *, name: str | None = None) -> asyncio.Task:
    """调度一个「发射后不管」的后台协程。

    - 用全局集合持有强引用，直到任务结束才丢弃，避免被 GC 静默回收；
    - 任务内未捕获的异常在这里统一记日志（业务协程仍应自行 try/except）。
    """
    task = asyncio.ensure_future(coro)
    if name:
        try:
            task.set_name(name)
        except Exception:
            pass
    _BG_TASKS.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _BG_TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            _logger.warning("background task %s failed: %s", t.get_name(), exc)

    task.add_done_callback(_on_done)
    return task


async def await_all(timeout: float | None = None) -> None:
    """关停前等待在途后台任务（lifespan shutdown 可选调用）。"""
    if _BG_TASKS:
        await asyncio.wait(list(_BG_TASKS), timeout=timeout)
