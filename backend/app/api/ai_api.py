"""48b 角色开放成 API：AI 角色列表 / 详情 / 按人设对话（HTTP 层；核心流程在 app.services.character_chat_api）"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import get_current_user_id
from app.i18n import lang_of
from app.services import character_chat_api

router = APIRouter(prefix="/api/v1/ai", tags=["AI Character API"])


class _HistoryItem(BaseModel):
    role: str
    content: str


class _ChatRequest(BaseModel):
    aiId: int
    input: str
    history: list[_HistoryItem] | None = None
    maxTokens: int | None = None
    temperature: float | None = None
    lang: str | None = None


@router.get("/list")
async def list_ai_characters(user_id: int = Depends(get_current_user_id)):
    """当前用户全部 is_active 角色 → {items:[{id,name,avatar_url}], total}（id=AICharacter.id）"""
    return await character_chat_api.list_characters(user_id)


@router.get("/{ai_id}")
async def get_ai_character(
    ai_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    """角色详情（含 personality/chat_style/bio/self_statement/greeting_message/relationship_summary）；不存在或非本人 404"""
    return await character_chat_api.get_character_detail(ai_id, user_id, lang_of(request))


@router.post("/chat")
async def ai_chat(
    body: _ChatRequest,
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    """按 aiId 以该角色人设对话（归属/限额/BYOK；不落库/不建会话/不写记忆/不触发 hook）"""
    lang = (body.lang or "").strip() or lang_of(request)
    return await character_chat_api.chat_with_character(
        ai_id=body.aiId,
        user_id=user_id,
        input_text=body.input,
        history=[h.model_dump() for h in body.history] if body.history else None,
        max_tokens=body.maxTokens,
        temperature=body.temperature,
        lang=lang,
    )
