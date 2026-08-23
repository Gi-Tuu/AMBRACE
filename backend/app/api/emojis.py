"""表情包 API：包列表 / 下载 / 删除"""
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile, Header
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.services import emoji_service

router = APIRouter(prefix="/api/v1/emojis", tags=["Emojis"])


@router.get("/packs")
async def list_emoji_packs(user_id: int = Depends(get_current_user_id)):
    """表情包列表（含已下载标记与表情内容；未下载包不返回表情明细）"""
    packs = await emoji_service.list_packs(user_id)
    return {"packs": packs}


@router.post("/packs/{pack_id}/download")
async def download_emoji_pack(pack_id: str, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    ok = await emoji_service.download_pack(user_id, pack_id)
    if not ok:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "emoji_pack_not_found"))
    return {"ok": True, "pack_id": pack_id}


@router.delete("/packs/{pack_id}")
async def remove_emoji_pack(pack_id: str, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    ok = await emoji_service.remove_pack(user_id, pack_id)
    if not ok:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "emoji_pack_builtin"))
    return {"ok": True, "pack_id": pack_id}


# ── 自定义表情（用户上传图片）──

@router.get("/custom")
async def list_custom_emojis(user_id: int = Depends(get_current_user_id)):
    emojis = await emoji_service.list_custom_emojis(user_id)
    return {"emojis": emojis}


@router.post("/custom")
async def upload_custom_emoji(
    file: UploadFile = File(...),
    name: str = Form("表情"),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """上传自定义表情图片（仅本用户可用）"""
    from app.services.upload_service import save_image, ALLOWED_IMAGE_EXTS
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename else ""
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "image_format_only"))
    url = await save_image(file, f"emojis/user/{user_id}", lang)
    row = await emoji_service.add_custom_emoji(user_id, name, url)
    if row is None:
        raise HTTPException(status_code=500, detail=tr_lang(lang, "save_failed"))
    return row


@router.delete("/custom/{emoji_id}")
async def delete_custom_emoji(emoji_id: int, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    ok = await emoji_service.delete_custom_emoji(user_id, emoji_id)
    if not ok:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "emoji_not_found"))
    return {"ok": True, "id": emoji_id}
