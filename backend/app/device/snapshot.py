# -*- coding: utf-8 -*-
"""设备界面快照会话（P0，只读）——借鉴 agent-desktop 的 snapshot/ref 语义。

目标（不改现有动作链）：把客户端已有的无障碍树 JSON（`captureNodeTreeJson`）包成
「快照会话」：一次快照分配 `snapshot_id` 与元素 `ref`，动作前可复核唯一性（stale/ambiguous），
并可分层观察（skeleton 浅层、drill 下钻单区）。**只读**：本模块不执行任何动作。

错误码（与 agent-desktop 对齐）：
- ``SNAPSHOT_NOT_FOUND``：快照不存在/已过期；
- ``INVALID_REF``：ref 不属于该快照；
- ``STALE_REF``：元素在最新树里已消失（``saved_ref_absent``）；
- ``AMBIGUOUS_TARGET``：同窗出现相同元素（``saved_ref_ambiguous``）或捕获时就不唯一（``identity_not_unique``）。

纪律：ref 只在一次快照内有效；TTL 默认 120s；进程内存储、有上限（默认保留 32 份）。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

# 常见可交互角色（客户端/平台差异下尽量宽松；最终以 available_actions/clickable 为准）
INTERACTIVE_ROLES = frozenset({
    "button", "edit", "textfield", "combobox", "checkbox", "radio",
    "menuitem", "tab", "tabitem", "listitem", "switch", "slider", "spinner",
    "link", "datepicker", "searchbox",
})

# 值需要脱敏的角色/类型（密码、密钥输入框）
SENSITIVE_ROLES = frozenset({"password", "passwordtext", "securetextfield"})

DEFAULT_TTL = 120.0
MAX_SNAPSHOTS = 32
MAX_REFS = 500


class SnapshotError(ValueError):
    """带 code/details 的失败（供上层转成 JSON 错误）。"""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class RefRecord:
    ref: str
    role: str
    name: str
    native_id: str | None
    actions: tuple[str, ...]
    depth: int
    identity_unique: bool
    path: str = ""  # 稳定遍历路径（如 "0/2/1"）：客户端可按路径直解，不再模糊按文本查找

    @property
    def identity(self) -> tuple:
        return (self.role, self.native_id or "", self.name)


@dataclass
class Snapshot:
    snapshot_id: str
    window_title: str
    tree: dict
    refs: dict[str, RefRecord]
    created_at: float
    ttl: float
    redacted: int = 0
    next_seq: int = 0


_STORE: dict[str, Snapshot] = {}
_LOCK = threading.Lock()


def _nid(node: dict) -> str | None:
    v = node.get("native_id")
    if isinstance(v, dict):
        v = v.get("value")
    if v is None:
        v = node.get("resource-id") or node.get("resource_id") or node.get("automation_id")
    return None if v in (None, "") else str(v)


def _actions(node: dict) -> tuple[str, ...]:
    acts = node.get("available_actions") or node.get("actions") or ()
    if isinstance(acts, str):
        acts = (acts,)
    out = [str(a) for a in acts if a]
    if node.get("clickable") and "Click" not in out:
        out.append("Click")
    return tuple(out)


def _role(node: dict) -> str:
    return str(node.get("role") or node.get("role_description") or node.get("class") or "")


def _name(node: dict) -> str:
    for k in ("name", "text", "desc", "content_desc", "label"):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _children(node: dict) -> list:
    ch = node.get("children") or []
    return [c for c in ch if isinstance(c, dict)]


def _is_actionable(node: dict, role: str, actions: tuple[str, ...]) -> bool:
    return bool(actions) or bool(node.get("clickable")) or role.lower() in INTERACTIVE_ROLES


def _redact(node: dict, role: str) -> bool:
    """敏感输入框的 value 置空（仍保留元素与 ref）。返回是否发生脱敏。"""
    if role.lower() in SENSITIVE_ROLES and node.get("value"):
        node["value"] = ""
        return True
    return False


def _normalize(node: dict, depth: int, counts: dict, *, redact: bool, path: str = "0") -> dict:
    role = _role(node)
    out = {
        "role": role,
        "name": _name(node),
        "native_id": _nid(node),
        "actions": list(_actions(node)),
        "depth": depth,
        "path": path,
    }
    if redact and _redact(node, role):
        counts["redacted"] += 1
    if "value" in node and not (redact and role.lower() in SENSITIVE_ROLES):
        out["value"] = node.get("value")
    states = node.get("states")
    if states:
        out["states"] = list(states) if isinstance(states, (list, tuple)) else [states]
    if node.get("clickable"):
        out["clickable"] = True
    kids = _children(node)
    if kids:
        out["children"] = [_normalize(k, depth + 1, counts, redact=redact,
                                      path=f"{path}.{i}") for i, k in enumerate(kids)]
    return out


def _walk(norm: dict):
    yield norm
    for c in norm.get("children") or ():
        yield from _walk(c)


def create_snapshot(tree: dict, *, window_title: str = "", ttl: float = DEFAULT_TTL,
                    max_refs: int = MAX_REFS, redact: bool = True) -> Snapshot:
    """把客户端无障碍树 JSON 变成快照会话（纯内存、只读）。"""
    if not isinstance(tree, dict):
        raise SnapshotError("INVALID_ARGS", "tree 必须是对象")
    counts = {"redacted": 0}
    norm = _normalize(tree, 0, counts, redact=redact)
    nodes = list(_walk(norm))

    # 身份唯一性：同一窗口内 identity 只出现一次的才算可寻址
    ident_count: dict[tuple, int] = {}
    for n in nodes:
        ident = (_role(n), _nid(n) or "", _name(n))
        ident_count[ident] = ident_count.get(ident, 0) + 1

    sid = uuid.uuid4().hex[:12]
    refs: dict[str, RefRecord] = {}
    seq = 0
    for n in nodes:
        role, name, nid = _role(n), _name(n), _nid(n)
        acts = _actions(n)
        if not _is_actionable(n, role, acts) or seq >= max_refs:
            continue
        seq += 1
        ident = (role, nid or "", name)
        rec = RefRecord(
            ref=f"@{sid}:e{seq}", role=role, name=name, native_id=nid,
            actions=acts, depth=int(n.get("depth") or 0),
            identity_unique=ident_count.get(ident, 0) == 1,
            path=str(n.get("path") or ""),
        )
        n["ref_id"] = rec.ref
        refs[rec.ref] = rec

    snap = Snapshot(snapshot_id=sid, window_title=window_title, tree=norm,
                    refs=refs, created_at=time.monotonic(), ttl=float(ttl),
                    redacted=counts["redacted"], next_seq=seq)
    with _LOCK:
        _purge_locked()
        _STORE[sid] = snap
        while len(_STORE) > MAX_SNAPSHOTS:
            _STORE.pop(next(iter(_STORE)))
    return snap


def _purge_locked(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for sid in [k for k, v in _STORE.items() if now - v.created_at > v.ttl]:
        _STORE.pop(sid, None)


def get_snapshot(snapshot_id: str) -> Snapshot:
    with _LOCK:
        _purge_locked()
        snap = _STORE.get(snapshot_id)
    if snap is None:
        raise SnapshotError("SNAPSHOT_NOT_FOUND", "快照不存在或已过期；请重新快照")
    return snap


def clear_store() -> None:
    """测试/会话清理用。"""
    with _LOCK:
        _STORE.clear()


def _find_matches(tree: dict, ident: tuple) -> list[dict]:
    hits = []
    for n in _walk(tree):
        if (_role(n), _nid(n) or "", _name(n)) == ident:
            hits.append(n)
    return hits


def resolve(snapshot_id: str, ref: str, *, current_tree: dict | None = None) -> dict:
    """把 ref 解析成元素（只读）。给了 current_tree 就做 stale/ambiguous 复核。"""
    snap = get_snapshot(snapshot_id)
    rec = snap.refs.get(ref)
    if rec is None:
        raise SnapshotError("INVALID_REF", "ref 不属于该快照", details={"ref": ref})
    if not rec.identity_unique:
        raise SnapshotError(
            "AMBIGUOUS_TARGET", "该元素在捕获时就不唯一，不能按 ref 寻址",
            details={"kind": "identity_not_unique", "ref": ref})
    out = {"ref": rec.ref, "role": rec.role, "name": rec.name,
           "native_id": rec.native_id, "actions": list(rec.actions),
           "path": rec.path}
    if current_tree is None:
        return out
    norm = _normalize(current_tree, 0, {"redacted": 0}, redact=True)
    hits = _find_matches(norm, rec.identity)
    if not hits:
        raise SnapshotError("STALE_REF", "元素已消失或不匹配；请重新快照",
                            details={"kind": "saved_ref_absent", "ref": ref})
    if len(hits) > 1:
        raise SnapshotError("AMBIGUOUS_TARGET", "当前窗口出现相同元素，无法唯一寻址",
                            details={"kind": "saved_ref_ambiguous", "ref": ref,
                                     "matches": len(hits)})
    return out


def skeleton(snapshot_id: str, *, max_depth: int = 2, max_children: int = 8) -> dict:
    """浅层视图：超过深度/数量的分支折叠成 children_count（省 token）。"""

    def cut(n: dict, depth: int) -> dict:
        out = {k: v for k, v in n.items() if k != "children"}
        kids = n.get("children") or []
        if kids and depth < max_depth:
            out["children"] = [cut(c, depth + 1) for c in kids[:max_children]]
            if len(kids) > max_children:
                out["children_count"] = len(kids)
        elif kids:
            out["children_count"] = len(kids)
        return out

    snap = get_snapshot(snapshot_id)
    return {"snapshot_id": snap.snapshot_id, "window_title": snap.window_title,
            "ref_count": len(snap.refs), "tree": cut(snap.tree, 0)}


def drill(snapshot_id: str, root_ref: str, subtree: dict) -> Snapshot:
    """对某元素重新取子树并替换该区 refs（区外 ref 保持有效，区内旧 ref 失效）。"""
    snap = get_snapshot(snapshot_id)
    rec = snap.refs.get(root_ref)
    if rec is None:
        raise SnapshotError("INVALID_REF", "root ref 不属于该快照",
                            details={"ref": root_ref})
    counts = {"redacted": 0}
    norm = _normalize(subtree, 0, counts, redact=True)
    nodes = list(_walk(norm))
    # 把新子树挂到旧树上（按 identity 找到原节点）
    target = None
    for n in _walk(snap.tree):
        if (_role(n), _nid(n) or "", _name(n)) == rec.identity:
            target = n
            break
    if target is None:
        raise SnapshotError("STALE_REF", "root 元素已不在快照树中",
                            details={"kind": "saved_ref_absent", "ref": root_ref})
    # 删除旧子树内（不含 root 本身）的 ref：按旧节点的 identity 比对，再替换
    old_idents = {(_role(n), _nid(n) or "", _name(n)) for n in _walk(target)}
    for r, v in list(snap.refs.items()):
        if r != root_ref and v.depth > rec.depth and v.identity in old_idents:
            snap.refs.pop(r, None)
    target.clear()
    target.update(norm)

    ident_count: dict[tuple, int] = {}
    for n in _walk(snap.tree):
        ident = (_role(n), _nid(n) or "", _name(n))
        ident_count[ident] = ident_count.get(ident, 0) + 1
    # ref 序号只增不回退（避免删除旧 ref 后新元素复用同一编号造成混用）
    seq = max(snap.next_seq, max([int(r.split(":e")[1]) for r in snap.refs] or [0]))
    for n in nodes:
        role, name, nid = _role(n), _name(n), _nid(n)
        acts = _actions(n)
        if not _is_actionable(n, role, acts):
            continue
        seq += 1
        ref = f"@{snap.snapshot_id}:e{seq}"
        ident = (role, nid or "", name)
        n["ref_id"] = ref
        snap.refs[ref] = RefRecord(ref=ref, role=role, name=name, native_id=nid,
                                   actions=acts, depth=int(n.get("depth") or 0),
                                   identity_unique=ident_count.get(ident, 0) == 1,
                                   path=str(n.get("path") or ""))
    snap.next_seq = seq
    snap.redacted += counts["redacted"]
    return snap

# ── P1 第一刀（2026-11-13）：ref → 动作计划（客户端/执行器不改）──
# 只把"按 ref 找到元素"变成"给现有执行器的命令"；真正执行仍走既有动作链，
# 因此本模块保持只读，不引入任何动作执行调用。
_VERB_TO_EXISTING = {
    "click": "click",
    "long_click": "long_click",
    "scroll": "scroll",
    "set_text": "set_text",
}
_VERB_NEEDS_ACTION = {
    "click": ("Click",),
    "long_click": ("LongClick",),
    "scroll": ("Scroll",),
    "set_text": ("SetValue", "SetText"),
}


def plan_action(snapshot_id: str, ref: str, verb: str, *, value: str | None = None,
                current_tree: dict | None = None) -> dict:
    """把 ref 解析成给既有执行器的动作计划（不执行）。

    - 元素可用动作从快照的 ``available_actions`` 推导，缺动作直接拒绝（不再盲点）；
    - ``current_tree`` 传入时先做 stale/ambiguous 复核；
    - 返回 ``{"action": <既有动作名>, "target": <文本或 native_id>, "value": ...}``；
      调用方（现有 device 动作链）据此执行并回填投递结果。
    """
    if verb not in _VERB_TO_EXISTING:
        raise SnapshotError("INVALID_ARGS", f"不支持的 verb: {verb!r}",
                            details={"supported": sorted(_VERB_TO_EXISTING)})
    element = resolve(snapshot_id, ref, current_tree=current_tree)
    acts = {a.lower(): a for a in element.get("actions") or ()}
    want = _VERB_NEEDS_ACTION[verb]
    if verb != "set_text" and not any(w.lower() in acts for w in want):
        raise SnapshotError(
            "INVALID_ARGS", f"元素不支持 {verb}（可用：{sorted(acts.values())}）",
            details={"ref": ref, "actions": sorted(acts.values())})
    if verb == "set_text" and (value is None or value == ""):
        raise SnapshotError("INVALID_ARGS", "set_text 需要非空 value", details={"ref": ref})
    target = element.get("name") or element.get("native_id")
    if not target:
        raise SnapshotError("INVALID_ARGS", "元素缺少可定位的文本/ID（无法复用既有执行器）",
                            details={"ref": ref})
    return {
        "snapshot_id": snapshot_id,
        "ref": ref,
        "action": _VERB_TO_EXISTING[verb],
        "target": str(target),
        "value": value,
        # P1.5：稳定路径 + 期望身份——客户端可按 path 直解并按身份校验，
        # 不必再用 findNodeByText 模糊重定位（旧路径将在真机回归后删除）。
        "path": element.get("path") or "",
        "expected_identity": {
            "role": element.get("role") or "",
            "native_id": element.get("native_id") or "",
            "name": element.get("name") or "",
        },
        "delivery": "not_delivered",  # 执行器回填；not_delivered 才可安全重试
    }

def verify_after(snapshot_id: str, ref: str, current_tree: dict) -> dict:
    """动作后复核（P1.5）：用最新树判断元素还在不在、值/状态有没有变化。

    返回 ``{"present": bool, "value_before", "value_after", "state_before", "state_after",
    "changed": bool}``；元素消失抛 ``STALE_REF``（调用方据此决定重新快照，而不是重试动作）。
    """
    snap = get_snapshot(snapshot_id)
    rec = snap.refs.get(ref)
    if rec is None:
        raise SnapshotError("INVALID_REF", "ref 不属于该快照", details={"ref": ref})
    norm = _normalize(current_tree, 0, {"redacted": 0}, redact=True)
    hits = _find_matches(norm, rec.identity)
    if not hits:
        raise SnapshotError("STALE_REF", "动作后元素消失；请重新快照",
                            details={"kind": "saved_ref_absent", "ref": ref})
    before = _find_element_in_snapshot(snap, rec.identity)
    after = hits[0]
    bv, bst = (before or {}).get("value"), (before or {}).get("states")
    av, ast = after.get("value"), after.get("states")
    return {"present": True, "value_before": bv, "value_after": av,
            "state_before": bst, "state_after": ast,
            "changed": (bv != av) or (bst != ast)}


def _find_element_in_snapshot(snap: Snapshot, ident: tuple) -> dict | None:
    for n in _walk(snap.tree):
        if (_role(n), _nid(n) or "", _name(n)) == ident:
            return n
    return None

