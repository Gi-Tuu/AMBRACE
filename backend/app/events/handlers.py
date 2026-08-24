"""内置事件订阅者（演进规划 v2 Phase A）

- memory.written → 织库增量补卡（原 memory/service.py 硬编码逻辑迁移）
- life.activity_completed → 朋友圈联动（原 life/activity.py 硬编码逻辑迁移）
"""
import logging

from sqlalchemy import select as _sel

from app.db.database import async_session_factory

_logger = logging.getLogger("events.handlers")


async def _on_memory_written(payload: dict) -> None:
    """记忆写入：importance≥60（3 星+）异步整理织库卡片；source=life 进私·织库，其余进全·织库"""
    try:
        _d = payload.get("data") or payload
        importance = float(_d.get("importance") or 0)
        if importance < 60.0:
            return
        user_id = _d.get("user_id")
        character_id = _d.get("character_id")
        if not user_id or not character_id:
            return
        from app.weave.incremental import schedule_incremental_weave
        source = _d.get("source") or ""
        domain = "private" if source == "life" else "shared"
        schedule_incremental_weave(user_id, character_id, domain)
    except Exception as e:
        _logger.warning("events on_memory_written failed: %s", e)


async def _on_tool_executed(payload: dict) -> None:
    """工具执行完成（Phase G）：flag agent_tool_events 开启时联动织库增量（每角色防抖由 weave 内部控制）；

    默认关 = 完全无联动（tool.executed 事件仍可被其他订阅者追踪）。
    """
    try:
        from app.agent import loop as _loop
        if not _loop.AGENT_FLAGS.get("agent_tool_events", False):
            return
        if payload.get("status") != "ok":
            return
        user_id = payload.get("user_id")
        character_id = payload.get("character_id")
        if not user_id or not character_id:
            return
        from app.weave.incremental import schedule_incremental_weave
        schedule_incremental_weave(user_id, character_id, "shared")
        _logger.info("tool.executed -> weave incremental: tool=%s char=%d", payload.get("tool"), character_id)
    except Exception as e:
        _logger.warning("events on_tool_executed failed: %s", e)


async def _on_activity_completed(payload: dict) -> None:
    """Life 活动完成：create 且带产物 → 朋友圈联动（受朋友圈开关/每日 3 条/2h 间隔约束）"""
    try:
        _d = payload.get("data") or payload
        if _d.get("activity_type") != "create" or not _d.get("artifact_id"):
            return
        character_id = _d.get("character_id")
        user_id = _d.get("user_id")
        summary = _d.get("summary") or ""
        if not character_id or not user_id:
            return
        from app.models.proactive_settings import ProactiveSettings
        from app.services.moment_service import publish_moment
        async with async_session_factory() as _db:
            _ps = (
                await _db.execute(_sel(ProactiveSettings).where(ProactiveSettings.character_id == character_id))
            ).scalar_one_or_none()
        if _ps is None or not _ps.moments_enabled:
            return
        # 世界状态折叠（P4）：活动完成 → 角色最近活动事实
        from app.events.facts import fold_activity
        await fold_activity(character_id, user_id, str(payload.get("activity_type") or ""), str(summary)[:120])
        await publish_moment(character_id, skip_interval=False, extra_hint=str(summary)[:200])
    except Exception as e:
        _logger.warning("events on_activity_completed failed: %s", e)


async def _on_interest_updated(payload: dict) -> None:
    """兴趣变更 trace（S-3/P0-8，2026-08-16）：写 agent_task_logs trigger=interest，只写不读"""
    try:
        _d = payload.get("data") or payload
        cid = _d.get("character_id")
        if not cid:
            return
        from app.agent.trace import enqueue_task_log
        import json as _json
        enqueue_task_log(
            character_id=int(cid),
            user_id=_d.get("user_id"),
            trigger="interest",
            route="interest.updated",
            steps_json=_json.dumps({
                "interest": _d.get("interest"),
                "old_level": _d.get("old_level"),
                "new_level": _d.get("new_level"),
                "created": _d.get("created"),
            }, ensure_ascii=False),
            status="ok",
        )
    except Exception:
        pass


async def _on_moment_published(payload: dict) -> None:
    """朋友圈发布 trace（P0-8，2026-08-16）：写 agent_task_logs trigger=moment，只写不读"""
    try:
        _d = payload.get("data") or payload
        cid = _d.get("character_id")
        if not cid:
            return
        from app.agent.trace import enqueue_task_log
        import json as _json
        enqueue_task_log(
            character_id=int(cid),
            user_id=_d.get("user_id"),
            trigger="moment",
            route="moment_published",
            steps_json=_json.dumps({"content": str(_d.get("content") or "")[:80]}, ensure_ascii=False),
            status="ok",
        )
    except Exception:
        pass


def register_builtin_handlers() -> None:
    """注册内置订阅者（幂等：重复注册会去重）"""
    from app.events.bus import event_bus
    for et, h in (
        ("memory.written", _on_memory_written),
        ("life.activity_completed", _on_activity_completed),
        ("tool.executed", _on_tool_executed),
        ("interest.updated", _on_interest_updated),
        ("life.moment_published", _on_moment_published),
    ):
        if h not in event_bus._subscribers.get(et, []):
            event_bus.subscribe(et, h)
