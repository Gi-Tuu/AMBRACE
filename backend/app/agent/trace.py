"""Task Trace（Phase A，2026-08-16）：agent_task_logs 轻量写入，先只写不读。

- 所有写入失败静默，绝不阻塞主链路；
- 成本归因（全面审计第三批）后续直接复用本表聚合。
"""
import asyncio
import uuid

from app.utils.logger import get_logger

_logger = get_logger("agent.trace")


def new_task_id() -> str:
    """生成一次任务的短 id（12 位 hex，足够区分与展示）"""
    return uuid.uuid4().hex[:12]


async def write_task_log(**kwargs) -> None:
    """写一条任务 trace（异步，失败静默）"""
    try:
        from app.db.database import async_session_factory
        from app.models.agent_task_log import AgentTaskLog

        async with async_session_factory() as db:
            db.add(AgentTaskLog(**kwargs))
            await db.commit()
    except Exception as e:
        _logger.warning("Task trace write failed: %s", e)


def enqueue_task_log(**kwargs) -> None:
    """fire-and-forget 写 trace（调用处不 await，避免拖慢对话）"""
    try:
        coro = write_task_log(**kwargs)
    except Exception as e:
        _logger.warning("Task trace create failed: %s", e)
        return
    try:
        asyncio.ensure_future(coro)
    except Exception as e:
        coro.close()  # 无运行中事件循环等场景：关闭协程避免 RuntimeWarning
        _logger.warning("Task trace enqueue failed: %s", e)
