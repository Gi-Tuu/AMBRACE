# -*- coding: utf-8 -*-
"""M10（2026-11-04）：直写记忆入口的加固——分级归属注册表 ＋ 插件来源白名单。

背景（C 路只读核查）：四处**绕过 `save_memory`** 的直写没有任何分级归属，写出的默认值是：
- `preoccupations`（内心模拟）→ `epistemic_status="FACT"`
- `summary` ×2（派生摘要/身份画像）→ 不写 epistemic（NULL，读侧当事实）
- `working_state`（会话内结构化状态）→ `"FACT"`
另外插件 `sdk.save_memory` 的 `source` 直接透传，插件可自报 `chat` 等任意来源。

本模块只做两件事：
1. 把「每个直写点应有的分级」集中成注册表（`HISTORICAL` / `GRADED`），供调用点与棘轮测试共用；
2. 插件来源规范化：flag 开时强制 `source="plugin"`，不再接受自报来源。

flag `memory_write_gate_v1` **默认关** ⇒ 返回历史字面量，现网行为逐字节不变。
"""
from __future__ import annotations

FLAG = "memory_write_gate_v1"
PLUGIN_SOURCE = "plugin"

# 直写点 → 历史字面量（flag 关时**必须逐字返回这些值**）
HISTORICAL: dict[str, str | None] = {
    "preoccupation": "FACT",
    "summary": None,
    "working_state": "FACT",
}
# 直写点 → 分级后的目标值（flag 开）：内心模拟 / 派生摘要 / 工作状态一律 INFERRED
GRADED: dict[str, str] = {
    "preoccupation": "INFERRED",
    "summary": "INFERRED",
    "working_state": "INFERRED",
}
# 已知的直写点（供棘轮比对：出现新直写点必须先在这里登记）
REGISTERED_SITES: dict[str, int] = {
    "life/preoccupations.py": 1,
    "memory/summary.py": 2,
    "application/working_state_service.py": 1,
}


def enabled() -> bool:
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(FLAG, False))
    except Exception:  # noqa: BLE001 - 取数失败按历史口径
        return False


def epistemic_for(site: str) -> str | None:
    """该直写点应写入的 epistemic_status；未知 site 抛错（fail-fast，防新点悄悄绕过）。"""
    if site not in HISTORICAL:
        raise KeyError(f"unregistered direct memory write site: {site}")
    return GRADED[site] if enabled() else HISTORICAL[site]


def normalize_plugin_kwargs(kwargs: dict) -> dict:
    """插件写记忆的来源规范化：flag 开 ⇒ 强制 plugin；关 ⇒ 原样透传（历史行为）。"""
    out = dict(kwargs)
    if enabled():
        out["source"] = PLUGIN_SOURCE
    return out
