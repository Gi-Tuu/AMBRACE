# -*- coding: utf-8 -*-
"""M4 设备适配器：把 agent 工具调用接到 AMBRACE 既有设备执行器。

链路：工具端点 → `enqueue_and_wait()` 入队（内存 `_PENDING`）→ 手机 App 轮询取件执行 →
POST /api/v1/device/actions/{token}/result 回执 → 本适配器唤醒等待方。
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time

from app.device import actions as dev_actions
from app.device import snapshot as snap

_WAITERS: dict[str, asyncio.Event] = {}
_RESULTS: dict[str, dict] = {}


def _notify(token: str, ok: bool, detail: str) -> None:
    _RESULTS[token] = {"ok": bool(ok), "detail": detail or ""}
    ev = _WAITERS.get(token)
    if ev is not None:
        ev.set()


async def enqueue_and_wait(*, user_id: int, capability: str, target_app: str,
                            timeout: float = 15.0, **params) -> dict:
    """入队一条设备动作并等待 App 回执；超时/未回执返回 {"ok": False, "detail": ...}。"""
    intent = dev_actions.ActionIntent(capability=capability, target_app=target_app,
                                      by=params.get("by"), query=params.get("query"),
                                      path=params.get("path"), text=params.get("text"),
                                      direction=params.get("direction"),
                                      x1=params.get("x1"), y1=params.get("y1"),
                                      x2=params.get("x2"), y2=params.get("y2"),
                                      ms=params.get("ms"), dry_run=False)
    token = secrets.token_hex(8)
    ev = asyncio.Event()
    _WAITERS[token] = ev
    dev_actions._PENDING[token] = dev_actions._PendingAction(
        token=token, user_id=int(user_id), tenant_id=None, plugin="builtin",
        intent=intent, enqueued_at=time.monotonic())
    try:
        async with asyncio.timeout(timeout):
            await ev.wait()
        return _RESULTS.get(token) or {"ok": False, "detail": "no_result"}
    except TimeoutError:
        return {"ok": False, "detail": "device_timeout"}
    finally:
        _WAITERS.pop(token, None)
        _RESULTS.pop(token, None)
        dev_actions._PENDING.pop(token, None)


def _tree_from_detail(detail: str) -> dict | None:
    try:
        tree = json.loads(detail)
    except Exception:
        return None
    if isinstance(tree, list):
        return {"role": "window", "name": "", "children": tree}
    return tree if isinstance(tree, dict) else None


async def observe_tree(*, user_id: int, target_app: str, timeout: float = 35.0) -> dict:
    """让 App 采集当前界面树 → 建快照；失败抛 SnapshotError。"""
    res = await enqueue_and_wait(user_id=user_id, capability="action_observe_tree",
                                 target_app=target_app or "", timeout=timeout)
    if not res.get("ok"):
        raise snap.SnapshotError("DEVICE_CAPTURE_UNAVAILABLE",
                                 f"设备树采集失败：{res.get('detail') or 'unknown'}")
    tree = _tree_from_detail(res.get("detail") or "")
    if tree is None:
        raise snap.SnapshotError("DEVICE_CAPTURE_UNAVAILABLE", f"设备树回执不可解析: {str(res.get('detail'))[:160]!r}")
    return tree


async def execute_action(*, user_id: int, target_app: str, plan: dict,
                         timeout: float = 25.0) -> dict:
    """执行一个设备动作计划（by=path），返回 {ok, detail}。"""
    return await enqueue_and_wait(user_id=user_id, capability="action_tap",
                                  target_app=target_app, timeout=timeout,
                                  by="path", path=plan.get("path") or "",
                                  query=(plan.get("expected_identity") or {}).get("name") or "")


async def open_app(*, user_id: int, package: str, timeout: float = 15.0) -> dict:
    return await enqueue_and_wait(user_id=user_id, capability="action_open_app",
                                  target_app=package, timeout=timeout)


async def set_text(*, user_id: int, target_app: str, path: str, text: str,
                   timeout: float = 20.0) -> dict:
    return await enqueue_and_wait(user_id=user_id, capability="action_set_text",
                                  target_app=target_app, timeout=timeout,
                                  by="path", path=path, text=text)



async def swipe(*, user_id: int, target_app: str, direction: str = "",
                x1: int | None = None, y1: int | None = None,
                x2: int | None = None, y2: int | None = None,
                ms: int = 300, timeout: float = 20.0) -> dict:
    return await enqueue_and_wait(user_id=user_id, capability="action_swipe",
                                  target_app=target_app, timeout=timeout,
                                  direction=direction or None,
                                  x1=x1, y1=y1, x2=x2, y2=y2, ms=ms)
