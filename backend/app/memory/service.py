"""记忆服务：结构化记忆（SQLite）与向量记忆（ChromaDB）的读写入口"""
import json
import time  # noqa: F401  # F4 接缝：以下导入经 _sync_seams 同步到 write/retrieve/maintain，勿按“未使用”删除

from sqlalchemy import select, true as _sql_true  # noqa: F401  # F4 接缝

from app.db.database import async_session_factory  # noqa: F401  # F4 接缝
from app.db.vector_store import (  # noqa: F401  # F4 接缝
    add_memory,
    search_memories as vector_search,
    delete_memory_vector,
    find_similar_memory,
)
from app.models.memory import Memory  # noqa: F401  # F4 接缝
from app.utils.logger import get_logger
from app.memory.embedding import text_embedding  # noqa: F401  # F4 接缝
from app.memory.embedding_cache import get_cached_embedding  # noqa: F401  # F4 接缝（X-3 LRU 缓存）
from app.memory.constants import (  # noqa: F401  # F4 接缝
    DECAY_MAX_PCT,
    VECTOR_DEDUP_THRESHOLD, S_DEFAULT, S_MIN_DAYS, S_MAX_DAYS,
    REINFORCE_FACTOR_WRITE,
)
from app.memory.decay import retention_pct  # noqa: F401  # F4 接缝
from app.memory.bm25_index import search as bm25_search, invalidate as bm25_invalidate  # noqa: F401  # F4 接缝（检索增强）
from app.memory import rrf as _rrf  # noqa: F401  # F4 接缝（检索增强深化）

_logger = get_logger("memory.service")

# M-P1-4（2026-08-18）：置顶加分与置顶配额——置顶摘要不再 +10000 恒霸检索 top3，
# 近期具体事件/情绪记忆（非置顶）也能进入注入上下文。
PINNED_BONUS = 500.0     # 置顶加分（原 10000 → 500）
PINNED_QUOTA = 2         # 排序后结果中最多保留的置顶条数，其余置顶不挤占非置顶槽位

# #70-C 状态词表（与 epistemic_status 正交）：active=现行 / superseded=被取代 / stale=派生失效
_ACTIVE = "active"
_SUPERSEDED = "superseded"
_STALE = "stale"


def _supersede_flag_on() -> bool:
    """#70-C 门控：读 memory_supersede flag（延迟 import，避免顶层循环依赖 loop）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_supersede", False))
    except Exception:
        return False


def _retrievable_status_clause():
    """检索可见集合 = {active, stale}（stale 在 rerank 降权）。flag 关返回永真，与现状逐字节一致。"""
    if not _supersede_flag_on():
        return _sql_true()
    return Memory.status.in_([_ACTIVE, _STALE])


def _active_status_clause():
    """无条件注入/展示/结算 = 仅 active。flag 关返回永真，与现状逐字节一致。"""
    if not _supersede_flag_on():
        return _sql_true()
    return Memory.status == _ACTIVE


def star_from_pct(pct: float) -> int:
    """百分比重要度转 1-5 星：pct/20 取整钳制到 1-5"""
    return max(1, min(5, round((pct or 0.0) / 20.0)))


def _normalize_importance(imp) -> float:
    """重要度标度归一化（M-P2-2）：≤5 视为 1-5 星制（×20 → 百分比），否则视为已是百分比原值返回。
    创建路径与三条查重/合并路径共用，避免调用方以百分比传入时把旧记忆 importance 顶高。
    """
    v = float(imp or 0)
    return v * 20.0 if v <= 5 else v

from app.utils.timeutil import now_naive_utc as _now_naive


def _merge_derived(raw, extra_ids: list[int]) -> str:
    """#70-C M2：把 extra_ids 并入既有 derived_from_ids（JSON 数组字符串），去重、幂等。"""
    try:
        cur = [int(x) for x in json.loads(raw or "[]")]
    except Exception:
        cur = []
    for i in extra_ids:
        if i is not None and int(i) not in cur:
            cur.append(int(i))
    return json.dumps(cur, ensure_ascii=False)


def _apply_reinforce(m, factor: float, now) -> None:
    """艾宾浩斯强化（同步，操作 ORM 对象，由调用方 commit）：
    S *= factor（上限 S_MAX）、review_count+1、刷新 last_reinforce_at、
    取消删除倒计时，importance 回升到至少"复习半日后保留率"。
    强化视为一次成功复习：排下次主动复习时间（now + S 天）。is_locked 记忆不参与。
    """
    import math
    from datetime import timedelta
    if m.is_locked:
        return
    s = float(m.strength_days or S_DEFAULT)
    m.strength_days = min(S_MAX_DAYS, max(S_MIN_DAYS, s * factor))
    m.review_count = (m.review_count or 0) + 1
    m.last_reinforce_at = now
    m.delete_at = None
    s_new = float(m.strength_days)
    pct = min(DECAY_MAX_PCT, max(float(m.importance or 40.0), math.exp(-0.5 / s_new) * 120.0))
    m.importance = pct
    m.next_review_at = now + timedelta(days=s_new)
    m.updated_at = now


async def reinforce_memories(
    memory_ids: list[int],
    factor: float,
    debounce_hours: float = 0.0,
) -> None:
    """艾宾浩斯强化（独立 session 版）：S *= factor + review_count+1 + 刷新遗忘起点。

    debounce_hours > 0 时距上次强化不足该时长则跳过（检索命中防抖）。
    """
    from datetime import timedelta
    if not memory_ids:
        return
    now = _now_naive()
    async with async_session_factory() as db:
        for mid in memory_ids:
            m = await db.get(Memory, mid)
            if m is None or m.is_archived or m.is_pinned or m.is_locked:
                continue
            last = m.last_reinforce_at
            # 防抖仅对"已强化过"的记忆生效（review_count>0），新记忆首次命中不拦截
            if debounce_hours > 0 and (m.review_count or 0) > 0 and last is not None:
                last = last.replace(tzinfo=None) if last.tzinfo else last
                if (now - last) < timedelta(hours=debounce_hours):
                    continue
            _apply_reinforce(m, factor, now)
        await db.commit()


def _initial_strength(memory_type: str) -> float:
    """新记忆初始强度 S（按类型，艾宾浩斯）"""
    from app.memory.constants import S_BY_TYPE
    return S_BY_TYPE.get(memory_type, S_DEFAULT)

async def save_memory(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/write.py；先同步 service 命名空间再委托（保 patch 接缝）。"""
    from app.memory import write as _m
    _m._sync_seams()
    return await _m.save_memory(*args, **kwargs)


async def _rerank(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/retrieve.py。"""
    from app.memory import retrieve as _m
    _m._sync_seams()
    return await _m._rerank(*args, **kwargs)


def _diversify_by_type(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/retrieve.py（M1-S1 类型多样性重排）。"""
    from app.memory import retrieve as _m
    _m._sync_seams()
    return _m._diversify_by_type(*args, **kwargs)


async def search_memories(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/retrieve.py。"""
    from app.memory import retrieve as _m
    _m._sync_seams()
    return await _m.search_memories(*args, **kwargs)


async def list_memories(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/maintain.py。"""
    from app.memory import maintain as _m
    _m._sync_seams()
    return await _m.list_memories(*args, **kwargs)


async def delete_memory(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/maintain.py。"""
    from app.memory import maintain as _m
    _m._sync_seams()
    return await _m.delete_memory(*args, **kwargs)



