from datetime import datetime, timedelta
import secrets

from jose import jwt

from app.config import settings


def _load_or_create_secret() -> str:
    """JWT 签名密钥：.env AUTH_SECRET_KEY 优先；未配置则首次启动生成随机密钥并持久化（重启不失效）。"""
    if (settings.auth_secret_key or "").strip():
        return settings.auth_secret_key.strip()
    key_file = settings.PROJECT_ROOT / settings.auth_secret_file
    try:
        if key_file.exists():
            content = key_file.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception:
        pass
    key = secrets.token_hex(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key, encoding="utf-8")
        print(f"[auth] AUTH_SECRET_KEY 未配置，已自动生成持久化密钥: {key_file}")
    except Exception as e:
        print(f"[auth] 密钥持久化失败（仍使用本次随机密钥）: {e}")
    return key


class AuthSettings:
    secret_key: str = _load_or_create_secret()
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 days


auth_settings = AuthSettings()


def create_token(user_id: int) -> str:
    """Generate JWT token for user"""
    expire = datetime.utcnow() + timedelta(minutes=auth_settings.access_token_expire_minutes)
    return jwt.encode(
        {"user_id": user_id, "exp": expire, "iat": datetime.utcnow()},
        auth_settings.secret_key,
        algorithm=auth_settings.algorithm,
    )
