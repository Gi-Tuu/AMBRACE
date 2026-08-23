"""记忆功能开关查询（统一实现，避免各模块多份复制）"""
from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.character import AICharacter


async def memory_v2_enabled(character_id: int) -> bool:
    """读取记忆 v2.1 开关（查询失败按关闭处理，不影响主链路）"""
    try:
        async with async_session_factory() as db:
            row = (await db.execute(
                select(AICharacter.memory_v2_enabled).where(AICharacter.id == character_id)
            )).scalar_one_or_none()
        return bool(row)
    except Exception:
        return False
