# -*- coding: utf-8 -*-
"""跨角色用户事实对齐（§20.7，2026-09-04 落地）：把角色 per-char 的「可变槽位旧记忆」与全局用户事实对齐。

- 新值出现后，旧值记忆标 ``status='stale'``（复用 #70 语义），不物理删、可追溯；
- 双通道一致：SQLite 提交后再同步 Chroma metadata（沿用 supersede 做法，失败静默）；
- 零 LLM、幂等：已是 stale 的不再命中；重复跑零副作用；
- 显式与 world_facts（P1-3 权威层）分治：本模块只对齐 per-char memories 的旧值失效，
  world_facts 的角色视角世界状态不动。
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.memory.supersede import ACTIVE, STALE
from app.memory.user_facts import MUTABLE_SLOTS, get_active_user_facts


async def stale_character_slot_memory(
    character_id: int,
    slot: str,
    old_value: str | None,
) -> int:
    """单角色单槽：把 active 的 user_info 记忆中「文本命中 old_value」的标 stale。

    - 只用 old_value 的文本锚点（前 6 字符）匹配，**不靠 sub_type==slot 单独命中**
      （避免把刚写入的新值记忆误标 stale；insight 等非 user_info 不误伤）；
    - 返回命中数；失败返回 0。
    """
    if not old_value:
        return 0
    anchor = (old_value or "").strip()[:6]
    if not anchor:
        return 0
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.status == ACTIVE,
                    Memory.is_archived == False,  # noqa: E712
                    Memory.memory_type == "user_info",
                )
            )).scalars().all()
            hit = [m for m in rows if anchor in (m.content or "")]
            for m in hit:
                m.status = STALE
            await db.commit()
            # 双通道：向量 metadata.status 同步降级（沿用 supersede 做法，失败静默）
            if hit:
                try:
                    from app.db.vector_store import mark_memory_vector_status
                    for m in hit:
                        await mark_memory_vector_status(m.id, STALE)
                except Exception:
                    pass
            return len(hit)
    except Exception:
        return 0


async def _maybe_project_user_fact(
    character_id: int,
    user_id: int,
    slot: str,
    new_value: str,
) -> None:
    """§20.8（可选）：flag cross_char_fact_projection 开时，把变化投影一条记忆进记忆本（零 LLM）。

    - 只在「尚无该槽 active 的 global_sync 记忆」时写，避免每日/每轮重复投影；
    - source='global_sync'（非 chat，extractor/dedup/ai_rating 不会当普通聊天记忆二次处理）；
    - skip_dedup=True，失败静默。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("cross_char_fact_projection", False):
            return
        async with async_session_factory() as db:
            dup = (await db.execute(
                select(Memory.id).where(
                    Memory.character_id == character_id,
                    Memory.memory_type == "user_info",
                    Memory.source == "global_sync",
                    Memory.sub_type == slot,
                    Memory.status == ACTIVE,
                    Memory.content.ilike(f"%{new_value[:6]}%"),
                )).first())
            if dup is not None:
                return
        label = MUTABLE_SLOTS.get(slot, (slot,))[0]
        from datetime import datetime, timezone
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        content = f"用户当前{label}：{new_value}（{date} 更新）"
        from app.memory import save_memory
        await save_memory(
            user_id=user_id, character_id=character_id, memory_type="user_info",
            content=content[:100], importance=3, source="global_sync", sub_type=slot,
            epistemic_status="FACT", skip_dedup=True,
        )
    except Exception:
        pass


async def align_character_to_user_facts(character_id: int, user_id: int) -> dict:
    """惰性对齐：某角色「下次被激活（构建上下文前）」调用，一次轻量查询。

    - 只处理发生过更替的槽位（previous_value 非空）；
    - 返回各槽命中数（供观测）；失败静默返回空 dict。
    """
    report: dict[str, int] = {}
    try:
        for f in await get_active_user_facts(user_id):
            if not f.previous_value:
                continue
            n = await stale_character_slot_memory(character_id, f.slot, f.previous_value)
            report[f.slot] = n
            await _maybe_project_user_fact(character_id, user_id, f.slot, f.value)
    except Exception:
        pass
    return report


async def sweep_all_characters_alignment(user_id: int, per_char_cap: int = 0) -> int:
    """定时对齐：遍历该用户全部角色做同样的事，覆盖长期不活跃角色。返回处理角色数。

    - 内部幂等（已是 stale 的不再命中），重复跑零副作用；
    - ``per_char_cap>0`` 时最多处理前 N 个角色（防大库单次过久）；0=不设上限（默认，全量对齐）；
    - 失败静默返回 0。
    """
    from app.models.character import AICharacter
    total = 0
    try:
        async with async_session_factory() as db:
            char_ids = [r for (r,) in (await db.execute(
                select(AICharacter.id).where(AICharacter.user_id == user_id)
            )).all()]
        if per_char_cap > 0:
            char_ids = char_ids[:per_char_cap]
        for cid in char_ids:
            await align_character_to_user_facts(cid, user_id)
            total += 1
    except Exception:
        pass
    return total
