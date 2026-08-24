"""AI 能力权限服务：三档权限（allow/ask/forbid）+ 待确认动作管理（2026-08-12）

模型：用户级全局默认档位 + 每能力例外；实际权限 = 能力例外优先，无例外跟随全局默认。
能力清单：image_gen(生图)/image_understand(识图)/tts(语音回复)/asr(语音转写)/
        browser(浏览器扩展)/douyin(抖音扩展)/extension(其他扩展)。
"""
import json
from app.utils.logger import get_logger
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.tool_permission import PendingPermissionAction, ToolPermission

_logger = get_logger("permission")

SCOPE_GLOBAL = "__global__"
SCOPE_IMAGE_GEN = "image_gen"
SCOPE_IMAGE_UNDERSTAND = "image_understand"
SCOPE_TTS = "tts"
SCOPE_ASR = "asr"
SCOPE_BROWSER = "browser"
SCOPE_DOUYIN = "douyin"
SCOPE_EXTENSION = "extension"

SCOPES = [
    SCOPE_IMAGE_GEN,
    SCOPE_IMAGE_UNDERSTAND,
    SCOPE_TTS,
    SCOPE_ASR,
    SCOPE_BROWSER,
    SCOPE_DOUYIN,
    SCOPE_EXTENSION,
]

SCOPE_LABELS = {
    SCOPE_IMAGE_GEN: "生图",
    SCOPE_IMAGE_UNDERSTAND: "识图",
    SCOPE_TTS: "语音回复",
    SCOPE_ASR: "语音转写",
    SCOPE_BROWSER: "浏览器",
    SCOPE_DOUYIN: "抖音",
    SCOPE_EXTENSION: "扩展",
}

SCOPE_DESCRIPTIONS = {
    SCOPE_IMAGE_GEN: "AI 生成图片发给你（聊天内发图/主动生图）",
    SCOPE_IMAGE_UNDERSTAND: "AI 理解你发来的图片内容（本地识图）",
    SCOPE_TTS: "AI 用语音回复你（TTS 合成）",
    SCOPE_ASR: "转写你的语音消息（ASR 识别）",
    SCOPE_BROWSER: "浏览器扩展：AI 搜索网页、读取页面",
    SCOPE_DOUYIN: "抖音扩展：发布图文、回复评论",
    SCOPE_EXTENSION: "其他扩展/插件的能力调用",
}

LEVELS = ("allow", "ask", "forbid")

LEVEL_LABELS = {"allow": "允许", "ask": "每次询问", "forbid": "禁止"}

DEFAULT_GLOBAL_LEVEL = "allow"  # 默认全局 = 允许（保持现有体验），用户可收紧


def _plugin_scope(plugin: str) -> str:
    """插件名 → 能力 scope 映射（browser/douyin 特判，其余归 extension）"""
    name = (plugin or "").lower()
    if "browser" in name or "brows" in name:
        return SCOPE_BROWSER
    if "douyin" in name or "tiktok" in name:
        return SCOPE_DOUYIN
    return SCOPE_EXTENSION


async def _get_rows(user_id: int) -> dict[str, str]:
    async with async_session_factory() as db:
        rows = (
            await db.execute(select(ToolPermission).where(ToolPermission.user_id == user_id))
        ).scalars().all()
    return {r.scope: r.level for r in rows}


async def get_global_level(user_id: int) -> str:
    """全局默认档位（无配置 = allow）"""
    rows = await _get_rows(user_id)
    return rows.get(SCOPE_GLOBAL, DEFAULT_GLOBAL_LEVEL)


async def get_scope_level(user_id: int, scope: str) -> str:
    """能力档位：例外优先，否则全局默认"""
    rows = await _get_rows(user_id)
    if scope in rows:
        return rows[scope]
    return rows.get(SCOPE_GLOBAL, DEFAULT_GLOBAL_LEVEL)


async def get_all_levels(user_id: int) -> dict:
    """设置页数据：全局默认 + 每能力档位（未配置的能力显示当前生效值）"""
    rows = await _get_rows(user_id)
    global_level = rows.get(SCOPE_GLOBAL, DEFAULT_GLOBAL_LEVEL)
    scopes = {s: rows.get(s, global_level) for s in SCOPES}
    return {"global_level": global_level, "scopes": scopes}


async def set_levels(user_id: int, global_level: str | None = None, scopes: dict | None = None) -> None:
    """批量 upsert 权限档位（仅接受合法档位）"""
    updates: list[tuple[str, str]] = []
    if global_level is not None and global_level in LEVELS:
        updates.append((SCOPE_GLOBAL, global_level))
    for scope, level in (scopes or {}).items():
        if scope in SCOPES and level in LEVELS:
            updates.append((scope, level))
    if not updates:
        return
    async with async_session_factory() as db:
        existing = {
            r.scope: r
            for r in (
                await db.execute(
                    select(ToolPermission).where(
                        ToolPermission.user_id == user_id,
                        ToolPermission.scope.in_([s for s, _ in updates]),
                    )
                )
            ).scalars().all()
        }
        for scope, level in updates:
            row = existing.get(scope)
            if row is None:
                db.add(ToolPermission(user_id=user_id, scope=scope, level=level))
            elif row.level != level:
                row.level = level
        await db.commit()
    _logger.info("permission set user=%d updates=%d", user_id, len(updates))


async def check_mode(user_id: int, scope: str) -> str:
    """能力当前生效模式：allow / ask / forbid"""
    return await get_scope_level(user_id, scope)


async def create_pending_action(
    user_id: int, session_id: int, character_id: int, scope: str, action: dict
) -> PendingPermissionAction:
    """权限=ask 时挂起动作，返回待确认记录"""
    async with async_session_factory() as db:
        row = PendingPermissionAction(
            user_id=user_id,
            session_id=session_id,
            character_id=character_id,
            scope=scope,
            action=json.dumps(action, ensure_ascii=False),
            status="pending",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    _logger.info("permission pending created user=%d scope=%s action_id=%d", user_id, scope, row.id)
    return row


async def resolve_pending_action(action_id: int, user_id: int, approve: bool) -> dict | None:
    """用户确认/拒绝待确认动作；返回动作 JSON（approve 时调用方执行），已处理返回 None"""
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(PendingPermissionAction).where(
                    PendingPermissionAction.id == action_id,
                    PendingPermissionAction.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None or row.status != "pending":
            return None
        row.status = "approved" if approve else "denied"
        row.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        action = json.loads(row.action or "{}")
        scope = row.scope
        session_id = row.session_id
        character_id = row.character_id
        await db.commit()
    _logger.info("permission action %s id=%d user=%d scope=%s", "approved" if approve else "denied", action_id, user_id, scope)
    if approve:
        return {
            "scope": scope,
            "user_id": user_id,
            "session_id": session_id,
            "character_id": character_id,
            **action,
        }
    return None


async def expire_pending_actions(max_age_minutes: int = 30) -> int:
    """清理超时未确认的挂起动作（标记 expired），返回清理数"""
    deadline = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=max_age_minutes)
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(PendingPermissionAction).where(
                    PendingPermissionAction.status == "pending",
                    PendingPermissionAction.created_at < deadline,
                )
            )
        ).scalars().all()
        for r in rows:
            r.status = "expired"
            r.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        return len(rows)


# ================= #46 主账号判定（选择型） =================
# 数据源：users.is_admin（DB 权威）；settings.admin_user_ids（env）仅作读取失败/用户不存在的兜底。
# 进程内短缓存（30s TTL）；设置/取消主账号时调用 _invalidate_admin_cache 立即失效。

import time as _time

from app.config import settings
from app.models.user import User

_ADMIN_CACHE_TTL = 30.0
_admin_cache: dict[int, tuple[bool, float]] = {}


def _invalidate_admin_cache() -> None:
    """主账号集合变更后失效缓存（设置/取消主账号时调用）"""
    _admin_cache.clear()


def _is_admin_fallback(user_id: int) -> bool:
    """DB 读取失败/用户不存在时的兜底：回退到 settings.admin_user_ids（env，与旧行为一致）"""
    return user_id in settings.admin_user_ids


async def _load_admin_from_db(user_id: int) -> bool:
    async with async_session_factory() as db:
        row = (await db.execute(select(User.is_admin).where(User.id == user_id))).first()
    if row is None:
        return _is_admin_fallback(user_id)
    return bool(row.is_admin)


async def is_admin_user(user_id: int) -> bool:
    """主账号判定（async）：读 users.is_admin + 30s 短缓存；失败降级 settings.admin_user_ids 兜底"""
    now = _time.time()
    cached = _admin_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    try:
        result = await _load_admin_from_db(user_id)
    except Exception:
        result = _is_admin_fallback(user_id)
    _admin_cache[user_id] = (result, now + _ADMIN_CACHE_TTL)
    return result


def is_admin_user_sync(user_id: int) -> bool:
    """主账号判定（sync，供非 async 调用点）：优先读缓存，未命中回退 settings.admin_user_ids 兜底"""
    now = _time.time()
    cached = _admin_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    return _is_admin_fallback(user_id)
