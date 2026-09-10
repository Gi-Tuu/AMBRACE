"""L4（2026-09-09 主动复习「回忆化」）：过期计划记忆自动退场（每日维护挂载，flag 灰度）。

- 把"已过期、仍 active"的未来计划记忆置 stale（#70-C 语义）：退出主动复习/无条件注入，
  检索保留（stale 在 _retrievable_status_clause 可见、rerank 降权）、可怀旧；
- next_review_at 置 None（停止主动复习轮转）、valid_to 置实际有效期（tense 规则解析）；
- 双通道：SQLite 提交后再同步 Chroma metadata（对齐 supersede._mark_vectors）+ bm25 失效；
- flag review_plan_expire_stale 默认 False（灰度）；任何异常静默返回 0，不阻塞日终维护。
- 不物理删除任何记忆。
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.memory.tense import classify_tense, is_plan_expired, plan_valid_until, PLAN_MARKERS
from app.utils.timeutil import now_naive_utc
from app.utils.logger import get_logger

_logger = get_logger("memory.plan_expiry")

EXPIRE_BATCH_LIMIT = 200  # 单次最多置 stale 条数（日终维护节流）


async def expire_stale_plans(limit: int = EXPIRE_BATCH_LIMIT) -> int:
    """把已过期、仍 active 的未来计划记忆置为 stale；返回本次处理条数。

    SQL 侧先用将来时标记词 LIKE 预筛（content 命中任一标记），Python 侧再以
    classify_tense + is_plan_expired 精判（tense 规则是唯一权威，LIKE 只做扫描窗收窄）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("review_plan_expire_stale", False):
            return 0
    except Exception:
        return 0
    now = now_naive_utc()
    moved_ids: list[int] = []
    char_ids: set[int] = set()
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.status == "active",
                    Memory.is_archived == False,  # noqa: E712
                    Memory.memory_type == "event",
                    or_(*[Memory.content.like(f"%{k}%") for k in PLAN_MARKERS]),
                ).order_by(Memory.created_at.asc()).limit(max(1, limit) * 4)
            )).scalars().all()
            for m in rows:
                if len(moved_ids) >= limit:
                    break
                try:
                    if classify_tense(m) == "plan" and is_plan_expired(m, now):
                        m.status = "stale"
                        m.valid_to = plan_valid_until(m, now) or now
                        m.next_review_at = None  # 停止主动复习轮转
                        moved_ids.append(m.id)
                        char_ids.add(m.character_id)
                except Exception as e:
                    _logger.warning("plan-expire classify failed mem=%s: %s", m.id, e)
            await db.commit()
    except Exception as e:
        _logger.warning("expire_stale_plans failed: %s", e)
        return 0
    # 双通道：向量状态同步（与 supersede 置 stale 一致）+ bm25 索引失效（均失败静默）
    if moved_ids:
        try:
            from app.memory.supersede import _mark_vectors, _bm25_invalidate_safe
            await _mark_vectors(moved_ids, {mid: "stale" for mid in moved_ids})
            for cid in char_ids:
                await _bm25_invalidate_safe(cid)
        except Exception as e:
            _logger.warning("plan-expire vector/bm25 sync failed: %s", e)
    _logger.info("expire_stale_plans moved=%d", len(moved_ids))
    return len(moved_ids)
