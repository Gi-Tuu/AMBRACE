"""memory.retrieve（F4 拆分，2026-08-31 自 service.py 迁入）。

接缝已摘除（2026-09-02）：改为顶部/函数顶显式 import（async_session_factory/text_embedding/
vector_search/bm25_search/_rrf 等可被 monkeypatch(service, ...) 的名字于函数顶延迟 import 自
app.memory.service，调用时解析；其余稳定名字模块级 import）。不再经 _sync_seams 把 service
命名空间同步进 globals。
"""
import json
import time

from sqlalchemy import select

from app.models.memory import Memory
from app.memory.embedding_cache import get_cached_embedding
from app.memory.service import (
    PINNED_BONUS,
    PINNED_QUOTA,
    _STALE,
    _logger,
    _now_naive,
    _retrievable_status_clause,
    _supersede_flag_on,
)


async def _rerank(results: list[dict], character_id: int, hit_count: dict[int, int] | None = None, relevance_bonus: dict[int, float] | None = None, return_debug: bool = False, _keep_score: bool = False):
    """B2 检索加权（向量路径与 keyword 兜底共用，M-P2-3）：以 DB 为准补全元数据
    （向量 meta 的 importance 可能过期），加分项：置顶恒在前、关系/情绪类近 7 天 +15、
    状态/剧情来源近 3 天 +10；60 天以上旧记忆 x0.8 抑制，避免旧记忆重要性虚高盖过
    近期关系温度。v2.1 加成：被多路查询召回（多路命中）说明与当前话题/情绪更相关，
    每多一路 +5。回填查询过滤 is_archived（向量残留的已软删记忆直接剔除，不参与注入）。

    #70 方案B（memory-trace 可观察）：return_debug=False 返回 list（清理临时 _score，零行为变化）；
    return_debug=True 返回 (ordered, debug)，debug 含 db_pool + rerank_top（Top10 的
    id/score/importance/has_why/status，体积按方案硬上限）。
    """
    from app.memory.service import async_session_factory
    if not results:
        return ([], {"db_pool": 0, "rerank_top": []}) if return_debug else []
    now = _now_naive()
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Memory).where(
                Memory.id.in_([r["id"] for r in results]),
                Memory.is_archived == False,   # noqa: E712
                _retrievable_status_clause(),   # #70-C：双通道过滤（flag 关=永真，逐字节一致）
            )
        )).scalars().all()
    meta = {m.id: m for m in rows}
    results = [r for r in results if r["id"] in meta]
    if not results:
        return ([], {"db_pool": 0, "rerank_top": []}) if return_debug else []
    _db_pool = len(results)  # #70-B：候选池大小（DB 回填后，能打分的条数）
    # 记忆架构 v2.1 Phase 4a：进行中目标话题 / 未完成（follow_up）话题 → 内容重叠加分
    goal_topics: list[str] = []
    unfin_topics: list[str] = []
    try:
        from app.models.memory import ConversationTopic
        async with async_session_factory() as _tdb:
            _trows = (await _tdb.execute(
                select(ConversationTopic).where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
            )).scalars().all()
        goal_topics = [t.topic for t in _trows if t.goal and t.topic]
        unfin_topics = [t.topic for t in _trows if t.follow_up and t.topic]
    except Exception:
        goal_topics, unfin_topics = [], []

    def _topic_bonus(content: str) -> int:
        c = content or ""
        if any(t and (t in c or c in t) for t in unfin_topics):
            return 15
        if any(t and (t in c or c in t) for t in goal_topics):
            return 10
        return 0

    _hit = hit_count or {}
    _bonus = relevance_bonus or {}   # RRF 相关性加权（2026-08-23 深化；异常为空则退化为纯合并）
    for r in results:
        m = meta.get(r["id"])
        base = float(m.importance or 0) if m else float(r.get("importance") or 0)
        score = base
        if m is not None:
            created = m.created_at
            if created is not None:
                created = created.replace(tzinfo=None) if created.tzinfo else created
            days = (now - created).days if created else 999
            if m.is_pinned:
                score += PINNED_BONUS  # M-P1-4：置顶加分从 10000 降到 500，由置顶配额控顶
            if m.sub_type in ("relationship", "emotion") and days <= 7:
                score += 15
            if m.source in ("state_trigger", "storyline") and days <= 3:
                score += 10
            if m.why_it_matters:
                score += 20  # 意义记忆（v2.1）：已提炼"为什么重要"的里程碑记忆优先
            if (m.contradiction_count or 0) > 0:
                score -= (m.contradiction_count or 0) * 10  # M-P1-2：被用户纠正过的记忆降权（矛盾惩罚）
            score += _topic_bonus(m.content)
            if days > 60:
                score *= 0.8
            r["importance"] = base
            r["created_at"] = m.created_at
            r["epistemic_status"] = m.epistemic_status
            r["speaker_id"] = m.speaker_id
            r["speaker_type"] = m.speaker_type
            r["contradiction_count"] = m.contradiction_count
            r["is_pinned"] = bool(m.is_pinned)
            # #70 方案A：L0 需要 why_it_matters（无则缺省，_final 带出）；status 兼容旧记忆（无该列 => active）
            r["why_it_matters"] = m.why_it_matters
            r["status"] = getattr(m, "status", "active")
            try:
                from app.memory.reliability import reliability_score
                r["reliability_score"] = reliability_score(m)
            except Exception:
                r["reliability_score"] = None
        score += (_hit.get(r["id"], 1) - 1) * 5
        score += _bonus.get(r["id"], 0.0)   # RRF：按稠密/稀疏两路 rank 融合的相关性加权
        # #70-C：stale（派生失效结论）降权 0.5，排序落到同分 active 之后；flag 关不参与（逐字节一致）
        if _supersede_flag_on() and r.get("status") == _STALE:
            score *= 0.5
        r["_score"] = score
    results.sort(key=lambda x: x.get("_score") or 0, reverse=True)
    # M-P1-4：置顶配额——排序后最多保留 PINNED_QUOTA 条置顶，其余置顶不挤占非置顶槽位；
    # 置顶与非置顶各自保持分数排序稳定，整体条数由调用方取 limit 决定。
    if any(r.get("is_pinned") for r in results):
        _pinned_top = [r for r in results if r.get("is_pinned")][:PINNED_QUOTA]
        _normal_top = [r for r in results if not r.get("is_pinned")]
        results = _pinned_top + _normal_top

    # #70-B：return_debug=True 时返回 (ordered, debug)，debug 含 rerank 前后 Top10 观测；
    # False 路径与现状一致——清理临时 _score 后返回 list（零行为变化）。
    if return_debug:
        debug = {
            "db_pool": _db_pool,
            "rerank_top": [
                {
                    "id": r["id"],
                    "score": round(float(r.get("_score") or 0.0), 3),
                    "importance": round(float(r.get("importance") or 0.0), 1),
                    "has_why": bool(r.get("why_it_matters")),
                    "status": r.get("status", "active"),
                }
                for r in results[:10]
            ],
        }
        if not _keep_score:
            for r in results:
                r.pop("_score", None)
        return results, debug

    for r in results:
        r.pop("_score", None)
    return results


# ── Ariadne 模块 D（2026-09-04）：自然收敛替代硬截断（PAR 寻峰本地平替，纯函数）──
# 阈值来源（E v2 标定，scripts/diagnostics/memory_context_bench.py，104 例数据集）：
# 观测 gold 命中项 _score 分布与弃权类（abstention）候选分布后取安全边界——
# 弃权类候选全部低于 floor、gold 类不因 gap/floor 误杀。E v1 首轮实测（abstention 失败）
# 证明 floor 必要；标定过程与数据见 docs/dev-changelog 2026-09-04 节。
PEAK_MIN_KEEP = 3      # 至少保留条数（硬下界，防全灭）
PEAK_MAX_KEEP = 8      # 至多保留条数（给后续 diversify/limit 截断留池）
PEAK_SCORE_GAP = 12.0  # 相邻分数陡降阈值（>gap 视为断档，截断其后）
PEAK_MIN_SCORE = 18.0  # 分数地板（rerank 分被 importance 主导，仅兜极低重要度；相关性主要靠稠密距离地板）
PEAK_DENSE_MAX_DISTANCE = 0.50  # 稠密 cosine 距离地板（E v2 距离标定，104 例：弃权类候选 min=0.502 全切、gold 命中 P50=0.419/P90=0.526——尾部 gold 命中约 10-15% 以弃权正确率换之；探针 _dist_calib.py）


def peak_cutoff(ranked: list[dict], *, min_keep: int = PEAK_MIN_KEEP, max_keep: int = PEAK_MAX_KEEP,
                score_gap: float = PEAK_SCORE_GAP, min_score: float = PEAK_MIN_SCORE) -> list[dict]:
    """按 rerank _score 自然收敛（纯函数，可单测）：至少 min_keep；之后遇「分数陡降(>score_gap)」
    或「低于 min_score 地板」即止，至多 max_keep。输入须已按 _score 降序、元素含 _score。

    替代硬 top-limit 截断：避免漏掉成簇相关项，也避免无脑塞满（弃权场景候选整体弱相关时
    自然收敛到极少）。flag memory_peak_cutoff 默认关——关=现状路径逐字节不变。
    """
    if not ranked:
        return []
    # 地板先行：候选整体低于 floor（弃权/弱相关场景）→ 收敛为空（方案原稿的「无条件 min_keep」
    # 会使弃权场景仍注入 min_keep 条、abstention 永远不过——此处为有意偏离并已在回报说明）。
    above = [r for r in ranked if float(r.get("_score") or 0) >= min_score]
    if not above:
        return []
    out = above[:min_keep]
    for prev, cur in zip(above[min_keep - 1:], above[min_keep:]):
        if len(out) >= max_keep:
            break
        if float(prev.get("_score") or 0) - float(cur.get("_score") or 0) > score_gap:
            break
        out.append(cur)
    return out


def _diversify_by_type(ranked: list[dict], topk: int, per_type_cap: int = 2) -> list[dict]:
    """M1-S1（2026-08-31）类型多样性重排（纯函数，可单测）：

    每类先取 per_type_cap 条做一轮（保持原相对顺序），不足 topk 再按原序补齐；
    避免 top3/top5 被同一 memory_type 占满、中段记忆永无出场机会。
    输入须已按相关性降序；返回条数 = min(len(ranked), topk)，与 [:topk] 恒等条数。
    """
    if topk <= 0 or not ranked:
        return []
    picked: list[dict] = []
    seen: set = set()
    bucket_count: dict = {}
    for m in ranked:
        t = m.get("type", "event")
        if bucket_count.get(t, 0) >= per_type_cap:
            continue
        if m["id"] in seen:
            continue
        bucket_count[t] = bucket_count.get(t, 0) + 1
        seen.add(m["id"])
        picked.append(m)
        if len(picked) >= topk:
            return picked
    for m in ranked:
        if m["id"] not in seen:
            picked.append(m)
            seen.add(m["id"])
            if len(picked) >= topk:
                break
    return picked


async def search_memories(
    character_id: int,
    query: str,
    limit: int = 5,
    queries: list[str] | None = None,
    trace_meta: dict | None = None,
    time_range: tuple | None = None,
) -> list[dict]:
    """检索记忆（认知循环 v2.1 多路召回）：向量优先，兜底关键词。

    多路查询（原 query + 感知派生查询，最多 4 路）各召回后按 id 合并；
    加权排序（importance + 关系/情绪时效 + 置顶 + 多路命中加成）取 top limit。
    """

    from app.memory.service import (
        async_session_factory,
        bm25_search,
        _rrf,
        text_embedding,
        vector_search,
    )
    _t0 = time.monotonic()
    # #70 方案B（memory-trace 可观察）：读 feature flag，默认开；flag 关时检索/排序/trace 与现状一致。
    try:
        from app.agent.loop import AGENT_FLAGS
        _trace_debug = bool(AGENT_FLAGS.get("memory_trace_debug", True))
    except Exception:
        _trace_debug = False
    # 检索轨迹 debug（只多写 trace，不影响返回结果）：体积硬上限（每路 id≤5、rrf/rerank_top≤10、
    # preview≤60 字、steps_json≤8000 字符）在组装处逐一钳制。
    debug: dict = {"query": (query or "")[:60], "derived_queries": list(queries or [])[:3]}
    query_list = [query] + (list(queries or [])[:3])
    results: list[dict] = []
    hit_count: dict[int, int] = {}

    # P1 性能（2026-08-16）：多路召回并发执行（原串行最多 4 倍延迟放大）
    # X-3（2026-08-18）：主查询（用户原话）不缓存；感知派生查询（话题/情绪词）走进程内 LRU
    # 缓存（key=character_id+query，TTL 5 分钟，命中免 ONNX 推理——派生查询在相近消息间高度
    # 重复，缓存收益最大；主查询每轮内容变化、命中率低且需保持最新，故不缓存）
    async def _dense_one(i: int) -> list[dict]:
        q = query_list[i]
        derived = i > 0
        try:
            if derived:
                embedding = await get_cached_embedding(character_id, q)
            else:
                embedding = await text_embedding(q)
            hits = await vector_search(
                character_id=character_id,
                query_embedding=embedding,
                limit=limit * 2,  # 多取一些，按重要性排序后截断
            )
            # 模块 D：稠密相似度地板（flag memory_peak_cutoff 开）——弱相关候选在源头剔除，
            # 「时间对/语义弱」与本地板正交（时间路由 SQL 时间窗独立召回）。
            try:
                from app.agent.loop import AGENT_FLAGS as _af
                if _af.get("memory_peak_cutoff", False):
                    hits = [h for h in hits if float(h.get("distance") or 0) <= PEAK_DENSE_MAX_DISTANCE]
            except Exception:
                pass
            return hits
        except Exception:
            return []

    # BM25 稀疏路（2026-08-23 检索增强）：与向量路并行召回；异常静默返回 []，不影响主链路
    async def _sparse_one(i: int) -> list[tuple[int, float]]:
        q = query_list[i]
        try:
            return await bm25_search(character_id, q, top_k=max(limit * 2, 5))
        except Exception:
            return []

    import asyncio as _asyncio
    # 双路并行：每路对多路查询各自召回（原向量路与新增 BM25 路）
    dense_hits, sparse_hits = await _asyncio.gather(
        _asyncio.gather(*[_dense_one(i) for i in range(len(query_list))]),
        _asyncio.gather(*[_sparse_one(i) for i in range(len(query_list))]),
    )

    # #70-B：稠密/稀疏两路命中 id（每路 ≤5，体积上限）
    debug["dense_hits"] = [d["id"] for _hits in dense_hits for d in (_hits or [])][:5]
    debug["sparse_hits"] = [mid for _hits in sparse_hits for mid, _sc in (_hits or [])][:5]

    # RRF 融合（2026-08-23 深化）：dense/sparse 各按相关性 rank 归一化，融合分作 relevance_bonus
    # 注入 _rerank；RRF 计算异常时静默退化为纯合并（relevance_bonus 空），不影响主链路。
    relevance_bonus: dict[int, float] = {}
    debug["rrf_top"] = []
    try:
        _ranked: list[list] = []
        for _hits in dense_hits:
            _ranked.append([_r["id"] for _r in _hits])
        for _hits in sparse_hits:
            _ranked.append([_mid for _mid, _sc in _hits])
        _rrf_scores = _rrf.reciprocal_rank_fusion(_ranked, k=_rrf._BRRF_DEFAULT_K)
        relevance_bonus = _rrf.normalized_bonus(_rrf_scores, weight=_rrf._RRF_WEIGHT)
        # #70-B：RRF 融合后按分数降序的 Top10 id（体积上限）
        debug["rrf_top"] = sorted(_rrf_scores, key=lambda x: _rrf_scores[x], reverse=True)[:10]
    except Exception as _e:
        _logger.warning("RRF fusion failed, degrade to pure merge: %s", _e)
        relevance_bonus = {}

    # 合并 vector（dense）：按 id 去重并入 hit_count（多路查询命中同样计入多路命中加成）
    _seen_ids: set[int] = set()
    for hits in dense_hits:
        for r in hits:
            rid = r["id"]
            hit_count[rid] = hit_count.get(rid, 0) + 1
            if rid not in _seen_ids:
                _seen_ids.add(rid)
                results.append(r)

    # 合并 BM25（sparse）：hit_count 累加（与向量路重叠 = 多路命中，_rerank 每多一路 +5）；
    # 仅 BM25 命中的新 id 需从 DB 补 content/type/importance（向量路已带全量字段）。
    _new_sparse_ids: list[int] = []
    for hits in sparse_hits:
        for mid, _score in hits:
            hit_count[mid] = hit_count.get(mid, 0) + 1
            if mid not in _seen_ids:
                _seen_ids.add(mid)
                _new_sparse_ids.append(mid)
    if _new_sparse_ids:
        try:
            async with async_session_factory() as _db:
                _rows = (await _db.execute(
                    select(Memory).where(
                        Memory.id.in_(_new_sparse_ids),
                        Memory.is_archived == False,   # noqa: E712
                        Memory.memory_type != "working_state",  # M3-a：补取双保险，挡旧索引残留 id
                        _retrievable_status_clause(),   # #70-C：双通道过滤（flag 关=永真）
                    )
                )).scalars().all()
            for _m in _rows:
                results.append({
                    "id": _m.id,
                    "content": _m.content,
                    "type": _m.memory_type,
                    "importance": float(_m.importance or 0),
                })
        except Exception as _e:
            _logger.warning("BM25 sparse hit enrich failed: %s", _e)

    # P2-4 召回候选命中数（2026-08-23）：多路（向量/BM25）合并去重后的候选池大小（截断/插件追加前），
    # 供「召回 N / 返回 M」展示；行为不变（只改指标）。
    candidate_count = len(hit_count)

    _dense_has = any(hs for hs in dense_hits)
    _sparse_has = any(hs for hs in sparse_hits)

    _no_candidates = not results
    if not results:
        # 向量+BM25 双路皆空：LIKE 关键词兜底（仍走统一 _rerank：置顶/时效/意义/话题加分 + reliability 透传，M-P2-3）
        async with async_session_factory() as db:
            result = await db.execute(
                select(Memory)
                .where(
                    Memory.character_id == character_id,
                    Memory.is_archived == False,
                    Memory.memory_type != "working_state",  # M3-a：工作记忆不进召回（注入走专用分区）
                    Memory.content.like(f"%{query}%"),
                    _retrievable_status_clause(),   # #70-C：双通道过滤（flag 关=永真）
                )
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(limit * 2)
            )
            memories = result.scalars().all()
            results = [
                {
                    "id": m.id,
                    "content": m.content,
                    "type": m.memory_type,
                    "importance": m.importance,
                    "created_at": m.created_at,
                    "epistemic_status": m.epistemic_status,
                    "speaker_id": m.speaker_id,
                    "speaker_type": m.speaker_type,
                }
                for m in memories
            ]
        # 关键词兜底路径：双路无命中，候选池即当前关键词结果集合
        candidate_count = len(results)

    # Ariadne 模块 A（2026-09-03）：时间维度确定性检索路（flag memory_temporal_recall 默认关=零行为变化）。
    # 仅当调用方解析出时间区间（app/memory/time_query.parse_time_range）且 flag 开：
    # 区间内按重要度补一条确定性 SQL 召回，合并去重后参与同一套 rerank/截断（不享有特权插队），
    # 保证「时间对、语义弱」的记忆不被向量路漏掉。
    if time_range is not None:
        try:
            from app.agent.loop import AGENT_FLAGS as _af
            _temporal_on = bool(_af.get("memory_temporal_recall", False))
        except Exception:
            _temporal_on = False
        if _temporal_on:
            t_start, t_end = time_range
            async with async_session_factory() as _tdb:
                _trows = (await _tdb.execute(
                    select(Memory)
                    .where(
                        Memory.character_id == character_id,
                        Memory.is_archived == False,  # noqa: E712
                        Memory.memory_type != "working_state",
                        Memory.created_at >= t_start,
                        Memory.created_at < t_end,
                        _retrievable_status_clause(),
                    )
                    .order_by(Memory.importance.desc(), Memory.created_at.desc())
                    .limit(limit)
                )).scalars().all()
            _have = {r["id"] for r in results}
            _added = 0
            for _m in _trows:
                if _m.id in _have:
                    continue
                _have.add(_m.id)
                _added += 1
                results.append({
                    "id": _m.id,
                    "content": _m.content,
                    "type": _m.memory_type,
                    "importance": _m.importance,
                    "created_at": _m.created_at,
                })
            if _trace_debug and _added:
                debug["time_route"] = {"range": [str(t_start), str(t_end)], "added": _added}

    if results:
        # 模块 D：peak_cutoff 需要 _score（flag memory_peak_cutoff 开时强制走 debug 路径并保留分数）
        try:
            from app.agent.loop import AGENT_FLAGS as _af
            _peak_on = bool(_af.get("memory_peak_cutoff", False))
        except Exception:
            _peak_on = False
        # #70-B：flag 开走 _rerank(return_debug=True) 取 debug 并入 trace；关走原非 debug 路径（零行为变化）。
        if _trace_debug or _peak_on:
            _ranked, _rk_debug = await _rerank(results, character_id, hit_count, relevance_bonus=relevance_bonus, return_debug=True, _keep_score=_peak_on)
            debug.update(_rk_debug)
        else:
            _ranked = await _rerank(results, character_id, hit_count, relevance_bonus=relevance_bonus)
        # M1-S1（2026-08-31）：类型多样性重排（flag 开）——防单一类型占满出口；关=纯 _ranked[:limit] 旧行为
        try:
            from app.agent.loop import AGENT_FLAGS as _af
            _diversify = bool(_af.get("recall_diversify", True))
        except Exception:
            _diversify = True
        if _peak_on:
            # 模块 D：先自然收敛（断档/地板截断）再类型均衡；条数可少于 limit（弃权/弱相关场景）
            _kept = peak_cutoff(_ranked)
            results = _diversify_by_type(_kept, limit) if _diversify else _kept[:limit]
            for r in results:
                r.pop("_score", None)  # 对外形状与旧路径一致
        else:
            results = _diversify_by_type(_ranked, limit) if _diversify else _ranked[:limit]

    # 插件 Hook：memory_search（调整/追加召回记忆；插件返回的 dict 列表追加到结果，原结果让位给插件追加；异常隔离）
    try:
        from app.plugins.registry import run_hook_collect
        _hook_items = await run_hook_collect("memory_search", {
            "character_id": character_id,
            "query": query,
            "results": list(results),
            "limit": limit,
        })
        if _hook_items:
            _seen = {r.get("id") for r in results}
            _extra: list[dict] = []
            for _item in _hook_items:
                _cand = _item.get("result")
                if not isinstance(_cand, list):
                    continue
                for _m in _cand:
                    if not isinstance(_m, dict) or _m.get("id") is None:
                        continue
                    if _m["id"] in _seen:
                        continue
                    _seen.add(_m["id"])
                    _extra.append({
                        "id": _m["id"],
                        "content": str(_m.get("content") or ""),
                        "type": str(_m.get("type") or "plugin"),
                        "importance": float(_m.get("importance") or 0),
                    })
            if _extra:
                results = results[:max(0, limit - len(_extra))] + _extra
    except Exception:
        pass

    _final = [
        {
            "id": r["id"],
            "content": r["content"],
            "type": r["type"],
            "importance": float(r["importance"] or 0),
            "created_at": r.get("created_at"),
            "epistemic_status": r.get("epistemic_status"),
            "speaker_id": r.get("speaker_id"),
            "speaker_type": r.get("speaker_type"),
            "reliability_score": r.get("reliability_score"),
            "contradiction_count": r.get("contradiction_count"),
            "why_it_matters": r.get("why_it_matters"),
            "status": r.get("status", "active"),
        }
        for r in results
    ]

    # #70-B：汇总 debug 的候选数 / 最终注入（preview≤60）/ 延迟；并保留 hit_count 供旧读端（agent-mind/既有测试）。
    _latency_ms = int((time.monotonic() - _t0) * 1000)
    if _trace_debug:
        debug.update({
            "candidate_count": candidate_count,
            "hit_count": candidate_count,  # 兼容旧读端（agent-mind / 既有 trace 测试）
            "limit": limit,  # M1-S11：recall_pool_vs_return 读端用 candidate_count vs returned vs limit 聚合
            "returned": [{"id": m["id"], "preview": (m.get("content") or "")[:60]} for m in _final],
            "latency_ms": _latency_ms,
        })

    # P0-2 记忆检索 Trace（2026-08-16）：只写不读，失败静默，为 Memory Benchmark 提供数据
    try:
        from app.agent.trace import enqueue_task_log
        # 检索增强（2026-08-23）：route 标记召回来源——hybrid=向量+BM25 双路 / dense=仅向量 /
        # sparse=仅 BM25 / keyword=双路皆空时的 LIKE 兜底
        if _no_candidates:
            route = "keyword"
        elif _dense_has and _sparse_has:
            route = "hybrid"
        elif _sparse_has:
            route = "sparse"
        else:
            route = "dense"
        if _trace_debug:
            # #70-B：把汇总 debug 写透（只多写 trace，防膨胀：steps_json 硬上限 8000 字符）
            debug["route"] = route
            steps_json = json.dumps(debug, ensure_ascii=False)[:8000]
        else:
            # 关 flag：trace 与现状逐字节一致（不写扩充 debug）
            steps_json = json.dumps({
                "query": query,
                "queries": len(query_list),
                "hit_ids": [str(i) for i in (r["id"] for r in results)][:5],
                # P2-4 语义修正：hit_count=召回候选命中数（合并去重后的候选池大小），
                # returned=实际返回条数；旧日志无 returned 字段，展示端回退用 hit_count。
                "hit_count": candidate_count,
                "returned": len(results),
            }, ensure_ascii=False)
        enqueue_task_log(
            character_id=character_id,
            user_id=(trace_meta or {}).get("user_id"),
            session_id=(trace_meta or {}).get("session_id"),
            task_id=(trace_meta or {}).get("task_id"),
            trigger="memory_search",
            route=route,
            steps_json=steps_json,
            latency_ms=_latency_ms,
            status="ok",
        )
    except Exception as _e:
        _logger.warning("Memory search trace failed: %s", _e)

    return _final
