# -*- coding: utf-8 -*-
"""派生查询 embedding 进程内 LRU 缓存（X-3，2026-08-18）

感知派生查询（话题/情绪词）在相近消息间高度重复：主链路每次回复的 topic/emotion
派生词（如「低落难过」「开心高兴」）在短时间窗口内反复出现，每次走本地 ONNX
推理纯属浪费。此处做进程内 LRU 缓存（key=character_id+query，TTL 5 分钟，命中免推理）。
记忆写入后的新鲜度：TTL 短（5 分钟）即可，失效策略保持简单（到期即弃，不额外联动写入）。
主查询（用户原话）不缓存——每轮内容变化、命中率低，且需保持最新（见 service.search_memories）。
"""
import time
from collections import OrderedDict

from app.memory.embedding import text_embedding

_MAX_ENTRIES = 512    # LRU 容量上限（512 组 1024 维向量，进程内可接受）
_TTL_SECONDS = 300    # 5 分钟

_cache: OrderedDict[tuple[int, str], tuple[float, list[float]]] = OrderedDict()


def _now() -> float:
    return time.monotonic()


def _evict_expired() -> None:
    """惰性剔除过期条目（TTL 到期的条目在下一次访问/写入前清掉；策略简单化）"""
    if not _cache:
        return
    now = _now()
    stale = [k for k, (ts, _v) in _cache.items() if now - ts > _TTL_SECONDS]
    for k in stale:
        del _cache[k]


async def get_cached_embedding(character_id: int, query: str) -> list[float]:
    """带进程内 LRU 缓存的 embedding 获取：TTL 内命中直接返回缓存向量（免推理），
    未命中则推理并写缓存。character_id 为空或 query 为空时不缓存（直接推理）。"""
    if character_id is None or not query:
        return await text_embedding(query)
    key = (int(character_id), query)
    now = _now()
    hit = _cache.get(key)
    if hit is not None:
        ts, vec = hit
        if now - ts <= _TTL_SECONDS:
            _cache.move_to_end(key)
            return vec
        del _cache[key]
    vec = await text_embedding(query)
    _evict_expired()
    if len(_cache) >= _MAX_ENTRIES:
        while len(_cache) >= _MAX_ENTRIES:
            _cache.popitem(last=False)
    _cache[key] = (now, vec)
    return vec


def cache_stats() -> dict:
    """观测/测试用：当前缓存条目数、容量上限与 TTL"""
    _evict_expired()
    return {"entries": len(_cache), "max": _MAX_ENTRIES, "ttl_seconds": _TTL_SECONDS}


def clear_cache() -> None:
    """清空缓存（测试/运维用）"""
    _cache.clear()
