"""World State 查询层（World & Cognition P4，2026-08-15）

事件 → 规则折叠 → 世界事实（当前状态，event-sourced 物化视图，不是新数据库）：

- assert_fact：断言新事实；同 subject+predicate 的 active 事实自动 supersede（新替旧）
- get_active_facts / get_character_view：按 audience 可见性 + 过期时间过滤查询
- fold_status_update：聊天【状态更新】标记 → 角色当前状态事实（FACT，TTL 12h）
- fold_activity：life.activity_completed 事件 → 角色最近活动事实

范围（2026-08-15 用户拍板）：只做世界状态记忆增强（让 AI 记得"正在进行/刚发生的事"），
不做内容审查/违规重生成类拦截，保持角色扮演自由度。
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.database import async_session_factory
from app.events.schema import EPISTEMIC_FACT
from app.models.memory import WorldFact
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc as _now_naive

_logger = get_logger("events.facts")

PUBLIC_AUDIENCE = "public"
MAX_FACTS_PER_CHAR = 12  # 每 (用户, 角色) 活跃事实上限，超出按最旧淘汰
STATUS_FRESH_HOURS = 12  # status 类瞬时状态注入新鲜度窗口（2026-08-16：防 stale 状态反复注入）


def _status_fresh(asserted_at: datetime | None, now: datetime) -> bool:
    """status 事实是否在新鲜窗口内（naive UTC 比较；asserted_at 缺失视为不新鲜，保守不注入）"""
    if asserted_at is None:
        return False
    at = asserted_at.replace(tzinfo=None) if asserted_at.tzinfo else asserted_at
    return (now - at) <= timedelta(hours=STATUS_FRESH_HOURS)


def _latest_facts_by_predicate(facts: list, predicates: set) -> list:
    """同 predicate 只保留最新一条（入参已按 asserted_at 倒序）；矛盾状态只注入最新，避免场景错乱（2026-08-16）"""
    seen = set()
    out = []
    for f in facts:
        p = getattr(f, "predicate", None)
        if p in predicates:
            if p in seen:
                continue
            seen.add(p)
        out.append(f)
    return out


def audience_list(audience: list) -> str:
    """audience（[(type,id)...] 或 ["public"]）→ 存储 JSON 字符串。"""
    items = []
    for a in audience or []:
        items.append(a if isinstance(a, str) else f"{a[0]}:{a[1]}")
    return json.dumps(items, ensure_ascii=False)


def audience_visible(stored: str | None, viewer_type: str, viewer_id: int) -> bool:
    """可见性判定：public 所有人可见；否则 viewer 在列表中可见。纯函数。

    viewer_type 归一化：character/char 统一为 char（audience 存储用短名）。
    """
    if not stored:
        return False
    try:
        items = json.loads(stored)
    except Exception:
        return False
    if PUBLIC_AUDIENCE in items:
        return True
    t = "char" if viewer_type in ("character", "char") else viewer_type
    return f"{t}:{viewer_id}" in items


def fact_text(fact: WorldFact) -> str:
    """事实 → 注入文本行（认知状态前缀：FACT 默认不标，其余显式标注）。"""
    prefix = ""
    if getattr(fact, "is_authoritative", False):
        prefix = "[权威] "
    elif fact.epistemic_status and fact.epistemic_status != EPISTEMIC_FACT:
        prefix = f"[{fact.epistemic_status}] "
    _t = getattr(fact, "asserted_at", None) or getattr(fact, "created_at", None)
    _time_tag = f"[记录于 {str(_t)[:10]}] " if _t else ""
    return f"- {_time_tag}{prefix}{fact.object_value[:120]}"


async def assert_fact(
    *, subject_type: str, subject_id: int, predicate: str, object_value: str,
    user_id: int, character_id: int,
    audience: list | None = None,
    epistemic_status: str = EPISTEMIC_FACT,
    confidence: float = 1.0,
    source: str | None = None,
    source_event_id: str | None = None,
    ttl_minutes: int | None = None,
    author: str = "system",
    is_authoritative: bool = False,
) -> int | None:
    """断言世界事实：旧 active 同键事实 supersede → 插入新事实 → 活跃上限淘汰。失败静默返回 None。"""
    try:
        now = _now_naive()
        async with async_session_factory() as db:
            old = (await db.execute(
                select(WorldFact).where(
                    WorldFact.user_id == user_id,
                    WorldFact.character_id == character_id,
                    WorldFact.subject_type == subject_type,
                    WorldFact.subject_id == subject_id,
                    WorldFact.predicate == predicate,
                    WorldFact.status == "active",
                )
            )).scalars().all()
            aud = audience_list(audience) if audience is not None else json.dumps(
                [f"user:{user_id}", f"char:{character_id}"], ensure_ascii=False)
            expires_at = (now + timedelta(minutes=ttl_minutes)) if ttl_minutes else None
            f = WorldFact(
                user_id=user_id, character_id=character_id,
                subject_type=subject_type, subject_id=subject_id,
                predicate=predicate, object_value=object_value[:200],
                confidence=max(0.0, min(1.0, float(confidence))),
                epistemic_status=epistemic_status,
                audience=aud, source=source, source_event_id=source_event_id,
                expires_at=expires_at,
                author=author, is_authoritative=is_authoritative,
            )
            db.add(f)
            await db.flush()
            for o in old:
                o.status = "superseded"
                o.superseded_by = f.id
                o.superseded_at = now
            active = (await db.execute(
                select(WorldFact).where(
                    WorldFact.user_id == user_id,
                    WorldFact.character_id == character_id,
                    WorldFact.status == "active",
                ).order_by(WorldFact.asserted_at.asc())
            )).scalars().all()
            if len(active) > MAX_FACTS_PER_CHAR:
                for extra in active[:len(active) - MAX_FACTS_PER_CHAR]:
                    extra.status = "superseded"
                    extra.superseded_at = now
            await db.commit()
            return f.id
    except Exception as e:
        _logger.warning("assert_fact failed %s/%s/%s: %s", subject_type, subject_id, predicate, e)
        return None


async def get_active_facts(
    *, character_id: int, user_id: int,
    viewer_type: str = "character", viewer_id: int | None = None,
    limit: int = 8,
) -> list[WorldFact]:
    """当前可见的活跃事实（audience 过滤 + 过期过滤）。"""
    try:
        now = _now_naive()
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(WorldFact).where(
                    WorldFact.character_id == character_id,
                    WorldFact.user_id == user_id,
                    WorldFact.status == "active",
                ).order_by(WorldFact.is_authoritative.desc(), WorldFact.asserted_at.desc()).limit(limit * 4)  # 权威事实优先（P1-3）；预取放大防过期/不可见事实占位截断
            )).scalars().all()
        out = []
        v_id = viewer_id if viewer_id is not None else character_id
        for f in rows:
            if f.expires_at is not None:
                exp = f.expires_at.replace(tzinfo=None) if f.expires_at.tzinfo else f.expires_at
                if exp <= now:
                    continue
            # 瞬时状态新鲜度兜底（2026-08-16）：status 事实超过窗口不注入（兼容无 TTL 旧数据）
            if f.predicate == "status" and not _status_fresh(f.asserted_at, now):
                continue
            if audience_visible(f.audience, viewer_type, v_id):
                out.append(f)
        # 矛盾状态只取最新（2026-08-16）：status/activity 同 predicate 只注入最新一条，避免场景错乱
        out = _latest_facts_by_predicate(out, {"status", "activity"})
        # 权威事实稳定优先（P1-3）：同一角色多条事实时权威设定不被瞬时状态挤掉
        out = sorted(out, key=lambda f: (0 if getattr(f, "is_authoritative", False) else 1), reverse=False)
        return out[:limit]
    except Exception as e:
        _logger.warning("get_active_facts failed char=%d: %s", character_id, e)
        return []


async def get_character_view(character_id: int, user_id: int, limit: int = 6) -> str:
    """角色视角的世界状态文本（注入对话上下文用；失败静默返回空串）。"""
    facts = await get_active_facts(
        character_id=character_id, user_id=user_id,
        viewer_type="character", viewer_id=character_id, limit=limit,
    )
    if not facts:
        return ""
    return "\n".join(fact_text(f) for f in facts)


async def fold_status_update(character_id: int, user_id: int, status_text: str) -> None:
    """聊天【状态更新】标记 → 角色当前状态事实（FACT，audience=[用户,角色]，TTL 12h）。"""
    text = (status_text or "").strip()
    if not text:
        return
    await assert_fact(
        subject_type="character", subject_id=character_id, predicate="status",
        object_value=text, user_id=user_id, character_id=character_id,
        audience=[("user", user_id), ("char", character_id)],
        epistemic_status=EPISTEMIC_FACT, confidence=0.9, source="chat_status",
        ttl_minutes=STATUS_FRESH_HOURS * 60,  # 瞬时状态 12h 自动过期（2026-08-16）
    )


async def fold_activity(character_id: int, user_id: int, activity_type: str, summary: str = "") -> None:
    """Life 活动完成 → 角色最近活动事实（FACT）。"""
    text = (summary or activity_type or "").strip()[:120]
    if not text:
        return
    await assert_fact(
        subject_type="character", subject_id=character_id, predicate="activity",
        object_value=text, user_id=user_id, character_id=character_id,
        audience=[("user", user_id), ("char", character_id)],
        epistemic_status=EPISTEMIC_FACT, confidence=0.85, source="life_activity",
        ttl_minutes=3 * 24 * 60,  # 活动事实 3 天过期，防旧活动内容常驻（2026-08-17）
    )
