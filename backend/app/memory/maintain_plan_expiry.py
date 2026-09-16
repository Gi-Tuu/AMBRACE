"""L4（2026-09-09 主动复习「回忆化」）：过期计划记忆自动退场（每日维护挂载，flag 灰度）。

- 把"已过期、仍 active"的未来计划记忆置 stale（#70-C 语义）：退出主动复习/无条件注入，
  检索保留（stale 在 _retrievable_status_clause 可见、rerank 降权）、可怀旧；
- next_review_at 置 None（停止主动复习轮转）、valid_to 置实际有效期（tense 规则解析）；
- 双通道：SQLite 提交后再同步 Chroma metadata（对齐 supersede._mark_vectors）+ bm25 失效；
- flag review_plan_expire_stale 默认 False（灰度）；任何异常静默返回 0，不阻塞日终维护。
- 不物理删除任何记忆。

2026-09-16（批次一任务3）覆盖面修正：原 SQL 预筛硬绑 ``memory_type == 'event'``，导致
「用户将前往长沙出差/旅行，行程6天」(7017，user_info/extracted) 与显式 ``sub_type='plan'``
的计划（9865，user_info/plan）永远扫不到（实测 event 分支已无可清项、非 event 项积压）。
现扫描窗扩为三条并集（只改覆盖面，**不改「已过期」判定**——classify_tense + is_plan_expired 仍是唯一权威）：
  ① memory_type='event' 且 content 命中 PLAN_MARKERS（原口径）；
  ② sub_type='plan'（显式计划标注，任意 memory_type）；
  ③ memory_type='user_info' 且 sub_type='extracted' 且命中 PLAN_MARKERS
     （classify_tense 本就把「user_info+extracted」交给将来时规则判定，非 enduring）。
刻意不纳入 `preference`（多为长期偏好，仅顺带提及计划词）与 `insight`（日记/朋友圈是既成记录），
避免把持久偏好与往事记录误当计划清掉——即「不放宽安全边界」。
"""
from __future__ import annotations

from sqlalchemy import and_, or_, select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.memory.tense import classify_tense, is_plan_expired, plan_valid_until, PLAN_MARKERS
from app.utils.timeutil import now_naive_utc
from app.utils.logger import get_logger

_logger = get_logger("memory.plan_expiry")

EXPIRE_BATCH_LIMIT = 200  # 单次最多置 stale 条数（日终维护节流）


def _plan_scan_window():
    """过期计划扫描窗（三条并集，见模块 docstring；只收窄扫描范围，不改过期判定）。"""
    like = or_(*[Memory.content.like(f"%{k}%") for k in PLAN_MARKERS])
    return or_(
        and_(Memory.memory_type == "event", like),
        Memory.sub_type == "plan",
        and_(Memory.memory_type == "user_info", Memory.sub_type == "extracted", like),
    )


def _is_expired_plan(m, now) -> bool:
    """过期计划判定唯一入口（扫描窗之外的精判）：classify_tense=plan 且 is_plan_expired。

    在线日终维护（expire_stale_plans）与只读治理/诊断（list_expired_plans）共用本函数，
    保证「跑一次」与「先看清单再跑」的口径完全一致。
    """
    return classify_tense(m) == "plan" and is_plan_expired(m, now)


async def list_expired_plans(limit: int = EXPIRE_BATCH_LIMIT) -> list[dict]:
    """只读：列出「已判定过期、仍 active」的计划类记忆（dry-run / 存量治理脚本用，不写任何东西）。

    与 ``expire_stale_plans`` 共用同一扫描窗（_plan_scan_window）与同一判定（_is_expired_plan），
    因此 `list_expired_plans()` 的条数就是「再跑一次 expire_stale_plans 会处理多少条」。
    """
    now = now_naive_utc()
    out: list[dict] = []
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Memory).where(
                Memory.status == "active",
                Memory.is_archived == False,  # noqa: E712
                _plan_scan_window(),
            ).order_by(Memory.created_at.asc()).limit(max(1, limit) * 4)
        )).scalars().all()
    for m in rows:
        if len(out) >= limit:
            break
        try:
            if _is_expired_plan(m, now):
                out.append({
                    "id": m.id, "character_id": m.character_id,
                    "memory_type": m.memory_type, "sub_type": m.sub_type,
                    "content": (m.content or "")[:60],
                    "created_at": m.created_at,
                    "valid_to": plan_valid_until(m, now) or now,
                })
        except Exception as e:
            _logger.warning("plan-expiry classify failed mem=%s: %s", getattr(m, "id", None), e)
    return out


async def expire_stale_plans(limit: int = EXPIRE_BATCH_LIMIT) -> int:
    """把已过期、仍 active 的未来计划记忆置为 stale；返回本次处理条数。

    SQL 侧先用扫描窗（``_plan_scan_window``：PLAN_MARKERS LIKE 预筛 + 显式 plan 标注）收窄，
    Python 侧再以 ``_is_expired_plan``（classify_tense + is_plan_expired，tense 规则是唯一权威）精判。
    幂等：只动 status='active' 的行，重复跑不会重复计数（已 stale 的不再入窗）。
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
                    _plan_scan_window(),
                ).order_by(Memory.created_at.asc()).limit(max(1, limit) * 4)
            )).scalars().all()
            for m in rows:
                if len(moved_ids) >= limit:
                    break
                try:
                    if _is_expired_plan(m, now):
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
