"""朋友圈调度入口 — 由调度器触发，统一委托 services.moment_service 发布（上限/间隔/生成逻辑收敛到服务层）"""
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.proactive_settings import ProactiveSettings
from app.services.moment_service import publish_moment, generate_comments_for_moment
from app.utils.logger import get_logger

_logger = get_logger("scheduler.moments")


async def publish_pending_moments():
    """检查所有开启了朋友圈的角色，发布待发动态（每日上限/时间间隔由 moment_service 统一控制）"""
    async with async_session_factory() as db:
        result = await db.execute(select(ProactiveSettings).where(ProactiveSettings.moments_enabled == True))
        settings_list = result.scalars().all()

    # 过滤已删除角色
    active_settings = []
    for s in settings_list:
        async with async_session_factory() as db:
            cr = await db.execute(select(AICharacter).where(AICharacter.id == s.character_id, AICharacter.is_active == True))
            if cr.scalar_one_or_none():
                active_settings.append(s)

    for settings in active_settings:
        try:
            result = await publish_moment(settings.character_id)
            if result is not None:
                try:
                    await generate_comments_for_moment(result["id"])
                except Exception:
                    pass
        except Exception as e:
            _logger.warning("Moment publish failed char=%d: %s", settings.character_id, e)
