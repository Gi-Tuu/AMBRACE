"""插件管理 API：列表 / 启停 / 配置 / zip 安装"""
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.plugins import registry
from app.plugins.zip_safety import validate_zip_bytes, ZipSafetyError
from app.utils.logger import get_logger

_logger = get_logger("api.plugins")

router = APIRouter(prefix="/api/v1/plugins", tags=["Plugins"])


def _is_owner(user_id: int) -> bool:
    from app.config import settings
    return user_id in settings.admin_user_ids  # 主账号可管理插件；其他用户只读


@router.get("")
async def list_plugins(user_id: int = Depends(get_current_user_id)):
    """插件列表（含启用状态与配置）"""
    items = registry.list_plugins()
    return {"items": items, "total": len(items)}


@router.put("/{name}")
async def update_plugin(
    name: str,
    body: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """启用/禁用/更新配置（仅主账号可写）"""
    if not _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    plugin = registry.get_plugin(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_not_found"))
    enabled = body.get("enabled")
    config = body.get("config")
    if enabled is not None and not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "enabled_invalid"))
    if config is not None and not isinstance(config, dict):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    updated = await registry.set_plugin_state(name, enabled=enabled, config=config)
    return updated


@router.post("/install")
async def install_plugin(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """zip 安装插件：校验 manifest + 安全解压到 backend/data/plugins/"""
    if not _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_install_only"))
    data = await file.read()
    try:
        manifest, names = validate_zip_bytes(data)
    except ZipSafetyError as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, e.key, **e.kwargs))
    name = manifest["name"]

    # 解压到 backend/data/plugins/<name>/
    from app.plugins.zip_safety import extract_zip_bytes
    target = registry.USER_DIR / name
    if not str(target.resolve()).startswith(str(registry.USER_DIR.resolve())):
        raise HTTPException(status_code=400, detail="invalid plugin name (path traversal blocked)")
    if target.exists():
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    try:
        extract_zip_bytes(data, names, target)
    except Exception as e:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "install_failed", err=str(e)[:200]))
    _logger.info("插件 %s 安装到 %s", name, target)

    # 重新扫描加载
    await registry.sync_plugins_db()
    plugin = registry.get_plugin(name)
    if plugin is None:
        raise HTTPException(status_code=500, detail=tr_lang(lang, "plugin_load_failed"))
    return plugin
