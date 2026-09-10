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
# 3.10 事件流水（P1）：朋友圈 api 侧变更点（用户发布/评论/点赞/删除/清算/已读）
from app.events.store import append_domain_event
from app.events.types import EventType as _ET

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

    # ── T6 批量预取：角色 / 用户 / 点赞，消除主循环 N+1（原每条 AI 动态查角色 2 次 + 逐条查点赞）──
    ai_ids = {m.character_id for m in moments if m.sender_type == "ai" and m.character_id}
    user_ids = {m.user_id for m in moments if m.sender_type == "user" and m.user_id}
    moment_ids = [m.id for m in moments]

    from app.models.user import User
    char_map: dict[int, AICharacter] = {}
    if ai_ids:
        char_map = {
            c.id: c for c in (await db.execute(
                select(AICharacter).where(AICharacter.id.in_(ai_ids), AICharacter.is_active == True)  # noqa: E712
            )).scalars().all()
        }
    user_map: dict[int, User] = {}
    if user_ids:
        user_map = {u.id: u for u in (await db.execute(
            select(User).where(User.id.in_(user_ids))
        )).scalars().all()}

    ul_rows: list[MomentLike] = []
    ai_like_rows: list[MomentAILike] = []
    liked_by_me_ids: set[int] = set()
    ai_like_count: dict[int, int] = {}
    if moment_ids:
        ul_rows = (await db.execute(
            select(MomentLike).where(MomentLike.moment_id.in_(moment_ids))
            .order_by(MomentLike.created_at.asc())
        )).scalars().all()
        liked_by_me_ids = {r.moment_id for r in ul_rows if r.user_id == user_id}

        ai_like_rows = (await db.execute(
            select(MomentAILike).where(MomentAILike.moment_id.in_(moment_ids))
            .order_by(MomentAILike.created_at.asc())
        )).scalars().all()
        for mid, cnt in (await db.execute(
            select(MomentAILike.moment_id, func.count(MomentAILike.id))
            .where(MomentAILike.moment_id.in_(moment_ids))
            .group_by(MomentAILike.moment_id)
        )).all():
            ai_like_count[mid] = cnt

        liker_user_ids = {r.user_id for r in ul_rows if r.user_id}
        liker_char_ids = {r.character_id for r in ai_like_rows if r.character_id}
        liker_user_map = {u.id: (u.nickname or u.username or "我") for u in (
            await db.execute(select(User).where(User.id.in_(liker_user_ids)))
        ).scalars().all()} if liker_user_ids else {}
        liker_char_map = {c.id: c.name for c in (
            await db.execute(select(AICharacter).where(AICharacter.id.in_(liker_char_ids)))
        ).scalars().all()} if liker_char_ids else {}
    else:
        liker_user_map, liker_char_map = {}, {}

    ul_by_moment: dict[int, list[MomentLike]] = {}
    for r in ul_rows:
        ul_by_moment.setdefault(r.moment_id, []).append(r)
    ail_by_moment: dict[int, list[MomentAILike]] = {}
    for r in ai_like_rows:
        ail_by_moment.setdefault(r.moment_id, []).append(r)

    def _likers_batched(m: AIMoment) -> tuple[int, list[str]]:
        names = [liker_user_map[r.user_id] for r in ul_by_moment.get(m.id, []) if r.user_id in liker_user_map]
        names += [liker_char_map[r.character_id] for r in ail_by_moment.get(m.id, []) if r.character_id in liker_char_map]
        total = (m.likes_count or 0) + ai_like_count.get(m.id, 0)
        return total, names

    for m in moments:
        char_name = ""
        avatar_url = ""
        author_tz = 8  # 作者所在时区：AI 取角色 timezone_offset，用户默认北京

        if m.sender_type == "ai" and m.character_id:
            char = char_map.get(m.character_id)
            if not char:  # 角色已删/停用：跳过（合并原 98/113 两次查询为一次字典命中）
                continue
            char_name = char.name
            avatar_url = char.avatar_url or ""
            author_tz = char.timezone_offset if char.timezone_offset is not None else 8
        elif m.sender_type == "user" and m.user_id:
            u = user_map.get(m.user_id)
            char_name = u.nickname if u and u.nickname else (u.username if u else "我")
            avatar_url = (u.avatar_url if u else "") or ""

        total_likes, likers = _likers_batched(m)

        moment_list.append(MomentResponse(
            id=m.id, character_id=m.character_id or 0,
            character_name=char_name, user_id=m.user_id or 0,
            sender_type=m.sender_type, content=m.content,
            image_url=m.image_url, image_desc=m.image_desc,
            avatar_url=avatar_url,
            likes_count=total_likes, is_active=m.is_active,
            created_at=m.created_at, author_tz_offset=author_tz,
            liked_by_me=(m.id in liked_by_me_ids),
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
    from app.application.moment_service import publish_moment, generate_comments_for_moment
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
        from app.application.upload_service import save_image
        from app.application.image_understanding_service import describe_image
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
    # 3.10 事件流水（P1）：用户发布动态（actor=user；图片只存描述前 200 字，不存二进制）
    await append_domain_event(
        _ET.MOMENT_PUBLISHED.value, "moment", moment.id,
        entity_type="ai_moment", entity_id=moment.id,
        actor_type="user", actor_id=user_id,
        payload={"sender_type": "user", "content": (moment.content or "")[:200],
                 "has_image": bool(moment.image_url)},
        idempotency_key=f"moment.published:ai_moment:{moment.id}",
        origin="user_message",
    )
    # P0 发布即评论：用户发布后立即让 AI 角色评论（异步，不阻塞；隔离由评论生成内部保证）
    try:
        from app.application.moment_service import generate_comments_for_moment
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
        _removed_like_id = existing.id
        await db.delete(existing)
        moment.likes_count = max(0, moment.likes_count - 1)
        await db.commit()
        # 3.10 事件流水（P1）：取消赞（此时 like 行已删，用 (moment,user,被删 like_id) 组幂等键）
        await append_domain_event(
            _ET.MOMENT_UNLIKED.value, "moment", moment_id,
            actor_type="user", actor_id=user_id,
            payload={"action": "unliked", "removed_like_id": _removed_like_id},
            idempotency_key=f"moment.unliked:{moment_id}:{user_id}:{_removed_like_id}",
            origin="user_message",
        )
        total_likes, _ = await _likers_for_moment(db, moment)
        return LikeResponse(moment_id=moment_id, likes_count=total_likes, liked=False)
    else:
        like = MomentLike(moment_id=moment_id, user_id=user_id)
        db.add(like)
        moment.likes_count = moment.likes_count + 1
        await db.commit()
        await db.refresh(like)
        # 3.10 事件流水（P1）：点赞（toggle 之「赞」）
        await append_domain_event(
            _ET.MOMENT_LIKED.value, "moment", moment_id,
            entity_type="moment_like", entity_id=like.id,
            actor_type="user", actor_id=user_id,
            payload={"action": "liked"},
            idempotency_key=f"moment.liked:moment_like:{like.id}",
            origin="user_message",
        )
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
    # 3.10 事件流水（P1）：用户评论（与 AI 四条评论点共用收敛口径，round=user）
    await append_domain_event(
        _ET.MOMENT_COMMENT_ADDED.value, "moment", moment_id,
        entity_type="moment_comment", entity_id=comment.id,
        actor_type="user", actor_id=user_id,
        payload={"parent_id": comment.parent_id, "round": "user",
                 "content": (comment.content or "")[:200]},
        idempotency_key=f"moment.comment_added:moment_comment:{comment.id}",
        origin="user_message",
    )
    # AI 回复用户评论（异步不阻塞）：动态作者 / 其他 AI 角色按幂等规则回复；重复触发安全
    try:
        from app.application.moment_service import generate_comments_for_moment
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
    from app.application.upload_service import delete_image_file
    _deleted_content = (moment.content or "")[:200]
    _deleted_sender = moment.sender_type
    _deleted_likes = moment.likes_count or 0
    delete_image_file(moment.image_url)
    await db.execute(delete(MomentLike).where(MomentLike.moment_id == moment_id))
    await db.execute(delete(MomentAILike).where(MomentAILike.moment_id == moment_id))
    await db.execute(delete(MomentComment).where(MomentComment.moment_id == moment_id))
    moment.is_active = False
    await db.commit()
    # 3.10 事件流水（P1）：动态软删清算（payload 保留被删实体关键字面量，主表软删后流水仍可追溯）
    await append_domain_event(
        _ET.MOMENT_DELETED.value, "moment", moment_id,
        entity_type="ai_moment", entity_id=moment_id,
        actor_type="user", actor_id=user_id,
        payload={"sender_type": _deleted_sender, "content": _deleted_content,
                 "likes_count": _deleted_likes},
        idempotency_key=f"moment.deleted:ai_moment:{moment_id}",
        origin="user_message",
    )
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
    # F-7（v3.4.6 审查）：硬删父评论须级联删除其整棵子回复树（该动态下 parent 链到它的
    # 所有子孙），否则子回复 parent 悬空成孤儿；删前先取各被删行内容供事件 payload 保留。
    all_result = await db.execute(
        select(MomentComment).where(MomentComment.moment_id == moment_id)
    )
    _children: dict[int | None, list] = {}
    for _c in all_result.scalars().all():
        _children.setdefault(_c.parent_id, []).append(_c)
    _to_delete = [comment]
    _stack = [comment]
    while _stack:
        _cur = _stack.pop()
        for _ch in _children.get(_cur.id, []):
            _to_delete.append(_ch)
            _stack.append(_ch)
    _deleted_meta = [(c.id, c.parent_id, (c.content or "")[:200]) for c in _to_delete]
    for _c in _to_delete:
        await db.delete(_c)
    await db.commit()
    # 3.10 事件流水（P1）：评论硬删（含级联删除的子回复，每条各落一事件；
    # payload 保留被删内容前 200 字）
    for _cid, _cparent, _ccontent in _deleted_meta:
        await append_domain_event(
            _ET.MOMENT_COMMENT_DELETED.value, "moment", moment_id,
            entity_type="moment_comment", entity_id=_cid,
            actor_type="user", actor_id=user_id,
            payload={"parent_id": _cparent, "content": _ccontent,
                     "cascade": _cid != comment_id},
            idempotency_key=f"moment.comment_deleted:moment_comment:{_cid}",
            origin="user_message",
        )
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
    # F-4（v3.4.6 审查）：子表 FK 无 ondelete，硬删动态前显式级联清评论/点赞/AI 赞
    # （不依赖 SQLite FK），否则孤儿行累积。
    _ids = [m.id for m in moments]
    _cnt_comments = _cnt_likes = _cnt_ai_likes = 0
    if _ids:
        _cnt_comments = (await db.execute(
            delete(MomentComment).where(MomentComment.moment_id.in_(_ids)))).rowcount or 0
        _cnt_likes = (await db.execute(
            delete(MomentLike).where(MomentLike.moment_id.in_(_ids)))).rowcount or 0
        _cnt_ai_likes = (await db.execute(
            delete(MomentAILike).where(MomentAILike.moment_id.in_(_ids)))).rowcount or 0
    from app.application.upload_service import delete_image_file
    for m in moments:
        delete_image_file(m.image_url)
        await db.delete(m)
    await db.commit()
    # 3.10 事件流水（P1）：清空角色当日动态（幂等键带日期，同日重复清理不产生第二条；
    # payload 保留被删计数，F-4）
    await append_domain_event(
        _ET.MOMENT_CLEARED.value, "moment", character_id,
        actor_type="user", actor_id=user_id,
        payload={"scope": "character_daily", "character_id": character_id,
                 "moment_count": len(moments), "date": str(start)[:10],
                 "comment_count": _cnt_comments, "like_count": _cnt_likes,
                 "ai_like_count": _cnt_ai_likes},
        idempotency_key=f"moment.cleared:{character_id}:{str(start)[:10]}",
        origin="user_message",
    )
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
    # 3.10 事件流水（P1）：朋友圈已读（按自然日聚合，同日只保留一条口径；幂等键带日期）
    await append_domain_event(
        _ET.MOMENT_READ.value, "moment", user_id,
        actor_type="user", actor_id=user_id,
        payload={"last_read_at": str(now)},
        idempotency_key=f"moment.read:{user_id}:{str(now)[:10]}",
        origin="user_message",
    )
    return {"status": "ok"}


async def _batch_likers(db: AsyncSession, moments: list[AIMoment]) -> dict[int, tuple[int, list[str]]]:
    """``_likers_for_moment`` 的批量版：3~4 次查询算完全部动态的「谁赞了」。

    返回 ``{moment_id: (总赞数, 名字列表)}``，与逐条 ``_likers_for_moment`` 逐字段等价：
    总赞数 = ``moment.likes_count`` + AI 赞条数；名字列表 = 用户赞（昵称/用户名/「我」，
    created_at 升序）在前、AI 赞（角色名，created_at 升序）在后。
    """
    out: dict[int, tuple[int, list[str]]] = {}
    if not moments:
        return out
    from app.models.user import User

    mids = [m.id for m in moments]
    totals = {m.id: (m.likes_count or 0) for m in moments}
    names: dict[int, list[str]] = {m.id: [] for m in moments}

    ul_rows = (await db.execute(
        select(MomentLike)
        .where(MomentLike.moment_id.in_(mids))
        .order_by(MomentLike.moment_id.asc(), MomentLike.created_at.asc())
    )).scalars().all()
    uids = {r.user_id for r in ul_rows}
    users = {}
    if uids:
        users = {u.id: u for u in (await db.execute(
            select(User).where(User.id.in_(uids))
        )).scalars().all()}
    for r in ul_rows:
        u = users.get(r.user_id)
        if u:
            names[r.moment_id].append(u.nickname or u.username or "我")

    ai_rows = (await db.execute(
        select(MomentAILike)
        .where(MomentAILike.moment_id.in_(mids))
        .order_by(MomentAILike.moment_id.asc(), MomentAILike.created_at.asc())
    )).scalars().all()
    cids = {r.character_id for r in ai_rows}
    chars = {}
    if cids:
        chars = {c.id: c for c in (await db.execute(
            select(AICharacter).where(AICharacter.id.in_(cids))
        )).scalars().all()}
    for r in ai_rows:
        totals[r.moment_id] = totals.get(r.moment_id, 0) + 1
        ac = chars.get(r.character_id)
        if ac:
            names[r.moment_id].append(ac.name)

    for mid in totals:
        out[mid] = (totals[mid], names[mid])
    return out


@router.get("/archive")
async def list_moments_archive(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """朋友圈归档—按日期分组。

    §4.5（2026-09-09）：原实现在循环内逐条查 AICharacter/User/MomentLike/_likers_for_moment
    （最坏 200×4~5 ≈ 上千次查询）。改为进入循环前一次性批量预取，循环内零查询；
    返回结构与原实现逐字段等价。
    """
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
    if not moments:
        return {"days": [], "total_days": 0}

    mids = [m.id for m in moments]

    # ── 批量预取（循环内零查询）──
    char_ids = {m.character_id for m in moments if m.sender_type == "ai" and m.character_id}
    user_ids = {m.user_id for m in moments if m.sender_type == "user" and m.user_id}
    chars: dict[int, AICharacter] = {}
    if char_ids:
        chars = {c.id: c for c in (await db.execute(
            select(AICharacter).where(
                AICharacter.id.in_(char_ids), AICharacter.is_active == True)
        )).scalars().all()}
    users = {}
    if user_ids:
        from app.models.user import User
        users = {u.id: u for u in (await db.execute(
            select(User).where(User.id.in_(user_ids))
        )).scalars().all()}
    my_likes = set((await db.execute(
        select(MomentLike.moment_id).where(
            MomentLike.moment_id.in_(mids), MomentLike.user_id == user_id)
    )).scalars().all())
    likers_map = await _batch_likers(db, moments)

    for m in moments:
        author_tz = 8  # 作者所在时区：AI 取角色 timezone_offset，用户默认北京；日期分组按作者地区
        char_name = ""
        avatar_url = ""
        if m.sender_type == "ai" and m.character_id:
            char = chars.get(m.character_id)
            if not char:
                continue
            char_name = char.name
            avatar_url = char.avatar_url or ""
            author_tz = char.timezone_offset if char.timezone_offset is not None else 8
        elif m.sender_type == "user" and m.user_id:
            u = users.get(m.user_id)
            char_name = u.nickname if u and u.nickname else (u.username if u else "我")
            avatar_url = (u.avatar_url if u else "") or ""

        day_key = shift_utc_naive(m.created_at, author_tz).strftime("%Y-%m-%d")

        liked_by_me = m.id in my_likes
        total_likes, likers = likers_map.get(m.id, (0, []))

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