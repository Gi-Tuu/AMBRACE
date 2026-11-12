# -*- coding: utf-8 -*-
"""M8（2026-11-04）：记忆分级落地——读侧标注、核心区标注、晋升认知闸。

问题（C 路只读核查）：
- 生产 40.8% 的记忆 `epistemic_status` 为 NULL，而读侧把「不标」当事实（`format.py` 对 None/FACT 都不加前缀）；
- 核心记忆注入区（`section_memories._inject_core_anchors_loops`）只拼日期，**没有认知前缀与说话人**；
- 晋升核心只看重要度/确认次数（`core.py`），INFERRED 一旦晋升即与事实不可区分；
- `epistemic_status` 取值空间缺「未知/失败」终态（L4）。

本模块把三件事集中成一处，并用 flag `memory_grading_v1`（**默认关**）控制：
- 读侧：flag 开时 NULL → 标注 `[UNVERIFIED]`；FACT 仍不标；新增 `UNKNOWN` 可用；
- 核心区：同一前缀接入核心/关系锚点行；
- 晋升：flag 开时 推断/未核/未知/虚构/计划 一律不晋升核心（用户确认改 FACT 后照旧可晋升）。
flag 关 ⇒ 三者**逐字返回旧行为**，现网零变化。
"""
from __future__ import annotations

FLAG = "memory_grading_v1"

# 合法取值（新增 UNKNOWN＝L4「结果未知/失败」，String(12) 容得下，无需迁移）
GRADES = frozenset({"FACT", "INFERRED", "PLANNED", "FICTIONAL", "UNVERIFIED", "UNKNOWN"})
# 不得晋升核心的等级（计划不是已发生事实；推断/未核/未知/虚构同理）
NOT_CORE_GRADES = frozenset({"INFERRED", "PLANNED", "FICTIONAL", "UNVERIFIED", "UNKNOWN"})


def enabled() -> bool:
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(FLAG, False))
    except Exception:  # noqa: BLE001 - 取数失败按旧口径
        return False


def missing_grade_label() -> str:
    """flag 开时，未标注（NULL/空）记忆使用的标注。"""
    return "[UNVERIFIED] "


def prefix_for(status: str | None) -> str:
    """注入前缀（flag 关时返回空串，由调用方回落到历史实现）。"""
    if not status:
        return missing_grade_label()
    if status == "FACT":
        return ""
    return f"[{status}] "


def core_allowed(epistemic_status: str | None) -> bool:
    """是否允许晋升核心（flag 关恒 True＝历史行为）。未知取值保守拒绝。"""
    if not enabled():
        return True
    if not epistemic_status:
        return False
    if epistemic_status not in GRADES:
        return False
    return epistemic_status not in NOT_CORE_GRADES
