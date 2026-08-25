"""聊天请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    session_id: int
    content: str = Field(..., min_length=1, max_length=4000)
    quote: dict | None = None  # 引用：{message_id, sender, content}（完整引用消息 v2.0.0）


class StreamMessageRequest(BaseModel):
    """SSE 流式聊天请求（会话 id 走路径参数，不复用 SendMessageRequest.session_id 避免歧义）"""
    content: str = Field(..., min_length=1, max_length=4000)
    quote: dict | None = None
    tts: bool = False              # 语音模式：逐句合成 TTS 并随 block 实时下发
    save_user_message: bool = True  # 语音链路用户消息已单独落库（/voice）时置 False，避免重复


class ChatMessageResponse(BaseModel):
    id: int
    session_id: int
    sender_type: str
    content: str
    image_url: str | None = None
    extra_meta: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatHistoryResponse(BaseModel):
    session_id: int
    messages: list[ChatMessageResponse]
    total: int
