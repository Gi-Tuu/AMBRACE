"""记忆可靠度评分 + 确认/纠正信号采集（World & Cognition P5，2026-08-15）

reliability = source_weight × 矛盾惩罚 × 确认加分 × 时间衰减

- source_weight：来源可信度（用户说的 1.0 > 系统 1.0 > Life 0.9 > 社交 0.85 > AI 表述 0.6 > 推断 0.4）
- 矛盾惩罚：1/(1+contradiction_count)，用户纠正一次明显下降
- 确认加分：平滑系数 0.7 + min(确认次数,3)*0.1（未确认不归零，确认越多越可信）
- 时间衰减：每天 -1%，最低 0.5

信号采集（$0 规则）：确认/纠正仅能作用于调用者提供的唯一实际引用 ID。
没有可验证引用时延后处理；禁止用当前反馈文本重新检索 top1 作为修改对象。
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

# 相同对象、相同用户反馈事件的重复节流（独立对象不相互屏蔽）
SIGNAL_THROTTLE_SECONDS = 600

_last_signal_at: dict[tuple, float] = {}


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


async def apply_correction(memory_id: int, *, source: str = "user") -> None:
    """Only explicit user evidence can downgrade a memory; AI conflicts cannot."""
    if source != "user":
        _logger.info("Ignored model-generated correction mem=%s source=%s", memory_id, source)
        return
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
                from app.flags.agent_flags import AGENT_FLAGS
                if AGENT_FLAGS.get("memory_supersede", False):
                    from app.memory.supersede import supersede_memory
                    await supersede_memory(memory_id, new_id=None, reason="user_correction")
            except Exception as e:
                _logger.warning("supersede on correction failed mem=%s: %s", memory_id, e)
    except Exception as e:
        _logger.warning("apply_correction failed mem=%s: %s", memory_id, e)


async def process_feedback_signal(character_id: int, user_id: int, user_msg: str,
                                  ai_response: str = "", *, cited_memory_ids=None) -> None:
    """Apply feedback only to one supplied actual citation, never a fresh search.

    Existing callers without citation provenance defer automatic mutation. Text in
    ai_response is not parsed as an authoritative citation list.
    """
    try:
        correction = detect_correction(user_msg)
        if not correction and not detect_confirmation(user_msg):
            return
        ids = set(cited_memory_ids or ())
        if len(ids) != 1:
            _logger.info("Memory feedback unresolved char=%s citation_count=%s", character_id, len(ids))
            return
        mid = next(iter(ids))
        async with async_session_factory() as db:
            memory = await db.get(Memory, mid)
        if (memory is None or memory.character_id != character_id or memory.user_id != user_id
                or memory.is_archived or memory.status != "active"):
            return
        key = (user_id, character_id, mid, user_msg.strip())
        now = time.time()
        if now - _last_signal_at.get(key, 0) < SIGNAL_THROTTLE_SECONDS:
            return
        if correction:
            await apply_correction(mid)
        else:
            from app.memory.core import confirm_memory
            await confirm_memory(mid)
        _last_signal_at[key] = now
        for old in list(_last_signal_at):
            if now - _last_signal_at[old] >= SIGNAL_THROTTLE_SECONDS:
                _last_signal_at.pop(old, None)
    except Exception as error:
        _logger.warning("process_feedback_signal failed char=%d: %s", character_id, error)


def schedule_feedback_processing(character_id: int, user_id: int, user_msg: str,
                                 ai_response: str) -> None:
    """fire-and-forget 入口（不阻塞回复）。"""
    try:
        from app.utils.async_tasks import spawn_background
        spawn_background(process_feedback_signal(character_id, user_id, user_msg, ai_response))
    except Exception as e:
        _logger.warning("schedule_feedback_processing failed: %s", e)
