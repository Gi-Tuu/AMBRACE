# -*- coding: utf-8 -*-
"""念头池 T2 · 源侧配额 / 准入 / 结构化键（A4 批 4 M1，2026-09-30）——纯函数，零 IO。

**为什么必须有这一层**（方案 F §1、§3 的实测结论）：6 套参数变体（扩容 / 快衰减 / 严养熟 /
降权重 / 组合 / 真半衰期）里 **可入池恒为 3409 条**、池量日均恒为 3.788 条，动的只有「谁被
挤走」。原因＝可入池由「抽取器 + 三道过滤」决定，而容量顶、升级阈值、τ、来源权重这四类旋钮
全都不参与那段决定。⇒ 灌爆**不能靠调参解决**，必须在**源头**加一道与权重无关的硬闸。

本模块就是那道硬闸，设计目标（方案 F §3.1 第一行）：把可入池压到 **≤420 条/30 天**
（每（角色×用户）日均 ≤0.5），使 ``CAP_SPARK`` 回落成「兜底护栏」而不是常态机制。

三道新闸（顺序＝短路计数，一条候选只记第一个拦它的原因，与 §2.2 三道过滤同口径）：
1. **准入门槛** ``ADMIT_MIN_NOVELTY``：太陈的信号不值得占额度（离线补抽 30 天历史时尤其重要）；
2. **按面每日硬闸** ``FACE_DAY_CAP``：同一（角色 × 用户 × 来源面 × 北京日）最多 N 条，
   **超额直接丢弃、不延后**——延后等于把今天的洪峰挪到明天，配额就变成了排队而不是闸门；
3. **结构化键去重** ``source_key``：同一来源信号只落一行（取代 §3.2「按朋友圈原文哈希」，
   理由见该函数 docstring）。

``CAP_SPARK`` 12→16 是本方案认可的**次级旋钮**（方案 F §4.2：16 可把理论留存抬到 448，配合
≤420 的入流让挤出归零），但它**不改代码默认值**（本批禁止改既有阈值数值）：回放用
``--params "CAP_SPARK=16"`` 进程内拨，标定结论确认后再由后续批次落地。

边界（与 extract/filters/dynamics 同规格）：**零 IO、零 ORM、零 DB、零 flag、零网络、零域外
业务 import**；入参只接受基础类型，计数状态（当日已用条数、已落结构化键集合）由调用方持有
并注入，本模块不藏任何全局可变状态。
"""
from __future__ import annotations

from datetime import datetime

from app.domain.thought import extract as ex
# 北京日界与 age_days 同源（dynamics._as_beijing_date）：本包内复用，不落第二套时区换算
from app.domain.thought.dynamics import _as_beijing_date

# ── 闸①：入池准入门槛（novelty 下限）。τ=7 时 0.20 ⇒ 约 11.3 天以上的旧信号不入池 ──
ADMIT_MIN_NOVELTY = 0.20

# ── 闸②：按面每日条数硬闸（每（角色 × 用户 × 来源面 × 北京日）最多 N 条）──
# 取值依据：F1 activity 独占可入池 72.9%（2485/3409）且价值密度最低（摘要短语多是活动日志
# 原文降级），F5 fact 占 26.0%（M0 报告第 6 节第 8 条：这一路靠「截长记忆」混过长度闸）⇒
# 两路各给 1 条/天；其余四路本次合计仅 38 条，给 2 条/天不构成压力，留额度给主力面养熟。
FACE_DAY_CAP: dict[str, int] = {
    ex.SRC_ACTIVITY: 1,
    ex.SRC_FACT: 1,
    ex.SRC_REFLECT: 2,
    ex.SRC_MOMENT: 2,
    ex.SRC_USER_HOOK: 2,
    ex.SRC_INTEREST: 2,
}
# 未知/新增来源面的兜底顶（保守取最严，防「新面默认无闸」把配额架空）
FACE_DAY_CAP_DEFAULT = 1

# ── 丢弃原因（留痕按此分组：丢了多少、因为哪条闸）──
DROP_ADMIT = "admit"          # 准入门槛不足
DROP_QUOTA = "quota"          # 按面每日硬闸超额（丢弃，不延后）
DROP_DUP_KEY = "dup_key"      # 结构化键已存在（同源重复抽取）
DROP_REASONS: tuple[str, ...] = (DROP_ADMIT, DROP_QUOTA, DROP_DUP_KEY)


def face_day_cap(source_type: str | None, caps: dict[str, int] | None = None) -> int:
    """该来源面的每日硬闸条数（负数按 0 处理＝该面全拦，便于回放试「砍掉一整路」）。"""
    table = FACE_DAY_CAP if caps is None else caps
    cap = table.get(str(source_type or ""), FACE_DAY_CAP_DEFAULT)
    return max(0, int(cap))


def pair_day_key(
    character_id: int | None,
    user_id: int | None,
    source_type: str | None,
    moment: datetime | None,
) -> tuple:
    """配额计数位＝（角色, 用户, 来源面, 北京日历日）。

    ``user_id`` 为 ``None`` 时归一到 ``0``，与 ``thought_pool.user_id`` 的「角色级」哨兵同值
    （F1 活动 / F6 兴趣的生产表没有用户维度）。日界用北京日历日而非 24 小时滑动窗，
    与 ``dynamics.age_days`` / 回放报告「最忙的三天」同一口径。
    """
    day = _as_beijing_date(moment)
    return (int(character_id or 0), int(user_id or 0), str(source_type or ""), str(day or ""))


def source_key(source_type: str | None, source_ref: str | None) -> str:
    """结构化幂等键＝「来源面 + 来源主键」（设计 §3.2 的 M1 订正位）。

    **替代「按朋友圈原文哈希去重」**：F3 的输入本来就来自 ``ai_moments``，按原文哈希等于让
    每一条 F3 候选都被自己那条朋友圈封锁 ⇒ F3 永久为 0（方案 F §5.2 记为「自我封锁」反例）。
    改用结构化键后：同一条朋友圈只占一个坑（换文案、改模板都不会重复落库），而「同一件事被
    多个来源面指到」仍然各自成行——跨面养熟（§2.2 ``spark→obsession``）靠的就是这个。

    落库后该键的两个部位由唯一约束 ``(character_id, user_id, source_type, source_ref,
    text_hash)`` 的前四位承载；本函数给的是**进程内**（抽取当场 / 回放）判重的紧凑形式。

    ⚠️ 判重力度**比 DB 唯一约束严一档**（不带 text_hash）：同一来源信号只占一个坑，换模板、
    改文案都不会重复落。偏差方向＝**少落不多落**（F5 两路 ``world_facts`` / ``memories`` 的
    自增 id 若撞号会被并成一条，配额期可接受；真要放宽再退回 ``idempotency_key`` 五位口径）。
    """
    return f"{str(source_type or '')}:{str(source_ref or '')}"


def dedup_key(
    character_id: int | None,
    user_id: int | None,
    source_type: str | None,
    source_ref: str | None,
) -> str:
    """判重位＝（角色, 用户, 结构化键）——同一来源信号在不同（角色, 用户）下各自成行。"""
    return f"{int(character_id or 0)}|{int(user_id or 0)}|{source_key(source_type, source_ref)}"


def idempotency_key(
    character_id: int | None,
    user_id: int | None,
    source_type: str | None,
    source_ref: str | None,
    text: str | None,
) -> tuple:
    """与 DB 唯一约束**逐位同形**的幂等键（五元组），供服务层/测试直接拿来比对。"""
    return (
        int(character_id or 0),
        int(user_id or 0),
        str(source_type or ""),
        str(source_ref or ""),
        ex.text_hash(text),
    )


def admit_reject_reason(
    *,
    source_type: str | None,
    novelty_value: float,
    used_today: int,
    caps: dict[str, int] | None = None,
    min_novelty: float | None = None,
) -> str | None:
    """源侧两道新闸的串行判定，返回首个拦截原因（``None`` ＝放行）。

    顺序＝先准入后配额：准入门槛只看候选自身（不占额度），配额闸看「该（角色,用户,面,日）
    今天已用几条」。``used_today`` 由调用方注入**且不得包含本条候选自己**。
    """
    floor = ADMIT_MIN_NOVELTY if min_novelty is None else float(min_novelty)
    if float(novelty_value) < floor:
        return DROP_ADMIT
    if int(used_today) >= face_day_cap(source_type, caps):
        return DROP_QUOTA
    return None
