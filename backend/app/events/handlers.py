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
    """工具执行完成（Phase G）：工具执行联动织库增量（曾为灰度开关 agent_tool_events，2026-09-17 固化常开；每角色防抖由 weave 内部控制）。

    R5（2026-09-09，工具轨迹治理批次四）：flag agent_tool_exec_trace 开启时，
    插件/内置工具单次成败落 agent_task_logs（trigger=tool），让「工具轨迹」可见真实工具执行。
    与织库联动并列、互不影响；默认关=零写放大。MCP 工具不在此落轨迹（已有 mcp_call_logs，
    前端 MCP 分区读取，避免双记）。
    """
    try:
        # 曾为灰度开关，2026-09-17 固化（用户拍板：功能常驻不下放）：工具执行联动织库增量恒执行
        if payload.get("status") == "ok":
            user_id = payload.get("user_id")
            character_id = payload.get("character_id")
            if user_id and character_id:
                from app.weave.incremental import schedule_incremental_weave
                schedule_incremental_weave(user_id, character_id, "shared")
                _logger.info("tool.executed -> weave incremental: tool=%s char=%d", payload.get("tool"), character_id)
    except Exception as e:
        _logger.warning("events on_tool_executed weave failed: %s", e)

    # ── R5：工具执行轨迹（agent_tool_exec_trace，默认关；只记真实工具，不记 scheduler） ──
    try:
        from app.agent import loop as _loop2
        if not _loop2.AGENT_FLAGS.get("agent_tool_exec_trace", False):
            return
        tool = str(payload.get("tool") or "")
        # 只记 MCP / 插件 / 内置工具，排除 scheduler 等噪音前缀
        if not tool or tool.startswith("scheduler"):
            return
        # MCP 工具已有 mcp_call_logs（前端 MCP 分区），跳过避免双记
        if tool.startswith("mcp."):
            return
        from app.agent import trace as _trace
        import json as _json
        status = payload.get("status")  # ok / error / blocked
        _trace.enqueue_task_log(
            task_id=_trace.new_task_id(),
            character_id=payload.get("character_id"),
            user_id=payload.get("user_id"),
            session_id=payload.get("session_id"),
            trigger="tool",
            route=str(tool)[:30],
            steps_json=_json.dumps(
                {
                    "tool": tool,
                    "provenance": payload.get("provenance"),
                    "summary": str(payload.get("summary") or "")[:200],
                },
                ensure_ascii=False,
            ),
            tool_calls=1,
            latency_ms=int(payload.get("latency_ms") or 0),
            status=status if status in ("ok", "error", "blocked", "pending") else "ok",
            error=(str(payload.get("error"))[:500] if payload.get("error") else None),
        )
    except Exception as e:
        _logger.warning("events on_tool_executed trace failed: %s", e)


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
        from app.models.character import ProactiveSettings
        from app.application.moment_service import publish_moment
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


async def _on_life_share(payload: dict) -> None:
    """生活活动完成 → 自然分享（#63 机制4，Flag life_share_enabled；失败静默不阻塞活动主链路）。"""
    try:
        from app.scheduling.life_share import on_activity_completed
        await on_activity_completed(payload)
    except Exception as e:
        _logger.warning("events on_life_share failed: %s", e)


async def _on_thought_pool_activity(payload: dict) -> None:
    """批 4 M1-挂点（2026-10-01）：``life.activity_completed`` → F1 活动面抽念头入池。

    与 ``_on_life_share`` **同一个**订阅位（设计 §2.1「抽取挂点·事件侧」）：只读 payload、
    只写 ``thought_pool`` 行，复用既有纯函数 ``supply_thought_pool``（不重写抽取逻辑）。

    硬约束（派单）：
      - **先判 flag 再干活**：``thought_pool_shadow`` 关 ⇒ 首行返回，一次 SQL 都不发；
      - **异常隔离**：任何失败只记 WARNING，绝不影响活动分享 / 朋友圈联动主链路；
      - **不改发送链**：不碰消息生成、不碰 arbiter、不给念头池发送权；
      - 幂等键沿用既有（角色,用户,来源类型,来源主键,规范化文本哈希）——重复事件不增行。

    批 4 M2-b2（2026-10-01）补两条同源去重（设计 §3.2，判据确定性、不靠模型）：
      - **防线 1（与 life_share 同源二选一）**：读 life_share 既有 approved 留痕判定本次是否
        分享成功——成功 ⇒ ``shared_refs={ref}``，该活动只写一条 ``spent`` 留痕行（永不参与选择）；
        未成功 ⇒ 才以 ``spark`` 入池。**只读留痕，不碰 life_share 任何发送逻辑**；
      - **防线 5（与 life_regression 去重）**：F1 入池前比对近 24h 生活回灌候选集
        （``scheduling/life_regression.py`` 既有读法：``Memory.source='life'`` ∧ importance 达标
        ∧ 近 24h），该活动记忆已在回灌候选集 ⇒ ``faded_refs={ref}``，spark 直接标 ``faded``
        （不删行、永不参与选择），避免「我最近做了什么」被两个通道各讲一遍。
    """
    try:
        from app.application import thought_pool_service as _tps
        if not _tps.shadow_enabled():
            return                      # flag 关：首行返回，零 SQL（照 arbiter._pacing_gate 早退写法）
        _d = payload.get("data") or payload
        character_id = _d.get("character_id")
        if not character_id:
            return
        from app.domain.thought import extract as _ex
        # 稳定来源主键：优先 artifact_id（create 类有产物），退化 memory_id（两个发布点都带）
        ref = _d.get("artifact_id") or _d.get("memory_id") or _d.get("id")
        mem_id = _d.get("memory_id")
        row = {
            "id": ref,
            "character_id": character_id,
            "user_id": _d.get("user_id"),
            "activity_type": _d.get("activity_type"),
            "status": "completed",       # 事件即「活动完成」
            "summary": _d.get("summary") or "",
            "epistemic_status": None,
        }
        async with async_session_factory() as db:
            # 防线 1：本次是否已被 life_share 当场讲掉（读既有 approved 留痕；异常按未成功）
            shared = await _tps.life_share_succeeded(db, character_id)
            shared_refs = frozenset({str(ref)}) if (shared and ref is not None) else frozenset()
            # 防线 5：未被分享时，再比对近 24h 生活回灌候选集（已被回灌讲过 ⇒ faded）
            faded_refs: frozenset[str] = frozenset()
            if not shared and ref is not None and mem_id is not None:
                if await _in_life_regression_window(db, character_id, _d.get("user_id"), mem_id):
                    faded_refs = frozenset({str(ref)})
            await _tps.supply_thought_pool(
                db, {_ex.SRC_ACTIVITY: [row]},
                shared_refs=shared_refs, faded_refs=faded_refs,
            )
            await db.commit()           # 钩子自开 session ⇒ 自己 commit（与 T1 settle 钩子同口径）
    except Exception as e:
        _logger.warning("events on_thought_pool_activity failed: %s", e)


async def _in_life_regression_window(db, character_id, user_id, memory_id) -> bool:
    """防线 5 判据：该活动记忆是否落在 ``life_regression`` 近 24h 生活回灌候选集里。

    复用 ``scheduling/life_regression.collect_life_regression_events`` 的**既有读法**
    （``Memory.source='life'`` ∧ ``importance >= MIN_IMPORTANCE`` ∧ ``created_at >= now-24h``
    ∧ 未删），不新建查询口径、不改 life_regression 任何逻辑。命中 ⇒ 回灌通道owns这件事，
    念头侧让位（标 faded）。异常按 False（不让位、正常 spark）——宁可多一条 spark 也不误丢。
    """
    try:
        from datetime import timedelta
        from app.models.memory import Memory
        from app.scheduling.life_regression import LOOKBACK_HOURS, MIN_IMPORTANCE
        from app.utils.timeutil import now_naive_utc
        since = now_naive_utc() - timedelta(hours=LOOKBACK_HOURS)
        conds = [
            Memory.character_id == int(character_id),
            Memory.source == "life",
            Memory.importance >= MIN_IMPORTANCE,
            Memory.delete_at.is_(None),
            Memory.created_at >= since,
            Memory.id == int(memory_id),
        ]
        if user_id:
            conds.append(Memory.user_id == int(user_id))
        row = (await db.execute(_sel(Memory.id).where(*conds).limit(1))).first()
        return row is not None
    except Exception as e:
        _logger.warning("life_regression window check failed char=%s: %s", character_id, e)
        return False


def register_builtin_handlers() -> None:
    """注册内置订阅者（幂等：重复注册会去重）"""
    from app.events.bus import event_bus
    for et, h in (
        ("memory.written", _on_memory_written),
        ("life.activity_completed", _on_activity_completed),
        ("life.activity_completed", _on_life_share),
        ("life.activity_completed", _on_thought_pool_activity),  # 批 4 M1-挂点：F1 活动面入池
        ("tool.executed", _on_tool_executed),
        ("interest.updated", _on_interest_updated),
        ("life.moment_published", _on_moment_published),
    ):
        if h not in event_bus._subscribers.get(et, []):
            event_bus.subscribe(et, h)
