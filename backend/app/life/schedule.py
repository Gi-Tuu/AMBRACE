"""AI 日程服务（Phase B-2，2026-08-14，演进规划 v2）

三来源（零增量 LLM 成本）：
- fixed_routine 固定作息：每日 07:00 起床 / 12:30 午休 / 23:00 睡觉（规则生成）
- goal_derived Goal 推导：有 deadline 的 active Goal，deadline 前 1 天生成准备日程
- ai_generated AI 自生成：reflect 活动顺手输出 [SCHEDULE] YYYY-MM-DD HH:MM 标题 [/SCHEDULE]

状态机：scheduled → active（时间到）→ completed（结束视为完成）/ overdue（低优先级超时）
约束：每角色活跃（scheduled+active）≤5 条；不单独推送，只在对话/回归摘要自然提及。
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.life import LifeGoal, LifeSchedule
from app.utils.logger import get_logger

_logger = get_logger("life.schedule")

FIXED_ROUTINES = [
    {"title": "起床", "time": "07:00", "priority": 1},
    {"title": "午休", "time": "12:30", "priority": 1},
    {"title": "睡觉", "time": "23:00", "priority": 1},
]
MAX_ACTIVE = 5          # 每角色活跃日程上限
GOAL_LEAD_DAYS = 1      # deadline 前 1 天生成准备日程
DEFAULT_DURATION_MIN = 60

_SCHEDULE_RE = re.compile(r"\[SCHEDULE\]\s*(.*?)\s*\[/SCHEDULE\]", re.S)


def _beijing_to_utc_naive(y: int, mo: int, d: int, hh: int, mm: int) -> datetime:
    bj = datetime(y, mo, d, hh, mm, tzinfo=timezone(timedelta(hours=8)))
    return bj.astimezone(timezone.utc).replace(tzinfo=None)


def parse_schedule_time(s: str) -> datetime | None:
    """解析 'YYYY-MM-DD HH:MM' 或 'HH:MM'（北京时间）→ UTC naive；缺日期=明天"""
    s = (s or "").strip()
    from app.utils.timeutil import now_naive_utc
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})$", s)
    if m:
        return _beijing_to_utc_naive(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]))
    m2 = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m2:
        bj = now_naive_utc() + timedelta(hours=8)
        target = datetime(bj.year, bj.month, bj.day, int(m2[1]), int(m2[2]))
        if target <= bj:
            target += timedelta(days=1)
        return _beijing_to_utc_naive(target.year, target.month, target.day, int(m2[1]), int(m2[2]))
    return None


def extract_schedule_mark(text: str) -> tuple[str, dict | None]:
    """解析 [SCHEDULE] YYYY-MM-DD HH:MM 标题 [/SCHEDULE]；返回 (剥离后文本, 日程dict或None)"""
    if not text:
        return text or "", None
    m = _SCHEDULE_RE.search(text)
    if not m:
        return text, None
    clean = _SCHEDULE_RE.sub("", text).rstrip()
    raw = m.group(1).strip()
    # 时间 = 开头 YYYY-MM-DD HH:MM 或 HH:MM（标题可含空格）
    tm = re.match(r"^(\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}|\d{1,2}:\d{2})\s+(.*)$", raw, re.S)
    if not tm:
        return clean, None
    start = parse_schedule_time(tm.group(1))
    if start is None:
        return clean, None
    title = tm.group(2).strip()[:120]
    if not title:
        return clean, None
    return clean, {
        "title": title,
        "start_time": start,
        "end_time": start + timedelta(minutes=DEFAULT_DURATION_MIN),
    }


async def _active_count(db, character_id: int) -> int:
    rows = await db.execute(
        select(LifeSchedule).where(
            LifeSchedule.character_id == character_id,
            LifeSchedule.status.in_(["scheduled", "active"]),
        )
    )
    return len(rows.scalars().all())


async def create_schedule(db, user_id: int, character_id: int, title: str, start_time: datetime,
                          end_time: datetime | None = None, priority: int = 2, source: str = "ai_generated",
                          source_goal_id: int | None = None, recurrence: str | None = None,
                          description: str = "") -> LifeSchedule | None:
    if await _active_count(db, character_id) >= MAX_ACTIVE:
        _logger.info("schedule create skipped char=%d: active limit %d", character_id, MAX_ACTIVE)
        return None
    s = LifeSchedule(
        user_id=user_id, character_id=character_id, title=title[:120],
        description=(description or "")[:500], start_time=start_time,
        end_time=end_time or (start_time + timedelta(minutes=DEFAULT_DURATION_MIN)),
        priority=priority, source=source, source_goal_id=source_goal_id, recurrence=recurrence,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def ensure_fixed_routines(db, character_id: int, user_id: int) -> int:
    """每日固定作息（北京时间）：生成当天尚未创建的作息；已过的时间不补（明天自然生成）"""
    from app.utils.timeutil import now_naive_utc
    now = now_naive_utc()
    bj = now + timedelta(hours=8)
    created = 0
    for rt in FIXED_ROUTINES:
        hh, mm = (int(x) for x in rt["time"].split(":"))
        start = _beijing_to_utc_naive(bj.year, bj.month, bj.day, hh, mm)
        if start <= now:
            continue
        exist = await db.execute(
            select(LifeSchedule).where(
                LifeSchedule.character_id == character_id,
                LifeSchedule.recurrence == "daily",
                LifeSchedule.title == rt["title"],
                LifeSchedule.start_time >= start - timedelta(minutes=30),
                LifeSchedule.start_time < start + timedelta(minutes=30),
            )
        )
        if exist.scalar_one_or_none() is None:
            await create_schedule(db, user_id, character_id, rt["title"], start,
                                  priority=rt["priority"], source="fixed_routine", recurrence="daily")
            created += 1
    return created


async def ensure_goal_derived(db, character_id: int, user_id: int) -> int:
    """有 deadline 的 active Goal：deadline 前 1 天生成准备日程（每目标 ≤1 条活跃）"""
    from app.utils.timeutil import now_naive_utc
    now = now_naive_utc()
    goals = (
        await db.execute(
            select(LifeGoal).where(
                LifeGoal.character_id == character_id,
                LifeGoal.status == "active",
                LifeGoal.deadline.is_not(None),
            )
        )
    ).scalars().all()
    created = 0
    for g in goals:
        dl = g.deadline.replace(tzinfo=None) if g.deadline and g.deadline.tzinfo else g.deadline
        if dl is None:
            continue
        lead = dl - timedelta(days=GOAL_LEAD_DAYS)
        if not (lead - timedelta(hours=24) <= now <= dl):
            continue
        exist = await db.execute(
            select(LifeSchedule).where(
                LifeSchedule.character_id == character_id,
                LifeSchedule.source == "goal_derived",
                LifeSchedule.source_goal_id == g.id,
                LifeSchedule.status.in_(["scheduled", "active"]),
            )
        )
        if exist.scalar_one_or_none() is None:
            await create_schedule(
                db, user_id, character_id, f"准备：{g.title[:100]}",
                _beijing_to_utc_naive(lead.year, lead.month, lead.day, 9, 0), priority=2,
                source="goal_derived", source_goal_id=g.id, description=g.description or "",
            )
            created += 1
    return created


async def advance_schedules(db, character_id: int) -> int:
    """状态流转：scheduled → active（时间到）；到结束时间 → completed（低优先级固定作息超时 → overdue）"""
    from app.utils.timeutil import now_naive_utc
    now = now_naive_utc()
    rows = (
        await db.execute(
            select(LifeSchedule).where(
                LifeSchedule.character_id == character_id,
                LifeSchedule.status.in_(["scheduled", "active"]),
            )
        )
    ).scalars().all()
    changed = 0
    for s in rows:
        st = s.start_time.replace(tzinfo=None) if s.start_time and s.start_time.tzinfo else s.start_time
        et = s.end_time.replace(tzinfo=None) if s.end_time and s.end_time.tzinfo else s.end_time
        et = et or (st + timedelta(minutes=DEFAULT_DURATION_MIN))
        if s.status == "scheduled" and now >= st:
            s.status = "active"
            changed += 1
        if now >= et + timedelta(minutes=15):
            if s.priority <= 1 and s.source == "fixed_routine":
                s.status = "overdue"
            else:
                s.status = "completed"
            s.completed_at = now
            changed += 1
    if changed:
        await db.commit()
    return changed


async def schedule_tick(db, character_id: int, user_id: int) -> dict:
    """LifeTick 入口：固定作息生成 + Goal 推导 + 状态流转"""
    created_r = await ensure_fixed_routines(db, character_id, user_id)
    created_g = await ensure_goal_derived(db, character_id, user_id)
    changed = await advance_schedules(db, character_id)
    if created_r or created_g or changed:
        _logger.info("schedule tick char=%d: routines=%d goal_derived=%d state_changed=%d",
                     character_id, created_r, created_g, changed)
    return {"routines": created_r, "goal_derived": created_g, "state_changed": changed}


async def list_schedules(db, character_id: int, date_str: str | None = None, limit: int = 30) -> list[LifeSchedule]:
    """查询：未完成优先 + 开始时间倒序；可选按北京时间日期过滤"""
    q = select(LifeSchedule).where(LifeSchedule.character_id == character_id)
    if date_str:
        try:
            y, mo, d = (int(x) for x in date_str.split("-"))
            lo = _beijing_to_utc_naive(y, mo, d, 0, 0)
            # 月末不直接 d+1（跨月/闰年会 ValueError），改为当天零点加一天再取分量
            from datetime import datetime as _dt, timedelta as _td
            _next = _dt(y, mo, d) + _td(days=1)
            hi = _beijing_to_utc_naive(_next.year, _next.month, _next.day, 0, 0)
            q = q.where(LifeSchedule.start_time >= lo, LifeSchedule.start_time < hi)
        except Exception:
            pass
    q = q.order_by(LifeSchedule.start_time.desc()).limit(max(1, min(limit, 100)))
    return list((await db.execute(q)).scalars().all())
