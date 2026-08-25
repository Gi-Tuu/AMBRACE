"""Lorebook 关键词匹配器（P1-2，2026-08-16 → L2 触发式注入：正则/概率/组/时序，2026-08-24）

匹配规则：
- 关键词必须 ≥2 字（单字子串易误伤，如「猫」命中「猫屎咖啡」）；is_regex=True 时按正则解析
  （/pattern/flags 或裸 pattern），不再限定长度（用户显式选用正则即认可）；
- 子串匹配（简单可控）；条目配置排除词时，文本含任一排除词则该条目关键词命中失效
  （排除词恒按子串匹配，行为与 P1-2 一致）；
- 概率检定：命中条目按 probability 决定是否注入（100=必注入；rng 可注入做单测）；
- Inclusion Group：同 inclusion_group（非空）命中多条时只取一条（updated_at 最新）；
- Timed Effects：sticky（触发后 sticky_rounds 轮内继续注入）/ cooldown（触发后
  cooldown_rounds 轮内不注入）——进程内轮次状态 dict（character_id, entry_id -> 最近触发轮次），
  重启清零；轮次复用 context 的角色上下文构建次数；
- 返回命中条目（active 且命中），按 updated_at 倒序，条数受 MAX_LOREBOOK_HITS 限制。

向后兼容：is_regex=False / probability=100 / inclusion_group='' / sticky_rounds=0 /
cooldown_rounds=0 时，匹配结果与 P1-2 完全一致（默认条目不会被写入时序状态）。
"""
import functools
import json
import random
import re
from datetime import datetime

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.lorebook_entry import LorebookEntry
from app.utils.logger import get_logger

_logger = get_logger("memory.lorebook")

MAX_LOREBOOK_HITS = 3  # 单轮最多命中条数（防注入膨胀）

# 进程内时序状态：槽位记录 (character_id, entry_id) -> 最近触发轮次（重启清零，行为可控）
_lorebook_trigger_rounds: dict[tuple[int, int], int] = {}


def clear_trigger_state(character_id: int, entry_id: int) -> None:
    """删除条目/角色时清理对应触发时序状态（防进程内存滞留）。"""
    _lorebook_trigger_rounds.pop((character_id, entry_id), None)


# 正则 flag 字母映射（/pattern/i 等）
_REGEX_FLAGS = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
    "a": re.ASCII,
    "u": re.UNICODE,
    "L": re.LOCALE,
}


def _split_keywords(raw: str, is_regex: bool = False) -> list[str]:
    """解析 JSON 关键词列表，过滤空项。

    - 子串模式（is_regex=False，向后兼容）：单字剔除（防「猫」命中「猫屎咖啡」类误伤）；
    - 正则模式（is_regex=True）：任意非空 pattern 均保留（正则本身可表达单字符语义）。
    """
    try:
        ks = json.loads(raw or "[]")
    except Exception:
        return []
    out = []
    for k in ks:
        s = str(k or "").strip()
        if not s:
            continue
        if is_regex:
            out.append(s)
        elif len(s) >= 2:
            out.append(s)
    return out


@functools.lru_cache(maxsize=256)
def _compile_regex(keyword: str):
    """把正则关键词编译为 re.Pattern；支持 /pattern/flags 与裸 pattern；非法返回 None。"""
    pattern_src = keyword
    flags = 0
    if keyword.startswith("/") and keyword.rfind("/") > 0:
        last = keyword.rfind("/")
        pattern_src = keyword[1:last]
        for ch in keyword[last + 1:]:
            f = _REGEX_FLAGS.get(ch)
            if f is None:
                return None
            flags |= f
    try:
        return re.compile(pattern_src, flags)
    except re.error:
        return None


def _keyword_matches(text: str, e: LorebookEntry) -> bool:
    """关键词命中判定：is_regex 时按正则（re.search），否则子串；排除词恒按子串过滤。"""
    # 排除词（子串，行为与 P1-2 一致）：任一命中则该条目关键词命中失效
    exs = _split_keywords(e.exclude_keywords)
    if exs and any(x in text for x in exs):
        return False
    kws = _split_keywords(e.keywords, is_regex=bool(e.is_regex))
    if not kws:
        return False
    if not e.is_regex:
        return any(k in text for k in kws)
    for kw in kws:
        pat = _compile_regex(kw)
        if pat is not None and pat.search(text):
            return True
    return False


def _roll_probability(probability: int | None, rng) -> bool:
    """概率检定：100=必注入；0=不注入；否则 rng() < probability/100。异常/缺失按 100 处理（向后兼容）。"""
    try:
        p = int(probability)
    except Exception:
        p = 100
    if p >= 100:
        return True
    if p <= 0:
        return False
    return rng() < (p / 100.0)


def _entry_time(e: LorebookEntry) -> datetime:
    return (e.updated_at or e.created_at) or datetime.min


def _get_round(character_id: int) -> int:
    """当前对话轮次：复用 context 的角色上下文构建次数（进程内状态，重启清零）。"""
    try:
        from app.agent.context.section_memories import _memory_char_rounds
        return int(_memory_char_rounds.get(character_id, 0) or 0)
    except Exception:
        return 0


def _is_in_cooldown(state: dict, key, round_no: int, cooldown_rounds: int) -> bool:
    if cooldown_rounds <= 0:
        return False
    last = state.get(key)
    if last is None:
        return False
    return (round_no - last) <= cooldown_rounds


def _is_sticky(state: dict, key, round_no: int, sticky_rounds: int) -> bool:
    if sticky_rounds <= 0:
        return False
    last = state.get(key)
    if last is None:
        return False
    return 0 < (round_no - last) <= sticky_rounds


def _dedup_by_group(candidates: list[LorebookEntry]) -> list[LorebookEntry]:
    """Inclusion Group：同 inclusion_group（非空）只保留 updated_at 最新一条；非组条目各自保留。"""
    independent: list[LorebookEntry] = []
    group_best: dict[str, LorebookEntry] = {}
    for e in candidates:
        g = (e.inclusion_group or "").strip()
        if not g:
            independent.append(e)
            continue
        cur = group_best.get(g)
        if cur is None or _entry_time(e) > _entry_time(cur):
            group_best[g] = e
    return independent + list(group_best.values())


def match_lorebook_entries(
    text: str,
    entries: list[LorebookEntry],
    *,
    round_no: int | None = None,
    rng=None,
    state: dict | None = None,
    character_id: int | None = None,
) -> list[LorebookEntry]:
    """纯函数（rng / round_no / state / character_id 可注入）：对给定文本匹配触发式注入候选。

    流程：过滤 active → 冷却检查 → 关键词命中 或 sticky 续命 → 概率检定（仅新触发）→
    Inclusion Group 去重 → updated_at 倒序 → MAX_LOREBOOK_HITS 上限 → 记录触发（sticky/cooldown）。
    返回按 updated_at 倒序的命中条目。
    """
    if not entries:
        return []
    if rng is None:
        rng = random.random
    if state is None:
        state = _lorebook_trigger_rounds
    if round_no is None:
        round_no = _get_round(character_id or 0)

    candidates: list[LorebookEntry] = []
    for e in entries:
        if not e.active:
            continue
        key = (character_id, e.id) if character_id is not None else e.id
        sticky_rounds = int(e.sticky_rounds or 0)
        cooldown_rounds = int(e.cooldown_rounds or 0)
        # 冷却期：无论关键词命中或 sticky 均不注入
        if _is_in_cooldown(state, key, round_no, cooldown_rounds):
            continue
        kw_hit = _keyword_matches(text, e)
        sticky = _is_sticky(state, key, round_no, sticky_rounds)
        if not kw_hit and not sticky:
            continue
        # 概率检定仅对新触发（关键词命中）生效；sticky 续命已在触发时检定过，不重复掷骰
        if kw_hit and not sticky:
            if not _roll_probability(e.probability, rng):
                continue
        candidates.append(e)

    candidates = _dedup_by_group(candidates)
    candidates.sort(key=_entry_time, reverse=True)
    selected = candidates[:MAX_LOREBOOK_HITS]

    # 记录触发轮次（仅 sticky/cooldown 条目；默认条目不写入状态，严格向后兼容）
    for e in selected:
        key = (character_id, e.id) if character_id is not None else e.id
        if int(e.sticky_rounds or 0) > 0 or int(e.cooldown_rounds or 0) > 0:
            state[key] = round_no

    return selected


async def load_matching_entries(
    character_id: int, text: str, round_no: int | None = None, rng=None,
) -> list[LorebookEntry]:
    """DB 加载该角色全部活跃条目并匹配（失败静默返回空）。"""
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(LorebookEntry).where(
                    LorebookEntry.character_id == character_id,
                    LorebookEntry.active == True,
                )
            )).scalars().all()
        if round_no is None:
            round_no = _get_round(character_id)
        return match_lorebook_entries(
            text, list(rows), round_no=round_no, rng=rng, character_id=character_id,
        )
    except Exception as e:
        _logger.warning("Lorebook match failed char=%d: %s", character_id, e)
        return []
