"""插件市场纯逻辑测试：市场扫描 / 字段完整性 / 安装复制（临时目录）/ 权限。"""

import asyncio
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


def test_market_权限(monkeypatch):
    # A2 M2（2026-09-20）：_is_owner 口径由 is_admin_user 收口到 is_server_admin，
    # 本用例改为打桩新口径（不依赖会话库 users 行的 is_admin/server_admin 分布）。
    async def _fake_is_server_admin(user_id: int) -> bool:
        return user_id == 1
    monkeypatch.setattr('app.application.permission_service.is_server_admin', _fake_is_server_admin)
    assert asyncio.run(_is_owner(1))
    assert not asyncio.run(_is_owner(4))
    assert not asyncio.run(_is_owner(0))


def test_market_installed标记按flag分别断言(monkeypatch):
    """A2 M3（2026-09-20）：市场 installed 标记随可见集重算，按 flag 分别断言。

    - flag 关（默认）：全量口径，别家装的插件也显示「已安装」（既有旧断言语义）；
    - flag 开：别的家庭安装的插件对本账号显示「未安装」，本家庭装机仍为「已安装」。
    只注入内存缓存，不加载真实插件、不碰库。
    """
    from app.agent.loop import AGENT_FLAGS
    from app.api.marketplace import _merge_installed

    def _info(name):
        return {"name": name, "version": "0.0.1", "description": "", "author": "",
                "category": "plugin", "type": "http", "icon": "", "page": "",
                "has_page": False, "hooks": [], "permissions": [], "config": {},
                "usage": "", "display_name": "", "hook_timeout": None,
                "context_keys": [], "content": {}, "path": ""}

    names = ("mine_local", "other_local")
    monkeypatch.setattr(registry, "_loaded",
                        {n: {"info": _info(n), "module": None, "hooks": {},
                             "actions": {}, "router": None} for n in names})
    monkeypatch.setattr(registry, "_enabled", {n: True for n in names})
    monkeypatch.setattr(registry, "_db_config", {})
    monkeypatch.setattr(registry, "_db_prov", {
        "mine_local": {"source": "local", "owner_user_id": 1, "owner_tenant_id": 1},
        "other_local": {"source": "local", "owner_user_id": 2, "owner_tenant_id": 2},
    })
    items = [{"name": "mine_local", "has_page": False},
             {"name": "other_local", "has_page": False}]

    # flag 关：旧断言（全量已安装，与 viewer 无关）
    monkeypatch.setitem(AGENT_FLAGS, "plugin_user_scope", False)
    out = {i["name"]: i["installed"] for i in
           _merge_installed(items, viewer_user_id=1, viewer_tenant_id=1)}
    assert out == {"mine_local": True, "other_local": True}
    assert {i["name"]: i["installed"] for i in _merge_installed(items)} == {
        "mine_local": True, "other_local": True}

    # flag 开：installed 随可见集重算
    monkeypatch.setitem(AGENT_FLAGS, "plugin_user_scope", True)
    out = {i["name"]: i["installed"] for i in
           _merge_installed(items, viewer_user_id=1, viewer_tenant_id=1)}
    assert out == {"mine_local": True, "other_local": False}
    out2 = {i["name"]: i["installed"] for i in
            _merge_installed(items, viewer_user_id=2, viewer_tenant_id=2)}
    assert out2 == {"mine_local": False, "other_local": True}
