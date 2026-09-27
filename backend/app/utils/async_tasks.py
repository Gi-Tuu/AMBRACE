"""后台协程统一调度：持强引用防 GC，异常落日志，可选优雅收尾。"""
import asyncio

from app.utils.llm_channel import CHANNEL_SERVER, reset_channel, set_channel
from app.utils.logger import get_logger

_logger = get_logger("async_tasks")
_BG_TASKS: set[asyncio.Task] = set()


async def _detached(coro):
    """派生边界：子任务先把「LLM 用量渠道」切成 server，再执行业务协程。

    asyncio Task 创建时复制当前 contextvars 快照 ⇒ 请求内 spawn 的后台协程会**继承本轮渠道**；
    而请求内的 17 处 spawn 至少 5 处下游真打 LLM（自述/记忆提取/工作记忆/八维状态/情绪关怀），
    不切断就会把后台用量误归到 app/wechat_ilink（勘察 §0.5 + §3 防护 2）。
    子 Task 拥有独立 context 副本，这里 set 不回灌父请求，父上下文零污染。
    """
    token = set_channel(CHANNEL_SERVER)
    try:
        return await coro
    finally:
        reset_channel(token)


def spawn_background(coro, *, name: str | None = None) -> asyncio.Task:
    """调度一个「发射后不管」的后台协程。

    - 用全局集合持有强引用，直到任务结束才丢弃，避免被 GC 静默回收；
    - 任务内未捕获的异常在这里统一记日志（业务协程仍应自行 try/except）。
    """
    # 派生边界：后台任务不得继承本轮请求的 LLM 用量渠道（见 _detached）
    if asyncio.iscoroutine(coro):
        coro = _detached(coro)
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
