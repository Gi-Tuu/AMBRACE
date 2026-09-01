"""用机时长插件：把手机上报的最近 24h 应用使用时长注入 AI 上下文
（仅注入 2.5 小时内的新快照，避免旧数据；数据来自手机感知「应用使用时长」开关）"""
from app.plugins import sdk


@sdk.hook("context_inject")
async def inject_usage(ctx):
    try:
        from datetime import datetime, timedelta
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.device import PhoneSnapshot

        uid = ctx.get("user_id") or 1
        cfg = sdk.get_config()
        max_age_min = int(cfg.get("max_age_minutes", 150))
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(PhoneSnapshot)
                    .where(PhoneSnapshot.user_id == uid, PhoneSnapshot.source == "usage_stats")
                    .order_by(PhoneSnapshot.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None or not row.content:
            return
        created = row.created_at
        if created is not None and created < datetime.utcnow() - timedelta(minutes=max_age_min):
            return
        prefix = cfg.get("prefix") or "【插件-用机时长】"
        ctx["context_messages"].append({"role": "system", "content": prefix + row.content})
        sdk.log("已注入用机时长: %s", row.content[:60])
    except Exception as e:
        sdk.log("用机时长注入失败: %s", e)
