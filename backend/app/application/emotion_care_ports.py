"""情绪关怀 IO 端口的生产实现（架构地图断点 #1 样板，2026-09-29）。

原先写在 app/domain/emotion/care.py 里的 DB / LLM / 发送 / 人设素材调用原样搬到此处：
SQL 语句、查询条件与调用形态逐字保留（唯一变化是各操作各自开一个会话，原来是共用同一会话，
读的是同一批表且无跨操作事务，语义不变）。

接线点（domain 不反向依赖本模块，由上层注入）：
- app/agent/internal_runner.py —— run_internal("emotion_care") 执行关怀
- app/application/chat_service.py —— 登记延迟关怀任务
- app/scheduling/sources/emotion_care.py —— arbiter 事件源采集
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.database import async_session_factory
from app.domain.emotion.care import CARE_TYPE
from app.domain.emotion.ports import CareCharacterView, CareTaskView
from app.models.agent import EmotionCareTask
from app.models.character import AICharacter, ProactiveMessageLog


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _task_view(task) -> CareTaskView:
    return CareTaskView(
        id=task.id, status=task.status, trigger_msg=task.trigger_msg,
        user_id=task.user_id, character_id=task.character_id, due_at=task.due_at,
    )


class ProductionCarePorts:
    """EmotionCarePorts 生产实现（原 care.py 的 IO 代码，行为不变）。"""

    # ── 开关与任务表 ──

    async def proactive_enabled(self, character_id: int) -> bool:
        from app.scheduling.triggers import proactive_enabled
        return await proactive_enabled(character_id)

    async def has_pending_care_task(self, user_id: int, character_id: int) -> bool:
        async with async_session_factory() as db:
            existing = (await db.execute(
                select(EmotionCareTask.id).where(
                    EmotionCareTask.character_id == character_id,
                    EmotionCareTask.user_id == user_id,
                    EmotionCareTask.status == "pending",
                ).limit(1)
            )).first()
        return bool(existing)

    async def create_care_task(self, *, user_id: int, character_id: int,
                               trigger_msg: str, due_at: datetime) -> None:
        async with async_session_factory() as db:
            db.add(EmotionCareTask(
                user_id=user_id, character_id=character_id,
                trigger_msg=trigger_msg,
                due_at=due_at, status="pending",
            ))
            await db.commit()

    async def load_care_task(self, task_id: int) -> CareTaskView | None:
        async with async_session_factory() as db:
            task = await db.get(EmotionCareTask, task_id)
            return _task_view(task) if task else None

    async def finish_care_task(self, task_id: int, status: str) -> None:
        now = _now_naive()
        async with async_session_factory() as db:
            task = await db.get(EmotionCareTask, task_id)
            if task:
                task.status = status
                task.finished_at = now
                await db.commit()

    async def cancel_stale_care_tasks(self, now: datetime, stale_before: datetime) -> None:
        # 先作废超 24h 未发送的任务，避免无限重试
        async with async_session_factory() as db:
            stale = await db.execute(
                select(EmotionCareTask).where(
                    EmotionCareTask.status == "pending",
                    EmotionCareTask.due_at < stale_before,
                )
            )
            for t in stale.scalars().all():
                t.status = "cancelled"
                t.finished_at = now
            await db.commit()

    async def fetch_due_care_tasks(self, now: datetime,
                                   stale_before: datetime) -> list[CareTaskView]:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(EmotionCareTask)
                .where(
                    EmotionCareTask.status == "pending",
                    EmotionCareTask.due_at <= now,
                    EmotionCareTask.due_at >= stale_before,
                )
                .order_by(EmotionCareTask.due_at.asc())
            )).scalars().all()
            return [_task_view(t) for t in rows]

    # ── 每日限额 / 最小间隔 / 免打扰 ──

    async def daily_care_count(self, character_id: int) -> int:
        cn_tz = timezone(timedelta(hours=8))
        today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
        async with async_session_factory() as db:
            return (await db.execute(
                select(func.count(ProactiveMessageLog.id)).where(
                    ProactiveMessageLog.character_id == character_id,
                    ProactiveMessageLog.message_type == CARE_TYPE,
                    ProactiveMessageLog.created_at >= today_start,
                )
            )).scalar() or 0

    async def last_care_at(self, character_id: int) -> datetime | None:
        async with async_session_factory() as db:
            return (await db.execute(
                select(func.max(ProactiveMessageLog.created_at)).where(
                    ProactiveMessageLog.character_id == character_id,
                    ProactiveMessageLog.message_type == CARE_TYPE,
                )
            )).scalar_one_or_none()

    async def user_in_dnd(self, user_id: int) -> bool:
        from app.utils.dnd import user_in_dnd_period
        async with async_session_factory() as db:
            return await user_in_dnd_period(db, user_id)

    # ── 角色与会话 ──

    async def load_character(self, character_id: int) -> CareCharacterView | None:
        async with async_session_factory() as db:
            char = await db.get(AICharacter, character_id)
            if char is None:
                return None
            return CareCharacterView(id=char.id, name=char.name, personality=char.personality)

    async def latest_session_id(self, user_id: int, character_id: int) -> int | None:
        from app.application.chat_service import get_latest_session_id
        return await get_latest_session_id(user_id, character_id)

    # ── 生成素材 ──

    async def build_identity_prompt(self, character_id: int, user_id: int) -> str:
        from app.agent.user_profile import build_role_prompt_block
        async with async_session_factory() as db:
            char = await db.get(AICharacter, character_id)
        return await build_role_prompt_block(char, user_id)

    async def build_active_persona(self, character_id: int, user_id: int) -> str:
        from app.agent.persona import build_active_channel_persona
        return await build_active_channel_persona(character_id, user_id)

    async def weather_line(self, user_id: int) -> str:
        from app.application.weather_service import get_user_weather_line
        return await get_user_weather_line(user_id)

    async def state_guard_block(self, character_id: int, user_id: int) -> str:
        from app.scheduling import state_guard
        return state_guard.guard_block(
            await state_guard.current_state_anchor(character_id=character_id, user_id=user_id))

    # ── LLM 与主动消息出口 ──

    async def reasoning_level(self, character_id: int) -> int:
        from app.agent.llm_client import load_character_reasoning_level
        return await load_character_reasoning_level(character_id)

    async def chat_completion(self, *, messages: list[dict[str, str]], temperature: float,
                              max_tokens: int, task: str, user_id: int) -> str:
        from app.agent.llm_client import chat_completion
        return await chat_completion(messages=messages, temperature=temperature,
                                     max_tokens=max_tokens, task=task, user_id=user_id)

    async def send_care_message(self, *, session_id: int, character_id: int, user_id: int,
                                content: str, message_type: str,
                                extra_meta: str | None = None) -> None:
        from app.scheduling.scheduler import send_to_session
        await send_to_session(
            session_id=session_id, character_id=character_id, user_id=user_id,
            content=content, message_type=message_type,
            extra_meta=extra_meta,
        )


production_care_ports = ProductionCarePorts()
