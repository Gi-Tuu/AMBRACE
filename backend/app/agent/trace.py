"""Task Trace（Phase A，2026-08-16）：agent_task_logs 轻量写入，先只写不读。

- 所有写入失败静默，绝不阻塞主链路；
- 成本归因（全面审计第三批）后续直接复用本表聚合。
"""
from app.utils.async_tasks import spawn_background
import uuid

from app.utils.logger import get_logger

_logger = get_logger("agent.trace")


def new_task_id() -> str:
    """生成一次任务的短 id（12 位 hex，足够区分与展示）"""
    return uuid.uuid4().hex[:12]


async def resolve_owner_user_id(character_id: int | None, *, db=None) -> int | None:
    """按角色解析归属 user_id（审计第三批 P2-05 写入兜底：缺省时自动补归属）。

    优先复用调用方已打开的 db 会话（避免多余连接）；失败返回 None 不抛出。
    """
    if not character_id:
        return None
    try:
        from app.models.character import AICharacter
        if db is not None:
            c = await db.get(AICharacter, int(character_id))
        else:
            from app.db.database import async_session_factory
            async with async_session_factory() as _db:
                c = await _db.get(AICharacter, int(character_id))
        return c.user_id if c else None
    except Exception:
        return None


async def write_task_log(**kwargs) -> None:
    """写一条任务 trace（异步，失败静默）"""
    try:
        from app.db.database import async_session_factory
        from app.models.agent import AgentTaskLog

        # 审计第三批 P2-05：user_id 缺省时按 character 归属自动兜底（防 agent_task_logs 写 NULL）
        if not kwargs.get("user_id") and kwargs.get("character_id"):
            uid = await resolve_owner_user_id(kwargs.get("character_id"))
            if uid:
                kwargs["user_id"] = uid

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
        spawn_background(coro)
    except Exception as e:
        coro.close()  # 无运行中事件循环等场景：关闭协程避免 RuntimeWarning
        _logger.warning("Task trace enqueue failed: %s", e)
