# -*- coding: utf-8 -*-
"""沿链/沿边「半故事化」组装（Ariadne 模块 C，2026-09-04）——纯组装，零 LLM。

把检索命中沿记忆链（chain_id/parent_id，建链器另案）组织成小段叙事：只重排 + 连接词，
不创作、不补全、不推断因果以外的内容；保留每条的 [日期]、说话人、认知状态标注由
format_memory_line 层负责——本模块输入为检索命中 dict，输出为注入行列表。

红线（方案 §5.3）：沿链素材必须来自真实记忆（chain_index 由 get_chain_index_for_hits 提供）；
组装行受检索区既有 token 配额硬裁剪（section 层），绝不因成链把整链灌爆上下文。

接线：get_chain_index_for_hits 已实现——读建链器（memory_chain_builder flag，默认关）写入的
chain_id，把本轮命中 id 反查回同链节点索引（每链 ≤4、总 ≤8）。建链器 flag 关/元数据缺失时返回 {}，
section 接线在空 index 时走原路径（逐字节等价退化），绝不因取链失败阻塞主回复。
"""
from __future__ import annotations

# 简单轮换连接词（避免千篇一律；可扩）
_LINK = ("之后", "后来", "紧接着")
_rot = 0


def assemble_story_lines(retrieved: list[dict], chain_index: dict[int, list[dict]]) -> list[str]:
    """沿链组装（纯函数）。

    - retrieved: 检索命中 dict 列表（已 N 轮去重的 candidate）；
    - chain_index: 命中 id → 同链相邻节点列表（含自身；建链器提供）；
    - 输出行：孤立点走单行；能成链（同 chain_id ≥2 节点）的合并为一个缩进小块，
      按时间升序，首个节点前缀 ┌、其余 └+轮换连接词；已进块的链成员不再重复平铺。
    """
    global _rot
    out: list[str] = []
    consumed: set[str] = set()
    for m in retrieved:
        cid = str(m.get("chain_id") or "")
        chain = chain_index.get(m.get("id"))
        if chain and len(chain) >= 2 and cid and cid not in consumed:
            consumed.add(cid)
            ordered = sorted(chain, key=lambda x: str(x.get("created_at") or ""))
            for j, node in enumerate(ordered):
                date = str(node.get("created_at") or "")[:10]
                prefix = "┌" if j == 0 else f"└{_LINK[(_rot + j) % len(_LINK)]}"
                out.append(f"{prefix} [{date}] {str(node.get('content') or '')[:120]}")
            _rot += 1
        elif cid and cid in consumed:
            continue  # 已在某小块里，不重复平铺
        else:
            date = str(m.get("created_at") or "")[:10]
            out.append(f"- [{date}] {str(m.get('content') or '')[:150]}")
    return out


async def get_chain_index_for_hits(hit_ids: list) -> dict[int, list[dict]]:
    """命中 id → 同链节点索引（含自身，时间升序）。每链 ≤4、总 ≤8，受检索区 token 配额在外层硬裁剪。

    依赖建链器 flag：``memory_chain_builder`` 关（无可靠链数据）时返回 {}，section 走原路径，
    等价退化（F-1 第一处配套修复）。纯读、失败静默返回 {}，绝不阻塞主回复。
    """
    ids = [int(x) for x in (hit_ids or []) if x is not None]
    if not ids:
        return {}
    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("memory_chain_builder", False):
            return {}
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.memory import Memory

        PER_CHAIN, TOTAL = 4, 8
        async with async_session_factory() as db:
            hit_rows = (await db.execute(
                select(Memory).where(Memory.id.in_(ids), Memory.chain_id.is_not(None))
            )).scalars().all()
            chain_ids = list({m.chain_id for m in hit_rows if m.chain_id})
            if not chain_ids:
                return {}
            nodes = (await db.execute(
                select(Memory).where(
                    Memory.chain_id.in_(chain_ids),
                    Memory.is_archived == False,  # noqa: E712
                ).order_by(Memory.chain_id.asc(), Memory.created_at.asc())
            )).scalars().all()

        by_chain: dict[str, list[dict]] = {}
        for n in nodes:                      # 每链最多 PER_CHAIN 条（已按时间升序）
            lst = by_chain.setdefault(n.chain_id, [])
            if len(lst) < PER_CHAIN:
                lst.append({
                    "id": n.id, "content": n.content, "type": n.memory_type,
                    "importance": float(n.importance or 0),
                    "created_at": n.created_at, "chain_id": n.chain_id,
                    "epistemic_status": getattr(n, "epistemic_status", None),
                })
        out: dict[int, list[dict]] = {}
        budget = TOTAL
        for m in hit_rows:                  # 只给"本轮命中"的记忆挂索引，总量 ≤TOTAL
            chain = by_chain.get(m.chain_id)
            if chain and budget > 0 and m.id not in out:
                out[m.id] = chain
                budget -= len(chain)
        return out
    except Exception:
        return {}
