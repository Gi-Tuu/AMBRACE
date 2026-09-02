"""宠物 API：领养、查看、互动、改名"""
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import get_db
from app.models.pet import Pet
from app.schemas.pet import PetResponse, PetListResponse, CreatePetRequest, RenamePetRequest
from app.application import pet_service

router = APIRouter(prefix="/api/v1/pets", tags=["Pets"])


@router.get("", response_model=PetListResponse)
async def list_pets(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    result = await db.execute(
        select(Pet).where(
            Pet.user_id == user_id,
            or_(Pet.owner_type.is_(None), Pet.owner_type == "user"),
        ).order_by(Pet.created_at.asc())
    )
    pets = result.scalars().all()
    items = [await pet_service.build_response(p, db) for p in pets]
    return {"pets": items, "total": len(items)}


@router.post("", response_model=PetResponse)
async def adopt_pet(
    data: CreatePetRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    species = (data.species or "").strip().lower()
    meta = pet_service.SPECIES_META.get(species)
    if not meta:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_species_unsupported"))
    if meta["special"]:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_species_coming_soon"))
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_name_required"))
    if len(name) > pet_service.MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_name_too_long", max=pet_service.MAX_NAME_LEN))
    # 领养上限
    count = (await db.execute(
        select(Pet).where(
            Pet.user_id == user_id,
            or_(Pet.owner_type.is_(None), Pet.owner_type == "user"),
        )
    )).scalars().all()
    if len(count) >= pet_service.MAX_PETS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_limit_max", max=pet_service.MAX_PETS))
    pet = Pet(
        user_id=user_id,
        name=name,
        species=species,
        avatar_url=f"/uploads/pets_assets/{species}/idle.png",
        owner_type="user",
    )
    db.add(pet)
    await db.commit()
    await db.refresh(pet)
    await pet_service.log_activity(pet.id, user_id, "adopt", f"用户领养了{name}（{pet_service.species_label(species)}）")
    return await pet_service.build_response(pet, db)


async def _get_owned_pet(db: AsyncSession, pet_id: int, user_id: int, lang: str = "zh") -> Pet:
    result = await db.execute(
        select(Pet).where(Pet.id == pet_id, Pet.user_id == user_id)
    )
    pet = result.scalar_one_or_none()
    if not pet:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "pet_not_found"))
    return pet


async def _owner_char_name(db: AsyncSession, pet: Pet) -> str:
    """AI 宠物所属角色名（互动/记忆文案用）"""
    from app.models.character import AICharacter
    result = await db.execute(select(AICharacter).where(AICharacter.id == pet.owner_id))
    char = result.scalar_one_or_none()
    return char.name if char else "AI"


@router.get("/ai-pets")
async def list_ai_pets(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """用户所有角色的 AI 宠物（拜访/代为领养面板）：[{character_id, character_name, pet|null}]"""
    return {"characters": await pet_service.list_ai_pets(user_id)}


class AiPetAdoptRequest(BaseModel):
    character_id: int
    species: str
    name: str


@router.post("/ai-adopt", response_model=PetResponse)
async def ai_adopt_pet(
    data: AiPetAdoptRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """用户代为领养：为指定角色领养 AI 宠物（每角色 ≤1 只；与用户宠物 3 只上限分开）"""
    species = (data.species or "").strip().lower()
    meta = pet_service.SPECIES_META.get(species)
    if not meta or meta["special"]:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_species_unsupported"))
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_name_required"))
    if len(name) > pet_service.MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_name_too_long", max=pet_service.MAX_NAME_LEN))
    try:
        pet = await pet_service.ai_adopt(data.character_id, user_id, species, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await pet_service.build_response(pet, db)

@router.get("/{pet_id}", response_model=PetResponse)
async def get_pet(
    pet_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    pet = await _get_owned_pet(db, pet_id, user_id, lang)
    return await pet_service.build_response(pet, db)


@router.post("/{pet_id}/feed", response_model=PetResponse)
async def feed_pet(
    pet_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    pet = await _get_owned_pet(db, pet_id, user_id, lang)
    if pet.owner_type == "ai":
        char_name = await _owner_char_name(db, pet)
        return await pet_service.interact_by(db, pet, "feed", user_id, owner_char_name=char_name)
    return await pet_service.interact(db, pet, "feed", user_id)


@router.post("/{pet_id}/play", response_model=PetResponse)
async def play_pet(
    pet_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    pet = await _get_owned_pet(db, pet_id, user_id, lang)
    if pet.owner_type == "ai":
        char_name = await _owner_char_name(db, pet)
        return await pet_service.interact_by(db, pet, "play", user_id, owner_char_name=char_name)
    return await pet_service.interact(db, pet, "play", user_id)


@router.post("/{pet_id}/clean", response_model=PetResponse)
async def clean_pet(
    pet_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    pet = await _get_owned_pet(db, pet_id, user_id, lang)
    if pet.owner_type == "ai":
        char_name = await _owner_char_name(db, pet)
        return await pet_service.interact_by(db, pet, "clean", user_id, owner_char_name=char_name)
    return await pet_service.interact(db, pet, "clean", user_id)


@router.post("/{pet_id}/rename", response_model=PetResponse)
async def rename_pet(
    pet_id: int,
    data: RenamePetRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    pet = await _get_owned_pet(db, pet_id, user_id, lang)
    if pet.owner_type == "ai":
        raise HTTPException(status_code=400, detail=tr_lang(lang, "ai_pet_rename_forbidden"))
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_name_empty"))
    if len(name) > pet_service.MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "pet_name_too_long", max=pet_service.MAX_NAME_LEN))
    pet.name = name
    await db.commit()
    await pet_service.log_activity(pet.id, user_id, "rename", f"用户把宠物改名为{name}")
    return await pet_service.build_response(pet, db)


@router.get("/{pet_id}/activities")
async def get_pet_activities(
    pet_id: int,
    limit: int = 5,
    actor: str | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """宠物最近互动活动（互动展示区，倒序；短时重复事件已去重）；actor=ai 只返回角色自己照顾的记录"""
    await _get_owned_pet(db, pet_id, user_id, lang)
    return {"pet_id": pet_id, "activities": await pet_service.get_activities(pet_id, limit=limit, actor=actor)}


@router.delete("/{pet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_pet(
    pet_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """遗弃宠物（硬删除）：不影响角色与用户既有记忆，会记录'遗弃'事件供角色知晓"""
    pet = await _get_owned_pet(db, pet_id, user_id, lang)
    if pet.owner_type == "ai":
        raise HTTPException(status_code=400, detail=tr_lang(lang, "ai_pet_abandon_forbidden"))
    await pet_service.abandon_pet(pet_id, user_id)
