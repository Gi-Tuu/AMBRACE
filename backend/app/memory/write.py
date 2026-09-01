"""memory.write（F4 拆分，2026-08-31 自 service.py 迁入）。

接缝机制：本模块不静态 import service（防循环）；垫片调用 _sync_seams() 把
app.memory.service 的命名空间同步进本模块 globals——搬入函数体内裸名字照常解析，
且 monkeypatch(app.memory.service, "X") 在调用时生效（与拆分前语义一致）。
"""
import sys as _sys

_OWN = frozenset()


def _sync_seams(_src="app.memory.service") -> None:
    m = _sys.modules.get(_src)
    if m is None:
        return
    own = _OWN
    g = globals()
    for name, val in vars(m).items():
        if name.startswith("__") or name in own:
            continue
        g[name] = val


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
    group_id: int | None = None,  # 群聊归属（P3-3 按群节流；None=非群聊/旧数据）
    scope: str = "private",
    skip_dedup: bool = False,
    speaker_id: int | None = None,
    speaker_type: str | None = None,
    epistemic_status: str | None = None,
    chain_id: str | None = None,
    parent_id: int | None = None,
    node_type: str | None = None,
    derived_from_ids: list[int] | None = None,  # #70-C M2：本记忆派生自哪些记忆 id（默认 None -> '[]'）
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
                if m and not m.is_archived and not m.is_pinned and not m.is_locked:
                    # 艾宾浩斯强化：写入查重命中 = 一次复习，S ×2 并刷新遗忘起点
                    new_pct = _normalize_importance(importance)
                    if new_pct > float(m.importance or 40.0):
                        m.importance = min(DECAY_MAX_PCT, new_pct)
                    _apply_reinforce(m, REINFORCE_FACTOR_WRITE, _now_naive())
                    # #70-C M2（OBS-2 修复）：并入调用方声明的派生来源 derived_from_ids，
                    # 不含自身 id（去掉「∪ 自身 id」自环噪声）。
                    m.derived_from_ids = _merge_derived(m.derived_from_ids, derived_from_ids or [])
                    await db.commit()
                    _logger.info("Memory dedup on write: char=%d vector-hit id=%d sim=%.3f S=%.1f",
                                 character_id, mem_id, sim, m.strength_days or 0)
                    # M1-S11：dual_write_dup_merge（kind=vector_dedup）
                    from app.memory.observability import obs_event
                    obs_event(character_id, "dual_write_dup_merge",
                              {"hit_id": mem_id, "sim": round(float(sim), 3)}, kind="vector_dedup")
                    return m

            # 2) 字符级查重兜底（嵌入失败或旧记忆无向量时仍能命中）
            from difflib import SequenceMatcher
            recent_result = await db.execute(
                select(Memory)
                .where(Memory.character_id == character_id, Memory.is_archived == False, _retrievable_status_clause())
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
                    if m.is_pinned or m.is_locked:
                        continue
                    new_pct = _normalize_importance(importance)
                    if new_pct > float(m.importance or 40.0):
                        m.importance = min(DECAY_MAX_PCT, new_pct)
                    _apply_reinforce(m, REINFORCE_FACTOR_WRITE, _now_naive())
                    # #70-C M2（OBS-2 修复）：并入调用方声明的派生来源 derived_from_ids，不含自身 id。
                    m.derived_from_ids = _merge_derived(m.derived_from_ids, derived_from_ids or [])
                    await db.commit()
                    _logger.info("Memory dedup on write: char=%d text-hit id=%d S=%.1f",
                                 character_id, m.id, m.strength_days or 0)
                    # M1-S11：dual_write_dup_merge（kind=text_dedup）
                    from app.memory.observability import obs_event
                    obs_event(character_id, "dual_write_dup_merge", {"hit_id": m.id}, kind="text_dedup")
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
                    _retrievable_status_clause(),   # #70-C
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
                    new_pct = _normalize_importance(importance)
                    if new_pct > float(_m.importance or 40.0):
                        _m.importance = min(DECAY_MAX_PCT, new_pct)
                    _apply_reinforce(_m, REINFORCE_FACTOR_WRITE, _now_naive())
                    # #70-C M2（OBS-2 修复）：并入调用方声明的派生来源 derived_from_ids，不含自身 id。
                    _m.derived_from_ids = _merge_derived(_m.derived_from_ids, derived_from_ids or [])
                    await db.commit()
                    _logger.info("Memory merge on write: char=%d topic-hit id=%d sim=%.2f",
                                 character_id, _m.id, SequenceMatcher(None, _a, b).ratio())
                    # M1-S11：dual_write_dup_merge（kind=merge）
                    from app.memory.observability import obs_event
                    obs_event(character_id, "dual_write_dup_merge",
                              {"hit_id": _m.id, "sim": round(SequenceMatcher(None, _a, b).ratio(), 3)},
                              kind="merge")
                    return _m


        pct = _normalize_importance(importance)
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
            group_id=group_id,
            chain_id=chain_id,
            parent_id=parent_id,
            node_type=node_type,
            speaker_id=_spk_id,
            speaker_type=_spk_type,
            epistemic_status=_epi,
            decay_base_at=_now_naive(),
            strength_days=_initial_strength(memory_type),
            last_reinforce_at=_now_naive(),
            next_review_at=_now_naive() + timedelta(days=_initial_strength(memory_type)),
            derived_from_ids=json.dumps(list(derived_from_ids or []), ensure_ascii=False, default=str),
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
        from app.memory.dedup import _schedule_dedup
        from app.utils.async_tasks import spawn_background as _spawn_bg
        _spawn_bg(_schedule_dedup(character_id), name=f"dedup-{character_id}")

        # 记忆架构 v2.1：里程碑记忆（event/relationship 且重要度达标）→ 异步低频意义提炼（开关控制，失败静默）
        try:
            from app.memory.meaning import maybe_extract_meaning
            _spawn_bg(
                maybe_extract_meaning(
                    character_id, user_id, memory.id, memory.memory_type, memory.sub_type,
                    memory.content, float(memory.importance or 0),
                ),
                name=f"meaning-{memory.id}",
            )
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

        # 检索增强（2026-08-23）：记忆已写入该角色 → 使 BM25 索引失效（下次检索懒重建）
        bm25_invalidate(character_id)
        return memory


_OWN = frozenset(globals().keys())
