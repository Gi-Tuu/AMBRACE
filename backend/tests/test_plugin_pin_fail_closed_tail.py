# -*- coding: utf-8 -*-
"""audit-tail（2026-10-14）：插件桥 http 连接层 pin-IP 失败改 **fail-closed** 的接线回归。

安全契约（对应 app/application/plugin_bridge_service.py::_http_fetch）
--------------------------------------------------------------------
默认安全档（``plugin_http_allow_private=False``）下，只要「拿不到可钉的 IP」或「钉 IP 传输构造
失败」，就**不许建立普通 httpx 客户端**——普通客户端会在连接期重新解析 DNS，把
``_check_url_allowed()`` 校验的那一刻与真正 TCP 连接的那一刻解耦（DNS-rebinding / TOCTOU），
pin 等于归零。与 :mod:`app.mcp.transport` 的 ``_build_pinned_http_client``（audit13 P3-1）同策略。

唯一的 plain 例外：``plugin_http_allow_private=True``。该档在 ``_check_url_allowed`` 里是
**在任何解析与拦截判定之前**放行的（内网/自托管显式授权），压根不存在「已校验 IP」可绕，
故允许降级普通传输；本文件把这条例外也钉住，防止有人把例外一起改掉、或反过来把它当后门。

覆盖面：正常 public pin / 解析异常 / 解析为空 / 传输构造失败 / 混合私网 / 非法地址 /
显式例外档放行 / 禁止 plain 客户端的真实断言 / 两发精确变异（内存 compile，不落生产工作区）。

全程 DNS 与 httpx 客户端构造打桩，不触网、不连真实站点、不调 LLM；测试数据纯合成。
"""
import asyncio
import inspect
import socket
from types import ModuleType

import httpx
import pytest

from app.application import plugin_bridge_service as pb
from app.config import settings
from app.i18n import tr_lang

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP6 = "2606:2800:220:1:248:1893:25c8:1946"


# ------------------------------------------------------------------ 打桩工具

class _Stop(Exception):
    """哨兵：客户端一被构造就中断，绝不走到网络。"""


class _ClientSpy:
    """替换 ``httpx.Client``：记录每次构造拿到的 transport（None = 普通客户端 = 违宪）。"""

    def __init__(self) -> None:
        self.transports: list = []

    def install(self, monkeypatch) -> "_ClientSpy":
        def _factory(*a, **kw):
            self.transports.append(kw.get("transport", "MISSING"))
            raise _Stop("no network in this test")

        monkeypatch.setattr(httpx, "Client", _factory)
        return self

    @property
    def plain_built(self) -> int:
        """被建立的「不钉 IP」客户端数量（安全档下必须恒为 0）。"""
        return sum(1 for t in self.transports if t is None)

    @property
    def pinned(self) -> list:
        return [t for t in self.transports if isinstance(t, pb._PinnedSyncHTTPTransport)]


def _dns(monkeypatch, mapping: dict[str, list[str]] = None, *,
         raise_hosts=(), empty_hosts=()) -> None:
    """host → ips 的确定性解析桩；未收录的 host 视作「本身就是 IP 字面量」。"""
    mapping = mapping or {}

    def _fake(host, port, *a, **kw):
        h = str(host)
        if h in raise_hosts:
            raise socket.gaierror(-2, "synthetic DNS failure")
        if h in empty_hosts:
            return []
        out = []
        for ip in mapping.get(h, [h]):
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            out.append((fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port)))
        return out

    monkeypatch.setattr(socket, "getaddrinfo", _fake)


def _safe_mode(monkeypatch, *, allow_private: bool = False) -> None:
    monkeypatch.setattr(settings, "plugin_http_allow_private", allow_private, raising=False)
    monkeypatch.setattr(settings, "plugin_http_allow_http", True, raising=False)


def _boom_backend():
    raise RuntimeError("httpcore sync backend layout changed")


# ------------------------------------------------------------------ 1：正向——公网可解析必须钉 IP

def test_public_host_经接线钉住已校验IP(monkeypatch):
    """① 正常 public pin：走 http_proxy 真实接线，客户端拿到钉 IP 传输、且钉的正是校验过的 IP。"""
    _safe_mode(monkeypatch)
    _dns(monkeypatch, {"pinok.public.test": [PUBLIC_IP]})
    spy = _ClientSpy().install(monkeypatch)

    r = asyncio.run(pb.http_proxy({"url": "https://pinok.public.test/x"}, lang="zh"))

    assert spy.transports, "客户端根本没被构造（接线断了，断言会空转）"
    assert spy.plain_built == 0, "安全档下建立了普通客户端"
    assert len(spy.pinned) == 1
    assert spy.pinned[0]._pool._network_backend._pinned_ips == [PUBLIC_IP]
    # _Stop 被 _http_fetch 的兜底 except 收成业务错误；回包结构不变（不新增字段、不改形状）
    assert r["ok"] is False and set(r) == {"ok", "error"}


def test_pinned_backend_连接期只吃IP字面量(monkeypatch):
    """① 连接层真实语义：TCP 目标被改写成已校验 IP，域名不参与连接期解析；双栈按解析顺序回退。"""
    seen: list[tuple] = []

    class _Inner:
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            seen.append((host, port))
            raise OSError("synthetic: stop before real socket")

        def connect_unix_socket(self, *a, **kw):
            raise AssertionError("unix socket not expected")

    monkeypatch.setattr(pb, "_make_sync_network_backend", lambda: _Inner())
    backend = pb._PinnedSyncNetworkBackend([PUBLIC_IP6, PUBLIC_IP])
    with pytest.raises(OSError):
        backend.connect_tcp("pinok.public.test", 443)
    assert seen == [(PUBLIC_IP6, 443), (PUBLIC_IP, 443)], "必须逐个试同一轮解析出的地址，且绝不带域名"


# ------------------------------------------------------------------ 2：拿不到 pin IP → 拒绝，不许 plain

@pytest.mark.parametrize("url,case_kwargs", [
    ("https://flaky.test/x", dict(raise_hosts=("flaky.test",))),    # 解析抛错
    ("https://empty.test/x", dict(empty_hosts=("empty.test",))),    # 解析成功但结果为空
])
def test_解析异常或为空_不建立plain客户端(monkeypatch, url, case_kwargs):
    """② 默认档：解析失败 / 结果为空 → 拒绝连接，且**一个普通客户端都不许建**。"""
    _safe_mode(monkeypatch)
    _dns(monkeypatch, **case_kwargs)
    spy = _ClientSpy().install(monkeypatch)

    r = pb._http_fetch(url, "GET", None, {}, 5.0, 1024)

    assert r["ok"] is False, f"{url}：本应拒绝，实际放行"
    assert spy.transports == [], f"{url}：建立了客户端 {spy.transports}，pin 归零"


@pytest.mark.parametrize("url", ["ftp://nope.test/x", "https:///no-host", "not-a-url"])
def test_非法地址_不建立plain客户端(monkeypatch, url):
    """② 非法地址（协议不支持 / 缺 host）直接进 _http_fetch：同样拒绝，不回退普通客户端。"""
    _safe_mode(monkeypatch)
    _dns(monkeypatch)
    spy = _ClientSpy().install(monkeypatch)

    r = pb._http_fetch(url, "GET", None, {}, 5.0, 1024)

    assert r["ok"] is False, url
    assert spy.transports == [], f"{url} 竟建立了客户端"


def test_传输构造失败_不建立plain客户端(monkeypatch):
    """② 钉 IP 传输构造失败（httpcore 内部路径变动）：安全档拒绝，而不是丢掉已校验 IP 走 plain。"""
    _safe_mode(monkeypatch)
    _dns(monkeypatch, {"buildfail.test": [PUBLIC_IP]})
    monkeypatch.setattr(pb, "_make_sync_network_backend", _boom_backend)
    spy = _ClientSpy().install(monkeypatch)

    r = pb._http_fetch("https://buildfail.test/x", "GET", None, {}, 5.0, 1024)

    assert r["ok"] is False
    assert spy.plain_built == 0, "构造失败仍降级普通传输 → rebinding 窗口重新打开"
    assert spy.transports == []


def test_混合私网解析_拒绝且无plain客户端(monkeypatch):
    """② 混合解析（公网 + 私网）：判 drift 直接拒（沿用 P2-B 语义），且不得偷偷建普通客户端。"""
    _safe_mode(monkeypatch)
    _dns(monkeypatch, {"mixed.test": [PUBLIC_IP, "10.0.0.5"]})
    spy = _ClientSpy().install(monkeypatch)

    r = pb._http_fetch("https://mixed.test/x", "GET", None, {}, 5.0, 1024)

    assert r["ok"] is False
    assert r["error"] == tr_lang("zh", "http_ssrf_blocked")
    assert spy.transports == []


def test_统一入口dispatch同样failclosed(monkeypatch):
    """② 接线：桥的统一入口 ``call``→``http`` 与直接 http 落在同一道闸上（不是只测内部函数）。"""
    _safe_mode(monkeypatch)
    _dns(monkeypatch, empty_hosts=("empty.test",))
    spy = _ClientSpy().install(monkeypatch)

    r = asyncio.run(pb.dispatch("plugin_tail", "call",
                                {"api": "http", "params": {"url": "https://empty.test/x"}},
                                user_id=1, lang="zh"))

    assert r["ok"] is False
    assert spy.transports == [], "统一入口绕过了闸"


# ------------------------------------------------------------------ 3：唯一显式例外（语义原样保留）

@pytest.fixture()
def local_http():
    """本机回环 http 服务（显式授权档专用）：证明 pin 传输真能连通，而不是摆设。"""
    import http.server
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        payload = b'{"pong": 1}'

        def _respond(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)

        do_GET = _respond

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_显式授权档_解析失败仍可plain(local_http, monkeypatch):
    """③ allow_private=True 的例外语义保留：pin 解析失败 → 普通路径，连接期**再解析一次**（这正是
    安全档必须拒绝的行为，这里只作为「已显式授权内网」档的现状钉住，不作放宽）。

    主机名用合成域 ``localhost.tail``，第二次解析由桩直接答回 127.0.0.1，不依赖外部 DNS。
    """
    _safe_mode(monkeypatch, allow_private=True)
    port = local_http.server_address[1]
    seen: list[str] = []

    def _fake(host, p, *a, **kw):
        h = str(host)
        seen.append(h)
        if seen.count("localhost.tail") == 1:   # 第一次（pin 解析）模拟失败
            return []
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", p))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    r = pb._http_fetch(f"http://localhost.tail:{port}/ping", "GET", None, {}, 5.0, 1024)

    assert r["ok"] is True, r
    assert r["data"]["body"] == '{"pong": 1}'
    assert seen.count("localhost.tail") == 2, f"例外档下连接期确实又解析了一次：{seen}"


def test_显式授权档_构造失败仍可plain(local_http, monkeypatch):
    """③ 同一档下传输构造失败也不报错（旧用例 test_http_pin_构造失败回退普通路径 即此档，判据不放宽）。"""
    _safe_mode(monkeypatch, allow_private=True)
    monkeypatch.setattr(pb, "_make_sync_network_backend", _boom_backend)
    port = local_http.server_address[1]

    r = pb._http_fetch(f"http://127.0.0.1:{port}/ping", "GET", None, {}, 5.0, 1024)

    assert r["ok"] is True, r
    assert r["data"]["status"] == 200


def test_显式授权档_能解析时照样钉IP(local_http, monkeypatch):
    """③ 例外档不等于关掉 pin：能解析时仍走钉 IP 传输（连接目标是 IP 字面量，域名只解析一次）。"""
    _safe_mode(monkeypatch, allow_private=True)
    calls: list[str] = []
    port = local_http.server_address[1]

    def _fake(host, p, *a, **kw):
        h = str(host)
        calls.append(h)
        ip = "127.0.0.1" if h == "tail-pin.test" else h
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, p))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    r = pb._http_fetch(f"http://tail-pin.test:{port}/ping", "GET", None, {}, 5.0, 1024)

    assert r["ok"] is True, r
    assert calls.count("tail-pin.test") == 1, calls   # 连接期不再查域名
    assert calls[-1] == "127.0.0.1", calls            # 连的是 IP 字面量


# ------------------------------------------------------------------ 4：两发精确变异（内存 compile）

def _load_mutant(replacement: tuple[str, str]) -> ModuleType:
    """把生产源码在内存里 compile 成变异副本（不写文件、不碰仓库），返回该模块对象。

    变异体只活在本条用例的命名空间里；生产模块字节不变。
    """
    src = inspect.getsource(pb)
    old, new = replacement
    assert src.count(old) == 1, f"变异锚点不唯一/不存在：{old!r}"
    code = compile(src.replace(old, new, 1), f"<mutant:{pb.__name__}>", "exec")
    mod = ModuleType(pb.__name__ + ".__mutant__")
    mod.__file__ = pb.__file__
    mod.__package__ = pb.__package__
    exec(code, mod.__dict__)
    return mod


def test_变异1_摘掉failclosed分支_守卫必须转红(monkeypatch):
    """反例①：把闸的条件改成永假 → 解析为空时**真的**会建普通客户端（证明 §2 的断言不是空转）。"""
    _safe_mode(monkeypatch)
    _dns(monkeypatch, empty_hosts=("empty.test",))
    mutant = _load_mutant((
        "    if transport is None and not _plain_fallback_authorized():",
        "    if False:  # MUTANT1 摘掉 fail-closed",
    ))

    spy = _ClientSpy().install(monkeypatch)
    mutant._http_fetch("https://empty.test/x", "GET", None, {}, 5.0, 1024)
    assert spy.plain_built == 1, "变异体没暴露普通客户端 → 本条变异证明无效"

    prod = _ClientSpy().install(monkeypatch)
    pb._http_fetch("https://empty.test/x", "GET", None, {}, 5.0, 1024)
    assert prod.plain_built == 0


def test_变异2_例外档放大成恒真_守卫必须转红(monkeypatch):
    """反例②：把「唯一 plain 例外」写成恒真 → 默认安全档也降级普通传输（等价于整条 pin 被暗改废除）。"""
    _safe_mode(monkeypatch)  # plugin_http_allow_private=False
    _dns(monkeypatch, {"buildfail.test": [PUBLIC_IP]})
    mutant = _load_mutant((
        '    return bool(getattr(settings, "plugin_http_allow_private", False))',
        "    return True  # MUTANT2 例外政策被暗改",
    ))
    # 变异体有自己的全局命名空间，传输构造失败必须在它那份里同样发生
    monkeypatch.setattr(pb, "_make_sync_network_backend", _boom_backend)
    monkeypatch.setattr(mutant, "_make_sync_network_backend", _boom_backend)

    spy = _ClientSpy().install(monkeypatch)
    mutant._http_fetch("https://buildfail.test/x", "GET", None, {}, 5.0, 1024)
    assert spy.plain_built == 1, "变异体没暴露普通客户端 → 本条变异证明无效"

    prod = _ClientSpy().install(monkeypatch)
    pb._http_fetch("https://buildfail.test/x", "GET", None, {}, 5.0, 1024)
    assert prod.plain_built == 0
