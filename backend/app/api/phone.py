"""手机感知接口：接收手机端采集的快照（屏幕文字/剪贴板/相册元数据，可选图片），
写入 phone_snapshots，供聊天上下文注入（AI 走出沙箱 Phase 1）。
硬约束：图片文件/二进制绝不传入 deepseek；图片经本地 OCR/VLM 转文字后仅存文本。
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Header
from sqlalchemy import delete, select

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.schemas.phone import AutoReportRequest
from app.db.database import async_session_factory
from app.models.device import CheckInRequest, PhoneSnapshot
from app.application.upload_service import UPLOAD_DIR, save_image
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

router = APIRouter(prefix="/api/v1/phone", tags=["Phone Perception"])
_logger = get_logger("api.phone")

MAX_KEEP = 20          # 每用户最多保留的快照条数
MAX_CONTENT = 2000     # 文本快照上限
MAX_PAYLOAD_JSON = 4000  # X7-M1 结构化载荷上限（超长只丢该字段，快照本身照常写库）
# 同内容去重窗口（2026-09-21 P2）：补传/重复采集不再写库，也不把有效数据挤出 MAX_KEEP
DEDUP_WINDOW = timedelta(minutes=5)
# 查岗请求有效期（2026-09-22 P8，S8）：原 120s 太短——后台服务没跑时前端轮询不到，
# 请求就永久错过；放宽到 600s 覆盖「后台服务稍后才被拉起」的场景。过期判定只此一处。
CHECK_IN_TTL_SECONDS = 600


def _snapshot_to_dict(s: PhoneSnapshot) -> dict:
    created = s.created_at
    return {
        "id": s.id,
        "source": s.source,
        "content": s.content or "",
        "image_desc": s.image_desc or "",
        # X7-M1 回显（P9）：原样返回入库的结构化载荷字符串，无载荷为 None
        "payload_json": s.payload_json,
        "created_at": created.isoformat() if created else "",
    }


def _clean_payload_json(raw: str | None) -> str | None:
    """X7-M1 结构化载荷入参校验：只接受「合法 JSON **对象**」且长度 ≤ MAX_PAYLOAD_JSON。

    非法（坏 JSON / 数组 / 裸量 / 空）或超长一律返回 ``None`` 丢弃该字段——客户端脏数据不得让
    一次采集整体失败，快照正文照常写库。返回的是去首尾空白后的原串（读侧再解析一次）。
    """
    text = (raw or "").strip()
    if not text or len(text) > MAX_PAYLOAD_JSON:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return text if isinstance(obj, dict) else None


@router.post("/perception")
async def create_perception(
    source: str = Form(""),
    content: str = Form(""),
    client_key: str = Form(""),
    payload_json: str = Form(""),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """写入一条手机感知快照。source: accessibility/clipboard/media；content 为文本；image 可选（本地 VLM/OCR 转文字）。
    client_key 为客户端确定性指纹（P2 补传用，仅进日志，不落库）。
    payload_json 可选（X7-M1 结构化承载）：字段级 JSON 对象，见 :func:`_clean_payload_json`。"""
    source = (source or "accessibility").strip()[:20]
    if source not in {"accessibility", "clipboard", "media", "media_video", "media_audio", "media_document", "notification", "action_result", "usage_stats", "shizuku_system"}:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "source_unsupported"))

    image_desc = ""
    if image is not None:
        try:
            image_url = await save_image(image, f"phone/{user_id}", lang)
            from app.application.image_understanding_service import describe_image
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
    payload = _clean_payload_json(payload_json)

    async with async_session_factory() as db:
        # P2（盘点 S3 幂等）：写库前查一次「同用户 + 同 source + 同 content + 最近 5 分钟」，
        # 命中即不写库（补传/重复采集不再把有效数据挤出 MAX_KEEP）。只按现有字段查，
        # 不加列不加迁移；client_key 仅进日志。带图快照不参与去重（content 可能同为空但图不同）。
        # P9 第 3 条：载荷（payload_json）也纳入去重条件——同正文但**不同**结构化载荷属于两次
        # 不同的采集（例如同一句通知文字、字段级内容变了），不得被吞；都为 NULL 时仍按原口径去重。
        if text and not image_desc:
            # 用项目统一入口（test_time_discipline 棘轮：禁止新增裸 aware 写法，2026-09-22 CI 抓到）
            since = now_naive_utc() - DEDUP_WINDOW
            dup_cond = (
                PhoneSnapshot.payload_json.is_(None)
                if payload is None
                else PhoneSnapshot.payload_json == payload
            )
            dup_id = (
                await db.execute(
                    select(PhoneSnapshot.id)
                    .where(
                        PhoneSnapshot.user_id == user_id,
                        PhoneSnapshot.source == source,
                        PhoneSnapshot.content == text,
                        PhoneSnapshot.created_at >= since,
                        dup_cond,
                    )
                    .limit(1)
                )
            ).scalars().first()
            if dup_id is not None:
                _logger.info(
                    "Perception deduped: user=%s source=%s existing=%s client_key=%s",
                    user_id, source, dup_id, (client_key or "")[:64],
                )
                return {"status": "ok", "deduped": True}

        snap = PhoneSnapshot(
            user_id=user_id,
            source=source,
            content=text,
            image_desc=image_desc,
            payload_json=payload,
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
    """查岗请求轮询：返回当前用户是否有待采集的查岗请求（超时 CHECK_IN_TTL_SECONDS 自动作废）"""
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
        if datetime.now(timezone.utc).replace(tzinfo=None) - created > timedelta(seconds=CHECK_IN_TTL_SECONDS):
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
    from app.application.phone_auto_notify_service import handle_auto_report
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
