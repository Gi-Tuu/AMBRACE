"""ChromaDB 向量存储封装 — 使用同步 Client + asyncio.to_thread 避免阻塞事件循环"""
from __future__ import annotations  # P0-1：延迟注解，避免 chromadb.Client(function) | None 在 Python<3.14 导入崩溃

import asyncio

import chromadb
from chromadb.config import Settings
from app.config import settings

# 单例客户端（同步）
_client: chromadb.Client | None = None

COLLECTION_NAME = "character_memories"


def get_client() -> chromadb.Client:
    """获取 ChromaDB 客户端（同步，调用方用 to_thread 包装）"""
    global _client
    if _client is None:
        _client = chromadb.Client(
            Settings(
                persist_directory=settings.chroma_persist_dir,
                anonymized_telemetry=False,
                is_persistent=True,
            )
        )
    return _client


async def get_or_create_collection():
    """获取或创建记忆集合"""
    def _sync():
        client = get_client()
        try:
            return client.get_collection(COLLECTION_NAME)
        except Exception:
            return client.create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
    return await asyncio.to_thread(_sync)


async def add_memory(
    memory_id: int,
    character_id: int,
    memory_type: str,
    content: str,
    embedding: list[float],
    importance: int = 1,
    document: str | None = None,
    status: str = "active",
):
    """存入一条向量记忆（#70 方案A.4.1：支持自定义文档文本 document 与状态 status，向后兼容）。

    - ``document``：入库向量文本（默认用 content 原文；L0 参与向量时传 'why content' 拼接）。
    - ``status``：metadata 状态（#70-C 双通道过滤用）；**始终写入**（含默认 active）。
      #70-C BUG-2 修复：若不写 status 键，开 flag 后 Chroma `$in[active,stale]` 会把
      「缺键」的新 active 向量整批漏掉，稠密召回与写前查重静默失效。flag 关时按
      character_id 过滤不看 status，多写一个键零副作用。
    """
    collection = await get_or_create_collection()
    _meta = {
        "memory_id": memory_id,
        "character_id": character_id,
        "memory_type": memory_type,
        "importance": importance,
        # #70-C BUG-2 修复：始终写 status（含 active）。否则开 flag 后 $in[active,stale]
        # 会把「缺键」的新 active 向量整批漏掉，稠密召回与写前查重静默失效。
        "status": status,
    }
    await asyncio.to_thread(
        collection.add,
        ids=[str(memory_id)],
        embeddings=[embedding],
        documents=[document or content],
        metadatas=[_meta],
    )


def _supersede_flag_on() -> bool:
    """#70-C：读 memory_supersede flag。延迟 import（避免 vector_store 顶层依赖 loop 造成环）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_supersede", False))
    except Exception:
        return False


def _current_facts_flag_on() -> bool:
    """2026-09-17 批次一（任务2）：现状面新口径 current_facts_active_only（默认 True）。

    延迟 import（避免 vector_store 顶层依赖 loop 造成环），与 service._current_facts_flag_on 同口径。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("current_facts_active_only", True))
    except Exception:
        return True


def _char_where(character_id: int, supersede_on: bool, status: str | None = None) -> dict:
    """按角色检索的 where 子句。

    - ``status`` 非空：**现状面**——只取该状态（不受 memory_supersede 门控；旧现状不得参与现状面）。
    - ``status`` 空：旧行为 / 怀旧面——flag 开=active/stale（stale 保留，怀旧可见），关=不做状态过滤。

    由 vector_store 的读取函数与 supersede 相关测试共用（可独立单测）。
    """
    if status:
        return {"$and": [
            {"character_id": character_id},
            {"status": {"$in": [status]}},
        ]}
    if not supersede_on:
        return {"character_id": character_id}          # 旧行为（逐字节一致）
    return {"$and": [
        {"character_id": character_id},
        {"status": {"$in": ["active", "stale"]}},
    ]}


async def search_memories(
    character_id: int,
    query_embedding: list[float],
    limit: int = 5,
    status: str | None = None,
) -> list[dict]:
    """向量搜索相关记忆（怀旧/复习面：默认 active+stale；现状面调用方显式传 status="active"）。"""
    collection = await get_or_create_collection()
    try:
        results = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=limit,
            where=_char_where(character_id, _supersede_flag_on(), status),
        )
    except Exception:
        return []

    memories = []
    if results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            memories.append({
                "id": int(doc_id),
                "content": results["documents"][0][i] if results["documents"] else "",
                "type": results["metadatas"][0][i].get("memory_type", "unknown"),
                "importance": results["metadatas"][0][i].get("importance", 1),
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })
    return memories


async def find_similar_memory(
    character_id: int,
    query_embedding: list[float],
    limit: int = 20,
    min_similarity: float = 0.9,
    status: str | None = None,
):
    """在 ChromaDB 中查找同角色与给定向量最相似的记忆。

    返回 (memory_id, similarity) 或 None（cosine 空间：distance = 1 - similarity）。
    用于写路径向量查重：语义相同的记忆不新增，改为更新原记忆。

    2026-09-17 批次一（任务2）：这是**现状面**向量查询——默认只与现行（active）向量比对，
    避免新写入的现行事实被一条 stale 旧现状（旧「在长沙」）吞并成「合并到旧行」；
    current_facts_active_only 关 = 回退旧行为（status=None 不做状态过滤）。
    """
    _status = status if status is not None else ("active" if _current_facts_flag_on() else None)
    collection = await get_or_create_collection()
    try:
        results = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=limit,
            where=_char_where(character_id, _supersede_flag_on(), _status),
        )
    except Exception:
        return None
    if not results["ids"] or not results["ids"][0]:
        return None
    for i, doc_id in enumerate(results["ids"][0]):
        distance = results["distances"][0][i] if results["distances"] else 1.0
        similarity = 1.0 - distance
        if similarity >= min_similarity:
            return int(doc_id), similarity
    return None


async def get_all_vectors_by_character(character_id: int) -> dict:
    """取该角色全部向量记忆：{memory_id: embedding}。用于全量向量去重。"""
    collection = await get_or_create_collection()
    try:
        results = await asyncio.to_thread(
            collection.get,
            where={"character_id": character_id},
            include=["embeddings"],
        )
    except Exception:
        return {}
    out = {}
    ids = results.get("ids") or []
    embs = results.get("embeddings")
    if embs is None:
        embs = []
    for i, doc_id in enumerate(ids):
        if i < len(embs) and embs[i] is not None:
            try:
                out[int(doc_id)] = embs[i]
            except Exception:
                pass
    return out


async def upsert_memory_vector(
    memory_id: int,
    character_id: int,
    memory_type: str,
    content: str,
    embedding: list[float],
    importance: int = 1,
    document: str | None = None,
    status: str = "active",
):
    """更新（或插入）一条向量记忆：记忆内容被改写（如半重复融合）后重算嵌入同步到 ChromaDB。

    #70 方案A.4.1：支持自定义文档文本 document 与状态 status（向后兼容，默认值下旧调用零改动）。
    ``status`` **始终写入**（含默认 active）——#70-C BUG-2 修复：缺键会令开 flag 后的
    Chroma `$in[active,stale]` 把新 active 向量整批漏掉，稠密召回与写前查重静默失效。
    """
    collection = await get_or_create_collection()
    _meta = {
        "memory_id": memory_id,
        "character_id": character_id,
        "memory_type": memory_type,
        "importance": importance,
        # #70-C BUG-2 修复：始终写 status（含 active）。否则开 flag 后 $in[active,stale]
        # 会把「缺键」的新 active 向量整批漏掉，稠密召回与写前查重静默失效。
        "status": status,
    }
    await asyncio.to_thread(
        collection.upsert,
        ids=[str(memory_id)],
        embeddings=[embedding],
        documents=[document or content],
        metadatas=[_meta],
    )


async def delete_memory_vector(memory_id: int):
    """删除指定向量记忆"""
    collection = await get_or_create_collection()
    try:
        await asyncio.to_thread(collection.delete, ids=[str(memory_id)])
    except Exception:
        pass


async def delete_memory_vectors_by_character(character_id: int):
    """按角色删除全部向量记忆（metadata.character_id 精确匹配）"""
    collection = await get_or_create_collection()
    try:
        await asyncio.to_thread(collection.delete, where={"character_id": character_id})
    except Exception:
        pass


async def mark_memory_vector_status(memory_id: int, status: str) -> None:
    """#70-C：只改向量 metadata.status（合并旧 metadata，不动向量本身）。

    供 supersede/restore 级联标记；异常静默（失败不阻塞主链路/取代结果已在 SQLite 落库）。
    """
    collection = await get_or_create_collection()
    try:
        ids = [str(memory_id)]
        got = await asyncio.to_thread(
            collection.get, ids=ids, include=["metadatas"],
        )
        metas = got.get("metadatas") or []
        if not metas:
            return
        meta = dict(metas[0] or {})
        meta["status"] = status
        await asyncio.to_thread(
            collection.update, ids=ids, metadatas=[meta],
        )
    except Exception as e:
        import logging
        logging.getLogger("db.vector_store").warning(
            "mark_memory_vector_status failed mem=%s: %s", memory_id, e)
