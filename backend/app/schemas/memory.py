"""记忆请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel


class MemoryResponse(BaseModel):
    id: int
    user_id: int
    character_id: int
    memory_type: str
    sub_type: str | None = None
    source: str | None = None
    source_id: int | None = None
    source_label: str | None = None
    source_icon: str | None = None
    speaker_type: str | None = None
    speaker_id: int | None = None
    title: str | None
    content: str
    importance: int
    importance_pct: float = 0.0
    is_archived: bool
    is_pinned: bool = False
    is_locked: bool = False
    why_it_matters: str | None = None  # 意义记忆（v2.1）：AI 提炼的"为什么重要"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]
    total: int
