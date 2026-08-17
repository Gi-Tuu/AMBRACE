"""任务级 Agent 引擎（Phase H，2026-08-16）

agent_tasks：任务状态（goal/status/progress/result），与 agent_task_logs（执行流水）分离——
logs 记每步执行，tasks 记任务态（可追踪、可续作、可对比）。

- run_chat_task：聊天内多工具轮次任务化（≥2 个动作才建任务，避免噪音；只记不改行为）
- create/update：Scheduler 灰度角色主动行为建真实任务记录（Phase D trace 升级）
"""
import json

from app.utils.logger import get_logger

_logger = get_logger("agent.task_engine")


def _task_id() -> str:
    from app.agent import trace as _trace
    return _trace.new_task_id()


async def create_agent_task(
    *,
    trigger: str,
    goal: str,
    character_id: int,
    user_id: int | None = None,
    session_id: int | None = None,
) -> int:
    """创建任务（落库 agent_tasks），返回行 id；失败静默返回 0"""
    try:
        from app.db.database import async_session_factory
        from app.models.agent_task import AgentTask
        async with async_session_factory() as db:
            row = AgentTask(
                task_id=_task_id(), trigger=trigger, goal=str(goal)[:500],
                status="running", character_id=character_id,
                user_id=user_id, session_id=session_id,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return int(row.id)
    except Exception as e:
        _logger.warning("Agent task create failed: %s", e)
        return 0


async def update_task(
    task_id: int,
    *,
    status: str | None = None,
    progress: list[dict] | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """更新任务状态/进度/结果（失败静默）"""
    if not task_id:
        return
    try:
        from app.db.database import async_session_factory
        from app.models.agent_task import AgentTask
        async with async_session_factory() as db:
            row = await db.get(AgentTask, int(task_id))
            if row is None:
                return
            if status is not None:
                row.status = status
            if progress is not None:
                row.progress_json = json.dumps(progress, ensure_ascii=False)[:8000]
            if result is not None:
                row.result_json = json.dumps(result, ensure_ascii=False)[:8000]
            if error is not None:
                row.error = str(error)[:500]
            await db.commit()
    except Exception as e:
        _logger.warning("Agent task update failed: %s", e)


async def run_chat_task(
    character_id: int,
    user_id: int | None,
    session_id: int | None,
    user_msg: str,
    steps: list[dict],
    ai_response: str,
    ok: bool = True,
) -> int:
    """聊天内多工具轮次任务化（Phase H）：

    - 建任务（goal=用户消息）→ 记录已执行步骤 → 完成/失败回写；
    - 仅当 steps 含 ≥2 个动作才调用（由调用方判断），避免为简单对话建任务；
    - 失败静默，绝不阻塞主链路。
    """
    tid = await create_agent_task(
        trigger="chat_task", goal=user_msg, character_id=character_id,
        user_id=user_id, session_id=session_id,
    )
    if not tid:
        return 0
    await update_task(
        tid,
        progress=steps,
        status="done" if ok else "failed",
        result={"steps": len(steps), "ai_response": str(ai_response)[:200]} if ok else None,
        error=None if ok else "工具执行未全部成功（已完成步骤已保留）",
    )
    return tid
