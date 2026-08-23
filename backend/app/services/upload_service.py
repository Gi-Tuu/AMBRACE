"""通用图片上传服务（私聊 / 朋友圈 / 头像共用）"""
import os
import time as _time
import uuid
from fastapi import HTTPException, UploadFile
from app.i18n import tr_lang
from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("services.upload")

UPLOAD_DIR = settings.PROJECT_ROOT / "data" / "uploads"
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB
# 私聊文件白名单（文档/表格/压缩/文本等）与大小上限
ALLOWED_FILE_EXTS = {
    ".pdf", ".doc", ".docx", ".txt", ".md", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".zip", ".rar", ".7z", ".json", ".log",
}
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB
# 语音消息音频格式
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".amr", ".opus"}
MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 15MB


async def _save_upload(file: UploadFile, subdir: str, allowed_exts: set, max_bytes: int, lang: str = "zh") -> str:
    """保存上传文件到 uploads/{subdir}/，返回 /uploads/... 相对路径"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "unsupported_file_format", ext=ext or tr_lang(lang, "unknown_ext")))
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "file_too_large", mb=max_bytes // 1024 // 1024))
    sub = UPLOAD_DIR / subdir
    sub.mkdir(parents=True, exist_ok=True)
    fname = f"{_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    path = sub / fname
    with open(path, "wb") as f:
        f.write(data)
    return f"/uploads/{subdir}/{fname}"


async def save_image(file: UploadFile, subdir: str, lang: str = "zh") -> str:
    """保存上传图片到 uploads/{subdir}/，返回 /uploads/... 相对路径。
    subdir 示例：'1'（私聊会话）、'moments/3'（用户朋友圈）、'avatars/5'（头像）"""
    return await _save_upload(file, subdir, ALLOWED_IMAGE_EXTS, MAX_IMAGE_BYTES, lang)


async def save_file(file: UploadFile, subdir: str, lang: str = "zh") -> str:
    """保存私聊文件到 uploads/files/{subdir}/，返回 /uploads/... 相对路径"""
    return await _save_upload(file, f"files/{subdir}", ALLOWED_FILE_EXTS, MAX_FILE_BYTES, lang)


async def save_voice(file: UploadFile, subdir: str, lang: str = "zh") -> str:
    """保存语音消息到 uploads/voice/{subdir}/，返回 /uploads/... 相对路径"""
    return await _save_upload(file, f"voice/{subdir}", ALLOWED_AUDIO_EXTS, MAX_AUDIO_BYTES, lang)


def delete_image_file(image_url: str | None) -> None:
    """按 /uploads/... 相对路径删除文件（不存在/路径异常时静默）"""
    if not image_url:
        return
    rel = image_url.removeprefix("/uploads/").lstrip("/")
    if not rel or ".." in rel or rel.startswith(("/", "\\")):
        return
    try:
        path = UPLOAD_DIR / rel
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass


async def _cleanup_expired_media(subdirs: tuple[str, ...], days: int, *, file_key: str | None = None, clear_keys: tuple[str, ...] = ()) -> tuple[int, int]:
    """共享清理实现：扫描 uploads/{subdir}/ 下超期文件→按会话分组→更新消息 extra_meta。

    file_key 非空时把 meta[file_key].url 命中者置 expired=True；clear_keys 命中者整体删除。
    返回 (删除文件数, 更新消息数)。"""
    import json as _json
    from app.db.database import async_session_factory
    from app.models.chat_message import ChatMessage
    from sqlalchemy import select

    deleted_by_session: dict[str, set[str]] = {}
    deleted = 0
    cutoff = _time.time() - days * 86400
    for sub in subdirs:
        root = UPLOAD_DIR / sub
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    rel = path.relative_to(root)
                    parts = rel.parts
                    if len(parts) < 2:
                        continue
                    url = f"/uploads/{sub}/{rel.as_posix()}"
                    path.unlink()
                    deleted_by_session.setdefault(parts[0], set()).add(url)
                    deleted += 1
            except Exception:
                continue
    updated = 0
    if deleted_by_session:
        async with async_session_factory() as db:
            for session_id, urls in deleted_by_session.items():
                try:
                    sid = int(session_id)
                except ValueError:
                    continue
                rows = (await db.execute(
                    select(ChatMessage).where(
                        ChatMessage.session_id == sid,
                        ChatMessage.extra_meta.isnot(None),
                    )
                )).scalars().all()
                for m in rows:
                    try:
                        meta = _json.loads(m.extra_meta or "{}")
                        changed = False
                        if file_key:
                            f_meta = meta.get(file_key)
                            if isinstance(f_meta, dict) and f_meta.get("url") in urls and not f_meta.get("expired"):
                                f_meta["expired"] = True
                                meta[file_key] = f_meta
                                changed = True
                        for key in clear_keys:
                            v_meta = meta.get(key)
                            if isinstance(v_meta, dict) and v_meta.get("url") in urls:
                                meta.pop(key, None)
                                changed = True
                        if changed:
                            m.extra_meta = _json.dumps(meta, ensure_ascii=False) if meta else None
                            updated += 1
                    except Exception:
                        continue
            await db.commit()
    return deleted, updated


async def cleanup_expired_files(days: int = 5) -> dict:
    """清理 uploads/files/ 下超过 days 天的文件，并把对应消息 extra_meta.file.expired 置 True。
    返回 {"deleted": 删除文件数, "marked": 标记消息数}。文件 404 时前端显示"已过期"。"""
    deleted, marked = await _cleanup_expired_media(("files",), days, file_key="file")
    _logger.info("File cleanup: deleted=%d marked=%d (days=%d)", deleted, marked, days)
    return {"deleted": deleted, "marked": marked}


async def cleanup_expired_voice(days: int = 14) -> dict:
    """清理 uploads/voice/ 与 uploads/tts/ 下超过 days 天的音频文件，
    并清空对应消息 extra_meta 中的 voice/tts 元数据（保留消息文本内容，仅语音播放失效）。
    返回 {"deleted": 删除文件数, "cleared": 清空元数据消息数}。"""
    deleted, cleared = await _cleanup_expired_media(("voice", "tts", "preview"), days, clear_keys=("voice", "tts"))
    _logger.info("Voice cleanup: deleted=%d cleared=%d (days=%d)", deleted, cleared, days)
    return {"deleted": deleted, "cleared": cleared}
