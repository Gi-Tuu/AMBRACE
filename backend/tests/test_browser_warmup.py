# -*- coding: utf-8 -*-
"""Edge 预热状态机测试（plans #39，2026-08-16）：常驻上下文惰性启动/保持/空闲回收/warmup 幂等"""
import asyncio
import sys
import time

from app.plugins import registry


def _load_browser_mod():
    """加载 browser_mcp 插件模块（不启用，仅拿模块引用）"""
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "browser_mcp")
    mod = sys.modules.get("ai_plugin_browser_mcp")
    assert mod is not None
    return mod


class _FakePage:
    def __init__(self):
        self.url = "https://example.com"
        self.closed = False

    def goto(self, *a, **k):
        self.url = a[0]
        return None

    def wait_for_timeout(self, *a, **k):
        return None

    def wait_for_selector(self, *a, **k):
        return None

    def wait_for_function(self, *a, **k):
        return None

    def title(self):
        return "测试页"

    def evaluate(self, expr, *a, **k):
        if "innerText" in expr:
            return "正文内容"
        if "b_algo" in expr:
            return [
                {"title": "测试查询 结果1", "href": "https://example.com/r1", "snippet": "关于测试查询的摘要1"},
                {"title": "测试查询 结果2", "href": "https://example.com/r2", "snippet": "关于测试查询的摘要2"},
                {"title": "测试查询 结果3", "href": "https://example.com/r3", "snippet": "关于测试查询的摘要3"},
                {"title": "测试查询 结果4", "href": "https://example.com/r4", "snippet": "关于测试查询的摘要4"},
                {"title": "测试查询 结果5", "href": "https://example.com/r5", "snippet": "关于测试查询的摘要5"},
            ]
        if "querySelectorAll('img')" in expr:
            return ["https://x/a.png"]
        if "querySelectorAll('a[href]')" in expr:
            return ["https://x/link"]
        return []

    def close(self):
        self.closed = True


class _FakeCtx:
    def __init__(self):
        self.pages = [_FakePage()]
        self.closed = False

    def new_page(self):
        return _FakePage()

    def close(self):
        self.closed = True


class _FakePW:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_warm_ctx_惰性启动并保持(monkeypatch):
    mod = _load_browser_mod()
    mod._reset_warm()
    fake_pw, fake_ctx = _FakePW(), _FakeCtx()
    calls = []

    def fake_launch(headless=True):
        calls.append(headless)
        return fake_pw, fake_ctx

    monkeypatch.setattr(mod, "_launch", fake_launch)
    pw, ctx = mod._ensure_warm_ctx()
    assert ctx is fake_ctx
    assert calls == [True]  # 惰性启动（headless）
    mod._ensure_warm_ctx()
    assert len(calls) == 1  # 再次调用不重新 launch
    assert mod._WARM["ready"] is True
    mod._reset_warm()


def test_warm_空闲超时回收重建(monkeypatch):
    mod = _load_browser_mod()
    mod._reset_warm()
    fake1 = _FakeCtx()
    monkeypatch.setattr(mod, "_launch", lambda headless=True: (_FakePW(), fake1))
    mod._ensure_warm_ctx()
    mod._WARM["last_used"] = time.time() - 700  # 超过 600s 空闲
    fake2 = _FakeCtx()
    monkeypatch.setattr(mod, "_launch", lambda headless=True: (_FakePW(), fake2))
    pw, ctx = mod._ensure_warm_ctx()
    assert ctx is fake2  # 空闲后重建
    assert fake1.closed is True  # 旧常驻被回收
    mod._reset_warm()


def test_idle_timeout_配置化(monkeypatch):
    """warm_idle_minutes 配置生效：0=常驻不回收；正值=按分钟换算"""
    mod = _load_browser_mod()
    # 默认 10 分钟
    monkeypatch.setattr(mod, "sdk", type("SDK", (), {"get_config": staticmethod(lambda: {})})())
    assert mod._idle_timeout_sec() == 600.0
    # 配置 3 分钟
    monkeypatch.setattr(mod, "sdk", type("SDK", (), {"get_config": staticmethod(lambda: {"warm_idle_minutes": 3})})())
    assert mod._idle_timeout_sec() == 180.0
    # 0 = 常驻不回收（inf）
    monkeypatch.setattr(mod, "sdk", type("SDK", (), {"get_config": staticmethod(lambda: {"warm_idle_minutes": 0})})())
    assert mod._idle_timeout_sec() == float("inf")


def test_warm_配置0常驻不回收(monkeypatch):
    mod = _load_browser_mod()
    mod._reset_warm()
    monkeypatch.setattr(mod, "sdk", type("SDK", (), {"get_config": staticmethod(lambda: {"warm_idle_minutes": 0})})())
    fake1 = _FakeCtx()
    monkeypatch.setattr(mod, "_launch", lambda headless=True: (_FakePW(), fake1))
    mod._ensure_warm_ctx()
    mod._WARM["last_used"] = time.time() - 999999  # 极长空闲
    pw, ctx = mod._ensure_warm_ctx()
    assert ctx is fake1  # 配置 0：常驻不回收
    assert fake1.closed is False
    mod._reset_warm()


def test_warmup_幂等(monkeypatch):
    mod = _load_browser_mod()
    mod._reset_warm()
    calls = []
    monkeypatch.setattr(mod, "_launch", lambda headless=True: (calls.append(1), (_FakePW(), _FakeCtx()))[1])
    asyncio.run(mod.warmup())
    assert len(calls) == 1
    asyncio.run(mod.warmup())
    assert len(calls) == 1  # 已就绪不再启动
    mod._reset_warm()


def test_warmup_失败静默(monkeypatch):
    mod = _load_browser_mod()
    mod._reset_warm()

    def boom(headless=True):
        raise RuntimeError("edge 不可用")

    monkeypatch.setattr(mod, "_launch", boom)
    asyncio.run(mod.warmup())  # 不抛
    assert mod._WARM["ctx"] is None
    mod._reset_warm()


def test_sync_browse_复用常驻不关闭(monkeypatch):
    mod = _load_browser_mod()
    mod._reset_warm()
    fake_ctx = _FakeCtx()
    monkeypatch.setattr(mod, "_launch", lambda headless=True: (_FakePW(), fake_ctx))
    mod._ensure_warm_ctx()  # 预热
    r = mod._sync_browse("https://example.com")
    assert r["ok"] is True
    assert r["title"] == "测试页"
    assert fake_ctx.closed is False  # 常驻上下文保持打开
    mod._reset_warm()


def test_sync_search_复用常驻(monkeypatch):
    mod = _load_browser_mod()
    mod._reset_warm()
    fake_ctx = _FakeCtx()
    monkeypatch.setattr(mod, "_launch", lambda headless=True: (_FakePW(), fake_ctx))
    r = mod._sync_search("测试查询")
    assert r["ok"] is True
    assert r.get("results"), "应返回结构化结果"
    assert fake_ctx.closed is False
    mod._reset_warm()
