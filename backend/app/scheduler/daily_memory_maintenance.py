from app.utils.timeutil import beijing_day_start_utc as _beijing_day_start_utc
"""日终记忆维护 v1（P0-5，2026-08-16）：23:00 后每天一次，限额节流、失败静默

1. 当日日摘要补生成：今天消息 >= DAILY_SUMMARY_MIN_MSGS 且尚无今日摘要的会话，LLM 生成摘要；
2. 低价值记忆合并/淘汰：复用 memory/dedup.deduplicate_memories 全量向量去重（零 LLM）；
3. 置顶摘要补生成：复用 memory/summary.summarize_memories（force=False，尊重 6h 节流，缺失才生成）。

Feature Flag：agent_daily_memory_maintenance（默认开，可一键关闭）。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.database import async_session_factory
from app.utils.logger import get_logger

_logger = get_logger("scheduler.daily_memory_maintenance")

DAILY_SUMMARY_MIN_MSGS = 20  # 今天消息数达到该值才补生成日摘要
MAX_SESSIONS_PER_RUN = 15  # 单次最多补生成的会话数（token 限额）
SUMMARY_PROMPT = (
    "请用中文概括以下聊天的核心内容，包括用户提到的个人信息、重要事件、偏好。回复在80字以内。\n"
    "时间规则：涉及日期写具体日期（如 2026-08-10 类格式），不要使用'今天/昨天/最近'等相对时间词。\n"
)


def _beijing_today_str() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


async def _active_characters() -> list[dict]:
    from app.scheduler.triggers import get_active_characters
    return await get_active_characters()


async def generate_today_summaries() -> int:
    """为今天消息足够但尚无日摘要的会话补生成日摘要；返回生成条数"""
    from app.agent.llm_client import chat_completion
    from app.models.character import AICharacter
    from app.models.chat_message import ChatMessage
    from app.models.chat_session import ChatSession
    from app.models.daily_summary import DailySummary

    today = _beijing_today_str()
    start_utc = _beijing_day_start_utc()
    done = 0
    try:
        async with async_session_factory() as db:
            sessions = (await db.execute(
                select(ChatSession).where(ChatSession.updated_at >= start_utc)
            )).scalars().all()
    except Exception as e:
        _logger.warning("Today summary sessions load failed: %s", e)
        return 0
    for s in sessions:
        if done >= MAX_SESSIONS_PER_RUN:
            break
        try:
            async with async_session_factory() as db:
                exists = (await db.execute(
                    select(DailySummary.id).where(
                        DailySummary.session_id == s.id,
                        DailySummary.summary_date == today,
                    )
                )).first()
                cnt = (await db.execute(
                    select(func.count()).select_from(ChatMessage).where(
                        ChatMessage.session_id == s.id,
                        ChatMessage.created_at >= start_utc,
                    )
                )).scalar() or 0
            if exists is not None or int(cnt) < DAILY_SUMMARY_MIN_MSGS:
                continue
            async with async_session_factory() as db:
                msgs = (await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == s.id, ChatMessage.created_at >= start_utc)
                    .order_by(ChatMessage.created_at.asc())
                    .limit(200)
                )).scalars().all()
                char = (await db.execute(
                    select(AICharacter).where(AICharacter.id == s.character_id)
                )).scalar_one_or_none()
            char_name = char.name if char else "AI"
            lines = [
                f"{'用户' if m.sender_type == 'user' else char_name}: {(m.content or '')[:200]}"
                for m in msgs
            ]
            day_text = "\n".join(lines)[:8000]
            summary = await chat_completion(
                messages=[{"role": "user", "content": SUMMARY_PROMPT + day_text}],
                max_tokens=512, temperature=0,
                task="memory", user_id=(char.user_id if char else 1),
            )
            summary = (summary or "").strip()[:200]
            if not summary:
                continue
            async with async_session_factory() as db:
                # OR IGNORE（审计第三批）：与 context_builder 补生成同键幂等，防并发/重复落两条日摘要
                from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
                await db.execute(_sqlite_insert(DailySummary).values(
                    session_id=s.id, summary_date=today, summary_text=summary,
                ).prefix_with("OR IGNORE"))
                await db.commit()
            done += 1
        except Exception:
            continue
    return done


async def run_dedup_light() -> int:
    """低价值记忆合并/淘汰：全量向量去重（零 LLM）；返回删除条数"""
    from app.memory.dedup import deduplicate_memories
    removed = 0
    try:
        chars = await _active_characters()
    except Exception as e:
        _logger.warning("Dedup chars load failed: %s", e)
        return 0
    for c in chars:
        try:
            removed += int(await deduplicate_memories(int(c.get("character_id") or 0)) or 0)
        except Exception:
            continue
    return removed


async def refresh_pinned_summaries() -> int:
    """置顶摘要补生成（force=False，尊重 6h 节流，缺失才生成）；返回新生成条数"""
    from app.memory.summary import summarize_memories
    done = 0
    try:
        chars = await _active_characters()
    except Exception as e:
        _logger.warning("Pinned refresh chars load failed: %s", e)
        return 0
    for c in chars:
        cid = int(c.get("character_id") or 0)
        for mt in ("user_info", "preference", "event", "insight"):
            try:
                r = await summarize_memories(cid, mt, force=False)
                if r.get("generated"):
                    done += 1
            except Exception:
                continue
    return done


async def run_daily_memory_maintenance() -> dict:
    """日终记忆维护主入口（23:00 后调度调用；Flag 关闭则跳过）"""
    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("agent_daily_memory_maintenance", True):
            return {"enabled": False}
    except Exception:
        pass
    out = {"summaries": 0, "dedup_removed": 0, "pinned_refreshed": 0}
    try:
        out["summaries"] = await generate_today_summaries()
    except Exception as e:
        _logger.warning("Daily summary maintenance failed: %s", e)
    try:
        out["dedup_removed"] = await run_dedup_light()
    except Exception as e:
        _logger.warning("Daily dedup failed: %s", e)
    try:
        out["pinned_refreshed"] = await refresh_pinned_summaries()
    except Exception as e:
        _logger.warning("Pinned summary refresh failed: %s", e)
    _logger.info("Daily memory maintenance done: %s", out)
    return out
