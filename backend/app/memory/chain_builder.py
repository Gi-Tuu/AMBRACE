# -*- coding: utf-8 -*-
"""记忆链条构建器（B1-②，2026-09-04，方案 §10-§18 / 阶段 C0-C5）。

背景根因（方案 §11）：`Memory.chain_id/parent_id/node_type/version` 四列早已建，
`save_memory()` 也接收这三个参数，但全后端没有任何生产代码在写记忆时赋过值
（除测试手工造链）——建链能力是个"未接线"的半成品，故真机记忆本链条全空。

本模块 = 建链器本体（纯规则、零额外 LLM）：写入后异步把"同一件事的演进"挂成链。
- 只对 event/insight 建时序链；user_info/preference 等静态画像不建链（否则把
  "用户喜欢美式"这种静态事实串成伪时间线）。
- 找父：向量近邻（cosine ≥ 阈值）+ 14 天时间窗 + 同类型/关系情绪奖励排序；
  达不到阈值就新开一条链（当 root），宁缺毋滥。
- 全部异步、失败静默、幂等（已挂链的不重复挂）；flag ``memory_chain_builder`` 控制，默认关。

另含沿链读取/扩展与 RECALL_SHARED 捞链（§14 / §15），供 ``section_memories`` /
``message_generator`` 复用；均以各自 flag 门控（默认关 = 零行为变化）。

与 Ariadne 模块 C（``app/memory/story_assemble.py``）的关系：本模块**不串改**模块 C。
模块 C 的 ``get_chain_index_for_hits`` 是独立空实现（等建链器可用后另行启用组装），
本模块只提供 ``get_chain_nodes`` / ``expand_along_chain`` 两条纯读原语，二者正交。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger

_logger = get_logger("memory.chain")

# ── 常量（方案 §13.2）──
CHAINABLE_TYPES = {"event", "insight"}   # 只对"事件/洞察"建时序链
PARENT_SIM_THRESHOLD = 0.82              # 低于写前查重 0.86：相关延续即可挂，重复才合并
CHAIN_WINDOW_DAYS = 14                   # 只挂 14 天内的父，杜绝把陈年旧事拉成一条链
MAX_CHAIN_NODES = 12                     # 每链节点上限，超限另开 root（防巨链）
# 同类型 +0.05，关系/情绪互挂 +0.03（叠加在相似度上做排序，不改变阈值语义）
SAME_TYPE_BONUS = 0.05
RELATION_BONUS = 0.03
RELATION_SUBS = {"relationship", "emotion"}
# 沿链反哺（§14）：扩展节点预算与降权
CHAIN_EXPAND_BUDGET = 2
CHAIN_EXPAND_DOWNWEIGHT = 0.9


def memory_chain_builder_enabled() -> bool:
    """公开 flag 门控：读 ``memory_chain_builder``（默认关）。供 write.py 调用点判断是否挂链。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_chain_builder", False))
    except Exception:
        return False


def _flag_on() -> bool:
    """模块内 flag 门控：读 ``memory_chain_builder``（默认关）。"""
    return memory_chain_builder_enabled()


def _chainable(m: Memory) -> bool:
    """一条记忆是否可建链（纯函数，可单测）：可建链类型 + 未归档 + 状态非 stale/superseded。

    只对 event/insight 建链；user_info/preference 等静态画像（方案 §13.1）不建链。
    """
    if getattr(m, "memory_type", None) not in CHAINABLE_TYPES:
        return False
    if getattr(m, "is_archived", False):
        return False
    status = getattr(m, "status", "active") or "active"
    # 保守排除：stale（派生失效）/ superseded（被取代）的节点不应再作为父/被扩展注入
    if status in ("stale", "superseded"):
        return False
    return True


def parent_score(sim: float, cand_mtype: str, cand_sub_type: str | None,
                 mtype: str, sub_type: str | None) -> float:
    """父节点候选评分（纯函数，可单测）：裸相似度 + 类型奖励，奖励只影响排序不改变阈值。"""
    score = sim
    if cand_mtype == mtype:
        score += SAME_TYPE_BONUS
    if sub_type in RELATION_SUBS and (cand_sub_type or "") in RELATION_SUBS:
        score += RELATION_BONUS
    return score


async def _best_parent(character_id: int, embedding: list[float], self_id: int,
                       mtype: str, sub_type: str | None) -> Memory | None:
    """在近 14 天同角色候选里选最优父；达不到裸相似度阈值返回 None（→ 新开 root）。

    阈值看**裸相似度**（≥PARENT_SIM_THRESHOLD 才有资格），奖励只影响候选间排序，
    不允许把不够像的硬拉进来（方案 §13.3 红线）。
    """
    from app.db.vector_store import search_memories
    neighbors = await search_memories(character_id, query_embedding=embedding, limit=10)
    if not neighbors:
        return None
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=CHAIN_WINDOW_DAYS)
    ids = [n["id"] for n in neighbors if n["id"] != self_id]
    if not ids:
        return None

    async with async_session_factory() as db:
        rows = (await db.execute(select(Memory).where(Memory.id.in_(ids)))).scalars().all()
        by_id = {m.id: m for m in rows}
        best: Memory | None = None
        best_score = -1.0
        for n in neighbors:
            cand = by_id.get(n["id"])
            if cand is None or not _chainable(cand):
                continue
            created = cand.created_at
            if created is not None:
                created = created.replace(tzinfo=None) if created.tzinfo else created
                if created < cutoff:
                    continue  # 超出时间窗不挂
            sim = 1.0 - float(n.get("distance") or 0.0)
            if sim < PARENT_SIM_THRESHOLD:
                continue  # 裸相似度不达标：不进候选
            score = parent_score(sim, cand.memory_type, cand.sub_type, mtype, sub_type)
            if score > best_score:
                best, best_score = cand, score
        return best


async def link_new_memory(memory_id: int, embedding: list[float] | None = None) -> None:
    """写入后挂链（异步调用）。``embedding`` 复用 save_memory 已算好的向量，避免重复 ONNX 推理。

    - flag ``memory_chain_builder`` 关 → no-op（零行为变化）；
    - 幂等：已挂链（chain_id 非空）不再动；
    - 只对可建链类型挂；无合格父 → 新开 root；链长已达上限 → 另开 root。
    """
    if not _flag_on():
        return
    try:
        async with async_session_factory() as db:
            m = await db.get(Memory, memory_id)
            if m is None or m.chain_id is not None:  # 幂等：已挂链不动
                return
            if not _chainable(m):
                return
            if embedding is None:  # 兜底：没复用成就现算（正常情况下由调用方传入）
                from app.memory.embedding import text_embedding
                embedding = await text_embedding(m.content)
            parent = await _best_parent(m.character_id, embedding, m.id, m.memory_type, m.sub_type)
            if parent is not None and parent.chain_id:
                cnt = (await db.execute(
                    select(Memory.id).where(Memory.chain_id == parent.chain_id)
                )).scalars().all()
                if len(cnt) >= MAX_CHAIN_NODES:
                    m.chain_id, m.parent_id, m.node_type = uuid.uuid4().hex, None, "root"
                else:
                    m.chain_id, m.parent_id, m.node_type = parent.chain_id, parent.id, "branch"
            else:
                m.chain_id, m.parent_id, m.node_type = uuid.uuid4().hex, None, "root"
            await db.commit()
    except Exception as e:
        _logger.warning("link_new_memory failed id=%s: %s", memory_id, e)


async def get_chain_nodes(memory_id: int) -> list[Memory]:
    """取某记忆所在链的全部节点（按时间升序）；无链返回仅自身。供记忆本时间线/主动回忆。"""
    async with async_session_factory() as db:
        m = await db.get(Memory, memory_id)
        if m is None or not m.chain_id:
            return [m] if m else []
        rows = (await db.execute(
            select(Memory).where(Memory.chain_id == m.chain_id, Memory.is_archived == False)  # noqa: E712
            .order_by(Memory.created_at.asc())
        )).scalars().all()
        return list(rows)


async def expand_along_chain(memory_id: int, max_extra: int = 2) -> list[int]:
    """检索扩展：给定命中节点，返回可补充注入的相邻节点 id（父 + 最近一个子），最多 max_extra。

    用于 §14 沿链补"前因后果"：只取紧邻的 1 个父 + 1 个最近子，避免把整链灌进上下文。
    """
    async with async_session_factory() as db:
        m = await db.get(Memory, memory_id)
        if m is None or not m.chain_id:
            return []
        ids: list[int] = []
        if m.parent_id:
            ids.append(m.parent_id)
        child = (await db.execute(
            select(Memory.id).where(Memory.parent_id == memory_id)
            .order_by(Memory.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if child:
            ids.append(child)
        return ids[:max_extra]


async def maybe_expand_chain(character_id: int, picked: list[dict], budget_extra: int = CHAIN_EXPAND_BUDGET) -> list[dict]:
    """§14 沿链反哺：对排序最靠前的可建链命中，沿链补最多 budget_extra 个相邻节点。

    - flag ``memory_chain_expand`` 关 → 原样返回新列表（零行为变化）；
    - 扩展节点仍走后续既有 token 配额与 5 轮去重（本函数只扩充待注入候选，不直接注入）；
    - 相邻节点降权 0.9、内容前缀「（同一件事）」，只给最相关的两条命中找上下文，避免爆 token。
    - 不传入被修改：返回**新列表**，避免污染调用方持有的 state 原始检索结果。
    """
    if not picked:
        return list(picked)

    def _id_of(m) -> int | None:
        """兼容检索 dict 与 ORM 对象取 id。"""
        return m.get("id") if isinstance(m, dict) else getattr(m, "id", None)

    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("memory_chain_expand", False):
            return list(picked)
        have = {i for i in (_id_of(m) for m in picked) if i is not None}
        extra_ids: list[int] = []
        for m in picked[:2]:  # 只给最相关的两条找上下文
            hit_id = _id_of(m)
            if hit_id is None:
                continue
            for pid in await expand_along_chain(hit_id, budget_extra):
                if pid in have or pid in extra_ids:
                    continue
                have.add(pid)
                extra_ids.append(pid)
            if len(extra_ids) >= budget_extra:
                break
        if not extra_ids:
            return list(picked)
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(Memory.id.in_(extra_ids))
            )).scalars().all()
        out = list(picked)
        for r in rows:  # 相邻节点降权展示，标注"同一件事"
            if r.is_archived:
                continue
            out.append({
                "id": r.id,
                "content": "（同一件事）" + (r.content or ""),
                "type": r.memory_type,
                "importance": float(r.importance or 0) * CHAIN_EXPAND_DOWNWEIGHT,
                "created_at": r.created_at,
                "epistemic_status": r.epistemic_status,
                "status": r.status,
            })
        return out
    except Exception:
        return list(picked)


async def pick_recall_chain(character_id: int) -> str | None:
    """§15 挑一条值得主动回忆的链：优先关系/情绪、节点≥2、近 3-60 天，返回带日期的多行短文本。

    若命中一条链，则把它按时间压成一小段有起承的回忆（时间锚点天然清晰）；
    无则返回 None（调用方回退到普通语义检索）。纯只读查询，失败返回 None。
    """
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory.chain_id, func.count(Memory.id).label("n"), func.max(Memory.created_at).label("last"))
                .where(
                    Memory.character_id == character_id,
                    Memory.chain_id.is_not(None),
                    Memory.is_archived == False,  # noqa: E712
                    Memory.created_at >= now - timedelta(days=60),
                    Memory.created_at <= now - timedelta(days=3),  # 太近的留给正常承接，不主动"回忆"
                )
                .group_by(Memory.chain_id)
                .having(func.count(Memory.id) >= 2)
                .order_by(func.max(Memory.created_at).desc())
                .limit(20)
            )).all()
            if not rows:
                return None
            # 优先节点数更多的链（关系/情绪链通常节点多、更像"一件事的发展"）
            rows = sorted(rows, key=lambda r: r.n, reverse=True)

            async def _timeline(cid) -> str:
                nodes = (await db.execute(
                    select(Memory).where(Memory.chain_id == cid)
                    .order_by(Memory.created_at.asc()).limit(4)
                )).scalars().all()
                return "\n".join(f"[{str(x.created_at)[:10]}] {(x.content or '')[:60]}" for x in nodes)

            for cid, _n, _last in rows:                      # 优先关系/情绪链
                nodes = (await db.execute(
                    select(Memory).where(Memory.chain_id == cid)
                    .order_by(Memory.created_at.asc()).limit(4)
                )).scalars().all()
                if any((x.sub_type or "") in RELATION_SUBS for x in nodes):
                    return await _timeline(cid)
            return await _timeline(rows[0][0])               # 退而取任一链
    except Exception as _e:
        _logger.warning("pick_recall_chain failed char=%s: %s", character_id, _e)
        return None
