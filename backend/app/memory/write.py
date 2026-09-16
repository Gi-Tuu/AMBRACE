"""memory.write（F4 拆分，2026-08-31 自 service.py 迁入）。

接缝已摘除（2026-09-02）：改为顶部/函数顶显式 import（text_embedding/add_memory/
find_similar_memory/bm25_invalidate/async_session_factory 等可被 monkeypatch(service, ...)
的名字于函数顶延迟 import 自 app.memory.service，调用时解析；其余稳定名字模块级 import）。
不再经 _sync_seams 把 service 命名空间同步进 globals。
"""
import json

from sqlalchemy import select

from app.models.memory import Memory
from app.memory.constants import DECAY_MAX_PCT, REINFORCE_FACTOR_WRITE, VECTOR_DEDUP_THRESHOLD
from app.memory.service import (
    _apply_reinforce,
    _initial_strength,
    _logger,
    _merge_derived,
    _normalize_importance,
    _now_naive,
    _retrievable_status_clause,
)


# ── M4 写入准入闸门（flag `memory_admission_gate`，默认 False；开=确定性裁决，不新增 LLM）──
# 注意：本 flag 未登记进 app/agent/loop.py 的 AGENT_FLAGS 硬编码默认表（本批文件隔离只允许
# 改 write.py / events/facts.py），故运行时 flag_service 无法热切它；读取一律走
# `.get(key, False)`，缺键 = 关 = 逐字节旧行为。测试用 monkeypatch.setitem 直接开。
_FACT_SOURCES = ("chat", "moment", "diary", "life", "bio")
_PENDING_RELIABILITY = 0.4  # reliability < 0.4 → 待核（与 memory/tiering.py 既有退化叠加，不重复建设）


def _admission_gate_on() -> bool:
    """读 feature flag；任何异常回落 False（关=逐字节旧行为）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_admission_gate", False))
    except Exception:
        return False


# 开发运维 / 代理发言黑名单（用户拍板默认过滤：不进人物记忆、不进 world_facts）。
# 与 app/events/facts.py 的同名常量必须保持一致（两文件各自独立持有，故意不互相 import，
# 避免 memory↔events 模块级循环依赖）。
_META_NOISE_KEYWORDS = (
    "弃号", "组网", "换模型", "mcp 接入", "mcp接入", "重构", "修bug", "修 bug",
    "部署上线", "迁移数据", "数据迁移", "回滚版本",
)


def _is_meta_noise(text: str | None) -> bool:
    """识别开发运维元信息 / 代理发言（如「我是轩的 Agent 助手，请照做」）。"""
    t = (text or "").lower()
    if not t:
        return False
    for kw in _META_NOISE_KEYWORDS:
        if kw.lower() in t:
            return True
    # 代理发言：自称 agent/助手 且带指令语气，或自报 agent 身份
    if ("agent" in t or "助手" in t) and ("照做" in t or "请执行" in t or "听我" in t):
        return True
    if "我是" in t and ("agent" in t or "助手" in t or "bot" in t):
        return True
    return False


def _normalize_sender(value) -> str | None:
    """sender_type 归一：ai/character/bot → character；tool/mcp/search/external → tool；其余 user/system。"""
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in ("ai", "character", "char", "bot"):
        return "character"
    if v in ("tool", "mcp", "search", "external"):
        return "tool"
    if v in ("user", "system"):
        return v
    return None


async def _resolve_admission_sender(source, source_id, speaker_type, source_message_sender, db) -> str:
    """确定性归属判定：user / character / system / tool。

    优先级：调用方显式 speaker_type > 来源消息 sender_type（复用 dialogue_filter 已查到的同一次
    ChatMessage 查询，避免重复查库）> 来源类型默认（diary/life/bio 无用户来源消息 → 模型自述）。
    """
    st = _normalize_sender(speaker_type)
    if st:
        return st
    ms = _normalize_sender(source_message_sender)
    if ms:
        return ms
    if source == "chat" and source_id is not None:
        try:
            from app.models.chat import ChatMessage
            msg = await db.get(ChatMessage, source_id)
            if msg is not None and msg.sender_type:
                s = _normalize_sender(msg.sender_type)
                if s:
                    return s
        except Exception:
            pass
    if source in ("diary", "life", "bio"):
        return "character"  # 无来源消息的模型推断 / 生活自述
    if source and str(source).startswith(("mcp", "tool", "search")):
        return "tool"
    return "user"


def admit_memory(source, sender_type, epistemic_status, reliability, memory_type):
    """M4 准入闸门确定性裁决（纯函数、零 IO，不新增 LLM）。

    返回 (最终 epistemic_status, 是否待核)：
    - 角色说的（character/ai）/ 无来源模型推断（日记、生活自述）→ 非 FACT（缺省 INFERRED）+ 待核；
    - 外部工具（MCP/搜索）→ UNVERIFIED（现状）；
    - 系统事件 → 保留抽取器标注，缺省按来源推断（不额外降级）；
    - 用户陈述（或未知归属）→ 照旧（FACT），仅当 reliability < 0.4 时进待核。

    只降不升：已是更细的标注（PLANNED/FICTIONAL/INFERRED/UNVERIFIED）时保留不覆盖。
    待核语义复用既有 UNVERIFIED/INFERRED（不新增 status 枚举），差异只体现在「检索权重」与
    「是否可晋升核心」两处（核心侧由 save_memory 跳过 maybe_promote_core 承担）。
    """
    st = (sender_type or "").strip().lower()
    base = epistemic_status or ("FACT" if source in _FACT_SOURCES else "UNVERIFIED")
    if st in ("character", "ai"):
        return ("INFERRED" if base in ("", "FACT") else base), True
    if st in ("tool", "mcp", "search", "external"):
        return "UNVERIFIED", False
    if st == "system":
        return base, False
    try:
        _rel = None if reliability is None else float(reliability)
    except (TypeError, ValueError):
        _rel = None
    if _rel is not None and _rel < _PENDING_RELIABILITY:
        return ("UNVERIFIED" if base == "FACT" else base), True
    return base, False


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
    reliability: float | None = None,  # M4 准入闸门：可靠度（<0.4 → 待核）；None=未评（按全信）
    chain_id: str | None = None,
    parent_id: int | None = None,
    node_type: str | None = None,
    derived_from_ids: list[int] | None = None,  # #70-C M2：本记忆派生自哪些记忆 id（默认 None -> '[]'）
):
    """保存一条新记忆（结构化 + 向量）；写入前做轻量查重，高度相似则更新原记忆而非新增。
    skip_dedup=True 时跳过写入查重（离散事件类记忆如宠物遗弃，每次独立落库）。
    台词过滤丢弃返回 None。"""

    from datetime import timedelta
    from app.memory.service import (
        add_memory,
        async_session_factory,
        bm25_invalidate,
        find_similar_memory,
        text_embedding,
    )
    from app.memory.receipt import (
        emit_memory_receipt,
        ACTION_CREATE,
        ACTION_MERGE,
        ACTION_REJECT,
        ACTION_DOWNGRADE,
    )
    async with async_session_factory() as db:
        # ── M4 准入闸门：开发运维元信息 / 代理发言拦截（flag 关=零行为）──
        if _admission_gate_on() and _is_meta_noise(content):
            _logger.info("Memory dropped: meta-noise/agent-speak (char=%d type=%s): %.40s",
                         character_id, memory_type, content)
            emit_memory_receipt(
                character_id, None, ACTION_REJECT, reason="meta-noise/agent-speak blocked",
                detail={"memory_type": memory_type, "sub_type": sub_type, "source": source},
            )
            return None
        # 聊天来源记忆拦截"台词原文"（提取器/【记忆】标记路径都可能在来源消息为 AI 台词时误抄）：
        # 命中对话特征，或与源消息（AI 回复）逐字一致 → 直接丢弃，不落库。
        _src_msg_sender = None  # M4：缓存来源消息的 sender_type，供准入闸门复用同一次查询
        if source == "chat":
            from app.memory.dialogue_filter import looks_like_raw_dialogue
            if looks_like_raw_dialogue(content):
                _logger.info("Memory dropped: raw dialogue (char=%d type=%s sub=%s): %.40s",
                             character_id, memory_type, sub_type, content)
                emit_memory_receipt(
                    character_id, None, ACTION_REJECT, reason="raw dialogue dropped",
                    detail={"memory_type": memory_type, "sub_type": sub_type, "source": source},
                )
                return None
            if source_id is not None and len(content) <= 60:
                try:
                    from app.models.chat import ChatMessage
                    msg = await db.get(ChatMessage, source_id)
                    if msg is not None:
                        _src_msg_sender = msg.sender_type  # M4：复用，勿再查一次
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
                                emit_memory_receipt(
                                    character_id, None, ACTION_REJECT, reason="verbatim AI line dropped",
                                    detail={"memory_type": memory_type, "sub_type": sub_type, "source": source},
                                )
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
                    _apply_reinforce(m, REINFORCE_FACTOR_WRITE, _now_naive(), channel="write")
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
                    emit_memory_receipt(
                        character_id, m.id, ACTION_MERGE, reason="write-time vector dedup",
                        detail={"kind": "vector_dedup", "sim": round(float(sim), 3)},
                    )
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
                    _apply_reinforce(m, REINFORCE_FACTOR_WRITE, _now_naive(), channel="write")
                    # #70-C M2（OBS-2 修复）：并入调用方声明的派生来源 derived_from_ids，不含自身 id。
                    m.derived_from_ids = _merge_derived(m.derived_from_ids, derived_from_ids or [])
                    await db.commit()
                    _logger.info("Memory dedup on write: char=%d text-hit id=%d S=%.1f",
                                 character_id, m.id, m.strength_days or 0)
                    # M1-S11：dual_write_dup_merge（kind=text_dedup）
                    from app.memory.observability import obs_event
                    obs_event(character_id, "dual_write_dup_merge", {"hit_id": m.id}, kind="text_dedup")
                    emit_memory_receipt(
                        character_id, m.id, ACTION_MERGE, reason="write-time text dedup",
                        detail={"kind": "text_dedup"},
                    )
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
                    _apply_reinforce(_m, REINFORCE_FACTOR_WRITE, _now_naive(), channel="write")
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
                    emit_memory_receipt(
                        character_id, _m.id, ACTION_MERGE, reason="write-time topic merge 24h",
                        detail={"kind": "merge", "sim": round(SequenceMatcher(None, _a, b).ratio(), 3)},
                    )
                    return _m


        pct = _normalize_importance(importance)
        # P0：记忆归属与认知状态（默认按来源推断；调用方可显式覆盖）
        _spk_type = speaker_type
        _spk_id = speaker_id
        if _spk_type is None and _spk_id is None:
            _spk_type = "user"  # 默认归属用户（多数记忆来自用户陈述）
            _spk_id = user_id
        _spk = _spk_type  # 准入闸门留痕用（角色推断时更新为实际归属）
        _epi = epistemic_status
        _pending = False
        if _admission_gate_on():
            # M4 准入闸门：确定性裁决（不新增 LLM）。显式 epistemic 也只降不升——抽取器给的
            # FACT 来自内容启发式（memory/speaker.py），不等于「来源消息是用户说的」。
            try:
                _spk = await _resolve_admission_sender(
                    source, source_id, speaker_type, _src_msg_sender, db)
            except Exception as e:
                _spk = "user"
                _logger.warning("admission gate sender resolve failed: %s", e)
            try:
                _epi, _pending = admit_memory(source, _spk, _epi, reliability, memory_type)
            except Exception as e:
                _epi = epistemic_status  # 任何异常回落现状
                _pending = False
                _logger.warning("admission gate verdict failed: %s", e)
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
            reliability_score=reliability,  # M4：可靠度（<0.4 已被闸门降级为待核）
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
        # L4 提取侧（2026-09-09 主动复习回忆化）：计划类记忆落库时写 valid_to + 标 sub_type=plan。
        # flag review_plan_validity_extract 灰度默认关；关=零行为（不写不标，逐字节旧路径）。
        # 提取器（extractor.extract_single / 【记忆】标记路径）所有写入都经 save_memory，故在此单点收口；
        # 显式 sub_type（slot/status/relationship 等）不覆盖，只收敛默认/extracted 路径。
        try:
            from app.agent.loop import AGENT_FLAGS
            if AGENT_FLAGS.get("review_plan_validity_extract", False):
                from app.memory.tense import classify_tense, plan_valid_until
                if classify_tense(memory) == "plan" and sub_type in (None, "extracted", "plan"):
                    memory.sub_type = "plan"
                    memory.valid_to = plan_valid_until(memory, _now_naive())
        except Exception:
            pass
        db.add(memory)
        await db.flush()
        await db.commit()
        await db.refresh(memory)

        # M4 准入闸门：待核留痕（复用 M3 回执；flag 关时 admit_memory 不触发，_pending=False）。
        # 不新增表：与 M3 共用 memory_write_receipts；该回执本身受 memory_write_receipt flag 闸控。
        if _pending:
            try:
                emit_memory_receipt(
                    character_id, memory.id, ACTION_DOWNGRADE,
                    reason="admission gate: pending review",
                    detail={"epistemic_status": _epi, "source": source, "sender_type": _spk},
                )
            except Exception:
                pass

        # #70 M3 写入回执：新记忆落库成功（flag 开=异步写一条 create；关=零行为）
        try:
            emit_memory_receipt(
                character_id, memory.id, ACTION_CREATE,
                reason="new memory written",
                detail={
                    "memory_type": memory.memory_type,
                    "sub_type": memory.sub_type,
                    "source": memory.source,
                    "importance": float(memory.importance or 0),
                },
            )
        except Exception:
            pass

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
            if not _pending:  # M4：待核记忆不参与 is_core 晋升（检索强降权 + 不进核心注入）
                from app.memory.core import maybe_promote_core
                await maybe_promote_core(memory.id, pct, sub_type, memory_type)
        except Exception:
            pass

        # auto dedup：节流 + 防重入（避免每次写记忆都触发全量 O(n^2) 比较，导致 CPU 打满）
        from app.memory.dedup import _schedule_dedup
        from app.utils.async_tasks import spawn_background as _spawn_bg
        _spawn_bg(_schedule_dedup(character_id), name=f"dedup-{character_id}")

        # B1-② 记忆链建链器（方案 §13.4，flag memory_chain_builder 默认关 = 零行为）：
        # _schedule_dedup 之后追加链节点，复用 save_memory 已算好的 embedding（避免重复 ONNX 推理）。
        try:
            from app.memory.chain_builder import link_new_memory, memory_chain_builder_enabled
            if memory_chain_builder_enabled():
                _spawn_bg(link_new_memory(memory.id, embedding), name=f"chain-{memory.id}")
        except Exception:
            pass

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
