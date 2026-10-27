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
    epistemic_status: str | None = None  # FACT/INFERRED/...（M1b：前端据此决定「认可」按钮是否出现）
    speaker_type: str | None = None
    speaker_id: int | None = None
    title: str | None
    content: str
    importance: int
    importance_pct: float = 0.0
    is_archived: bool
    is_pinned: bool = False
    is_locked: bool = False
    chain_id: str | None = None
    parent_id: int | None = None
    node_type: str | None = None
    version: int = 0
    why_it_matters: str | None = None  # 意义记忆（v2.1）：AI 提炼的"为什么重要"
    # 记忆衰减曲线（2026-08-24）：前端可视化 S/遗忘起点/下次复习/复习次数
    strength_days: float | None = None
    last_reinforce_at: datetime | None = None
    next_review_at: datetime | None = None
    review_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]
    total: int


class DerivationItem(BaseModel):
    """派生关系条目：只含授权行必需摘要（不放正文之外的原始派生数组/owner/计数）。"""
    id: int
    content: str
    title: str | None = None
    memory_type: str
    status: str = "active"
    is_archived: bool = False


class DerivationDirection(BaseModel):
    """单方向（parents / children）独立分页与完整性状态。"""
    items: list[DerivationItem] = []
    has_more: bool = False
    next_cursor: str | None = None
    data_incomplete: bool = False


class MemoryDerivationsResponse(BaseModel):
    """GET /api/v1/memories/{id}/derivations 响应（只读、一跳、直接边）。"""
    status: str = "ok"
    memory_id: int
    parents: DerivationDirection
    children: DerivationDirection
