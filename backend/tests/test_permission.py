"""AI 能力权限纯逻辑测试：scope 映射 / 常量完整性（DB 相关走接口冒烟）。"""

from app.providers import channel as channels
from app.providers import registry as prov_reg
from app.services import permission_service


class _FakePort:
    pass


def test_plugin_scope_映射():
    assert permission_service._plugin_scope("browser") == permission_service.SCOPE_BROWSER
    assert permission_service._plugin_scope("BrowserMCP") == permission_service.SCOPE_BROWSER
    # X5：渠道 scope 经注册表 meta 上报（注册 → 命中；清理 → 回退 extension）
    try:
        channels.register_channel("fakech", _FakePort(),
                                  meta={"plugin": "fake_douyin_like", "scope": "douyin"},
                                  source="fake_douyin_like")
        assert permission_service._plugin_scope("fake_douyin_like") == "douyin"
    finally:
        prov_reg.unregister_providers_for_source("fake_douyin_like")
    assert permission_service._plugin_scope("weather") == permission_service.SCOPE_EXTENSION
    assert permission_service._plugin_scope("") == permission_service.SCOPE_EXTENSION


def test_scopes_常量完整():
    for s in permission_service.SCOPES:
        assert s in permission_service.SCOPE_LABELS
        assert s in permission_service.SCOPE_DESCRIPTIONS
    assert permission_service.SCOPE_GLOBAL not in permission_service.SCOPES


def test_levels_合法值():
    assert set(permission_service.LEVELS) == {"allow", "ask", "forbid"}
    for lv in permission_service.LEVELS:
        assert lv in permission_service.LEVEL_LABELS


def test_默认全局档位():
    assert permission_service.DEFAULT_GLOBAL_LEVEL == "allow"
