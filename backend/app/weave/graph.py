"""织库画布图数据：节点 + 关联边（Phase B/C）

关联强度 = 0.45*共享记忆占比 + 0.30*卡片向量余弦 + 0.25*时间邻近
节点含跨角色合并信息（character_ids）与 mood（画布筛选）
"""
import json
from app.utils.logger import get_logger
import math

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.life import LifeInterest
from app.models.memory import Memory
from app.models.weave_card import WeaveCard, WeaveCardCharacter, WeaveCardMemory

_logger = get_logger("weave.graph")

MIN_EDGE_STRENGTH = 0.15
MAX_EDGES = 400


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / denom


def _pick_life_type(sub_types: list[str]) -> str:
    """私域节点生活类型：reflection > note > life_event（按参与记忆 sub_type 聚合）"""
    if "reflection" in sub_types:
        return "reflection"
    if "note" in sub_types:
        return "note"
    return "life_event"


def _extract_mood(detail_json: str | None) -> str:
    """从卡片详情 JSON 提取 mood（画布筛选用），失败返回空"""
    if not detail_json:
        return ""
    try:
        d = json.loads(detail_json)
        if isinstance(d, dict):
            return str(d.get("mood") or "").strip()
    except Exception:
        pass
    return ""


async def build_graph(user_id: int, character_id: int | None = None, domain: str = "shared") -> dict:
    """构建画布数据 {"nodes": [...], "edges": [...]}（跨角色卡片按 character_ids 归属）"""
    async with async_session_factory() as db:
        q = select(WeaveCard).where(WeaveCard.user_id == user_id, WeaveCard.domain == domain)
        all_cards = (await db.execute(q.order_by(WeaveCard.importance.desc()))).scalars().all()
        if not all_cards:
            return {"nodes": [], "edges": []}
        card_ids = [c.id for c in all_cards]
        cc_rows = (
            await db.execute(
                select(WeaveCardCharacter.card_id, WeaveCardCharacter.character_id).where(
                    WeaveCardCharacter.card_id.in_(card_ids)
                )
            )
        ).all()
        card_chars: dict[int, set[int]] = {}
        for r in cc_rows:
            card_chars.setdefault(r[0], set()).add(r[1])
        all_char_ids = {c.character_id for c in all_cards} | {
            cid for s in card_chars.values() for cid in s
        }
        name_rows = (
            await db.execute(
                select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(all_char_ids))
            )
        ).all()
        names = {r[0]: r[1] for r in name_rows}
        # 角色过滤：主角色匹配 或 跨角色关联包含（or_ 语义）
        if character_id is not None:
            cards = [
                c
                for c in all_cards
                if c.character_id == character_id or character_id in card_chars.get(c.id, set())
            ]
            if not cards:
                return {"nodes": [], "edges": []}
        else:
            cards = all_cards
        rel_rows = (
            await db.execute(select(WeaveCardMemory.card_id, WeaveCardMemory.memory_id))
        ).all()
        mem_map: dict[int, set[int]] = {c.id: set() for c in cards}
        for r in rel_rows:
            if r[0] in mem_map:
                mem_map[r[0]].add(r[1])
        emb_map: dict[int, list[float]] = {}
        for c in cards:
            if c.embedding:
                try:
                    emb_map[c.id] = json.loads(c.embedding)
                except Exception:
                    pass
        # 私域增强（Phase 3）：参与记忆 sub_type 聚合 → 节点生活类型；角色兴趣关键词 → 热点标记
        life_type_map: dict[int, str] = {}
        hot_map: dict[int, list[str]] = {}
        if domain == "private":
            mem_ids = {mid for s in mem_map.values() for mid in s}
            sub_map: dict[int, str] = {}
            if mem_ids:
                mrows = (
                    await db.execute(
                        select(Memory.id, Memory.sub_type).where(Memory.id.in_(mem_ids))
                    )
                ).all()
                sub_map = {r[0]: r[1] or "life_event" for r in mrows}
            char_ids = {c.character_id for c in cards}
            interests = (
                await db.execute(
                    select(LifeInterest.name).where(
                        LifeInterest.character_id.in_(char_ids),
                        LifeInterest.level >= 40,
                    )
                )
            ).scalars().all() if char_ids else []
            interest_names = [str(x) for x in interests]
            for c in cards:
                subs = [sub_map[mid] for mid in mem_map.get(c.id, set()) if mid in sub_map]
                life_type_map[c.id] = _pick_life_type(subs)
                hits = [
                    kw for kw in interest_names
                    if kw and (kw in (c.title or "") or kw in (c.summary or ""))
                ]
                if hits:
                    hot_map[c.id] = hits[:2]

    nodes = []
    for c in cards:
        cids = sorted(card_chars.get(c.id, set()) | {c.character_id})
        parts = [names.get(x, f"角色{x}") for x in cids]
        cname = "、".join(parts[:2]) + (f" 等{len(parts)}" if len(parts) > 2 else "")
        nodes.append(
            {
                "id": c.id,
                "character_id": c.character_id,
                "character_ids": cids,
                "character_name": cname,
                "title": c.title,
                "summary": c.summary,
                "importance": round(float(c.importance or 0), 1),
                "mood": _extract_mood(c.detail),
                "created_at": c.created_at,
                "life_type": life_type_map.get(c.id, ""),
                "hot_tags": hot_map.get(c.id, []),
            }
        )
    edges = []
    for i in range(len(cards)):
        a = cards[i]
        for j in range(i + 1, len(cards)):
            b = cards[j]
            sa, sb = mem_map[a.id], mem_map[b.id]
            share_ratio = len(sa & sb) / max(1, min(len(sa), len(sb)))
            cos = _cos(emb_map.get(a.id, []), emb_map.get(b.id, []))
            ta = a.created_at.timestamp() if a.created_at else 0.0
            tb = b.created_at.timestamp() if b.created_at else 0.0
            days = abs(ta - tb) / 86400.0
            time_prox = 1.0 / (1.0 + days / 15.0)
            strength = 0.45 * share_ratio + 0.30 * max(0.0, cos) + 0.25 * time_prox
            if strength >= MIN_EDGE_STRENGTH:
                edges.append({"source": a.id, "target": b.id, "strength": round(strength, 3)})
    edges.sort(key=lambda e: -e["strength"])
    edges = edges[:MAX_EDGES]
    _logger.info("weave graph: nodes=%d edges=%d", len(nodes), len(edges))
    return {
        "nodes": nodes,
        "edges": edges,
        "characters": [{"id": k, "name": v} for k, v in names.items()],
    }
