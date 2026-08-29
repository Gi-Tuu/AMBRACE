"""小手机桌面 API（2026-08-11）：桌面布局 / 相册 / 日历备注 / 浏览器搜索历史 / 天气
关联：AI 互动 → 角色小手机（flutter ai_interaction_screen）；日历备注与搜索历史注入聊天上下文。
"""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Header
from sqlalchemy import delete, select

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import async_session_factory
from app.models.phone_desktop import PhoneDesktop, PhoneLayout, CalendarNote, BrowserHistory, MemoNote
from app.services.upload_service import UPLOAD_DIR, save_image
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/phone-desktop", tags=["Phone Desktop"])
_logger = get_logger("api.phone_desktop")


async def _check_character_owned(character_id: int, user_id: int) -> None:
    """P0-6 安全加固（2026-08-16）：校验角色归属当前用户，不匹配 404（防跨用户读写删）"""
    from app.models.character import AICharacter
    async with async_session_factory() as _db:
        row = (await _db.execute(
            select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id == user_id)
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="character not found")

# 应用目录：前端渲染用同一份约定；plugin 字段 = 扩展附属（关闭扩展不显示）
APP_DIR: dict[str, dict] = {
    "chat": {"label": "畅聊", "deletable": False},
    "album": {"label": "相册", "deletable": False},
    "market": {"label": "应用市场", "deletable": False},
    "calendar": {"label": "日历", "deletable": False},
    "browser": {"label": "浏览器", "deletable": True, "plugin": "browser_mcp"},
    "theme": {"label": "主题", "deletable": False},
    "settings": {"label": "设置", "deletable": False},
    "memo": {"label": "备忘录", "deletable": False},
    "pets": {"label": "宠物", "deletable": False},
}
BROWSER_PLUGIN = "browser_mcp"
HISTORY_DAYS = 7
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _browser_plugin_enabled() -> bool:
    try:
        from app.plugins import registry
        plugin = registry.get_plugin(BROWSER_PLUGIN)
        return bool(plugin and plugin.get("enabled"))
    except Exception:
        return False


def _list_images(rel_dir: Path) -> list[str]:
    if not rel_dir.is_dir():
        return []
    return [
        f"/uploads/{rel_dir.relative_to(UPLOAD_DIR).as_posix()}/{f.name}"
        for f in sorted(rel_dir.iterdir())
        if f.is_file() and f.suffix.lower() in _IMG_EXTS
    ]


@router.get("/layouts")
async def get_layouts(character_id: int, user_id: int = Depends(get_current_user_id)):
    await _check_character_owned(character_id, user_id)
    """桌面布局 + 壁纸 + 浏览器插件状态 + 应用目录"""
    async with async_session_factory() as db:
        desk = (await db.execute(
            select(PhoneDesktop).where(PhoneDesktop.character_id == character_id)
        )).scalar_one_or_none()
        rows = (await db.execute(
            select(PhoneLayout).where(PhoneLayout.character_id == character_id)
        )).scalars().all()
    return {
        "apps": [{"key": r.app_key, "pos": r.pos, "is_hidden": r.is_hidden} for r in rows],
        "wallpaper": desk.wallpaper if desk else None,
        "browser_plugin_enabled": _browser_plugin_enabled(),
        "catalog": [{"key": k, **v} for k, v in APP_DIR.items()],
    }


@router.put("/layouts")
async def save_layouts(character_id: int, body: dict, user_id: int = Depends(get_current_user_id)):
    await _check_character_owned(character_id, user_id)
    """保存桌面布局（全量覆盖 apps + wallpaper）"""
    apps = body.get("apps") or []
    wallpaper = str(body.get("wallpaper") or "")[:500] or None
    async with async_session_factory() as db:
        desk = (await db.execute(
            select(PhoneDesktop).where(PhoneDesktop.character_id == character_id)
        )).scalar_one_or_none()
        if desk is None:
            db.add(PhoneDesktop(character_id=character_id, wallpaper=wallpaper))
        else:
            desk.wallpaper = wallpaper
        await db.execute(delete(PhoneLayout).where(PhoneLayout.character_id == character_id))
        for a in apps:
            key = str(a.get("key") or "")[:30]
            if key not in APP_DIR:
                continue
            db.add(PhoneLayout(
                character_id=character_id,
                app_key=key,
                pos=int(a.get("pos") or 0),
                is_hidden=bool(a.get("is_hidden")),
            ))
        await db.commit()
    return {"status": "ok"}


@router.get("/photos")
async def list_photos(user_id: int = Depends(get_current_user_id)):
    """相册：AI 生成图片（uploads/images/{uid}/）+ 用户上传（uploads/phone/{uid}/album/）"""
    ai_dir = UPLOAD_DIR / "images" / str(user_id)
    album_dir = UPLOAD_DIR / "phone" / str(user_id) / "album"
    return {"ai_photos": _list_images(ai_dir), "user_photos": _list_images(album_dir)}


@router.post("/photos")
async def upload_photo(image: UploadFile = File(...), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """相册上传（存入 uploads/phone/{uid}/album/）"""
    try:
        url = await save_image(image, f"phone/{user_id}/album", lang)
    except Exception as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "image_save_failed", err=e))
    return {"url": url}


@router.post("/photos/save")
async def save_photo_to_album(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """把 AI 生成图片保存到我的相册（复制 uploads/images/{uid}/ → uploads/phone/{uid}/album/，幂等）"""
    name = Path(str(body.get("filename") or "")).name
    if Path(name).suffix.lower() not in _IMG_EXTS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "illegal_filename"))
    src = UPLOAD_DIR / "images" / str(user_id) / name
    if not src.is_file():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "image_not_found"))
    album_dir = UPLOAD_DIR / "phone" / str(user_id) / "album"
    album_dir.mkdir(parents=True, exist_ok=True)
    dst = album_dir / name
    if not dst.exists():
        shutil.copy2(src, dst)
    _logger.info("相册保存 source=ai uid=%s file=%s", user_id, name)
    return {"url": f"/uploads/phone/{user_id}/album/{name}"}


@router.delete("/photos")
async def delete_photo(source: str, filename: str, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """相册删除：source=ai（生图目录）或 user（我的上传），filename 为 URL 末段文件名"""
    name = Path(filename).name
    if Path(name).suffix.lower() not in _IMG_EXTS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "illegal_filename"))
    if source == "ai":
        rel_dir = UPLOAD_DIR / "images" / str(user_id)
    elif source == "user":
        rel_dir = UPLOAD_DIR / "phone" / str(user_id) / "album"
    else:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "source_invalid"))
    target = rel_dir / name
    if target.is_file():
        target.unlink()
        _logger.info("相册删除 source=%s uid=%s file=%s", source, user_id, name)
    return {"status": "ok"}


@router.get("/calendar-notes")
async def list_calendar_notes(character_id: int, month: str | None = None, user_id: int = Depends(get_current_user_id)):
    await _check_character_owned(character_id, user_id)
    """日历备注列表（可选按月 YYYY-MM 过滤）"""
    async with async_session_factory() as db:
        q = select(CalendarNote).where(CalendarNote.character_id == character_id)
        if month and len(month) == 7:
            q = q.where(CalendarNote.note_date.like(f"{month}%"))
        rows = (await db.execute(q.order_by(CalendarNote.note_date))).scalars().all()
    return {"notes": [
        {"id": r.id, "date": r.note_date, "text": r.note_text, "author": r.author,
         "created_at": r.created_at.isoformat() if r.created_at else ""}
        for r in rows
    ]}


@router.post("/calendar-notes")
async def add_calendar_note(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """新增/覆盖某日备注（同日多备注，前端合并展示）"""
    character_id = int(body.get("character_id") or 0)
    date = str(body.get("date") or "").strip()[:10]
    text = str(body.get("text") or "").strip()[:500]
    if character_id <= 0 or len(date) != 10 or not text:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_missing_char_date_content"))
    await _check_character_owned(character_id, user_id)
    _author = None
    try:
        from app.models.user import User
        async with async_session_factory() as db2:
            _u = (await db2.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if _u:
            _author = (_u.nickname or _u.username or "我")[:50]
    except Exception:
        _author = None
    async with async_session_factory() as db:
        row = CalendarNote(character_id=character_id, note_date=date, note_text=text, author=_author or "我")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return {"id": row.id, "date": row.note_date, "text": row.note_text, "author": row.author}


@router.delete("/calendar-notes/{note_id}")
async def delete_calendar_note(note_id: int, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        row = (await db.execute(
            select(CalendarNote).where(CalendarNote.id == note_id)
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        await _check_character_owned(row.character_id, user_id)
        await db.execute(delete(CalendarNote).where(CalendarNote.id == note_id))
        await db.commit()
    return {"status": "ok"}


@router.get("/memos")
async def list_memos(character_id: int, user_id: int = Depends(get_current_user_id)):
    await _check_character_owned(character_id, user_id)
    """备忘录列表（倒序，最多 100 条；AI 与用户共同维护）"""
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(MemoNote)
            .where(MemoNote.character_id == character_id)
            .order_by(MemoNote.created_at.desc())
            .limit(100)
        )).scalars().all()
    return {"items": [
        {"id": r.id, "text": r.text, "author": r.author,
         "created_at": r.created_at.isoformat() if r.created_at else ""}
        for r in rows
    ]}


@router.post("/memos")
async def add_memo(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """新增备忘录（同内容去重刷新）"""
    character_id = int(body.get("character_id") or 0)
    text = str(body.get("text") or "").strip()[:300]
    if character_id <= 0 or not text:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_missing_char_content"))
    await _check_character_owned(character_id, user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        existing = (await db.execute(
            select(MemoNote).where(
                MemoNote.character_id == character_id, MemoNote.text == text)
        )).scalar_one_or_none()
        _author = None
        try:
            from app.models.user import User
            async with async_session_factory() as db3:
                _u = (await db3.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if _u:
                _author = (_u.nickname or _u.username or "我")[:50]
        except Exception:
            _author = None
        if existing:
            existing.created_at = now
            await db.commit()
            return {"id": existing.id, "text": existing.text, "author": existing.author}
        row = MemoNote(character_id=character_id, text=text, created_at=now, author=_author or "我")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return {"id": row.id, "text": row.text, "author": row.author}


@router.delete("/memos/{memo_id}")
async def delete_memo(memo_id: int, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        row = (await db.execute(select(MemoNote).where(MemoNote.id == memo_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        await _check_character_owned(row.character_id, user_id)
        await db.execute(delete(MemoNote).where(MemoNote.id == memo_id))
        await db.commit()
    return {"status": "ok"}


@router.get("/browser-history")
async def list_browser_history(character_id: int, user_id: int = Depends(get_current_user_id)):
    await _check_character_owned(character_id, user_id)
    """浏览器搜索历史（保留 7 天，最多 50 条）"""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=HISTORY_DAYS)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(BrowserHistory)
            .where(BrowserHistory.character_id == character_id, BrowserHistory.created_at >= cutoff)
            .order_by(BrowserHistory.created_at.desc())
            .limit(50)
        )).scalars().all()
    return {"items": [
        {"id": r.id, "query": r.query, "created_at": r.created_at.isoformat() if r.created_at else ""}
        for r in rows
    ]}


@router.post("/browser-history")
async def add_browser_history(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """记录搜索（同词去重刷新时间）；无浏览器插件时仍记录（AI 上下文可见）"""
    character_id = int(body.get("character_id") or 0)
    query = str(body.get("query") or "").strip()[:200]
    if character_id <= 0 or not query:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_missing_char_keyword"))
    await _check_character_owned(character_id, user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        existing = (await db.execute(
            select(BrowserHistory).where(
                BrowserHistory.character_id == character_id, BrowserHistory.query == query)
        )).scalar_one_or_none()
        if existing:
            existing.created_at = now
            await db.commit()
            return {"id": existing.id, "query": existing.query, "refreshed": True}
        row = BrowserHistory(character_id=character_id, query=query, created_at=now)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return {"id": row.id, "query": row.query}


@router.delete("/browser-history/{hist_id}")
async def delete_browser_history(hist_id: int, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        row = (await db.execute(select(BrowserHistory).where(BrowserHistory.id == hist_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        await _check_character_owned(row.character_id, user_id)
        await db.execute(delete(BrowserHistory).where(BrowserHistory.id == hist_id))
        await db.commit()
    return {"status": "ok"}


@router.get("/search")
async def search_web(q: str, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """真实搜索：浏览器插件（browser_mcp）打开 Bing 抓取结果（仅插件启用时可用）；
    结果文本来自插件 browse 的网页摘要，链接为页面内链接。"""
    query = (q or "").strip()[:100]
    if not query:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_missing_keyword"))
    if not _browser_plugin_enabled():
        raise HTTPException(status_code=400, detail=tr_lang(lang, "browser_extension_disabled"))
    import sys
    mod = sys.modules.get("ai_plugin_browser_mcp")
    if mod is None:
        return {"ok": False, "message": "浏览器插件未加载"}
    try:
        res = await mod.search_web(query)
    except Exception as e:
        return {"ok": False, "message": f"搜索失败: {e}"}
    return {
        "ok": bool(res.get("ok")),
        "query": query,
        "engine": res.get("engine") or "",
        "results": res.get("results") or [],
        "message": res.get("message") or "",
    }


@router.get("/weather")
async def phone_weather(user_id: int = Depends(get_current_user_id)):
    """天气小组件：复用用户位置天气（未开启返回提示）"""
    from app.services.weather_service import get_user_weather_line
    line = await get_user_weather_line(user_id)
    return {"line": line or "未开启位置信息，无法获取天气"}
