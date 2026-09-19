from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from app.auth.config import auth_settings

security = HTTPBearer(auto_error=False)

async def get_current_user_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    from app.i18n import tr
    if credentials is None:
        raise HTTPException(status_code=401, detail=tr(request, "please_login"))
    try:
        payload = jwt.decode(
            credentials.credentials,
            auth_settings.secret_key,
            algorithms=[auth_settings.algorithm],
        )
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail=tr(request, "token_invalid"))
    except JWTError:
        raise HTTPException(status_code=401, detail=tr(request, "token_expired"))
    uid = int(user_id)
    # 账号门禁（账号独立 P2，2026-09-19）：被控制台禁用的账号，后续请求在鉴权阶段即 403。
    # 判定走 permission_service.get_account_state 的 30s 短缓存（不每请求查库）；
    # 读失败/用户不存在 → 放行（fail-open，与改动前逐字节一致）。
    from app.application.permission_service import is_account_disabled
    if await is_account_disabled(uid):
        raise HTTPException(status_code=403, detail=tr(request, "account_disabled"))
    return uid


async def require_server_admin(
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> int:
    """服务器控制台管理员依赖（账号独立 P1）：非 users.server_admin → 403。

    口径：``server_admin`` = 服务器控制台管理员（跨家庭、管服务器级配置），与家庭主账号
    ``is_admin``（家庭内管理）分离。判定走 permission_service.is_server_admin（DB 权威 +
    30s 缓存 + env 兜底），与 app/application/system.py:_require_admin 同风格。
    返回 user_id，便于路由直接使用。
    """
    from app.i18n import tr
    from app.application.permission_service import is_server_admin
    if not await is_server_admin(user_id):
        raise HTTPException(status_code=403, detail=tr(request, "admin_config_only"))
    return user_id
