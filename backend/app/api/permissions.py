"""AI 能力权限 API（2026-08-12）：三档权限读取/更新 + 待确认动作批准/拒绝

设计参考 Operit 工具权限模型：全局默认 + 每能力例外。
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from app.utils.async_tasks import spawn_background
from pydantic import BaseModel

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.services import permission_service
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/permissions", tags=["Permissions"])
_logger = get_logger("api.permissions")


class ScopeLevelUpdate(BaseModel):
    scope: str
    level: str


class PermissionUpdateRequest(BaseModel):
    global_level: str | None = None
    scopes: dict[str, str] | None = None


class PermissionActionResponse(BaseModel):
    action_id: int
    status: str


@router.get("")
async def get_permissions(user_id: int = Depends(get_current_user_id)) -> dict:
    """当前用户权限配置：全局默认 + 每能力档位"""
    return await permission_service.get_all_levels(user_id)


@router.put("")
async def update_permissions(
    req: PermissionUpdateRequest, user_id: int = Depends(get_current_user_id)
) -> dict:
    """批量更新权限档位（仅接受合法值）"""
    await permission_service.set_levels(
        user_id, global_level=req.global_level, scopes=req.scopes
    )
    return await permission_service.get_all_levels(user_id)


@router.post("/actions/{action_id}/approve", response_model=PermissionActionResponse)
async def approve_action(action_id: int, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")) -> dict:
    """用户批准待确认动作（如生图）→ 立即执行"""
    payload = await permission_service.resolve_pending_action(action_id, user_id, approve=True)
    if payload is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "action_done"))
    await _execute_approved(payload)
    return {"action_id": action_id, "status": "approved"}


@router.post("/actions/{action_id}/deny", response_model=PermissionActionResponse)
async def deny_action(action_id: int, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")) -> dict:
    """用户拒绝待确认动作"""
    result = await permission_service.resolve_pending_action(action_id, user_id, approve=False)
    if result is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "action_done"))
    return {"action_id": action_id, "status": "denied"}


async def _execute_approved(payload: dict) -> None:
    """按 scope 执行已批准动作（v1 支持生图；其余能力按需扩展）"""
    scope = payload.get("scope")
    if scope == permission_service.SCOPE_IMAGE_GEN:
        from app.services.chat_service import _gen_image_flow

        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return
        spawn_background(
            _gen_image_flow(
                int(payload["user_id"]) if "user_id" in payload else 0,
                int(payload["character_id"] or 0),
                int(payload["session_id"] or 0),
                prompt,
                str(payload.get("img_text") or "").strip() or None,
            )
        )
    else:
        _logger.info("approved action scope=%s 未实现执行器", scope)
