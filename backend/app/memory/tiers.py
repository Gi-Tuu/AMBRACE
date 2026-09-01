"""#70 方案A：记忆注入分层 L0/L1/L2 —— 纯规则、零 LLM、失败不阻塞主链路。

三层定义（不加表、主链路零 LLM）：
- L0（摘要 ≤80 字）：``why_it_matters``；无则取 ``content`` 规则首句。
- L1（那天概览 ≤120 字）：该记忆日期的 ``DailySummary.summary_text``（join ChatSession）。
- L2（详情）：``Memory.content`` 原文，Top1 上限 ``L2_MAX_CHARS``。

注入规则（由 section_memories 分层编排）：**Top1 → L2；Top2/3 → L0；Top1 再挂一行 L1 桥接**。
所有函数失败静默/可空，绝不阻塞主链路。
"""
from __future__ import annotations

L2_MAX_CHARS = 240     # Top1 详情上限
L0_MAX_CHARS = 80      # 摘要上限（与 why_it_matters 上限一致）
L1_MAX_CHARS = 120     # 那天日摘要上限
_MIN_SENTENCE_LEN = 8  # 短于该长度不把首个标点当句末，避免「嗯。」被切碎
_SENT_BREAKS = "。！？!?；;\n"


def first_sentence(text: str, max_len: int = L0_MAX_CHARS) -> str:
    """规则首句：遇到句读且已达到最小长度即截断；否则硬截。"""
    text = (text or "").strip()
    if not text:
        return ""
    for i, ch in enumerate(text):
        if ch in _SENT_BREAKS and i + 1 >= _MIN_SENTENCE_LEN:
            return text[: i + 1]
    return text[:max_len]


def extract_l0(m: dict, max_len: int = L0_MAX_CHARS) -> str:
    """L0：优先 why_it_matters，缺省回退 content 首句（兼容 dict）。"""
    why = (m.get("why_it_matters") or "").strip()
    if why:
        return why[:max_len]
    return first_sentence(m.get("content") or m.get("title") or "", max_len)


def build_vector_text(obj) -> str:
    """入库向量文本：里程碑用 'why content'，普通记忆用 content。

    去重口径不变（find_similar_memory 仍只用 content 原文 embedding）。兼容 ORM/dict。
    """
    def _g(k):
        if isinstance(obj, dict):
            return obj.get(k) or ""
        return getattr(obj, k, None) or ""

    content = (_g("content")).strip()
    why = (_g("why_it_matters")).strip()
    return f"{why} {content}".strip() if why else content


def tiered_memory_lines(memories: list[dict], *, include_speaker: bool = True) -> list[str]:
    """Top1→L2(240)，其余→L0；复用 format_memory_line 的时间/认知/说话人/纠正标注，保证格式不漂移。

    #70 方案A：严格按各层上限把内容截断后再交给 format_memory_line（显式传 max_len，
    否则会被其默认 150 再截一刀，导致 L2 详情达不到 240 的配额）。
    """
    from app.memory.format import format_memory_line

    out: list[str] = []
    for idx, m in enumerate(memories or []):
        if idx == 0:
            body = (m.get("content") or "")[:L2_MAX_CHARS]
            out.append(format_memory_line(
                {**m, "content": body}, include_speaker=include_speaker, max_len=L2_MAX_CHARS,
            ))
        else:
            body = extract_l0(m)
            out.append(format_memory_line(
                {**m, "content": body}, include_speaker=include_speaker, max_len=L0_MAX_CHARS,
            ))
    return out


async def load_l1_summary(character_id: int, date_str: str | None, max_len: int = L1_MAX_CHARS) -> str | None:
    """L1：取某角色某日的日摘要（DailySummary 无 character_id，必须 join ChatSession）。失败返回 None。"""
    if not date_str:
        return None
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.memory.daily_summary import DailySummary
        from app.models.chat.session import ChatSession

        async with async_session_factory() as db:
            row = (await db.execute(
                select(DailySummary)
                .join(ChatSession, DailySummary.session_id == ChatSession.id)
                .where(ChatSession.character_id == character_id,
                       DailySummary.summary_date == date_str)
                .order_by(DailySummary.id.desc())
                .limit(1)
            )).scalars().first()
        text = (row.summary_text or "").strip() if row else ""
        return text[:max_len] if text else None
    except Exception:
        return None
