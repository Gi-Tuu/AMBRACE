"""AI 能力权限服务：三档权限（allow/ask/forbid）+ 待确认动作管理（2026-08-12）

模型：用户级全局默认档位 + 每能力例外；实际权限 = 能力例外优先，无例外跟随全局默认。
能力清单：image_gen(生图)/image_understand(识图)/tts(语音回复)/asr(语音转写)/
        browser(浏览器扩展)/渠道 scope(渠道扩展经注册表上报，X5)/extension(其他扩展)。
"""
import json
from app.utils.logger import get_logger
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.agent import PendingPermissionAction, ToolPermission

_logger = get_logger("permission")

SCOPE_GLOBAL = "__global__"
SCOPE_IMAGE_GEN = "image_gen"
SCOPE_IMAGE_UNDERSTAND = "image_understand"
SCOPE_TTS = "tts"
SCOPE_ASR = "asr"
SCOPE_BROWSER = "browser"
SCOPE_EXTENSION = "extension"

SCOPES = [
    SCOPE_IMAGE_GEN,
    SCOPE_IMAGE_UNDERSTAND,
    SCOPE_TTS,
    SCOPE_ASR,
    SCOPE_BROWSER,
    SCOPE_EXTENSION,
]

SCOPE_LABELS = {
    SCOPE_IMAGE_GEN: "生图",
    SCOPE_IMAGE_UNDERSTAND: "识图",
    SCOPE_TTS: "语音回复",
    SCOPE_ASR: "语音转写",
    SCOPE_BROWSER: "浏览器",
    SCOPE_EXTENSION: "扩展",
}

SCOPE_DESCRIPTIONS = {
    SCOPE_IMAGE_GEN: "AI 生成图片发给你（聊天内发图/主动生图）",
    SCOPE_IMAGE_UNDERSTAND: "AI 理解你发来的图片内容（本地识图）",
    SCOPE_TTS: "AI 用语音回复你（TTS 合成）",
    SCOPE_ASR: "转写你的语音消息（ASR 识别）",
    SCOPE_BROWSER: "浏览器扩展：AI 搜索网页、读取页面",
    SCOPE_EXTENSION: "其他扩展/插件的能力调用",
}

LEVELS = ("allow", "ask", "forbid")

LEVEL_LABELS = {"allow": "允许", "ask": "每次询问", "forbid": "禁止"}

DEFAULT_GLOBAL_LEVEL = "allow"  # 默认全局 = 允许（保持现有体验），用户可收紧


def _channel_scopes() -> dict[str, tuple[str, str]]:
    """渠道上报的 scope → (label, desc)（X5：内核不持有具体渠道名，scope 清单动态合并）"""
    try:
        from app.providers.channel import list_channels
        out: dict[str, tuple[str, str]] = {}
        for c in list_channels():
            m = c.get("meta") or {}
            s = str(m.get("scope") or "").strip()
            if s:
                out[s] = (str(m.get("scope_label") or m.get("label") or s), str(m.get("scope_desc") or ""))
        return out
    except Exception:
        return {}


def _plugin_scope(plugin: str) -> str:
    """插件名 → 能力 scope：注册渠道取 meta.scope（渠道上报），browser 特判，其余归 extension"""
    name = (plugin or "").lower()
    if "browser" in name or "brows" in name:
        return SCOPE_BROWSER
    try:
        from app.providers.channel import channel_for_plugin
        hit = channel_for_plugin(plugin or "")
        if hit is not None:
            scope = str((hit[1] or {}).get("scope") or "").strip()
            if scope:
                return scope
    except Exception:
        pass
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


async def check_mcp_mode(user_id: int, scope: str, risk_level: str = "medium") -> str:
    """MCP 工具权限裁决（Phase 2）：显式配置优先，否则按工具风险默认（高风险 ask、其余 allow）。

    - mcp_{server} scope 是动态生成的（不在静态 SCOPES），无显式 ToolPermission 行时
      `get_scope_level` 会落回全局默认（allow）；
    - 目标是「高风险工具默认 ASK，低风险默认 ALLOW」：无显式配置且用户未收紧全局档位时，
      高风险的 MCP 工具默认 ask（需确认），低/中风险默认 allow；
    - 用户显式配置过 mcp_{server} scope → 跟随该档位；用户全局收紧（forbid/ask）→ 跟随全局。
    """
    rows = await _get_rows(user_id)
    if scope in rows:
        return rows[scope]
    global_level = rows.get(SCOPE_GLOBAL, DEFAULT_GLOBAL_LEVEL)
    if global_level != DEFAULT_GLOBAL_LEVEL:
        return global_level
    return "ask" if risk_level == "high" else "allow"


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
_server_admin_cache: dict[int, tuple[bool, float]] = {}


def _invalidate_admin_cache() -> None:
    """主账号集合变更后失效缓存（设置/取消主账号时调用）"""
    _admin_cache.clear()
    _server_admin_cache.clear()


def _invalidate_server_admin_cache() -> None:
    """服务器控制台管理员集合变更后失效缓存（账号独立 P1）。"""
    _server_admin_cache.clear()


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


# ================= 服务器控制台管理员（账号独立 P1，2026-09-19） =================
# is_admin = 家庭主账号（家庭内管理）；server_admin = 服务器控制台管理员（跨家庭）。
# 数据源：users.server_admin（DB 权威）；读失败/用户不存在时回落 settings.admin_user_ids
# （与 is_admin_user 同口径的兜底，避免远古库缺列时把唯一管理员锁死）。


async def _load_server_admin_from_db(user_id: int) -> bool:
    async with async_session_factory() as db:
        row = (await db.execute(select(User.server_admin).where(User.id == user_id))).first()
    if row is None:
        return _is_admin_fallback(user_id)
    return bool(row.server_admin)


async def is_server_admin(user_id: int) -> bool:
    """服务器控制台管理员判定（async）：读 users.server_admin + 30s 短缓存；失败降级 env 兜底"""
    now = _time.time()
    cached = _server_admin_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    try:
        result = await _load_server_admin_from_db(user_id)
    except Exception:
        result = _is_admin_fallback(user_id)
    _server_admin_cache[user_id] = (result, now + _ADMIN_CACHE_TTL)
    return result


# ================= 账号门禁（账号独立 P2，2026-09-19） =================
# 控制台可对每个账号设：
# - users.disabled_at 非空 = 禁用（登录 403 + 后续请求在 get_current_user_id 阶段 403）；
# - users.llm_mode = 模型来源策略（own / default_allowed / blocked），生效点在四模态唯一出口
#   app/application/llm_config_service.resolve_modality_config。
# 二者同源于 users 一行 → 共用一个 30s 短缓存（契约 §4：避免每请求查库）。
# fail-open：读失败/用户不存在 → (False, default_allowed)，与改动前行为一致（不给旧库/异常路径
# 制造新的 403）。

LLM_MODE_OWN = "own"
LLM_MODE_DEFAULT_ALLOWED = "default_allowed"
LLM_MODE_BLOCKED = "blocked"
LLM_MODES = (LLM_MODE_OWN, LLM_MODE_DEFAULT_ALLOWED, LLM_MODE_BLOCKED)
DEFAULT_LLM_MODE = LLM_MODE_DEFAULT_ALLOWED

_account_state_cache: dict[int, tuple[tuple[bool, str], float]] = {}


def _invalidate_account_state_cache(user_id: int | None = None) -> None:
    """账号门禁状态变更后失效缓存（禁用/启用、llm_mode 变更时调用；None=全清）。"""
    if user_id is None:
        _account_state_cache.clear()
    else:
        _account_state_cache.pop(int(user_id), None)


async def _load_account_state_from_db(user_id: int) -> tuple[bool, str]:
    async with async_session_factory() as db:
        row = (await db.execute(
            select(User.disabled_at, User.llm_mode).where(User.id == user_id)
        )).first()
    if row is None:
        return False, DEFAULT_LLM_MODE
    mode = str(row.llm_mode or DEFAULT_LLM_MODE).strip().lower()
    if mode not in LLM_MODES:
        mode = DEFAULT_LLM_MODE
    return bool(row.disabled_at is not None), mode


async def get_account_state(user_id: int | None) -> tuple[bool, str]:
    """(是否禁用, llm_mode)：users 一行 + 30s 短缓存；异常/缺行 → (False, default_allowed)。"""
    if not user_id or int(user_id) <= 0:
        return False, DEFAULT_LLM_MODE
    uid = int(user_id)
    now = _time.time()
    cached = _account_state_cache.get(uid)
    if cached and cached[1] > now:
        return cached[0]
    try:
        result = await _load_account_state_from_db(uid)
    except Exception:
        result = (False, DEFAULT_LLM_MODE)
    _account_state_cache[uid] = (result, now + _ADMIN_CACHE_TTL)
    return result


async def is_account_disabled(user_id: int | None) -> bool:
    """账号是否被控制台禁用（缺列/异常 → False，fail-open）。"""
    return (await get_account_state(user_id))[0]


async def get_account_llm_mode(user_id: int | None) -> str:
    """账号模型来源策略（缺列/异常 → default_allowed，即现状行为）。"""
    return (await get_account_state(user_id))[1]
