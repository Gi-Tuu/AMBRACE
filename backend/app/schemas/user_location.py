"""用户位置信息 API 请求/响应模型"""
from pydantic import BaseModel, Field


class UserLocationResponse(BaseModel):
    user_id: int
    location_enabled: bool = False
    location_gps_enabled: bool = False
    user_location: str | None = None
    ai_location: str | None = None
    location_follow: bool = False
    timezone_offset_minutes: int | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    location_city: str | None = None


class UserLocationUpdate(BaseModel):
    location_enabled: bool | None = None
    location_gps_enabled: bool | None = None
    user_location: str | None = Field(default=None, max_length=100)
    ai_location: str | None = Field(default=None, max_length=100)
    location_follow: bool | None = None
    timezone_offset_minutes: int | None = None
    location_lat: float | None = None
    location_lng: float | None = None
