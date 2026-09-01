"""手机感知接口：接收手机端采集的快照（屏幕文字/剪贴板/相册元数据，可选图片），
写入 phone_snapshots，供聊天上下文注入（AI 走出沙箱 Phase 1）。
硬约束：图片文件/二进制绝不传入 deepseek；图片经本地 OCR/VLM 转文字后仅存文本。
"""

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Header
from sqlalchemy import delete, select

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.schemas.phone import AutoReportRequest
from app.db.database import async_session_factory
from app.models.device import CheckInRequest, PhoneSnapshot
from app.services.upload_service import UPLOAD_DIR, save_image
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/phone", tags=["Phone Perception"])
_logger = get_logger("api.phone")

MAX_KEEP = 20          # 每用户最多保留的快照条数
MAX_CONTENT = 2000     # 文本快照上限


def _snapshot_to_dict(s: PhoneSnapshot) -> dict:
    created = s.created_at
    return {
        "id": s.id,
        "source": s.source,
        "content": s.content or "",
        "image_desc": s.image_desc or "",
        "created_at": created.isoformat() if created else "",
    }


@router.post("/perception")
async def create_perception(
    source: str = Form(""),
    content: str = Form(""),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """写入一条手机感知快照。source: accessibility/clipboard/media；content 为文本；image 可选（本地 VLM/OCR 转文字）。"""
    source = (source or "accessibility").strip()[:20]
    if source not in {"accessibility", "clipboard", "media", "media_video", "media_audio", "media_document", "notification", "action_result", "usage_stats", "shizuku_system"}:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "source_unsupported"))

    image_desc = ""
    if image is not None:
        try:
            image_url = await save_image(image, f"phone/{user_id}", lang)
            from app.services.image_understanding_service import describe_image
            abs_path = str(UPLOAD_DIR / image_url.removeprefix("/uploads/"))
            desc = await describe_image(abs_path, user_id=user_id)
            image_desc = (desc or "").strip()[:1000]
        except HTTPException:
            raise
        except Exception as e:
            _logger.warning("Phone image describe failed: %s", e)

    text = (content or "").strip()[:MAX_CONTENT]
    if not text and not image_desc:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty_phone"))

    async with async_session_factory() as db:
        snap = PhoneSnapshot(
            user_id=user_id,
            source=source,
            content=text,
            image_desc=image_desc,
        )
        db.add(snap)
        # 每用户只保留最近 MAX_KEEP 条
        old_ids = (
            await db.execute(
                select(PhoneSnapshot.id)
                .where(PhoneSnapshot.user_id == user_id)
                .order_by(PhoneSnapshot.created_at.desc())
                .offset(MAX_KEEP)
            )
        ).scalars().all()
        if old_ids:
            await db.execute(delete(PhoneSnapshot).where(PhoneSnapshot.id.in_(old_ids)))
        await db.commit()
        await db.refresh(snap)
    return {"status": "ok", "snapshot": _snapshot_to_dict(snap)}



@router.get("/perception/check-in-request")
async def get_check_in_request(
    user_id: int = Depends(get_current_user_id),
):
    """查岗请求轮询：返回当前用户是否有待采集的查岗请求（超时 120s 自动作废）"""
    async with async_session_factory() as db:
        req = (await db.execute(
            select(CheckInRequest)
            .where(CheckInRequest.user_id == user_id, CheckInRequest.status == "pending")
            .order_by(CheckInRequest.id.desc())
            .limit(1)
        )).scalar_one_or_none()
        if req is None:
            return {"has": False}
        created = req.created_at.replace(tzinfo=None) if req.created_at.tzinfo else req.created_at
        if datetime.now(timezone.utc).replace(tzinfo=None) - created > timedelta(seconds=120):
            req.status = "expired"
            await db.commit()
            return {"has": False}
        return {"has": True, "id": req.id, "character_id": req.character_id}


@router.post("/perception/check-in-request/{req_id}/done")
async def done_check_in_request(
    req_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """前端完成查岗采集后标记 done"""
    async with async_session_factory() as db:
        req = await db.get(CheckInRequest, req_id)
        if req is not None and req.user_id == user_id and req.status == "pending":
            req.status = "done"
            await db.commit()
    return {"status": "ok"}


@router.post("/perception/auto")
async def auto_report_notifications(
    data: AutoReportRequest,
    user_id: int = Depends(get_current_user_id),
):
    """AI 主动提通知：手机后台服务定时上报通知缓存，服务器对比新增并节流触发 AI 主动消息"""
    from app.services.phone_auto_notify_service import handle_auto_report
    notifications = [
        {"app": n.app, "package": n.package, "title": n.title, "text": n.text, "time": n.time}
        for n in data.notifications
    ]
    return await handle_auto_report(user_id, notifications)


@router.get("/perception/recent")
async def list_recent(user_id: int = Depends(get_current_user_id)):
    """返回该用户最近快照（倒序，最多 MAX_KEEP 条）"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(PhoneSnapshot)
            .where(PhoneSnapshot.user_id == user_id)
            .order_by(PhoneSnapshot.created_at.desc())
            .limit(MAX_KEEP)
        )
        snaps = result.scalars().all()
    return {"snapshots": [_snapshot_to_dict(s) for s in snaps]}


@router.delete("/perception")
async def clear_all(user_id: int = Depends(get_current_user_id)):
    """清除该用户全部快照（隐私：一键清除）"""
    async with async_session_factory() as db:
        await db.execute(delete(PhoneSnapshot).where(PhoneSnapshot.user_id == user_id))
        await db.commit()
    return {"status": "ok"}
