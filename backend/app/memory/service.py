"""记忆服务：结构化记忆（SQLite）与向量记忆（ChromaDB）的读写入口"""
import json
import time

from sqlalchemy import select

from app.db.database import async_session_factory
from app.db.vector_store import (
    add_memory,
    search_memories as vector_search,
    delete_memory_vector,
    find_similar_memory,
)
from app.models.memory import Memory
from app.utils.logger import get_logger
from app.memory.embedding import text_embedding
from app.memory.constants import (
    DECAY_MAX_PCT,
    VECTOR_DEDUP_THRESHOLD, S_DEFAULT, S_MIN_DAYS, S_MAX_DAYS,
    REINFORCE_FACTOR_WRITE,
)
from app.memory.decay import retention_pct

_logger = get_logger("memory.service")


def star_from_pct(pct: float) -> int:
    """百分比重要度转 1-5 星：pct/20 取整钳制到 1-5"""
    return max(1, min(5, round((pct or 0.0) / 20.0)))

from app.utils.timeutil import now_naive_utc as _now_naive


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

async def save_memory(
    user_id: int,
    character_id: int,
    memory_type: str,
    content: str,
    title: str = "",
    importance: int = 2,
    sub_type: str | None = None,
    source: str | None = None,
    related_memory_id: int | None = None,
    source_id: int | None = None,
    scope: str = "private",
    skip_dedup: bool = False,
    speaker_id: int | None = None,
    speaker_type: str | None = None,
    epistemic_status: str | None = None,
):
    """保存一条新记忆（结构化 + 向量）；写入前做轻量查重，高度相似则更新原记忆而非新增。
    skip_dedup=True 时跳过写入查重（离散事件类记忆如宠物遗弃，每次独立落库）。
    台词过滤丢弃返回 None。"""

    from datetime import timedelta
    async with async_session_factory() as db:
        # 聊天来源记忆拦截"台词原文"（提取器/【记忆】标记路径都可能在来源消息为 AI 台词时误抄）：
        # 命中对话特征，或与源消息（AI 回复）逐字一致 → 直接丢弃，不落库。
        if source == "chat":
            from app.memory.dialogue_filter import looks_like_raw_dialogue
            if looks_like_raw_dialogue(content):
                _logger.info("Memory dropped: raw dialogue (char=%d type=%s sub=%s): %.40s",
                             character_id, memory_type, sub_type, content)
                return None
            if source_id is not None and len(content) <= 60:
                try:
                    from app.models.chat_message import ChatMessage
                    msg = await db.get(ChatMessage, source_id)
                    if msg is not None:
                        cands = []
                        if msg.sender_type == "ai":
                            cands.append(msg)
                        elif sub_type is None:
                            # 标记路径（【记忆：...】）的 source_id 指向用户消息：
                            # 再取同会话紧随的 AI 回复，防止把 AI 台词/自我介绍当用户信息落库
                            nxt = (await db.execute(
                                select(ChatMessage).where(
                                    ChatMessage.session_id == msg.session_id,
                                    ChatMessage.id > msg.id,
                                    ChatMessage.sender_type == "ai",
                                ).order_by(ChatMessage.id.asc()).limit(1)
                            )).scalar_one_or_none()
                            if nxt is not None:
                                cands.append(nxt)
                        for cand in cands:
                            raw = (cand.content or "").strip().strip("“”「」『』...")
                            c = content.strip().strip("“”「」『』...")
                            if raw and c and (raw == c or raw.startswith(c) or raw.endswith(c)):
                                _logger.info("Memory dropped: verbatim AI line (char=%d type=%s): %.40s",
                                             character_id, memory_type, content)
                                return None
                except Exception:
                    pass
        # 写入前查重：优先向量语义查重（cosine >= 0.9），未命中再字符级兜底（最近 30 条 >= 0.72）
        embedding = None
        if content and content.strip() and not skip_dedup:
            # 1) 向量语义查重：先算嵌入，命中高度相似则更新原记忆而非新增
            try:
                embedding = await text_embedding(content)
                similar = await find_similar_memory(
                    character_id, embedding, limit=20, min_similarity=VECTOR_DEDUP_THRESHOLD
                )
            except Exception:
                embedding = None
                similar = None
            if similar:
                mem_id, sim = similar
                m = await db.get(Memory, mem_id)
                if m and not m.is_archived and not m.is_pinned:
                    # 艾宾浩斯强化：写入查重命中 = 一次复习，S ×2 并刷新遗忘起点
                    new_pct = float(importance) * 20.0
                    if new_pct > float(m.importance or 40.0):
                        m.importance = min(DECAY_MAX_PCT, new_pct)
                    _apply_reinforce(m, REINFORCE_FACTOR_WRITE, _now_naive())
                    await db.commit()
                    _logger.info("Memory dedup on write: char=%d vector-hit id=%d sim=%.3f S=%.1f",
                                 character_id, mem_id, sim, m.strength_days or 0)
                    return m

            # 2) 字符级查重兜底（嵌入失败或旧记忆无向量时仍能命中）
            from difflib import SequenceMatcher
            recent_result = await db.execute(
                select(Memory)
                .where(Memory.character_id == character_id, Memory.is_archived == False)
                .order_by(Memory.created_at.desc())
                .limit(30)
            )
            recent = recent_result.scalars().all()
            b = content.strip()[:80]
            for m in recent:
                a = (m.content or "").strip()[:80]
                if len(a) < 4 or len(b) < 4:
                    continue
                if SequenceMatcher(None, a, b).ratio() >= 0.72:
                    # 艾宾浩斯强化：写入查重命中 = 一次复习，S ×2 并刷新遗忘起点
                    if m.is_pinned:
                        continue
                    new_pct = float(importance) * 20.0
                    if new_pct > float(m.importance or 40.0):
                        m.importance = min(DECAY_MAX_PCT, new_pct)
                    _apply_reinforce(m, REINFORCE_FACTOR_WRITE, _now_naive())
                    await db.commit()
                    _logger.info("Memory dedup on write: char=%d text-hit id=%d S=%.1f",
                                 character_id, m.id, m.strength_days or 0)
                    return m

            # 3) 24h 同主题合并（2026-08-08）：同角色同类型 24h 内、字符相似 >0.6 → 更新原记忆而非新增。
            #    覆盖"同一信息换了说法反复写入"（向量 0.86/字符 0.72 拦不住的近义表述）。
            merge_rows = (await db.execute(
                select(Memory)
                .where(
                    Memory.character_id == character_id,
                    Memory.is_archived == False,
                    Memory.memory_type == memory_type,
                    Memory.created_at >= _now_naive() - timedelta(hours=24),
                )
                .order_by(Memory.created_at.desc())
                .limit(50)
            )).scalars().all()
            for _m in merge_rows:
                if _m.is_pinned or _m.is_locked:
                    continue
                _a = (_m.content or "").strip()[:80]
                if len(_a) < 4 or len(b) < 4:
                    continue
                if SequenceMatcher(None, _a, b).ratio() > 0.6:
                    new_pct = float(importance) * 20.0
                    if new_pct > float(_m.importance or 40.0):
                        _m.importance = min(DECAY_MAX_PCT, new_pct)
                    _apply_reinforce(_m, REINFORCE_FACTOR_WRITE, _now_naive())
                    await db.commit()
                    _logger.info("Memory merge on write: char=%d topic-hit id=%d sim=%.2f",
                                 character_id, _m.id, SequenceMatcher(None, _a, b).ratio())
                    return _m


        pct = float(importance) * 20.0 if importance <= 5 else float(importance)
        # P0：记忆归属与认知状态（默认按来源推断；调用方可显式覆盖）
        _spk_type = speaker_type
        _spk_id = speaker_id
        if _spk_type is None and _spk_id is None:
            _spk_type = "user"  # 默认归属用户（多数记忆来自用户陈述）
            _spk_id = user_id
        _epi = epistemic_status
        if _epi is None:
            _epi = "FACT" if source in ("chat", "moment", "diary", "life", "bio") else "UNVERIFIED"
        memory = Memory(
            user_id=user_id,
            character_id=character_id,
            memory_type=memory_type,
            title=title or None,
            content=content,
            scope=scope,
            importance=pct,
            sub_type=sub_type,
            source=source,
            related_memory_id=related_memory_id,
            source_id=source_id,
            speaker_id=_spk_id,
            speaker_type=_spk_type,
            epistemic_status=_epi,
            decay_base_at=_now_naive(),
            strength_days=_initial_strength(memory_type),
            last_reinforce_at=_now_naive(),
            next_review_at=_now_naive() + timedelta(days=_initial_strength(memory_type)),
        )
        db.add(memory)
        await db.flush()
        await db.commit()
        await db.refresh(memory)

        # 生成向量并存入 ChromaDB（复用查重阶段算好的嵌入，避免重复推理）
        try:
            if embedding is None:
                embedding = await text_embedding(content)
            await add_memory(
                memory_id=memory.id,
                character_id=character_id,
                memory_type=memory_type,
                content=content,
                embedding=embedding,
                importance=importance,
            )
        except Exception as e:
            _logger.warning("向量存储失败: %s", e)

        # P1：核心记忆自动晋升（高重要+多次确认 / 高价值类型 → is_core；失败静默）
        try:
            from app.memory.core import maybe_promote_core
            await maybe_promote_core(memory.id, pct, sub_type, memory_type)
        except Exception:
            pass

        # auto dedup：节流 + 防重入（避免每次写记忆都触发全量 O(n^2) 比较，导致 CPU 打满）
        import asyncio
        from app.memory.dedup import _schedule_dedup
        asyncio.ensure_future(_schedule_dedup(character_id))

        # 记忆架构 v2.1：里程碑记忆（event/relationship 且重要度达标）→ 异步低频意义提炼（开关控制，失败静默）
        try:
            from app.memory.meaning import maybe_extract_meaning
            asyncio.ensure_future(maybe_extract_meaning(
                character_id, user_id, memory.id, memory.memory_type, memory.sub_type,
                memory.content, float(memory.importance or 0),
            ))
        except Exception:
            pass

        # 织库增量补卡（2026-08-12 → 2026-08-14 改走事件总线）：发布 memory.written 事件，
        # 由 events/handlers 订阅者执行（importance≥60 才整理，source=life 进私·织库）
        try:
            from app.events import publish
            from app.events.schema import make_event
            _evt = make_event(
                "memory.written",
                speaker={"type": "system", "id": "memory_pipeline"},
                target={"type": "character", "id": character_id},
                audience=[character_id, user_id],
                provenance={"origin": "system_event"},
                data={
                    "user_id": user_id,
                    "character_id": character_id,
                    "memory_id": memory.id,
                    "memory_type": memory.memory_type,
                    "sub_type": memory.sub_type,
                    "source": source or "",
                    "importance": float(memory.importance or 0),
                },
            )
            publish("memory.written", _evt)
        except Exception:
            pass


        # 插件 Hook：memory_written（记忆写入成功 → 插件副动作；异常隔离，不阻断主链路）
        try:
            from app.plugins.registry import run_hook
            await run_hook("memory_written", {
                "user_id": user_id,
                "character_id": character_id,
                "memory_id": memory.id,
                "memory_type": memory.memory_type,
                "sub_type": memory.sub_type,
                "content": memory.content,
                "importance": float(memory.importance or 0),
                "source": memory.source,
            })
        except Exception:
            pass

        return memory

async def search_memories(
    character_id: int,
    query: str,
    limit: int = 5,
    queries: list[str] | None = None,
    trace_meta: dict | None = None,
) -> list[dict]:
    """检索记忆（认知循环 v2.1 多路召回）：向量优先，兜底关键词。

    多路查询（原 query + 感知派生查询，最多 4 路）各召回后按 id 合并；
    加权排序（importance + 关系/情绪时效 + 置顶 + 多路命中加成）取 top limit。
    """

    _t0 = time.monotonic()
    query_list = [query] + (list(queries or [])[:3])
    results: list[dict] = []
    hit_count: dict[int, int] = {}

    # P1 性能（2026-08-16）：多路召回并发执行（原串行最多 4 倍延迟放大）
    async def _one(q: str) -> list[dict]:
        try:
            embedding = await text_embedding(q)
            return await vector_search(
                character_id=character_id,
                query_embedding=embedding,
                limit=limit * 2,  # 多取一些，按重要性排序后截断
            )
        except Exception:
            return []

    import asyncio as _asyncio
    all_hits = await _asyncio.gather(*[_one(q) for q in query_list])
    for hits in all_hits:
        for r in hits:
            rid = r["id"]
            if rid in hit_count:
                hit_count[rid] += 1
            else:
                hit_count[rid] = 1
                results.append(r)

    _vec_empty = not results
    if not results:
        # 向量检索失败：关键词兜底，importance 倒序
        async with async_session_factory() as db:
            result = await db.execute(
                select(Memory)
                .where(
                    Memory.character_id == character_id,
                    Memory.is_archived == False,
                    Memory.content.like(f"%{query}%"),
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

    if results:
        # B2 检索加权：以 DB 为准补全元数据（向量 meta 的 importance 可能过期），
        # 加分项：置顶恒在前、关系/情绪类近 7 天 +15、状态/剧情来源近 3 天 +10；
        # 60 天以上旧记忆 x0.8 抑制，避免旧记忆重要性虚高盖过近期关系温度。
        # v2.1 加成：被多路查询召回（多路命中）说明与当前话题/情绪更相关，每多一路 +5。
        now = _now_naive()
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(Memory.id.in_([r["id"] for r in results]))
            )).scalars().all()
        meta = {m.id: m for m in rows}
        # 记忆架构 v2.1 Phase 4a：进行中目标话题 / 未完成（follow_up）话题 → 内容重叠加分
        goal_topics: list[str] = []
        unfin_topics: list[str] = []
        try:
            from app.models.conversation_topic import ConversationTopic
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
                    score += 10000  # 置顶恒在前
                if m.sub_type in ("relationship", "emotion") and days <= 7:
                    score += 15
                if m.source in ("state_trigger", "storyline") and days <= 3:
                    score += 10
                if m.why_it_matters:
                    score += 20  # 意义记忆（v2.1）：已提炼"为什么重要"的里程碑记忆优先
                score += _topic_bonus(m.content)
                if days > 60:
                    score *= 0.8
                r["importance"] = base
                r["created_at"] = m.created_at
                r["epistemic_status"] = m.epistemic_status
                r["speaker_id"] = m.speaker_id
                r["speaker_type"] = m.speaker_type
                try:
                    from app.memory.reliability import reliability_score
                    r["reliability_score"] = reliability_score(m)
                except Exception:
                    r["reliability_score"] = None
            score += (hit_count.get(r["id"], 1) - 1) * 5
            r["_score"] = score
        results.sort(key=lambda x: x.get("_score") or 0, reverse=True)
        results = results[:limit]

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
        }
        for r in results
    ]

    # P0-2 记忆检索 Trace（2026-08-16）：只写不读，失败静默，为 Memory Benchmark 提供数据
    try:
        from app.agent.trace import enqueue_task_log
        enqueue_task_log(
            character_id=character_id,
            user_id=(trace_meta or {}).get("user_id"),
            session_id=(trace_meta or {}).get("session_id"),
            task_id=(trace_meta or {}).get("task_id"),
            trigger="memory_search",
            route="keyword" if _vec_empty else "vector",
            steps_json=json.dumps({
                "query": query,
                "queries": len(query_list),
                "hit_ids": [str(i) for i in (r["id"] for r in results)][:5],
                "hit_count": len(results),
            }, ensure_ascii=False),
            latency_ms=int((time.monotonic() - _t0) * 1000),
            status="ok",
        )
    except Exception as _e:
        _logger.warning("Memory search trace failed: %s", _e)

    return _final


async def list_memories(
    user_id: int | None = None,
    character_id: int | None = None,
    memory_type: str | None = None,
    skip: int = 0,
    limit: int = 800,
) -> tuple[list[dict], int]:
    """列出记忆（按 importance 排序）；顺带惰性衰减。返回 (切片结果, 总数)。

    重构说明（2026-08-03）：原实现逐条开 session + commit（N+1，3000 条记忆时
    数十秒），且 _apply_decay 的 commit 使 ORM 属性过期、session 关闭后再访问
    抛 DetachedInstanceError（GET /api/v1/memories 500）。改为：单 session 内
    snapshot 全部字段，衰减批量落库（一次 commit），session 外只操作快照。
    """
    from datetime import datetime

    now = _now_naive()
    async with async_session_factory() as db:
        query = select(Memory).where(Memory.is_archived == False)
        if user_id is not None:
            # 严格按用户隔离（置顶摘要写入时 user_id 已为角色拥有者）
            query = query.where(Memory.user_id == user_id)
        if character_id:
            query = query.where(Memory.character_id == character_id)
        if memory_type:
            query = query.where(Memory.memory_type == memory_type)

        result = await db.execute(query)
        memories = result.scalars().all()

        # snapshot 字段（session 内取值，之后不再碰 ORM 对象，避免 detached 访问）
        rows = [{
            "id": m.id, "user_id": m.user_id, "character_id": m.character_id,
            "memory_type": m.memory_type, "sub_type": m.sub_type,
            "source": m.source, "source_id": m.source_id,
            "title": m.title, "content": m.content,
            "importance": float(m.importance or 0),
            "is_pinned": bool(m.is_pinned),
            "is_locked": bool(m.is_locked),
            "is_archived": bool(m.is_archived),
            "delete_at": m.delete_at, "created_at": m.created_at,
            "updated_at": m.updated_at, "decay_base_at": m.decay_base_at,
            "why_it_matters": m.why_it_matters,
            "strength_days": float(m.strength_days or S_DEFAULT),
            "last_reinforce_at": m.last_reinforce_at,
            "_obj": m,
        } for m in memories]

        # 惰性衰减展示（艾宾浩斯）：读列表只计算实时保留率用于展示，不写库、不刷新遗忘起点
        #（真正的结算+刷新起点由 6h 定时 run_memory_decay 负责；读列表刷新起点会让遗忘停滞）
        # 2026-08-08 修复：原实现读一次列表=全部记忆轻复习，97% 记忆 last_reinforce_at 恒为近 1 天，
        # 遗忘曲线几乎不可感知；展示值实时按 R=exp(-Δt/S) 计算，仅到期删除落库。
        removed_ids = set()
        for row in rows:
            if row["is_pinned"] or row["is_locked"]:
                continue
            obj = row["_obj"]
            if row["delete_at"] is not None:
                if now >= row["delete_at"]:
                    removed_ids.add(row["id"])
                    await db.delete(obj)
                continue
            base = row["last_reinforce_at"] or row["decay_base_at"] or row["created_at"]
            if base is None:
                continue
            if isinstance(base, datetime) and base.tzinfo is not None:
                base = base.replace(tzinfo=None)
            dt_days = (now - base).total_seconds() / 86400.0
            if dt_days <= 0:
                continue
            pct = retention_pct(dt_days, row["strength_days"])
            row["importance"] = pct
        if removed_ids:
            await db.commit()

    # 过期删除的同步清理向量（避免孤儿向量）
    if removed_ids:
        from app.db.vector_store import delete_memory_vector
        for mid in removed_ids:
            try:
                await delete_memory_vector(mid)
            except Exception:
                pass

    alive = [r for r in rows if r["id"] not in removed_ids]
    alive.sort(key=lambda r: (r["is_pinned"], star_from_pct(r["importance"]), r["created_at"] or now), reverse=True)
    total = len(alive)
    if limit:
        alive = alive[skip:skip + limit]
    else:
        alive = alive[skip:]
    _logger.debug("List memories: char=%s type=%s count=%d total=%d", character_id, memory_type, len(alive), total)
    return [
        {
            "id": r["id"], "user_id": r["user_id"], "character_id": r["character_id"],
            "memory_type": r["memory_type"], "sub_type": r["sub_type"],
            "source": r["source"], "source_id": r["source_id"],
            "title": r["title"], "content": r["content"],
            "importance": star_from_pct(r["importance"]),
            "importance_pct": round(r["importance"], 1),
            "delete_at": r["delete_at"].isoformat() if r["delete_at"] else None,
            "why_it_matters": r.get("why_it_matters"),
            "is_archived": r["is_archived"],
            "is_pinned": r["is_pinned"],
            "is_locked": r["is_locked"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
        for r in alive
    ], total

async def delete_memory(memory_id: int) -> bool:
    """删除记忆（软删除 + 删除向量）"""
    async with async_session_factory() as db:
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if memory:
            memory.is_archived = True
            # 织库（2026-08-12）：参与记忆被删 → 关联卡片置脏，generate 时懒重建
            try:
                from app.models.weave_card import WeaveCard, WeaveCardMemory
                from sqlalchemy import update
                await db.execute(
                    update(WeaveCard)
                    .where(
                        WeaveCard.id.in_(
                            select(WeaveCardMemory.card_id).where(WeaveCardMemory.memory_id == memory_id)
                        )
                    )
                    .values(is_stale=True)
                )
            except Exception:
                pass
            await db.flush()
            await db.commit()
            try:
                await delete_memory_vector(memory_id)
            except Exception:
                pass
            return True
        return False
