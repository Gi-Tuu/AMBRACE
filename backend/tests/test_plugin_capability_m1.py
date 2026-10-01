# -*- coding: utf-8 -*-
"""A4 批 8 块 B M1 —— capability_notes 真生效：插件自述 risk=high 接入 fail-open 分级（**只收紧**）。

钉住六条硬口径（任一改动即红）：
1. ``plugin_capability_notes`` 默认值 True（关掉即回 M0 行为）；
2. 插件已加载 manifest 的 ``capability_notes`` 里**存在任一** ``risk == "high"`` ⇒ 权限系统抛异常时
   fail-closed（forbid）；
3. medium / low / 无 capability_notes / 插件未加载 / manifest 读不到或非法 ⇒ 与改动前**逐字段相同**
   （把改动前的三条判据原样复刻成 `_legacy_is_high_risk` 做对照，逐组断言相等）；
4. flag 关 ⇒ 即使声明 high 也不生效（回旧行为 allow）；
5. 既有三条判据（risk_level=high / mcp_ 前缀 / 扩展类 scope）与正常裁决（allow/ask）不受影响；
6. 第 0 档基线放行（user_id=None / scope=None）不受影响——新判据**只**作用于异常兜底路径。

不连生产库：permission_service 全程 monkeypatch；manifest 落 tmp_path 下的临时文件，「已加载插件」
用 registry 既有内存视图口 ``get_plugin`` monkeypatch 指向该临时目录（不新造缓存）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent import tool_runner
from app.agent.tools import RISK_HIGH, RISK_LOW, ToolSpec
from app.application import permission_service
from app.plugins import manifest as mf
from app.plugins import registry as reg

_PLUGIN = "demo"
_PERM = "memory:read"
_PERM2 = "send_message"
_ABSENT = object()          # ＝「manifest 里没写 capability_notes」

# 改动前既有三条判据的原表（与 tests/test_tool_permission_failopen.py 同一张）
_LEGACY_TABLE = [
    (None, RISK_HIGH, True),
    ("mcp_demo", RISK_LOW, True),
    ("extension", RISK_LOW, True),
    ("browser", RISK_LOW, True),
    ("image_gen", RISK_LOW, False),
    ("asr", RISK_LOW, False),
    (None, RISK_LOW, False),
]


def _spec(*, risk=RISK_LOW, scope="image_gen", plugin=None, plugin_action=None) -> ToolSpec:
    """假 ToolSpec（纯数据构造，无 IO）；scope 默认取非行动类值以便隔离判据④。"""
    return ToolSpec(name="demo.act", description="d", risk_level=risk, scope=scope,
                    plugin=plugin, plugin_action=plugin_action)


def _run(coro):
    return asyncio.run(coro)


def _note(risk: str, why: str = "用于演示", data: str = "只读，不外发") -> dict:
    return {"why": why, "risk": risk, "data": data}


def _legacy_is_high_risk(spec: ToolSpec, scope: str | None) -> bool:
    """**改动前**的高风险判据（三条，原样复刻）——「除新增外逐字段相同」的对照基准。"""
    if getattr(spec, "risk_level", "") == RISK_HIGH:
        return True
    if scope and scope.startswith("mcp_"):
        return True
    try:
        from app.application import permission_service as ps
        return scope in {ps.SCOPE_EXTENSION, ps.SCOPE_BROWSER}
    except Exception:
        return False


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(mf, "capability_notes_enabled", lambda: True)


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(mf, "capability_notes_enabled", lambda: False)


def _install(tmp_path, monkeypatch, *, capability_notes=_ABSENT, plugin: str = _PLUGIN,
             raw: str | None = None, write_file: bool = True, resolvable: bool = True):
    """把「已加载插件」指向 tmp_path 下的临时 manifest（只改 registry.get_plugin 的返回值）。"""
    d = tmp_path / plugin
    d.mkdir(parents=True, exist_ok=True)
    if write_file:
        if raw is None:
            data = {"name": plugin, "version": "1.0.0", "description": "demo plugin",
                    "permissions": [_PERM, _PERM2], "type": "http"}
            if capability_notes is not _ABSENT:
                data["capability_notes"] = capability_notes
            raw = json.dumps(data)
        (d / "manifest.json").write_text(raw, encoding="utf-8")
    if resolvable:
        monkeypatch.setattr(reg, "get_plugin",
                            lambda name: {"name": name, "path": str(d)} if name == plugin else None)
    else:
        monkeypatch.setattr(reg, "get_plugin", lambda name: None)
    return d


class _Service:
    """假 permission_service：正常返回档位或直接抛异常，并记录调用。"""

    def __init__(self, *, mode="allow", raise_exc=None):
        self.mode = mode
        self.raise_exc = raise_exc
        self.calls = []

    async def check_mode(self, user_id, scope):
        self.calls.append(("check_mode", user_id, scope))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.mode

    async def check_mcp_mode(self, user_id, scope, risk="medium"):
        self.calls.append(("check_mcp_mode", user_id, scope, risk))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.mode


def _patch_service(monkeypatch, fake: _Service) -> _Service:
    monkeypatch.setattr(permission_service, "check_mode", fake.check_mode)
    monkeypatch.setattr(permission_service, "check_mcp_mode", fake.check_mcp_mode)
    return fake


@pytest.fixture
def boom(monkeypatch):
    """权限系统异常（模拟查表失败）——新判据只在这条路径上起作用。"""
    return _patch_service(
        monkeypatch, _Service(raise_exc=RuntimeError("permission table unavailable")))


@pytest.fixture
def stub(monkeypatch):
    """权限系统正常返回。"""
    return _patch_service(monkeypatch, _Service(mode="allow"))


# ───────────────── 1. 默认值与开关目录登记 ─────────────────

def test_flag_default_is_true_after_m1():
    """M1：默认开 ⇒ 校验与收紧都真生效（回退＝把那行改回 False）。"""
    from app.flags.agent_flags import AGENT_FLAGS
    assert AGENT_FLAGS.get("plugin_capability_notes") is True
    assert mf.capability_notes_enabled() is True


def test_flag_catalog_row_follows_new_default():
    """目录行与新默认值同口径：文案不再宣称默认关；visible 仍 False（不直显）。"""
    from app.application.flag_catalog import FLAG_CATALOG
    row = FLAG_CATALOG.get("plugin_capability_notes")
    assert row is not None
    assert row["group"] == "provider" and row["visible"] is False
    assert "默认关闭" not in row["desc_zh"]
    assert "高风险" in row["desc_zh"] and "收紧" in row["desc_zh"]
    assert "high risk" in row["desc_en"] and "tightened" in row["desc_en"]


# ───────────────── 2. 判据④本体（异常兜底 → fail-closed）─────────────────

def test_high_note_forbids_when_permission_raises(flag_on, boom, tmp_path, monkeypatch):
    """★ 核心新增：插件自述 high ⇒ 权限系统异常时 fail-closed（改动前是 allow）。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    spec = _spec(plugin=_PLUGIN, plugin_action="go")
    assert _run(tool_runner.check_tool_permission(spec, user_id=7)) == "forbid"
    assert boom.calls  # 确实进了权限系统才异常（不是提前短路）


def test_any_one_high_note_is_enough(flag_on, boom, tmp_path, monkeypatch):
    """多条自述里**任一** high 即高风险（其余 low 不抵消）。"""
    _install(tmp_path, monkeypatch,
             capability_notes={_PERM: _note("low"), _PERM2: _note("high")})
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "forbid"


def test_medium_note_keeps_old_allow(flag_on, boom, tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("medium")})
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_low_note_keeps_old_allow(flag_on, boom, tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("low")})
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_absent_notes_keep_old_allow(flag_on, boom, tmp_path, monkeypatch):
    """字段缺省（所有存量插件就是这个形态）⇒ 与改动前一字不变。"""
    _install(tmp_path, monkeypatch)
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_flag_off_high_note_falls_back_to_old_behavior(flag_off, boom, tmp_path, monkeypatch):
    """★ 一键回退：flag 关 ⇒ 声明 high 也照旧 allow（＝M0/改动前行为）。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_predicate_flag_off_is_false(flag_off, tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    assert tool_runner._plugin_self_declares_high_risk(_PLUGIN) is False


def test_predicate_flag_on_high_is_true(flag_on, tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    assert tool_runner._plugin_self_declares_high_risk(_PLUGIN) is True


def test_predicate_empty_plugin_is_false(flag_on):
    """spec.plugin 为空 ⇒ 一次都不查（非插件工具完全不进本判据）。"""
    assert tool_runner._plugin_self_declares_high_risk(None) is False
    assert tool_runner._plugin_self_declares_high_risk("") is False


# ───────────────── 3. 读不到 ⇒ 绝不新增拒绝、也绝不抛错 ─────────────────

def test_plugin_not_loaded_keeps_old_allow(flag_on, boom, tmp_path, monkeypatch):
    """插件不在内存视图（未加载/已卸载）⇒ 与改动前相同（allow）。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")}, resolvable=False)
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_manifest_file_missing_keeps_old_allow(flag_on, boom, tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, write_file=False)
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_manifest_broken_json_keeps_old_allow(flag_on, boom, tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, raw="{ not json")
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_manifest_schema_invalid_keeps_old_allow(flag_on, boom, tmp_path, monkeypatch):
    """manifest 本身通不过校验（load_manifest 返回 None）⇒ 不据此收紧。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")},
             raw=json.dumps({"version": "1.0.0"}))
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_invalid_notes_shape_keeps_old_allow(flag_on, boom, tmp_path, monkeypatch):
    """自述字段非法（risk 不在档内）⇒ 整份 manifest 校验失败 ⇒ 按旧行为放行。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("catastrophic")})
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_registry_lookup_raises_keeps_old_allow(flag_on, boom, monkeypatch):
    """读取过程抛异常（视图不可用）⇒ 吞掉并按旧行为放行，不阻断工具链路。"""
    def _boom(_name):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(reg, "get_plugin", _boom)
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"


def test_notes_not_dict_does_not_crash(flag_on, monkeypatch):
    """notes 不是对象（插件乱写，且绕过校验直读）⇒ 判 False，不抛异常。"""
    monkeypatch.setattr(reg, "get_plugin", lambda _n: {"path": "x"})
    monkeypatch.setattr(mf, "load_manifest", lambda _p: {"capability_notes": ["a", "b"]})
    assert tool_runner._plugin_self_declares_high_risk(_PLUGIN) is False


# ───────────────── 4. 与改动前逐字段相同（对照基准）─────────────────

@pytest.mark.parametrize("notes", [
    _ABSENT,                                       # 无 capability_notes
    {_PERM: _note("medium")},                      # 自述中风险
    {_PERM: _note("low"), _PERM2: _note("low")},   # 自述低风险
])
@pytest.mark.parametrize("scope,risk,_expected", _LEGACY_TABLE)
def test_equals_pre_change_when_not_declaring_high(flag_on, tmp_path, monkeypatch,
                                                   scope, risk, _expected, notes):
    """未声明 high 的每一种组合 ⇒ 判据结果与改动前**逐字段相同**（对照复刻的旧三条判据）。"""
    _install(tmp_path, monkeypatch, capability_notes=notes)
    spec = _spec(risk=risk, scope=scope, plugin=_PLUGIN)
    assert tool_runner._is_high_risk_for_failopen(spec, scope) is _legacy_is_high_risk(spec, scope)


@pytest.mark.parametrize("scope,risk,expected", _LEGACY_TABLE)
def test_existing_criteria_table_unchanged_without_notes(flag_on, tmp_path, monkeypatch,
                                                         scope, risk, expected):
    """装了插件视图但没写自述 ⇒ 既有三条判据整张表一格不变（含 False 格）。"""
    _install(tmp_path, monkeypatch)
    spec = _spec(risk=risk, scope=scope, plugin=_PLUGIN)
    assert tool_runner._is_high_risk_for_failopen(spec, scope) is expected


@pytest.mark.parametrize("scope,risk", [
    (None, RISK_HIGH), ("mcp_demo", RISK_LOW), ("extension", RISK_LOW), ("browser", RISK_LOW),
])
def test_declaring_high_never_relaxes_existing_true_cells(flag_on, tmp_path, monkeypatch,
                                                          scope, risk):
    """只收紧：原本 True 的格子（①②③）在声明 high 后仍 True，不存在任何放宽方向。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    spec = _spec(risk=risk, scope=scope, plugin=_PLUGIN)
    assert tool_runner._is_high_risk_for_failopen(spec, scope) is True


def test_no_plugin_field_never_touches_notes(flag_on, boom, tmp_path, monkeypatch):
    """非插件工具（plugin=None）连判据④都不进：声明 high 的插件摆在旁边也不影响。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    assert _run(tool_runner.check_tool_permission(
        _spec(risk=RISK_LOW, scope="image_gen"), user_id=7)) == "allow"


# ───────────────── 5. 其余档位不受影响 ─────────────────

def test_exception_decision_for_extension_scope_unchanged(flag_on, boom, tmp_path, monkeypatch):
    """行动类 scope（extension）异常 → 仍 forbid（判据③与④叠加不改变结果）。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("low")})
    assert _run(tool_runner.check_tool_permission(
        _spec(scope="extension", plugin=_PLUGIN), user_id=7)) == "forbid"


def test_mcp_plugin_tool_still_forbids_on_exception(flag_on, boom, tmp_path, monkeypatch):
    """判据②优先：mcp_ scope 插件工具异常 → forbid（与自述内容无关）。"""
    import app.mcp.ownership as ownership

    async def _owned(_user_id, _server_id):
        return True

    monkeypatch.setattr(ownership, "user_owns_server", _owned)
    _install(tmp_path, monkeypatch)
    assert _run(tool_runner.check_tool_permission(
        _spec(scope="mcp_demo", plugin=_PLUGIN), user_id=7)) == "forbid"


def test_normal_allow_and_ask_decisions_unaffected(flag_on, stub, tmp_path, monkeypatch):
    """权限系统正常返回 ⇒ 不因自述 high 改判（新判据只碰异常兜底这一条路径）。"""
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "allow"
    stub.mode = "ask"
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=7)) == "ask"


def test_baseline_tiers_unaffected_by_high_notes(flag_on, stub, tmp_path, monkeypatch):
    """第 0 档（user_id=None / scope=None）⇒ 仍 allow，不查权限系统、不看自述。"""
    monkeypatch.setattr(permission_service, "_plugin_scope", lambda _p: None)
    _install(tmp_path, monkeypatch, capability_notes={_PERM: _note("high")})
    assert _run(tool_runner.check_tool_permission(
        _spec(plugin=_PLUGIN), user_id=None)) == "allow"
    assert _run(tool_runner.check_tool_permission(
        _spec(scope=None, plugin=_PLUGIN), user_id=7)) == "allow"
    assert stub.calls == []


# ───────────────── 6. 边界自证（docstring 必须写清插件粒度）─────────────────

def test_docstring_declares_plugin_granularity_boundary():
    doc = tool_runner._is_high_risk_for_failopen.__doc__ or ""
    assert "插件粒度" in doc and "M2" in doc
    assert "动作 → 权限名" in doc
    helper_doc = tool_runner._plugin_self_declares_high_risk.__doc__ or ""
    assert "不新造缓存" in helper_doc


def test_helper_does_not_build_its_own_cache():
    """判据④只借用 registry 既有视图与既有读取函数（不新增缓存容器）。"""
    import inspect

    src = inspect.getsource(tool_runner._plugin_self_declares_high_risk)
    assert "get_plugin" in src and "load_manifest" in src
    assert "_cache" not in src and "global " not in src
