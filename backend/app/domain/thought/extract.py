# -*- coding: utf-8 -*-
"""念头池 T2 · 抽取规则（六个来源面 F1–F6）——纯函数，零 IO。

口径来源：``AMBRACE_批4_念头池T2_详细设计_v1_20260929.md`` §2.1（触发面表 F1–F6 各行）、
§2.2（「文本归一后长度」的度量口径）、§3.2（与 ``life_share`` / ``unfinished_topic`` /
朋友圈原文的排他边界）。每条规则函数自己的 docstring 再指明它对应 §2.1 的哪一行。

边界（强约束，M0 零行为）：**零 IO、零 ORM、零 DB、零 flag、零网络、零业务 import**——
只接受基础类型（str/int/float/dict/tuple/frozenset）入参、只返回基础类型。真实取数在
``backend/scripts/thought_replay.py``；本模块一行都不碰。风格与
``domain/relational/drives.py``（批 3 T1 M1a）同级同规格。

draft 的统一形状（＝后续 M1 池行的雏形，字段名对齐设计 §2.5 方案 C)::

    {"text", "source_type", "source_ref", "character_id", "user_id",
     "epistemic_status", "status_hint"}

``status_hint`` 只有两种取值：``"spark"``（正常入池候选）与 ``"spent"``（设计 §3.2 第 1 行
方案 A：同一信号已被 ``life_share`` 当场讲掉，只写一条留痕行、永不参与选择）。
"""
from __future__ import annotations

import hashlib
import re

# ── 六个来源面（设计 §2.1 表的 F1–F6 行；值即落库 source_type）────────────────
SRC_ACTIVITY = "activity"    # F1 活动产物
SRC_REFLECT = "reflect"      # F2 反思/复盘
SRC_MOMENT = "moment"        # F3 朋友圈沉淀
SRC_USER_HOOK = "user_hook"  # F4 用户钩子
SRC_FACT = "fact"            # F5 新事实余波
SRC_INTEREST = "interest"    # F6 兴趣演化

SOURCE_TYPES: tuple[str, ...] = (
    SRC_ACTIVITY, SRC_REFLECT, SRC_MOMENT, SRC_USER_HOOK, SRC_FACT, SRC_INTEREST,
)

# ── 咸度权重 w(source)：设计 §2.2 salt 公式一栏逐值照抄（初值，必须回标定）────
SALT_WEIGHT_BY_SOURCE: dict[str, float] = {
    SRC_ACTIVITY: 1.0,    # w(create/learn)
    SRC_REFLECT: 0.8,     # w(reflect)
    SRC_MOMENT: 0.6,      # w(moment)
    SRC_USER_HOOK: 1.2,   # w(user_hook)
    SRC_FACT: 0.7,        # w(fact)
    SRC_INTEREST: 0.5,    # w(interest)
}

# ── F1：可产生念头的活动类型 ──
# 设计 §2.1 F1 行原文写「活动类型为 create/learn」。仓库 ``life_activity_logs.activity_type``
# 近 30 天的实际取值里学习类叫 ``study``（``learn`` 只出现在 life_share 的概率表键上），
# 故这里把 ``study`` 一并列入——**口径落地订正，不改任何活动语义**（回放报告第 6 节同条注明）。
F1_ACTIVITY_TYPES: frozenset[str] = frozenset({"create", "learn", "study"})
F1_ACTIVITY_STATUS_OK: frozenset[str] = frozenset({"completed"})
# 抽取阶段只做**列宽截断**（设计 §2.5 text 字段 ≤120），不提前替过滤① 放行：
# 长正文截成 40 字照样能过 §2.2①，那等于把「抄原文」洗白成合法候选（2026-09-29 回放实测
# 正是如此——F5 一路 4000+ 条全靠预截断混过长度闸）。长度判定只归 §2.2① 一处。
POOL_TEXT_MAX_LEN = 120

# ── F2：复盘文本切句与触发词（设计 §2.1 F2 行「按分隔切 ≤3 条候选，命中词表才成念」）──
F2_MAX_CANDIDATES = 3
F2_SEPARATORS = "。！？!?；;\n\r"
F2_TRIGGER_WORDS: tuple[str, ...] = ("想", "要不要", "试试", "下次")
# 设计 §2.1 F2 行写 sub_type plan/review（`agent/reflection.py:48`）。仓库 ai_reflection
# 多数行 sub_type 为 NULL，故默认放宽到「含 NULL」；置 False 即回到严格口径。
F2_SUB_TYPES: frozenset[str] = frozenset({"plan", "review"})
F2_ACCEPT_NULL_SUB_TYPE = True

# ── F3：朋友圈「讲了没人理」（设计 §2.1 F3 行）──
# 7 天内无人评论/点赞才成念；窗口未走完的行不判定（避免「还没人来得及理」被当冷场）
F3_ENGAGEMENT_WINDOW_DAYS = 7
# 成念文本是**对那条朋友圈的回望**（设计原话：「这事我讲了没人理」成念），不抄原文——
# 这既是 §3.2 最后一行「原文哈希去重」天然不自我封锁的原因，也是 M1 前待标定的占位文案。
F3_TEXT_TEMPLATE = "之前提过一嘴{value}，没人接"

# ── F4：用户钩子（设计 §2.1 F4 行 + §3.2 第 2 行防线①）──
F4_IDLE_DAYS = 3.0                      # last_touched_at 距今 ≥3 天
F4_ACTIVE_STATUS = "进行中"              # conversation_topics.status 现值（中文枚举）
# 与 unfinished_topic 通道词表同源（`scheduling/unfinished_topic.py:22`）。本模块为零业务
# import 的纯函数，故**就地声明同一份词表**，不 import 该模块；两处若漂移以生产词表为准。
UNFINISHED_TOPIC_KEYWORDS: tuple[str, ...] = (
    "下次", "以后", "改天", "有空", "回头", "到时候", "再聊", "晚点", "找时间",
)

# ── F5：新事实余波（设计 §2.1 F5 行，provenance 与批 2 召回门共用、不另立真相源）──
F5_EPISTEMIC_ACCEPT: frozenset[str] = frozenset({"INFERRED", "UNVERIFIED"})
F5_TEXT_TEMPLATE = "想跟ta确认一下：{value}"   # 占位文案，M1 前待标定（不进 prompt、不可见）

# ── F6：兴趣演化（设计 §2.1 F6 行「强度变化（新增/上升）」）──
# 仓库 life_interests 无历史快照表，「上升」离线不可判；M0 只按 created_at 落在窗口内
# 视为「新增」。置 prev_level 时本函数会走上升分支，为 M1 预留同一判据。
F6_TEXT_TEMPLATE = "最近又惦记上{name}了"      # 占位文案，同上
F6_MAX_NAME_LEN = 20

# ── 文本归一（设计 §2.2 过滤①「文本归一后长度」的度量口径）──
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(
    r"[，。、！？；：、「」『』“”‘’（）《》【】〈〉…—－··,\.!\?;:\"'`~@#\$%\^&\*\(\)\[\]\{\}<>_\/\\\|\+=\-]+"
)


def normalize_text(text: str | None) -> str:
    """归一：折叠空白 → 去标点 → ASCII 转小写（设计 §2.2 过滤① 与幂等哈希共用此口径）。

    **只用于度量与去重，展示文本仍是原文**（用户/角色产生的内容一律不改动，见 AGENTS.md
    「语言边界」）。
    """
    if not text:
        return ""
    s = _WHITESPACE_RE.sub("", str(text).strip())
    s = _PUNCTUATION_RE.sub("", s)
    return s.lower()


def normalized_length(text: str | None) -> int:
    """归一后长度（「字」＝归一串里的字符数，中英文均按 1 计）。"""
    return len(normalize_text(text))


def text_hash(text: str | None) -> str:
    """归一文本的 sha256 前 16 位——设计 §2.5 唯一约束里的 ``text_hash`` 幂等位。"""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:16]


def _draft(
    text: str,
    source_type: str,
    source_ref: str,
    character_id: int | None,
    user_id: int | None,
    epistemic_status: str | None = None,
    status_hint: str = "spark",
) -> dict:
    """构造统一形状的 draft（未知 source_type 直接拒，防脏面混入）。"""
    return {
        "text": str(text).strip(),
        "source_type": source_type,
        "source_ref": str(source_ref),
        "character_id": character_id,
        "user_id": user_id,
        "epistemic_status": epistemic_status,
        "status_hint": status_hint if source_type in SALT_WEIGHT_BY_SOURCE else "rejected",
    }


def split_candidates(text: str | None, max_n: int = F2_MAX_CANDIDATES) -> list[str]:
    """按分隔符切句并取前 ``max_n`` 条（设计 §2.1 F2 行「按分隔切 ≤3 条候选」）。

    只做切分不做取舍判断——成不成念由调用方（``extract_reflection``）过词表。
    """
    if not text:
        return []
    out: list[str] = []
    buf: list[str] = []
    for ch in str(text):
        if ch in F2_SEPARATORS:
            seg = "".join(buf).strip()
            buf = []
            if seg:
                out.append(seg)
                if len(out) >= max_n:
                    return out
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail and len(out) < max_n:
        out.append(tail)
    return out[:max_n]


def extract_activity(row: dict, shared_refs: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """F1 活动产物（设计 §2.1 F1 行 + §3.2 第 1 行「同源归属二选一」）。

    判据：``activity_type ∈ F1_ACTIVITY_TYPES`` 且 ``status == completed``。
    归属二选一（推荐口径＝方案 A）：``source_ref`` 已在 ``shared_refs``（本次活动已被
    ``life_share`` 讲掉）→ 只出 ``status_hint="spent"`` 的留痕 draft，不以 spark 入池；
    未讲过 → 以 ``spark`` 入池。
    """
    if str(row.get("activity_type") or "") not in F1_ACTIVITY_TYPES:
        return []
    if F1_ACTIVITY_STATUS_OK and str(row.get("status") or "") not in F1_ACTIVITY_STATUS_OK:
        return []
    summary = str(row.get("summary") or "").strip() or str(row.get("activity_type") or "")
    summary = summary[:POOL_TEXT_MAX_LEN]
    ref = str(row.get("id") or row.get("source_ref") or "")
    hint = "spent" if ref in set(shared_refs) else "spark"
    return [
        _draft(
            summary, SRC_ACTIVITY, ref,
            row.get("character_id"), row.get("user_id"),
            epistemic_status=row.get("epistemic_status"), status_hint=hint,
        )
    ]


def extract_reflection(row: dict) -> list[dict]:
    """F2 反思/复盘（设计 §2.1 F2 行）：切 ≤3 条候选，命中 ``F2_TRIGGER_WORDS`` 才成念。

    ``sub_type`` 门：默认 plan/review（NULL 是否放行由 ``F2_ACCEPT_NULL_SUB_TYPE`` 决定）。
    """
    sub_type = row.get("sub_type")
    if sub_type is not None and str(sub_type) not in F2_SUB_TYPES:
        return []
    if sub_type is None and not F2_ACCEPT_NULL_SUB_TYPE:
        return []
    ref = str(row.get("id") or row.get("source_ref") or "")
    drafts = []
    for i, seg in enumerate(split_candidates(row.get("content"))):
        if not any(w in seg for w in F2_TRIGGER_WORDS):
            continue
        drafts.append(
            _draft(
                seg, SRC_REFLECT, f"{ref}#{i}",
                row.get("character_id"), row.get("user_id"),
                epistemic_status=row.get("epistemic_status"),
            )
        )
    return drafts


def extract_moment(
    row: dict,
    blocked_text_hashes: frozenset[str] | set[str] = frozenset(),
) -> list[dict]:
    """F3 朋友圈沉淀（设计 §2.1 F3 行 + §3.2 最后一行「原文哈希去重」）。

    成念条件（三条全满足）：① 已过 7 天观察窗（``age_days >= F3_ENGAGEMENT_WINDOW_DAYS``）
    ② 窗口内零互动（``engagement_count == 0``；``None``＝互动未知，一律不判冷场）
    ③ **成念文本**的归一哈希不在 ``blocked_text_hashes``（§3.2 最后一行「已在 ``ai_moments``
    出现过的原文按文本哈希去重不入池」——去重位是入池文本，故 F3 的回望文案不会被自己那条
    朋友圈原文封锁，但同一条重复抽取/他面照抄原文会撞上 §2.5 的 ``text_hash`` 唯一约束）。
    """
    if float(row.get("age_days") or 0.0) < F3_ENGAGEMENT_WINDOW_DAYS:
        return []
    engagement = row.get("engagement_count")
    if engagement is None or int(engagement) > 0:
        return []
    draft_text = F3_TEXT_TEMPLATE.format(value=str(row.get("content") or "").strip()[:POOL_TEXT_MAX_LEN])
    if text_hash(draft_text) in set(blocked_text_hashes):
        return []
    return [
        _draft(
            draft_text, SRC_MOMENT,
            str(row.get("id") or row.get("source_ref") or ""),
            row.get("character_id"), row.get("user_id"),
            epistemic_status=row.get("epistemic_status"),
        )
    ]


def extract_user_hook(
    row: dict,
    unfinished_keywords: tuple[str, ...] = UNFINISHED_TOPIC_KEYWORDS,
) -> list[dict]:
    """F4 用户钩子（设计 §2.1 F4 行 + §3.2 第 2 行防线①）。

    成念条件：话题 ``status == "进行中"`` 且 ``idle_days >= F4_IDLE_DAYS``（「他上次说了一半」），
    且话题文本**未被** ``unfinished_topic`` 词表命中——命中即该话题已被那条通道占用，双向排除。
    """
    if str(row.get("status") or "") != F4_ACTIVE_STATUS:
        return []
    if float(row.get("idle_days") or 0.0) < F4_IDLE_DAYS:
        return []
    topic = str(row.get("topic") or "").strip()
    if any(k in topic for k in unfinished_keywords):
        return []
    return [
        _draft(
            topic[:POOL_TEXT_MAX_LEN], SRC_USER_HOOK,
            str(row.get("id") or row.get("source_ref") or ""),
            row.get("character_id"), row.get("user_id"),
            epistemic_status=row.get("epistemic_status"),
        )
    ]


def extract_fact(row: dict) -> list[dict]:
    """F5 新事实余波（设计 §2.1 F5 行）：``epistemic_status ∈ {INFERRED, UNVERIFIED}`` 才成念。

    文本按 ``F5_TEXT_TEMPLATE`` 包装成「想跟ta确认一下…」；FICTIONAL 不入池由 §2.2 过滤③
    统一兜底，这里靠白名单已经先挡了一道。
    """
    if str(row.get("epistemic_status") or "") not in F5_EPISTEMIC_ACCEPT:
        return []
    value = str(row.get("value") or row.get("object_value") or "").strip()[:POOL_TEXT_MAX_LEN]
    if not value:
        return []
    return [
        _draft(
            F5_TEXT_TEMPLATE.format(value=value), SRC_FACT,
            str(row.get("id") or row.get("source_ref") or ""),
            row.get("character_id"), row.get("user_id"),
            epistemic_status=row.get("epistemic_status"),
        )
    ]


def extract_interest(row: dict) -> list[dict]:
    """F6 兴趣演化（设计 §2.1 F6 行）：新增（无 prev_level）或强度上升才成念。

    ``prev_level`` 缺省＝离线无历史，按「窗口内新建」视同新增；给了旧值时只在**变高**时成念，
    持平/下降一律不出（防漂移任务每轮重复灌池）。
    """
    name = str(row.get("name") or row.get("title") or "").strip()[:F6_MAX_NAME_LEN]
    if not name:
        return []
    level = int(row.get("level") or 0)
    prev = row.get("prev_level")
    if prev is not None and level <= int(prev):
        return []
    return [
        _draft(
            F6_TEXT_TEMPLATE.format(name=name), SRC_INTEREST,
            str(row.get("id") or row.get("source_ref") or ""),
            row.get("character_id"), row.get("user_id"),
            epistemic_status=row.get("epistemic_status"),
        )
    ]


FACE_EXTRACTORS = {
    SRC_ACTIVITY: extract_activity,
    SRC_REFLECT: extract_reflection,
    SRC_MOMENT: extract_moment,
    SRC_USER_HOOK: extract_user_hook,
    SRC_FACT: extract_fact,
    SRC_INTEREST: extract_interest,
}
