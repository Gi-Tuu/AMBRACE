"""记忆管理 API"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import distinct, select

from app.utils.logger import get_logger
from app.schemas.memory import MemoryResponse, MemoryListResponse
from app.memory import list_memories
from app.memory.sources import memory_source_meta
from app.auth.deps import get_current_user_id
from app.application.tenant_service import tenant_scope_ids
from app.i18n import tr_lang
# P3-12（2026-09-17）：async_session_factory / Memory / AICharacter / purge_memory 原在多个函数内重复
# import，统一上提。其中 session 工厂经**模块对象引用**（db_mod.async_session_factory）调用而非早绑定
# 函数名——测试用 monkeypatch ``app.db.database.async_session_factory`` 注入临时库，依赖「调用时取属性」
# 的 late binding；直接 from ... import 会让 patch 失效（实测让本文件 API 用例整片 404）。
from app.db import database as db_mod
from app.models.memory import Memory
from app.models.character import AICharacter
from app.memory.supersede import purge_memory  # 原在 2 个端点内重复 import，上提

router = APIRouter(prefix="/api/v1/memories", tags=["Memories"])
_logger = get_logger("api.memories")


@router.get("", response_model=MemoryListResponse)
async def get_memories(
    character_id: int | None = None,
    memory_type: str | None = None,
    skip: int = 0,
    limit: int = 800,
    user_id: int = Depends(get_current_user_id),
):
    """获取记忆列表（账号独立 P1：按本账号租户白名单过滤——家庭内共享、跨家庭隔离）"""
    _logger.debug("List memories: character_id=%s type=%s skip=%d limit=%d", character_id, memory_type, skip, limit)
    async with db_mod.async_session_factory() as _db:
        scope_ids = await tenant_scope_ids(_db, user_id)
    memories, total = await list_memories(user_ids=scope_ids,
        character_id=character_id,
        memory_type=memory_type,
        skip=skip,
        limit=limit,
    )
    _logger.debug("List memories result: count=%d total=%d", len(memories), total)
    decorated = []
    for m in memories:
        meta = memory_source_meta(m.get("source"), m.get("sub_type"))
        decorated.append({**m, "source_label": meta["label"], "source_icon": meta["icon"]})
    return MemoryListResponse(
        memories=[MemoryResponse(**m) for m in decorated],
        total=total,
    )


@router.get("/stats/perception")
async def get_perception_stats(
    user_id: int = Depends(get_current_user_id),
):
    """感知记忆只读统计（批 0-2 / M1b，方案 §四-4）：观测打标量与「进画像层」占比。

    口径：当前账号可见范围（``tenant_scope_ids``），不剔归档/不剔状态——纯计数、只 SELECT 不写库。
    空库返回全 0（不报错）。``*_ratio`` 分母为 0 时同样返回 0。
    """
    from sqlalchemy import func

    from app.memory.perception_tier import FACT_STATUS, PERCEPTION_SOURCE

    async with db_mod.async_session_factory() as db:
        scope_ids = await tenant_scope_ids(db, user_id)

        async def _count(*conds) -> int:
            if not scope_ids:
                return 0
            stmt = select(func.count(Memory.id)).where(Memory.user_id.in_(scope_ids), *conds)
            return int((await db.execute(stmt)).scalar() or 0)

        total = await _count()
        p_total = await _count(Memory.source == PERCEPTION_SOURCE)
        p_accepted = await _count(Memory.source == PERCEPTION_SOURCE,
                                  Memory.epistemic_status == FACT_STATUS)
        core_total = await _count(Memory.is_core.is_(True))
        core_perception = await _count(Memory.is_core.is_(True),
                                       Memory.source == PERCEPTION_SOURCE)
    return {
        "perception_total": p_total,
        "perception_accepted": p_accepted,
        "perception_ratio": round(p_total / total, 4) if total else 0,
        "core_perception": core_perception,
        "core_total": core_total,
        "core_perception_ratio": round(core_perception / core_total, 4) if core_total else 0,
    }


# ── 批 0-14「查」（2026-09-28）：按需访问三操作的最小子集——只做「查」这一步 ──
# 硬约束落地：①零 LLM（走 app/memory/retrieve.py 既有检索链，本处不新增任何召回/打分逻辑）
# ②条数可传但硬顶 ③越权 404 / 未命中空数组 ④只读（不动写入侧、不改 memories 行、不写回执）。
LOOKUP_LIMIT_DEFAULT = 5
LOOKUP_LIMIT_MAX = 20    # 硬顶：调用方传更大也只返回这么多（防把整库记忆一次拖出上下文）
LOOKUP_SNIPPET_MAX = 200  # 原文片段上限；取全文走既有 GET /{memory_id}


def _lookup_merge(per_char_hits: list[tuple[int, list[dict]]], cap: int) -> list[tuple[int, dict]]:
    """跨角色合并（纯函数）：按「各角色内部名次」轮转取，截断到 cap，附带 character_id。

    检索链是角色级的（``search_memories`` 只吃一个 ``character_id``），对外只给排好序的行，
    **分数不外露**（``_rerank`` 的临时 ``_score`` 在出口前已被清掉）⇒ 跨角色唯一可比的信号是名次。
    轮转保证「每个角色的头名先进来」，不让单一角色凭重要度独占整轮结果。
    """
    out: list[tuple[int, dict]] = []
    seen: set = set()
    rank = 0
    while len(out) < cap:
        added = False
        for cid, hits in per_char_hits:
            if rank >= len(hits):
                continue
            row = hits[rank]
            if row.get("id") in seen:
                continue
            seen.add(row.get("id"))
            out.append((cid, row))
            added = True
            if len(out) >= cap:
                break
        if not added:
            break
        rank += 1
    return out


def _lookup_item(character_id: int, row: dict) -> dict:
    """出口形状（纯函数）：带命中原文片段与时间；不臆造检索链里没有的字段。"""
    content = str(row.get("content") or "")
    created = row.get("created_at")
    if created is None:
        created_at = None
    elif hasattr(created, "isoformat"):
        created_at = created.isoformat()   # 库内 naive UTC（北京时间 = UTC+8）
    else:
        created_at = str(created)
    return {
        "id": row.get("id"),
        "character_id": character_id,
        "memory_type": row.get("type"),
        "snippet": content[:LOOKUP_SNIPPET_MAX],
        "truncated": len(content) > LOOKUP_SNIPPET_MAX,
        "created_at": created_at,
        "importance": float(row.get("importance") or 0),
    }


@router.get("/lookup")
async def lookup_memories(
    query: str,
    character_id: int | None = None,
    limit: int = LOOKUP_LIMIT_DEFAULT,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """按需「查」记忆（批 0-14 三操作中的最小子集，2026-09-28）：只读、零 LLM、复用既有检索链。

    端点契约::

        GET /api/v1/memories/lookup?query=<自然语言问句或关键词>
                                          &character_id=<可选，限定单个角色>
                                          &limit=<可选，默认 5；正数硬顶 20；<=0 视作未传回落默认值>
        200 → {"status": "ok", "query": str, "limit": int（生效值）, "characters_searched": int,
               "total": int,
               "memories": [{"id": int, "character_id": int, "memory_type": str,
                             "snippet": str（命中原文，最多 200 字）, "truncated": bool,
                             "created_at": str|null（ISO，库内 naive UTC）, "importance": float}]}
        400 → query 为空/全空白
        404 → character_id 不属于本账号可见租户（与「不存在」同观感，防探测）
        未命中 → 200 + memories 空数组（不报错）

    口径：
    - 可见范围＝本账号租户白名单（``tenant_scope_ids``：家庭内共享、跨家庭隔离），与记忆列表同源；
      不传 ``character_id`` 时，对该范围内**有记忆的每个角色**各跑一次检索再按名次轮转合并。
    - 检索完全复用 ``app.memory.retrieve.search_memories``（向量 + BM25 + 专名三路 → RRF →
      ``_rerank`` 加权 → 类型均衡），本端点不改排序、不改阈值、不新增召回路。
    - **本期不做**：「合成」（把多条记忆归并成结论）与「回放原始轮次」（由记忆取回当轮对话原文）。
    """
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty"))
    # 硬顶夹取：正数一律夹进 [1, LOOKUP_LIMIT_MAX]；<=0 视作没传回落默认值；返回值给生效值而非请求值
    eff_limit = LOOKUP_LIMIT_DEFAULT if limit <= 0 else min(int(limit), LOOKUP_LIMIT_MAX)
    # 延迟 import：检索链在调用时解析属性，测试可 monkeypatch app.memory.retrieve.search_memories
    from app.memory.retrieve import search_memories

    async with db_mod.async_session_factory() as db:
        scope_ids = await tenant_scope_ids(db, user_id)
        if character_id is not None:
            owned = (await db.execute(
                select(AICharacter.id).where(
                    AICharacter.id == character_id,
                    AICharacter.user_id.in_(scope_ids),
                )
            )).scalar_one_or_none()
            if owned is None:
                raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
            char_ids = [character_id]
        elif not scope_ids:
            char_ids = []
        else:
            # 只枚举本范围内「真有记忆」的角色，避免为空角色白跑一次向量检索
            char_ids = sorted({r for r in (await db.execute(
                select(distinct(Memory.character_id)).where(Memory.user_id.in_(scope_ids))
            )).scalars().all() if r is not None})

    async def _one(cid: int) -> list[dict]:
        """单角色检索（异常隔离：某个角色的检索链故障不该让整次查询 500）。"""
        try:
            return await search_memories(character_id=cid, query=q, limit=eff_limit, user_id=user_id)
        except Exception as e:
            _logger.warning("Memory lookup failed char=%s: %s", cid, e)
            return []

    per_char_hits = await asyncio.gather(*[_one(c) for c in char_ids]) if char_ids else []
    merged = _lookup_merge(list(zip(char_ids, per_char_hits)), eff_limit)
    items = [_lookup_item(cid, row) for cid, row in merged]
    _logger.debug("Memory lookup: user=%s chars=%d query=%.30s -> %d", user_id, len(char_ids), q, len(items))
    return {
        "status": "ok",
        "query": q,
        "limit": eff_limit,
        "characters_searched": len(char_ids),
        "total": len(items),
        "memories": items,
    }


async def _get_owned_memory(memory_id: int, user_id: int):
    """按租户归属获取记忆（账号独立 P1：跨家庭 → None → 404；置顶摘要为角色级归属）"""
    async with db_mod.async_session_factory() as db:
        result = await db.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id.in_(await tenant_scope_ids(db, user_id)),
            )
        )
        mem = result.scalar_one_or_none()
    return mem


@router.get("/{memory_id}")
async def get_memory(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取单条记忆详情"""
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    async with db_mod.async_session_factory() as db:
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        mem = result.scalar_one_or_none()
        meta = memory_source_meta(mem.source, mem.sub_type)
        from app.memory import star_from_pct
        return MemoryResponse(
            id=mem.id, user_id=mem.user_id, character_id=mem.character_id,
            memory_type=mem.memory_type, sub_type=mem.sub_type,
            source=mem.source, source_id=mem.source_id,
            source_label=meta["label"], source_icon=meta["icon"],
            epistemic_status=mem.epistemic_status,
            speaker_type=mem.speaker_type, speaker_id=mem.speaker_id,
            title=mem.title, content=mem.content,
            importance=star_from_pct(mem.importance),
            importance_pct=round(float(mem.importance or 0), 1),
            strength_days=float(mem.strength_days or 7.0),
            last_reinforce_at=mem.last_reinforce_at,
            next_review_at=mem.next_review_at,
            review_count=int(mem.review_count or 0),
            is_archived=mem.is_archived, is_pinned=mem.is_pinned, is_locked=mem.is_locked,
            why_it_matters=mem.why_it_matters,
            created_at=mem.created_at, updated_at=mem.updated_at,
        )


@router.get("/{memory_id}/chain")
async def get_memory_chain(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """记忆链条（B1-② §13.6，2026-09-04）：只读返回该记忆所在链的时间线（root→branch…，时间升序）。

    当前节点标 ``is_current=true``；归属校验沿用 ``_get_owned_memory``（404 防越权）；
    链为空（未建链/孤立点）返回仅自身。与 ``DELETE /{memory_id}/tree`` 的级联语义一致（都按 parent_id/chain_id）。
    """
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    from app.memory.chain_builder import get_chain_nodes
    nodes = await get_chain_nodes(memory_id)
    async with db_mod.async_session_factory() as _db:
        scope_ids = await tenant_scope_ids(_db, user_id)
    return {"status": "ok", "chain": [
        {
            "id": x.id,
            "content": x.content,
            "memory_type": x.memory_type,
            "sub_type": x.sub_type,
            "node_type": x.node_type,
            "parent_id": x.parent_id,
            "chain_id": x.chain_id,
            "created_at": str(x.created_at)[:19] if x.created_at else None,
            "is_current": x.id == memory_id,
            "is_archived": x.is_archived,
        }
        for x in nodes
        if x.user_id in scope_ids
    ]}


@router.get("/{memory_id}/history")
async def get_memory_fact_history(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """事实「修正历史」只读（批 0-6，2026-09-28）：沿既有取代链回溯该事实的「旧值 → 新值」时间线。

    零新表、零写、不改任何写入侧裁决；数据只来自 memories.superseded_by/valid_to 与
    memory_write_receipts（原因）。取不到的字段明确返回 null 而非编造：actor（取代链与回执
    都不记操作者）、reason（回执 flag 关时无留痕）。无历史 → corrections 为空数组。
    归属校验沿用 _get_owned_memory（跨家庭 → 404，与 /{memory_id}/chain 同口径）。
    """
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    from app.memory.facts import build_fact_history
    async with db_mod.async_session_factory() as db:
        scope_ids = await tenant_scope_ids(db, user_id)
        history = await build_fact_history(db, memory_id, scope_ids)
    if history is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    return {"status": "ok", "memory_id": memory_id, **history}


@router.patch("/{memory_id}/content")
async def update_memory_content(
    memory_id: int,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """改内容：重算向量并覆盖/替换 + version+=1（保留 created_at，updated_at 自动刷新）"""
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    content = (data.get("content") or "")
    if not content.strip():
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty"))
    async with db_mod.async_session_factory() as db:
        mem = (await db.execute(
            select(Memory).where(Memory.id == memory_id, Memory.user_id.in_(await tenant_scope_ids(db, user_id)))
        )).scalar_one_or_none()
        if not mem:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
        mem.content = content
        mem.version = (mem.version or 0) + 1
        await db.commit()
        await db.refresh(mem)
        # 重算向量并覆盖/替换（复用现有 embedding 写/删逻辑；失败不阻塞内容更新）
        try:
            from app.memory.embedding import text_embedding
            from app.db.vector_store import upsert_memory_vector
            embedding = await text_embedding(content)
            await upsert_memory_vector(
                memory_id=mem.id,
                character_id=mem.character_id,
                memory_type=mem.memory_type,
                content=content,
                embedding=embedding,
                importance=int(mem.importance or 0),
            )
        except Exception as e:
            _logger.warning("Memory content update vector refresh failed id=%d: %s", memory_id, e)
        # 检索增强（2026-08-23）：内容已改写 → 使该角色 BM25 索引失效（下次检索懒重建）
        try:
            from app.memory.bm25_index import invalidate as _bm25_invalidate
            _bm25_invalidate(mem.character_id)
        except Exception:
            pass
    return {"status": "ok", "version": int(mem.version or 0)}


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: int,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """更新记忆（重要性等）"""
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    accepted = False
    character_id = None
    async with db_mod.async_session_factory() as db:
        result = await db.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id.in_(await tenant_scope_ids(db, user_id))))
        mem = result.scalar_one_or_none()
        if not mem:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
        if "importance" in data:
            try:
                star = max(1, min(5, int(data["importance"])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=tr_lang(lang, "invalid_importance"))
            from app.memory import _now_naive
            from app.memory.constants import S_MAX_DAYS, S_MIN_DAYS
            mem.importance = float(star * 20)  # 手动评星：importance 直设（最高 100%）
            # 艾宾浩斯：手动评星视为强复习，S 拉高（星级越高越稳）并刷新遗忘起点、取消删除倒计时
            s = float(mem.strength_days or 7.0)
            mem.strength_days = min(S_MAX_DAYS, max(S_MIN_DAYS, max(s, star / 5.0 * S_MAX_DAYS)))
            mem.review_count = (mem.review_count or 0) + 1
            mem.last_reinforce_at = _now_naive()
            mem.delete_at = None
        if "is_archived" in data:
            mem.is_archived = bool(data["is_archived"])
        if "is_pinned" in data:
            mem.is_pinned = bool(data["is_pinned"])
            if mem.is_pinned:
                mem.delete_at = None
        if "is_locked" in data:
            # 手动记忆锁（P2）：冻结强度与重要性，不衰减/不删除/不强化；解锁后按当前 S 继续衰减
            mem.is_locked = bool(data["is_locked"])
            if mem.is_locked:
                mem.delete_at = None
        if "epistemic_status" in data:
            # 批 0-2 / M1b「认可」：只允许把**感知派生条**认可为 FACT（方案 §2.3）。
            # 来源不改——"这条来自手机观察"的证据永久保留；撤回走上面的 is_archived 分支。
            from app.memory.perception_tier import FACT_STATUS, PERCEPTION_SOURCE

            want = str(data["epistemic_status"] or "").strip().upper()
            if want != FACT_STATUS:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "perception_status_value_invalid"))
            if (mem.source or "").strip().lower() != PERCEPTION_SOURCE:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "perception_status_not_perception"))
            mem.epistemic_status = FACT_STATUS
            mem.confirmation_count = int(mem.confirmation_count or 0) + 1
            character_id = mem.character_id
            accepted = True
        await db.commit()
    if accepted:
        # 审计留痕（flag 关时 emit 内部直接 return，零写入）
        from app.memory.receipt import ACTION_UPDATE, emit_memory_receipt

        emit_memory_receipt(character_id, memory_id, ACTION_UPDATE,
                            reason="perception accepted", detail={"epistemic_status": "FACT"})
    return {"status": "ok"}


@router.post("/deduplicate/{character_id}")
async def deduplicate(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """对该角色去重记忆"""
    async with db_mod.async_session_factory() as db:
        cresult = await db.execute(select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id.in_(await tenant_scope_ids(db, user_id))))
        if cresult.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    from app.memory import deduplicate_memories
    deleted = await deduplicate_memories(character_id)
    return {"deleted": deleted}

@router.post("/{character_id}/summarize")
async def summarize_character_memories(
    character_id: int,
    memory_type: str,
    force: bool = False,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """生成/刷新角色记忆置顶摘要（6 小时节流）"""
    async with db_mod.async_session_factory() as db:
        cresult = await db.execute(select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id.in_(await tenant_scope_ids(db, user_id))))
        if cresult.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    from app.memory import summarize_memories
    return await summarize_memories(character_id, memory_type, force=force)



@router.delete("/{memory_id}/tree")
async def remove_memory_tree(
    memory_id: int,
    cascade: bool = False,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """记忆链条级联软删：cascade=false 仅返回直接子列表供前端二次确认；cascade=true 对根+子逐个软删。"""
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    async with db_mod.async_session_factory() as db:
        rows = (await db.execute(
            select(Memory).where(Memory.parent_id == memory_id, Memory.user_id.in_(await tenant_scope_ids(db, user_id)))
        )).scalars().all()
    if not cascade:
        return {
            "status": "ok",
            "cascade": False,
            "children": [
                {
                    "id": c.id,
                    "title": c.title,
                    "content": c.content,
                    "memory_type": c.memory_type,
                    "node_type": c.node_type,
                    "chain_id": c.chain_id,
                    "is_archived": c.is_archived,
                }
                for c in rows
            ],
        }
    deleted = 0
    for mid in [memory_id] + [c.id for c in rows]:
        try:
            if await purge_memory(mid):
                deleted += 1
        except Exception as e:
            _logger.warning("Memory tree purge failed id=%d: %s", mid, e)
    return {"status": "ok", "cascade": True, "deleted": deleted}


@router.delete("/{memory_id}")
async def remove_memory(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除记忆（#70-C2：purge 后门——连冷归档 memory_archive 一起物理删，不光软删）。

    边界（R-7 记录）：若记忆已被日终 archive_cold_superseded 冷归档（热行已迁走、仅 memory_archive
    保留），此处归属校验查热表会 404，无法经单删 API 清归档；冷归档属长期留存历史，清归档走管理工具。"""
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    deleted = await purge_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    return {"status": "ok", "deleted": True}


@router.post("/{memory_id}/supersede")
async def supersede_memory_api(
    memory_id: int,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """#70-C（M1）：把记忆标记为被新记忆取代（管理/调试入口，归属校验，只允许明确指定）。

    body: {"new_id": int|None, "reason": str}；默认 new_id=None（仅失效无替代）。
    不做自动臆测取代；本接口仅在调试/明确改口时手动触发。
    """
    from app.memory.supersede import supersede_memory
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    new_id = data.get("new_id")
    if new_id is not None:
        try:
            new_id = int(new_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=tr_lang(lang, "invalid_memory_id"))
    reason = str(data.get("reason") or "")
    ok = await supersede_memory(memory_id, new_id=new_id, reason=reason)
    if not ok:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "supersede_failed"))
    return {"status": "ok", "superseded": True, "memory_id": memory_id, "new_id": new_id}


@router.post("/{memory_id}/restore")
async def restore_memory_api(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """#70-C（M1）：回滚一条 supersede/stale（调试/误判纠正用），status 回 active、清 valid_to。"""
    from app.memory.supersede import restore_memory
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    ok = await restore_memory(memory_id)
    if not ok:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "restore_failed"))
    return {"status": "ok", "restored": True, "memory_id": memory_id}
