"""ChromaDB 向量存储封装 — 使用同步 Client + asyncio.to_thread 避免阻塞事件循环"""
from __future__ import annotations  # P0-1：延迟注解，避免 chromadb.Client(function) | None 在 Python<3.14 导入崩溃

import asyncio
import logging

import chromadb
from chromadb.config import Settings
from app.config import settings

_logger = logging.getLogger("db.vector_store")

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
    user_id: int | None = None,
):
    """存入一条向量记忆（#70 方案A.4.1：支持自定义文档文本 document 与状态 status，向后兼容）。

    - ``document``：入库向量文本（默认用 content 原文；L0 参与向量时传 'why content' 拼接）。
    - ``status``：metadata 状态（#70-C 双通道过滤用）；**始终写入**（含默认 active）。
      #70-C BUG-2 修复：若不写 status 键，开 flag 后 Chroma `$in[active,stale]` 会把
      「缺键」的新 active 向量整批漏掉，稠密召回与写前查重静默失效。flag 关时按
      character_id 过滤不看 status，多写一个键零副作用。
    - ``user_id``（A1，2026-09-19）：账号归属。None 时回退「该角色 owner」= ai_characters.user_id
      （进程内缓存）。**始终写 user_id**（与 status 同理：缺键会被 user_id 过滤条件整批漏掉）；
      owner 亦查不到时不写该键并 warning，不抛异常。
    """
    _uid = user_id if user_id is not None else await _character_owner_of(character_id)
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
    if _uid is not None:
        # A1（2026-09-19）：始终写 user_id（与 status 同理，缺键会被 scope 过滤整批漏掉）。
        _meta["user_id"] = int(_uid)
    else:
        _logger.warning("add_memory: character %s owner unresolved; user_id omitted", character_id)
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


def _vector_user_scope_flag_on() -> bool:
    """A1（2026-09-19）：读 vector_user_scope flag。延迟 import（照抄 _supersede_flag_on 风格）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("vector_user_scope", False))
    except Exception:
        return False


# A1（2026-09-19）向量「账号归属」：角色 owner（ai_characters.user_id）进程内缓存。
# 写路径回退与读路径 scope 兜底共用，避免每轮打库；上限 512，超出按插入序淘汰最旧一个。
# 查询异常（DB 不可用等）不写缓存，下一轮可重试；明确的「无该角色」结果（None）写缓存。
_OWNER_CACHE: dict[int, int | None] = {}
_OWNER_CACHE_MAX = 512


def _remember_owner(character_id: int, owner: int | None) -> None:
    if character_id not in _OWNER_CACHE and len(_OWNER_CACHE) >= _OWNER_CACHE_MAX:
        try:
            _OWNER_CACHE.pop(next(iter(_OWNER_CACHE)))
        except (StopIteration, KeyError):
            pass
    _OWNER_CACHE[character_id] = owner


async def _character_owner_of(character_id: int) -> int | None:
    """A1：查该角色 owner（ai_characters.user_id）。查不到/异常一律返回 None，不抛。

    用项目既有会话工厂（app.db.database，与文件内既有风格一致）；函数内延迟 import，
    不在模块顶层 import 造成环。
    """
    if character_id in _OWNER_CACHE:
        return _OWNER_CACHE[character_id]
    try:
        from sqlalchemy import select

        from app.db.database import async_session_factory
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            owner = (await db.execute(
                select(AICharacter.user_id).where(AICharacter.id == character_id)
            )).scalar_one_or_none()
        owner = int(owner) if owner is not None else None
    except Exception as e:
        _logger.warning("character owner lookup failed char=%s: %s", character_id, e)
        return None
    _remember_owner(character_id, owner)
    return owner


async def _resolve_user_scope(character_id: int, user_ids: list[int] | None) -> list[int]:
    """A1 scope 解析：显式 ``user_ids`` 非空 → 用它；为空 → ``[owner_of(character_id)]``。

    返回去重升序 list；owner 查不到时返回 []（调用方据此不追加 user_id 子句，fail-open 保召回）。
    """
    if user_ids:
        return sorted({int(u) for u in user_ids})
    owner = await _character_owner_of(character_id)
    return [owner] if owner is not None else []


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


def _char_where_scoped(character_id: int, supersede_on: bool, status: str | None = None,
                       user_scope: list[int] | None = None) -> dict:
    """A1（2026-09-19）：在 _char_where 结果外再包一层账号归属条件。

    - ``user_scope`` 为空（flag 关 / scope 解析不出）→ 逐字节返回 _char_where 旧行为（连 where 都不变）。
    - 非空 → ``{"$and": [<char_clause>, {"user_id": {"$in": sorted(scope)}}]}``。
    """
    clause = _char_where(character_id, supersede_on, status)
    if not user_scope:
        return clause
    return {"$and": [clause, {"user_id": {"$in": sorted(user_scope)}}]}


async def search_memories(
    character_id: int,
    query_embedding: list[float],
    limit: int = 5,
    status: str | None = None,
    user_ids: list[int] | None = None,
) -> list[dict]:
    """向量搜索相关记忆（怀旧/复习面：默认 active+stale；现状面调用方显式传 status="active"）。

    A1（2026-09-19）：``user_ids`` = 账号 scope，缺省 None → 回退该角色 owner；
    仅在 vector_user_scope flag 开时追加 user_id 过滤，flag 关 = 逐字节旧行为（where/返回结构/异常静默均不变）。
    """
    _scope = await _resolve_user_scope(character_id, user_ids) if _vector_user_scope_flag_on() else None
    collection = await get_or_create_collection()
    try:
        results = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=limit,
            where=_char_where_scoped(character_id, _supersede_flag_on(), status, _scope),
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
    user_ids: list[int] | None = None,
):
    """在 ChromaDB 中查找同角色与给定向量最相似的记忆。

    返回 (memory_id, similarity) 或 None（cosine 空间：distance = 1 - similarity）。
    用于写路径向量查重：语义相同的记忆不新增，改为更新原记忆。

    2026-09-17 批次一（任务2）：这是**现状面**向量查询——默认只与现行（active）向量比对，
    避免新写入的现行事实被一条 stale 旧现状（旧「在长沙」）吞并成「合并到旧行」；
    current_facts_active_only 关 = 回退旧行为（status=None 不做状态过滤）。

    A1（2026-09-19）：``user_ids`` 同上（缺省回退角色 owner）；flag 关 = 逐字节旧行为。
    """
    _status = status if status is not None else ("active" if _current_facts_flag_on() else None)
    _scope = await _resolve_user_scope(character_id, user_ids) if _vector_user_scope_flag_on() else None
    collection = await get_or_create_collection()
    try:
        results = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=limit,
            where=_char_where_scoped(character_id, _supersede_flag_on(), _status, _scope),
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


async def get_all_vectors_by_character(character_id: int, user_ids: list[int] | None = None) -> dict:
    """取该角色全部向量记忆：{memory_id: embedding}。用于全量向量去重。

    A1（2026-09-19）：``user_ids`` 同上（缺省回退角色 owner）；flag 关时 where 与旧文
    逐字节一致（{"character_id": character_id}），返回结构与异常静默不变。
    """
    _scope = await _resolve_user_scope(character_id, user_ids) if _vector_user_scope_flag_on() else None
    collection = await get_or_create_collection()
    try:
        results = await asyncio.to_thread(
            collection.get,
            where=_char_where_scoped(character_id, False, None, _scope),
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
    user_id: int | None = None,
):
    """更新（或插入）一条向量记忆：记忆内容被改写（如半重复融合）后重算嵌入同步到 ChromaDB。

    #70 方案A.4.1：支持自定义文档文本 document 与状态 status（向后兼容，默认值下旧调用零改动）。
    ``status`` **始终写入**（含默认 active）——#70-C BUG-2 修复：缺键会令开 flag 后的
    Chroma `$in[active,stale]` 把新 active 向量整批漏掉，稠密召回与写前查重静默失效。
    ``user_id``（A1，2026-09-19）：同 add_memory——None 回退角色 owner，始终写入；
    owner 查不到则不写该键并 warning，不抛异常。
    """
    _uid = user_id if user_id is not None else await _character_owner_of(character_id)
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
    if _uid is not None:
        # A1（2026-09-19）：始终写 user_id（与 status 同理，缺键会被 scope 过滤整批漏掉）。
        _meta["user_id"] = int(_uid)
    else:
        _logger.warning(
            "upsert_memory_vector: character %s owner unresolved; user_id omitted", character_id)
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


#: 按账号删除时的分批大小（一次传几万个 id 给 Chroma 会把整批请求撑爆）
_VECTOR_DELETE_CHUNK = 500


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def delete_memory_vectors_by_user(user_id: int, memory_ids: list[int] | None = None) -> dict:
    """按**账号**删除向量记忆（控制台删号·第二期，方案 v2 §3.2 第 3 步）。

    两条路都要走，缺一不可：

    1. ``where={"user_id": uid}`` —— 2026-09-19（A1）起 metadata 始终写 ``user_id``，
       这一句就能覆盖新数据，且由 Chroma 侧完成，最便宜；
    2. **老向量没有 ``user_id`` 键**（A1 之前入库的）按 where 永远匹配不到 → 必须用
       ``memory_id`` 反查补齐再删（向量文档 id 就是 ``str(memory_id)`，见 :func:`add_memory`）。
       ``memory_ids`` 由调用方在**删 ``memories`` 行之前**取出传进来（行一旦删掉就永久不可反查）。

    失败语义照抄 :func:`delete_memory_vectors_by_character`：**不抛穿**（向量库是旁路，
    删不动不该让整条删号链断掉），以计数为主。单批失败退化成逐 id 删，
    「一个删不掉的 id 不能拖垮整批」；仍失败的计入 ``unresolved``（进审计/报告）。

    返回 ``{"by_user": n1, "by_memory": n2, "unresolved": n3}``。
    """
    uid = int(user_id)
    out = {"by_user": 0, "by_memory": 0, "unresolved": 0}
    try:
        collection = await get_or_create_collection()
    except Exception as e:  # 向量库整体不可用：计数 0，不抛
        _logger.warning("delete_memory_vectors_by_user: collection unavailable user=%s: %s", uid, e)
        return out

    def _delete_ids(ids: list[str]) -> tuple[int, int]:
        """删一批文档 id，返回 (成功数, 失败数)。整批失败时逐条重试。"""
        if not ids:
            return 0, 0
        try:
            collection.delete(ids=ids)
            return len(ids), 0
        except Exception as e:
            _logger.warning("vector batch delete failed (%d ids): %s", len(ids), e)
        ok = bad = 0
        for one in ids:
            try:
                collection.delete(ids=[one])
                ok += 1
            except Exception:
                bad += 1
        return ok, bad

    def _sync() -> dict:
        # ① 新数据：metadata 带 user_id，直接按 where 删（计数用 get 先取，delete 不给条数）
        try:
            got = collection.get(where={"user_id": uid}, include=[])
            out["by_user"] = len(got.get("ids") or [])
        except Exception as e:
            _logger.warning("vector get by user_id failed user=%s: %s", uid, e)
        try:
            collection.delete(where={"user_id": uid})
        except Exception as e:
            _logger.warning("vector delete by user_id failed user=%s: %s", uid, e)

        # ② 老数据：按 memory_id 反查补齐（上面那句 where 匹配不到缺 user_id 键的向量）
        ids = [str(int(m)) for m in (memory_ids or []) if _as_int(m) is not None]
        for i in range(0, len(ids), _VECTOR_DELETE_CHUNK):
            chunk = ids[i:i + _VECTOR_DELETE_CHUNK]
            try:
                present = collection.get(ids=chunk, include=[])
            except Exception as e:
                _logger.warning("vector get by memory_id failed chunk=%d: %s", i, e)
                out["unresolved"] += len(chunk)
                continue
            alive = [str(x) for x in (present.get("ids") or [])]
            if not alive:
                continue  # 该记忆本就没有向量（未参与向量/已删），不算残留
            ok, bad = _delete_ids(alive)
            out["by_memory"] += ok
            out["unresolved"] += bad
        return out

    try:
        await asyncio.to_thread(_sync)
    except Exception as e:  # 兜底：整体异常也不抛穿
        _logger.warning("delete_memory_vectors_by_user failed user=%s: %s", uid, e)
    return out


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
