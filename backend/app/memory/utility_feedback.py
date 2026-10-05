"""召回后「效用反馈」（Slowave 式，小增量 2026-09-16）

flag ``memory_utility_feedback`` 已预注册（默认关）；本模块全部逻辑在该 flag 开后生效，
关时 ``schedule_utility_feedback`` 直接返回、零行为变化（不读不写）。

目标：补上「这条召回到底有没有用上」的缺失信号，用来微调记忆 salience/衰减——
我们已有艾宾浩斯强化、reliability（矛盾纠正）、tiering（低置信加速退化），缺的正是这一环。

信号（确定性规则，不引入 LLM；**用户侧措辞只看用户消息，「用没用上」只看 AI 回复**）：
- negative：**用户消息**命中**纠正词**（在 reliability.CORRECT_WORDS 基础上扩量，
  ``_UTILITY_EXTRA_CORRECT_WORDS``）→ 用户改口/否认，单命中即整轮降权（不要求记忆片段共现）；
- positive：记忆关键片段出现在**AI 回复**（被用上）→ 微上调；
- neutral：以上皆无 → 不调整（无关闲聊不误记）。
  （放宽点 2026-09-21 L2 任务2：原「纠正词 + 记忆被引用」双命中口径在 char13 灰度 29h 仅 1 positive /
  0 negative，几近抓不到信号；改为任一强信号即记，并扩大纠正词表。
  收口点 2026-10-04 A25 方案 a：放宽时把两段拼成一串扫，于是**角色自己说「记错了」也被当成用户改口**。
  按回执逐条回放的账（39 条，2026-10-05 生产库只读复核，修复前判据可复现 34/39）：32 条 negative 里
  23 条命中词只在角色侧、4 条用户侧确有改口措辞、5 条因判定文本未落库而无法归因 ⇒ 误降权 20 条记忆
  ⇒ 改回两段各扫各的（历史误降权已于 10-05 按账逐条回滚，见 dev-changelog 的 A25 条目）。
  再收口点 2026-10-05 A25②：**撤掉「用户明确表态词」这条 positive 通道**。实测依据＝25 个表态词在
  session 11 的 2612 条真实用户消息里只命中 **1 次**（「你记得」；09-20 起的 245 条里 0 次），
  而回放出的 5 条误升权回执正是表态词命中在**角色自己**的话上（拼扫时代）
  ⇒ 它几乎不产出真信号、却是假 positive 的来源；positive 从此只认「回复引用了记忆片段」这条
  问得对、也还在响的通道。判效详见 docs/dev-changelog.md 2026-10-04／10-05 的 A25 条目。）

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
# A27甲（2026-10-05）：判定文本随回执留底的截断长度（两段各计）。留底是为了事后能归因，
# 不是把聊天记录再存一份 ⇒ 取够判定的长度即可，别把回执撑成日志表。
UTILITY_EVIDENCE_MAX_CHARS = 400
# ── 灰度白名单（2026-09-18 用户拍板：先只在 char13 观察）─────────────────────────
# 口径与 section_working_state.WORKING_STATE_INJECT_GRAY_CHARS / domain/proactivity/pacing.py
# 的 OUTREACH_PACING_GRAY_CHARS 一致：**总开关开 且 角色命中白名单** 才生效，其余角色零行为变化。
# 扩量 = 把角色加进来；热回滚 = 关总开关 memory_utility_feedback（无需改代码）。
UTILITY_FEEDBACK_GRAY_CHARS = frozenset({13})


_CORRECT_WORDS: tuple | None = None  # 延迟取自 reliability（避免顶层循环依赖）


def _flag_on(character_id=None) -> bool:
    """读 feature flag + 灰度白名单；任何异常回落 False（关=逐字节旧行为）。

    口径（与 M3-b section_working_state / outreach pacing 灰度一致）：
    **总开关 memory_utility_feedback 开 且 角色命中 UTILITY_FEEDBACK_GRAY_CHARS** 才生效；
    角色为空 / 非法 / 非白名单 → False（保守，零行为变化）。
    """
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        if not AGENT_FLAGS.get("memory_utility_feedback", False):
            return False
    except Exception:
        return False
    if character_id is None:
        return False
    try:
        cid = int(character_id)
    except (TypeError, ValueError):
        return False
    return cid in UTILITY_FEEDBACK_GRAY_CHARS


def is_enabled(character_id=None) -> bool:
    """flag + 灰度是否对本角色开启（供调用点早判：关时调用方不产生任何状态改动）。

    与 schedule_utility_feedback 内的判定复用同一实现，二者不会分叉；
    character_id 必传才能真正生效（白名单口径），缺省即 False。
    """
    return _flag_on(character_id)


def _correction_words() -> tuple:
    global _CORRECT_WORDS
    if _CORRECT_WORDS is None:
        try:
            from app.memory.reliability import CORRECT_WORDS
            _CORRECT_WORDS = CORRECT_WORDS + _UTILITY_EXTRA_CORRECT_WORDS
        except Exception:
            _CORRECT_WORDS = _UTILITY_EXTRA_CORRECT_WORDS
    return _CORRECT_WORDS


# 效用反馈专用纠正词（在 reliability.CORRECT_WORDS 基础上扩量，2026-09-21 L2 任务2）：
# 只影响效用反馈判据，不动 reliability 的「矛盾/纠正」语义（红线：记忆写入语义不变）。
# 扩量的直接动机：char13 灰度 29h 内 12 个原纠正词在 342 条消息上命中 0 —— 词面太窄几乎不触发。
_UTILITY_EXTRA_CORRECT_WORDS = (
    "不对", "错了", "记反了", "记岔了", "搞反了", "你搞反了", "想错了", "理解有误",
    "记混了", "不是那回事", "完全错了", "大错特错", "和我说的相反", "恰恰相反",
    "恰好相反", "不是这样", "记错了吧", "弄错了", "说反了", "你想多了", "搞错了",
    "记差了", "理解错了", "你理解反了", "正好相反",
)

# 注：2026-10-05 A25② 撤掉了原 ``_UTILITY_ATTITUDE_WORDS``（25 个「明确表态词」）通道，
# 词表一并删除而非留着当摆设——它在 2612 条真实用户消息里只命中 1 次，却是 5 条误升权的来源。


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

    **两条通道各扫各的**（2026-10-04 A25 方案 a 定形，2026-10-05 A25② 收口为两条）：
    - ① 纠正词（含扩量后的 ``_UTILITY_EXTRA_CORRECT_WORDS``）只扫 **user_message** → negative；
    - ② 记忆关键片段被引用只扫 **ai_response** → positive（记忆被用上）；
    - 其余 → neutral（无关闲聊不误记）。

    ``user_message`` 缺省为空 ⇒ 只有 ② 可能成立（此时没有任何「用户改口」的证据）。

    为什么不再把两段拼起来扫（实测证据，生产库只读，session 11 全量 2612 条用户消息／4868 条回复）：
    拼扫把**角色自己的措辞**当成用户改口——39 条效用回执逐条回放（修复前判据可复现 34/39）：32 条 negative 里
    23 条命中词只在角色侧（如「那我记岔了」「行，记错了，练背就练背」），4 条用户侧确有改口措辞，
    5 条无法归因（判定文本没落库，见下方观测边界）⇒ 20 条记忆属误降权。

    为什么撤掉「③ 明确表态词」通道（A25②，2026-10-05）：25 个表态词在 2612 条真实用户消息里**只命中 1 次**
    （「你记得」；09-20 起的 245 条里 0 次），而回放出的 5 条误升权回执正是它命中在**角色自己**的话上
    ——几乎不产出真信号、稳定产出假 positive，故删除词表，positive 只认 ②。

    观测边界（2026-10-05 只读复核发现，判据侧**已知未解**）：本判定发生在 ``generate_response`` 末尾，
    而开头的括号内心活动、成对动作标记要到 ``split_response``／落库清洗才剥离
    （图 ``perceive→retrieve→build_context→generate→reflect``，切分在服务层）⇒ ② 可能读到
    用户从未看到的角色独白（上面 5 条无法归因即出自这个取证缺口，属高度可疑而非铁证）。
    同日的 A27甲 已把**判定文本随回执落库** ⇒ 这条边界从此可审计（下次再出现"归不了因"能直接看到当时读了什么）。
    """
    u = user_message or ""
    resp = ai_response or ""
    # ① 纠正词（用户侧）→ negative：仍不要求与记忆片段共现（09-21 的放宽点保留）
    if any(w in u for w in _correction_words()):
        return "negative"
    # ② 记忆关键片段被**回复**引用 → positive（只看 AI 文本，用户自己复述不算「被用上」）
    if _contains_key_fragment(_core_snippet(memory_content), resp):
        return "positive"
    return "neutral"


async def apply_utility_feedback(
    character_id: int,
    user_id: int,
    items: list[tuple[int, str]],
    round_id: int | None = None,
    evidence: dict | None = None,
) -> None:
    """把效用信号落到记忆（写 M3 回执 + importance 微调）。失败静默、不抛。

    ``items``：[(memory_id, signal), ...]，signal ∈ {positive, negative, neutral}；
    neutral 跳过（不写不调）。
    ``evidence``（A27甲，2026-10-05）：判定时真正读到的两段文本（已截断），随回执落库。
    加它的唯一理由＝**事后可归因**——10-05 逐条回放 39 条回执时有 5 条再也对不上文本，
    因为判定文本从来没留过底。只多写两个键，权重与信号判定一字不变。
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
                            evidence=evidence,
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
                    detail_json=_detail_json(mid, sig, round_id, user_id=user_id, evidence=evidence),
                ))
            await db.commit()
    except Exception as e:
        _logger.warning("utility_feedback apply failed char=%d: %s", character_id, e)


def _detail_json(
    memory_id: int, signal: str, round_id: int | None,
    skipped: bool = False, user_id: int | None = None,
    evidence: dict | None = None,
) -> str:
    """回执 detail：谁（user_id）/哪条记忆/信号/回合/是否跳过（时间由 receipt.created_at 记）。

    ``evidence`` 存在时再多两个键（``evidence.user_message``／``evidence.ai_response``），
    缺省时**一个键都不加**——旧调用方与历史回执的 JSON 形态逐字节不变。
    """
    import json as _json
    payload = {
        "memory_id": memory_id,
        "signal": signal,
        "round_id": round_id,
        "user_id": user_id,
        "skipped": skipped,
    }
    if evidence:
        payload["evidence"] = dict(evidence)
    return _json.dumps(payload, ensure_ascii=False)


def _note(character_id, kind: str, n: int) -> None:
    """轻量埋点：区分「闸门没触发」与「触发了但判 neutral / 判出信号」（2026-09-18 补）。

    背景：灰度开启首日 `memory_write_receipts` 里**没有** utility 回执，无法区分是
    「本轮根本没触发」还是「触发了但三类信号全 neutral（按设计不写回执）」——这层不可观测会让
    后续观察无据可依。metric `utility_feedback_note`，detail=`{"kind": neutral|scheduled, "n": N}`；失败静默。
    """
    try:
        from app.memory.observability import obs_event

        obs_event(character_id, "utility_feedback_note", {"kind": kind, "n": n})
    except Exception:
        pass


def visible_reply_text(text: str) -> str:
    """A27乙（2026-10-05）：把「用户真看到的正文」算出来给判据用——**与落库清洗同一个函数**。

    为什么判据要过这一道：本判定跑在 `generate_response` 末尾，此刻开头的括号内心活动还没剥
    （要到 `split_response`／落库清洗才剥），所以 ②「记忆被回复引用」可能只命中在角色独白里
    ——用户从没看见过的文本也能给记忆加权。10-05 只读实测：近 7 天 348 条 AI 回复里
    **92 条（26.4%）带被剥掉的独白**（独白原文在 `chat_messages.extra_meta.reasoning`），
    这就是判定读到独白的概率上限。

    刻意复用 `agent.context.reasoning_prompt.extract_leading_bracket_reasoning`（生产清洗就是它），
    **不在这里另写一套剥离规则**——两把尺子迟早会漂。函数内 import 是为了不把 memory→agent 的
    依赖提到模块导入期（循环导入的历史坑）。任何异常都退回原文：那只是回到 10-05 之前的旧行为，
    不会因为观测补丁把判定整个掐掉，且会留一条 WARNING 让人看得见它发生了。
    """
    if not text:
        return text or ""
    try:
        from app.agent.context.reasoning_prompt import extract_leading_bracket_reasoning

        visible, _inner = extract_leading_bracket_reasoning(text)
        return visible if visible is not None else text
    except Exception as e:
        _logger.warning("visible_reply_text fallback to raw text: %s", e)
        return text


def schedule_utility_feedback(
    character_id: int,
    user_id: int,
    recalled: list[dict],
    ai_response: str,
    round_id: int | None = None,
    user_message: str = "",
) -> None:
    """fire-and-forget 入口（不阻塞回复）。``recalled`` = state['retrieved_memories']（含 id/content）。

    ``user_message``：本轮用户原始消息——纠正词**只在这一段里找**（用户改口才算改口，角色自己的措辞不算，
    A25 方案 a）；缺省时 ① 不触发，只有「记忆被回复引用」②可能成立。
    flag 关 → 直接返回（零行为变化）。异步失败不影响主流程。

    A27甲：这里顺手把**判定时真正用到的两段文本**（各截断 ``UTILITY_EVIDENCE_MAX_CHARS`` 字）交给
    ``apply_utility_feedback`` 随回执落库 ⇒ 以后任何一条回执都能回答「当时到底读了什么才判成这样的」。

    A27乙（2026-10-05）：② 的输入从「原始 ai_response」换成**剥掉角色独白后的可见正文**
    （`visible_reply_text`），这样「记忆被回复引用」只在用户真看见的话上成立。
    evidence 里**两段都留**（`ai_response`＝剥前原文、`ai_response_visible`＝判据实际读的），
    差值随时可离线复算——要不要再把判定挪到服务层（乙-重）就靠这些数决定，不靠争论。
    """
    if not _flag_on(character_id):
        return
    if not recalled or not (ai_response or "").strip():
        return
    try:
        judged_response = visible_reply_text(ai_response)
        items: list[tuple[int, str]] = []
        for r in recalled:
            mid = r.get("id")
            if mid is None:
                continue
            sig = classify_utility_signal(r.get("content") or "", judged_response, user_message=user_message)
            if sig != "neutral":
                items.append((int(mid), sig))
        if not items:
            # 召回了记忆但三类信号全 neutral（按设计不写回执）→ 留一条埋点，避免「没触发/判中性」不可分
            _note(character_id, "neutral", len(recalled))
            return
        _note(character_id, "scheduled", len(items))
        evidence = {
            "user_message": (user_message or "")[:UTILITY_EVIDENCE_MAX_CHARS],
            "ai_response": (ai_response or "")[:UTILITY_EVIDENCE_MAX_CHARS],
            "ai_response_visible": judged_response[:UTILITY_EVIDENCE_MAX_CHARS],
        }
        from app.utils.async_tasks import spawn_background
        spawn_background(apply_utility_feedback(character_id, user_id, items, round_id, evidence=evidence))
    except Exception as e:
        _logger.warning("schedule_utility_feedback failed: %s", e)
