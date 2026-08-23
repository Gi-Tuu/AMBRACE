"""AI 能力权限纯逻辑测试：scope 映射 / 常量完整性（DB 相关走接口冒烟）。"""

from app.services import permission_service


def test_plugin_scope_映射():
    assert permission_service._plugin_scope("browser") == permission_service.SCOPE_BROWSER
    assert permission_service._plugin_scope("BrowserMCP") == permission_service.SCOPE_BROWSER
    assert permission_service._plugin_scope("douyin") == permission_service.SCOPE_DOUYIN
    assert permission_service._plugin_scope("tiktok_publish") == permission_service.SCOPE_DOUYIN
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
