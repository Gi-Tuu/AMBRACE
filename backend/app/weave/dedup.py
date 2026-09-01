"""织库卡片查重/去重：embedding 余弦 + 标题/概要文本相似度，贪心分组合并

- 查重：两两相似度 ≥ 阈值归组（联通分量），组内保留记忆数最多/重要度最高的一张
- 去重：被删卡片的参与记忆关联转移到保留卡（去重 memory_id），不删除原始记忆
"""
import json
import math

from sqlalchemy import delete, select

from app.db.database import async_session_factory
from app.models.memory import WeaveCard, WeaveCardCharacter, WeaveCardMemory

SIM_COS_WEIGHT = 0.7  # embedding 余弦权重
SIM_TEXT_WEIGHT = 0.3  # 标题/概要文本相似权重
SIM_THRESHOLD = 0.82  # 综合相似度阈值（≥ 视为重复）


def _cos_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _text_sim(a: str, b: str) -> float:
    """中文场景字符 bigram Jaccard 相似度"""

    def bigrams(s: str) -> set[str]:
        s = s.replace(" ", "").replace("\u3000", "")
        return {s[i : i + 2] for i in range(max(0, len(s) - 1))}

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def _parse_embedding(raw: str | None) -> list[float]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [float(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


def _similarity(a: dict, b: dict) -> float:
    cos = _cos_sim(a["embedding"], b["embedding"])
    text = max(_text_sim(a["title"], b["title"]), _text_sim(a["summary"], b["summary"]))
    # 任一卡片缺向量时，仅以文本相似度判断（避免向量缺失导致永远不达标）
    if not a["embedding"] or not b["embedding"]:
        return text
    if cos <= 0 and text <= 0:
        return 0.0
    return SIM_COS_WEIGHT * cos + SIM_TEXT_WEIGHT * text


def find_duplicates(cards: list[dict]) -> list[list[dict]]:
    """两两相似度 ≥ 阈值，BFS 归组；每组按（记忆数↓、重要度↓、id↑）排序，第 1 张为 keeper"""
    n = len(cards)
    adj: list[set[int]] = [set() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if _similarity(cards[i], cards[j]) >= SIM_THRESHOLD:
                adj[i].add(j)
                adj[j].add(i)
    seen = [False] * n
    groups: list[list[dict]] = []
    for i in range(n):
        if seen[i]:
            continue
        stack = [i]
        seen[i] = True
        g: list[int] = []
        while stack:
            u = stack.pop()
            g.append(u)
            for v in adj[u]:
                if not seen[v]:
                    seen[v] = True
                    stack.append(v)
        if len(g) >= 2:
            members = sorted(
                (cards[u] for u in g),
                key=lambda c: (-len(c["memory_ids"]), -c["importance"], c["id"]),
            )
            groups.append(members)
    return groups




async def _load_cards(user_id: int, domain: str = "shared") -> list[dict]:
    """取该用户指定域的卡片（含 embedding 与参与记忆 id 集合）"""
    async with async_session_factory() as db:
        cards = (await db.execute(
            select(WeaveCard).where(WeaveCard.user_id == user_id, WeaveCard.domain == domain)
        )).scalars().all()
        mem_rows = []
        if cards:
            mem_rows = (
                await db.execute(select(WeaveCardMemory.card_id, WeaveCardMemory.memory_id))
            ).all()
    counts: dict[int, set[int]] = {}
    for cid, mid in mem_rows:
        counts.setdefault(cid, set()).add(mid)
    return [
        {
            "id": c.id,
            "title": c.title or "",
            "summary": c.summary or "",
            "importance": float(c.importance or 0),
            "embedding": _parse_embedding(c.embedding),
            "memory_ids": counts.get(c.id, set()),
        }
        for c in cards
    ]

def _brief(c: dict) -> dict:
    return {
        "id": c["id"],
        "title": c["title"],
        "summary": c["summary"],
        "memory_count": len(c["memory_ids"]),
    }


async def dedup_check(user_id: int, domain: str = "shared") -> dict:
    """查重：返回重复组预览（keeper + duplicates），不执行任何修改"""
    cards = await _load_cards(user_id, domain)
    groups = find_duplicates(cards)
    return {
        "groups": [{"keeper": _brief(g[0]), "duplicates": [_brief(c) for c in g[1:]]} for g in groups],
        "total_groups": len(groups),
    }


async def dedup_execute(user_id: int, domain: str = "shared") -> dict:
    """去重：每组保留 keeper，删除重复卡并转移参与记忆关联（不删原始记忆）"""
    cards = await _load_cards(user_id, domain)
    groups = find_duplicates(cards)
    removed = 0
    async with async_session_factory() as db:
        for g in groups:
            keeper = g[0]
            dup_ids = [c["id"] for c in g[1:]]
            keeper_mids = set(keeper["memory_ids"])
            mem_rows = (
                await db.execute(select(WeaveCardMemory).where(WeaveCardMemory.card_id.in_(dup_ids)))
            ).scalars().all()
            for row in mem_rows:
                if row.memory_id in keeper_mids:
                    await db.delete(row)  # keeper 已含该记忆，去掉重复关联
                else:
                    row.card_id = keeper["id"]  # 转移关联到保留卡
                    keeper_mids.add(row.memory_id)
            for cid in dup_ids:
                await db.execute(delete(WeaveCardCharacter).where(WeaveCardCharacter.card_id == cid))
                await db.execute(delete(WeaveCard).where(WeaveCard.id == cid))
            removed += len(dup_ids)
        await db.commit()
    return {"groups": len(groups), "removed": removed}
