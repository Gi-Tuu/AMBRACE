"""用户八维可视化状态请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel, Field


class UserStateUpdate(BaseModel):
    mood: int | None = Field(default=None, ge=0, le=100)
    body_temp: int | None = Field(default=None, ge=0, le=100)
    desire: int | None = Field(default=None, ge=0, le=100)
    possessiveness: int | None = Field(default=None, ge=0, le=100)
    fatigue: int | None = Field(default=None, ge=0, le=100)
    sensitivity: int | None = Field(default=None, ge=0, le=100)
    comfort: int | None = Field(default=None, ge=0, le=100)
    anger: int | None = Field(default=None, ge=0, le=100)


class UserStateResponse(BaseModel):
    user_id: int
    mood: int
    body_temp: int
    desire: int
    possessiveness: int
    fatigue: int
    sensitivity: int
    comfort: int
    anger: int
    updated_at: datetime | None = None
