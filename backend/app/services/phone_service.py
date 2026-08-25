"""手机感知服务：快照读取与上下文注入文本组装（供 agent/context_builder 使用）。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.phone_snapshot import PhoneSnapshot
from app.utils.logger import get_logger

_logger = get_logger("services.phone")


async def get_recent_perception_text(user_id: int, max_age_minutes: int = 30, max_chars: int = 500) -> str:
    """供 context_builder 调用：取最近 max_age_minutes 分钟内各来源（屏幕/剪贴板/相册/通知）最新快照，
    按来源合并注入（一次采集会上传多条，只取 1 条会漏掉用户问的内容）。
    超时按 UTC 时间比较（数据库存 UTC naive）。"""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(PhoneSnapshot)
                .where(PhoneSnapshot.user_id == user_id)
                .order_by(PhoneSnapshot.created_at.desc())
                .limit(8)
            )
            snaps = result.scalars().all()
    except Exception as e:
        _logger.warning("Load phone snapshot failed: %s", e)
        return ""
    if not snaps:
        return ""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    source_label = {"accessibility": "屏幕", "clipboard": "剪贴板", "media": "相册", "media_video": "视频", "media_audio": "音频", "media_document": "文件", "notification": "通知", "action_result": "操作结果"}
    seen = set()
    lines = []
    for snap in snaps:
        if snap.created_at is None:
            continue
        created = snap.created_at
        if created.tzinfo is not None:
            created = created.replace(tzinfo=None)
        if now - created > timedelta(minutes=max_age_minutes):
            continue
        if snap.source in seen:
            continue
        seen.add(snap.source)
        parts = []
        if snap.content:
            parts.append(snap.content)
        if snap.image_desc:
            parts.append(f"[图片] {snap.image_desc}")
        text = "；".join(parts)
        if not text:
            continue
        minutes_ago = max(0, int((now - created).total_seconds() // 60))
        lines.append(f"[{source_label.get(snap.source, snap.source)} {minutes_ago}分钟前] {text}")
    if not lines:
        return ""
    return "\n".join(lines)[:max_chars]


async def request_check_in(user_id: int, character_id: int) -> bool:
    """查岗登记（2026-08-15）：角色想感知用户手机时登记 pending，前端轮询采集后完成。
    防刷：用户近 5 分钟已有 pending/已完成请求则不重复登记。"""
    try:
        from app.models.phone_snapshot import CheckInRequest
        async with async_session_factory() as db:
            since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
            recent = (await db.execute(
                select(CheckInRequest).where(
                    CheckInRequest.user_id == user_id,
                    CheckInRequest.created_at >= since,
                ).order_by(CheckInRequest.id.desc()).limit(1)
            )).scalar_one_or_none()
            if recent is not None and recent.status != "expired":
                return False
            db.add(CheckInRequest(user_id=user_id, character_id=character_id, status="pending"))
            await db.commit()
            _logger.info("Check-in requested user=%d char=%d", user_id, character_id)
            return True
    except Exception as e:
        _logger.warning("Check-in request failed: %s", e)
        return False


async def get_check_in_foreground_app(user_id: int, max_age_minutes: int = 10) -> str:
    """查岗（2026-08-15）：返回用户当前正在用的软件名。

    从最近的 shizuku_system 快照解析「前台应用：xxx」；无新鲜快照或解析不到返回空串
    （调用方据此不注入，避免 AI 编造）。"""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(PhoneSnapshot)
                .where(PhoneSnapshot.user_id == user_id, PhoneSnapshot.source == "shizuku_system")
                .order_by(PhoneSnapshot.created_at.desc())
                .limit(3)
            )
            snaps = result.scalars().all()
    except Exception as e:
        _logger.warning("Check-in snapshot load failed: %s", e)
        return ""
    if not snaps:
        return ""
    import re
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for snap in snaps:
        created = snap.created_at
        if created is not None:
            if created.tzinfo is not None:
                created = created.replace(tzinfo=None)
            if now - created > timedelta(minutes=max_age_minutes):
                continue
        content = snap.content or ""
        m = re.search(r"前台应用[:：]\s*([^；;\n]+)", content)
        if m:
            app = m.group(1).strip()
            if app and app not in ("无", "unknown", "未识别", "未知"):
                return app
    return "" 
