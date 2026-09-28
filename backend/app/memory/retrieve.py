"""memory.retrieve（F4 拆分，2026-08-31 自 service.py 迁入）。

接缝已摘除（2026-09-02）：改为顶部/函数顶显式 import（async_session_factory/text_embedding/
vector_search/bm25_search/_rrf 等可被 monkeypatch(service, ...) 的名字于函数顶延迟 import 自
app.memory.service，调用时解析；其余稳定名字模块级 import）。不再经 _sync_seams 把 service
命名空间同步进 globals。
"""
import json
import time

from sqlalchemy import or_, select

from app.models.memory import Memory
from app.memory.embedding_cache import get_cached_embedding
from app.memory.service import (
    PINNED_BONUS,
    PINNED_QUOTA,
    _STALE,
    _logger,
    _now_naive,
    _retrievable_status_clause,
)


def _perception_tag_on() -> bool:
    """批 0-2 M1a：召回输出是否补 source/sub_type 字段（flag 默认关＝逐字节旧输出）；异常回落 False。

    本 flag 在这里**只决定输出多不多两个字段**：不改排序、不改条数、不改预算、不改阈值、
    不剔除任何条目（剔除属 M2）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("perception_source_tag", False))
    except Exception:
        return False


# 批 0-2 M2 禁令 3（召回侧）：被隔离感知条的负向偏置。
# 取 -15 的理由：既有加分档位是 +20（意义记忆）/ +15（关系情绪近 7 天、未完成话题）/ +10（状态剧情近 3 天），
# 分数又被 importance（0~100 量级）主导。-15 与这些既有档位同量级 ⇒ 足以让「同分竞争的感知条」落到
# 非感知条之后，又不至于把用户明确在问的感知条压到地板（本批要求「降权不剔除」，见方案 §2.2 / 待拍板 1）。
PERCEPTION_QUARANTINE_PENALTY = -15.0


def _perception_isolate_on() -> bool:
    """批 0-2 M2：隔离禁令总闸（默认关＝排序逐字节旧行为）；异常回落 False（R8：回退退得干净）。

    开时**只做一件事**：给被隔离的感知条在 rerank 里加一个负向偏置。
    禁止把它当 exclude 用——用户问「刚才屏幕上那个」必须还能命中，条数不得因本偏置而减少。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("perception_isolate", False))
    except Exception:
        return False


def _quarantine_penalty(source, epistemic_status) -> float:
    """被隔离感知条的排序偏置（纯调用 M0 判据）；flag 关 / 非感知 / 已认可 ⇒ 0.0（逐字节旧行为）。"""
    if not _perception_isolate_on():
        return 0.0
    from app.memory.perception_tier import is_quarantined
    try:
        return PERCEPTION_QUARANTINE_PENALTY if is_quarantined(source, epistemic_status) else 0.0
    except Exception:
        return 0.0


# ── 批 0-7 任务①（2026-09-28，雷达 08）：召回排序的「显式 recency」项 ──
# 既有加分里只有两处带时间，且都按 sub_type/source 触发（关系/情绪近 7 天 +15、状态/剧情近 3 天 +10）：
# 一条昨天写下的普通 event 与一条三年前写下的同分 event 在时效上**没有任何差别**，只能靠 importance 硬拼。
# 这里补一个对所有记忆都生效的显式档位，取值刻意与既有档位同量级（+20/+15/+10）：足以让「新」与
# 「为什么重要」(+20)、「多路命中」(+5/路) 正面对撞，又盖不过 importance（0~120 量级）与置顶（+500）。
# 分档**不叠加**：每条只取自己所在那一档，30 天以上为 0（与旧行为完全一致，也不与 days>60 的 x0.8 相互纠缠）。
RECENCY_TIERS: tuple[tuple[float, float], ...] = (
    (24.0, 20.0),         # 24 小时内：今天/刚才发生的事最该先看到
    (24.0 * 7, 15.0),     # 7 天内：本周
    (24.0 * 30, 10.0),    # 30 天内：近期
)


def _recency_bonus_on() -> bool:
    """批 0-7 任务①：显式 recency 是否生效（flag 默认关＝排序逐字节旧行为）；异常回落 False（R8：退得干净）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("recall_recency_bonus", False))
    except Exception:
        return False


def _recency_bonus_hours(age_hours: float) -> float:
    """纯函数：记忆年龄（小时）→ 显式时效加分。越新分越高，只取所在档位、不叠加。

    超出所有档位 ⇒ 0.0（旧行为）；未来时间戳（时钟回拨/脏数据）按最新档处理。
    """
    for limit, bonus in RECENCY_TIERS:
        if age_hours <= limit:
            return bonus
    return 0.0


# ── 批 0-7 任务②（2026-09-28，雷达 08）：命中记忆的「相邻块」 ──
# 先核对过现有三条通道，都**不覆盖**这个需求：二跳（memory_recall_second_hop）要模型主动打 [RECALL]
# 标记才会再查一次；时序召回（memory_temporal_recall）的时间窗来自用户原话解析，不是「命中条目的邻域」；
# 沿链补充（memory_chain_expand）在注入层按 chain_id/parent_id 找邻居，没挂上链的条根本没有邻居可带。
# 本单补的缺口＝同角色（群记忆则同群）+ created_at 紧邻的时间邻域：memories 表没有会话外键，
# 同一轮对话产出的几条记忆在写入时间上天然相邻，用 ±30 分钟窗口近似「同一段对话的邻居」。
# 只补本轮空缺槽位（见 _expand_neighbor_blocks），不新增条数上限、不挤占任何一条已有结果。
NEIGHBOR_WINDOW_MINUTES = 30      # 邻域半宽（±30 分钟）
NEIGHBOR_ANCHOR_MAX = 2           # 只给排序最前的 2 条命中找邻居（与沿链补充同口径，防 SQL 放大）
NEIGHBOR_PER_ANCHOR_MAX = 2       # 每个锚点最多带出 2 条
NEIGHBOR_QUERY_LIMIT = 12         # 单锚点窗口内取回上限（邻居异常密集时的体积钳制）


def _neighbor_block_on() -> bool:
    """批 0-7 任务②：相邻块是否生效（flag 默认关＝不额外发任何查询、逐字节旧行为）；异常回落 False。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("recall_neighbor_block", False))
    except Exception:
        return False


def _pick_nearest(rows: list[dict], anchor_created, cap: int) -> list[dict]:
    """纯函数：窗口内候选按「与锚点的时间距离」最近优先取 cap 条（同距离按 id 升序，稳定可测）。"""
    if anchor_created is None or cap <= 0 or not rows:
        return []

    def _gap(r: dict) -> float:
        c = r.get("created_at")
        if c is None:
            return float("inf")
        c = c.replace(tzinfo=None) if c.tzinfo else c
        return abs((c - anchor_created).total_seconds())

    return sorted(rows, key=lambda r: (_gap(r), r.get("id") or 0))[:cap]


async def _neighbor_rows_for_anchor(character_id: int, anchor: dict, have: set[int]) -> list[dict]:
    """单个锚点的时间邻域查询（只读；异常静默 []，绝不影响主链路）。

    have 为「已出现过的 id」集合：本函数会把新候选登记进去，跨锚点共用即可去重。
    """
    from datetime import timedelta

    from app.memory.service import async_session_factory

    created = anchor.get("created_at")
    if created is None:
        return []
    created = created.replace(tzinfo=None) if created.tzinfo else created
    win = timedelta(minutes=NEIGHBOR_WINDOW_MINUTES)
    cond = [
        Memory.character_id == character_id,
        Memory.is_archived == False,   # noqa: E712
        Memory.memory_type != "working_state",   # M3-a 同口径：工作记忆不进召回
        Memory.created_at >= created - win,
        Memory.created_at <= created + win,
        _retrievable_status_clause(),   # #70-C 双通道过滤（flag 关=永真）
    ]
    gid = anchor.get("group_id")
    if gid is not None:
        cond.append(Memory.group_id == gid)   # 群记忆只带同群邻居，别把别的群的流水拖进来
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Memory).where(*cond).order_by(Memory.created_at.asc()).limit(NEIGHBOR_QUERY_LIMIT)
        )).scalars().all()
    out: list[dict] = []
    for m in rows:
        if m.id == anchor.get("id") or m.id in have:
            continue
        have.add(m.id)
        out.append({
            "id": m.id,
            "content": m.content,
            "type": m.memory_type,
            "importance": float(m.importance or 0),
            "created_at": m.created_at,
        })
    return out


async def _expand_neighbor_blocks(character_id: int, ranked: list[dict], slots: int) -> list[dict]:
    """给本轮最靠前的命中条带出「±窗口」邻居，最多 slots 条（最近优先）。

    - ranked 须是已过 _rerank 的行（带 created_at/group_id）；
    - 邻居同样过一次 _rerank 回填字段（输出形状与普通命中逐字节一致，不另开一套字段），
      但**不参与**本轮排序竞争——调用处只把它塞进「本轮不足 limit 的空缺槽位」。
    """
    if slots <= 0 or not ranked:
        return []
    try:
        have = {r["id"] for r in ranked if r.get("id") is not None}
        picked: list[dict] = []
        for anchor in ranked[:NEIGHBOR_ANCHOR_MAX]:
            cand = await _neighbor_rows_for_anchor(character_id, anchor, have)
            for row in _pick_nearest(cand, anchor.get("created_at"), NEIGHBOR_PER_ANCHOR_MAX):
                if len(picked) >= slots:
                    break
                picked.append(row)
            if len(picked) >= slots:
                break
        if not picked:
            return []
        scored = await _rerank(picked, character_id)
        by_id = {r["id"]: r for r in scored}
        return [by_id[p["id"]] for p in picked if p["id"] in by_id]
    except Exception as _e:
        _logger.warning("neighbor block expand failed char=%s: %s", character_id, _e)
        return []


# ── 批 0-11（2026-09-28，雷达 44）：专名确定性匹配「第三路」 ──
# 现网召回＝向量（bge-m3）+ 关键词（BM25）两路 RRF 融合，两路都吃「词形」；专名（人名/昵称/
# 关系称谓）最容易字面错开：问「我妈」而记忆写「母亲」、问「mike」而记忆写「MIKE」、
# 问「阿明」而记忆写「小明」。本路只做**确定性**匹配（词面抽取与判定见 memory/entity_match.py，
# 纯字符串/正则/字典，零模型、零外网、零新依赖），命中的 id 并入既有 RRF 与 _rerank——
# 不另开一套排序、不插队、不剔除任何已有候选，条数上限与 token 预算一律不变。
# 与批 0-7 的相互作用见 §docs/feature-flags.md 十三（recency 只改次序、邻居只补空缺槽）。
ENTITY_ROUTE_LIMIT = 8    # 单轮实体路最多带回几条候选（与 limit 解耦，防 LIKE 把候选池灌满）
ENTITY_REASON_TRACE_MAX = 5   # trace 里最多记几条命中理由（体积钳制）


def _entity_match_on() -> bool:
    """批 0-11：专名第三路是否生效（flag 默认关＝不发那条 LIKE 查询、逐字节旧行为）；异常回落 False。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("recall_entity_match", False))
    except Exception:
        return False


async def _entity_route(character_id: int, query: str) -> tuple[list[dict], dict]:
    """专名路（只读）：抽词面 → 一条 LIKE 粗筛 → 内存逐条判定，返回 (命中行, 观测元数据)。

    SQL 只负责「粗筛」，是否命中一律以 `hit_reasons` 的归一化比对为准（捞进来但对不上的行
    一条都不留）；抽不出词面 ⇒ 连查询都不发。任何异常静默退化为空，绝不影响主链路。
    """
    from app.memory.entity_match import extract_terms, hit_reasons, pull_literals

    from app.memory.service import async_session_factory

    try:
        terms = extract_terms(query)
        if not terms:
            return [], {"terms": [], "reasons": []}
        literals = pull_literals(terms)
        if not literals:
            return [], {"terms": terms, "reasons": []}
        conds = [
            Memory.character_id == character_id,
            Memory.is_archived == False,   # noqa: E712
            Memory.memory_type != "working_state",   # M3-a 同口径：工作记忆不进召回
            or_(*[Memory.content.like(f"%{_like_escape(s)}%", escape="\\") for s in literals]),
            _retrievable_status_clause(),   # #70-C 双通道过滤（flag 关=永真）
        ]
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(*conds)
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(ENTITY_ROUTE_LIMIT)
            )).scalars().all()
        out: list[dict] = []
        reasons: list[dict] = []
        for m in rows:
            hit = hit_reasons(m.content or "", terms)
            if not hit:
                continue
            out.append({
                "id": m.id,
                "content": m.content,
                "type": m.memory_type,
                "importance": float(m.importance or 0),
                "created_at": m.created_at,
            })
            _r = hit[0]
            reasons.append({"id": m.id, "term": _r["term"], "via": _r["via"],
                            "literal": _r["literal"], "folded": _r["folded"]})
        return out, {"terms": terms, "reasons": reasons[:ENTITY_REASON_TRACE_MAX]}
    except Exception as _e:
        _logger.warning("entity route failed char=%s: %s", character_id, _e)
        return [], {"terms": [], "reasons": []}


async def _rerank(results: list[dict], character_id: int, hit_count: dict[int, int] | None = None, relevance_bonus: dict[int, float] | None = None, return_debug: bool = False, _keep_score: bool = False):
    """B2 检索加权（向量路径与 keyword 兜底共用，M-P2-3）：以 DB 为准补全元数据
    （向量 meta 的 importance 可能过期），加分项：置顶恒在前、关系/情绪类近 7 天 +15、
    状态/剧情来源近 3 天 +10、显式 recency 档位（批 0-7 任务①，flag 门控）；60 天以上旧记忆 x0.8 抑制，避免旧记忆重要性虚高盖过
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
    _recency_on = _recency_bonus_on()   # 批 0-7 任务①：关 ⇒ 下面那一行加分恒为 0，排序逐字节旧行为
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
            if _recency_on:
                # 批 0-7 任务①：显式 recency（对全部记忆生效，与上面两条按 sub_type/source 触发的
                # 时效加分正交叠加）；flag 关 ⇒ 一行都不执行，逐字节旧排序
                _age_h = (now - created).total_seconds() / 3600.0 if created else float("inf")
                score += _recency_bonus_hours(_age_h)
            if m.why_it_matters:
                score += 20  # 意义记忆（v2.1）：已提炼"为什么重要"的里程碑记忆优先
            if (m.contradiction_count or 0) > 0:
                score -= (m.contradiction_count or 0) * 10  # M-P1-2：被用户纠正过的记忆降权（矛盾惩罚）
            # 批 0-2 M2 禁令 3：被隔离的感知条降权（负向偏置，**不剔除、不减条数**）；flag 关＝0.0 逐字节旧排序
            score += _quarantine_penalty(m.source, m.epistemic_status)
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
            # #72 场景过滤需要（只回填结果行，_final 不输出，故默认行为逐字节不变）
            r["source"] = m.source
            r["sub_type"] = m.sub_type
            r["group_id"] = m.group_id
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
        # #70-C / 2026-09-17 批次一（任务2）：stale（派生失效结论）降权 0.5 **无条件生效**——
        # 排序落到同分 active 之后。memory_supersede flag 从此只决定「怀旧面能否召回 stale」，
        # 不再门控降权本身（否则 flag 关时旧现状与现行事实同分竞争，旧「长沙」盖过现行湛江）。
        if r.get("status") == _STALE:
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


def _scene_filter(rows: list[dict], scene: str | None,
                  exclude_sources: set[str] | None, group_id: int | None) -> list[dict]:
    """#72 场景可见性过滤（纯函数；scene=None 时原样返回，保证零行为变化）。

    在最终返回前做纯内存过滤（结果行已带 source/sub_type/group_id，无需改向量库）：
    - scene="dm"：私聊不召回群聊逐条流水（旧 source=group 且非摘要指针）；群摘要指针
      (group_summary)/游戏摘要指针(game_summary)属"知道发生过"，允许保留。
    - scene="group"：其它群的 group 记忆不进本群上下文（本群的由专门 group 通道注入，不靠这里）。
    - exclude_sources：命中即剔除。
    """
    if not scene and not exclude_sources and group_id is None:
        return rows
    excl = exclude_sources or set()
    out = []
    for r in rows:
        src = r.get("source") or ""
        sub = r.get("sub_type") or ""
        gid = r.get("group_id")
        if src in excl:
            continue
        if scene == "dm":
            if src == "group" and sub != "group_summary":
                continue
            # #72 PR-C P3（2026-09-15）：DM 不召回逐角色群认知（与 group 逐条流水同理，保持 DM 干净）
            if src == "group_cognition":
                continue
        if scene == "group":
            if src == "group" and gid is not None and group_id is not None and gid != group_id:
                continue
        out.append(r)
    return out


def _like_escape(q: str) -> str:
    """P3-E：转义 LIKE 通配符（参数化绑定已防注入，此处只处理通配符语义）。

    先转义反斜杠自身，再转义 % 与 _；配合调用处 `.like(..., escape="\\\\")` 使用，
    避免用户搜「50%」「a_b」时把 % / _ 当通配符而误召回「5012」「axb」。
    """
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_memories(
    character_id: int,
    query: str,
    limit: int = 5,
    queries: list[str] | None = None,
    trace_meta: dict | None = None,
    time_range: tuple | None = None,
    # ↓↓ #72 新增：全部默认 None/空 = 与现状完全一致 ↓↓
    scene: str | None = None,              # "dm" | "group" | None(不过滤)
    exclude_sources: set[str] | None = None,
    group_id: int | None = None,
    # A2 M0-4（2026-09-20）：调用者账号，仅透传给 memory_search 插件 hook ctx（默认 None，
    # 放末尾保证既有调用不破；检索/排序/hook 返回语义零变化）。
    user_id: int | None = None,
) -> list[dict]:
    """检索记忆（认知循环 v2.1 多路召回）：向量优先，兜底关键词。

    多路查询（原 query + 感知派生查询，最多 4 路）各召回后按 id 合并；
    加权排序（importance + 关系/情绪时效 + 置顶 + 多路命中加成）取 top limit。
    批 0-11 起在向量 + 关键词之外还有**第三路专名匹配**（flag `recall_entity_match` 默认关＝
    连查询都不发）：命中的 id 同样并进 RRF 融合与 _rerank，不插队、不改条数（见 _entity_route）。
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

    # 批 0-11 专名第三路：与向量/BM25 并行；flag 关 ⇒ 立刻返回空，一条查询都不发
    async def _entity_one() -> tuple[list[dict], dict]:
        if not _entity_match_on():
            return [], {"terms": [], "reasons": []}
        return await _entity_route(character_id, query)

    import asyncio as _asyncio
    # 三路并行：向量路与 BM25 路各自对多路查询召回，专名路只发一条 LIKE 查询（批 0-11）
    dense_hits, sparse_hits, (entity_rows, entity_meta) = await _asyncio.gather(
        _asyncio.gather(*[_dense_one(i) for i in range(len(query_list))]),
        _asyncio.gather(*[_sparse_one(i) for i in range(len(query_list))]),
        _entity_one(),
    )

    # #70-B：稠密/稀疏两路命中 id（每路 ≤5，体积上限）
    debug["dense_hits"] = [d["id"] for _hits in dense_hits for d in (_hits or [])][:5]
    debug["sparse_hits"] = [mid for _hits in sparse_hits for mid, _sc in (_hits or [])][:5]
    # 批 0-11：专名路命中 + 命中理由（**只在真有命中时多写键** ⇒ flag 关时 trace 逐字节不变）
    _entity_ids = [r["id"] for r in entity_rows]
    if _entity_ids:
        debug["entity_hits"] = _entity_ids[:5]
        debug["entity_route"] = {
            "terms": (entity_meta.get("terms") or [])[:4],
            "reasons": (entity_meta.get("reasons") or [])[:ENTITY_REASON_TRACE_MAX],
        }

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
        if _entity_ids:
            # 批 0-11：专名路作为**第三路**进同一套 RRF（路序 dense→sparse→entity），
            # 只贡献一路 rank 证据，不给任何插队特权；旧的两路取值逐字节不变。
            _ranked.append(_entity_ids)
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

    # 合并专名路（批 0-11，flag recall_entity_match 默认关 ⇒ entity_rows 恒空、整段不执行）：
    # 与前两路完全同构——按 id 去重并入候选池，重叠即多一路命中（_rerank 每多一路 +5），
    # 不插队、不剔除、不加字段；行已带 content/type/importance，无需再补一次 DB 查询。
    for _r in entity_rows:
        _rid = _r["id"]
        hit_count[_rid] = hit_count.get(_rid, 0) + 1
        if _rid not in _seen_ids:
            _seen_ids.add(_rid)
            results.append(_r)

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
                    Memory.content.like(f"%{_like_escape(query)}%", escape="\\"),   # P3-E：% / _ 不再当通配符
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
        # M1-S1（2026-08-31）：类型多样性重排（曾为 flag recall_diversify，2026-09-17 固化常开）——防单一类型占满出口
        _diversify = True
        if _peak_on:
            # 模块 D：先自然收敛（断档/地板截断）再类型均衡；条数可少于 limit（弃权/弱相关场景）
            _kept = peak_cutoff(_ranked)
            results = _diversify_by_type(_kept, limit) if _diversify else _kept[:limit]
            for r in results:
                r.pop("_score", None)  # 对外形状与旧路径一致
        else:
            results = _diversify_by_type(_ranked, limit) if _diversify else _ranked[:limit]

        # 批 0-7 任务②：命中记忆的相邻块（flag recall_neighbor_block 默认关＝不发这条查询）。
        # 只填「本轮不足 limit 的空缺槽位」：条数上限不变、已有结果一条都不被挤掉，邻居排在直接命中之后。
        # peak_cutoff 开时跳过——那条 flag 的语义就是「弱相关就少注入/弃权」，拿邻居补空缺会与之相冲。
        if _neighbor_block_on() and not _peak_on and results and len(results) < limit:
            _anchors = [r["id"] for r in results[:NEIGHBOR_ANCHOR_MAX]]
            _nbrs = await _expand_neighbor_blocks(character_id, results, limit - len(results))
            if _nbrs:
                results = results + _nbrs
                if _trace_debug:
                    debug["neighbor_block"] = {"anchors": _anchors, "added": len(_nbrs)}

    # 插件 Hook：memory_search（调整/追加召回记忆；插件返回的 dict 列表追加到结果，原结果让位给插件追加；异常隔离）
    try:
        from app.plugins.registry import run_hook_collect
        _hook_items = await run_hook_collect("memory_search", {
            "character_id": character_id,
            "query": query,
            "results": list(results),
            "limit": limit,
            # A2 M0-4：补调用者（ctx 多一个键不影响任何既有消费方；None=调用点拿不到）
            "user_id": user_id,
        },
            # A2 M4：显式带调用者 → flag 开时只分发给本账号可见插件（拿不到 user_id 时 fail-closed）
            user_id=user_id,
            callsite="memory/retrieve.py:memory_search",
        )
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

    # #72：场景可见性过滤（scene/exclude_sources/group_id 默认 None/空 = 与现状逐字节一致）
    if scene is not None or exclude_sources or group_id is not None:
        results = _scene_filter(results, scene, exclude_sources, group_id)

    # 批 0-2 M1a：flag `perception_source_tag` 开时输出补「来源/子类」两字段（观测与前端标注用）。
    # 只加字段：排序、条数、预算、阈值、是否剔除一律不变（键追加在末尾，旧键顺序逐字节保持）。
    _with_source = _perception_tag_on()
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
            **({"source": r.get("source"), "sub_type": r.get("sub_type")} if _with_source else {}),
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
        elif _dense_has:
            route = "dense"
        else:
            # 批 0-11：双路皆无候选、只有专名路出候选（旧逻辑只会落到 "keyword"，
            # 该分支在 flag 关时到不了 ⇒ 既有 route 取值一个都没变）
            route = "entity"
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
