"""记忆归属判定公共模块（X-2，2026-08-18）：统一提取端与标记端的 speaker/epistemic 判定。

此前两条写入路径各用一套启发式，判定标准不一致（X-2「speaker 契约未闭环」）：
- 提取端 extractor._resolve_speaker：内容前缀启发式（「我」开头→character/INFERRED、
  「用户/对方/他/她」开头或前 8 字含「用户」→user/FACT、无主语回退批级）；
- 标记端 nodes.generate_response：含推断词（可能/好像/也许/大概/我觉得/猜测）→
  character/INFERRED，否则默认 user/FACT。

本模块把两套启发式收敛为同一套规则（推断词优先 → 「我」前缀 → 用户指代前缀 → 批级回退），
extractor.py 与 nodes.py 均调用 resolve_speaker_from_content，保证两条写入路径判定一致；
「我们一起/我们」类共同事件不再因「我」字头误归角色，按批级回退规则处理。
"""
from __future__ import annotations

from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_INFERRED

# 推断词（标记端 nodes.py 原启发式，2026-08-18 收敛进公共函数）：
# 内容含任一推断词 → AI 的推测（character, INFERRED），优先于内容前缀判定。
INFERENCE_MARKERS = ("可能", "好像", "也许", "大概", "我觉得", "猜测")

# 用户指代前缀（提取端 extractor._resolve_speaker 原启发式）：
# 内容以这些词开头，或前 8 字内含「用户」→ 用户陈述（user, FACT）。
_USER_PREFIXES = ("用户", "对方", "他", "她")


def _batch_fallback(user_msg: str, user_id: int, character_id: int) -> tuple[str | None, str | None, str]:
    """批级回退（提取端原行为）：用户消息在场 → 用户陈述（FACT），否则 → 角色表述（INFERRED）。"""
    if (user_msg or "").strip():
        return "user", user_id, EPISTEMIC_FACT
    return "character", character_id, EPISTEMIC_INFERRED


def resolve_speaker_from_content(
    content: str,
    user_msg: str,
    ai_msg: str,
    user_id: int,
    character_id: int,
) -> tuple[str | None, str | None, str]:
    """统一归属判定（X-2）：返回 (speaker_type, speaker_id, epistemic_status)。

    依次判定，命中即返回：
    1. 内容含推断词（可能/好像/也许/大概/我觉得/猜测）→ AI 的推测（character, INFERRED）；
    2. 以「我」开头且非「我们」→ 角色自己的表述（character, INFERRED）；
    3. 以「用户/对方/他/她」开头或前 8 字含「用户」→ 用户陈述（user, FACT）；
    4. 「我们一起/我们」类共同事件 → 批级回退（不因「我」字头误归角色）；
    5. 其余无主语内容 → 批级回退（用户消息在场=user/FACT，否则 character/INFERRED）。

    content 为空时返回 (None, None, FACT)：无内容可归属，调用方（save_memory）按默认值处理。
    ai_msg 暂未参与判定（与提取端 _extract_epistemic 原行为一致），保留在签名中供后续扩展。
    """
    text = (content or "").strip()
    if not text:
        return None, None, EPISTEMIC_FACT
    if any(w in text for w in INFERENCE_MARKERS):
        return "character", character_id, EPISTEMIC_INFERRED
    if text.startswith("我") and not text.startswith("我们"):
        return "character", character_id, EPISTEMIC_INFERRED
    if text.startswith(_USER_PREFIXES) or "用户" in text[:8]:
        return "user", user_id, EPISTEMIC_FACT
    return _batch_fallback(user_msg, user_id, character_id)
