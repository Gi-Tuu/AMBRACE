"""生活可视·小家 API（v3.3）：多房间像素家居 + 交互事件 + 宠物互动

- GET  /api/v1/life-home/state   读取小家状态（角色名/用户昵称/恋人/体力/心情/饥饿/房间布局/宠物）
- PUT  /api/v1/life-home/layout  保存自定义房间布局（家具位置/尺寸/朝向 rotation 0-7）
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
from app.models.character import CharacterState
from app.models.life import LifeState, LifeActivityLog
from app.models.pet import Pet
from app.models.user import User
from app.games.registry import list_games

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

# ── 小家大地图 v1.1（2026-08-26）：房间世界坐标 + 邻接 + 出口 ──
# 世界坐标系：每房间 ROOM_W×ROOM_H 格，origin 为房间左上角在世界空间的位置
WORLD_LAYOUT = {
    "room_origins": {
        "living":   {"wx": 0,   "wy": 0},
        "bedroom":  {"wx": 16,  "wy": 0},    # 卧室在客厅东侧
        "kitchen":  {"wx": 0,   "wy": 12},   # 厨房在客厅南侧
        "bathroom": {"wx": 16,  "wy": 12},   # 浴室在东南
    },
    "adjacency": [
        {"from": "living", "to": "bedroom",  "door_type": "wall_gap", "side": "east"},
        {"from": "living", "to": "kitchen",  "door_type": "wall_gap", "side": "south"},
        {"from": "living", "to": "bathroom", "door_type": "wall_gap", "side": "southeast"},
    ],
    "exit": {"room": "living", "side": "west", "x": 0, "y": 6},
}

# 房间尺寸（格）
ROOM_W, ROOM_H = 16, 12

# ── 小家 v3.2 家具自由摆放：角色自定义布局（home_layout_json）────
# 自定义布局只影响家具位置/尺寸；房间 id/name/doors 一律保持默认（doors 不持久化）。
_LAYOUT_MAX_BYTES = 50 * 1024      # 布局 JSON 体积上限 50KB
_LAYOUT_MAX_FURNITURE = 30         # 每房间家具数量上限
_LAYOUT_MAX_COORD = 16.0           # gx/gy 范围 0-16 格（可小数，自由摆放）
_LAYOUT_MIN_SIZE = 0.5             # gw/gh 合理范围 0.5-4 格
_LAYOUT_MAX_SIZE = 4.0

# 房间 → 合法家具 key 集合（校验：新增家具 key 一律拒绝）
_ROOM_FURN_KEYS: dict[str, set[str]] = {
    rid: {f["key"] for f in r["furniture"]} for rid, r in ROOMS_LAYOUT.items()
}


def _is_num(v) -> bool:
    """数值校验（bool 是 int 子类，需排除）"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parse_home_layout(raw: str | None) -> dict:
    """解析角色自定义布局 JSON → {rid: [家具位置尺寸]}

    结构非法/字段越界/未知房间/未知家具一律丢弃该项（回退默认），不会整包拒绝。
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list] = {}
    for rid, room in data.items():
        if rid not in ROOMS_LAYOUT or not isinstance(room, dict):
            continue
        furn = room.get("furniture")
        if not isinstance(furn, list):
            continue
        items = []
        for f in furn:
            if not isinstance(f, dict) or f.get("key") not in _ROOM_FURN_KEYS[rid]:
                continue
            key = f["key"]
            gx, gy, gw, gh = f.get("gx"), f.get("gy"), f.get("gw"), f.get("gh")
            if not all(_is_num(v) for v in (gx, gy, gw, gh)):
                continue
            if not (0 <= gx <= _LAYOUT_MAX_COORD and 0 <= gy <= _LAYOUT_MAX_COORD):
                continue
            if not (_LAYOUT_MIN_SIZE <= gw <= _LAYOUT_MAX_SIZE and _LAYOUT_MIN_SIZE <= gh <= _LAYOUT_MAX_SIZE):
                continue
            rot = f.get("rotation", 0)
            if not (isinstance(rot, int) and not isinstance(rot, bool)) or not (0 <= rot <= 7):
                continue
            items.append({"key": key, "gx": float(gx), "gy": float(gy), "gw": float(gw), "gh": float(gh), "rotation": int(rot)})
        if items:
            out[rid] = items
    return out


def _build_rooms(custom: dict) -> list[dict]:
    """合并默认房间与自定义布局：仅覆盖家具位置/尺寸/朝向；id/name/doors 保持默认"""
    rooms = []
    for rid, r in ROOMS_LAYOUT.items():
        by_key = {f["key"]: f for f in custom.get(rid, [])}
        furniture = []
        for f in r["furniture"]:
            c = by_key.get(f["key"])
            if c is None:
                furniture.append({**f, "rotation": 0})
            else:
                furniture.append({**f, "gx": c["gx"], "gy": c["gy"],
                                  "gw": c["gw"], "gh": c["gh"], "rotation": c.get("rotation", 0)})
        rooms.append({"id": rid, "name": r["name"], "furniture": furniture, "doors": r["doors"]})
    return rooms


def _lmsg(lang: str, zh: str, en: str) -> str:
    """布局校验错误消息（不引入新 i18n key，就地双语）"""
    return en if lang.strip().lower().startswith("en") else zh


# 恋人判定关键词（relation_type 兜底；关系网「我的对象」权威字段 is_partner 优先）
_LOVER_KEYWORDS = ("恋人", "对象", "伴侣", "男朋友", "女朋友", "男友", "女友", "老公", "老婆")


def _lover_name(char) -> str | None:
    """当前角色是否为用户的恋人（对象）：is_partner 优先，relation_type 含关键词兜底。

    relationship_summary 为自由文本（AI 生成），不作判定依据。
    """
    if char.is_partner:
        return char.name
    rt = char.relation_type or ""
    if any(k in rt for k in _LOVER_KEYWORDS):
        return char.name
    return None


async def _is_admin(user_id: int) -> bool:
    from app.application.permission_service import is_admin_user
    return await is_admin_user(user_id)


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
    "game":     {"stamina": -4,  "mood": 12, "hunger": -3, "activity": "rest",             "label": "玩游戏"},  # 群聊游戏 Phase 1：点击改开游戏面板，不再直接体力加成（保留此条目以防回退）
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
        user = await db.get(User, user_id)

        # 小家大地图 v1.1（2026-08-26）：world 载荷（flag 开启时返回，否则 null 保持向后兼容）
        flag_world = False
        try:
            from app.agent.loop import AGENT_FLAGS
            flag_world = AGENT_FLAGS.get("life_home_worldmap_enabled", False)
        except Exception:
            pass

        world_payload = None
        if flag_world:
            room = st.current_room or "living"
            origin = WORLD_LAYOUT["room_origins"].get(room, WORLD_LAYOUT["room_origins"]["living"])
            world_payload = {
                "room_origins": WORLD_LAYOUT["room_origins"],
                "adjacency": WORLD_LAYOUT["adjacency"],
                "exit": WORLD_LAYOUT["exit"],
                "room_size": {"w": ROOM_W, "h": ROOM_H},
                "character": {
                    "room": room,
                    "location": st.location or "home",
                    "wx": origin["wx"] + ROOM_W / 2,
                    "wy": origin["wy"] + ROOM_H / 2,
                },
            }

        return {
            "character_id": cid,
            "character_name": char.name,
            "user": {"id": user.id, "nickname": user.nickname} if user else None,
            "lover_name": _lover_name(char),
            "player": {
                "stamina": _clamp(st.energy or 70),
                "mood": _clamp(cs.mood or 50),
                "hunger": _clamp(needs.get("food", 70)),
            },
            "ai": {
                "name": char.name,
                "current_status": char.current_status or "正在家里",
            },
            "current_room": st.current_room or "living",
            "location": st.location or "home",
            "rooms": _build_rooms(_parse_home_layout(st.home_layout_json)),
            "world": world_payload,  # null=前端回退旧独立房间视图
            "pets": [
                {"id": p.id, "name": p.name, "species": p.species,
                 "hunger": p.hunger, "mood": p.mood, "energy": p.energy}
                for p in pets
            ],
        }


@router.put("/layout")
async def save_home_layout(payload: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """保存角色自定义房间布局（家具自由摆放 + 朝向，v3.3）

    body: {character_id, rooms: [{id, name, furniture: [{key, name, gx, gy, gw, gh, rotation, action}], doors}]}
    - 仅保存家具位置/尺寸/朝向；房间 id/name/doors 保持默认（doors 忽略不持久化）
    - 校验：房间 id 与家具 key 必须在默认布局内（新增拒绝）、gx/gy ∈ [0,16] 可小数、
      gw/gh ∈ [0.5,4]、rotation ∈ [0,7]（0=前 1=后 2=左 3=右，斜向 4-7 预留）、
      每房间家具 ≤30、整包 JSON ≤50KB
    - 仅主账号或角色 owner 可保存
    """
    character_id = int(payload.get("character_id") or 0)
    rooms = payload.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        raise HTTPException(400, _lmsg(lang, "房间布局数据无效", "Invalid room layout data"))
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _LAYOUT_MAX_BYTES:
        raise HTTPException(400, _lmsg(lang, "布局数据过大（超过 50KB）", "Layout data too large (over 50KB)"))

    async with async_session_factory() as db:
        cid = await _resolve_character(db, user_id, character_id, lang)
        char, st, cs, needs = await _read_holder(db, cid)
        if char is None or (char.user_id != user_id and not await _is_admin(user_id)):
            raise HTTPException(404, tr_lang(lang, "character_not_found"))

        custom: dict[str, dict] = {}
        for room in rooms:
            if not isinstance(room, dict):
                raise HTTPException(400, _lmsg(lang, "房间数据无效", "Invalid room data"))
            rid = room.get("id")
            if rid not in ROOMS_LAYOUT:
                raise HTTPException(400, _lmsg(lang, f"未知房间: {rid}", f"Unknown room: {rid}"))
            furn_list = room.get("furniture")
            if not isinstance(furn_list, list):
                raise HTTPException(400, _lmsg(lang, f"房间 {rid} 家具数据无效", f"Invalid furniture data in room {rid}"))
            if len(furn_list) > _LAYOUT_MAX_FURNITURE:
                raise HTTPException(
                    400, _lmsg(lang, f"房间 {rid} 家具数量超过 {_LAYOUT_MAX_FURNITURE} 个上限",
                               f"Room {rid} has more than {_LAYOUT_MAX_FURNITURE} furniture items"))
            items = []
            for f in furn_list:
                if not isinstance(f, dict) or f.get("key") not in _ROOM_FURN_KEYS[rid]:
                    raise HTTPException(400, _lmsg(lang, f"房间 {rid} 包含未知家具", f"Room {rid} contains unknown furniture"))
                key = f["key"]
                gx, gy, gw, gh = f.get("gx"), f.get("gy"), f.get("gw"), f.get("gh")
                if not all(_is_num(v) for v in (gx, gy, gw, gh)):
                    raise HTTPException(400, _lmsg(lang, f"家具 {key} 坐标或尺寸不是数字", f"Furniture {key} position/size must be numeric"))
                if not (0 <= gx <= _LAYOUT_MAX_COORD and 0 <= gy <= _LAYOUT_MAX_COORD):
                    raise HTTPException(400, _lmsg(lang, f"家具 {key} 坐标超出范围(0-16)", f"Furniture {key} position out of range (0-16)"))
                if not (_LAYOUT_MIN_SIZE <= gw <= _LAYOUT_MAX_SIZE and _LAYOUT_MIN_SIZE <= gh <= _LAYOUT_MAX_SIZE):
                    raise HTTPException(400, _lmsg(lang, f"家具 {key} 尺寸超出范围(0.5-4)", f"Furniture {key} size out of range (0.5-4)"))
                rot = f.get("rotation", 0)
                if not (isinstance(rot, int) and not isinstance(rot, bool)):
                    raise HTTPException(400, _lmsg(lang, f"家具 {key} rotation 必须为整数", f"Furniture {key} rotation must be an integer"))
                if not (0 <= rot <= 7):
                    raise HTTPException(400, _lmsg(lang, f"家具 {key} rotation 超出范围(0-7)", f"Furniture {key} rotation out of range (0-7)"))
                items.append({"key": key, "gx": float(gx), "gy": float(gy), "gw": float(gw), "gh": float(gh), "rotation": int(rot)})
            custom[rid] = {"furniture": items}

        st.home_layout_json = json.dumps(custom, ensure_ascii=False)
        await db.commit()
        return {"saved": True}


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
            from app.application import pet_service
            await pet_service.interact_by(
                db, pet, spec["pet_action"], user_id, actor="user"
            )
            return {"ok": True, "action": action, "label": spec["label"], "pet": {
                "id": pet.id, "name": pet.name, "species": pet.species,
                "hunger": pet.hunger, "mood": pet.mood, "energy": pet.energy,
                "cleanliness": pet.cleanliness,
            }}

    # 游戏机：打开游戏面板（不再是简单体力加成）
    if action == "game":
        return {
            "ok": True,
            "action": "game",
            "open_panel": "game_console",  # 前端据此打开游戏面板
            "catalog": list_games(),       # 游戏目录
        }

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