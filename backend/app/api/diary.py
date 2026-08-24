"""AI 日记 API"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.diary import AIDiary
from app.models.character import AICharacter
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.schemas.diary import DiaryEntryResponse, DiaryListResponse
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/diary", tags=["Diary"])
_logger = get_logger("api.diary")


async def _check_character_owned(db: AsyncSession, character_id: int, user_id: int, lang: str = "zh"):
    """校验角色归属当前用户"""
    result = await db.execute(
        select(AICharacter).where(
            AICharacter.id == character_id,
            AICharacter.user_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))


@router.get("/{character_id}", response_model=DiaryListResponse)
async def get_diary(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取某个角色的所有日记"""
    await _check_character_owned(db, character_id, user_id, lang)
    result = await db.execute(
        select(AIDiary)
        .where(AIDiary.character_id == character_id)
        .order_by(AIDiary.diary_date.desc())
    )
    entries = result.scalars().all()
    return DiaryListResponse(
        entries=[DiaryEntryResponse.model_validate(e) for e in entries],
        total=len(entries),
    )


@router.get("/{character_id}/date/{diary_date}", response_model=DiaryEntryResponse)
async def get_diary_by_date(
    character_id: int,
    diary_date: str,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取某天的日记"""
    await _check_character_owned(db, character_id, user_id, lang)
    result = await db.execute(
        select(AIDiary).where(
            AIDiary.character_id == character_id,
            AIDiary.diary_date == diary_date,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "no_diary_today"))
    return entry


@router.post("/generate/{character_id}")
async def generate_diary(
    character_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """手动触发生成日记（供调试用；鉴权 + 归属校验，防未登录烧 LLM/越权生成）"""
    await _check_character_owned(db, character_id, user_id, lang)
    from app.scheduler.diary_generator import generate_diary_for_character
    result = await generate_diary_for_character(character_id, force=force)
    if result is None:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "diary_gen_failed"))
    return {"status": "ok", "diary": result}
