"""Lorebook 关键词匹配器（P1-2，2026-08-16）：用户消息 → 命中条目 → 注入候选

匹配规则：
- 关键词必须 ≥2 字（单字子串易误伤，如「猫」命中「猫屎咖啡」）；
- 子串匹配（简单可控）；条目配置排除词时，文本含任一排除词则该条目本轮不触发；
- 返回命中条目（active 且命中），按 updated_at 倒序，条数受 MAX_LOREBOOK_HITS 限制。
"""
import json
from datetime import datetime

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.lorebook_entry import LorebookEntry
from app.utils.logger import get_logger

_logger = get_logger("memory.lorebook")

MAX_LOREBOOK_HITS = 3  # 单轮最多命中条数（防注入膨胀）


def _split_keywords(raw: str) -> list[str]:
    """解析 JSON 关键词列表，过滤空与单字（防「猫」命中「猫屎咖啡」类误伤）"""
    try:
        ks = json.loads(raw or "[]")
    except Exception:
        return []
    out = []
    for k in ks:
        s = str(k or "").strip()
        if len(s) >= 2:
            out.append(s)
    return out


def match_lorebook_entries(text: str, entries: list[LorebookEntry]) -> list[LorebookEntry]:
    """纯函数：对给定文本匹配条目（关键词命中且无排除词）；返回按 updated_at 倒序"""
    if not text:
        return []
    hits = []
    for e in entries:
        if not e.active:
            continue
        kws = _split_keywords(e.keywords)
        if not kws:
            continue
        if not any(k in text for k in kws):
            continue
        exs = _split_keywords(e.exclude_keywords)
        if any(x in text for x in exs):
            continue
        hits.append(e)
    hits.sort(key=lambda e: (e.updated_at or e.created_at) or datetime.min, reverse=True)
    return hits[:MAX_LOREBOOK_HITS]


async def load_matching_entries(character_id: int, text: str) -> list[LorebookEntry]:
    """DB 加载该角色全部活跃条目并匹配（失败静默返回空）"""
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(LorebookEntry).where(
                    LorebookEntry.character_id == character_id,
                    LorebookEntry.active == True,
                )
            )).scalars().all()
        return match_lorebook_entries(text, list(rows))
    except Exception as e:
        _logger.warning("Lorebook match failed char=%d: %s", character_id, e)
        return []
