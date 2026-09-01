"""记忆可靠度评分 + 确认/纠正信号采集（World & Cognition P5，2026-08-15）

reliability = source_weight × 矛盾惩罚 × 确认加分 × 时间衰减

- source_weight：来源可信度（用户说的 1.0 > 系统 1.0 > Life 0.9 > 社交 0.85 > AI 表述 0.6 > 推断 0.4）
- 矛盾惩罚：1/(1+contradiction_count)，用户纠正一次明显下降
- 确认加分：平滑系数 0.7 + min(确认次数,3)*0.1（未确认不归零，确认越多越可信）
- 时间衰减：每天 -1%，最低 0.5

信号采集（$0 规则）：用户消息命中确认词 → 相关记忆 confirmation+1（复用 P1 晋升逻辑）；
命中纠正词 → contradiction+1、reliability 重算、FACT 降级 UNVERIFIED（下次对话自然少用/求证）。
范围（2026-08-15 用户拍板）：只做记忆可靠度增强，不做内容审查/违规重生成。
"""
import time
from datetime import datetime

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc as _now_naive

_logger = get_logger("memory.reliability")

# 确认 / 纠正关键词（中文日常口语，强信号才触发，避免误伤普通"对/没有"）
CONFIRM_WORDS = (
    "没错", "说得对", "对对对", "对呀对呀", "就是这样的", "就是这么回事",
    "你记得", "你记住了", "记得很清楚", "你记性真好", "对，就是这样", "对对",
)
CORRECT_WORDS = (
    "你记错", "记错了", "我说过吗", "我没说过", "不是这样的", "你搞错",
    "说错了", "不对不对", "不是的", "错啦", "弄反了", "你理解错了",
)

# 信号处理节流（秒）：每角色同一信号通道最小间隔
SIGNAL_THROTTLE_SECONDS = 600

_last_signal_at: dict[int, float] = {}


def source_weight(memory: Memory) -> float:
    """来源可信度权重（贴近 vnew3.0 9.1 表；项目 source 与 speaker/epistemic 联合映射）。"""
    if (memory.epistemic_status or "") == "INFERRED":
        return 0.4
    if (memory.speaker_type or "") == "user":
        return 1.0
    src = (memory.source or "").strip()
    if src in ("system", "status"):
        return 1.0  # 系统/状态更新：场景事实
    if src == "life":
        return 0.9
    if src in ("moment", "social", "group"):
        return 0.85
    if (memory.speaker_type or "") == "character":
        return 0.6  # AI 自己说的（可能出错）
    return 0.6


def reliability_score(memory: Memory, now: datetime | None = None) -> float:
    """可靠度计算（纯函数）：来源权重 × 矛盾惩罚 × 确认加分 × 时间衰减。"""
    sw = source_weight(memory)
    contrad = float(memory.contradiction_count or 0)
    confirm = int(memory.confirmation_count or 0)
    conflict_factor = 1.0 / (1.0 + contrad)
    confirm_factor = 0.7 + min(confirm, 3) * 0.1  # 0 次 0.7 / 1 次 0.8 / 2 次 0.9 / 3+ 1.0
    base_ts = memory.updated_at or memory.created_at
    days = 999.0
    if base_ts is not None:
        ts = base_ts.replace(tzinfo=None) if base_ts.tzinfo else base_ts
        days = max(0.0, ((now or _now_naive()) - ts).total_seconds() / 86400.0)
    decay = max(0.5, 1.0 - days * 0.01)  # 每天 -1%，最低 0.5
    return round(sw * conflict_factor * confirm_factor * decay, 3)


def detect_confirmation(text: str | None) -> bool:
    """用户消息是否包含强确认信号。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(w in t for w in CONFIRM_WORDS)


def detect_correction(text: str | None) -> bool:
    """用户消息是否包含纠正/否认信号。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(w in t for w in CORRECT_WORDS)


async def _top_related_memory(character_id: int, user_id: int, query: str) -> Memory | None:
    """定位与当前消息最相关的一条记忆（向量检索 top1；失败静默）。"""
    try:
        from app.memory import search_memories
        hits = await search_memories(character_id=character_id, query=query or "最近的事情", limit=1)
        if not hits:
            return None
        async with async_session_factory() as db:
            return await db.get(Memory, hits[0]["id"])
    except Exception as e:
        _logger.warning("top_related_memory failed char=%d: %s", character_id, e)
        return None


async def apply_correction(memory_id: int, *, source: str = "user") -> None:
    """记忆纠正：矛盾+1、reliability 重算、FACT 降级 UNVERIFIED。失败静默。

    ``source``：
    - ``"user"``（默认）：用户明确改口/否认 → 在 flag memory_supersede 开时追加
      supersede_memory，把被纠正记忆标记为 superseded 并级联 stale（治「用户改口后
      AI 仍抱旧偏好」）；
    - ``"fact_check"``：AI 异步自判矛盾 → **只降级，不取代**（防止 LLM 自判去永久隐藏
      一条记忆——AI 幻觉式回复与记忆冲突时错的更可能是回复而非记忆），provenance 也不
      会误标成 user_correction。
    """
    try:
        async with async_session_factory() as db:
            m = await db.get(Memory, memory_id)
            if m is None:
                return
            m.contradiction_count = (m.contradiction_count or 0) + 1
            if (m.epistemic_status or "") == "FACT":
                m.epistemic_status = "UNVERIFIED"
            m.reliability_score = reliability_score(m)
            await db.commit()
            _logger.info("memory corrected mem=%d contrad=%d reliability=%.3f",
                         memory_id, m.contradiction_count, m.reliability_score)
        # #70-C：仅「用户明确改口」才取代（AI 自判只降级）；失败静默，不阻塞降级主链路。
        if source == "user":
            try:
                from app.agent.loop import AGENT_FLAGS
                if AGENT_FLAGS.get("memory_supersede", False):
                    from app.memory.supersede import supersede_memory
                    await supersede_memory(memory_id, new_id=None, reason="user_correction")
            except Exception as e:
                _logger.warning("supersede on correction failed mem=%s: %s", memory_id, e)
    except Exception as e:
        _logger.warning("apply_correction failed mem=%s: %s", memory_id, e)


async def process_feedback_signal(character_id: int, user_id: int, user_msg: str,
                                  ai_response: str = "") -> None:
    """对话后处理记忆反馈信号（确认/纠正，$0 规则 + 节流）。"""
    try:
        now = time.time()
        last = _last_signal_at.get(character_id) or 0.0
        if now - last < SIGNAL_THROTTLE_SECONDS:
            return
        if detect_correction(user_msg):
            _last_signal_at[character_id] = now
            m = await _top_related_memory(character_id, user_id, user_msg)
            if m is not None:
                await apply_correction(m.id)
            return
        if detect_confirmation(user_msg):
            _last_signal_at[character_id] = now
            m = await _top_related_memory(character_id, user_id, user_msg)
            if m is not None:
                from app.memory.core import confirm_memory
                await confirm_memory(m.id)
    except Exception as e:
        _logger.warning("process_feedback_signal failed char=%d: %s", character_id, e)


def schedule_feedback_processing(character_id: int, user_id: int, user_msg: str,
                                 ai_response: str) -> None:
    """fire-and-forget 入口（不阻塞回复）。"""
    try:
        from app.utils.async_tasks import spawn_background
        spawn_background(process_feedback_signal(character_id, user_id, user_msg, ai_response))
    except Exception as e:
        _logger.warning("schedule_feedback_processing failed: %s", e)
