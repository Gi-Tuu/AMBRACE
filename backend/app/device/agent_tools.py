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
from app.device import adapter as device_adapter
from app.device import client_tree, snapshot as snap
from app.utils.logger import get_logger

_logger = get_logger("device.agent_tools")

OBSERVE_TOOL = "device_observe"
ACT_TOOL = "device_act"

# tool 内部快照 → 目标应用（api/agent_tools 的映射只在 HTTP 端，角色工具独立维护）
_TOOL_SNAPSHOT_TARGETS: dict[str, str] = {}
_TOOL_SNAPSHOT_PATHS: dict[tuple[str, str], str] = {}
_LAST_TARGET_APP: str = ""


async def _execute_observe(payload: dict) -> dict:
    """角色工具入口：真实拉取无障碍树并返回 skeleton 快照（只读）。"""
    from app.agent import loop as _loop
    from app.device.actions import KILL_SWITCH_KEY, _account_actions_enabled
    try:
        uid = int(payload.get("user_id") or 0)
    except Exception:
        uid = 0
    target = str(payload.get("target_app") or "").strip()
    if not target:
        return {"ok": False, "text": "需要目标应用包名（target_app）"}
    if not bool((_loop.AGENT_FLAGS or {}).get(KILL_SWITCH_KEY, False)):
        return {"ok": False, "text": "手机操作总开关未开"}
    try:
        if not await _account_actions_enabled(uid):
            return {"ok": False, "text": "当前账号未开启手机操作"}
    except Exception as e:
        return {"ok": False, "text": f"权限检查失败：{e}"}
    try:
        tree = await device_adapter.observe_tree(user_id=uid, target_app=target, timeout=15.0)
        out = client_tree.observe(tree, mode="skeleton")
    except Exception as e:
        _logger.warning("device_observe tool failed: %s", e)
        return {"ok": False, "text": f"读取手机界面失败：{e}"}
    refs = out.get("refs") or []
    sid = str(out.get("snapshot_id") or "")
    _TOOL_SNAPSHOT_TARGETS[sid] = target
    global _LAST_TARGET_APP
    _LAST_TARGET_APP = target
    by_name: dict[str, list[str]] = {}
    def _walk(nodes):
        for n in nodes or []:
            if isinstance(n, dict):
                name = str(n.get("name") or n.get("text") or n.get("desc") or "")
                cp = str(n.get("path") or "")
                if name and cp:
                    by_name.setdefault(name, []).append(cp)
                _walk(n.get("children") or [])
    _walk(tree.get("children") or [tree])
    for r in refs:
        cands = by_name.get(str(r.get("name") or "")) or []
        if cands:
            _TOOL_SNAPSHOT_PATHS[(sid, str(r.get("ref") or ""))] = cands.pop(0)
    title = str(out.get("window_title") or "")
    names = [str(r.get("name") or "").strip() for r in refs[:8]]
    names = [n for n in names if n]
    detail = ("；元素：" + "、".join(names)) if names else ""
    ref_map = "、".join(
        f"{r.get('ref')}={str(r.get('name') or '').strip()}" for r in refs[:12] if r.get("ref")
    )
    if ref_map:
        detail += "；refs：" + ref_map
    return {
        "ok": True,
        "text": f"已读取{('「' + title + '」') if title else '手机界面'}，{len(refs)} 个可寻址元素{detail}；snapshot_id={out.get('snapshot_id', '')}",
        "snapshot_id": out.get("snapshot_id", ""),
        "refs": refs[:30],
        "_target_app": target,
    }


def _spec_observe(enabled: bool = True) -> ToolSpec:
    return ToolSpec(
        name=OBSERVE_TOOL,
        description="读取当前手机界面无障碍树，返回可寻址元素（ref）。只读，不执行任何操作。",
        action_type="DEVICE_OBSERVE",
        risk_level="LOW",
        idempotent=True,
        enabled=enabled,
        execute=_execute_observe,
        max_observation_chars=4000,
    )


async def _execute_act(payload: dict) -> dict:
    """角色动作入口：仅支持 click；必须已有 once_ever 授权。"""
    from app.agent import loop as _loop
    from app.device.actions import KILL_SWITCH_KEY, _account_actions_enabled, configured_policies
    try:
        uid = int(payload.get("user_id") or 0)
    except Exception:
        uid = 0
    snapshot_id = str(payload.get("snapshot_id") or "").strip()
    ref = str(payload.get("ref") or "").strip()
    verb = str(payload.get("verb") or "click").strip().lower()
    _logger.info("device_act enter verb=%s payload=%s", verb, payload)
    if verb == "swipe":
        if not bool((_loop.AGENT_FLAGS or {}).get(KILL_SWITCH_KEY, False)):
            return {"ok": False, "text": "手机操作总开关未开"}
        try:
            if not await _account_actions_enabled(uid):
                return {"ok": False, "text": "当前账号未开启手机操作"}
        except Exception as e:
            return {"ok": False, "text": f"权限检查失败：{e}"}
        try:
            _pol = await configured_policies(uid)
        except Exception as e:
            return {"ok": False, "text": f"读取授权失败：{e}"}
        if _pol.get("action_swipe") != "once_ever":
            return {"ok": False, "text": "需要先在 App 手机操作里完成一次确认（action_swipe）"}
        target = str(payload.get("target_app") or _LAST_TARGET_APP or "").strip()
        if not target:
            return {"ok": False, "text": "swipe 需要 target_app"}
        direction = str(payload.get("direction") or "").strip().lower()
        if direction not in ("", "up", "down", "left", "right"):
            return {"ok": False, "text": "direction 只能是 up/down/left/right"}
        try:
            res = await device_adapter.swipe(
                user_id=uid, target_app=target, direction=direction,
                x1=payload.get("x1"), y1=payload.get("y1"),
                x2=payload.get("x2"), y2=payload.get("y2"),
                ms=int(payload.get("ms") or 300), timeout=20.0,
            )
        except Exception as e:
            _logger.warning("device swipe failed payload=%s err=%s", payload, e)
            return {"ok": False, "text": f"滑动下发失败：{e}"}
        if res.get("ok"):
            return {"ok": True, "text": f"已滑动（{direction or '坐标'}）"}
        return {"ok": False, "text": f"滑动失败：{res.get('detail') or 'unknown'}"}
    if not snapshot_id or not ref:
        return {"ok": False, "text": "需要 snapshot_id 与 ref（先用 device_observe）"}
    if not bool((_loop.AGENT_FLAGS or {}).get(KILL_SWITCH_KEY, False)):
        return {"ok": False, "text": "手机操作总开关未开"}
    try:
        if not await _account_actions_enabled(uid):
            return {"ok": False, "text": "当前账号未开启手机操作"}
    except Exception as e:
        return {"ok": False, "text": f"权限检查失败：{e}"}
    if verb not in ("click", "set_text"):
        return {"ok": False, "text": f"暂只支持 click/set_text/swipe（收到 {verb}）"}
    value = str(payload.get("value") or payload.get("text") or "")
    if verb == "set_text" and not value.strip():
        return {"ok": False, "text": "set_text 需要非空 value"}
    try:
        plan = snap.plan_action(snapshot_id, ref, verb, value=value or None)
    except snap.SnapshotError as e:
        return {"ok": False, "text": f"动作计划失败：{e.code} {e}"}
    plan = dict(plan)
    _full = _TOOL_SNAPSHOT_PATHS.get((snapshot_id, ref))
    if _full:
        plan["path"] = _full
    cap = {"click": "action_tap", "set_text": "action_set_text"}[verb]
    try:
        policies = await configured_policies(uid)
    except Exception as e:
        return {"ok": False, "text": f"读取授权失败：{e}"}
    if policies.get(cap) != "once_ever":
        return {"ok": False, "text": f"需要先在 App 手机操作里完成一次确认（{cap}）"}
    target = str(payload.get("target_app") or _TOOL_SNAPSHOT_TARGETS.get(snapshot_id) or "")
    if not target:
        return {"ok": False, "text": "快照未关联目标应用，请重新 observe"}
    try:
        if verb == "click":
            res = await device_adapter.execute_action(user_id=uid, target_app=target, plan=plan, timeout=20.0)
        else:
            res = await device_adapter.set_text(user_id=uid, target_app=target, path=plan.get("path") or "", text=value, timeout=20.0)
    except Exception as e:
        _logger.warning("device_act tool failed: %s", e)
        return {"ok": False, "text": f"动作下发失败：{e}"}
    if res.get("ok"):
        label = {"click": "已点击", "set_text": "已输入"}[verb]
        return {"ok": True, "text": f"{label} {plan.get('expected_identity', {}).get('name') or ref}"}
    return {"ok": False, "text": f"动作失败：{res.get('detail') or 'unknown'}"}


def _spec_act(enabled: bool = True) -> ToolSpec:
    return ToolSpec(
        name=ACT_TOOL,
        description="对 device_observe 返回的 ref 执行点按/输入/滚动；也支持按方向滑动屏幕（swipe，无需 ref）。",
        action_type="DEVICE_ACT",
        risk_level="HIGH",
        idempotent=False,
        enabled=enabled,
        execute=_execute_act,
    )


def register_device_tools(enabled: bool = False, act_enabled: bool = False) -> list[ToolSpec]:
    """显式登记两个设备工具；observe/act 分别开关。"""
    return [register_tool(_spec_observe(enabled=enabled)), register_tool(_spec_act(enabled=act_enabled))]


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
