"""表情包服务：内置表情包数据 + 用户下载记录（表情为 emoji 文本，无需图片素材）"""
from sqlalchemy import select, delete

from app.db.database import async_session_factory
from app.models.emoji_pack import UserEmojiPack, UserCustomEmoji
from app.utils.logger import get_logger

_logger = get_logger("services.emoji")

# 内置表情包：id/名称/描述/emoji 列表（emoji+名称，发送时 emoji 文本直接进消息与 AI 上下文）
EMOJI_PACKS: list[dict] = [
    {
        "id": "daily", "name": "日常心情", "description": "常用情绪表情，聊天必备",
        "builtin": True,
        "emojis": [
            {"emoji": "😊", "name": "开心"}, {"emoji": "😂", "name": "笑死"},
            {"emoji": "😭", "name": "大哭"}, {"emoji": "😡", "name": "生气"},
            {"emoji": "😳", "name": "害羞"}, {"emoji": "😴", "name": "困了"},
            {"emoji": "🤔", "name": "思考"}, {"emoji": "🥰", "name": "爱你"},
            {"emoji": "😘", "name": "亲亲"}, {"emoji": "😤", "name": "哼"},
            {"emoji": "🙄", "name": "无语"}, {"emoji": "😱", "name": "惊吓"},
            {"emoji": "🥺", "name": "委屈"}, {"emoji": "😋", "name": "馋"},
            {"emoji": "🤗", "name": "抱抱"}, {"emoji": "💔", "name": "心碎"},
        ],
    },
    {
        "id": "cats", "name": "猫猫", "description": "猫猫表情，可爱暴击",
        "builtin": False,
        "emojis": [
            {"emoji": "🐱", "name": "猫猫"}, {"emoji": "😸", "name": "猫猫笑"},
            {"emoji": "😹", "name": "猫猫笑哭"}, {"emoji": "😻", "name": "猫猫爱"},
            {"emoji": "😼", "name": "猫猫坏笑"}, {"emoji": "🙀", "name": "猫猫震惊"},
            {"emoji": "🐈", "name": "猫猫路过"}, {"emoji": "🐾", "name": "爪印"},
        ],
    },
    {
        "id": "foods", "name": "美食", "description": "吃吃喝喝，深夜放毒",
        "builtin": False,
        "emojis": [
            {"emoji": "🍚", "name": "吃饭"}, {"emoji": "🍜", "name": "吃面"},
            {"emoji": "🍰", "name": "蛋糕"}, {"emoji": "🍎", "name": "苹果"},
            {"emoji": "🍗", "name": "鸡腿"}, {"emoji": "☕", "name": "咖啡"},
            {"emoji": "🍺", "name": "干杯"}, {"emoji": "🍡", "name": "甜点"},
        ],
    },
    {
        "id": "love", "name": "恋爱", "description": "和 TA 的甜蜜暗号",
        "builtin": False,
        "emojis": [
            {"emoji": "❤️", "name": "爱心"}, {"emoji": "💕", "name": "心动"},
            {"emoji": "💋", "name": "亲亲"}, {"emoji": "🌹", "name": "玫瑰"},
            {"emoji": "💍", "name": "戒指"}, {"emoji": "🫂", "name": "抱抱"},
            {"emoji": "🌙", "name": "晚安"}, {"emoji": "☀️", "name": "早安"},
        ],
    },
]


async def _downloaded_ids(user_id: int) -> set[str]:
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(UserEmojiPack.pack_id).where(UserEmojiPack.user_id == user_id)
            )).scalars().all()
        return set(rows)
    except Exception as e:
        _logger.warning("Emoji packs query failed user=%d: %s", user_id, e)
        return set()


async def list_packs(user_id: int) -> list[dict]:
    """包列表（含已下载标记；builtin 包默认已下载）"""
    downloaded = await _downloaded_ids(user_id)
    return [
        {
            "id": p["id"], "name": p["name"], "description": p["description"],
            "downloaded": p.get("builtin") or p["id"] in downloaded,
            # 表情明细全量返回（emoji 为纯文本，无隐私/体积问题）；downloaded 仅作管理标记
            "emojis": p["emojis"],
        }
        for p in EMOJI_PACKS
    ]


async def download_pack(user_id: int, pack_id: str) -> bool:
    """下载表情包（写用户下载记录，幂等）"""
    pack = next((p for p in EMOJI_PACKS if p["id"] == pack_id), None)
    if pack is None:
        return False
    try:
        async with async_session_factory() as db:
            exists = (await db.execute(
                select(UserEmojiPack).where(
                    UserEmojiPack.user_id == user_id, UserEmojiPack.pack_id == pack_id
                )
            )).scalar_one_or_none()
            if exists is None:
                db.add(UserEmojiPack(user_id=user_id, pack_id=pack_id, pack_name=pack["name"]))
                await db.commit()
        return True
    except Exception as e:
        _logger.warning("Emoji pack download failed user=%d pack=%s: %s", user_id, pack_id, e)
        return False


async def remove_pack(user_id: int, pack_id: str) -> bool:
    """删除已下载表情包（builtin 包不可删除）"""
    pack = next((p for p in EMOJI_PACKS if p["id"] == pack_id), None)
    if pack is None or pack.get("builtin"):
        return False
    try:
        async with async_session_factory() as db:
            await db.execute(delete(UserEmojiPack).where(
                UserEmojiPack.user_id == user_id, UserEmojiPack.pack_id == pack_id
            ))
            await db.commit()
        return True
    except Exception as e:
        _logger.warning("Emoji pack remove failed user=%d pack=%s: %s", user_id, pack_id, e)
        return False


# ── 自定义表情（用户上传图片，个人使用）──

async def list_custom_emojis(user_id: int) -> list[dict]:
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(UserCustomEmoji)
                .where(UserCustomEmoji.user_id == user_id)
                .order_by(UserCustomEmoji.id.desc())
            )).scalars().all()
        return [
            {"id": r.id, "name": r.name, "url": r.url, "created_at": r.created_at.isoformat()}
            for r in rows
        ]
    except Exception as e:
        _logger.warning("Custom emoji list failed user=%d: %s", user_id, e)
        return []


async def add_custom_emoji(user_id: int, name: str, url: str) -> dict | None:
    try:
        async with async_session_factory() as db:
            row = UserCustomEmoji(user_id=user_id, name=(name or "表情")[:30], url=url)
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return {"id": row.id, "name": row.name, "url": row.url, "created_at": row.created_at.isoformat()}
    except Exception as e:
        _logger.warning("Custom emoji add failed user=%d: %s", user_id, e)
        return None


async def delete_custom_emoji(user_id: int, emoji_id: int) -> bool:
    """删除自定义表情（行 + 磁盘文件），仅限本人"""
    from app.services.upload_service import UPLOAD_DIR
    try:
        async with async_session_factory() as db:
            row = (await db.execute(
                select(UserCustomEmoji).where(
                    UserCustomEmoji.id == emoji_id, UserCustomEmoji.user_id == user_id
                )
            )).scalar_one_or_none()
            if row is None:
                return False
            url = row.url
            await db.delete(row)
            await db.commit()
        try:
            rel = url.removeprefix("/uploads/").lstrip("/")
            if rel and ".." not in rel:
                p = UPLOAD_DIR / rel
                if p.exists() and p.is_file():
                    p.unlink()
        except Exception:
            pass
        return True
    except Exception as e:
        _logger.warning("Custom emoji delete failed user=%d id=%s: %s", user_id, emoji_id, e)
        return False
