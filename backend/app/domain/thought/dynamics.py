# -*- coding: utf-8 -*-
"""念头池 T2 · 双量动力学（novelty/salt 公式 + 升级/挤出判据 + 三档释放）。

口径来源：``AMBRACE_批4_念头池T2_详细设计_v1_20260929.md`` §2.2（新鲜度与咸度、状态迁移表、
挤出）、§2.3（``spent`` / ``told_flat`` / ``never_told`` 三档）；60 分钟窗口常量对齐
``api/scheduler.py:260`` 的 ``REPLY_WINDOW_MINUTES``。

边界：**零 IO、零 ORM、零 DB、零 flag、零网络、零业务 import**，纯函数。

⚠️ 下面所有阈值均为**初值，必须实测回标定**（设计 §2.2 只给了 S_OBS/TTL/容量与六个 w，
``NOVELTY_E_FOLDING_DAYS`` 与 ``SALT_BUMP_WEIGHT`` 设计未给值，由本单定为初值并在回放报告
第 6 节如实标注）。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from app.domain.thought.extract import SALT_WEIGHT_BY_SOURCE

# ── 新鲜度：e 折叠时间 τ（天）。设计 §2.2 只写了公式没给值 ⇒ 本单初值，待标定 ──
# 公式 ``novelty = exp(-age/τ)`` 里的 τ 是 **e 折叠时间**（掉到 1/e 的天数），不是半衰期：
#     真半衰期 = τ · ln2   （τ=7.0 ⇒ 约 4.85 天掉到 0.5）
#     τ = 真半衰期 / ln2 = 真半衰期 × 1.4427   （想要「真半衰期 7 天」须取 τ=10.1）
# M1 订正（方案 F §5.1）：**只改名、公式不动 ⇒ 零行为变更**，M0 全部已测读数（novelty
# p50=0.3679、挤出 90.14%）继续有效。等 M1 真要给 novelty 设阈值时再一次性切到
# ``0.5 ** (age/HALFLIFE)`` 写法并重跑标定。
NOVELTY_E_FOLDING_DAYS = 7.0
# 旧名别名：M0 单测（tests/test_thought_m0.py）与回放 ``--params`` 的既有口径按此名引用，
# 二者不在本批白名单内；两处读数与 ``novelty`` 的默认值实参一律走上面的新名。
NOVELTY_HALFLIFE_DAYS = NOVELTY_E_FOLDING_DAYS

# ── 升级判据（设计 §2.2 迁移表第 1 行）──
SALT_OBSSESSION_THRESHOLD = 2.0   # S_OBS，设计默认值 2.0
MIN_DISTINCT_HIT_SOURCES = 2      # 「≥2 个不同来源面」，单面重复命中不升级（防自说自话）

# ── 过期与容量（设计 §2.2 迁移表第 2 行 / 挤出行）──
TTL_DAYS = 21.0                   # obsession → faded（设计默认 21）
CAP_SPARK = 12                    # 每（角色×用户）活跃 spark 上限
CAP_OBSESSION = 3                 # 每（角色×用户）活跃 obsession 上限
# 设计 §2.2 只给了 spark/obsession 两个顶，没说 told_flat 占谁的额度。本单口径＝told_flat
# 与 obsession 同档（都是「已养熟且说过一次」），独立成顶避免无限堆积；此值待标定。
CAP_TOLD_FLAT = 3
ACTIVE_STATUSES: frozenset[str] = frozenset({"spark", "obsession", "told_flat"})

# ── 半释放（设计 §2.3 told_flat 行）──
SALT_TOLD_FLAT_RATIO = 0.35       # salt *= 0.35（与 T1 DRIVE_OPEN_RELEASE_RATIO 同量级）
MAX_TELL_COUNT = 2                # tell_count ≥ 2 后强制转 faded
# bump（用户自己又提到同一关键词）单次加权。设计 §2.2 只写「Σ_bump bump_terms」未给系数
# ⇒ 本单初值，待标定。**AI 自己想到几次一律不计**（设计明写，避免自循环放大）。
SALT_BUMP_WEIGHT = 0.3

# ── 释放窗口（设计 §2.3 spent 行，口径现成于 api/scheduler.py:260）──
REPLY_WINDOW_MINUTES = 60

# 库内时间统一 naive UTC，北京时间 = UTC+8（项目口径）
_BJ_TZ = timezone(timedelta(hours=8))

STATUS_SPARK = "spark"
STATUS_OBSESSION = "obsession"
STATUS_TOLD_FLAT = "told_flat"
STATUS_SPENT = "spent"
STATUS_FADED = "faded"


def now_utc() -> datetime:
    """「现在」（naive UTC，截到秒）——同行显式 .replace(tzinfo=None)（满足时间口径棘轮）＋截秒；域层不 import utils。"""
    return datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)


def _as_beijing_date(dt: datetime | None):
    """naive UTC → 北京时区的日历日（``date``）。带 tzinfo 的输入按其自身时区换算。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BJ_TZ).date()


def age_days(created_at_utc: datetime | None, now_utc: datetime | None = None) -> float:
    """``age_days``（设计 §2.2 novelty 的 age 口径）：**按北京自然日界**算隔了几个日历日，
    不是 24 小时滑动窗。

    算法与 ``scheduling/unfinished_topic.py:35-50``（设计 §2.2 点名的现成实现）同源，
    但本模块要求零业务 import，故就地实现同一口径。负数（未来时间戳）夹到 0。
    """
    a = _as_beijing_date(created_at_utc)
    b = _as_beijing_date(now_utc)
    if a is None or b is None:
        return 0.0
    return float(max(0, (b - a).days))


def novelty(age: float | int, halflife_days: float = NOVELTY_E_FOLDING_DAYS) -> float:
    """``novelty = exp(-age_days / NOVELTY_E_FOLDING_DAYS)``（设计 §2.2，公式照抄不改）。

    值域 (0, 1]：age=0 ⇒ 1.0，随时间**单调衰减**，与咸度正交。

    ⚠️ 数学口径（M1 已按方案 F §5.1 改名订正）：``exp(-t/τ)`` 里的 ``τ`` 是 **e 折叠时间**，
    真实减半点在 ``τ·ln2``——τ=7.0 天时 novelty≈0.368，要到约 4.85 天才掉到 0.5。旧常量名
    ``NOVELTY_HALFLIFE_DAYS`` 与公式不一致，现改名 ``NOVELTY_E_FOLDING_DAYS``；**公式与取值
    一字未动 ⇒ 零行为变更**，M0 的分布读数继续可比。入参名 ``halflife_days`` 保持不变
    （调用方与回放 ``--params`` 的早绑定刷默认值机制按此位置生效，改名只会增加风险）。
    """
    if halflife_days <= 0:
        return 0.0
    return math.exp(-max(0.0, float(age)) / float(halflife_days))


def salt_of(hit_sources: tuple[str, ...] | list[str], bump_hits: int = 0) -> float:
    """``salt = Σ_source w(source) + Σ_bump bump_terms``（设计 §2.2）。

    ``hit_sources`` 是**命中过的来源面列表**，按去重后的集合取权重（同一面重复命中不加盐，
    与「单面重复不升级」同向）；未知来源面权重记 0 并忽略。``bump_hits`` 只计用户自己又提到
    同一关键词这类外部加权。
    """
    distinct = {str(s) for s in hit_sources if s}
    total = sum(SALT_WEIGHT_BY_SOURCE.get(s, 0.0) for s in distinct)
    return round(total + max(0, int(bump_hits)) * SALT_BUMP_WEIGHT, 6)


def should_promote(salt: float, hit_sources: tuple[str, ...] | list[str]) -> bool:
    """``spark → obsession``（设计 §2.2 迁移表第 1 行）：``salt ≥ S_OBS`` **且** 命中过
    ≥2 个不同来源面。两个条件缺一不升级——「多个渠道都指向同一件事」才叫养熟。
    """
    distinct = {str(s) for s in hit_sources if s}
    return float(salt) >= SALT_OBSSESSION_THRESHOLD and len(distinct) >= MIN_DISTINCT_HIT_SOURCES


def should_fade(age: float | int) -> bool:
    """``obsession → faded``（设计 §2.2 迁移表第 2 行）：``age_days > TTL``，只标状态不删行。"""
    return float(age) > TTL_DAYS


def is_expired_told_flat(tell_count: int) -> bool:
    """``told_flat`` 重提上限（设计 §2.3）：``tell_count ≥ MAX_TELL_COUNT`` 后强制转 faded。"""
    return int(tell_count) >= MAX_TELL_COUNT


def apply_told_flat(salt: float, tell_count: int) -> tuple[float, int, str]:
    """半释放结算（设计 §2.3 ``told_flat`` 行）→ ``(new_salt, new_tell_count, status)``。

    ``salt *= 0.35``、``tell_count += 1``；未达上限时状态仍活跃（``told_flat``），
    达到 ``MAX_TELL_COUNT`` 即 ``faded``——沉默不等于可以无限重试。
    """
    n = int(tell_count) + 1
    if is_expired_told_flat(n):
        return round(float(salt) * SALT_TOLD_FLAT_RATIO, 6), n, STATUS_FADED
    return round(float(salt) * SALT_TOLD_FLAT_RATIO, 6), n, STATUS_TOLD_FLAT


def classify_release(sent_ok: bool, replied_within_window: bool) -> str:
    """三档释放（设计 §2.3）：``never_told`` / ``told_flat`` / ``spent``。

    被接住（成功发送 ∧ 60 分钟窗口内有用户回复）→ ``spent``（全额释放）；只发出去没人接 →
    ``told_flat``（半释放）；没发出去 → ``never_told``（不惩罚）。
    **被 §3.1 任一内核闸拦下的一轮不算「说了」**（设计 §3.1 第 11 行），故 ``sent_ok=False``
    一律落 never_told，绝不就地打折。
    """
    if not sent_ok:
        return "never_told"
    return STATUS_SPENT if replied_within_window else STATUS_TOLD_FLAT


def strength(salt: float, nov: float) -> float:
    """挤出排序用的强度＝``salt × novelty``（设计 §2.2 挤出行 / §6 R4）。"""
    return float(salt) * float(nov)


def evict(
    records: list[dict],
    cap_spark: int = CAP_SPARK,
    cap_obsession: int = CAP_OBSESSION,
    cap_told_flat: int = CAP_TOLD_FLAT,
) -> list[str]:
    """容量挤出（设计 §2.2「挤出（容量控制）」行，直接借 ``working_state`` 的挤桶做法）。

    入参 ``records`` 每项需含 ``id`` / ``status`` / ``salt`` / ``novelty``；只对
    ``ACTIVE_STATUSES`` 内的行计数（已 faded/spent 的终态不占名额）。超限时**按
    ``salt × novelty`` 升序**把最弱者挤出去，返回应被标 ``faded`` 的 id 列表
    （只改状态、不物理删，协议 §十七）。

    三档各自独立成顶（``told_flat`` 与 ``obsession`` 同档，见 ``CAP_TOLD_FLAT`` 注释）。
    """
    caps = {
        STATUS_SPARK: cap_spark,
        STATUS_OBSESSION: cap_obsession,
        STATUS_TOLD_FLAT: cap_told_flat,
    }
    doomed: list[str] = []
    for status, cap in caps.items():
        pool = [r for r in records if str(r.get("status") or "") == status]
        if len(pool) <= cap:
            continue
        pool.sort(key=lambda r: strength(r.get("salt", 0.0), r.get("novelty", 0.0)))
        doomed.extend(str(r.get("id")) for r in pool[: len(pool) - max(0, int(cap))])
    return doomed
