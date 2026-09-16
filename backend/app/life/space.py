"""AI Life 空间模型（批次三 P0-5 止血，2026-09-16）：支持住校现实。

问题：life_states 永远是 location=home / current_room=bedroom，活动 room 只有
bedroom/living/kitchen；而用户住校（宿舍无厨房），角色一边私聊说
「去食堂垫两口」一边在系统里炖肉（eat→kitchen 做饭）。

止血策略（改动尽量小，不引入新表/新服务）：
- 地点/房间枚举扩展为可表达 宿舍 / 教学楼 / 食堂 / 图书馆 / 家（东莞）。
- 纯函数推导「当前应处的空间」「该空间有哪些房间」「是否有厨房」，由 life_loop 调用，
  让住校角色白天在 campus/canteen/library、晚上回 dorm，而非永远 home/bedroom。
- ``room_for`` 做空间合法性归一：不在有厨房的空间（宿舍/教学楼/食堂/图书馆）不落
  kitchen，eat 落 canteen，杜绝「宿舍里炖肉」这种系统内自相矛盾的落库。

扩展点：``is_term_time`` 现默认 True（住校）；假期切换只需改此函数/配置，
home_base / sleep_location / student_day_location 会随之联动。
"""
from __future__ import annotations

from datetime import datetime

# 地点枚举（在原有 home/world/friend/outside/exit 基础上扩展）
LOCATIONS: tuple[str, ...] = (
    "home",    # 东莞家（假期）
    "dorm",    # 宿舍（住校）
    "campus",  # 教学楼
    "canteen", # 食堂
    "library", # 图书馆
    "world", "friend", "outside", "exit",
)

# 房间枚举（在原有 bedroom/living/kitchen/bathroom/exit 基础上扩展）
ROOMS: tuple[str, ...] = (
    "bedroom", "living", "kitchen", "bathroom", "exit",
    "classroom", "canteen", "library",
)

# 「本地基地」地点：人在这些地点视为在住处/校园内（可做室内活动），
# 其余（world/friend/outside/exit）视为外出，需门控 / 回家。
AWAY_LOCATIONS: tuple[str, ...] = ("world", "friend", "outside", "exit")
BASE_LOCATIONS: tuple[str, ...] = ("home", "dorm", "campus", "canteen", "library")

# 各地点可用的房间（白名单；越界房间由 room_for 归一）
_ROOMS_BY_LOCATION: dict[str, frozenset[str]] = {
    "home": frozenset({"bedroom", "living", "kitchen", "bathroom", "exit"}),
    "dorm": frozenset({"bedroom", "exit"}),
    "campus": frozenset({"classroom", "exit"}),
    "canteen": frozenset({"canteen", "exit"}),
    "library": frozenset({"library", "exit"}),
    "world": frozenset({"exit"}),
    "friend": frozenset({"exit"}),
    "outside": frozenset({"exit"}),
    "exit": frozenset({"exit"}),
}

# 各地点默认房间（越界房间归一落点）
_DEFAULT_ROOM: dict[str, str] = {
    "home": "living", "dorm": "bedroom", "campus": "classroom",
    "canteen": "canteen", "library": "library", "world": "exit",
    "friend": "exit", "outside": "exit", "exit": "exit",
}

# 有厨房（可做饭）的地点：仅东莞家；宿舍无厨房（住校现实）
_KITCHEN_LOCATIONS: tuple[str, ...] = ("home",)

# 地点中文标签（内容生成/话术约束用）
SPACE_LABELS: dict[str, str] = {
    "home": "东莞的家里（有厨房）",
    "dorm": "学校宿舍（没有厨房）",
    "campus": "教学楼",
    "canteen": "食堂",
    "library": "图书馆",
    "world": "外面",
    "friend": "朋友那里",
    "outside": "外面",
    "exit": "门口",
}


def has_kitchen(location: str | None) -> bool:
    """该地点是否有厨房（可做饭）。宿舍/教学楼/食堂/图书馆均无。"""
    return (location or "") in _KITCHEN_LOCATIONS


def is_away(location: str | None) -> bool:
    """是否处于「外出」地点（world/friend/outside/exit）——decision 外出型门控用。

    空值/未知值按「不在外」处理（保守：不误触发强制回家）。
    """
    return (location or "") in AWAY_LOCATIONS


def at_base(location: str | None) -> bool:
    """是否处于本地基地地点（住处/校园内）——life_loop 按作息改写所在空间的前置条件。"""
    return (location or "") in BASE_LOCATIONS


def space_label(location: str | None) -> str:
    """地点中文标签（缺省按「自己的住处」）。"""
    return SPACE_LABELS.get(location or "", "自己的住处")


def room_for(location: str | None, requested_room: str | None = None,
             action: str | None = None) -> str:
    """把动作请求的房间归一到该地点真实存在的房间。

    - 房间合法 → 原样保留；
    - 请求 kitchen 但该地点无厨房：eat → 食堂（去食堂吃饭），其余 → 该地点默认房间；
    - 其它越界房间 → 该地点默认房间（如图书馆里不会出现 bedroom）。
    """
    loc = location or "home"
    allowed = _ROOMS_BY_LOCATION.get(loc)
    if allowed is None:
        return requested_room or "living"
    if requested_room and requested_room in allowed:
        return requested_room
    if requested_room == "kitchen" and not has_kitchen(loc) and action == "eat":
        return "canteen"
    return _DEFAULT_ROOM.get(loc, "living")


def normalize_location(location: str | None) -> str:
    """决策器的字面落点归一：``home`` 按住校/假期改写为真实住处（dorm / home）。"""
    if (location or "") == "home":
        return home_base()[0]
    return location or "home"


# ── 住校/假期判定 ──────────────────────────────────────────
# 批次三止血阶段：默认住校（term=True）。假期切换改这里即可全局联动。
_TERM_TIME_DEFAULT = True


def is_term_time(now: datetime | None = None) -> bool:
    """是否为住校学期内（决定住在宿舍还是东莞家）。

    默认 True（住校）。后续接入真实校历/用户设定时，按日期返回即可。
    """
    return _TERM_TIME_DEFAULT


def home_base(now: datetime | None = None) -> tuple[str, str]:
    """角色「回到的家」：住校 → 宿舍；假期 → 东莞家。"""
    if is_term_time(now):
        return "dorm", "bedroom"
    return "home", "bedroom"


def sleep_location(now: datetime | None = None) -> tuple[str, str]:
    """夜间睡眠落点：住校 → 宿舍卧室；假期 → 东莞家卧室。"""
    return home_base(now)


def student_day_location(phase: str, hour: int,
                         now: datetime | None = None) -> tuple[str, str]:
    """按作息推导住校角色白天所在空间（仅 term 生效；假期回 home）。

    - 睡眠段(23-7)：宿舍卧室
    - 07-08：宿舍（起床/洗漱）
    - 08-12：教学楼（上课/自习）
    - 12-14：食堂（午饭/午休）
    - 14-18：教学楼（上课/自习）
    - 18-23：图书馆（晚自习）
    """
    if not is_term_time(now):
        return "home", "bedroom"
    if phase == "sleep" or hour < 7 or hour >= 23:
        return "dorm", "bedroom"
    if hour < 8:
        return "dorm", "bedroom"
    if hour < 12:
        return "campus", "classroom"
    if hour < 14:
        return "canteen", "canteen"
    if hour < 18:
        return "campus", "classroom"
    return "library", "library"


def space_guard(location: str | None) -> str:
    """内容生成用的空间约束提示（注入活动 prompt，防「宿舍里炖肉/做好饭等你回家」）。"""
    return (
        f"你现在在{space_label(location)}，写下的内容必须与这个空间相符："
        "没有厨房的地方（宿舍/教学楼/食堂/图书馆）不要写做饭、炖汤、下厨；"
        "也不要写「做好饭等你回家」这类同住话术。"
    )
