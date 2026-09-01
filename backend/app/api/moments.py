"""AI 朋友圈 API"""
from app.utils.async_tasks import spawn_background
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, Header
from sqlalchemy import select, delete, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.models.life import AIMoment, MomentLike, MomentAILike, MomentComment
from app.models.character import AICharacter
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.schemas.moment import (
    MomentResponse, MomentListResponse, LikeResponse,
    CommentResponse, CommentListResponse, CreateCommentRequest,
)
from app.utils.logger import get_logger
from app.utils.timeutil import beijing_day_start_utc, shift_utc_naive

router = APIRouter(prefix="/api/v1/moments", tags=["Moments"])
_logger = get_logger("api.moments")


def _visible_moment_filter(user_id: int):
    """当前用户可见动态：自己发的动态 + 自己 AI 角色的动态"""
    return or_(
        and_(AIMoment.sender_type == "user", AIMoment.user_id == user_id),
        AIMoment.character_id.in_(
            select(AICharacter.id).where(AICharacter.user_id == user_id)
        ),
    )


async def _is_moment_visible(db: AsyncSession, moment_id: int, user_id: int) -> bool:
    """评论等子资源接口的可见性校验：动态必须属于当前用户（本人或本人 AI 角色）"""
    moment = await db.get(AIMoment, moment_id)
    if not moment or not moment.is_active:
        return False
    if moment.sender_type == "user":
        return moment.user_id == user_id
    cresult = await db.execute(
        select(AICharacter.id).where(
            AICharacter.id == moment.character_id, AICharacter.user_id == user_id
        )
    )
    return cresult.scalar_one_or_none() is not None




async def _likers_for_moment(db: AsyncSession, moment: AIMoment) -> tuple[int, list[str]]:
    """聚合"谁赞了"：用户赞（昵称）+ AI 赞（角色名），返回 (总赞数, 名字列表)"""
    total = moment.likes_count or 0
    names: list[str] = []
    ul_result = await db.execute(
        select(MomentLike).where(MomentLike.moment_id == moment.id).order_by(MomentLike.created_at.asc())
    )
    for ul in ul_result.scalars().all():
        from app.models.user import User
        u = await db.get(User, ul.user_id)
        if u:
            names.append(u.nickname or u.username or "我")
    ai_result = await db.execute(
        select(MomentAILike).where(MomentAILike.moment_id == moment.id).order_by(MomentAILike.created_at.asc())
    )
    ai_likes = ai_result.scalars().all()
    total += len(ai_likes)
    for al in ai_likes:
        ac = await db.get(AICharacter, al.character_id)
        if ac:
            names.append(ac.name)
    return total, names


@router.get("", response_model=MomentListResponse)
async def list_moments(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    stmt = (
        select(AIMoment)
        .where(AIMoment.is_active == True, _visible_moment_filter(user_id))
        .order_by(AIMoment.created_at.desc())
        .offset(skip).limit(limit)
    )
    result = await db.execute(stmt)
    moments = result.scalars().all()

    moment_list = []
    _moment_ids = []
    for m in moments:
        char_name = ""
        avatar_url = ""
        author_tz = 8  # 作者所在时区：AI 取角色 timezone_offset，用户默认北京

        if m.sender_type == "ai" and m.character_id:
            char_result = await db.execute(select(AICharacter).where(AICharacter.id == m.character_id, AICharacter.is_active == True))
            char = char_result.scalar_one_or_none()
            if not char:
                continue
            char_name = char.name
            avatar_url = char.avatar_url or ""
            author_tz = char.timezone_offset if char.timezone_offset is not None else 8
        elif m.sender_type == "user" and m.user_id:
            from app.models.user import User
            u_result = await db.execute(select(User).where(User.id == m.user_id))
            u = u_result.scalar_one_or_none()
            char_name = u.nickname if u and u.nickname else (u.username if u else "我")
            avatar_url = (u.avatar_url if u else "") or ""

        # 跳过已删除角色的动态
        if m.sender_type == "ai" and m.character_id:
            cr = await db.execute(select(AICharacter).where(AICharacter.id == m.character_id, AICharacter.is_active == True))
            if cr.scalar_one_or_none() is None:
                continue

        like_result = await db.execute(
            select(MomentLike).where(MomentLike.moment_id == m.id, MomentLike.user_id == user_id)
        )
        liked_by_me = like_result.first() is not None

        total_likes, likers = await _likers_for_moment(db, m)

        moment_list.append(MomentResponse(
            id=m.id, character_id=m.character_id or 0,
            character_name=char_name, user_id=m.user_id or 0,
            sender_type=m.sender_type, content=m.content,
            image_url=m.image_url, image_desc=m.image_desc,
            avatar_url=avatar_url,
            likes_count=total_likes, is_active=m.is_active,
            created_at=m.created_at, author_tz_offset=author_tz,
            liked_by_me=liked_by_me,
            likers=likers,
        ))
        _moment_ids.append(m.id)

    # P2-3 评论批量加载：一次查全部动态的评论并组装树（消除前端 N+1）
    if _moment_ids:
        cres = await db.execute(
            select(MomentComment).where(MomentComment.moment_id.in_(_moment_ids))
            .order_by(MomentComment.created_at.asc())
        )
        _trees = _build_comment_trees(cres.scalars().all())
        for item in moment_list:
            item.comments = _trees.get(item.id, [])

    return MomentListResponse(moments=moment_list, total=len(moment_list))


@router.post("/publish/{character_id}")
async def manually_publish_moment(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """手动为角色发布一条朋友圈"""
    cresult = await db.execute(select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id == user_id))
    if cresult.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    from app.services.moment_service import publish_moment, generate_comments_for_moment
    try:
        result = await publish_moment(character_id, skip_interval=True)
        if result is None:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "moment_daily_limit_or_not_found"))
        # P0 发布即评论：手动发布后立即让其他 AI 角色评论（异步，不阻塞）
        try:
            spawn_background(generate_comments_for_moment(result["id"]))
        except Exception:
            pass
        return {"success": True, "moment": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user", response_model=MomentResponse)
async def create_user_moment(
    content: str = Form(""),
    image: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """用户发布朋友圈（支持图片 1 张；图片描述经 VLM+OCR 生成，供 AI 评论理解）"""
    content = (content or "").strip()
    if not content and image is None:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty"))
    if len(content) > 500:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_too_long"))
    image_url = None
    image_desc = ""
    if image is not None:
        from app.services.upload_service import save_image
        from app.services.image_understanding_service import describe_image
        image_url = await save_image(image, f"moments/{user_id}", lang)
        try:
            abs_path = str(settings.PROJECT_ROOT / "data" / "uploads" / image_url.removeprefix("/uploads/"))
            image_desc = (await describe_image(abs_path, user_id=user_id)) or ""
        except Exception as e:
            _logger.warning("Moment image describe failed: %s", e)
    moment = AIMoment(
        character_id=0, user_id=user_id, sender_type="user",
        content=content or "[图片]", image_url=image_url,
        image_desc=image_desc or None,
    )
    db.add(moment)
    await db.commit()
    await db.refresh(moment)
    # P0 发布即评论：用户发布后立即让 AI 角色评论（异步，不阻塞；隔离由评论生成内部保证）
    try:
        from app.services.moment_service import generate_comments_for_moment
        spawn_background(generate_comments_for_moment(moment.id))
    except Exception:
        pass
    from app.models.user import User as _User
    ures = await db.execute(select(_User).where(_User.id == user_id))
    _u = ures.scalar_one_or_none()
    return MomentResponse(
        id=moment.id, character_id=0, character_name="",
        avatar_url=(_u.avatar_url if _u else "") or "",
        user_id=user_id, sender_type="user", content=moment.content,
        image_url=moment.image_url, image_desc=moment.image_desc,
        likes_count=0, is_active=True, created_at=moment.created_at,
    )


@router.post("/{moment_id}/like", response_model=LikeResponse)
async def like_moment(moment_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    # 多用户隔离：只能点赞自己可见的动态
    if not await _is_moment_visible(db, moment_id, user_id):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "moment_not_found"))
    result = await db.execute(select(AIMoment).where(AIMoment.id == moment_id))
    moment = result.scalar_one_or_none()
    like_result = await db.execute(
        select(MomentLike).where(MomentLike.moment_id == moment_id, MomentLike.user_id == user_id)
    )
    existing = like_result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        moment.likes_count = max(0, moment.likes_count - 1)
        await db.commit()
        total_likes, _ = await _likers_for_moment(db, moment)
        return LikeResponse(moment_id=moment_id, likes_count=total_likes, liked=False)
    else:
        like = MomentLike(moment_id=moment_id, user_id=user_id)
        db.add(like)
        moment.likes_count = moment.likes_count + 1
        await db.commit()
        total_likes, _ = await _likers_for_moment(db, moment)
        return LikeResponse(moment_id=moment_id, likes_count=total_likes, liked=True)


# ── 评论 ──


@router.get("/{moment_id}/comments", response_model=CommentListResponse)
async def list_comments(moment_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    # 多用户隔离：只能看自己可见动态的评论
    if not await _is_moment_visible(db, moment_id, user_id):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "moment_not_found"))
    result = await db.execute(
        select(MomentComment).where(MomentComment.moment_id == moment_id).order_by(MomentComment.created_at.asc())
    )
    comments = result.scalars().all()
    # 构建树结构
    cmap = {}
    roots = []
    for c in comments:
        cr = CommentResponse(
            id=c.id, moment_id=c.moment_id, parent_id=c.parent_id,
            sender_type=c.sender_type, sender_id=c.sender_id,
            sender_name=c.sender_name, content=c.content,
            created_at=c.created_at, replies=[],
        )
        cmap[c.id] = cr
    for c in comments:
        cr = cmap[c.id]
        if c.parent_id and c.parent_id in cmap:
            cmap[c.parent_id].replies.append(cr)
        elif not c.parent_id:
            roots.append(cr)
    return CommentListResponse(comments=roots, total=len(comments))


@router.post("/{moment_id}/comments")
async def create_comment(moment_id: int, data: CreateCommentRequest, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """用户发表评论或回复（多用户隔离：只能评论自己可见的动态）"""
    if not await _is_moment_visible(db, moment_id, user_id):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "moment_not_found"))
    if not data.content.strip():
        raise HTTPException(status_code=400, detail=tr_lang(lang, "comment_empty"))
    if len(data.content) > 200:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "comment_too_long"))
    if data.parent_id:
        presult = await db.execute(
            select(MomentComment).where(MomentComment.id == data.parent_id, MomentComment.moment_id == moment_id)
        )
        if not presult.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=tr_lang(lang, "replied_comment_not_found"))
    from app.models.user import User
    uresult = await db.execute(select(User).where(User.id == user_id))
    user = uresult.scalar_one_or_none()
    uname = user.nickname if user and user.nickname else "用户"
    comment = MomentComment(
        moment_id=moment_id, parent_id=data.parent_id,
        sender_type="user", sender_id=user_id, sender_name=uname,
        user_id=user_id,
        content=data.content.strip(),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    # AI 回复用户评论（异步不阻塞）：动态作者 / 其他 AI 角色按幂等规则回复；重复触发安全
    try:
        from app.services.moment_service import generate_comments_for_moment
        spawn_background(generate_comments_for_moment(moment_id))
    except Exception:
        pass
    return CommentResponse(
        id=comment.id, moment_id=comment.moment_id, parent_id=comment.parent_id,
        sender_type=comment.sender_type, sender_id=comment.sender_id,
        sender_name=comment.sender_name, content=comment.content,
        created_at=comment.created_at,
    )


@router.delete("/{moment_id}")
async def delete_moment(moment_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """删除朋友圈动态（用户可删除任意动态：自己的或 AI 的），级联清理评论/点赞/图片"""
    moment = await db.get(AIMoment, moment_id)
    if not moment:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "moment_not_found"))
    if not moment.is_active:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "moment_not_found"))
    # 归属校验：用户动态须本人；AI 动态须本人角色
    if moment.sender_type == "user":
        if moment.user_id != user_id:
            raise HTTPException(status_code=403, detail=tr_lang(lang, "delete_own_moment_only"))
    else:
        cresult = await db.execute(select(AICharacter.id).where(AICharacter.id == moment.character_id, AICharacter.user_id == user_id))
        if cresult.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail=tr_lang(lang, "delete_own_moment_only"))
    from app.services.upload_service import delete_image_file
    delete_image_file(moment.image_url)
    await db.execute(delete(MomentLike).where(MomentLike.moment_id == moment_id))
    await db.execute(delete(MomentAILike).where(MomentAILike.moment_id == moment_id))
    await db.execute(delete(MomentComment).where(MomentComment.moment_id == moment_id))
    moment.is_active = False
    await db.commit()
    _logger.info("Moment %d deleted by user %d", moment_id, user_id)
    return {"success": True, "deleted": moment_id}


@router.delete("/{moment_id}/comments/{comment_id}")
async def delete_comment(moment_id: int, comment_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    result = await db.execute(
        select(MomentComment).where(MomentComment.id == comment_id, MomentComment.moment_id == moment_id)
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "comment_not_found"))
    if comment.sender_type != "user" or comment.sender_id != user_id:
        raise HTTPException(status_code=403, detail=tr_lang(lang, "delete_own_comment_only"))
    await db.delete(comment)
    await db.commit()
    return {"status": "ok"}


@router.delete("/clear/{character_id}")
async def clear_character_moments(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    cresult = await db.execute(select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id == user_id))
    if cresult.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    start = beijing_day_start_utc()
    stmt = select(AIMoment).where(AIMoment.character_id == character_id, AIMoment.created_at >= start)
    result = await db.execute(stmt)
    moments = result.scalars().all()
    from app.services.upload_service import delete_image_file
    for m in moments:
        delete_image_file(m.image_url)
        await db.delete(m)
    await db.commit()
    return {"deleted": len(moments), "character_id": character_id}


def _build_comment_trees(comments) -> dict[int, list]:
    """把平铺评论按 parent 关系组装成树（按动态分组），复用列表接口的树结构。"""
    from app.schemas.moment import CommentResponse
    trees: dict[int, dict] = {}
    for c in comments:
        cr = CommentResponse(
            id=c.id, moment_id=c.moment_id, parent_id=c.parent_id,
            sender_type=c.sender_type, sender_id=c.sender_id,
            sender_name=c.sender_name, content=c.content,
            created_at=c.created_at, replies=[],
        )
        trees.setdefault(c.moment_id, {})[c.id] = cr
    out: dict[int, list] = {}
    for mid, cmap in trees.items():
        roots = []
        for c in comments:
            if c.moment_id != mid:
                continue
            cr = cmap[c.id]
            if c.parent_id and c.parent_id in cmap:
                cmap[c.parent_id].replies.append(cr)
            elif not c.parent_id:
                roots.append(cr)
        out[mid] = roots
    return out


@router.get("/unread-comments")
async def unread_comments(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """P2-4 回复提醒：last_read_at 之后，AI 回复过我的评论的条数（朋友圈 tab 红点用）"""
    from app.models.life import MomentReadMark
    from datetime import timedelta
    mark = await db.get(MomentReadMark, user_id)
    since = mark.last_read_at if mark and mark.last_read_at else (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7))
    if since.tzinfo is not None:
        since = since.replace(tzinfo=None)
    my_comment_ids = select(MomentComment.id).where(
        MomentComment.sender_type == "user", MomentComment.user_id == user_id)
    cnt = await db.execute(
        select(func.count()).select_from(MomentComment).where(
            MomentComment.parent_id.in_(my_comment_ids),
            MomentComment.sender_type == "ai",
            MomentComment.created_at > since,
        )
    )
    return {"count": cnt.scalar() or 0}


@router.post("/read")
async def mark_moments_read(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """进入朋友圈页时上报已读（重置回复提醒红点）"""
    from app.models.life import MomentReadMark
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    mark = await db.get(MomentReadMark, user_id)
    if mark:
        mark.last_read_at = now
    else:
        db.add(MomentReadMark(user_id=user_id, last_read_at=now))
    await db.commit()
    return {"status": "ok"}


@router.get("/archive")
async def list_moments_archive(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """朋友圈归档—按日期分组"""
    from collections import defaultdict

    stmt = (
        select(AIMoment)
        .where(AIMoment.is_active == True, _visible_moment_filter(user_id))
        .order_by(AIMoment.created_at.desc())
        .limit(200)
    )
    result = await db.execute(stmt)
    moments = result.scalars().all()

    days = defaultdict(list)
    for m in moments:
        author_tz = 8  # 作者所在时区：AI 取角色 timezone_offset，用户默认北京；日期分组按作者地区
        char_name = ""
        avatar_url = ""
        if m.sender_type == "ai" and m.character_id:
            char_result = await db.execute(select(AICharacter).where(AICharacter.id == m.character_id, AICharacter.is_active == True))
            char = char_result.scalar_one_or_none()
            if not char:
                continue
            char_name = char.name
            avatar_url = char.avatar_url or ""
            author_tz = char.timezone_offset if char.timezone_offset is not None else 8
        elif m.sender_type == "user" and m.user_id:
            from app.models.user import User
            u_result = await db.execute(select(User).where(User.id == m.user_id))
            u = u_result.scalar_one_or_none()
            char_name = u.nickname if u and u.nickname else (u.username if u else "我")
            avatar_url = (u.avatar_url if u else "") or ""

        day_key = shift_utc_naive(m.created_at, author_tz).strftime("%Y-%m-%d")

        like_result = await db.execute(
            select(MomentLike).where(MomentLike.moment_id == m.id, MomentLike.user_id == user_id)
        )
        liked_by_me = like_result.first() is not None
        total_likes, likers = await _likers_for_moment(db, m)

        days[day_key].append({
            "id": m.id, "character_id": m.character_id or 0,
            "character_name": char_name, "avatar_url": avatar_url,
            "user_id": m.user_id or 0,
            "sender_type": m.sender_type, "content": m.content,
            "image_url": m.image_url, "image_desc": m.image_desc,
            "likes_count": total_likes, "likers": likers, "is_active": m.is_active,
            "created_at": m.created_at.isoformat(), "author_tz_offset": author_tz, "liked_by_me": liked_by_me,
        })

    result_list = []
    for date_str in sorted(days.keys(), reverse=True):
        result_list.append({
            "date": date_str,
            "moments": days[date_str],
            "count": len(days[date_str]),
        })

    return {"days": result_list, "total_days": len(result_list)}