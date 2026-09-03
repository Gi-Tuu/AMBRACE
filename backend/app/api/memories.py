"""记忆管理 API"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select

from app.utils.logger import get_logger
from app.schemas.memory import MemoryResponse, MemoryListResponse
from app.memory import list_memories
from app.memory.sources import memory_source_meta
from app.auth.deps import get_current_user_id
from app.i18n import tr_lang

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
    """获取记忆列表"""
    _logger.debug("List memories: character_id=%s type=%s skip=%d limit=%d", character_id, memory_type, skip, limit)
    memories, total = await list_memories(user_id=user_id,
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


async def _get_owned_memory(memory_id: int, user_id: int):
    """按归属获取记忆（用户本人 + 本人角色的记忆；置顶摘要为角色级归属）"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.memory import Memory
    async with async_session_factory() as db:
        result = await db.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
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
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.memory import Memory
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    async with async_session_factory() as db:
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        mem = result.scalar_one_or_none()
        meta = memory_source_meta(mem.source, mem.sub_type)
        from app.memory import star_from_pct
        return MemoryResponse(
            id=mem.id, user_id=mem.user_id, character_id=mem.character_id,
            memory_type=mem.memory_type, sub_type=mem.sub_type,
            source=mem.source, source_id=mem.source_id,
            source_label=meta["label"], source_icon=meta["icon"],
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
        if x.user_id == user_id
    ]}


@router.patch("/{memory_id}/content")
async def update_memory_content(
    memory_id: int,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """改内容：重算向量并覆盖/替换 + version+=1（保留 created_at，updated_at 自动刷新）"""
    from app.db.database import async_session_factory
    from app.models.memory import Memory
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    content = (data.get("content") or "")
    if not content.strip():
        raise HTTPException(status_code=400, detail=tr_lang(lang, "content_empty"))
    async with async_session_factory() as db:
        mem = (await db.execute(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
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
    from app.db.database import async_session_factory
    from app.models.memory import Memory
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    async with async_session_factory() as db:
        result = await db.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
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
        await db.commit()
    return {"status": "ok"}


@router.post("/deduplicate/{character_id}")
async def deduplicate(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """对该角色去重记忆"""
    from app.models.character import AICharacter
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        cresult = await db.execute(select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id == user_id))
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
    from app.models.character import AICharacter
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        cresult = await db.execute(select(AICharacter).where(AICharacter.id == character_id, AICharacter.user_id == user_id))
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
    from app.db.database import async_session_factory
    from app.models.memory import Memory
    if await _get_owned_memory(memory_id, user_id) is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Memory).where(Memory.parent_id == memory_id, Memory.user_id == user_id)
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
    from app.memory.supersede import purge_memory
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
    from app.memory.supersede import purge_memory
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
