# -*- coding: utf-8 -*-
"""抖音 ChannelPort 适配器（X5 渠道外迁，2026-09-01）。

实现内核 app.providers.channel.ChannelPort 契约：发布/拉评论/回评/媒体上传/账号绑定状态。
实现方式=复用 main.py 既有能力函数（草稿进 douyin_pending 人工确认队列=渠道自身审批语义；
评论读 douyin_comments 渠道侧状态；上传经 upload_service + 待确认任务行）。
依赖注入：main.py 加载完成后以 handlers 装配（避免 channel_port ↔ main 循环导入）；
由 main.py 末尾调 sdk.register_channel("douyin", port, meta=...) 完成注册。
"""
import io
import json
import os

from starlette.datastructures import UploadFile


def _parse_ids(raw) -> list[int]:
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip().isdigit()]
    return [int(x) for x in str(raw or "").split(",") if x.strip().isdigit()]


class DouyinChannelPort:
    """抖音渠道端口：内核经 ChannelPort 契约调用，渠道语义（审批队列/额度/违禁词）由本实现自负。"""

    def __init__(self, *, status_handler, draft_image, draft_video, draft_reply,
                 upload_image_handler, upload_video_handler):
        self._status = status_handler
        self._draft_image = draft_image
        self._draft_video = draft_video
        self._draft_reply = draft_reply
        self._upload_image = upload_image_handler
        self._upload_video = upload_video_handler

    async def binding_status(self, payload: dict) -> dict:
        """账号绑定状态（复用 /status 处理器：登录态检查 + douyin_accounts upsert）"""
        return await self._status()

    async def publish(self, payload: dict) -> dict:
        """发布（进人工确认队列）：kind=image_post（默认）/video_post"""
        kind = str(payload.get("kind") or "image_post").strip()
        if kind == "video_post":
            return await self._draft_video(payload)
        return await self._draft_image(payload)

    async def reply_comment(self, payload: dict) -> dict:
        """回评（进人工确认队列）：post_title/commenter/reply_text"""
        return await self._draft_reply(payload)

    async def pull_comments(self, payload: dict) -> list[dict]:
        """拉取评论（渠道侧状态：douyin_comments 最近记录；limit 默认 20）"""
        import douyin_models
        from sqlalchemy import select
        from app.db.database import async_session_factory
        limit = max(1, min(100, int(payload.get("limit") or 20)))
        replied = payload.get("replied")
        async with async_session_factory() as db:
            q = select(douyin_models.DouyinComment).order_by(douyin_models.DouyinComment.id.desc()).limit(limit)
            if replied is not None:
                q = q.where(douyin_models.DouyinComment.replied == bool(replied))
            rows = (await db.execute(q)).scalars().all()
        return [{
            "id": r.id, "post_id": r.douyin_post_id, "commenter": r.commenter,
            "content": r.content, "is_fan": bool(r.is_fan), "replied": bool(r.replied),
            "comment_id": r.comment_id, "aweme_id": r.aweme_id,
            "commented_at": r.commented_at.isoformat() if r.commented_at else "",
        } for r in rows]

    async def upload_media(self, payload: dict) -> dict:
        """媒体上传：{task_id, file_path, media_type=image|video} → 保存并挂到待确认任务行"""
        task_id = int(payload.get("task_id") or 0)
        file_path = str(payload.get("file_path") or "").strip()
        media_type = str(payload.get("media_type") or "image").strip()
        if not task_id or not file_path or not os.path.isfile(file_path):
            return {"ok": False, "message": "task_id/file_path 无效"}
        fname = os.path.basename(file_path)
        with open(file_path, "rb") as fh:
            data = fh.read()
        uf = UploadFile(file=io.BytesIO(data), filename=fname)
        if media_type == "video":
            return await self._upload_video(task_id=task_id, file=uf)
        return await self._upload_image(task_id=task_id, file=uf)

    # ── 渠道元数据便捷读取 ──

    async def allowed_character_ids(self) -> list[int]:
        """当前绑定的角色 id（插件配置 allowed_character_ids，逗号分隔）"""
        from app.plugins import sdk
        cfg = sdk.get_config()
        return _parse_ids(cfg.get("allowed_character_ids"))

    @staticmethod
    async def pending_summary() -> dict:
        """待确认/已确认任务计数（运维观察用，零副作用）"""
        import douyin_models
        from sqlalchemy import select, func as sa_func
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            pending = (await db.execute(select(sa_func.count()).select_from(douyin_models.DouyinPending)
                                        .where(douyin_models.DouyinPending.status == "pending"))).scalar() or 0
            confirmed = (await db.execute(select(sa_func.count()).select_from(douyin_models.DouyinPending)
                                          .where(douyin_models.DouyinPending.status.in_(("confirmed", "running"))))).scalar() or 0
        return {"pending": int(pending), "confirmed": int(confirmed)}


def build_meta() -> dict:
    """渠道注册元数据（内核通用化消费：scope/风险/权限/绑定插件关联）"""
    return {
        "label": "抖音",
        "plugin": "douyin_mcp",
        "permissions": ["douyin_publish"],
        "scope": "douyin",
        "scope_label": "抖音",
        "scope_desc": "抖音扩展：发布图文、回复评论",
        "risk_level": "high",
        "binding": {"unique_per_family": True},
    }
