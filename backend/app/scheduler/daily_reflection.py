from app.utils.timeutil import beijing_day_start_utc as _beijing_day_start_utc
"""周复盘（Phase J，2026-08-16 起为周复盘）：Agent 自我反思与规划

- 每 REFLECT_INTERVAL_DAYS 天（23:00 后触发）对启用角色生成一次「周复盘」：本周工具使用/任务进展 → LLM 总结 + 下周计划
- 复盘沉淀为记忆（type=ai_reflection，importance=6，source=reflection）——AI 之后可自然想起
  （反思驱动雏形：高权重记忆参与后续检索，主动消息可自然延续计划）
- 写 agent_task_logs（trigger=reflection）统一可观测；失败静默
- Feature Flag agent_daily_reflection（2026-08-17 起全量默认开，开源包基线）
"""
import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.database import async_session_factory
from app.utils.logger import get_logger

_logger = get_logger("scheduler.daily_reflection")

REFLECT_INTERVAL_DAYS = 7  # 复盘间隔（天）：最近 N 天内已复盘则跳过（2026-08-16 用户拍板改周复盘，每周一次）


async def _used_recently(character_id: int) -> bool:
    """最近 REFLECT_INTERVAL_DAYS 天内（含今天，北京日界）该角色是否已生成过复盘（2026-08-16 审计：改查记忆表，判据与沉淀动作一致，不再依赖 fire-and-forget trace）"""
    try:
        async with async_session_factory() as db:
            from app.models.memory import Memory
            n = (await db.execute(
                select(func.count()).where(
                    Memory.memory_type == "ai_reflection",
                    Memory.character_id == character_id,
                    Memory.created_at >= _beijing_day_start_utc() - timedelta(days=REFLECT_INTERVAL_DAYS - 1),
                )
            )).scalar() or 0
            return int(n) >= 1
    except Exception:
        return True  # 查询失败视为已用，避免重复尝试


async def _collect_week_data(character_id: int) -> str:
    """收集最近一周可观测数据（工具执行/任务），返回汇总文本；失败返回空串"""
    try:
        lines = []
        async with async_session_factory() as db:
            from app.models.agent_task_log import AgentTaskLog
            from app.models.agent_task import AgentTask
            logs = (await db.execute(
                select(AgentTaskLog)
                .where(
                    AgentTaskLog.character_id == character_id,
                    AgentTaskLog.created_at >= _beijing_day_start_utc() - timedelta(days=REFLECT_INTERVAL_DAYS - 1),
                )
                .order_by(AgentTaskLog.id.asc())
                .limit(20)
            )).scalars().all()
            for lg in logs:
                _steps = (lg.steps_json or "")[:150]
                lines.append(f"- [{lg.trigger}/{lg.route}] {_steps}")
            tasks = (await db.execute(
                select(AgentTask)
                .where(
                    AgentTask.character_id == character_id,
                    AgentTask.created_at >= _beijing_day_start_utc() - timedelta(days=REFLECT_INTERVAL_DAYS - 1),
                )
                .order_by(AgentTask.id.desc())
                .limit(5)
            )).scalars().all()
            for tk in tasks:
                lines.append(f"- 任务「{tk.goal}」→ {tk.status}")
        return "\n".join(lines)
    except Exception as e:
        _logger.warning("Reflection data collect failed char=%d: %s", character_id, e)
        return ""


def _build_prompt(character_name: str, data: str, today: str | None = None) -> str:
    """构建复盘提示（纯函数，便于测试）；today=北京时间今天（YYYY-MM-DD）"""
    _today_line = f"今天是{today}。" if today else ""
    return (
        f"你是{character_name}，{_today_line}现在是一周的尾声。请像写私人手记一样，用 1-2 段话（≤180 字）写本周复盘：\n"
        "1. 过去 7 天你做了哪些事（基于下面的活动记录，没有就不提）；\n"
        "2. 有什么感受/想法（符合你的性格，可带情绪）；\n"
        "3. 接下来想做/打算做的一两件事（自然、具体，不要说空话）。\n"
        "不要用列表格式，不要提'AI/复盘/系统'等字眼，不要编造没有发生的具体事件。\n"
        "时间规则：涉及日期写具体日期（如 2026-08-10），不要用'这周/下周/最近'等相对时间词。\n\n"
        f"本周活动记录：\n{data or '（本周没有记录到特别的活动）'}"
    )


async def generate_daily_reflection(character_id: int, user_id: int | None = None) -> bool:
    """生成单角色周复盘：数据收集 → LLM 总结 → 沉淀记忆 + trace。返回是否成功。"""
    try:
        from app.agent import loop as _loop
        if not _loop.AGENT_FLAGS.get("agent_daily_reflection", False):
            return False
        if await _used_recently(character_id):
            return False
        async with async_session_factory() as db:
            from app.models.character import AICharacter
            char = await db.get(AICharacter, character_id)
        char_name = char.name if char else "我"
        data = await _collect_week_data(character_id)
        t0 = time.monotonic()
        from app.agent.llm_client import chat_completion
        content = (await chat_completion(
            messages=[
                {"role": "system", "content": "直接输出复盘内容，不要加引号和标注。"},
                {"role": "user", "content": _build_prompt(char_name, data, today=datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"))},
            ],
            temperature=0.85,
            max_tokens=256,
            task="reflection",
            user_id=user_id,
        ) or "").strip().strip('"').strip("'")
        if len(content) < 10:
            _logger.warning("Daily reflection too short char=%d", character_id)
            return False
        # 沉淀为记忆（AI 内心世界：之后可被检索自然想起）
        try:
            from app.memory import save_memory
            await save_memory(
                user_id=user_id, character_id=character_id,
                memory_type="ai_reflection", title="本周复盘",
                content=content, importance=5, source="reflection",  # 2026-08-16 审计修复：6 在 1-5 制下 pct=6 极低，改 5=100
                speaker_type="character", speaker_id=character_id,
                epistemic_status="FACT",
            )
        except Exception as e:
            _logger.warning("Reflection memory save failed char=%d: %s", character_id, e)
        # 可观测 trace
        try:
            from app.agent import trace as _trace
            _trace.enqueue_task_log(
                task_id=_trace.new_task_id(), character_id=character_id, user_id=user_id,
                trigger="reflection", route="daily_reflection",
                steps_json=json.dumps([{"action": "daily_reflection", "len": len(content)}], ensure_ascii=False),
                llm_calls=1, tool_calls=0,
                latency_ms=int((time.monotonic() - t0) * 1000),
                status="ok",
            )
        except Exception:
            pass
        _logger.info("Daily reflection generated char=%d len=%d", character_id, len(content))
        return True
    except Exception as e:
        _logger.warning("Daily reflection failed char=%d: %s", character_id, e)
        return False


async def run_daily_reflections() -> None:
    """对所有启用角色执行每日复盘（每角色独立，失败静默）"""
    try:
        from app.scheduler.triggers import get_active_characters
        chars = await get_active_characters()
    except Exception as e:
        _logger.warning("Daily reflections chars load failed: %s", e)
        return
    for c in chars:
        try:
            await generate_daily_reflection(
                int(c.get("character_id") or 0), c.get("user_id"),
            )
        except Exception:
            pass
