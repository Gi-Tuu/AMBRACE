"""U1 安全向：tool_runner 权限判定「fail-open 分级」（2026-09-29）

对应 docs 评审点名的 P1：check_tool_permission 原先三处 fail-open（user_id=None / scope=None /
except 分支）。本测试锁定改造后的三档语义：
- 第 0 档 基线放行（user_id=None、scope=None）：语义逐字不变，且**不会**触碰权限系统；
- 第 1 档 权限系统裁决：allow / ask / forbid 三档 + MCP 归属校验均不受影响；
- 第 2 档 异常兜底：高风险工具 fail-closed → forbid，低风险仍 allow，异常必打 WARNING。

全程 monkeypatch 假 permission_service / 假 ownership 查询，不连库（不触任何 SQL）。
"""
import asyncio

import pytest

from app.agent import tool_runner
from app.agent.tools import RISK_HIGH, RISK_LOW, ToolSpec
from app.application import permission_service


def _run(coro):
    return asyncio.run(coro)


def _spec(name="t.demo", *, risk=RISK_LOW, scope="image_gen", plugin=None,
          plugin_action=None, server_id=None):
    """假 ToolSpec（纯数据构造，无 IO）"""
    return ToolSpec(
        name=name, description="d", risk_level=risk, scope=scope,
        plugin=plugin, plugin_action=plugin_action, server_id=server_id,
    )


class _FakeService:
    """假 permission_service：可配置返回档位或直接抛异常，并记录调用"""

    def __init__(self, *, mode="allow", mcp_mode=None, raise_exc=None):
        self.mode = mode
        self.mcp_mode = mcp_mode
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
        return self.mcp_mode if self.mcp_mode is not None else self.mode


class _FakeLog:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else str(msg))

    def info(self, msg, *args):
        pass


@pytest.fixture
def boom(monkeypatch):
    """权限系统异常（模拟查表失败）"""
    fake = _FakeService(raise_exc=RuntimeError("permission table unavailable"))
    monkeypatch.setattr(permission_service, "check_mode", fake.check_mode)
    monkeypatch.setattr(permission_service, "check_mcp_mode", fake.check_mcp_mode)
    return fake


@pytest.fixture
def stub(monkeypatch):
    """权限系统正常返回"""
    fake = _FakeService(mode="allow")
    monkeypatch.setattr(permission_service, "check_mode", fake.check_mode)
    monkeypatch.setattr(permission_service, "check_mcp_mode", fake.check_mcp_mode)
    return fake


def _patch_ownership(monkeypatch, owned: bool):
    import app.mcp.ownership as ownership

    async def _fake(user_id, server_id):
        return owned

    monkeypatch.setattr(ownership, "user_owns_server", _fake)


# ---------------------------------------------------------------- 第 2 档：异常分级 fail-closed

def test_exception_high_risk_forbids(boom):
    """①判据 risk_level=high：异常 → forbid（旧行为是 allow，正是被点名的 P1）"""
    assert _run(tool_runner.check_tool_permission(_spec(risk=RISK_HIGH), user_id=7)) == "forbid"
    assert boom.calls  # 确实进了权限系统才异常


def test_exception_mcp_scope_forbids(boom, monkeypatch):
    """②判据 scope=mcp_*：异常 → forbid"""
    _patch_ownership(monkeypatch, True)
    spec = _spec(name="mcp.srv.write_file", scope="mcp_demo", server_id=1)
    assert _run(tool_runner.check_tool_permission(spec, user_id=7)) == "forbid"


def test_exception_plugin_extension_scope_forbids(boom):
    """③判据 插件行动类（scope=extension）：异常 → forbid"""
    assert _run(tool_runner.check_tool_permission(_spec(scope="extension"), user_id=7)) == "forbid"


def test_exception_browser_device_scope_forbids(boom):
    """③判据 设备/浏览器行动类（scope=browser）：异常 → forbid"""
    assert _run(tool_runner.check_tool_permission(_spec(scope="browser"), user_id=7)) == "forbid"


def test_exception_plugin_resolves_to_extension_and_forbids(boom, monkeypatch):
    """插件工具（scope 由 _plugin_scope 映射）异常 → forbid，走的是同一条分级路径"""
    monkeypatch.setattr(permission_service, "_plugin_scope", lambda p: "extension")
    spec = _spec(name="browser_ext.click", scope=None, plugin="browser_ext", plugin_action="click")
    assert _run(tool_runner.check_tool_permission(spec, user_id=7)) == "forbid"


def test_exception_low_risk_media_still_allows(boom):
    """低风险本地媒体类（scope=image_gen / risk=low）异常 → 仍 allow（不改体验）"""
    assert _run(tool_runner.check_tool_permission(_spec(risk=RISK_LOW), user_id=7)) == "allow"


def test_exception_asr_scope_allows(boom):
    """非高风险 scope（asr）异常 → allow"""
    assert _run(tool_runner.check_tool_permission(_spec(scope="asr", risk=RISK_LOW), user_id=7)) == "allow"


def test_exception_logs_warning_with_tool_user_and_error(boom, monkeypatch):
    """异常分支必打 WARNING，含工具名 / user_id / 异常摘要"""
    log = _FakeLog()
    monkeypatch.setattr(tool_runner, "_logger", log)
    _run(tool_runner.check_tool_permission(_spec(name="t.danger", risk=RISK_HIGH), user_id=42))
    assert len(log.warnings) == 1
    line = log.warnings[0]
    assert "t.danger" in line and "42" in line and "permission table unavailable" in line
    assert "forbid" in line  # 决策一并留痕，便于事后排查


# ---------------------------------------------------------------- 第 0 档：基线放行语义逐字不变

def test_baseline_user_id_none_allows_without_touching_service(stub, monkeypatch):
    """老路径①：user_id=None → allow，且不查权限系统、不解析 scope（逐字保持原调用顺序）"""
    resolved = []
    monkeypatch.setattr(tool_runner, "_resolve_scope",
                        lambda s: resolved.append(s) or None)
    assert _run(tool_runner.check_tool_permission(_spec(risk=RISK_HIGH), user_id=None)) == "allow"
    assert stub.calls == []
    assert resolved == []


def test_baseline_scope_none_allows_without_touching_service(stub):
    """老路径②：scope=None（本地能力）→ allow，且不查权限系统；高风险也不受影响"""
    assert _run(tool_runner.check_tool_permission(_spec(scope=None), user_id=7)) == "allow"
    assert _run(tool_runner.check_tool_permission(_spec(scope=None, risk=RISK_HIGH), user_id=7)) == "allow"
    assert stub.calls == []


def test_baseline_decision_function_returns_allow_for_both_tiers():
    """显式分级函数：两条基线场景都返回 allow（语义集中一处，便于审计）"""
    assert tool_runner._baseline_decision(_spec(), None, None) == "allow"
    assert tool_runner._baseline_decision(_spec(), 7, None) == "allow"
    assert tool_runner._baseline_decision.__doc__  # docstring 必须写清不是漏洞的理由
    assert "不是漏洞" in tool_runner._baseline_decision.__doc__


# ---------------------------------------------------------------- 第 1 档：正常裁决三档不受影响

def test_normal_allow_passthrough(stub):
    stub.mode = "allow"
    assert _run(tool_runner.check_tool_permission(_spec(), user_id=7)) == "allow"


def test_normal_ask_passthrough(stub):
    stub.mode = "ask"
    assert _run(tool_runner.check_tool_permission(_spec(), user_id=7)) == "ask"


def test_normal_forbid_passthrough(stub):
    stub.mode = "forbid"
    assert _run(tool_runner.check_tool_permission(_spec(scope="browser"), user_id=7)) == "forbid"


def test_normal_mcp_high_risk_ask_passthrough(stub, monkeypatch):
    """MCP 正常链路：归属通过 → 按 check_mcp_mode 结果（高风险默认 ask）"""
    _patch_ownership(monkeypatch, True)
    stub.mcp_mode = "ask"
    spec = _spec(name="mcp.srv.write_file", scope="mcp_demo", risk=RISK_HIGH, server_id=1)
    assert _run(tool_runner.check_tool_permission(spec, user_id=7)) == "ask"
    assert stub.calls[-1][0] == "check_mcp_mode"


def test_mcp_ownership_denied_forbids_without_consulting_service(stub, monkeypatch):
    """归属校验（mcp 非本人）仍 forbid，且不再问权限系统"""
    _patch_ownership(monkeypatch, False)
    spec = _spec(name="mcp.srv.read_file", scope="mcp_demo", server_id=99)
    assert _run(tool_runner.check_tool_permission(spec, user_id=7)) == "forbid"
    assert stub.calls == []


def test_mcp_ownership_denied_forbids_even_when_service_raises(boom, monkeypatch):
    """归属校验后 DB 异常也 forbid（非本人一律拒绝，不受分级影响）"""
    _patch_ownership(monkeypatch, False)
    spec = _spec(name="mcp.srv.x", scope="mcp_demo", server_id=99)
    assert _run(tool_runner.check_tool_permission(spec, user_id=7)) == "forbid"


# ---------------------------------------------------------------- 高风险判据本身

@pytest.mark.parametrize("scope,risk,expected", [
    (None, RISK_HIGH, True),          # 判据①：risk_level=high（scope 为空也不改变）
    ("mcp_demo", RISK_LOW, True),     # 判据②：mcp_ 前缀
    ("extension", RISK_LOW, True),    # 判据③：插件行动类
    ("browser", RISK_LOW, True),      # 判据③：设备/浏览器行动类
    ("image_gen", RISK_LOW, False),   # 低风险媒体能力：不判高风险
    ("asr", RISK_LOW, False),
    (None, RISK_LOW, False),
])
def test_high_risk_predicate(scope, risk, expected):
    assert tool_runner._is_high_risk_for_failopen(_spec(risk=risk, scope=scope), scope) is expected
