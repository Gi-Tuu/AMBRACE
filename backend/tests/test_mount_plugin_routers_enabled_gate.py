# -*- coding: utf-8 -*-
"""C3（2026-09-25）：``mount_plugin_routers`` 的「禁用插件不挂载」路由挂载级收口。

背景：请求级禁用闸（``plugin_http_gate``）早已存在，但 ``mount_plugin_routers`` 无条件
``include_router``，禁用插件的路由仍挂在 app 上。本锁要求 flag
``plugin_disabled_route_gate`` 开时跳过挂载、flag 关时**逐字旧行为**（全挂）。

不连库、不起服务：只用假 app（记录 ``include_router`` 调用）+ monkeypatch 注册表内存态。
"""
from app.plugins import registry

ENABLED = "pack_on"
DISABLED = "pack_off"
ROUTER_ON = object()
ROUTER_OFF = object()


class FakeApp:
    """只记录 include_router 调用的假 app。"""

    def __init__(self):
        self.mounted = []

    def include_router(self, router):
        self.mounted.append(router)


class RaisingEnabled:
    """``_enabled`` 替身：查指定插件名时抛异常，其余正常返回。"""

    def __init__(self, raise_for: str, values: dict):
        self._raise_for = raise_for
        self._values = values

    def get(self, name, default=False):
        if name == self._raise_for:
            raise RuntimeError("simulated _enabled read failure")
        return self._values.get(name, default)


def _prepare(monkeypatch, *, gate: bool, loaded: dict, enabled: dict):
    monkeypatch.setattr(registry, "_loaded", loaded, raising=False)
    monkeypatch.setattr(registry, "_enabled", enabled, raising=False)
    monkeypatch.setattr(registry, "plugin_disabled_route_gate_enabled", lambda: gate, raising=False)


def test_flag_off_mounts_everything(monkeypatch):
    """用例 a：flag 关 ⇒ enabled/disabled 两个插件都挂（旧行为逐字不变）。"""
    _prepare(monkeypatch, gate=False,
             loaded={ENABLED: {"router": ROUTER_ON}, DISABLED: {"router": ROUTER_OFF}},
             enabled={ENABLED: True, DISABLED: False})
    app = FakeApp()
    registry.mount_plugin_routers(app)
    assert app.mounted == [ROUTER_ON, ROUTER_OFF]


def test_flag_on_skips_disabled_plugin(monkeypatch):
    """用例 b：flag 开 ⇒ 只挂 enabled 的，disabled 的没被挂。"""
    _prepare(monkeypatch, gate=True,
             loaded={ENABLED: {"router": ROUTER_ON}, DISABLED: {"router": ROUTER_OFF}},
             enabled={ENABLED: True, DISABLED: False})
    app = FakeApp()
    registry.mount_plugin_routers(app)
    assert app.mounted == [ROUTER_ON]


def test_flag_on_treats_unknown_as_disabled(monkeypatch):
    """用例 c：flag 开 + 插件名不在 ``_enabled``（脏数据）⇒ 视为禁用、不挂。"""
    _prepare(monkeypatch, gate=True,
             loaded={ENABLED: {"router": ROUTER_ON}, "pack_ghost": {"router": ROUTER_OFF}},
             enabled={ENABLED: True})
    app = FakeApp()
    registry.mount_plugin_routers(app)
    assert app.mounted == [ROUTER_ON]


def test_flag_on_router_none_is_skipped_without_error(monkeypatch):
    """用例 d：flag 开 + ``router`` 为 None ⇒ 不挂也不抛，且不影响其它插件挂载。"""
    _prepare(monkeypatch, gate=True,
             loaded={DISABLED: {"router": None}, ENABLED: {"router": ROUTER_ON}},
             enabled={ENABLED: True, DISABLED: False})
    app = FakeApp()
    registry.mount_plugin_routers(app)
    assert app.mounted == [ROUTER_ON]

    _prepare(monkeypatch, gate=False,
             loaded={DISABLED: {"router": None}, ENABLED: {"router": ROUTER_ON}},
             enabled={ENABLED: True, DISABLED: False})
    app2 = FakeApp()
    registry.mount_plugin_routers(app2)
    assert app2.mounted == [ROUTER_ON]


def test_flag_on_enabled_read_exception_keeps_old_behavior(monkeypatch):
    """用例 e：``_enabled.get`` 抛异常 ⇒ 整体不抛，该插件按原逻辑照常挂载。"""
    loaded = {DISABLED: {"router": ROUTER_OFF}, ENABLED: {"router": ROUTER_ON}}
    enabled = RaisingEnabled(raise_for=DISABLED, values={ENABLED: True, DISABLED: False})
    _prepare(monkeypatch, gate=True, loaded=loaded, enabled=enabled)
    app = FakeApp()
    registry.mount_plugin_routers(app)
    assert ROUTER_OFF in app.mounted
    assert app.mounted == [ROUTER_OFF, ROUTER_ON]
