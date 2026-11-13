# -*- coding: utf-8 -*-
"""P2/P3 合同：客户端扁平树适配 + 角色工具规格（设计先行、默认不启用）。

数据全合成；不含真实用户内容。
"""
from __future__ import annotations

import pytest

from app.device import agent_tools, client_tree, snapshot as snap

ITEMS = [
    {"text": "登录", "clickable": True, "editable": False,
     "className": "android.widget.button", "x": 540, "y": 1200},
    {"text": "[输入框]", "clickable": False, "editable": True,
     "className": "android.widget.edittext", "x": 540, "y": 800},
    {"text": "记住我", "clickable": True, "editable": False,
     "className": "android.widget.checkbox", "x": 200, "y": 900},
]


@pytest.fixture(autouse=True)
def _clean():
    snap.clear_store()
    yield
    snap.clear_store()


def test_flat_list_maps_to_snapshot_tree():
    s = client_tree.observe(ITEMS, window_title="登录页", mode="full")
    assert s["window_title"] == "登录页"
    assert s["ref_count"] >= 2
    roles = {r["role"] for r in s["refs"]}
    assert "button" in roles and "edit" in roles
    edit = next(r for r in s["refs"] if r["role"] == "edit")
    assert "SetValue" in edit["actions"]
    assert edit["path"].startswith("0.")
    # 坐标只作为 hints，不进主定位字段
    assert edit["addressable"] is True


def test_placeholder_twins_are_not_addressable():
    twins = [
        {"text": "[图标]", "clickable": True, "editable": False,
         "className": "android.widget.imageview", "x": 100, "y": 100},
        {"text": "[图标]", "clickable": True, "editable": False,
         "className": "android.widget.imageview", "x": 300, "y": 100},
    ]
    s = client_tree.observe(twins, mode="full")
    assert s["refs"] and all(not r["addressable"] for r in s["refs"])


def test_observe_skeleton_collapses_children():
    many = [{"text": f"项{i}", "clickable": True, "editable": False,
             "className": "android.widget.button"} for i in range(12)]
    s = client_tree.observe(many, mode="skeleton", max_depth=1, max_children=5)
    tree = s["tree"]
    # window 根 -> 12 个子节点在 skeleton 下被折叠
    assert "children_count" in tree or any("children_count" in c for c in tree.get("children", []))


def test_observe_rejects_bad_payload():
    with pytest.raises(snap.SnapshotError) as e:
        client_tree.observe("not-a-tree")
    assert e.value.code == "INVALID_ARGS"


def test_agent_tool_specs_disabled_by_default():
    specs = agent_tools.register_device_tools()
    assert {s.name for s in specs} == {agent_tools.OBSERVE_TOOL, agent_tools.ACT_TOOL}
    assert all(s.enabled is False for s in specs)
    assert agent_tools.ACT_TOOL == "device_act"


def test_handle_observe_then_act_plan():
    out = agent_tools.handle_observe({"items": ITEMS, "mode": "full"})
    snap_id = out["snapshot_id"]
    btn = next(r for r in out["refs"] if r["role"] == "button" and r["addressable"])
    plan = agent_tools.handle_act({"snapshot_id": snap_id, "ref": btn["ref"], "verb": "click"})
    assert plan["action"] == "click"
    assert plan["delivery"] == "not_delivered"
    assert plan["expected_identity"]["name"] == btn["name"]


def test_handle_act_rejects_missing_fields():
    with pytest.raises(snap.SnapshotError) as e:
        agent_tools.handle_act({"snapshot_id": "x"})
    assert e.value.code == "INVALID_ARGS"
