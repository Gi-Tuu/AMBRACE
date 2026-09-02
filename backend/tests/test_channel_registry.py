# -*- coding: utf-8 -*-
"""X5 渠道端口测试：register_channel 注册/同源替换/异源重名/来源启用过滤/插件关联解析
/权限 scope 经渠道 meta/manifest 渠道权限前缀规则。"""
import pytest

from app.providers import channel as ch
from app.providers import registry as prov_reg
from app.plugins import manifest as manifest_mod


class _FakePort:
    async def publish(self, payload):  # pragma: no cover - 契约占位
        return {"ok": True}

    async def pull_comments(self, payload):
        return []

    async def reply_comment(self, payload):
        return {"ok": True}

    async def upload_media(self, payload):
        return {"ok": False}

    async def binding_status(self, payload):
        return {"bound": False}


@pytest.fixture()
def _clean_registry():
    from app.plugins import registry as plugin_reg
    plugin_reg._enabled["test_src"] = True  # 插件来源渠道默认被来源过滤拦下，测试显式启用
    plugin_reg._enabled["douyin_mcp"] = True
    yield
    plugin_reg._enabled.pop("test_src", None)
    plugin_reg._enabled.pop("douyin_mcp", None)
    prov_reg.unregister_providers_for_source("test_src")
    prov_reg.unregister_providers_for_source("other_src")
    prov_reg.unregister_providers_for_source("douyin_mcp")


def test_register_resolve_meta_list(_clean_registry):
    port = _FakePort()
    ch.register_channel("testch", port, meta={"label": "测试", "plugin": "test_src"}, source="test_src")
    assert ch.resolve_channel("testch") is port
    assert ch.channel_meta("testch")["label"] == "测试"
    names = [c["name"] for c in ch.list_channels()]
    assert "testch" in names


def test_replace_same_source_and_reject_other(_clean_registry):
    ch.register_channel("testch", _FakePort(), meta={}, source="test_src")
    # 同源重载（sync_plugins_db 重扫 / 测试重复加载）= 替换，不抛错
    ch.register_channel("testch", _FakePort(), meta={"v": 2}, source="test_src")
    assert ch.channel_meta("testch") == {"v": 2}
    # 异源重名 = 拒绝
    with pytest.raises(ValueError):
        ch.register_channel("testch", _FakePort(), meta={}, source="other_src")


def test_channel_for_plugin(_clean_registry):
    ch.register_channel("douyin", _FakePort(), meta={"plugin": "douyin_mcp"}, source="douyin_mcp")
    hit = ch.channel_for_plugin("douyin_mcp")
    assert hit is not None and hit[0] == "douyin"
    assert ch.channel_for_plugin("nope") is None


def test_resolve_disabled_source_returns_none(_clean_registry):
    """插件来源渠道停用（plugins registry _enabled 无记录）→ resolve 不可见（复用 X3 过滤）"""
    ch.register_channel("testch", _FakePort(), meta={}, source="test_src")
    from app.plugins import registry as plugin_reg
    plugin_reg._enabled.pop("test_src", None)
    assert ch.resolve_channel("testch") is None
    plugin_reg._enabled["test_src"] = True
    assert ch.resolve_channel("testch") is not None


def test_plugin_scope_via_channel_meta(_clean_registry):
    """权限 scope：注册渠道 meta.scope 优先，未注册插件回退 extension"""
    from app.application import permission_service as perm
    ch.register_channel("douyin", _FakePort(),
                        meta={"plugin": "douyin_mcp", "scope": "douyin"}, source="douyin_mcp")
    assert perm._plugin_scope("douyin_mcp") == "douyin"
    assert perm._plugin_scope("whatever_else") == perm.SCOPE_EXTENSION


def test_manifest_channel_permission_prefix(_clean_registry):
    """manifest 渠道权限前缀规则：声明 channel 的插件可用 <渠道名>_ 前缀自有权限"""
    base = {"name": "x_ch", "version": "0.1.0", "description": "d", "author": "a",
            "category": "plugin", "hooks": [], "permissions": [], "config": {}}
    # 未声明渠道：自有前缀权限拒绝
    m1 = dict(base, permissions=["xch_publish"])
    assert manifest_mod.validate_manifest(m1) is not None
    # 声明渠道：前缀匹配放行
    m2 = dict(base, permissions=["xch_publish"], channel="xch")
    assert manifest_mod.validate_manifest(m2) is None
    # 声明渠道但前缀不符：仍拒绝
    m3 = dict(base, permissions=["other_publish"], channel="xch")
    assert manifest_mod.validate_manifest(m3) is not None
