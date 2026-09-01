"""宠物服务：惰性衰减结算、互动、状态与记忆联动"""
from datetime import datetime

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.pet import Pet
from app.models.character import AICharacter
from app.memory import save_memory
from app.utils.logger import get_logger
from app.utils.clamp import clamp_int as _clamp

_logger = get_logger("services.pet")

# 物种白名单（special=True 为非常宠物，仅入口占位）
SPECIES_META = {
    "cat": {"label": "猫", "special": False,
            "diet": "吃猫粮/猫罐头，喝干净的水", "care": "猫砂盆要勤清理，别喂狗狗食物"},
    "dog": {"label": "狗", "special": False,
            "diet": "吃狗粮，适量肉和蔬果", "care": "需要遛弯运动，别喂猫咪食物"},
    "parrot": {"label": "鹦鹉", "special": False,
               "diet": "吃鸟粮/谷物、新鲜蔬果，不吃猫粮狗粮", "care": "喜欢有人陪说话，笼子要通风"},
    "rabbit": {"label": "兔子", "special": False,
               "diet": "吃干草、兔粮和蔬菜，不吃猫粮", "care": "要磨牙，别喂太多含糖水果"},
    "hamster": {"label": "仓鼠", "special": False,
                "diet": "吃仓鼠粮、谷物种子，少量蔬果", "care": "夜行性，白天睡觉别打扰"},
    "snake": {"label": "蛇", "special": False,
              "diet": "吃鼠类等活食或冷冻鼠", "care": "需要加热垫维持温度"},
    "gecko": {"label": "守宫", "special": False,
              "diet": "吃昆虫（面包虫/蟋蟀）", "care": "夜行性，需要加热与湿度"},
    "turtle": {"label": "乌龟", "special": False,
               "diet": "吃龟粮/鱼虾、蔬菜", "care": "需要晒背和干净的水，冬眠期注意保暖"},
    "chinchilla": {"label": "龙猫", "special": False,
                   "diet": "吃提摩西干草和龙猫粮，不吃猫粮", "care": "怕热，不能洗澡要用浴沙"},
    "hedgehog": {"label": "刺猬", "special": False,
                 "diet": "吃猫粮/刺猬粮和昆虫、熟肉", "care": "夜行性，怕冷需要保温"},
}

# 惰性衰减：属性 -> 基准时间字段、周期小时、每周期衰减量
DECAY_HOURS = {"hunger": 6, "mood": 8, "energy": 6, "cleanliness": 12}
DECAY_AMOUNT = {"hunger": 10, "mood": 8, "energy": 8, "cleanliness": 10}
_ATTR_BASE = {
    "hunger": "last_feed_at",
    "mood": "last_play_at",
    "energy": "last_play_at",
    "cleanliness": "last_clean_at",
}

# 互动效果（加到对应属性，上限 100）
INTERACTIONS = {
    "feed": {"label": "喂食", "hunger": 25},
    "play": {"label": "玩耍", "mood": 25, "energy": 15},
    "clean": {"label": "清洁", "cleanliness": 30},
}
_ACTION_BASE = {"feed": "last_feed_at", "play": "last_play_at", "clean": "last_clean_at"}

EXP_PER_INTERACTION = 10
EXP_PER_LEVEL = 100
ATTENTION_THRESHOLD = 30
MAX_PETS = 3          # 领养上限
MAX_NAME_LEN = 5      # 宠物名最多 5 个字
ACTIVITY_DEDUP_MINUTES = 30  # 同一宠物同动作短时去重窗口（互动展示区）


from app.utils.timeutil import now_naive_utc as _now_naive


def species_label(species: str) -> str:
    meta = SPECIES_META.get(species)
    return meta["label"] if meta else species


def species_fact(species: str) -> str:
    """宠物习性知识（饮食+照顾要点），用于注入 LLM 上下文，防止被口误带偏"""
    meta = SPECIES_META.get((species or "").strip().lower())
    if not meta:
        return ""
    parts = [p for p in (meta.get("diet"), meta.get("care")) if p]
    return "；".join(parts)



def apply_decay(pet: Pet, now: datetime | None = None) -> bool:
    """按上次互动时间惰性结算属性衰减；不移动基准时间，重复调用幂等"""
    now = now or _now_naive()
    changed = False
    for attr in DECAY_HOURS:
        base_attr = _ATTR_BASE[attr]
        last_at = getattr(pet, base_attr)
        if last_at is None:
            continue
        if getattr(last_at, "tzinfo", None) is not None:
            last_at = last_at.replace(tzinfo=None)
        hours = max(0.0, (now - last_at).total_seconds() / 3600.0)
        periods = int(hours // DECAY_HOURS[attr])
        if periods >= 1:
            new_val = _clamp(getattr(pet, attr) - DECAY_AMOUNT[attr] * periods)
            if new_val != getattr(pet, attr):
                setattr(pet, attr, new_val)
                changed = True
    if changed:
        recompute_status(pet)
    return changed


def recompute_status(pet: Pet) -> None:
    if pet.hunger < ATTENTION_THRESHOLD:
        pet.status_text = "饿坏了"
    elif pet.cleanliness < ATTENTION_THRESHOLD:
        pet.status_text = "脏兮兮的"
    elif pet.energy < ATTENTION_THRESHOLD:
        pet.status_text = "累得不想动"
    elif pet.mood < ATTENTION_THRESHOLD:
        pet.status_text = "闷闷不乐"
    elif min(pet.hunger, pet.mood, pet.energy, pet.cleanliness) >= 80:
        pet.status_text = "精神满满"
    elif min(pet.hunger, pet.mood, pet.energy, pet.cleanliness) >= 60:
        pet.status_text = "状态不错"
    elif min(pet.hunger, pet.mood, pet.energy, pet.cleanliness) >= 30:
        pet.status_text = "有点没精神"
    else:
        pet.status_text = "蔫蔫的"


def need_attention(pet: Pet) -> bool:
    return any(v < ATTENTION_THRESHOLD for v in (pet.hunger, pet.mood, pet.energy, pet.cleanliness))


async def build_response(pet: Pet, db) -> dict:
    """读时结算衰减并组装响应 dict（FastAPI 只返回 Schema 声明过的字段）"""
    changed = apply_decay(pet)
    if changed:
        pet.updated_at = _now_naive()
        await db.commit()
    meta = SPECIES_META.get(pet.species, {})
    return {
        "id": pet.id,
        "name": pet.name,
        "species": pet.species,
        "species_label": meta.get("label", pet.species),
        "avatar_url": pet.avatar_url,
        "level": pet.level,
        "exp": pet.exp,
        "hunger": pet.hunger,
        "mood": pet.mood,
        "energy": pet.energy,
        "cleanliness": pet.cleanliness,
        "status_text": pet.status_text,
        "need_attention": need_attention(pet),
        "is_special": bool(meta.get("special")),
        "created_at": pet.created_at,
    }


def _add_exp(pet: Pet) -> None:
    pet.exp += EXP_PER_INTERACTION
    new_level = 1 + pet.exp // EXP_PER_LEVEL
    if new_level > pet.level:
        pet.level = new_level


def _apply_interaction(pet: Pet, action: str, now: datetime) -> None:
    """互动属性结算（不落库）：衰减 → 属性回升 → 记时间/经验 → 状态"""
    apply_decay(pet, now)
    effects = INTERACTIONS[action]
    for attr, delta in effects.items():
        if attr == "label":
            continue
        setattr(pet, attr, _clamp(getattr(pet, attr) + delta))
    setattr(pet, _ACTION_BASE[action], now)
    _add_exp(pet)
    recompute_status(pet)
    pet.updated_at = now


async def interact(db, pet: Pet, action: str, user_id: int) -> dict:
    """执行互动：结算衰减 → 属性回升 → 记时间/经验 → 状态 → 记忆联动"""
    _apply_interaction(pet, action, _now_naive())
    await db.commit()
    await _save_pet_memory(user_id, pet, action)
    await log_activity(pet.id, user_id, action, _activity_content(pet, action), actor="user")
    return await build_response(pet, db)


async def interact_by(
    db, pet: Pet, action: str, user_id: int,
    actor: str = "user", owner_char_name: str | None = None,
) -> dict:
    """互动执行（区分执行者）：actor=ai 时角色自己照顾；owner_char_name 有值时=用户拜访角色的宠物"""
    _apply_interaction(pet, action, _now_naive())
    await db.commit()
    label = INTERACTIONS[action]["label"]
    species = species_label(pet.species)
    if actor == "ai":
        activity = f"{owner_char_name or 'AI'}给{pet.name}（{species}）{label}了"
        memory = f"{owner_char_name or 'AI'}给自己的宠物{pet.name}（{species}）{label}了"
    elif owner_char_name:
        activity = f"用户拜访了{owner_char_name}的宠物{pet.name}（{species}）并{label}了"
        memory = f"用户拜访了{owner_char_name}的宠物{pet.name}（{species}）并{label}了"
    else:
        activity = f"用户给{pet.name}（{species}）{label}了"
        memory = f"用户给宠物{pet.name}（{species}）{label}了"
    await log_activity(pet.id, user_id, action, activity, actor=actor)
    await _save_pet_memory(user_id, pet, action, content_override=memory)
    return await build_response(pet, db)


async def _save_pet_memory(user_id: int, pet: Pet, action: str, content_override: str | None = None) -> None:
    """宠物互动写入记忆：用户宠物→全活跃角色各一条（"家里的一份子"）；AI 宠物→仅归属角色本人（防止别的角色把宠物算成自己/用户养的）"""
    if content_override is not None:
        content = content_override
    else:
        action_label = INTERACTIONS[action]["label"]
        content = f"用户给宠物{pet.name}（{species_label(pet.species)}）{action_label}了"
    try:
        if getattr(pet, "owner_type", None) == "ai":
            # AI 宠物（某角色养的）：事件只写归属角色，其他角色不记录，避免归属串扰
            target_char_ids = [pet.owner_id]
        else:
            async with async_session_factory() as db:
                target_char_ids = [
                    ch.id for ch in (
                        await db.execute(
                            select(AICharacter).where(
                                AICharacter.user_id == user_id,
                                AICharacter.is_active == True,  # noqa: E712
                            )
                        )
                    ).scalars().all()
                ]
        for ch_id in target_char_ids:
            try:
                await save_memory(
                    user_id=user_id,
                    character_id=ch_id,
                    memory_type="event",
                    content=content,
                    importance=1,
                    source="pet",
                    speaker_type="user", speaker_id=user_id,
                    epistemic_status="FACT",
                )
            except Exception as e:
                _logger.warning("Pet memory char=%d failed: %s", ch_id, e)
    except Exception as e:
        _logger.warning("Pet memory save failed: %s", e)


async def log_activity(
    pet_id: int, user_id: int, action: str, content: str, actor: str = "user",
) -> None:
    """记录宠物活动；同宠物同动作同执行者 30 分钟内重复仅视为一件事（更新时间不新增行）"""
    try:
        from datetime import timedelta
        from app.models.pet import PetActivity
        now = _now_naive()
        async with async_session_factory() as db:
            existing = (
                await db.execute(
                    select(PetActivity).where(
                        PetActivity.pet_id == pet_id,
                        PetActivity.action == action,
                        PetActivity.actor == actor,
                        PetActivity.created_at >= now - timedelta(minutes=ACTIVITY_DEDUP_MINUTES),
                    ).order_by(PetActivity.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.created_at = now
                existing.content = content[:200]
            else:
                db.add(PetActivity(
                    pet_id=pet_id, user_id=user_id, action=action, actor=actor, content=content[:200],
                ))
            await db.commit()
    except Exception as e:
        _logger.warning("Pet activity log failed pet=%d: %s", pet_id, e)


def _activity_content(pet: Pet, action: str) -> str:
    label = INTERACTIONS.get(action, {}).get("label", action)
    return f"用户给{pet.name}（{species_label(pet.species)}）{label}了"


async def get_activities(
    pet_id: int, limit: int = 10, actor: str | None = None,
) -> list[dict]:
    """宠物最近活动（倒序）；actor 非空时按执行者过滤（ai=角色自己照顾）"""
    from app.models.pet import PetActivity
    async with async_session_factory() as db:
        q = select(PetActivity).where(PetActivity.pet_id == pet_id)
        if actor:
            q = q.where(PetActivity.actor == actor)
        rows = (
            await db.execute(
                q.order_by(PetActivity.created_at.desc()).limit(max(1, min(int(limit), 30)))
            )
        ).scalars().all()
    return [
        {"id": r.id, "action": r.action, "actor": r.actor, "content": r.content, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


async def abandon_pet(pet_id: int, user_id: int) -> bool:
    """遗弃宠物（硬删除 pets 行 + 互动展示区记录 + 写'遗弃'记忆给所有活跃角色；不影响角色与用户既有记忆）"""
    async with async_session_factory() as db:
        pet = await db.get(Pet, pet_id)
        if pet is None or pet.user_id != user_id:
            return False
        name = pet.name
        species = species_label(pet.species)
        await db.delete(pet)
        await db.commit()
    _logger.info("Pet abandoned: id=%d name=%s user=%d", pet_id, name, user_id)
    # 记录遗弃（先落活动，再写记忆；表独立于 pets，删除宠物后仍保留）
    await log_activity(pet_id, user_id, "abandon", f"用户遗弃了{name}（{species}）")
    # 写记忆：让所有活跃角色知道宠物被遗弃（记忆与角色/用户绑定，不随宠物删除）
    try:
        async with async_session_factory() as db:
            chars = (
                await db.execute(
                    select(AICharacter).where(
                        AICharacter.user_id == user_id,
                        AICharacter.is_active == True,  # noqa: E712
                    )
                )
            ).scalars().all()
        for ch in chars:
            try:
                await save_memory(
                    user_id=user_id,
                    character_id=ch.id,
                    memory_type="event",
                    content=f"用户遗弃了宠物{name}（{species}）",
                    importance=2,
                    source="pet",
                    skip_dedup=True,
                    speaker_type="user", speaker_id=user_id,
                    epistemic_status="FACT",
                )
            except Exception as e:
                _logger.warning("Abandon memory char=%d failed: %s", ch.id, e)
    except Exception as e:
        _logger.warning("Abandon memory save failed: %s", e)
    return True

# ── Phase 3：AI 自主养宠物（owner_type="ai"） ──

MAX_AI_PETS = 1  # 每角色 AI 宠物上限（与用户宠物 3 只上限分开）


async def list_ai_pets(user_id: int) -> list[dict]:
    """用户所有活跃角色的 AI 宠物（含角色名；无宠物返回 None）——拜访/代为领养面板数据源"""
    async with async_session_factory() as db:
        chars = (await db.execute(
            select(AICharacter).where(
                AICharacter.user_id == user_id, AICharacter.is_active == True  # noqa: E712
            ).order_by(AICharacter.id)
        )).scalars().all()
        pet_rows = (await db.execute(
            select(Pet).where(Pet.user_id == user_id, Pet.owner_type == "ai")
        )).scalars().all()
        pet_by_char: dict[int, Pet] = {p.owner_id: p for p in pet_rows}
        items = []
        for c in chars:
            pet = pet_by_char.get(c.id)
            items.append({
                "character_id": c.id,
                "character_name": c.name,
                "pet": await build_response(pet, db) if pet is not None else None,
            })
    return items


async def ai_adopt(character_id: int, user_id: int, species: str, name: str) -> Pet:
    """用户代为领养 / AI 自主领养：为角色创建 AI 宠物（owner_type='ai', owner_id=character_id）"""
    async with async_session_factory() as db:
        char = (await db.execute(
            select(AICharacter).where(
                AICharacter.id == character_id,
                AICharacter.user_id == user_id,
                AICharacter.is_active == True,  # noqa: E712
            )
        )).scalar_one_or_none()
        if char is None:
            raise ValueError("角色不存在")
        existing = (await db.execute(
            select(Pet).where(
                Pet.user_id == user_id, Pet.owner_type == "ai", Pet.owner_id == character_id
            )
        )).scalars().all()
        if len(existing) >= MAX_AI_PETS:
            raise ValueError("该角色已经养了宠物")
        pet = Pet(
            user_id=user_id, name=name, species=species,
            avatar_url=f"/uploads/pets_assets/{species}/idle.png",
            owner_type="ai", owner_id=character_id,
        )
        db.add(pet)
        await db.commit()
        await db.refresh(pet)
    await log_activity(pet.id, user_id, "adopt", f"{char.name}领养了{name}（{species_label(species)}）")
    await _save_pet_memory(
        user_id, pet, "adopt",
        content_override=f"{char.name}领养了一只宠物{name}（{species_label(species)}）",
    )
    return pet
