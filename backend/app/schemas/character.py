"""AI 角色请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError


def _reject_blank_name(value: str | None) -> str | None:
    """显式字符串 ``name`` 不得是**纯空白**（普通空格/换行制表/全角空格等）。

    只做「拒绝」，**不擅自 trim 或改写原文**：``"  留白名  "`` 仍按原文保存。
    ``None`` 一律放行（``CharacterUpdate.name`` 未提交时不参与校验，保持旧契约）。
    """
    if value is not None and not value.strip():
        # 用 PydanticCustomError：普通 ValueError 的 ctx.error 不可 JSON 序列化，
        # 会让 FastAPI 的 422 处理器自身抛错（500）。
        raise PydanticCustomError("blank_name", "Name must not be blank")
    return value


class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    personality: str | None = None
    chat_style: str | None = None
    greeting_message: str | None = None
    avatar_url: str | None = None
    talkativeness: int | None = Field(None, ge=0, le=100)
    talkativeness_locked: bool = False
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
    user_llm_config_id: int | None = None  # 角色绑定 LLM 配置（#68 P0-P2）

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        return _reject_blank_name(value)


class CharacterUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    personality: str | None = None
    chat_style: str | None = None
    greeting_message: str | None = None
    avatar_url: str | None = None
    talkativeness: int | None = Field(None, ge=0, le=100)
    talkativeness_locked: bool | None = None
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
    cognitive_loop_enabled: bool | None = None
    user_llm_config_id: int | None = None  # 角色绑定 LLM 配置（#68 P0-P2）

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        return _reject_blank_name(value)


class CharacterResponse(BaseModel):
    id: int
    user_id: int
    name: str
    personality: str | None
    chat_style: str | None
    system_prompt: str | None
    greeting_message: str | None
    avatar_url: str | None
    talkativeness: int | None = None
    talkativeness_locked: bool = False
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
    cognitive_loop_enabled: bool
    user_llm_config_id: int | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CharacterListResponse(BaseModel):
    characters: list[CharacterResponse]
    total: int
