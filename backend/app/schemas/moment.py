"""朋友圈请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel, Field


class MomentResponse(BaseModel):
    id: int
    character_id: int = 0
    character_name: str = ""
    avatar_url: str | None = None
    user_id: int = 0
    sender_type: str = "ai"  # ai / user
    content: str
    image_url: str | None = None
    image_desc: str | None = None
    likes_count: int
    is_active: bool
    created_at: datetime
    author_tz_offset: int = 8  # 作者所在时区（UTC 偏移小时，朋友圈时间按作者地区显示）
    liked_by_me: bool = False
    likers: list[str] = []
    comments: list["CommentResponse"] = []

    model_config = {"from_attributes": True}


class MomentListResponse(BaseModel):
    moments: list[MomentResponse]
    total: int


class LikeResponse(BaseModel):
    moment_id: int
    likes_count: int
    liked: bool


class CommentResponse(BaseModel):
    id: int
    moment_id: int
    parent_id: int | None = None
    sender_type: str
    sender_id: int
    sender_name: str
    content: str
    created_at: datetime
    replies: list["CommentResponse"] = []

    model_config = {"from_attributes": True}


class CommentListResponse(BaseModel):
    comments: list[CommentResponse]
    total: int


class CreateCommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)
    parent_id: int | None = None


class CreateUserMomentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
