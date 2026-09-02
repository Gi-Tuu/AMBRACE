"""通用上传 API（头像等）"""
from fastapi import APIRouter, Depends, UploadFile, File, Header
from app.auth.deps import get_current_user_id
from app.application.upload_service import save_image

router = APIRouter(prefix="/api/v1/uploads", tags=["Uploads"])


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """上传头像（角色/用户共用），返回 /uploads/ 相对路径，直接写入 avatar_url 字段"""
    url = await save_image(file, f"avatars/{user_id}", lang)
    return {"url": url}
