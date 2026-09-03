"""Memories section（试水，试点 B）：记忆检索区 N 轮去重 / 核心记忆 / 关系锚点 / 开放循环注入。

从 ``context_builder`` 迁出，逻辑与旧版完全一致（零行为变化）：
- N 轮去重：同一记忆最近 ``MEMORY_DEDUP_WINDOW_ROUNDS`` 轮内不重复注入检索区行；
- 长期画像不参与去重：核心记忆/关系锚点/开放循环不经过 ``_filter_recently_injected``；
- 去重轮次为进程内状态（重启清零，与现状一致）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, register_section, TARGET_APPEND, TARGET_TEMPLATE
from app.memory.format import format_memory_line

_logger = logging.getLogger("agent.context.section_memories")

# X-4（2026-08-18）：会话内「N 轮内不重复注入」轻量去重（仅「和你相关的记忆」检索区行；
# 核心记忆/锚点/置顶摘要等长期画像分区不受限，避免丢画像）。进程内状态，重启清零可接受。
MEMORY_DEDUP_WINDOW_ROUNDS = 5
MEMORY_DEDUP_MAX_PER_CHAR = 200
_memory_inject_rounds: dict[tuple[int, int], int] = {}  # (character_id, memory_id) -> 最近注入轮次
_memory_char_rounds: dict[int, int] = {}  # character_id -> 轮次计数（每次 build_context 递增）

# 核心记忆 / 关系锚点 / 开放循环注入配额
_CORE_MEMORIES_QUOTA_TOKENS = 1200
_ANCHORS_QUOTA_TOKENS = 500
_OPEN_LOOPS_QUOTA_TOKENS = 500
_MEMORIES_QUOTA_TOKENS = 400  # A4（2026-08-18 降本）：600->400；#70 方案A：真正裁剪在 context_builder._SECTION_QUOTA_TOKENS（flag 开 500/关 400），此处仅注册表元数据


def _memory_id_of(m) -> int | None:
    """取记忆 id（兼容检索 dict 与 ORM 对象）；无 id 返回 None（不参与去重）"""
    if isinstance(m, dict):
        return m.get("id")
    return getattr(m, "id", None)


def _filter_recently_injected(character_id: int, memories: list, window: int = MEMORY_DEDUP_WINDOW_ROUNDS) -> list:
    """X-4（2026-08-18）：检索区 N 轮去重——同一记忆最近 window 轮内注入过则本轮跳过。

    仅作用于「和你相关的记忆」检索区行（不改变检索结果本身）；
    核心记忆/锚点/置顶摘要等长期画像分区不经本函数，避免丢画像。
    纯逻辑函数（便于单测）：读进程内 _memory_char_rounds/_memory_inject_rounds 状态。
    """
    cur = _memory_char_rounds.get(character_id, 0)
    out = []
    for m in memories:
        mid = _memory_id_of(m)
        if mid is None:
            out.append(m)
            continue
        last = _memory_inject_rounds.get((character_id, mid))
        if last is not None and cur - last < window:
            continue
        out.append(m)
    return out


def _mark_memories_injected(character_id: int, memories: list) -> None:
    """X-4（2026-08-18）：记录本轮实际注入检索区的记忆轮次，并按每角色容量裁剪（超限剔除最旧）。"""
    cur = _memory_char_rounds.get(character_id, 0)
    for m in memories:
        mid = _memory_id_of(m)
        if mid is not None:
            _memory_inject_rounds[(character_id, mid)] = cur
    char_keys = [k for k in _memory_inject_rounds if k[0] == character_id]
    if len(char_keys) > MEMORY_DEDUP_MAX_PER_CHAR:
        _oldest = sorted(char_keys, key=lambda k: _memory_inject_rounds[k])[:len(char_keys) - MEMORY_DEDUP_MAX_PER_CHAR]
        for k in _oldest:
            _memory_inject_rounds.pop(k, None)


def _bump_memory_round(character_id: int) -> int:
    """X-4（2026-08-18）：角色检索区轮次 +1（每次 build_context 调用计一轮），返回新轮次。"""
    _memory_char_rounds[character_id] = _memory_char_rounds.get(character_id, 0) + 1
    return _memory_char_rounds[character_id]


def _build_retrieved_memory_lines(character_id: int, retrieved: list) -> list[str]:
    """「和你相关的记忆」检索区行（X-4：N 轮去重 + X-2 说话人标注公共格式化）。

    同一记忆最近 MEMORY_DEDUP_WINDOW_ROUNDS 轮内注入过则本轮跳过（仅影响检索区行，
    不改变检索结果）；核心记忆/锚点/置顶摘要等长期画像分区不经过本函数。
    返回格式化行列表；实际注入的行同步记录轮次（供后续轮次去重）。

    #70 方案A：**flag memory_tiered_inject 关时走完全旧行为**（统一 150 字的
    format_memory_line + N 轮去重，逐字节一致）；开时 Top1→L2(240)、其余→L0 分层注入
    （复用 format_memory_line 的时间/认知/说话人/纠正标注，格式不漂移）。
    """
    from app.agent.loop import AGENT_FLAGS

    # 关 flag：完全旧行为（统一 150 字 + N 轮去重 + 说话人标注）——回归保护
    if not AGENT_FLAGS.get("memory_tiered_inject", False):
        lines = []
        injected = []
        for m in _filter_recently_injected(character_id, retrieved):
            # X-2（2026-08-18）：说话人标注（[你说的]/[TA说的]，speaker_type 无值不加），
            # 让 LLM 区分「用户亲口说的（FACT）」与「AI 自己推测的（INFERRED）」；认知前缀（epistemic）之后、内容之前
            _line = format_memory_line(m, include_speaker=True)
            if _line:
                lines.append(_line)
                injected.append(m)
        if injected:
            _mark_memories_injected(character_id, injected)
        return lines

    # 开 flag：Top1 L2 / 其余 L0（L1 桥接由 memories_section 异步补挂）；N 轮去重语义与关 flag 一致
    from app.memory.tiers import tiered_memory_lines
    candidate = list(_filter_recently_injected(character_id, retrieved))
    lines = tiered_memory_lines(candidate, include_speaker=True)
    if candidate:
        _mark_memories_injected(character_id, candidate)
    return [ln for ln in lines if ln]


async def _inject_core_anchors_loops(cid: int | None, uid: int, trim: dict) -> tuple[str, str, str]:
    """核心记忆 + 关系锚点 + 开放循环注入文本（X-4：核心/锚点上限按热度裁剪；失败静默，缺省"无"）。"""
    core_text = "无"
    anchors_text = "无"
    loops_text = "无"
    try:
        from app.memory.core import get_core_memories, get_relationship_anchors, get_open_loops
        if cid:
            _cores = await get_core_memories(cid, limit=trim["core_limit"])
            if _cores:
                core_text = "\n".join(
                    f"- [记录于 {str(m.created_at)[:10]}] {m.content[:120]}" + (f"（{m.core_category}）" if m.core_category else "")
                    for m in _cores
                )
            _anchors = await get_relationship_anchors(cid, uid, limit=trim["anchor_limit"])
            if _anchors:
                anchors_text = "\n".join(f"- [记录于 {str(m.created_at)[:10]}] {m.content[:120]}" for m in _anchors)
            _loops = await get_open_loops(cid, uid)
            if _loops:
                loops_text = "\n".join(f"- [PLANNED] {l}" for l in _loops)
    except Exception as e:
        _logger.warning("Core/anchors/loops inject failed: %s", e)
    return core_text, anchors_text, loops_text


# ------------------------------------------------------------------ section builder（注册表接入）

async def _get_core_anchors_loops(state: dict, ctx: dict) -> tuple[str, str, str]:
    """P4-1（2026-08-31）：core/anchors/loops 三槽共享一次 _inject_core_anchors_loops 结果。

    此前三个 section builder 各自完整调用（get_core_memories + get_relationship_anchors +
    get_open_loops 各 3 次）；现按 ctx 缓存合并为 1 次，结果一致（无副作用，纯查询）。
    """
    if ctx.get("_core_anchors_loops") is None:
        ctx["_core_anchors_loops"] = await _inject_core_anchors_loops(
            state.get("character_id"), state.get("user_id", 1), ctx["trim"]
        )
    return ctx["_core_anchors_loops"]


async def memories_section(state: dict, ctx: dict) -> str:
    """memories 分区：检索区记忆行（template 槽；无检索结果缺省「暂无」）。"""
    character_id = state["character_id"]
    retrieved = state.get("retrieved_memories", [])
    # 先取「本轮实际注入」候选：_build_retrieved_memory_lines 内部会再次过滤并标记注入轮次，
    # 若在其后再过滤会把刚标记的记忆排除掉，故在构建行之前先取 candidate（语义一致）。
    candidate = _filter_recently_injected(character_id, retrieved)
    # Ariadne 模块 C（2026-09-04）：沿链半故事化组装（flag memory_story_assemble 默认关）。
    # 链建链器另案（get_chain_index_for_hits 空实现恒返回 {}）——空 index 走原路径逐字节等价；
    # 建链器落地后此处自动启用真实组装（行受检索区 token 配额硬裁剪，组装只重排不创作）。
    chain_index: dict = {}
    try:
        from app.agent.loop import AGENT_FLAGS as _af
        if _af.get("memory_story_assemble", False):
            from app.memory.story_assemble import get_chain_index_for_hits
            chain_index = await get_chain_index_for_hits([m.get("id") for m in candidate]) or {}
    except Exception as _e:
        _logger.warning("story chain index load failed: %s", _e)
        chain_index = {}
    if chain_index:
        from app.memory.story_assemble import assemble_story_lines
        lines = assemble_story_lines(candidate, chain_index)
        if candidate:
            _mark_memories_injected(character_id, candidate)  # 与原路径的注入轮次标记语义一致
    else:
        lines = _build_retrieved_memory_lines(character_id, retrieved)
    # #70 方案A：flag 开时给 Top1 挂 L1 桥接（当日日摘要；「非今天」才挂，今天由 chat_history 分区覆盖；
    # 锚定「本轮实际注入」的 Top1 = N 轮去重后的 candidate[0]（而非原始检索首位），
    # candidate 为空则不挂 L1；失败 warning，不阻塞主链路）。
    try:
        from app.agent.loop import AGENT_FLAGS
        if AGENT_FLAGS.get("memory_tiered_inject", False) and lines:
            from app.memory.tiers import load_l1_summary
            from app.utils.timeutil import now_naive_utc
            _top = candidate[0] if candidate else None
            if _top is not None:
                date_str = str(_top.get("created_at") or "")[:10]  # YYYY-MM-DD
                today_str = now_naive_utc().strftime("%Y-%m-%d")
                if date_str and date_str != today_str:
                    summary = await load_l1_summary(character_id, date_str)
                    if summary:
                        lines.append(f"└ [那天 {date_str}] {summary}")
    except Exception as _e:
        _logger.warning("memories L1 bridge failed char=%s: %s", character_id, _e)
    return "\n".join(lines) or "\u6682\u65e0"


async def core_memories_section(state: dict, ctx: dict) -> str:
    """core_memories 分区：核心记忆（template 槽；长期画像不参与 N 轮去重）。"""
    core, _, _ = await _get_core_anchors_loops(state, ctx)
    return core


async def anchors_section(state: dict, ctx: dict) -> str:
    """anchors 分区：关系锚点（template 槽；长期画像不参与 N 轮去重）。"""
    _, anchors, _ = await _get_core_anchors_loops(state, ctx)
    return anchors


async def open_loops_section(state: dict, ctx: dict) -> str:
    """open_loops 分区：开放循环（template 槽）。"""
    _, _, loops = await _get_core_anchors_loops(state, ctx)
    return loops


register_section(ContextSection(
    key="memories",
    builder=memories_section,
    target=TARGET_TEMPLATE,
    slot="memories",
    quota_tokens=_MEMORIES_QUOTA_TOKENS,
    order=40,
))
async def recall_capability_section(state: dict, ctx: dict) -> list[str]:
    """recall_capability 分区（Ariadne 模块 B，2026-09-04）：[RECALL] 记忆调取规则声明。

    flag ``memory_recall_second_hop`` 默认关 → 返回空（零行为变化）；开时告知主模型
    何时允许输出 [RECALL]（给规则而非自由发挥，防凑话式调取）。"""
    try:
        from app.agent.loop import AGENT_FLAGS as _af
        if not bool(_af.get("memory_recall_second_hop", False)):
            return []
    except Exception:
        return []
    return ["【记忆调取规则】系统已在上方提供与本轮最相关的记忆。仅当出现以下情况，才允许输出一行 "
            "[RECALL]关键词（时间=YYYY-MM，可选）[/RECALL] 来调取更多记忆，且每轮至多一次："
            "1) 用户提及更早的往事/具体时间段，而上方没有对应时间的记忆；"
            "2) 需要把两件相关的事联系起来（多跳），上方只出现其中一件；"
            "3) 上方记忆明显不足以回答，且你确知「以前聊过」。"
            "不允许为了凑话而调取；调取后直接给最终回复，不要向用户解释检索过程。"]


register_section(ContextSection(
    key="recall_capability",
    builder=recall_capability_section,
    target=TARGET_APPEND,
    order=39,
))

register_section(ContextSection(
    key="core_memories",
    builder=core_memories_section,
    target=TARGET_TEMPLATE,
    slot="core_memories",
    quota_tokens=_CORE_MEMORIES_QUOTA_TOKENS,
    order=41,
))
register_section(ContextSection(
    key="anchors",
    builder=anchors_section,
    target=TARGET_TEMPLATE,
    slot="anchors",
    quota_tokens=_ANCHORS_QUOTA_TOKENS,
    order=42,
))
register_section(ContextSection(
    key="open_loops",
    builder=open_loops_section,
    target=TARGET_TEMPLATE,
    slot="open_loops",
    quota_tokens=_OPEN_LOOPS_QUOTA_TOKENS,
    order=43,
))
