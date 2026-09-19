"""生图 API（异步任务）：POST 创建 / GET 查询状态"""
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.application.image_gen_service import (
    get_image_provider, check_daily_limit, create_image_gen_task,
    get_image_gen_task, schedule_image_gen,
)
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/images", tags=["Images"])
_logger = get_logger("api.images")


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    character_id: int | None = None
    session_id: int | None = None


@router.post("/generate")
async def generate_image(data: GenerateRequest, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """创建生图任务，立即返回 task_id；完成后用 GET /tasks/{id} 取图

    账号独立 P1：body 的 character_id / session_id 是「引用他行」——落库前必须过租户归属
    （否则可把别家角色/会话 id 记进本账号任务，形成跨租户引用）。
    """
    await _assert_refs_in_tenant(user_id, data.character_id, data.session_id, lang)
    if await get_image_provider() is None:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "image_gen_not_configured"))
    if await check_daily_limit(user_id):
        raise HTTPException(status_code=429, detail=tr_lang(lang, "image_gen_limit"))
    task = await create_image_gen_task(
        user_id=user_id, prompt=data.prompt.strip(),
        character_id=data.character_id, session_id=data.session_id,
    )
    schedule_image_gen(task.id)
    return {"task_id": task.id, "status": task.status}


async def _assert_refs_in_tenant(user_id: int, character_id: int | None, session_id: int | None, lang: str) -> None:
    """校验 body 引用的 character_id / session_id 归属本账号租户，否则 404。"""
    if character_id is None and session_id is None:
        return
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    from app.application.tenant_service import tenant_scope_ids

    async with async_session_factory() as db:
        scope_ids = await tenant_scope_ids(db, user_id)
        if character_id is not None:
            hit = (await db.execute(
                select(AICharacter.id).where(
                    AICharacter.id == character_id, AICharacter.user_id.in_(scope_ids)
                )
            )).scalar_one_or_none()
            if hit is None:
                raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        if session_id is not None:
            from app.application.chat_service import get_owned_session
            if await get_owned_session(db, session_id, user_id) is None:
                raise HTTPException(status_code=404, detail=tr_lang(lang, "session_not_found"))


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """查询生图任务状态（用户隔离：仅本人任务）"""
    task = await get_image_gen_task(task_id, user_id)
    if not task:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "task_not_found"))
    return {
        "task_id": task.id,
        "status": task.status,
        "prompt": task.prompt,
        "image_url": task.image_url,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }
