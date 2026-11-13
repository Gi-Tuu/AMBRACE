# -*- coding: utf-8 -*-
"""P3 角色工具循环·服务端规格（设计先行，默认不注册进活动工具表）。

两个工具：
- ``device_observe``：把客户端无障碍树变成快照（skeleton/full），只读；
- ``device_act``：对 ref 生成动作计划（plan_action），执行仍由既有设备动作链负责。

注册策略：``register_device_tools()`` 显式调用才登记，且 ``enabled=False`` 起步
（避免未经真机回归就进入角色可用工具面）；开关与权限另按 P3 设计接。
"""
from __future__ import annotations

from app.agent.tools import ToolSpec, get_tool, register_tool
from app.device import client_tree, snapshot as snap

OBSERVE_TOOL = "device_observe"
ACT_TOOL = "device_act"


def _spec_observe() -> ToolSpec:
    return ToolSpec(
        name=OBSERVE_TOOL,
        description="读取当前手机界面无障碍树，返回可寻址元素（ref）。只读，不执行任何操作。",
        risk_level="LOW",
        idempotent=True,
        enabled=False,
        max_observation_chars=4000,
    )


def _spec_act() -> ToolSpec:
    return ToolSpec(
        name=ACT_TOOL,
        description="对 device_observe 返回的 ref 生成点按/输入计划（含唯一性复核）。",
        risk_level="HIGH",
        idempotent=False,
        enabled=False,
    )


def register_device_tools() -> list[ToolSpec]:
    """显式登记两个设备工具（默认 enabled=False；真机回归后由批内开关启用）。"""
    return [register_tool(_spec_observe()), register_tool(_spec_act())]


def handle_observe(payload: dict) -> dict:
    """handler：入参 {tree|items, window_title, mode, max_depth, max_children}。"""
    raw = payload.get("tree", payload.get("items"))
    if raw is None:
        raise snap.SnapshotError("INVALID_ARGS", "缺少 tree/items")
    return client_tree.observe(
        raw,
        window_title=str(payload.get("window_title") or ""),
        mode=str(payload.get("mode") or "skeleton"),
        max_depth=int(payload.get("max_depth") or 2),
        max_children=int(payload.get("max_children") or 8),
    )


def handle_act(payload: dict) -> dict:
    """handler：入参 {snapshot_id, ref, verb, value?, current_tree?}。只出计划不执行。"""
    snap_id = str(payload.get("snapshot_id") or "")
    ref = str(payload.get("ref") or "")
    verb = str(payload.get("verb") or "")
    if not snap_id or not ref or not verb:
        raise snap.SnapshotError("INVALID_ARGS", "需要 snapshot_id/ref/verb")
    return snap.plan_action(snap_id, ref, verb, value=payload.get("value"),
                            current_tree=payload.get("current_tree"))


def tool_status() -> dict:
    """观测用：两个工具的登记与启用状态。"""
    out = {}
    for name in (OBSERVE_TOOL, ACT_TOOL):
        spec = get_tool(name)
        out[name] = None if spec is None else {"enabled": spec.enabled, "risk": spec.risk_level}
    return out
