# -*- coding: utf-8 -*-
"""X2 内容包测试：分 kind schema 校验、manifest 集成、示例包加载、get_holidays 合并"""
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1].parent

from app.plugins.content_schema import validate_content_payload
from app.plugins.manifest import validate_manifest
from app.scheduling import holiday_calendar as hc


# ── schema 校验 ──


def test_holiday_fixed_valid():
    assert validate_content_payload({
        "kind": "holiday_fixed",
        "items": [{"date": "03-14", "name": "白色情人节", "lang": "zh"}],
    }) is None


def test_holiday_fixed_invalid_cases():
    base = {"date": "03-14", "name": "x"}
    assert validate_content_payload({"kind": "holiday_fixed", "items": []}) is not None
    assert validate_content_payload({"kind": "holiday_fixed", "items": [{}]}) is not None
    assert validate_content_payload({"kind": "holiday_fixed", "items": [{**base, "date": "13-01"}]}) is not None
    assert validate_content_payload({"kind": "holiday_fixed", "items": [{**base, "date": "3/14"}]}) is not None
    assert validate_content_payload({"kind": "holiday_fixed", "items": [{**base, "name": ""}]}) is not None
    assert validate_content_payload({"kind": "holiday_fixed", "items": [{**base, "lang": "fr"}]}) is not None


def test_opening_lines_valid_and_invalid():
    ok = {"kind": "opening_lines", "items": [{"text": "今天也想早点见到你。"}]}
    assert validate_content_payload(ok) is None
    assert validate_content_payload({"kind": "opening_lines", "items": [{"text": "好"}]}) is not None
    assert validate_content_payload({"kind": "opening_lines", "items": "not-a-list"}) is not None


def test_unknown_kind_rejected():
    assert validate_content_payload({"kind": "emoji", "items": [{"x": 1}]}) is not None
    assert validate_content_payload("nope") is not None


# ── manifest 集成 ──


def _manifest(**over):
    data = {
        "name": "festival_extra", "version": "1.0.0", "description": "d",
        "category": "plugin", "type": "content",
        "content": {"kind": "holiday_fixed", "items": [{"date": "03-14", "name": "白色情人节"}]},
    }
    data.update(over)
    return data


def test_manifest_accepts_content_pack():
    assert validate_manifest(_manifest()) is None


def test_manifest_rejects_bad_content():
    assert validate_manifest(_manifest(content={"kind": "emoji", "items": []})) is not None
    assert validate_manifest(_manifest(content=None)) is not None
    # 非 content 类型带 content 块 → 拒绝
    assert validate_manifest(_manifest(type="http", content={"kind": "holiday_fixed", "items": []})) is not None


# ── 示例包加载 + 节日合并 ──


def test_festival_extra_loads():
    import app.plugins.registry as plugin_registry

    info = plugin_registry.load_plugin_dir(REPO_ROOT / "plugins" / "examples" / "festival_extra")
    assert info is not None and info["type"] == "content"
    assert info["content"]["kind"] == "holiday_fixed"
    plugin_registry._loaded.pop("festival_extra", None)


def test_get_holidays_merges_enabled_content_pack(monkeypatch):
    import app.plugins.registry as plugin_registry

    info = plugin_registry.load_plugin_dir(REPO_ROOT / "plugins" / "examples" / "festival_extra")
    assert info is not None
    monkeypatch.setattr(plugin_registry, "_loaded", {"festival_extra": {"info": info}})
    monkeypatch.setattr(plugin_registry, "_enabled", {"festival_extra": True})
    try:
        names = [h["name"] for h in hc.get_holidays(date(2026, 3, 14))]
        assert "白色情人节" in names
    finally:
        plugin_registry._loaded.pop("festival_extra", None)


def test_get_holidays_disabled_pack_not_merged(monkeypatch):
    import app.plugins.registry as plugin_registry

    info = plugin_registry.load_plugin_dir(REPO_ROOT / "plugins" / "examples" / "festival_extra")
    monkeypatch.setattr(plugin_registry, "_loaded", {"festival_extra": {"info": info}})
    monkeypatch.setattr(plugin_registry, "_enabled", {"festival_extra": False})
    try:
        names = [h["name"] for h in hc.get_holidays(date(2026, 2, 14))]
        assert "白色情人节" not in names  # 内容包只在 03-14 生效
        assert "情人节" in names  # 内置不受影响
    finally:
        plugin_registry._loaded.pop("festival_extra", None)
