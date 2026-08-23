"""示例插件：晚安话题（proactive_candidate + action + send_message 演示）

每晚 enabled_hours 时段（默认 22-23 点）为每个启用角色提供一次晚安候选，
arbiter 执行 action=send_goodnight → sdk.send_message 向用户发送晚安消息。
每角色每日 1 条（进程内存节流，重启后重置，示例可接受）。
"""
import datetime
import random

from app.plugins import sdk

GOOD_NIGHT_LINES = [
    "今天辛苦啦，早点休息，梦里见。",
    "夜已经深了，把烦恼都留给今天吧。晚安，明天见。",
    "晚安！记得盖好被子，做个好梦。",
    "忙了一整天，也该好好睡一觉了。晚安。",
]

# 已发送的 (日期, 角色id) 集合，节流每日限额
_sent_keys: set[str] = set()


def _day_key(character_id: int) -> str:
    return f"{datetime.date.today().isoformat()}:{character_id}"


def _is_night_hour() -> bool:
    try:
        cfg = sdk.get_config()
        start_h, end_h = (int(x) for x in str(cfg.get("enabled_hours", "22-23")).split("-"))
    except Exception:
        start_h, end_h = 22, 23
    return start_h <= datetime.datetime.now().hour < end_h


@sdk.hook("proactive_candidate")
async def goodnight_candidate(ctx):
    """22-23 点为所有启用角色提供晚安候选（带 session_id，arbiter 执行 action）"""
    if not _is_night_hour():
        return None
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.character import AICharacter
        from app.services.chat_service import get_latest_session_id

        async with async_session_factory() as db:
            rows = (await db.execute(
                select(AICharacter).where(AICharacter.is_active == True)
            )).scalars().all()
        out = []
        for char in rows:
            uid = char.user_id or 1
            if _day_key(char.id) in _sent_keys:
                continue
            try:
                sid = await get_latest_session_id(uid, char.id)
            except Exception:
                sid = None
            if not sid:
                continue
            out.append({
                "character_id": char.id,
                "user_id": uid,
                "session_id": sid,
                "action": "send_goodnight",
                "hint": "向用户道晚安",
            })
        return out or None
    except Exception as e:
        sdk.log("goodnight candidate 异常: %s", e)
        return None


@sdk.action("send_goodnight")
async def send_goodnight(payload):
    """arbiter 执行：给用户发送一条晚安消息（每日 1 条/角色）"""
    cid = payload.get("character_id")
    uid = payload.get("user_id")
    if not cid or not uid:
        return False
    key = _day_key(int(cid))
    if key in _sent_keys:
        return False
    line = random.choice(GOOD_NIGHT_LINES)
    ok = await sdk.send_message(int(cid), int(uid), line)
    if ok:
        _sent_keys.add(key)
        sdk.log("晚安已发送 char=%s: %s", cid, line[:20])
    return bool(ok)
