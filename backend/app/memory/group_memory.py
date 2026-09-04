# -*- coding: utf-8 -*-
"""#72 群共享长期记忆（子库）：本地聚合写入 + 按群读取；不走向量、失败静默。

范式对齐 games/memory_bridge：群"共同经历"细节存 group_memories（一份/群），
角色主 memories 只滚动保留少量 group_summary 摘要指针。

本模块只提供读写原语（PR-B），不接 _save_group_memory 双轨（那属 PR-C）；
group_cognition_v2 总开关彼时在 agent/loop.py AGENT_FLAGS 登记，默认关——
此处仅读取该 flag（缺省 False），异常一律视为关（零行为变化）。
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.chat import GroupMemory
from app.utils.logger import get_logger

_logger = get_logger("memory.group_memory")

_SUMMARY_KEEP_PER_GROUP = 3      # 每角色每群主记忆只留最近 3 条 group_summary 指针
_LONGTERM_LIMIT = 6              # 群生成时注入的长期群记忆条数上限


def group_cognition_on() -> bool:
    """总开关：默认关。读 AGENT_FLAGS 真值源，异常一律 False（零行为变化）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("group_cognition_v2", False))
    except Exception:
        return False


async def append_group_event(
    *, group_id: int, user_id: int, round_id: str | None,
    user_content: str, replies: list[dict], name_map: dict[int, str],
) -> None:
    """一轮群聊 → 1 条群共享事件（本地规则聚合，零 LLM）。

    replies: [{"character_id":..,"content":..}]（与 _generate_replies 返回同构）。
    开关关 / 无内容 / 异常 → 直接返回，不影响主链路。
    """
    if not group_cognition_on():
        return
    try:
        lines = []
        u = (user_content or "").strip()[:100]
        if u:
            lines.append(f"用户：{u}")
        for r in replies or []:
            cid = r.get("character_id")
            txt = (r.get("content") or "").strip()[:80]
            if cid and txt:
                lines.append(f"{name_map.get(cid, '角色')}：{txt}")
        if not lines:
            return
        async with async_session_factory() as db:
            db.add(GroupMemory(
                group_id=group_id, user_id=user_id, round_id=round_id,
                speaker_type="system", speaker_id=None,
                content="；".join(lines)[:600],
                epistemic_status="FACT", importance=40,
            ))
            await db.commit()
    except Exception as e:
        _logger.warning("append_group_event failed group=%s: %s", group_id, e)


async def recall_group_longterm(group_id: int, limit: int = _LONGTERM_LIMIT) -> list[str]:
    """取本群长期共同记忆（跨天，按时间倒序取若干条后正序展示）；非本群调用方拿不到。"""
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(GroupMemory)
                .where(GroupMemory.group_id == group_id)
                .order_by(GroupMemory.id.desc()).limit(limit)
            )).scalars().all()
        return [f"[{str(r.created_at)[:10]}] {r.content}" for r in reversed(rows)]
    except Exception as e:
        _logger.warning("recall_group_longterm failed group=%s: %s", group_id, e)
        return []


async def trim_group_summary_pointers(db, character_id: int, group_id: int,
                                      keep: int = _SUMMARY_KEEP_PER_GROUP) -> None:
    """每角色每群只留最近 keep 条 group_summary 指针，超出软删（is_archived=True，可追溯不物理删）。"""
    try:
        from app.models.memory import Memory
        rows = (await db.execute(
            select(Memory).where(
                Memory.character_id == character_id,
                Memory.source == "group",
                Memory.sub_type == "group_summary",
                Memory.group_id == group_id,
            ).order_by(Memory.id.desc())
        )).scalars().all()
        for old in rows[keep:]:
            old.is_archived = True
    except Exception as e:
        _logger.warning("trim_group_summary_pointers failed char=%s group=%s: %s", character_id, group_id, e)
