from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=30)
    password: str = Field(..., min_length=8, max_length=64)
    nickname: str | None = Field(None, max_length=30)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=1, max_length=64)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=64)
    new_password: str = Field(..., min_length=1, max_length=64)


class ForgotPasswordRequest(BaseModel):
    """忘记密码（本地部署）：仅用户名+新密码，无需旧密码。"""
    username: str = Field(..., min_length=1, max_length=30)
    new_password: str = Field(..., min_length=1, max_length=64)


class UpdateProfileRequest(BaseModel):
    nickname: str | None = Field(None, max_length=30)
    birthday: str | None = Field(None, max_length=10)
    gender: str | None = Field(None, max_length=10)
    height: float | None = Field(None, ge=0, le=300)
    weight: float | None = Field(None, ge=0, le=500)
    bio: str | None = Field(None, max_length=500)
    avatar_url: str | None = Field(None, max_length=500)
    ai_social_enabled: bool | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    nickname: str


class UpdateDndRequest(BaseModel):
    dnd_enabled: bool = False
    notifications_enabled: bool = True
    start_hour: int = 22
    start_minute: int = 0
    end_hour: int = 8
    end_minute: int = 0
