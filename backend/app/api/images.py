"""生图 API（异步任务）：POST 创建 / GET 查询状态"""
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.services.image_gen_service import (
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
    """创建生图任务，立即返回 task_id；完成后用 GET /tasks/{id} 取图"""
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
