# -*- coding: utf-8 -*-
"""M4 设备适配器（统一执行脊线）。

链路：调用方（角色 ToolSpec / YAI Core API / host run）
  → ``_submit()`` 构造 ``ActionIntent``
  → ``device.actions.decide_action``（总开关／账号／目标白名单／熔断／限流／审计）
  → 同一 ``_PENDING`` 队列 → 手机 App 轮询取件执行
  → ``report_result`` 回执唤醒等待方。

本模块**不再自行写 _PENDING**；所有入口共用中央裁决，避免两套安全语义。
"""
from __future__ import annotations

import asyncio
import json

from app.device import actions as dev_actions
from app.device import snapshot as snap

_WAITERS: dict[str, asyncio.Event] = {}
_RESULTS: dict[str, dict] = {}


def _notify(token: str, ok: bool, detail: str) -> None:
    _RESULTS[token] = {"ok": bool(ok), "detail": detail or ""}
    ev = _WAITERS.get(token)
    if ev is not None:
        ev.set()


async def _resolve_tenant_id(user_id: int) -> int | None:
    """当前账号的家庭根（= 租户）；失败 fail-closed 到 None。"""
    from app.application.family_service import get_family_root_id
    from app.db import database

    try:
        async with database.async_session_factory() as db:
            return await get_family_root_id(db, user_id)
    except Exception:
        return None


async def _submit(intent: dev_actions.ActionIntent, *, user_id: int,
                  timeout: float = 15.0) -> dict:
    """过中央裁决并入队；批准后等待 App 回执。"""
    tenant_id = await _resolve_tenant_id(user_id)
    decision = await dev_actions.decide_action(
        user_id=user_id, tenant_id=tenant_id,
        plugin_name=dev_actions.BUILTIN_CALLER, intent=intent,
    )
    if not decision.allowed:
        return {"ok": False, "detail": decision.reason or "denied"}
    token = decision.action_token or ""
    if not token:
        return {"ok": False, "detail": "no_action_token"}
    return await _wait_token(token, timeout)


async def _wait_token(token: str, timeout: float) -> dict:
    """等待指定 token 的回执；回执可能早于 waiter 注册，先查结果表。"""
    if token in _RESULTS:
        return _RESULTS.pop(token)
    ev = _WAITERS.get(token)
    if ev is None:
        ev = asyncio.Event()
        _WAITERS[token] = ev
    try:
        async with asyncio.timeout(timeout):
            await ev.wait()
        return _RESULTS.pop(token, {"ok": False, "detail": "no_result"})
    except TimeoutError:
        return {"ok": False, "detail": "device_timeout"}
    finally:
        _WAITERS.pop(token, None)
        _RESULTS.pop(token, None)


def _tree_from_detail(detail: str) -> dict | None:
    try:
        tree = json.loads(detail)
    except Exception:
        return None
    if isinstance(tree, list):
        return {"role": "window", "name": "", "children": tree}
    return tree if isinstance(tree, dict) else None


async def observe_tree(*, user_id: int, target_app: str, timeout: float = 35.0) -> dict:
    """只读 observe：仍不套写动作白名单，但必须过总开关/账号闸。"""
    from app.device.actions import _account_actions_enabled, _global_actions_enabled
    if not await _global_actions_enabled():
        raise snap.SnapshotError("DEVICE_DISABLED", "手机操作总开关未开")
    try:
        if not await _account_actions_enabled(user_id):
            raise snap.SnapshotError("DEVICE_DISABLED", "当前账号未开启手机操作")
    except snap.SnapshotError:
        raise
    except Exception as e:
        raise snap.SnapshotError("DEVICE_DISABLED", f"权限检查失败：{e}")
    res = await _legacy_observe_wait(user_id=user_id, target_app=target_app, timeout=timeout)
    if not res.get("ok"):
        raise snap.SnapshotError("DEVICE_CAPTURE_UNAVAILABLE",
                                 f"设备树采集失败：{res.get('detail') or 'unknown'}")
    tree = _tree_from_detail(res.get("detail") or "")
    if tree is None:
        raise snap.SnapshotError("DEVICE_CAPTURE_UNAVAILABLE", f"设备树回执不可解析: {str(res.get('detail'))[:160]!r}")
    return tree


async def _legacy_observe_wait(*, user_id: int, target_app: str, timeout: float) -> dict:
    """observe 的只读等待（不经过行动裁决；仅采集，不执行界面动作）。"""
    intent = dev_actions.ActionIntent(capability="action_observe_tree", target_app=target_app or "")
    tenant_id = await _resolve_tenant_id(user_id)
    # 只读采集仍复用中央队列协议，但不走写动作的白名单/限流拒绝面：
    # 采集本身不改变界面；App 端仍按 user_id 隔离。
    token = dev_actions.enqueue_observe(user_id=user_id, tenant_id=tenant_id, intent=intent)
    return await _wait_token(token, timeout)


async def execute_action(*, user_id: int, target_app: str, plan: dict,
                         timeout: float = 25.0) -> dict:
    intent = dev_actions.ActionIntent(
        capability="action_tap", target_app=target_app, by="path",
        path=str(plan.get("path") or ""),
        query=str((plan.get("expected_identity") or {}).get("name") or plan.get("path") or ""),
    )
    return await _submit(intent, user_id=user_id, timeout=timeout)


async def set_text(*, user_id: int, target_app: str, path: str, text: str,
                   timeout: float = 20.0) -> dict:
    intent = dev_actions.ActionIntent(
        capability="action_set_text", target_app=target_app, path=path, text=text,
    )
    return await _submit(intent, user_id=user_id, timeout=timeout)


async def scroll(*, user_id: int, target_app: str, path: str,
                 timeout: float = 20.0) -> dict:
    intent = dev_actions.ActionIntent(
        capability="action_scroll", target_app=target_app, by="path", path=path,
    )
    return await _submit(intent, user_id=user_id, timeout=timeout)


async def swipe(*, user_id: int, target_app: str, direction: str = "",
                x1: int | None = None, y1: int | None = None,
                x2: int | None = None, y2: int | None = None,
                ms: int = 300, timeout: float = 20.0) -> dict:
    intent = dev_actions.ActionIntent(
        capability="action_swipe", target_app=target_app, direction=direction or "up",
        x1=x1, y1=y1, x2=x2, y2=y2, ms=ms,
    )
    return await _submit(intent, user_id=user_id, timeout=timeout)


async def open_app(*, user_id: int, package: str, timeout: float = 15.0) -> dict:
    intent = dev_actions.ActionIntent(capability="action_open_app", target_app=package)
    return await _submit(intent, user_id=user_id, timeout=timeout)


async def run_capability(*, user_id: int, capability: str, target_app: str,
                         timeout: float = 20.0, **params) -> dict:
    """host-run 等通用入口：同样过中央裁决。"""
    _path = params.get("path")
    _by = params.get("by") or ("path" if _path else None)
    _query = params.get("query") or (_path if capability == "action_tap" else None)
    intent = dev_actions.ActionIntent(
        capability=capability, target_app=target_app,
        by=_by, query=_query,
        path=_path, text=params.get("text"),
        direction=params.get("direction"),
        x1=params.get("x1"), y1=params.get("y1"),
        x2=params.get("x2"), y2=params.get("y2"), ms=params.get("ms"),
    )
    return await _submit(intent, user_id=user_id, timeout=timeout)
