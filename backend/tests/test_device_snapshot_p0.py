# -*- coding: utf-8 -*-
"""P0 合同：设备界面快照会话（只读）。

覆盖：ref 分配/唯一性、同窗双胞胎拒绝、STALE_REF、AMBIGUOUS_TARGET、脱敏、
skeleton 折叠、drill 替换旧 ref、TTL 过期、只读（模块内无任何动作调用）。
"""
from __future__ import annotations

import pathlib

import pytest

from app.device import snapshot as snap


def _tree(*children):
    return {"role": "window", "name": "测试窗口", "children": list(children)}


def _btn(name, nid=None):
    return {"role": "button", "name": name, "native_id": nid,
            "available_actions": ["Click", "SetFocus"]}


@pytest.fixture(autouse=True)
def _clean():
    snap.clear_store()
    yield
    snap.clear_store()


def test_refs_assigned_to_actionable_only():
    s = snap.create_snapshot(_tree(
        {"role": "text", "name": "静态说明"},              # 无动作、非交互角色
        _btn("确定", "OK"),
        {"role": "edit", "name": "输入", "available_actions": ["SetValue"]},
    ))
    refs = list(s.refs.values())
    assert len(refs) == 2
    assert {r.name for r in refs} == {"确定", "输入"}
    assert all(r.ref.startswith(f"@{s.snapshot_id}:e") for r in refs)


def test_twins_are_not_addressable():
    s = snap.create_snapshot(_tree(_btn("删除"), _btn("删除")))
    assert all(not r.identity_unique for r in s.refs.values())
    ref = next(iter(s.refs))
    with pytest.raises(snap.SnapshotError) as e:
        snap.resolve(s.snapshot_id, ref)
    assert e.value.code == "AMBIGUOUS_TARGET"
    assert e.value.details["kind"] == "identity_not_unique"


def test_resolve_stale_ref():
    s = snap.create_snapshot(_tree(_btn("保存", "SAVE")))
    ref = next(iter(s.refs))
    other = _tree(_btn("取消", "CANCEL"))
    with pytest.raises(snap.SnapshotError) as e:
        snap.resolve(s.snapshot_id, ref, current_tree=other)
    assert e.value.code == "STALE_REF"
    assert e.value.details["kind"] == "saved_ref_absent"


def test_resolve_ambiguous_after_duplicate_appears():
    s = snap.create_snapshot(_tree(_btn("提交", "SUBMIT")))
    ref = next(iter(s.refs))
    dup = _tree(_btn("提交", "SUBMIT"), _btn("提交", "SUBMIT"))
    with pytest.raises(snap.SnapshotError) as e:
        snap.resolve(s.snapshot_id, ref, current_tree=dup)
    assert e.value.code == "AMBIGUOUS_TARGET"
    assert e.value.details["kind"] == "saved_ref_ambiguous"


def test_secret_value_redacted():
    s = snap.create_snapshot(_tree(
        {"role": "password", "name": "密码", "value": "sk-secret",
         "available_actions": ["SetValue"]},
    ))
    assert s.redacted == 1
    node = next(iter(snap._walk(s.tree)))
    # 树里 value 已被清空（元素/ref 仍保留）
    assert all(n.get("value") in (None, "") for n in snap._walk(s.tree))


def test_skeleton_folds_branches():
    many = [_btn(f"项{i}") for i in range(12)]
    s = snap.create_snapshot(_tree({"role": "group", "name": "列表", "children": many}))
    sk = snap.skeleton(s.snapshot_id, max_depth=2, max_children=5)
    grp = sk["tree"]["children"][0]
    assert len(grp["children"]) == 5
    assert grp["children_count"] == 12


def test_drill_replaces_inner_refs():
    s = snap.create_snapshot(_tree(
        {"role": "group", "name": "面板", "available_actions": ["ScrollTo"],
         "children": [_btn("旧按钮", "OLD")]},
    ))
    root_ref = next(r.ref for r in s.refs.values() if r.name == "面板")
    inner = next(r.ref for r in s.refs.values() if r.name == "旧按钮")
    assert inner in s.refs
    snap.drill(s.snapshot_id, root_ref, {"role": "group", "name": "面板",
                                         "children": [_btn("新按钮", "NEW")]})
    assert inner not in s.refs
    names = {r.name for r in s.refs.values()}
    assert "新按钮" in names and "旧按钮" not in names


def test_ttl_expiry(monkeypatch):
    s = snap.create_snapshot(_tree(_btn("按钮")), ttl=1.0)
    real = snap.time.monotonic
    monkeypatch.setattr(snap.time, "monotonic", lambda: real() + 10)
    with pytest.raises(snap.SnapshotError) as e:
        snap.get_snapshot(s.snapshot_id)
    assert e.value.code == "SNAPSHOT_NOT_FOUND"


def test_invalid_ref_and_unknown_snapshot():
    s = snap.create_snapshot(_tree(_btn("按钮")))
    with pytest.raises(snap.SnapshotError) as e:
        snap.resolve(s.snapshot_id, "@nope:e1")
    assert e.value.code == "INVALID_REF"
    with pytest.raises(snap.SnapshotError) as e2:
        snap.get_snapshot("missing")
    assert e2.value.code == "SNAPSHOT_NOT_FOUND"


def test_module_is_readonly_ratchet():
    src = (pathlib.Path(__file__).resolve().parents[1] / "app/device/snapshot.py").read_text(
        encoding="utf-8")
    for banned in ("performAction", "performNodeAction", "ACTION_CLICK", "dispatchGesture"):
        assert banned not in src, banned

# ── P1 第一刀：ref → 动作计划（不执行）──

def test_plan_action_click_and_set_text():
    s = snap.create_snapshot(_tree(_btn("保存", "SAVE"),
                                   {"role": "edit", "name": "标题",
                                    "available_actions": ["SetValue"]}))
    btn = next(r for r in s.refs.values() if r.name == "保存")
    plan = snap.plan_action(s.snapshot_id, btn.ref, "click")
    assert plan["action"] == "click"
    assert plan["target"] == "保存"
    assert plan["delivery"] == "not_delivered"
    edit = next(r for r in s.refs.values() if r.name == "标题")
    plan2 = snap.plan_action(s.snapshot_id, edit.ref, "set_text", value="新标题")
    assert plan2["value"] == "新标题"


def test_plan_action_rejects_unsupported_verb_and_missing_action():
    s = snap.create_snapshot(_tree(_btn("关闭")))
    ref = next(iter(s.refs))
    with pytest.raises(snap.SnapshotError) as e:
        snap.plan_action(s.snapshot_id, ref, "drag")
    assert e.value.code == "INVALID_ARGS"
    # 有 ref 但元素没有 scroll 动作 → 拒绝（不盲点）
    with pytest.raises(snap.SnapshotError) as e2:
        snap.plan_action(s.snapshot_id, ref, "scroll")
    assert e2.value.code == "INVALID_ARGS"


def test_plan_action_propagates_stale_and_ambiguous():
    s = snap.create_snapshot(_tree(_btn("提交", "SUBMIT")))
    ref = next(iter(s.refs))
    with pytest.raises(snap.SnapshotError) as e:
        snap.plan_action(s.snapshot_id, ref, "click", current_tree=_tree(_btn("别的")))
    assert e.value.code == "STALE_REF"

# ── P1.5：稳定 path／身份签名／动作后复核 ──

def test_ref_carries_stable_path_and_identity():
    s = snap.create_snapshot(_tree(
        {"role": "group", "name": "区", "available_actions": ["ScrollTo"],
         "children": [_btn("确定", "OK")]},
    ))
    btn = next(r for r in s.refs.values() if r.name == "确定")
    assert btn.path and btn.path.startswith("0.")
    plan = snap.plan_action(s.snapshot_id, btn.ref, "click")
    assert plan["path"] == btn.path
    assert plan["expected_identity"] == {"role": "button", "native_id": "OK", "name": "确定"}


def test_verify_after_detects_value_change():
    s = snap.create_snapshot(_tree({"role": "edit", "name": "标题", "value": "旧",
                                    "available_actions": ["SetValue"]}))
    ref = next(iter(s.refs))
    same = _tree({"role": "edit", "name": "标题", "value": "旧",
                  "available_actions": ["SetValue"]})
    assert snap.verify_after(s.snapshot_id, ref, same)["changed"] is False
    changed = _tree({"role": "edit", "name": "标题", "value": "新",
                     "available_actions": ["SetValue"]})
    out = snap.verify_after(s.snapshot_id, ref, changed)
    assert out["changed"] is True and out["value_after"] == "新"


def test_verify_after_stale_when_element_gone():
    s = snap.create_snapshot(_tree(_btn("保存")))
    ref = next(iter(s.refs))
    with pytest.raises(snap.SnapshotError) as e:
        snap.verify_after(s.snapshot_id, ref, _tree(_btn("别的")))
    assert e.value.code == "STALE_REF"

