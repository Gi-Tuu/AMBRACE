"""记忆置顶摘要：按类型 LLM 概括最近记忆（6 小时节流）"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger
from app.memory.constants import SUMMARY_TTL_HOURS, _TYPE_CN

_logger = get_logger("memory.summary")


_OVERRIDE_NOW = None


def _rel_time(dt, now=None) -> str:
    """相对时间中文描述（今天/昨天/N天前/周前/月前/很久以前）"""
    if dt is None:
        return "时间不明"
    dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    days = (now - dt).total_seconds() / 86400.0
    if days < 1:
        return "今天"
    if days < 2:
        return "昨天"
    if days < 7:
        return f"{int(days)}天前"
    if days < 30:
        return f"{int(days // 7)}周前"
    if days < 365:
        return f"{int(days // 30)}个月前"
    return "很久以前"


async def summarize_memories(character_id: int, memory_type: str, force: bool = False) -> dict:
    """生成/更新某类型的置顶摘要记忆（is_pinned=1）。默认 6 小时内不重复生成；force=True 强制重新生成。"""
    from datetime import datetime, timezone, timedelta
    from app.agent.llm_client import chat_completion
    from app.models.character import AICharacter

    label = _TYPE_CN.get(memory_type, memory_type)
    async with async_session_factory() as db:
        # 已有置顶摘要 → 节流判断
        existing_result = await db.execute(
            select(Memory).where(
                Memory.character_id == character_id,
                Memory.memory_type == memory_type,
                Memory.is_pinned == True,
                Memory.is_archived == False,
            )
        )
        existing = existing_result.scalars().all()
        if existing and not force:
            newest = max(existing, key=lambda m: m.updated_at or m.created_at)
            last = newest.updated_at or newest.created_at
            if isinstance(last, datetime):
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last < timedelta(hours=SUMMARY_TTL_HOURS):
                    return {"generated": False, "memory_id": newest.id, "reason": "throttled"}

        # 最近 20 条该类型非摘要记忆
        result = await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.memory_type == memory_type,
                Memory.is_pinned == False,
                Memory.is_archived == False,
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(20)
        )
        memories = result.scalars().all()
        if not memories:
            return {"generated": False, "memory_id": None, "reason": "no_memories"}

        char = await db.get(AICharacter, character_id)
        char_name = char.name if char else "AI"
        # 2026-08-08 时间逻辑修复：每条记忆标注相对发生时间，防止把旧事写成"今天/最近"
        contents = "\n".join(f"- {m.content[:100]}（{_rel_time(m.created_at)}）" for m in memories)
        prompt = (
            f"你是{char_name}。以下是关于用户的若干条记忆条目（{label}类），括号内是各条目发生的时间：\n{contents}\n\n"
            f"请用1-2句话概括出当下最重要、最值得记住的内容，作为该类型的置顶提炼，"
            f"要求信息凝练、口语化、以AI自己的视角。忠实于记忆条目本身，不要引入条目中没有的性别代词或异性恋假设。"
            f"时间规则（重要）：必须按条目标注的时间准确描述——标注'很久以前'或'N个月前/N周前'的旧事，"
            f"禁止使用'今天/昨天/最近'等时间词；时间不明确时用'之前/某次'等中性表述；不要编造具体日期。"
            f"直接输出概括内容，不要加序号和引号。"
        )
        response = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200,
            task="memory", user_id=(char.user_id if char else 1),
        )
        summary = (response or "").strip().strip('"').strip("'")
        if len(summary) < 4:
            return {"generated": False, "memory_id": None, "reason": "empty_output"}

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        owner_user_id = char.user_id if char else 0
        if existing:
            target = existing[0]
            target.content = summary
            target.importance = 100.0
            target.user_id = owner_user_id
            target.updated_at = now_naive
            await db.commit()
            return {"generated": True, "memory_id": target.id}
        mem = Memory(
            user_id=owner_user_id, character_id=character_id, memory_type=memory_type,
            sub_type="summary", source="summary", content=summary,
            importance=100.0, is_pinned=True,
        )
        db.add(mem)
        await db.commit()
        await db.refresh(mem)
        _logger.info("Pinned summary generated for char=%d type=%s: %.40s", character_id, memory_type, summary)
        return {"generated": True, "memory_id": mem.id}


# ── 记忆架构 v2.1 Phase 5：身份画像提炼（复用置顶摘要模式，24h 节流）──
IDENTITY_TTL_HOURS = 24
IDENTITY_SUB_TYPE = "identity"


async def summarize_identity(character_id: int, user_id: int, force: bool = False) -> dict:
    """生成/刷新「身份画像」置顶记忆（memory_type=user_info, sub_type=identity, is_pinned=1）。

    输入：user_info 类记忆 + 意义记忆（why_it_matters 非空）最近 20 条；
    输出：1-2 句 AI 第一人称的长期用户画像（价值/动机/性格模式），24 小时节流。
    """
    from datetime import datetime, timezone, timedelta
    from app.agent.llm_client import chat_completion
    from app.models.character import AICharacter

    # 记忆架构 v2.1 开关：关闭时不生成/刷新身份画像（灰度语义）
    from app.memory.flags import memory_v2_enabled
    if not await memory_v2_enabled(character_id):
        return {"generated": False, "memory_id": None, "reason": "disabled"}

    async with async_session_factory() as db:
        existing_result = await db.execute(
            select(Memory).where(
                Memory.character_id == character_id,
                Memory.memory_type == "user_info",
                Memory.sub_type == IDENTITY_SUB_TYPE,
                Memory.is_pinned == True,
                Memory.is_archived == False,
            )
        )
        existing = existing_result.scalars().all()
        if existing and not force:
            newest = max(existing, key=lambda m: m.updated_at or m.created_at)
            last = newest.updated_at or newest.created_at
            if isinstance(last, datetime):
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last < timedelta(hours=IDENTITY_TTL_HOURS):
                    return {"generated": False, "memory_id": newest.id, "reason": "throttled"}

        rows = (await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_archived == False,
                Memory.is_pinned == False,
                Memory.memory_type == "user_info",
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(20)
        )).scalars().all()
        meaning_rows = (await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_archived == False,
                Memory.why_it_matters.is_not(None),
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(20)
        )).scalars().all()
        if not rows and not meaning_rows:
            return {"generated": False, "memory_id": None, "reason": "no_memories"}

        char = await db.get(AICharacter, character_id)
        char_name = char.name if char else "AI"
        parts = [f"- {m.content[:100]}（{_rel_time(m.created_at)}）" for m in rows]
        parts += [f"- {m.content[:100]}（意义：{m.why_it_matters[:60]}；{_rel_time(m.created_at)}）" for m in meaning_rows]
        contents = "\n".join(parts[:24])
        prompt = (
            f"你是{char_name}。以下是你长期观察用户积累的信息（印象 + 意义），括号内是发生时间：\n{contents}\n\n"
            "请用1-2句话概括用户的长期身份画像：他/她是什么样的人、最看重什么、行为模式如何。"
            "要求凝练、口语化、以AI自己的视角，忠实于条目，不要引入条目中没有的性别代词或异性恋假设。"
            "时间规则：身份画像是长期总结，避免使用'今天/最近'等短时间词；旧条目用'之前/以往'等中性表述。"
            "直接输出概括内容，不要加序号和引号。"
        )
        response = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200,
            task="memory", user_id=user_id,
        )
        summary = (response or "").strip().strip('"').strip("'")
        if len(summary) < 4:
            return {"generated": False, "memory_id": None, "reason": "empty_output"}

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        owner_user_id = char.user_id if char else 0
        if existing:
            target = existing[0]
            target.content = summary
            target.importance = 100.0
            target.user_id = owner_user_id
            target.updated_at = now_naive
            await db.commit()
            return {"generated": True, "memory_id": target.id}
        mem = Memory(
            user_id=owner_user_id, character_id=character_id, memory_type="user_info",
            sub_type=IDENTITY_SUB_TYPE, source="summary", content=summary,
            importance=100.0, is_pinned=True,
        )
        db.add(mem)
        await db.commit()
        await db.refresh(mem)
        _logger.info("Identity summary generated for char=%d: %.40s", character_id, summary)
        return {"generated": True, "memory_id": mem.id}
