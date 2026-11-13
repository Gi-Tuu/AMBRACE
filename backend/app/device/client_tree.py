# -*- coding: utf-8 -*-
"""客户端无障碍树适配（P2）：把 Android 端现有 JSON 接进快照会话。

客户端现状（`PhonePerceptionAccessibilityService.collectClickable`）输出的是**扁平可点元素列表**：
```json
[{"text": "...", "clickable": true, "editable": false, "className": "android.widget.button", "x": 540, "y": 1200}]
```
没有层级、resource-id、bounds——本模块把它适配成快照树，并标明：
- role/action 由 className/clickable/editable 推导；
- name 取 text（`[输入框]`/`[图标]` 是占位，打 `placeholder` 标）；
- 坐标只放 `hints`（**永不作为主定位**）；
- path 用扁平下标（真机批改为客户端真实遍历路径）。

因此"双胞胎占位图标"必然拿不到可寻址 ref（正确行为：宁拒绝不猜）。
"""
from __future__ import annotations

from app.device import snapshot as snap

_PLACEHOLDERS = ("[输入框]", "[图标]")

_ROLE_BY_CLASS = (
    ("edittext", "edit"),
    ("autocomplete", "edit"),
    ("button", "button"),
    ("imagebutton", "button"),
    ("checkbox", "checkbox"),
    ("radio", "radio"),
    ("switch", "switch"),
    ("seekbar", "slider"),
    ("spinner", "combobox"),
    ("tab", "tab"),
    ("imageview", "image"),
    ("textview", "text"),
)


def _role_of(item: dict) -> str:
    cls = str(item.get("className") or "").lower()
    for key, role in _ROLE_BY_CLASS:
        if key in cls:
            return role
    if item.get("editable"):
        return "edit"
    return "button" if item.get("clickable") else "text"


def _node_of(item: dict, index: int) -> dict:
    role = _role_of(item)
    text = str(item.get("text") or "").strip()
    actions: list[str] = []
    if item.get("clickable"):
        actions.append("Click")
    if item.get("editable"):
        actions.extend(["SetValue", "Click"])
    node = {
        "role": role,
        "name": text,
        "native_id": None,  # 客户端暂未上报 resource-id（真机批补）
        "available_actions": sorted(set(actions)),
        "path": f"0.{index}",
    }
    if text in _PLACEHOLDERS:
        node["placeholder"] = True
    if item.get("className"):
        node["client_class"] = str(item["className"])
    if item.get("x") is not None or item.get("y") is not None:
        node["hints"] = {"x": item.get("x"), "y": item.get("y")}
    return node


def from_clickable_list(items: list, *, window_title: str = "") -> dict:
    """扁平可点元素列表 → 快照树（window 根 + 平铺子节点）。"""
    if not isinstance(items, list):
        raise snap.SnapshotError("INVALID_ARGS", "客户端树必须是数组")
    children = [_node_of(it, i) for i, it in enumerate(items) if isinstance(it, dict)]
    return {"role": "window", "name": window_title or "当前界面", "children": children}


def normalize_client_tree(raw, *, window_title: str = "") -> dict:
    """接受两种形态：扁平列表（现状）或已是树（未来客户端带层级）。"""
    if isinstance(raw, list):
        return from_clickable_list(raw, window_title=window_title)
    if isinstance(raw, dict):
        return raw
    raise snap.SnapshotError("INVALID_ARGS", "客户端树必须是数组或对象")


def observe(raw, *, window_title: str = "", mode: str = "skeleton",
            max_depth: int = 2, max_children: int = 8, ttl: float = snap.DEFAULT_TTL) -> dict:
    """P2 观察入口：客户端树 → 快照会话（refs）＋按 mode 返回 skeleton/full。"""
    tree = normalize_client_tree(raw, window_title=window_title)
    s = snap.create_snapshot(tree, window_title=window_title, ttl=ttl)
    if mode == "full":
        payload = {"snapshot_id": s.snapshot_id, "window_title": s.window_title,
                   "ref_count": len(s.refs), "tree": s.tree}
    else:
        payload = snap.skeleton(s.snapshot_id, max_depth=max_depth, max_children=max_children)
    refs = [{"ref": r.ref, "role": r.role, "name": r.name, "path": r.path,
             "addressable": r.identity_unique,
             "actions": list(r.actions)} for r in s.refs.values()]
    payload["refs"] = refs
    payload["redacted"] = s.redacted
    return payload
