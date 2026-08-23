"""插件市场纯逻辑测试：市场扫描 / 字段完整性 / 安装复制（临时目录）/ 权限。"""

import shutil

from app.plugins import registry
from app.api.marketplace import _find_item, _is_owner, _scan_market_items


def test_market_扫描包含全部示例():
    items = _scan_market_items()
    names = {it["name"] for it in items}
    assert "weather_brief" in names
    assert "plugin_demo" in names
    assert "good_night_topic" in names
    assert "http_echo" in names
    assert "browser_mcp" in names
    assert len(items) >= 6
    for it in items:
        for k in ("name", "version", "description", "category", "hooks", "permissions", "config", "usage", "source"):
            assert k in it, f"{it.get('name')} 缺字段 {k}"


def test_market_字段类型():
    for it in _scan_market_items():
        assert it["category"] in ("plugin", "mcp")
        assert isinstance(it["hooks"], list)
        assert isinstance(it["permissions"], list)
        assert it["source"] == "builtin"


def test_market_安装复制到临时目录(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "USER_DIR", tmp_path / "plugins")
    item = _find_item("http_echo")
    assert item is not None
    src = registry.EXAMPLE_DIR / item["name"]
    assert src.is_dir()
    target = registry.USER_DIR / item["name"]
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    loaded = registry.load_plugin_dir(target)
    assert loaded is not None
    assert loaded["name"] == "http_echo"


def test_market_权限():
    assert _is_owner(1)
    assert not _is_owner(4)
    assert not _is_owner(0)
