"""织库请求/响应模型（2026-08-12）"""
from datetime import datetime
from pydantic import BaseModel


class WeaveDetail(BaseModel):
    time: str = "不详"
    weather: str = "不详"
    location: str = "不详"
    mood: str = "不详"
    events: list[str] = []
    details: list[str] = []


class WeaveMemoryRef(BaseModel):
    id: int
    memory_type: str
    sub_type: str | None = None
    content: str
    importance_pct: float = 0.0
    source_label: str | None = None
    source_icon: str | None = None
    created_at: datetime | None = None


class WeaveCardResponse(BaseModel):
    id: int
    character_id: int
    title: str
    summary: str
    importance: float = 0.0
    memory_count: int = 0
    created_at: datetime


class WeaveCardDetailResponse(WeaveCardResponse):
    character_name: str = ""
    detail: WeaveDetail = WeaveDetail()
    memories: list[WeaveMemoryRef] = []


class WeaveCardListResponse(BaseModel):
    cards: list[WeaveCardResponse]
    total: int


class WeaveGenerateResponse(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    token_estimate: int = 0


class WeaveNode(BaseModel):
    id: int
    character_id: int
    character_ids: list[int] = []  # 跨角色合并卡片：参与角色 id 列表
    character_name: str = ""
    title: str
    summary: str
    importance: float = 0.0
    mood: str = ""  # 心情（detail 提取，画布筛选用）
    created_at: datetime | None = None
    life_type: str = ""  # 私域增强：life_event/reflection/note（参与记忆 sub_type 聚合）
    hot_tags: list[str] = []  # 私域增强：命中的角色高兴趣关键词


class WeaveEdge(BaseModel):
    source: int
    target: int
    strength: float = 0.0


class WeaveCharacterRef(BaseModel):
    id: int
    name: str = ""


class WeaveGraphResponse(BaseModel):
    nodes: list[WeaveNode] = []
    edges: list[WeaveEdge] = []
    characters: list[WeaveCharacterRef] = []  # 画布角色筛选 chips
