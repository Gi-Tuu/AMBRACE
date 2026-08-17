"""生活可视·小家 API（v3.1.0）：多房间像素家居 + 交互事件 + 宠物互动

- GET  /api/v1/life-home/state   读取小家状态（角色名/体力/心情/饥饿/房间布局/宠物）
- POST /api/v1/life-home/event   交互事件（睡/做/吃/工作/看电视/读书/洗澡/运动/音乐/游戏/宠物喂食/抚摸）
- 状态存储复用：体力=life_states.energy、心情=character_states.mood、饥饿=life_states.needs_json[food]
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select

from app.db.database import async_session_factory
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.utils.clamp import clamp_int as _clamp
from app.models.character import AICharacter
from app.models.character_state import CharacterState
from app.models.life import LifeState, LifeActivityLog
from app.models.pet import Pet

router = APIRouter(prefix="/api/v1/life-home", tags=["Life Home"])

# 房间与家具布局（格坐标；逻辑画布 16x12 格 = 640x480 像素；2026-08-15 压缩画布+装饰填充）
ROOMS_LAYOUT = {
    "living": {
        "name": "客厅",
        "furniture": [
            {"key": "sofa", "name": "沙发", "gx": 6, "gy": 1, "gw": 2, "gh": 1, "action": "tv"},
            {"key": "tv", "name": "电视", "gx": 8, "gy": 1, "gw": 1, "gh": 1, "action": "tv"},
            {"key": "coffee", "name": "茶几", "gx": 7, "gy": 3, "gw": 1, "gh": 1, "action": None},
            {"key": "speaker", "name": "音响", "gx": 14, "gy": 1, "gw": 1, "gh": 1, "action": "music"},
            {"key": "game", "name": "游戏机", "gx": 14, "gy": 3, "gw": 1, "gh": 1, "action": "game"},
            {"key": "plant", "name": "盆栽", "gx": 1, "gy": 10, "gw": 1, "gh": 1, "action": None},
            {"key": "petbed", "name": "宠物窝", "gx": 14, "gy": 10, "gw": 1, "gh": 1, "action": None},
            {"key": "rug", "name": "地毯", "gx": 6, "gy": 5, "gw": 3, "gh": 2, "action": None},
            {"key": "painting", "name": "挂画", "gx": 1, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "lamp", "name": "台灯", "gx": 10, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "shelf", "name": "置物架", "gx": 12, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "clock", "name": "挂钟", "gx": 13, "gy": 1, "gw": 1, "gh": 1, "action": None},
        ],
        "doors": [
            {"target": "bedroom", "x": 15, "y": 7},
            {"target": "kitchen", "x": 7, "y": 0},
            {"target": "bathroom", "x": 11, "y": 11},
        ],
    },
    "bedroom": {
        "name": "卧室",
        "furniture": [
            {"key": "bed", "name": "床", "gx": 1, "gy": 1, "gw": 2, "gh": 2, "action": "sleep"},
            {"key": "wardrobe", "name": "衣柜", "gx": 14, "gy": 1, "gw": 1, "gh": 2, "action": None},
            {"key": "nightstand", "name": "床头柜", "gx": 4, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "desk", "name": "书桌", "gx": 2, "gy": 9, "gw": 2, "gh": 1, "action": "work"},
            {"key": "bookshelf", "name": "书柜", "gx": 4, "gy": 9, "gw": 1, "gh": 2, "action": "read"},
            {"key": "rug", "name": "地毯", "gx": 6, "gy": 4, "gw": 3, "gh": 2, "action": None},
            {"key": "painting", "name": "挂画", "gx": 10, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "lamp", "name": "台灯", "gx": 11, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "clock", "name": "挂钟", "gx": 12, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "plant", "name": "盆栽", "gx": 13, "gy": 9, "gw": 1, "gh": 1, "action": None},
        ],
        "doors": [
            {"target": "living", "x": 0, "y": 7},
        ],
    },
    "kitchen": {
        "name": "厨房",
        "furniture": [
            {"key": "stove", "name": "灶台", "gx": 10, "gy": 1, "gw": 2, "gh": 1, "action": "cook"},
            {"key": "fridge", "name": "冰箱", "gx": 13, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "table", "name": "餐桌", "gx": 10, "gy": 4, "gw": 2, "gh": 2, "action": "eat"},
            {"key": "chair", "name": "餐椅", "gx": 9, "gy": 4, "gw": 1, "gh": 1, "action": None},
            {"key": "plant", "name": "盆栽", "gx": 1, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "painting", "name": "挂画", "gx": 7, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "clock", "name": "挂钟", "gx": 8, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "shelf", "name": "置物架", "gx": 15, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "rug", "name": "地毯", "gx": 5, "gy": 8, "gw": 2, "gh": 1, "action": None},
            {"key": "bin", "name": "垃圾桶", "gx": 14, "gy": 10, "gw": 1, "gh": 1, "action": None},
        ],
        "doors": [
            {"target": "living", "x": 7, "y": 11},
        ],
    },
    "bathroom": {
        "name": "浴室",
        "furniture": [
            {"key": "shower", "name": "淋浴", "gx": 14, "gy": 8, "gw": 1, "gh": 2, "action": "shower"},
            {"key": "bathtub", "name": "浴缸", "gx": 11, "gy": 8, "gw": 2, "gh": 2, "action": "shower"},
            {"key": "sink", "name": "洗手台", "gx": 10, "gy": 8, "gw": 1, "gh": 1, "action": None},
            {"key": "plant", "name": "盆栽", "gx": 1, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "painting", "name": "挂画", "gx": 12, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "shelf", "name": "置物架", "gx": 14, "gy": 1, "gw": 1, "gh": 1, "action": None},
            {"key": "rug", "name": "地毯", "gx": 6, "gy": 9, "gw": 2, "gh": 1, "action": None},
            {"key": "bin", "name": "垃圾桶", "gx": 13, "gy": 10, "gw": 1, "gh": 1, "action": None},
        ],
        "doors": [
            {"target": "living", "x": 11, "y": 0},
        ],
    },
}

# 交互动作 → 状态增量（stamina/mood/hunger）与生活活动映射
ACTIONS = {
    "sleep":    {"stamina": 25, "mood": 5,  "hunger": -5,  "activity": "rest",             "label": "睡觉"},
    "work":     {"stamina": -15, "mood": -5, "hunger": -8, "activity": "organize_memory", "label": "工作"},
    "cook":     {"stamina": -8,  "mood": 3,  "hunger": 0,  "activity": "create",           "label": "做饭"},
    "eat":      {"stamina": 2,   "mood": 5,  "hunger": 35, "activity": "rest",             "label": "吃饭"},
    "tv":       {"stamina": -3,  "mood": 10, "hunger": -2, "activity": "rest",             "label": "看电视"},
    "read":     {"stamina": -4,  "mood": 8,  "hunger": -2, "activity": "learn",            "label": "读书"},
    "shower":   {"stamina": 10,  "mood": 8,  "hunger": -2, "activity": "rest",             "label": "洗澡"},
    "exercise": {"stamina": -12, "mood": 6,  "hunger": -6, "activity": "reflect",          "label": "运动"},
    "music":    {"stamina": -2,  "mood": 9,  "hunger": -1, "activity": "rest",             "label": "听音乐"},
    "game":     {"stamina": -4,  "mood": 12, "hunger": -3, "activity": "rest",             "label": "玩游戏"},
}

# 宠物互动动作映射到 pet_service
PET_ACTIONS = {
    "pet_feed": {"pet_action": "feed", "label": "喂食"},
    "pet_pet":  {"pet_action": "play", "label": "抚摸"},
}


async def _resolve_character(db, user_id: int, character_id: int, lang: str = "zh") -> int:
    """character_id=0 时解析为最近互动角色（与隐私锁/小手机口径一致）"""
    if character_id not in (0, None):
        return int(character_id)
    char = (
        await db.execute(
            select(AICharacter)
            .where(AICharacter.user_id == user_id, AICharacter.is_active == True)
            .order_by(AICharacter.updated_at.desc())
        )
    ).scalars().first()
    if char is None:
        raise HTTPException(404, tr_lang(lang, "no_character"))
    return char.id


async def _read_holder(db, character_id: int):
    char = await db.get(AICharacter, character_id)
    st = (
        await db.execute(select(LifeState).where(LifeState.character_id == character_id))
    ).scalar_one_or_none()
    if st is None:
        st = LifeState(character_id=character_id)
        db.add(st)
    cs = (
        await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
    ).scalar_one_or_none()
    if cs is None:
        cs = CharacterState(character_id=character_id)
        db.add(cs)
    needs = {}
    if st.needs_json:
        try:
            needs = json.loads(st.needs_json)
        except Exception:
            needs = {}
    if "food" not in needs:
        needs["food"] = 70
    return char, st, cs, needs


@router.get("/state")
async def home_state(character_id: int = 0, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    async with async_session_factory() as db:
        cid = await _resolve_character(db, user_id, character_id, lang)
        char, st, cs, needs = await _read_holder(db, cid)
        if char is None or char.user_id != user_id:
            raise HTTPException(404, tr_lang(lang, "character_not_found"))
        pets = (
            await db.execute(select(Pet).where(Pet.user_id == user_id))
        ).scalars().all()
        return {
            "character_id": cid,
            "character_name": char.name,
            "player": {
                "stamina": _clamp(st.energy or 70),
                "mood": _clamp(cs.mood or 50),
                "hunger": _clamp(needs.get("food", 70)),
            },
            "ai": {
                "name": char.name,
                "current_status": char.current_status or "正在家里",
            },
            "current_room": "living",
            "rooms": [
                {
                    "id": rid,
                    "name": r["name"],
                    "furniture": r["furniture"],
                    "doors": r["doors"],
                }
                for rid, r in ROOMS_LAYOUT.items()
            ],
            "pets": [
                {"id": p.id, "name": p.name, "species": p.species,
                 "hunger": p.hunger, "mood": p.mood, "energy": p.energy}
                for p in pets
            ],
        }


@router.post("/event")
async def home_event(payload: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    character_id = int(payload.get("character_id") or 0)
    action = str(payload.get("action") or "").strip()

    # 宠物互动（独立于生活动作）
    if action in PET_ACTIONS:
        spec = PET_ACTIONS[action]
        pet_id = int(payload.get("pet_id") or 0)
        async with async_session_factory() as db:
            cid = await _resolve_character(db, user_id, character_id, lang)
            pet = (await db.execute(
                select(Pet).where(Pet.id == pet_id, Pet.user_id == user_id)
            )).scalar_one_or_none()
            if pet is None:
                raise HTTPException(404, tr_lang(lang, "pet_not_found"))
            from app.services import pet_service
            await pet_service.interact_by(
                db, pet, spec["pet_action"], user_id, actor="user"
            )
            return {"ok": True, "action": action, "label": spec["label"], "pet": {
                "id": pet.id, "name": pet.name, "species": pet.species,
                "hunger": pet.hunger, "mood": pet.mood, "energy": pet.energy,
                "cleanliness": pet.cleanliness,
            }}

    if action not in ACTIONS:
        raise HTTPException(400, tr_lang(lang, "unsupported_action"))
    spec = ACTIONS[action]
    async with async_session_factory() as db:
        cid = await _resolve_character(db, user_id, character_id, lang)
        char, st, cs, needs = await _read_holder(db, cid)
        if char is None or char.user_id != user_id:
            raise HTTPException(404, tr_lang(lang, "character_not_found"))
        st.energy = _clamp((st.energy or 70) + spec["stamina"])
        cs.mood = _clamp((cs.mood or 50) + spec["mood"])
        needs["food"] = _clamp(needs.get("food", 70) + spec["hunger"])
        st.needs_json = json.dumps(needs, ensure_ascii=False)
        await db.commit()
        db.add(
            LifeActivityLog(
                character_id=cid,
                activity_type=spec["activity"],
                status="completed",
                input_json=json.dumps({"source": "life_home", "action": action, "label": spec["label"]}, ensure_ascii=False),
                energy_cost=abs(spec["stamina"]) if spec["stamina"] < 0 else 0,
                mood_delta=spec["mood"],
            )
        )
        await db.commit()
        return {
            "ok": True,
            "action": action,
            "label": spec["label"],
            "player": {
                "stamina": st.energy,
                "mood": cs.mood,
                "hunger": int(needs.get("food", 70)),
            },
        }