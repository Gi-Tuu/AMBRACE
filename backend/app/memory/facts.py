# -*- coding: utf-8 -*-
"""事实「修正历史」只读查询（批 0-6，2026-09-28）：零新表、零写、不动取代语义。

缺口：一条事实被改口后，用户侧只看得到当前值，「被谁在什么时候把什么改成了什么」无处可查
（世界设定槽位早有历史接口 app/events/facts.get_fact_history，记忆侧的取代链一直没有暴露面）。

口径（只读现成数据，写入侧一行不动）：
- 取代链 memories.superseded_by + valid_to ＝ 主体。从给定记忆**反向**逐层回溯
  （superseded_by 指向它的那些行就是它的前身），逐对拼成「旧值 → 新值」的修正序列；
- 写入回执 memory_write_receipts（action=supersede）＝ 补「原因」，并在旧行 valid_to 缺失时兜时间。
  回执受 flag memory_write_receipt 闸控，关时查不到 ⇒ reason 返回 null（不编造）。

明确取不到、因此恒为 null 的字段：
- actor：取代链与回执表都不记操作者（回执无 user_id 列），不臆造；
- 内容改写（PATCH content 使 version+1）的旧正文被原地覆盖、不留痕，只有次数存活，
  故不进修正序列，只在 fact.version 给出改写次数。

边界：被冷归档（memory_archive，#70-C2 满 60 天迁走）的前身行不在热表，本查询覆盖不到
（与 supersede.py OBS-5 登记的悬空指针同源）。深度上限见 FACT_HISTORY_MAX_DEPTH：逐轮改口
不设上限会随会话量放大成无界载荷（与 events/facts 的 FACT_HISTORY_MAX_VERSIONS 同口径）。
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.memory import Memory, MemoryWriteReceipt

# 取代链回溯的最大层数（每层向前一版）；超过则 truncated=True 并停在该层
FACT_HISTORY_MAX_DEPTH = 50


def _iso(dt) -> str | None:
    """时间 → ISO 字符串（库内是 naive UTC，带 tz 的先剥掉以统一口径）；无值返回 None。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None).isoformat() if getattr(dt, "tzinfo", None) else dt.isoformat()


def _fact_point(r: Memory) -> dict:
    """记忆行 → 事实本体读数（当前值 + 状态 + 来路）。"""
    return {
        "id": r.id,
        "content": r.content,
        "status": r.status,
        "source": r.source,
        "sub_type": r.sub_type,
        "epistemic_status": r.epistemic_status,
        "speaker_type": r.speaker_type,
        "version": int(r.version or 0),  # 内容改写次数（旧正文无留痕，故只给次数）
        "valid_from": _iso(r.valid_from),
        "valid_to": _iso(r.valid_to),
        "created_at": _iso(r.created_at),
    }


async def _latest_supersede_receipts(db, memory_ids: list[int]) -> dict[int, object]:
    """一次查询取回这批旧行的 supersede 回执；同一行多条时取最新一条。"""
    if not memory_ids:
        return {}
    from app.memory.receipt import ACTION_SUPERSEDE

    rows = (await db.execute(
        select(MemoryWriteReceipt).where(
            MemoryWriteReceipt.memory_id.in_(memory_ids),
            MemoryWriteReceipt.action == ACTION_SUPERSEDE,
        ).order_by(MemoryWriteReceipt.created_at.desc(), MemoryWriteReceipt.id.desc())
    )).scalars().all()
    out: dict[int, object] = {}
    for r in rows:  # 已按新→旧排，首个即该 memory_id 的最新一条
        out.setdefault(r.memory_id, r)
    return out


async def build_fact_history(db, memory_id: int, scope_ids) -> dict | None:
    """只读组装某条事实的修正时间线（时间升序，最早一次修正在前）。

    - db：调用方持有的会话（端点自己开）；本函数只做 SELECT，不 commit；
    - scope_ids：租户白名单，前身行不在白名单内一律不进序列（防跨家庭外泄）；
    - 记忆不存在 / 不属于该租户 → None（端点据此 404）；无前身 → corrections 为空数组。
    """
    scope = [int(i) for i in (scope_ids or [])]
    if not scope:
        return None
    anchor = (await db.execute(
        select(Memory).where(Memory.id == memory_id, Memory.user_id.in_(scope))
    )).scalar_one_or_none()
    if anchor is None:
        return None

    # ── 反向逐层回溯取代链：superseded_by 指向当前层的就是当前层的前身 ──
    by_id = {anchor.id: anchor}
    edges: list[tuple[Memory, Memory]] = []  # (旧行, 新行)
    frontier = [anchor]
    depth = 0
    truncated = False
    while frontier:
        if depth >= FACT_HISTORY_MAX_DEPTH:
            truncated = True  # 链还没走完就撞到上限：更早的版本本轮不返回
            break
        depth += 1
        nxt_ids = [f.id for f in frontier]
        prevs = (await db.execute(
            select(Memory).where(
                Memory.superseded_by.in_(nxt_ids),
                Memory.user_id.in_(scope),
            )
        )).scalars().all()
        frontier = []
        for p in prevs:
            if p.id in by_id:
                continue  # 防环（数据异常时不无限回溯）
            by_id[p.id] = p
            frontier.append(p)
            edges.append((p, by_id[p.superseded_by]))

    receipts = await _latest_supersede_receipts(db, [old.id for old, _ in edges])

    corrections = []
    for old, new in edges:
        rcpt = receipts.get(old.id)
        changed_at = _iso(old.valid_to)
        time_from = "valid_to" if changed_at else None
        if not changed_at and rcpt is not None:
            changed_at = _iso(rcpt.created_at)
            time_from = "receipt_created_at" if changed_at else None
        corrections.append({
            "old_id": old.id,
            "new_id": new.id,
            "old_value": old.content,
            "new_value": new.content,
            "changed_at": changed_at,          # 旧值失效＝本次修正发生的时间
            "changed_at_from": time_from,      # 时间出处；两者都缺 → null（不猜）
            "old_source": old.source,
            "new_source": new.source,
            "reason": (rcpt.reason if rcpt is not None else None),  # 回执原因，无回执 → null
            "actor": None,  # 取代链/回执都不记操作者，恒 null
        })
    # 一个新版可能同时吃掉多条旧版（淘汰/合并），故按时间排序而非按链层排序
    corrections.sort(key=lambda c: (c["changed_at"] is None, c["changed_at"] or "", c["old_id"]))
    for i, c in enumerate(corrections, start=1):
        c["seq"] = i
    return {
        "fact": _fact_point(anchor),
        "corrections": corrections,
        "truncated": truncated,
    }
