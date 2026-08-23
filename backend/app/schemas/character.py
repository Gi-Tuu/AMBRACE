"""AI 角色请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel, Field


class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    personality: str | None = None
    chat_style: str | None = None
    greeting_message: str | None = None
    avatar_url: str | None = None
    height: int | None = None
    weight: int | None = None
    gender: str | None = None
    birthday: str | None = None
    appearance: str | None = None
    voice: str | None = None
    voice_rate: float | None = None
    voice_pitch: float | None = None
    timezone_offset: int | None = Field(None, ge=-12, le=14)
    bio: str | None = None

class CharacterUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    personality: str | None = None
    chat_style: str | None = None
    greeting_message: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    relationship_summary: str | None = None
    current_status: str | None = None
    height: int | None = None
    weight: int | None = None
    gender: str | None = None
    birthday: str | None = None
    appearance: str | None = None
    voice: str | None = None
    voice_rate: float | None = None
    voice_pitch: float | None = None
    timezone_offset: int | None = Field(None, ge=-12, le=14)

class CharacterResponse(BaseModel):
    id: int
    user_id: int
    name: str
    personality: str | None
    chat_style: str | None
    system_prompt: str | None
    greeting_message: str | None
    avatar_url: str | None
    bio: str | None
    self_statement: str | None
    relationship_summary: str | None
    current_status: str | None
    height: int | None = None
    weight: int | None = None
    gender: str | None = None
    birthday: str | None = None
    appearance: str | None = None
    voice: str | None = None
    voice_rate: float | None = None
    voice_pitch: float | None = None
    timezone_offset: int | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CharacterListResponse(BaseModel):
    characters: list[CharacterResponse]
    total: int
