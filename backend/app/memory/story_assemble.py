# -*- coding: utf-8 -*-
"""沿链/沿边「半故事化」组装（Ariadne 模块 C，2026-09-04）——纯组装，零 LLM。

把检索命中沿记忆链（chain_id/parent_id，建链器另案）组织成小段叙事：只重排 + 连接词，
不创作、不补全、不推断因果以外的内容；保留每条的 [日期]、说话人、认知状态标注由
format_memory_line 层负责——本模块输入为检索命中 dict，输出为注入行列表。

红线（方案 §5.3）：沿链素材必须来自真实记忆（chain_index 由 get_chain_index_for_hits 提供）；
组装行受检索区既有 token 配额硬裁剪（section 层），绝不因成链把整链灌爆上下文。

波次边界：记忆链建链器在「主动消息自然接触+记忆链」另案落地——本波 get_chain_index_for_hits
恒返回 {}（空实现），section 接线在空 index 时走原路径（逐字节等价退化），模块结构/flag/
单测先行就位，建链器落地后无需改本文件即可启用真实组装。
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
    """命中 id → 同链相邻节点索引（含自身，时间升序）。

    空实现（波次边界）：记忆链建链器另案（主动消息自然接触+记忆链交接），落地后在此
    按 chain_id/parent_id 一次性查回同链节点（每链 ≤4、总 ≤8，受检索区 token 配额约束）。
    现恒返回 {} → section 接线走原路径（逐字节等价退化）。
    """
    return {}
