from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.services.timeline_service import build_timeline, generate_milestones

router = APIRouter(prefix="/api/v1/timeline", tags=["Timeline"])


@router.get("/{character_id}")
async def get_timeline(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """角色时光线：认识天数 + 关键节点（首次聊天/祝福/宠物领养/重要事件）"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id == user_id)
        )
        char = result.scalar_one_or_none()
    if char is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    return await build_timeline(user_id, character_id)


@router.post("/{character_id}/milestones")
async def create_milestones(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """离线生成/获取该角色大事记（幂等：已生成则直接返回现有）"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id == user_id)
        )
        char = result.scalar_one_or_none()
    if char is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    result = await generate_milestones(user_id, character_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
