"""日记请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel


class DiaryEntryResponse(BaseModel):
    id: int
    character_id: int
    diary_date: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DiaryListResponse(BaseModel):
    entries: list[DiaryEntryResponse]
    total: int

