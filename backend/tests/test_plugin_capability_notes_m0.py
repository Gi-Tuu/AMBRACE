# -*- coding: utf-8 -*-
"""A4 批 8 块 B M0 —— capability_notes 校验 + 只读审计端点 + 两个「只记不判」点位。

钉住四条硬口径（任一改动即红）：
1. **flag 关 ⇒ 整段不解析、不报错**（对旧 manifest 逐字节等价，已装插件升级不失效）；
2. flag 开 ⇒ 4 个校验分支（未知权限名 / risk 非法 / 字段类型错 / 文案超长）
   ＋「键必须是本 manifest 自身 permissions 的子集」；
3. **不做「少写也拒」**（permissions 有、notes 里没有 ⇒ **不报错**，反向断言）；
4. 两个 obs_event 点位**加了留痕但返回值逐字段相同**（只记不判）。

不连生产库：全部用假 session 与 monkeypatch。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.plugins import manifest as mf

_PERM = "memory:read"          # 确定存在的合法权限名
_DECLARED = [_PERM]
_GOOD_NOTE = {"why": "读取记忆用于上下文", "risk": "low", "data": "只读，不外发"}


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(mf, "capability_notes_enabled", lambda: True)


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(mf, "capability_notes_enabled", lambda: False)


def _manifest(**kw) -> dict:
    """最小合法骨架 + capability_notes（可选）。"""
    base = {"name": "demo", "version": "1.0.0", "description": "d" * 5,
            "permissions": list(_DECLARED), "type": "http"}
    base.update(kw)
    return base


# ───────────────── 1. flag 关＝整段不解析（旧行为等价）─────────────────

def test_flag_off_absent_field_is_fine(flag_off):
    """字段缺省 ⇒ 合法（旧 manifest 完全不受影响）。"""
    assert mf.validate_manifest(_manifest()) is None


def test_flag_off_invalid_notes_are_ignored(flag_off):
    """★ 旧行为等价：flag 关时**连非法 notes 也不解析**，绝不因新字段把旧插件打成拒装。"""
    bad = _manifest(capability_notes={
        "totally:bogus:perm": {"why": "", "risk": "catastrophic", "data": 12345},
    })
    assert mf.validate_manifest(bad) is None, "flag 关时不得因 capability_notes 拒装"


def test_flag_off_helper_reads_default(monkeypatch):
    """M1 起总闸默认开（校验真生效；读 flag 失败仍按关兜底，见 capability_notes_enabled）。"""
    monkeypatch.undo()
    assert mf.capability_notes_enabled() in (True, False)  # 真读 AGENT_FLAGS
    from app.flags.agent_flags import AGENT_FLAGS
    assert AGENT_FLAGS.get("plugin_capability_notes") is True


# ───────────────── 2. flag 开＝4 分支 + 子集约束 ─────────────────

def test_flag_on_valid_notes_pass(flag_on):
    assert mf.validate_capability_notes(
        _manifest(capability_notes={_PERM: _GOOD_NOTE})) is None


def test_flag_on_unknown_permission_rejected(flag_on):
    out = mf.validate_capability_notes(
        _manifest(capability_notes={"no:such:perm": _GOOD_NOTE}))
    assert out and "未知权限名" in out


def test_flag_on_invalid_risk_rejected(flag_on):
    out = mf.validate_capability_notes(
        _manifest(capability_notes={_PERM: dict(_GOOD_NOTE, risk="catastrophic")}))
    assert out and "risk" in out


def test_flag_on_notes_not_dict_rejected(flag_on):
    out = mf.validate_capability_notes(_manifest(capability_notes=["a", "b"]))
    assert out and "对象" in out


def test_flag_on_note_value_not_dict_rejected(flag_on):
    out = mf.validate_capability_notes(_manifest(capability_notes={_PERM: "说明"}))
    assert out and "必须是对象" in out


def test_flag_on_empty_why_rejected(flag_on):
    out = mf.validate_capability_notes(
        _manifest(capability_notes={_PERM: dict(_GOOD_NOTE, why="   ")}))
    assert out and "why" in out


def test_flag_on_overlong_why_rejected(flag_on):
    out = mf.validate_capability_notes(
        _manifest(capability_notes={_PERM: dict(_GOOD_NOTE, why="x" * 500)}))
    assert out and "why" in out


def test_flag_on_data_not_str_rejected(flag_on):
    out = mf.validate_capability_notes(
        _manifest(capability_notes={_PERM: dict(_GOOD_NOTE, data=123)}))
    assert out and "data" in out


def test_flag_on_overlong_data_rejected(flag_on):
    out = mf.validate_capability_notes(
        _manifest(capability_notes={_PERM: dict(_GOOD_NOTE, data="x" * 500)}))
    assert out and "data" in out


def test_flag_on_key_must_be_subset_of_own_permissions(flag_on):
    """★ 写了说明就必须先声明该权限（防凭空声明/防多写）。"""
    extra = "life:read"
    assert extra in mf.VALID_PERMISSIONS
    out = mf.validate_capability_notes(
        _manifest(capability_notes={extra: _GOOD_NOTE}))
    assert out and "未在本 manifest 的 permissions 里声明" in out


def test_flag_on_under_declaration_is_allowed(flag_on):
    """★ 反向断言：**不做「少写也拒」**——permissions 有、notes 里没写 ⇒ 不报错。"""
    data = _manifest(permissions=[_PERM, "life:read"],
                     capability_notes={_PERM: _GOOD_NOTE})
    assert mf.validate_capability_notes(data) is None


def test_flag_on_too_many_notes_rejected(flag_on):
    notes = {_PERM: _GOOD_NOTE}
    for i in range(mf.MAX_CAPABILITY_NOTES + 1):
        notes[f"memory:read{i}"] = _GOOD_NOTE
    out = mf.validate_capability_notes(_manifest(capability_notes=notes))
    assert out and "条数" in out


def test_flag_on_none_notes_is_fine(flag_on):
    assert mf.validate_capability_notes(_manifest(capability_notes=None)) is None


def test_wiring_keeps_existing_error_precedence(flag_on):
    """新校验插在最后 ⇒ 既有字段的错误优先级一字不变。"""
    bad = _manifest(name="bad name!")
    out = mf.validate_manifest(bad)
    assert out and "name" in out  # 仍是既有的 name 报错，而非 capability_notes


# ───────────────── 3. 只读审计端点 ─────────────────

class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _RowsResult(self._rows)


def _patch_audit(monkeypatch, plugins, rows, *, raise_db=False):
    from app.api import plugins as api_plugins
    from app.plugins import registry as reg

    monkeypatch.setattr(reg, "list_plugins", lambda *a, **k: plugins)

    if raise_db:
        def _boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("app.db.database.async_session_factory", _boom)
    else:
        monkeypatch.setattr("app.db.database.async_session_factory",
                            lambda: _FakeDb(rows))
    return api_plugins.capability_audit


def test_audit_endpoint_empty_state(monkeypatch):
    """空态明确：插件无声明权限 ⇒ permissions 为空数组，字段一个不少。"""
    fn = _patch_audit(monkeypatch, plugins=[{"name": "p0", "enabled": True,
                                             "permissions": []}], rows=[])
    out = asyncio.run(fn(user_id=1))
    assert set(out) == {"items", "total", "signature", "window_days", "note"}
    assert out["total"] == 1
    assert out["signature"] == "not_enforced"   # 签名校验是桩，不得宣称已验签
    assert out["items"][0]["permissions"] == []
    assert out["items"][0]["signature"] == "not_enforced"


def test_audit_endpoint_declared_but_unused(monkeypatch):
    """声明了但 30 天 0 调用 ⇒ drift='unused'。"""
    fn = _patch_audit(monkeypatch,
                      plugins=[{"name": "p1", "enabled": True,
                                "permissions": [_PERM]}], rows=[])
    out = asyncio.run(fn(user_id=1))
    row = out["items"][0]["permissions"][0]
    assert row == {"permission": _PERM, "declared": True, "calls_30d": 0,
                   "last_called_at": None, "drift": "unused"}


def test_audit_endpoint_undeclared_but_called(monkeypatch):
    """有调用却未声明 ⇒ drift='undeclared'（第二类漂移）。"""
    rows = [(json.dumps({"plugin": "p1", "permission": _PERM}),
             __import__("datetime").datetime(2026, 10, 1, 0, 0, 0))]
    fn = _patch_audit(monkeypatch,
                      plugins=[{"name": "p1", "enabled": True, "permissions": []}],
                      rows=rows)
    out = asyncio.run(fn(user_id=1))
    row = out["items"][0]["permissions"][0]
    assert row["declared"] is False and row["drift"] == "undeclared"
    assert row["calls_30d"] == 1 and row["last_called_at"]


def test_audit_endpoint_readonly_degrades_on_db_failure(monkeypatch):
    """只读聚合失败 ⇒ 退化为空事实，绝不 500（只读端点不当异常出口）。"""
    fn = _patch_audit(monkeypatch,
                      plugins=[{"name": "p1", "enabled": True,
                                "permissions": [_PERM]}],
                      rows=[], raise_db=True)
    out = asyncio.run(fn(user_id=1))
    assert out["items"][0]["permissions"][0]["calls_30d"] == 0


# ───────────────── 4. 两个点位：只记不判 ─────────────────

class _PlugRow:
    def __init__(self, enabled=True):
        self.enabled = enabled


class _RegResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _RegDb:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _RegResult(self._row)


def _run_capability(monkeypatch, captured, *, consented: bool, known=True):
    from app.plugins import registry as reg

    class _Spec:
        permission = _PERM

    monkeypatch.setattr("app.device.capabilities.get_capability",
                        lambda cid: _Spec() if known else None)
    monkeypatch.setattr("app.db.database.async_session_factory",
                        lambda: _RegDb(_PlugRow(True)))

    async def _perms(plugin_name, tid):
        return {_PERM} if consented else set()

    monkeypatch.setattr(reg, "get_tenant_consented_permissions", _perms)
    async def _fake_trace(cid, route, detail, kind=None):
        captured.append((route, detail))

    # 2026-10-01：留痕改为「本轮内写完」的 obs_event_now（await 版），见 dev-changelog
    monkeypatch.setattr("app.memory.observability.obs_event_now", _fake_trace)
    return asyncio.run(reg.has_capability_permission("plug", 7, "cap"))


def test_registry_allow_is_traced_and_return_unchanged(monkeypatch):
    """放行：返回值 True 不变，且留痕 decision=allow / reason=consented。"""
    captured = []
    assert _run_capability(monkeypatch, captured, consented=True) is True
    route, detail = captured[-1]
    assert route == "plugin_capability"
    assert detail["decision"] == "allow" and detail["reason"] == "consented"
    assert detail["plugin"] == "plug" and detail["permission"] == _PERM


def test_registry_deny_is_traced_and_return_unchanged(monkeypatch):
    """拒绝：返回值 False 不变，且留痕 decision=deny / reason=not_consented。"""
    captured = []
    assert _run_capability(monkeypatch, captured, consented=False) is False
    assert captured[-1][1]["decision"] == "deny"
    assert captured[-1][1]["reason"] == "not_consented"


def test_registry_deny_unknown_capability_reason(monkeypatch):
    """未知能力 ⇒ 仍返回 False（门禁语义未改），留痕 reason=unknown_capability。"""
    captured = []
    assert _run_capability(monkeypatch, captured, consented=True, known=False) is False
    assert captured[-1][1]["reason"] == "unknown_capability"


def test_bridge_denied_is_traced_and_return_unchanged(monkeypatch):
    """bridge 拒绝处：返回值**逐字段相同**，且留痕 decision=deny。"""
    from app.application import plugin_bridge_service as bridge
    from app.device import actions as device_actions

    captured = []
    async def _fake_trace(cid, route, detail, kind=None):
        captured.append((route, detail))

    # 2026-10-01：留痕改为「本轮内写完」的 obs_event_now（await 版），见 dev-changelog
    monkeypatch.setattr("app.memory.observability.obs_event_now", _fake_trace)

    expected_reason = (f"{device_actions.REASON_INVALID_INTENT}"
                       f":{device_actions.REASON_PLUGIN_NOT_ALLOWED}")
    out = asyncio.run(bridge.device_action_dispatch(
        plugin_name="plug", params={"plugin": "evil"}, user_id=1))

    assert out == {"ok": True, "data": {"allowed": False, "reason": expected_reason,
                                        "dry_run": False,
                                        "status": device_actions.STATUS_DENIED}}
    assert captured and captured[-1][0] == "plugin_capability"
    assert captured[-1][1]["decision"] == "deny"
    assert captured[-1][1]["plugin"] == "plug"
