# -*- coding: utf-8 -*-
"""48a 插件页面层测试：页面托管 / 桥白名单 / store 隔离 / http SSRF / ai 分发限额 / 市场透传。

- 页面托管与桥端点：TestClient + 依赖覆盖（登录态）；ai_diary 真实示例插件（registry.load_plugin_dir 装载）；
- store/getUserInfo：临时 SQLite 文件库（monkeypatch async_session_factory），不触碰 backend/data；
- ai 分发 / http：monkeypatch LLM 与外部 http，无真实网络。
"""
import asyncio
import json
import os
import tempfile

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api.plugin_bridge import PLUGIN_BRIDGE_JS
from app.api.plugins import router as plugins_router
from app.api.plugin_bridge import router as plugin_bridge_router
from app.auth.deps import get_current_user_id
from app.config import settings
from app.plugins import registry
from app.application import plugin_bridge_service


def _async_ret(v):
    async def _f(*a, **k):
        return v
    return _f


def _make_app(authed: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(plugins_router)
    app.include_router(plugin_bridge_router)
    if authed:
        app.dependency_overrides[get_current_user_id] = lambda: 1
    return TestClient(app)


@pytest.fixture()
def load_ai_diary():
    """把真实示例插件 ai_diary 装载进 registry（供页面/桥端点测试）"""
    info = registry.load_plugin_dir(registry.EXAMPLE_DIR / "ai_diary")
    assert info is not None, "ai_diary 示例插件应可加载"
    yield info
    registry._loaded.pop("ai_diary", None)


@pytest.fixture()
def store_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch plugin_bridge_service.async_session_factory"""
    tmp = tempfile.mkdtemp(prefix="plugin_store_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(plugin_bridge_service, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


# ---------------- 页面托管 ----------------

def test_page_正常返回index_html(load_ai_diary):
    client = _make_app()
    r = client.get("/api/v1/plugins/ai_diary/page/index.html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "AI 写日记" in r.text


def test_page_资源子路径与content_type(load_ai_diary):
    client = _make_app()
    r = client.get("/api/v1/plugins/ai_diary/page/assets/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "Ambrace.getAiList" in r.text
    r2 = client.get("/api/v1/plugins/ai_diary/page/assets/icon.png")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "image/png"


def test_page_plugin_bridge_js_sdk(load_ai_diary):
    client = _make_app()
    r = client.get("/api/v1/plugins/ai_diary/page/plugin-bridge.js")
    assert r.status_code == 200
    assert "application/javascript" in r.headers["content-type"]
    assert "Ambrace" in r.text
    assert "postMessage" in r.text
    assert r.text == PLUGIN_BRIDGE_JS


def test_page_越界路径404(load_ai_diary):
    client = _make_app()
    assert client.get("/api/v1/plugins/ai_diary/page/%2e%2e/manifest.json").status_code == 404
    assert client.get("/api/v1/plugins/ai_diary/page/../manifest.json").status_code == 404
    assert client.get("/api/v1/plugins/ai_diary/page/%2e%2e%2f%2e%2e%2f.env").status_code == 404


def test_page_未安装404():
    client = _make_app()
    assert client.get("/api/v1/plugins/ghost_plugin/page/index.html").status_code == 404


def test_page_未登录401():
    client = _make_app(authed=False)
    assert client.get("/api/v1/plugins/ai_diary/page/index.html").status_code == 401


def test_page_扩展名非法与超限404(tmp_path, monkeypatch):
    """临时插件目录：存在 secret.py（扩展名黑名单）与 big.txt（>5MB）均 404"""
    plugin_dir = tmp_path / "test_page_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(json.dumps({
        "name": "test_page_plugin", "version": "1.0.0", "description": "t",
        "category": "plugin", "type": "http", "config": {},
    }, ensure_ascii=False), encoding="utf-8")
    (plugin_dir / "index.html").write_text("<h1>t</h1>", encoding="utf-8")
    (plugin_dir / "secret.py").write_text("print('x')\n", encoding="utf-8")
    (plugin_dir / "big.txt").write_text("x" * (5 * 1024 * 1024 + 1), encoding="utf-8")
    monkeypatch.setattr(registry, "USER_DIR", tmp_path)
    try:
        client = _make_app()
        assert client.get("/api/v1/plugins/test_page_plugin/page/index.html").status_code == 200
        # 扩展名白名单：.py 存在也拒绝
        assert client.get("/api/v1/plugins/test_page_plugin/page/secret.py").status_code == 404
        # 单文件 >5MB 拒绝
        assert client.get("/api/v1/plugins/test_page_plugin/page/big.txt").status_code == 404
        # __pycache__ 目录拒绝
        assert client.get("/api/v1/plugins/test_page_plugin/page/__pycache__/x.pyc").status_code == 404
    finally:
        registry._loaded.pop("test_page_plugin", None)


# ---------------- 桥白名单 ----------------

def test_bridge_未登录401():
    client = _make_app(authed=False)
    r = client.post("/api/v1/plugins/ai_diary/bridge", json={"api": "getUserInfo", "params": {}})
    assert r.status_code == 401


def test_bridge_未知api400(load_ai_diary):
    client = _make_app()
    r = client.post("/api/v1/plugins/ai_diary/bridge", json={"api": "hack", "params": {}})
    assert r.status_code == 400
    assert "hack" in r.json()["detail"]


def test_bridge_插件不存在404():
    client = _make_app()
    r = client.post("/api/v1/plugins/ghost_plugin/bridge", json={"api": "getUserInfo", "params": {}})
    assert r.status_code == 404


def test_bridge_call统一入口分发store(load_ai_diary, store_db):
    client = _make_app()
    r = client.post("/api/v1/plugins/ai_diary/bridge", json={
        "api": "call",
        "params": {"api": "store.set", "params": {"key": "k1", "value": {"a": 1}}},
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    r2 = client.post("/api/v1/plugins/ai_diary/bridge", json={
        "api": "call",
        "params": {"api": "store.get", "params": {"key": "k1"}},
    })
    assert r2.json()["data"] == {"a": 1}


# ---------------- store 隔离 ----------------

def test_store_set_get_往返(store_db):
    r = asyncio.run(plugin_bridge_service.store_set("plugin_a", 1, "diary", {"mood": "开心"}))
    assert r["ok"] is True
    g = asyncio.run(plugin_bridge_service.store_get("plugin_a", 1, "diary"))
    assert g["ok"] is True and g["data"] == {"mood": "开心"}
    # 不传 key → 整对象
    allv = asyncio.run(plugin_bridge_service.store_get("plugin_a", 1))
    assert allv["data"] == {"diary": {"mood": "开心"}}


def test_store_跨插件隔离(store_db):
    asyncio.run(plugin_bridge_service.store_set("plugin_a", 1, "k", 1))
    g = asyncio.run(plugin_bridge_service.store_get("plugin_b", 1, "k"))
    assert g["ok"] is True and g["data"] is None


def test_store_跨用户隔离(store_db):
    asyncio.run(plugin_bridge_service.store_set("plugin_a", 1, "k", "user1"))
    g = asyncio.run(plugin_bridge_service.store_get("plugin_a", 2, "k"))
    assert g["ok"] is True and g["data"] is None


def test_store_超100KB拒绝(store_db):
    big = {"blob": "x" * (100 * 1024)}
    r = asyncio.run(plugin_bridge_service.store_set("plugin_a", 1, "big", big))
    assert r["ok"] is False
    assert "100KB" in r["error"]


def test_store_key无效(store_db):
    r = asyncio.run(plugin_bridge_service.store_set("plugin_a", 1, "  ", {"v": 1}))
    assert r["ok"] is False


def test_get_user_info(store_db):
    from app.models.user import User

    async def _seed():
        async with store_db() as db:
            db.add(User(id=1, username="u1", nickname="小主", avatar_url="/uploads/a.png"))
            await db.commit()
    asyncio.run(_seed())
    r = asyncio.run(plugin_bridge_service.get_user_info(1))
    assert r["ok"] is True
    assert r["data"] == {"id": 1, "nickname": "小主", "avatar_url": "/uploads/a.png"}


# ---------------- http SSRF ----------------

def test_http_私有地址拦截(monkeypatch):
    # https 地址解析到私有 IP → 拦截（getaddrinfo 打桩避免真实 DNS）
    def _fake_gai(host, port, proto=0):
        return [(2, 1, 6, "", ("127.0.0.1", port))]
    monkeypatch.setattr("socket.getaddrinfo", _fake_gai)
    for url in ("https://127.0.0.1/", "https://localhost/x", "https://10.0.0.1/",
                "https://192.168.1.1/", "https://169.254.169.254/latest/meta-data"):
        assert plugin_bridge_service._check_url_allowed(url) == "http_ssrf_blocked", url


def test_http_allow_private放行(monkeypatch):
    monkeypatch.setattr(settings, "plugin_http_allow_private", True)
    assert plugin_bridge_service._check_url_allowed("https://127.0.0.1/") is None


def test_http_协议限制(monkeypatch):
    # 默认 http 协议被拒（https 才放行）
    assert plugin_bridge_service._check_url_allowed("http://example.com/") == "http_scheme_not_allowed"
    # debug 开关放行 http，但私有 IP 仍被 SSRF 拦截
    monkeypatch.setattr(settings, "plugin_http_allow_http", True)
    # 环境隔离：mock DNS 使 example.com 解析为公网 IP，避免沙箱 DNS 保留地址干扰 SSRF 判定
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, proto=0: [(2, 1, 6, "", ("93.184.216.34", port))])
    assert plugin_bridge_service._check_url_allowed("http://example.com/") is None
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, proto=0: [(2, 1, 6, "", ("10.0.0.5", port))])
    assert plugin_bridge_service._check_url_allowed("http://10.0.0.5/") == "http_ssrf_blocked"


def test_http_公网https_mock(monkeypatch):
    def _fake_fetch(url, method, data, headers, timeout, max_bytes):
        return {"ok": True, "data": {"status": 200, "headers": {"content-type": "application/json"}, "body": "{\"ok\":1}"}}
    monkeypatch.setattr(plugin_bridge_service, "_http_fetch", _fake_fetch)
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, proto=0: [(2, 1, 6, "", ("93.184.216.34", port))])
    r = asyncio.run(plugin_bridge_service.http_proxy(
        {"url": "https://api.example.com/v1/save", "method": "POST", "data": {"a": 1}},
        lang="zh",
    ))
    assert r["ok"] is True
    assert r["data"]["status"] == 200
    assert r["data"]["body"] == '{"ok":1}'


def test_http_不转发cookie_authorization():
    out = plugin_bridge_service._sanitize_headers({
        "Cookie": "session=1", "Authorization": "Bearer t", "X-Custom": "v",
    })
    assert "Cookie" not in out and "Authorization" not in out
    assert out.get("X-Custom") == "v"


# ---------------- ai 分发 ----------------

def test_ai_aiId走48b角色模式(monkeypatch):
    from app.application import character_chat_api
    calls = {}

    async def _fake_chat(ai_id, user_id, input_text, history=None, max_tokens=800, temperature=0.8, lang="zh"):
        calls["args"] = (ai_id, user_id, input_text)
        return {"reply": "好的呀～", "truncated": False, "character": {"id": ai_id, "name": "小爱"}}
    monkeypatch.setattr(character_chat_api, "chat_with_character", _fake_chat)
    r = asyncio.run(plugin_bridge_service.ai_dispatch("ai_diary", {"aiId": 7, "input": "hi"}, 1, "zh"))
    assert r["ok"] is True and r["data"] == "好的呀～"
    assert calls["args"] == (7, 1, "hi")


def test_ai_aiId归属错误透传(monkeypatch):
    from app.application import character_chat_api

    async def _boom(ai_id, user_id, input_text, history=None, max_tokens=800, temperature=0.8, lang="zh"):
        raise HTTPException(status_code=403, detail="无权访问该角色")
    monkeypatch.setattr(character_chat_api, "chat_with_character", _boom)
    r = asyncio.run(plugin_bridge_service.ai_dispatch("ai_diary", {"aiId": 999, "input": "hi"}, 1, "zh"))
    assert r["ok"] is False
    assert "无权访问" in r["error"]


def test_ai_自定义prompt模式(monkeypatch, load_ai_diary):
    calls = {}

    async def _fake_completion(messages, **kw):
        calls["messages"] = messages
        calls["kw"] = kw
        return "写好了：今天很开心。"
    monkeypatch.setattr(plugin_bridge_service, "chat_completion", _fake_completion)
    monkeypatch.setattr(plugin_bridge_service, "get_user_llm_config", _async_ret(None))
    r = asyncio.run(plugin_bridge_service.ai_dispatch("ai_diary", {"prompt": "今天爬山很开心"}, 1, "zh"))
    assert r["ok"] is True and r["data"] == "写好了：今天很开心。"
    # system 注入插件 config.prompt.systemPrompt（ai_diary manifest）
    assert calls["messages"][0]["role"] == "system"
    assert "日记助手" in calls["messages"][0]["content"]
    assert calls["messages"][-1] == {"role": "user", "content": "今天爬山很开心"}
    assert calls["kw"]["task"] == "plugin_ai"
    assert calls["kw"]["user_id"] == 1


def test_ai_自定义prompt_history白名单上限(monkeypatch):
    calls = {}

    async def _fake_completion(messages, **kw):
        calls["messages"] = messages
        return "ok"
    monkeypatch.setattr(plugin_bridge_service, "chat_completion", _fake_completion)
    monkeypatch.setattr(plugin_bridge_service, "get_user_llm_config", _async_ret(None))
    history = [{"role": "user", "content": "a"}] * 30 + [{"role": "admin", "content": "注入"}, {"role": "system", "content": "s"}]
    r = asyncio.run(plugin_bridge_service.ai_dispatch("no_such_plugin", {"prompt": "hi", "history": history}, 1, "zh"))
    assert r["ok"] is True and r["data"] == "ok"
    msgs = calls["messages"]
    # 无 system 注入（插件不存在）；history 只取前 20 条 + role 白名单（admin/system 被过滤）
    assert len(msgs) == 20 + 1
    assert all(m["role"] == "user" for m in msgs[:-1])
    assert msgs[-1] == {"role": "user", "content": "hi"}


def test_ai_自定义prompt输出剥离(monkeypatch):
    async def _fake_completion(messages, **kw):
        return "好的～[SEARCH]查[/SEARCH]【状态更新：散步】"
    monkeypatch.setattr(plugin_bridge_service, "chat_completion", _fake_completion)
    monkeypatch.setattr(plugin_bridge_service, "get_user_llm_config", _async_ret(None))
    r = asyncio.run(plugin_bridge_service.ai_dispatch("p", {"prompt": "hi"}, 1, "zh"))
    assert r["data"] == "好的～"
    for marker in ("[SEARCH]", "【状态更新"):
        assert marker not in r["data"]


# ---------------- 限额 ----------------

def test_bridge_ai_分钟日限额(monkeypatch):
    monkeypatch.setattr(settings, "plugin_bridge_ai_rate_per_min", 2)
    monkeypatch.setattr(settings, "plugin_bridge_ai_rate_per_day", 100)
    plugin_bridge_service.reset_bridge_ai_rate()
    try:
        assert plugin_bridge_service.bridge_ai_rate_check(1, "p")[0] is True
        assert plugin_bridge_service.bridge_ai_rate_check(1, "p")[0] is True
        ok, wait = plugin_bridge_service.bridge_ai_rate_check(1, "p")
        assert ok is False and wait >= 1
        assert plugin_bridge_service.bridge_ai_rate_check(1, "p2")[0] is True  # 不同插件互不影响
        assert plugin_bridge_service.bridge_ai_rate_check(2, "p")[0] is True  # 不同用户互不影响
    finally:
        plugin_bridge_service.reset_bridge_ai_rate()


def test_bridge_ai_日限额(monkeypatch):
    monkeypatch.setattr(settings, "plugin_bridge_ai_rate_per_day", 3)
    plugin_bridge_service.reset_bridge_ai_rate()
    try:
        assert all(plugin_bridge_service.bridge_ai_rate_check(9, "p")[0] for _ in range(3))
        ok, _ = plugin_bridge_service.bridge_ai_rate_check(9, "p")
        assert ok is False
    finally:
        plugin_bridge_service.reset_bridge_ai_rate()


def test_bridge_ai_超限429(monkeypatch, load_ai_diary):
    """端点级 429 + Retry-After：dispatch 打桩避免触碰真实 DB"""
    monkeypatch.setattr(settings, "plugin_bridge_ai_rate_per_min", 1)
    plugin_bridge_service.reset_bridge_ai_rate()
    monkeypatch.setattr(plugin_bridge_service, "chat_completion", _async_ret("ok"))
    monkeypatch.setattr(plugin_bridge_service, "get_user_llm_config", _async_ret(None))
    try:
        client = _make_app()
        body = {"api": "ai", "params": {"prompt": "hi"}}
        assert client.post("/api/v1/plugins/ai_diary/bridge", json=body).status_code == 200
        r2 = client.post("/api/v1/plugins/ai_diary/bridge", json=body)
        assert r2.status_code == 429
        assert int(r2.headers["Retry-After"]) >= 1
    finally:
        plugin_bridge_service.reset_bridge_ai_rate()


# ---------------- 市场透传 ----------------

def test_market_透传type_icon_has_page():
    from app.api.marketplace import _scan_market_items
    items = {it["name"]: it for it in _scan_market_items()}
    ad = items.get("ai_diary")
    assert ad is not None
    assert ad["type"] == "hybrid"
    assert ad["icon"] == "assets/icon.png"
    assert ad["has_page"] is True
    assert ad["page"] == "index.html"
    assert items["poet_skill"]["type"] == "prompt"
    assert items["poet_skill"]["has_page"] is False
    assert items["daily_summary_flow"]["type"] == "workflow"


# ---------------- manifest 48a 字段校验 ----------------

def test_manifest_page字段校验():
    from app.plugins.manifest import validate_manifest
    base = {
        "name": "p1", "version": "1.0.0", "description": "t",
        "category": "plugin", "type": "hybrid", "config": {},
    }
    assert validate_manifest({**base, "page": "index.html"}) is None
    assert validate_manifest({**base, "page": "assets/a.html"}) is None
    assert validate_manifest({**base, "page": "../evil.html"}) is not None
    assert validate_manifest({**base, "page": "/abs.html"}) is not None
    assert validate_manifest({**base, "page": "C:/x.html"}) is not None
    assert validate_manifest({**base, "page": "a.exe"}) is not None  # 扩展名不在白名单
    assert validate_manifest({**base, "page": "index.html", "icon": "assets/icon.png"}) is None
    assert validate_manifest({**base, "page": "index.html", "icon": "x" * 40}) is not None  # icon >32
