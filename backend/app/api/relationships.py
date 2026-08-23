"""关系网 API：用户与各 AI 角色的关系设置"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import get_db
from app.schemas.relationship import RelationshipUpdate
from app.models.user import User
from app.models.character import AICharacter

router = APIRouter(prefix="/api/v1/relationships", tags=["Relationships"])


@router.get("")
async def get_relationships(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    user = await db.get(User, user_id)
    result = await db.execute(
        select(AICharacter).where(
            AICharacter.user_id == user_id,
            AICharacter.is_active == True,  # noqa: E712
        ).order_by(AICharacter.id)
    )
    chars = result.scalars().all()
    return {
        "user": {
            "id": user.id,
            "nickname": user.nickname,
            "gender": user.gender,
        } if user else None,
        "relationships": [
            {
                "character_id": c.id,
                "name": c.name,
                "avatar_url": c.avatar_url,
                "gender": c.gender,
                "relation_type": c.relation_type or "朋友",
                "is_partner": bool(c.is_partner),
                "relationship_summary": c.relationship_summary or "",
            }
            for c in chars
        ],
    }


@router.put("/{character_id}")
async def update_relationship(
    character_id: int,
    data: RelationshipUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    result = await db.execute(
        select(AICharacter).where(
            AICharacter.id == character_id,
            AICharacter.user_id == user_id,
            AICharacter.is_active == True,  # noqa: E712
        )
    )
    char = result.scalar_one_or_none()
    if not char:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))

    if data.relation_type is not None:
        char.relation_type = data.relation_type
    if data.is_partner is not None:
        if data.is_partner:
            # 保证唯一对象：先把其他角色取消对象标记
            others = await db.execute(
                select(AICharacter).where(
                    AICharacter.user_id == user_id,
                    AICharacter.is_partner == True,  # noqa: E712
                )
            )
            for o in others.scalars().all():
                o.is_partner = False
        char.is_partner = data.is_partner
    if data.relationship_summary is not None:
        char.relationship_summary = data.relationship_summary
    await db.commit()
    return {"status": "ok", "character_id": character_id}
