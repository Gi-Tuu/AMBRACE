# -*- coding: utf-8 -*-
"""念头池 T2 · 入池三道确定性过滤（设计 §2.2「池子只进不出」的防线）。

口径来源：``AMBRACE_批4_念头池T2_详细设计_v1_20260929.md`` §2.2 末段三道过滤 ①②③，
以及 §3.2 表 / §5 第 3 条「撞句基线」——②与撞句共用同一套关键词桶。

边界：**零 IO、零 ORM、零 DB、零 flag、零网络、零业务 import**，纯函数。
"""
from __future__ import annotations

from app.domain.thought.extract import normalize_text

# ── 过滤①：归一后文本长度下/上限（设计 §2.2①「≥6 且 ≤40 字」）──
TEXT_MIN_LEN = 6
TEXT_MAX_LEN = 40

# ── 过滤②：与最近 N 条已发主动消息比关键词（设计 §2.2②，读法复用
#    ``get_recent_proactive_messages``（arbiter.py:221-269），本层不新建查询）──
RECENT_SENT_LOOKBACK = 3

# ── 过滤③：设定内容不入池（设计 §2.2③「epistemic_status 为 FICTIONAL 的一律不入」）──
EPISTEMIC_BLOCKED: frozenset[str] = frozenset({"FICTIONAL"})

# 拦截原因枚举（回放报告按此分组出「三道过滤各拦下多少」）
REASON_LENGTH = "length"
REASON_RECENT_OVERLAP = "recent_overlap"
REASON_FICTIONAL = "fictional"
INTAKE_REASONS: tuple[str, ...] = (REASON_LENGTH, REASON_RECENT_OVERLAP, REASON_FICTIONAL)

# ── 主题桶：就地复制 ``scheduling/proactive_topic_guard.py:38`` 的关键词表 ──
# 设计 §2.2② 与 §5 第 3 条明写「词表口径复用 proactive_topic_guard」；本模块零业务 import，
# 故复制常量而非 import。两处若漂移以生产表为准（回放报告第 6 节标注此风险）。
TOPIC_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("meal", ("粥", "吃饭", "饭", "吃面", "面条", "饿", "用餐", "早餐", "午饭", "晚饭",
              "宵夜", "菜凉", "趁热", "锅里")),
    ("sleep", ("睡觉", "睡了", "睡吧", "起床", "醒了", "醒一醒", "困", "休息", "早睡", "赖床", "午觉")),
    ("shower", ("洗澡", "澡", "洗头", "淋浴")),
    ("commute", ("出门", "到家", "回来路上", "去上课", "下课", "上班", "放学", "通勤")),
    ("meds", ("吃药", "药", "喝药", "头疼", "头痛", "胃疼", "不舒服", "多喝热水")),
)

_TOPIC_BUCKETS_BY_NAME = dict(TOPIC_BUCKETS)


def topic_bucket(text: str | None) -> str | None:
    """归入首个命中的主题桶（按表顺序优先，一条文本只归一个桶）——同 ``proactive_topic_guard``。

    未命中任何桶返回 ``None``（调用方须按「不可比」处理，不得当成「不撞」）。
    """
    s = normalize_text(text)
    if not s:
        return None
    for name, words in TOPIC_BUCKETS:
        if any(w in s for w in words):
            return name
    return None


def filter_length(text: str | None) -> bool:
    """过滤①：归一后长度须落在 [TEXT_MIN_LEN, TEXT_MAX_LEN]。太短没内容、太长就是抄原文。"""
    n = len(normalize_text(text))
    return TEXT_MIN_LEN <= n <= TEXT_MAX_LEN


def filter_recent_overlap(text: str | None, recent_texts: tuple[str, ...] | list[str]) -> bool:
    """过滤②：候选文本与近 ``RECENT_SENT_LOOKBACK`` 条已发主动消息不撞主题桶。

    ``recent_texts`` 由调用方截最近若干条后传入（本函数不做排序，保持纯函数）。
    候选未命中任何桶时**放行**（与 ``proactive_topic_guard`` 的 fail-open 同向：没有主题
    就没有「说重了」），命中且与某条已发同桶时拦截。
    """
    bucket = topic_bucket(text)
    if bucket is None:
        return True
    for sent in list(recent_texts)[-RECENT_SENT_LOOKBACK:]:
        if topic_bucket(sent) == bucket:
            return False
    return True


def filter_epistemic(epistemic_status: str | None) -> bool:
    """过滤③：``FICTIONAL``（设定）一律不入池。None 视为未标注，放行（由来源面自身门槛把关）。"""
    return str(epistemic_status or "") not in EPISTEMIC_BLOCKED


def intake_reject_reason(
    text: str | None,
    epistemic_status: str | None,
    recent_texts: tuple[str, ...] | list[str] = (),
) -> str | None:
    """三道过滤串行判定，返回首个拦截原因（``None`` ＝放行）。

    顺序＝设计 §2.2 ①②③ 的行序，**短路**：一条候选只记第一个拦它的原因，
    因此报告里三道拦截数之和＝被拦候选总数（不重复计数）。
    """
    if not filter_length(text):
        return REASON_LENGTH
    if not filter_recent_overlap(text, recent_texts):
        return REASON_RECENT_OVERLAP
    if not filter_epistemic(epistemic_status):
        return REASON_FICTIONAL
    return None
