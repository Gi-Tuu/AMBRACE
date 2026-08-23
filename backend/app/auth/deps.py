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
        return int(user_id)
    except JWTError:
        raise HTTPException(status_code=401, detail=tr(request, "token_expired"))
