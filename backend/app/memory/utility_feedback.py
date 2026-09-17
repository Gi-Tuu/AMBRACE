"""召回后「效用反馈」（Slowave 式，小增量 2026-09-16）

flag ``memory_utility_feedback`` 已预注册（默认关）；本模块全部逻辑在该 flag 开后生效，
关时 ``schedule_utility_feedback`` 直接返回、零行为变化（不读不写）。

目标：补上「这条召回到底有没有用上」的缺失信号，用来微调记忆 salience/衰减——
我们已有艾宾浩斯强化、reliability（矛盾纠正）、tiering（低置信加速退化），缺的正是这一环。

信号（确定性规则，不引入 LLM）：
- negative：本轮文本（用户消息 + AI 回复）**同时**命中纠正词（复用 reliability.CORRECT_WORDS）
  **且**出现针对该条记忆的指代/承接（记忆关键片段被引用）→ 记忆被明确纠正才整轮降权；
- positive：记忆关键片段出现在回复文本（被用上），且无纠正 → 微上调；
- neutral：两者皆无 → 不调整。
  （收紧点：仅「随口否定」但不涉及该条记忆 → 判 neutral，不再误伤整轮召回池。）

作用（最小可用、幅度极小、常量可配）：
- positive → ``Memory.importance`` 微上调；negative → 微下调（复用既有 salient 权重通道，
  importance 直接进 rerank 基线 + 检索排序，与 tiering/reliability 同处「权重微调」语义，不另造一套）；
- 同时写一条轻量回执到既有 ``memory_write_receipts`` 表（M3 回执，零新表），记录谁/哪些 id/回合/时间。

存储：复用既有 ``memory_write_receipts`` 表 + ``Memory.importance`` 字段；不新增任何表。
异步 fire-and-forget，失败静默、不阻塞主链路。
"""
from app.utils.logger import get_logger

_logger = get_logger("memory.utility_feedback")

# ── 作用幅度（极小，常量可配；importance 为百分比标度 0-120）──
UTILITY_POSITIVE_IMPORTANCE_DELTA = 0.5  # 微强化：importance +0.5（≈ +0.4% 标度）
UTILITY_NEGATIVE_IMPORTANCE_DELTA = 0.8  # 微降权：importance -0.8
UTILITY_IMPORTANCE_FLOOR = 0.0
UTILITY_IMPORTANCE_CEIL = 120.0
# positive 命中要求记忆正文被回复包含的连续片段最小长度（防单字偶然命中误判为 positive）
UTILITY_POSITIVE_CONTAINS_MIN = 6

# 记忆正文用于命中检测的连续核心片段最小长度（短于此不参与 positive 命中）
UTILITY_MIN_SNIPPET_LEN = 6

_CORRECT_WORDS: tuple | None = None  # 延迟取自 reliability（避免顶层循环依赖）


def _flag_on() -> bool:
    """读 feature flag；任何异常回落 False（关=逐字节旧行为）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_utility_feedback", False))
    except Exception:
        return False


def is_enabled() -> bool:
    """flag 是否开启（供调用点早判：flag 关时调用方不产生任何状态改动）。

    与 ``schedule_utility_feedback`` 内的判定复用同一实现，二者不会分叉。
    """
    return _flag_on()


def _correction_words() -> tuple:
    global _CORRECT_WORDS
    if _CORRECT_WORDS is None:
        try:
            from app.memory.reliability import CORRECT_WORDS
            _CORRECT_WORDS = CORRECT_WORDS
        except Exception:
            _CORRECT_WORDS = ()
    return _CORRECT_WORDS


def _core_snippet(content: str) -> str:
    """取记忆正文中用于命中检测的连续核心片段（剥离【..】认知/时态标记前缀 + 时间标签 + 标点）。纯函数。"""
    import re as _re
    c = (content or "").strip()
    # 去掉行首连续【...】标记（如 [记录于 2026-08-16][往事]、[FACT]）
    c = _re.sub(r"^(?:\[[^\]]*\]\s*)+", "", c)
    c = _re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】~～\-—]", "", c)
    return c


def _contains_key_fragment(snippet: str, resp: str, min_len: int = UTILITY_POSITIVE_CONTAINS_MIN) -> bool:
    """记忆核心片段是否有长度 >= min_len 的连续子串出现在回复中（滑窗，纯函数）。"""
    if len(snippet) < min_len:
        return False
    for start in range(0, len(snippet) - min_len + 1):
        if snippet[start:start + min_len] in resp:
            return True
    return False


def classify_utility_signal(memory_content: str, ai_response: str, user_message: str = "") -> str:
    """确定性效用判定（纯函数，可单测）：'negative' / 'positive' / 'neutral'。

    - 文本（用户消息 + AI 回复）同时命中纠正词 **且** 含该条记忆关键片段 → negative（记忆被明确纠正，整轮降权）；
    - 否则记忆关键片段出现在文本 → positive（记忆被用上）；
    - 否则 → neutral（不调整；含「随口否定但不涉及该条记忆」的情形）。

    ``user_message`` 默认空：纠正词多为用户侧措辞，跨两段联合判定可更准；缺省时退回只看 AI 回复的旧口径。
    """
    resp = f"{(user_message or '')}\n{(ai_response or '')}"
    snippet = _core_snippet(memory_content)
    # ① 记忆被指代/引用（话题词重叠）→ 先判定 reference
    referenced = _contains_key_fragment(snippet, resp)
    # ② 纠正词 + 该条记忆被引用 → negative（收紧：随口否定不误伤）
    if referenced and any(w in resp for w in _correction_words()):
        return "negative"
    # ③ 记忆关键片段出现在回复 → positive（记忆被用上）
    if referenced:
        return "positive"
    return "neutral"


async def apply_utility_feedback(
    character_id: int,
    user_id: int,
    items: list[tuple[int, str]],
    round_id: int | None = None,
) -> None:
    """把效用信号落到记忆（写 M3 回执 + importance 微调）。失败静默、不抛。

    ``items``：[(memory_id, signal), ...]，signal ∈ {positive, negative, neutral}；
    neutral 跳过（不写不调）。
    """
    if not items:
        return
    try:
        from app.db.database import async_session_factory
        from app.models.memory import Memory, MemoryWriteReceipt
        from app.utils.timeutil import now_naive_utc as _now

        pos = UTILITY_POSITIVE_IMPORTANCE_DELTA
        neg = UTILITY_NEGATIVE_IMPORTANCE_DELTA
        now = _now()
        async with async_session_factory() as db:
            for mid, sig in items:
                if sig == "neutral":
                    continue
                m = await db.get(Memory, mid)
                if m is None or m.is_archived or m.is_locked:
                    # 记忆已不存在/已归档/锁定：仍留一条回执（可追溯），但不改其权重
                    db.add(MemoryWriteReceipt(
                        character_id=character_id,
                        memory_id=mid,
                        action="utility_feedback",
                        reason=sig,
                        detail_json=_detail_json(
                            mid, sig, round_id, user_id=user_id,
                            skipped=bool(m is None or m.is_archived or m.is_locked),
                        ),
                    ))
                    continue
                delta = pos if sig == "positive" else -neg
                m.importance = max(
                    UTILITY_IMPORTANCE_FLOOR,
                    min(UTILITY_IMPORTANCE_CEIL, float(m.importance or 0) + delta),
                )
                m.updated_at = now
                db.add(MemoryWriteReceipt(
                    character_id=character_id,
                    memory_id=mid,
                    action="utility_feedback",
                    reason=sig,
                    detail_json=_detail_json(mid, sig, round_id, user_id=user_id),
                ))
            await db.commit()
    except Exception as e:
        _logger.warning("utility_feedback apply failed char=%d: %s", character_id, e)


def _detail_json(
    memory_id: int, signal: str, round_id: int | None,
    skipped: bool = False, user_id: int | None = None,
) -> str:
    """回执 detail：谁（user_id）/哪条记忆/信号/回合/是否跳过（时间由 receipt.created_at 记）。"""
    import json as _json
    return _json.dumps(
        {
            "memory_id": memory_id,
            "signal": signal,
            "round_id": round_id,
            "user_id": user_id,
            "skipped": skipped,
        },
        ensure_ascii=False,
    )


def schedule_utility_feedback(
    character_id: int,
    user_id: int,
    recalled: list[dict],
    ai_response: str,
    round_id: int | None = None,
    user_message: str = "",
) -> None:
    """fire-and-forget 入口（不阻塞回复）。``recalled`` = state['retrieved_memories']（含 id/content）。

    ``user_message``：本轮用户原始消息，用于收窄 negative 判据（纠正词多为用户措辞，且需与该条记忆指代共现）。
    flag 关 → 直接返回（零行为变化）。异步失败不影响主流程。
    """
    if not _flag_on():
        return
    if not recalled or not (ai_response or "").strip():
        return
    try:
        items: list[tuple[int, str]] = []
        for r in recalled:
            mid = r.get("id")
            if mid is None:
                continue
            sig = classify_utility_signal(r.get("content") or "", ai_response, user_message=user_message)
            if sig != "neutral":
                items.append((int(mid), sig))
        if not items:
            return
        from app.utils.async_tasks import spawn_background
        spawn_background(apply_utility_feedback(character_id, user_id, items, round_id))
    except Exception as e:
        _logger.warning("schedule_utility_feedback failed: %s", e)
