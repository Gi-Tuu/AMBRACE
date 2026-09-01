"""时光页 · 共同回忆：按角色聚合"认识天数 + 关键节点"时间线。

全部为程序化统计（零 LLM 调用）：最早会话/祝福消息关键词/宠物领养/重要记忆精选。
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.chat import ChatSession
from app.models.chat import ChatMessage
from app.models.pet import Pet
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.life import TimelineEvent
from app.utils.logger import get_logger

_logger = get_logger("timeline")

# 祝福关键词 -> 对应节日名（节日对齐：消息日期必须是该节日才计入）
_BLESSING_HOLIDAY_MAP = {
    "新年快乐": "元旦", "元旦快乐": "元旦",
    "圣诞快乐": "圣诞节",
    "春节快乐": "春节",
    "中秋快乐": "中秋节",
}


def _birthday_mmdd(birthday: str | None) -> str | None:
    """用户生日归一化为 MM-DD（兼容 MM-DD 与 YYYY-MM-DD 两种存储格式）"""
    if not birthday:
        return None
    s = birthday.strip()
    if len(s) == 5 and s[2] == "-":
        return s
    if len(s) >= 10 and s[4] == "-":
        return s[5:10]
    return None


def _blessing_matches(content: str, msg_dt, user_birthday: str | None) -> bool:
    """祝福消息是否与消息日期对齐：生日按用户生日，节日按当日节日表（含农历）"""
    from app.scheduler.holiday_calendar import get_holidays
    mmdd = (msg_dt + timedelta(hours=8)).strftime("%m-%d")
    if "生日快乐" in content:
        b = _birthday_mmdd(user_birthday)
        return b is not None and mmdd == b
    for kw, holiday_name in _BLESSING_HOLIDAY_MAP.items():
        if kw in content:
            d = (msg_dt + timedelta(hours=8)).date()
            holidays = get_holidays(d)
            return any(h["name"] == holiday_name for h in holidays)
    return False

# 重要事件记忆：高 importance 的 event / 关系类 insight
_IMPORTANT_TYPES = (("insight", "relationship"), ("event", "extracted"), ("event", None))
_IMPORTANT_MIN_IMPORTANCE = 60  # 百分比（3 星及以上）
_IMPORTANT_LIMIT = 12


def _beijing_date(dt: datetime | None) -> str | None:
    """UTC naive -> 北京时间日期字符串（YYYY-MM-DD）"""
    if not dt:
        return None
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d")


_MILESTONE_LIMIT = 12


async def generate_milestones(user_id: int, character_id: int) -> dict:
    """离线大事记精选：从重要记忆挑 5-8 个代表性时刻，LLM 生成标题+描述并落库。

    幂等：该角色已有大事记则直接返回现有，不重复花 token；force 时重新生成。
    """
    from app.agent.llm_client import chat_completion

    async with async_session_factory() as db:
        char = await db.get(AICharacter, character_id)
        if not char or char.user_id != user_id:
            return {"error": "角色不存在"}
        existing = (
            await db.execute(
                select(TimelineEvent).where(TimelineEvent.character_id == character_id)
            )
        ).scalars().all()
        if existing:
            return {
                "generated": False,
                "count": len(existing),
                "events": [_ev_to_item(e) for e in existing],
            }
        # 候选：高 importance 的事件/关系记忆
        from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
        result = await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_archived == False,
                Memory.importance >= 60,
                _active_status_clause(),
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(_MILESTONE_LIMIT)
        )
        mems = result.scalars().all()
        char_name = char.name

    cands = []
    for m in mems:
        if m.memory_type not in ("event", "insight"):
            continue
        if m.memory_type == "insight" and m.sub_type not in ("relationship", None):
            continue
        content = (m.content or "").strip()[:80]
        if not content:
            continue
        d = _beijing_date(m.created_at)
        cands.append(f"- {d}: {content}")
    if not cands:
        return {"generated": False, "count": 0, "events": []}

    prompt = (
        f"你是{char_name}，请从下面与用户的回忆条目中，挑出 5-8 个最有纪念意义的时刻，"
        f"为每个时刻写 1 个简短标题（≤12字）和 1 句描述（≤30字），情感自然。\n\n"
        f"回忆条目（日期: 内容）：\n" + "\n".join(cands) +
        "\n\n直接输出 JSON 数组，不要其他文字："
        '[{"date":"YYYY-MM-DD","title":"标题","desc":"描述"}]'
    )
    try:
        response = await chat_completion(
            messages=[{"role": "system", "content": "你是一个细腻的朋友，正在整理共同回忆。只输出 JSON。"},
                      {"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=800,
            task="timeline", user_id=user_id,
        )
    except Exception as e:
        _logger.warning("Milestone generation failed char=%d: %s", character_id, e)
        return {"generated": False, "count": 0, "events": []}

    events = _parse_milestones(response)
    if not events:
        return {"generated": False, "count": 0, "events": []}

    async with async_session_factory() as db:
        for ev in events:
            db.add(TimelineEvent(
                character_id=character_id,
                event_date=ev["date"],
                title=ev["title"][:50],
                desc=ev["desc"][:200],
                kind="milestone",
            ))
        await db.commit()
    _logger.info("Milestones generated char=%d count=%d", character_id, len(events))
    return {"generated": True, "count": len(events), "events": events}


def _parse_milestones(response: str) -> list[dict]:
    """容错解析 LLM 输出的 JSON 数组"""
    import json
    text = (response or "").strip()
    try:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            return []
        data = json.loads(text[start:end + 1])
        out = []
        for item in data if isinstance(data, list) else []:
            d = str(item.get("date") or "").strip()
            t = str(item.get("title") or "").strip()
            desc = str(item.get("desc") or "").strip()
            if d and t:
                out.append({"date": d, "title": t, "desc": desc})
        return out
    except Exception:
        return []


def _ev_to_item(e) -> dict:
    return {
        "date": e.event_date,
        "type": "milestone",
        "title": e.title,
        "desc": e.desc,
    }


async def build_timeline(user_id: int, character_id: int) -> dict:
    async with async_session_factory() as db:
        char = await db.get(AICharacter, character_id)
        if not char or char.user_id != user_id:
            return {"error": "角色不存在"}
        char_name = char.name
        from app.models.user import User
        user = await db.get(User, user_id)
        user_birthday = user.birthday if user else None

        # 该角色全部活跃会话（最早即首次聊天）
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.character_id == character_id)
            .order_by(ChatSession.created_at.asc())
        )
        sessions = result.scalars().all()

        # 祝福消息：该角色会话内 AI 发的含关键词消息
        if sessions:
            session_ids = [s.id for s in sessions]
            result = await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id.in_(session_ids),
                    ChatMessage.sender_type == "ai",
                )
                .order_by(ChatMessage.created_at.asc())
            )
            messages = result.scalars().all()
        else:
            messages = []

        # 宠物（家庭共享事件，按用户维度）
        result = await db.execute(
            select(Pet).where(Pet.user_id == user_id).order_by(Pet.created_at.asc())
        )
        pets = result.scalars().all()

        # 重要记忆精选
        from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
        result = await db.execute(
            select(Memory)
            .where(
                Memory.character_id == character_id,
                Memory.is_archived == False,
                Memory.importance >= _IMPORTANT_MIN_IMPORTANCE,
                _active_status_clause(),
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
        )
        all_mems = result.scalars().all()

    days_known = 0
    first_chat_at = None
    if sessions:
        first_chat_at = sessions[0].created_at
        days_known = max(1, (datetime.now(timezone.utc).replace(tzinfo=None) - first_chat_at).days + 1)

    items = []

    # 首次聊天
    if sessions and sessions[0].created_at:
        items.append({
            "date": _beijing_date(sessions[0].created_at),
            "type": "first_chat",
            "title": "第一次聊天",
            "desc": f"和 {char_name} 的第一段对话开始于这一天",
        })

    # 祝福（节日对齐：关键词 + 消息日期=对应节日/用户生日才计入，按日期去重保留首次）
    seen_dates = set()
    for m in messages:
        content = m.content or ""
        if not _blessing_matches(content, m.created_at, user_birthday):
            continue
        d = _beijing_date(m.created_at)
        if not d or d in seen_dates:
            continue
        seen_dates.add(d)
        keyword = next(
            (k for k in ("生日快乐", *_BLESSING_HOLIDAY_MAP.keys()) if k in content),
            "祝福",
        )
        items.append({
            "date": d,
            "type": "blessing",
            "title": keyword,
            "desc": f"{char_name} 在{keyword}这天送上了祝福",
        })

    # 宠物领养（家庭事件）
    for pet in pets:
        d = _beijing_date(pet.created_at)
        if d:
            items.append({
                "date": d,
                "type": "pet",
                "title": f"领养了{pet.name}",
                "desc": f"家里多了一位新成员：{pet.name}",
            })

    # 重要事件（按日期去重，保留 importance 高者）
    mem_seen = set()
    mem_count = 0
    for m in all_mems:
        if mem_count >= _IMPORTANT_LIMIT:
            break
        is_target = (
            (m.memory_type, m.sub_type) in _IMPORTANT_TYPES
            or m.memory_type == "event"
        )
        if not is_target:
            continue
        d = _beijing_date(m.created_at)
        if not d or d in mem_seen:
            continue
        mem_seen.add(d)
        mem_count += 1
        content = (m.content or "").strip()
        if content.startswith("关系:"):
            content = content[3:].strip()
        elif content.startswith("自述:"):
            content = content[3:].strip()
        items.append({
            "date": d,
            "type": "memory",
            "title": content[:20] or "重要时刻",
            "desc": content[:80],
            "importance": int(m.importance or 0),
        })

    # 合并离线大事记（type=milestone）
    async with async_session_factory() as db:
        result = await db.execute(
            select(TimelineEvent).where(TimelineEvent.character_id == character_id)
        )
        events = result.scalars().all()
    has_milestones = bool(events)
    for e in events:
        items.append(_ev_to_item(e))

    # 有 milestone 的日期，隐藏同日普通 memory 精选（milestone 是升级版）
    milestone_dates = {it["date"] for it in items if it["type"] == "milestone"}
    items = [it for it in items if not (it["type"] == "memory" and it["date"] in milestone_dates)]

    # 同一天排序：first_chat 优先，其次 milestone/blessing/pet/memory
    items.sort(key=lambda x: (x["date"] or "", 0), reverse=True)
    return {
        "character_id": character_id,
        "character_name": char_name,
        "days_known": days_known,
        "first_chat_at": first_chat_at.isoformat() if first_chat_at else None,
        "has_milestones": has_milestones,
        "items": items,
    }
