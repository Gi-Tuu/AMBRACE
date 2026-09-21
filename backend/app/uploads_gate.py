# -*- coding: utf-8 -*-
"""``/uploads`` 静态目录的租户鉴权闸门（账号独立 P1，2026-09-19）。

背景（已核实，2026-09-19）
--------------------------
- 前端（Flutter）**全部**用 ``Image.network`` / ``NetworkImage`` / audioplayers ``UrlSource``
  直连裸 URL（``ApiClient().resolveUrl('/uploads/...')``，见 ``flutter_app/lib/services/api_client.dart``）：
  既不发送 ``Authorization`` 头、也不带 ``?token=``、也无 Cookie。全仓唯一带头的图片是插件图标
  （``plugin_features/plugin/plugin_card.dart`` 的 ``pluginAuthHeaders``）。
- 后端 HTTP 认证只有 HTTPBearer（``app/auth/deps.py``）；``?token=`` 仅 WebSocket 侧支持。
- ``/uploads`` 是裸 ``StaticFiles`` 挂载（``app/main.py``），不经 FastAPI 依赖。
- **没有**任何「上传文件归属表」：归属只能从路径段推导（``avatars/{uid}``、``moments/{uid}``、
  ``images/{uid}``、``phone/{uid}``、``emojis/user/{uid}`` 是用户目录；``{session_id}/``、
  ``files/{sid}/``、``voice/{sid}/`` 需回查 ``ChatSession.user_id``；``douyin/{task_id}/`` 是待发布
  私有草稿，需回查插件表 ``DouyinPending.tenant_id``（该列本身即家庭根，P3-3 起移出共享白名单）；
  ``pets_assets/``、``pets/``、``tts/``、``emojis/market/`` 等为共享资源，无归属）。

因此本闸门采取「**带身份即强制、匿名按开关**」的兼容方案：

1. 请求带身份证（``Authorization: Bearer`` 或新增的 ``?token=``）→ 解析账号 → 推导路径归属租户
   → 跨租户一律 **404**（与「不存在」同观感，防探测）；同租户/共享资源放行。
2. 请求不带身份（App 现状）→ 由 ``settings.uploads_require_auth`` 决定：
   - ``False``（P1 默认，兼容）：放行，保证 App 裸 URL 取图/取音频不回归；
   - ``True``（严格，公网/SaaS 推荐）：租户归属路径 **404**，共享资源仍放行。

**残余风险（必须如实记录）**：``uploads_require_auth=False`` 时，App 的裸请求无身份可比，
「别家账号拿 URL 打开」在**匿名直连**下无法拒绝——这正是本轮不改前端所必然的边界。
要彻底关闭需二选一（均超出 P1 的「不改前端」约束）：
(a) 前端在 ~20 处 ``Image.network`` 加 ``headers: pluginAuthHeaders(ApiClient().token)``
    （仓内已有可用模板），或 (b) 部署侧置 ``UPLOADS_REQUIRE_AUTH=true`` 并接受裸图 404。
"""
from __future__ import annotations

from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

from app.utils.logger import get_logger

_logger = get_logger("uploads.guard")

# 共享/资源类目录（无归属，任何身份都放行）：宠物素材、市场表情、TTS 产物等
# 注意：``douyin`` 曾在此白名单，P3-3（2026-09-20）移出——``douyin/{task_id}/`` 是待发布私有草稿
_SHARED_HEADS = {"pets_assets", "pets", "tts", "preview", "market"}
# 用户子目录（第 2 段是 user_id）
_USER_HEADS = {"avatars", "moments", "images", "phone"}
# 会话子目录（第 2 段是 chat_session_id）
_SESSION_HEADS = {"files", "voice"}


def resolve_upload_scope(rel_path: str) -> tuple[str, int | None]:
    """``/uploads`` 相对路径 → 归属 (kind, key)。

    kind 取值：
    - ``user``       ：key = 归属 user_id（路径已含用户目录）
    - ``session``    ：key = chat_session_id（需再回查 ChatSession.user_id）
    - ``douyin_task``：key = douyin_pending.id（需再回查 DouyinPending.tenant_id = 家庭根）
    - ``shared``     ：共享/资源目录，无归属（放行）
    - ``unresolved`` ：无法判定（放行并告警；避免把未知目录误判成越权）
    """
    parts = [p for p in str(rel_path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return "shared", None
    head = parts[0]
    if head in _USER_HEADS:
        if len(parts) >= 2 and parts[1].isdigit():
            return "user", int(parts[1])
        return "unresolved", None
    if head == "emojis":
        # emojis/user/{uid}/... 为用户私有；emojis/market/... 为市场共享
        if len(parts) >= 3 and parts[1] == "user" and parts[2].isdigit():
            return "user", int(parts[2])
        return "shared", None
    if head in _SESSION_HEADS:
        if len(parts) >= 2 and parts[1].isdigit():
            return "session", int(parts[1])
        return "unresolved", None
    if head.isdigit():  # 私聊图片直接落 uploads/{session_id}/...
        return "session", int(head)
    if head == "douyin":
        # douyin/{task_id}/... 私有草稿（待发布配图/视频），第 2 段 = DouyinPending.id
        if len(parts) >= 2 and parts[1].isdigit():
            return "douyin_task", int(parts[1])
        return "shared", None  # 非任务目录：维持原共享放行口径（与 unresolved 同为放行，不误伤未知布局）
    if head in _SHARED_HEADS or head.startswith("."):
        return "shared", None
    return "unresolved", None


def _require_auth() -> bool:
    """严格模式开关（UPLOADS_REQUIRE_AUTH / settings.uploads_require_auth），默认 False=兼容。"""
    try:
        from app.config import settings
        return bool(getattr(settings, "uploads_require_auth", False))
    except Exception:
        return False


def _identity_from_scope(scope) -> int | None:
    """从 ASGI scope 解析请求账号：Authorization: Bearer 优先，其次 ?token=（新增兼容入口）。

    解析失败/缺失/过期一律 None（按匿名处理，不抛 401，交由调用方按开关裁决）。
    """
    token = ""
    for raw_name, raw_value in scope.get("headers") or []:
        try:
            name = raw_name.decode("latin-1").lower()
            value = raw_value.decode("latin-1")
        except Exception:
            continue
        if name == "authorization" and value.lower().startswith("bearer "):
            token = value[7:].strip()
            break
    if not token:
        qs = scope.get("query_string") or b""
        try:
            from urllib.parse import parse_qs
            token = (parse_qs(qs.decode("latin-1")).get("token") or [""])[0].strip()
        except Exception:
            token = ""
    if not token:
        return None
    try:
        from jose import jwt
        from app.auth.config import auth_settings
        payload = jwt.decode(token, auth_settings.secret_key, algorithms=[auth_settings.algorithm])
        uid = payload.get("user_id")
        return int(uid) if uid is not None else None
    except Exception:
        return None


async def _owner_user_id_for(db, kind: str, key: int | None) -> int | None:
    """``session`` 归属回查：chat_sessions.user_id（会话不存在 → None）。"""
    if kind != "session" or key is None:
        return None
    from sqlalchemy import select
    from app.models.chat import ChatSession
    return (await db.execute(
        select(ChatSession.user_id).where(ChatSession.id == int(key))
    )).scalar_one_or_none()


async def _owner_tenant_for(db, kind: str, key: int | None) -> int | None:
    """``douyin_task`` 归属回查：douyin_pending.tenant_id（该列本身就是家庭根）。

    插件模型必须延迟 import（核心 app 不硬依赖插件包），且任何失败一律返回 None 交由
    调用方按兼容口径放行：插件未加载（``douyin_models`` 不可导入）、表未建、
    任务行已删（孤儿草稿）都属此类。
    """
    if kind != "douyin_task" or key is None:
        return None
    try:
        import douyin_models  # 插件目录由插件 main.py 注入 sys.path；未加载时这里 ImportError
        from sqlalchemy import select
        return (await db.execute(
            select(douyin_models.DouyinPending.tenant_id).where(douyin_models.DouyinPending.id == int(key))
        )).scalar_one_or_none()
    except Exception as e:
        _logger.debug("uploads guard: douyin owner tenant unavailable task=%s: %s", key, e)
        return None


async def decide_upload_access(rel_path: str, scope) -> bool:
    """闸门裁决：True=放行，False=拒绝（调用方回 404）。

    规则见模块 docstring：带身份按租户比对；匿名按 ``uploads_require_auth``。
    """
    kind, key = resolve_upload_scope(rel_path)
    if kind in ("shared", "unresolved"):
        if kind == "unresolved":
            _logger.debug("uploads guard: unresolved path allowed (fail-open) path=%s", rel_path)
        return True

    actor = _identity_from_scope(scope)
    if actor is None:
        if _require_auth():
            _logger.info("uploads guard: anonymous denied path=%s (strict mode)", rel_path)
            return False
        return True

    from app.db.database import async_session_factory
    from app.application.tenant_service import tenant_key
    async with async_session_factory() as db:
        if kind == "douyin_task":
            owner_tenant = await _owner_tenant_for(db, kind, key)
            if owner_tenant is None:
                # 归属查不到（插件未加载 / 任务已删）：证不了跨租户 → 回兼容口径放行，核心 app 不硬依赖插件
                _logger.debug("uploads guard: douyin owner unresolved, compat allow path=%s", rel_path)
                return True
            # 口径同源（09-21 收口）：owner 侧读的是 douyin_pending.tenant_id（该列恒为**家庭根**），
            # 因此调用者侧必须用同一把尺子——tenant_scope.resolve_tenant 恒按家庭根解析、
            # 刻意**不跟随 TENANT_KEY_MODE**（见其 docstring 的口径护栏）。旧写法 tenant_service.tenant_key
            # 会跟随开关：一旦切成 user 口径，子账号读自己家的草稿会被误判跨租户 → 404
            # （现场表现为「抖音配图突然全挂」，且很难联想到是口径开关）。
            from app.application.tenant_scope import resolve_tenant
            try:
                actor_tenant = await resolve_tenant(db, actor)
            except ValueError:
                # 归属解析不出来（如账号行已删）：严格模式拒绝、兼容模式放行（与 tenant_key 返回 None 同语义）
                return not _require_auth()
            if int(actor_tenant) != int(owner_tenant):
                _logger.info(
                    "uploads guard: cross-tenant denied path=%s actor=%s owner_tenant=%s",
                    rel_path, actor, owner_tenant
                )
                return False
            return True
        owner_user_id = await _owner_user_id_for(db, kind, key)
        if owner_user_id is None and kind == "session":
            # 会话已删/文件孤儿：无法证明跨租户 → 保持兼容放行（identity 已校验在案）
            _logger.debug("uploads guard: orphan session file allowed path=%s", rel_path)
            return True
        owner = key if owner_user_id is None else int(owner_user_id)
        actor_tenant = await tenant_key(db, actor)
        owner_tenant = await tenant_key(db, owner)
    if actor_tenant is None or owner_tenant is None:
        return not _require_auth()  # 归属不可解析：严格模式拒绝，兼容模式放行
    allowed = int(actor_tenant) == int(owner_tenant)
    if not allowed:
        _logger.info(
            "uploads guard: cross-tenant denied path=%s actor=%s owner=%s", rel_path, actor, owner
        )
    return allowed


class TenantStaticFiles(StaticFiles):
    """带租户闸门的 StaticFiles：跨租户 → 404（不泄漏文件是否存在）。"""

    async def get_response(self, path: str, scope):
        try:
            allowed = await decide_upload_access(path, scope)
        except Exception as e:  # fail-open：闸门自身异常不得让整个图片面 500
            _logger.warning("uploads guard failed, fail-open path=%s: %s", path, e)
            allowed = True
        if not allowed:
            return PlainTextResponse("Not Found", status_code=404)
        return await super().get_response(path, scope)


__all__ = ["TenantStaticFiles", "decide_upload_access", "resolve_upload_scope"]
