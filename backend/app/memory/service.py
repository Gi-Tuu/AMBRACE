"""记忆服务：结构化记忆（SQLite）与向量记忆（ChromaDB）的读写入口"""
import json

from sqlalchemy import true as _sql_true  # noqa: F401  # F4 再导出

from app.db.database import async_session_factory  # noqa: F401  # F4 再导出
from app.db.vector_store import (  # noqa: F401  # F4 再导出
    add_memory,
    search_memories as vector_search,
    delete_memory_vector,
    find_similar_memory,
)
from app.models.memory import Memory  # noqa: F401  # F4 再导出
from app.utils.logger import get_logger
from app.memory.embedding import text_embedding  # noqa: F401  # F4 再导出
from app.memory.embedding_cache import get_cached_embedding  # noqa: F401  # F4 再导出（X-3 LRU 缓存）
from app.memory.constants import (  # noqa: F401  # F4 再导出
    DECAY_MAX_PCT,
    VECTOR_DEDUP_THRESHOLD, S_DEFAULT, S_MIN_DAYS, S_MAX_DAYS,
    REINFORCE_FACTOR_WRITE,
    EPISODIC_REVIEW_S_CAP, EPISODIC_REVIEW_COUNT_CAP,
    PLAN_REVIEW_S_CAP, PLAN_REVIEW_COUNT_CAP,
)
from app.memory.decay import retention_pct  # noqa: F401  # F4 再导出
from app.memory.bm25_index import search as bm25_search, invalidate as bm25_invalidate  # noqa: F401  # F4 再导出（检索增强）
from app.memory import rrf as _rrf  # noqa: F401  # F4 再导出（检索增强深化）

_logger = get_logger("memory.service")

# M-P1-4（2026-08-18）：置顶加分与置顶配额——置顶摘要不再 +10000 恒霸检索 top3，
# 近期具体事件/情绪记忆（非置顶）也能进入注入上下文。
PINNED_BONUS = 500.0     # 置顶加分（原 10000 → 500）
PINNED_QUOTA = 2         # 排序后结果中最多保留的置顶条数，其余置顶不挤占非置顶槽位

# #70-C 状态词表（与 epistemic_status 正交）：active=现行 / superseded=被取代 / stale=派生失效
_ACTIVE = "active"
_SUPERSEDED = "superseded"
_STALE = "stale"


def _supersede_flag_on() -> bool:
    """#70-C 门控：读 memory_supersede flag（延迟 import，避免顶层循环依赖 loop）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_supersede", False))
    except Exception:
        return False


def _current_facts_flag_on() -> bool:
    """2026-09-17 批次一（任务2）：现状/事实面新口径门控 current_facts_active_only（默认 True）。

    延迟 import AGENT_FLAGS（避免顶层循环依赖 loop）；键未登记/读取异常按默认 True 处理
    （与硬编码默认一致）。置 False = 一键回退旧行为（status 子句退回 memory_supersede 门控）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("current_facts_active_only", True))
    except Exception:
        return True


def _legacy_active_status_clause():
    """旧口径（现状面新 flag 关时的回退）：memory_supersede 门控，关=永真，与改造前逐字节一致。"""
    if not _supersede_flag_on():
        return _sql_true()
    return Memory.status == _ACTIVE


def _retrievable_status_clause():
    """**怀旧/复习面**可见集合 = {active, stale}（stale 在 rerank 恒降权）。flag 关返回永真，与现状逐字节一致。

    2026-09-17 批次一（任务2）：拆口径——本子句仍按 memory_supersede 门控且保留 stale（怀旧可见），
    现状/事实注入面请改用 ``current_facts_status_clause()``（恒 active）。
    """
    if not _supersede_flag_on():
        return _sql_true()
    return Memory.status.in_([_ACTIVE, _STALE])


def current_facts_status_clause():
    """现状/事实注入面：恒「仅 active」——stale/superseded/expired 一律不取。

    2026-09-17 批次一（任务2）新增：不复用 memory_supersede（它是「怀旧可见性」门控且默认关，
    拿它当现状面闸门会让旧现状与现行事实同分竞争——线上 DeepSeek/Dom 反复「你在长沙」的根因）。
    current_facts_active_only（默认 True）开 = 恒 active；关 = 回退旧 _active_status_clause 语义。
    """
    if not _current_facts_flag_on():
        return _legacy_active_status_clause()
    return Memory.status == _ACTIVE


def _active_status_clause():
    """既有调用点（衰减/评星/意义/去重/时间线/摘要/展示）= 只作用于当前有效记忆。

    2026-09-17 批次一（任务2）：语义收紧为「恒 active」（与 current_facts_status_clause 同口径），
    由 current_facts_active_only（默认 True）包住；关掉即回退旧行为（memory_supersede 门控）。
    """
    if _current_facts_flag_on():
        return Memory.status == _ACTIVE
    return _legacy_active_status_clause()


def star_from_pct(pct: float) -> int:
    """百分比重要度转 1-5 星：pct/20 取整钳制到 1-5"""
    return max(1, min(5, round((pct or 0.0) / 20.0)))


def _normalize_importance(imp) -> float:
    """重要度标度归一化（M-P2-2）：≤5 视为 1-5 星制（×20 → 百分比），否则视为已是百分比原值返回。
    创建路径与三条查重/合并路径共用，避免调用方以百分比传入时把旧记忆 importance 顶高。
    """
    v = float(imp or 0)
    return v * 20.0 if v <= 5 else v

from app.utils.timeutil import now_naive_utc as _now_naive


def _merge_derived(raw, extra_ids: list[int]) -> str:
    """#70-C M2：把 extra_ids 并入既有 derived_from_ids（JSON 数组字符串），去重、幂等。"""
    try:
        cur = [int(x) for x in json.loads(raw or "[]")]
    except Exception:
        cur = []
    for i in extra_ids:
        if i is not None and int(i) not in cur:
            cur.append(int(i))
    return json.dumps(cur, ensure_ascii=False)


def _review_caps_for(m, now=None) -> tuple[float | None, int | None]:
    """L2（2026-09-09 主动复习「回忆化」）：一次性事件经"主动复习成功"强化的 (S 上限, 次数上限)。

    - 已过期/失效计划与瞬时状态收死（PLAN_REVIEW_*，防养出 7018 那种 S=60/复习 14 次的永生记忆）；
    - 真正的往事（episodic）保留适度强化空间（EPISODIC_REVIEW_*，中位而非全砍，不误伤正常回忆）；
    - 恒久记忆（enduring）不上限，仍可走到 S_MAX_DAYS；
    - 仅 channel="review"（AI 主动翻旧账）收口；检索/写入命中（retrieve/write）维持轻量强化。
    flag review_reinforce_event_cap 关 = 全部返回 None（旧强化行为，可到 60）。
    now 由 _apply_reinforce 透传（缺省=真实当前时间）：判定与强化用同一时刻，避免用
    墙钟给纯对象/回放用例做过期判定（2026-09-17 修复：该处曾随日历翻车）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("review_reinforce_event_cap", True):
            return None, None
    except Exception:
        return None, None
    from app.memory.tense import classify_tense, is_plan_expired
    tense = classify_tense(m)
    if tense == "plan":
        if is_plan_expired(m, now):
            return PLAN_REVIEW_S_CAP, PLAN_REVIEW_COUNT_CAP
        return EPISODIC_REVIEW_S_CAP, EPISODIC_REVIEW_COUNT_CAP  # 有效期内安排：适度（临期确认仍可巩固）
    if tense == "transient":
        return PLAN_REVIEW_S_CAP, PLAN_REVIEW_COUNT_CAP
    if tense == "episodic":
        return EPISODIC_REVIEW_S_CAP, EPISODIC_REVIEW_COUNT_CAP
    return None, None


def _apply_reinforce(m, factor: float, now, *, channel: str = "retrieve") -> None:
    """艾宾浩斯强化（同步，操作 ORM 对象，由调用方 commit）：
    S *= factor（上限 S_MAX）、review_count+1、刷新 last_reinforce_at、
    取消删除倒计时，importance 回升到至少"复习半日后保留率"。
    强化视为一次成功复习：排下次主动复习时间（now + S 天）。is_locked 记忆不参与。

    channel: write=写入查重命中 / retrieve=检索命中 / review=主动复习成功。
    L2（2026-09-09）：review 通道对一次性事件按 tense 分流收口（S/次数封顶、
    达上限后退出主动复习轮转 next_review_at=None），检索/写入通道不受影响。
    """
    import math
    from datetime import timedelta
    if m.is_locked:
        return
    s_cap: float | None = None
    count_cap: int | None = None
    if channel == "review":
        s_cap, count_cap = _review_caps_for(m, now)
    if count_cap is not None and (m.review_count or 0) >= count_cap:
        # 已达复习强化上限：不再强化、不再延长主动复习周期（停止主动翻旧账），检索仍可见
        m.last_reinforce_at = now
        m.next_review_at = None
        m.updated_at = now
        return
    s = float(m.strength_days or S_DEFAULT)
    s_max = S_MAX_DAYS if s_cap is None else min(S_MAX_DAYS, float(s_cap))
    m.strength_days = min(s_max, max(S_MIN_DAYS, s * factor))
    m.review_count = (m.review_count or 0) + 1
    m.last_reinforce_at = now
    m.delete_at = None
    s_new = float(m.strength_days)
    pct = min(DECAY_MAX_PCT, max(float(m.importance or 40.0), math.exp(-0.5 / s_new) * 120.0))
    m.importance = pct
    # 一次性事件达次数上限后不再排远期主动复习（退出复习轮转，保留检索可见）
    if channel == "review" and count_cap is not None and (m.review_count or 0) >= count_cap:
        m.next_review_at = None
    else:
        m.next_review_at = now + timedelta(days=s_new)
    m.updated_at = now


async def reinforce_memories(
    memory_ids: list[int],
    factor: float,
    debounce_hours: float = 0.0,
    *,
    channel: str = "retrieve",
) -> None:
    """艾宾浩斯强化（独立 session 版）：S *= factor + review_count+1 + 刷新遗忘起点。

    debounce_hours > 0 时距上次强化不足该时长则跳过（检索命中防抖）。
    channel 透传 _apply_reinforce（review=主动复习成功，受 L2 一次性事件收口约束）。
    """
    from datetime import timedelta
    if not memory_ids:
        return
    now = _now_naive()
    async with async_session_factory() as db:
        for mid in memory_ids:
            m = await db.get(Memory, mid)
            if m is None or m.is_archived or m.is_pinned or m.is_locked:
                continue
            last = m.last_reinforce_at
            # 防抖仅对"已强化过"的记忆生效（review_count>0），新记忆首次命中不拦截
            if debounce_hours > 0 and (m.review_count or 0) > 0 and last is not None:
                last = last.replace(tzinfo=None) if last.tzinfo else last
                if (now - last) < timedelta(hours=debounce_hours):
                    continue
            _apply_reinforce(m, factor, now, channel=channel)
        await db.commit()


def _initial_strength(memory_type: str) -> float:
    """新记忆初始强度 S（按类型，艾宾浩斯）"""
    from app.memory.constants import S_BY_TYPE
    return S_BY_TYPE.get(memory_type, S_DEFAULT)

async def save_memory(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/write.py。"""
    from app.memory import write as _m
    return await _m.save_memory(*args, **kwargs)


async def _rerank(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/retrieve.py。"""
    from app.memory import retrieve as _m
    return await _m._rerank(*args, **kwargs)


def _diversify_by_type(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/retrieve.py（M1-S1 类型多样性重排）。"""
    from app.memory import retrieve as _m
    return _m._diversify_by_type(*args, **kwargs)


async def search_memories(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/retrieve.py。"""
    from app.memory import retrieve as _m
    return await _m.search_memories(*args, **kwargs)


async def list_memories(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/maintain.py。"""
    from app.memory import maintain as _m
    return await _m.list_memories(*args, **kwargs)


async def delete_memory(*args, **kwargs):
    """垫片（F4）：实现迁至 app/memory/maintain.py。"""
    from app.memory import maintain as _m
    return await _m.delete_memory(*args, **kwargs)



