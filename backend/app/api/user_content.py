"""用户备忘录 + 用户日记 API（供角色聊天阅读的上下文来源）"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.life import UserMemo
from app.models.life import UserDiary
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/user", tags=["UserContent"])
_logger = get_logger("api.user_content")


def _memo_json(m):
    return {
        "id": m.id, "title": m.title, "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def _diary_json(d):
    return {
        "id": d.id, "diary_date": d.diary_date, "content": d.content,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


# ── 备忘录 ──
@router.get("/memos")
async def list_memos(user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserMemo).where(UserMemo.user_id == user_id).order_by(UserMemo.updated_at.desc())
        )
        memos = result.scalars().all()
    return {"memos": [_memo_json(m) for m in memos], "total": len(memos)}


@router.post("/memos", status_code=201)
async def create_memo(
    payload: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    title = str(payload.get("title") or "").strip()[:100]
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty"))
    async with async_session_factory() as db:
        memo = UserMemo(user_id=user_id, title=title or None, content=content[:2000])
        db.add(memo)
        await db.commit()
        await db.refresh(memo)
    return _memo_json(memo)


@router.put("/memos/{memo_id}")
async def update_memo(
    memo_id: int,
    payload: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    async with async_session_factory() as db:
        memo = await db.get(UserMemo, memo_id)
        if memo is None or memo.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "memo_not_found"))
        if "title" in payload:
            memo.title = str(payload["title"] or "").strip()[:100] or None
        if "content" in payload:
            content = str(payload["content"] or "").strip()
            if not content:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty"))
            memo.content = content[:2000]
        await db.commit()
        await db.refresh(memo)
    return _memo_json(memo)


@router.delete("/memos/{memo_id}")
async def delete_memo(
    memo_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    async with async_session_factory() as db:
        memo = await db.get(UserMemo, memo_id)
        if memo is None or memo.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "memo_not_found"))
        await db.delete(memo)
        await db.commit()
    return {"status": "ok"}


# ── 用户日记 ──
@router.get("/diaries")
async def list_diaries(user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserDiary).where(UserDiary.user_id == user_id).order_by(UserDiary.diary_date.desc())
        )
        diaries = result.scalars().all()
    return {"diaries": [_diary_json(d) for d in diaries], "total": len(diaries)}


@router.get("/diaries/{diary_date}")
async def get_diary(
    diary_date: str,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    try:
        date.fromisoformat(diary_date)
    except ValueError:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "date_format_invalid"))
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserDiary).where(
                UserDiary.user_id == user_id, UserDiary.diary_date == diary_date
            )
        )
        diary = result.scalar_one_or_none()
    if diary is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "no_diary_today"))
    return _diary_json(diary)


@router.post("/diaries", status_code=201)
async def upsert_diary(
    payload: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    diary_date = str(payload.get("diary_date") or "").strip()
    content = str(payload.get("content") or "").strip()
    try:
        date.fromisoformat(diary_date)
    except ValueError:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "date_format_invalid"))
    if not content:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty"))
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserDiary).where(
                UserDiary.user_id == user_id, UserDiary.diary_date == diary_date
            )
        )
        diary = result.scalar_one_or_none()
        if diary is None:
            diary = UserDiary(user_id=user_id, diary_date=diary_date, content=content[:5000])
            db.add(diary)
        else:
            diary.content = content[:5000]
        await db.commit()
        await db.refresh(diary)
    return _diary_json(diary)


@router.delete("/diaries/{diary_id}")
async def delete_diary(
    diary_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    async with async_session_factory() as db:
        diary = await db.get(UserDiary, diary_id)
        if diary is None or diary.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "diary_not_found"))
        await db.delete(diary)
        await db.commit()
    return {"status": "ok"}
