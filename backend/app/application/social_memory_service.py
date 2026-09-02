"""社交记忆服务（Module B，2026-08-10 拍板实施）。

平台关系档案读写（upsert / list / build 注入文本）。
硬约束：只操作 social_memories 表，绝不写入常规 memories / stage_memories 库。
"""
import json

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.social import SocialMemory
from app.utils.timeutil import now_naive_utc as _now_naive

_LEVEL_CN = {
    "stranger": "陌生访客",
    "follower": "粉丝",
    "familiar": "熟络粉丝",
}


async def upsert_social_memory(
    platform: str,
    external_user_key: str,
    *,
    nickname: str | None = None,
    relationship_level: str | None = None,
    topics: list[str] | None = None,
    trust_score: int | None = None,
) -> None:
    """互动 upsert：存在则互动计数 +1 / 刷新最近互动时间；不存在则新建。

    relationship_level 仅在传入且非空时覆盖；topics 做去重合并（最多保留 20 个）；
    trust_score 钳制在 0-100。
    """
    key = (external_user_key or "").strip()
    if not platform or not key:
        return
    now = _now_naive()
    async with async_session_factory() as db:
        row = (await db.execute(
            select(SocialMemory).where(
                SocialMemory.platform == platform,
                SocialMemory.external_user_key == key,
            )
        )).scalars().first()
        if row is None:
            try:
                topics_json = json.dumps(topics or [], ensure_ascii=False)
            except Exception:
                topics_json = "[]"
            db.add(SocialMemory(
                platform=platform,
                external_user_key=key[:200],
                nickname=(nickname or "").strip()[:100] or key[:100],
                interaction_count=1,
                relationship_level=(relationship_level or "stranger")[:20],
                topics_json=topics_json,
                trust_score=max(0, min(100, int(trust_score) if trust_score is not None else 50)),
                last_interaction_at=now,
            ))
        else:
            row.interaction_count = (row.interaction_count or 0) + 1
            row.last_interaction_at = now
            if nickname and nickname.strip():
                row.nickname = nickname.strip()[:100]
            if relationship_level and relationship_level.strip():
                row.relationship_level = relationship_level.strip()[:20]
            if topics:
                try:
                    old = json.loads(row.topics_json or "[]")
                except Exception:
                    old = []
                merged = list(dict.fromkeys([str(t) for t in old] + [str(t) for t in topics]))[:20]
                row.topics_json = json.dumps(merged, ensure_ascii=False)
            if trust_score is not None:
                row.trust_score = max(0, min(100, int(trust_score)))
        await db.commit()


async def list_top_memories(platform: str, limit: int = 5) -> list[dict]:
    """按互动次数 + 最近互动时间取 top N 社交记忆"""
    if limit <= 0:
        return []
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(SocialMemory)
            .where(SocialMemory.platform == platform)
            .order_by(
                SocialMemory.interaction_count.desc(),
                SocialMemory.last_interaction_at.desc().nullslast(),
            )
            .limit(limit)
        )).scalars().all()
        out = []
        for r in rows:
            try:
                topics = json.loads(r.topics_json or "[]")
            except Exception:
                topics = []
            out.append({
                "external_user_key": r.external_user_key,
                "nickname": r.nickname or r.external_user_key,
                "interaction_count": r.interaction_count or 0,
                "relationship_level": r.relationship_level or "stranger",
                "topics": topics,
                "trust_score": r.trust_score or 50,
                "last_interaction_at": r.last_interaction_at,
            })
        return out


async def build_social_context(platform: str, limit: int = 5) -> str:
    """生成「你记得的粉丝/常互动用户」注入文本；无数据返回空串"""
    items = await list_top_memories(platform, limit)
    if not items:
        return ""
    parts = []
    for it in items:
        lvl = _LEVEL_CN.get(it["relationship_level"], "粉丝")
        parts.append(f"{it['nickname']}（{lvl}，互动 {it['interaction_count']} 次）")
    return "你记得的粉丝与常互动用户：" + "、".join(parts)
