# -*- coding: utf-8 -*-
"""PWA 同源静态入口（/pwa/）纯静态路由守卫（M1a-1，2026-10-16）。

**全部在 tmp_path 合成静态树上做**，目录外放哨兵文件；用真实 ASGI/Starlette 路由跑请求
（不是搜源码字符串）。钉住的行为：

- 默认未配置 ⇒ 不挂载（即使 root/pwa/build/web 存在也**不**自动启用）；目录不存在 / 缺
  `index.html` 各自给出诊断串，而不是默默挂上或 200 返回"加载中"。
- 有效目录：`/pwa/` 出 index.html；相对资源可取；`/pwa`(无尾斜杠) 重定向 `/pwa/`。
- 缺资源 404；`../`、`%2e%2e`、越界软链接一律 404，**目录外哨兵不可达**。
- 入口/sw/manifest 带 `Cache-Control: no-store`，普通静态资源不带。
- `/api/v1/...` 不被吞（真实路由照常、未知 API 走 404 而不是回 index）；根路径不被接管。
"""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.system import router as system_router
from app.pwa_static import (
    PWA_MOUNT_PATH,
    prepare_pwa_static,
    resolve_pwa_static_dir,
)
from app.utils.errors import register_exception_handlers


def _make_build(root, *, with_index: bool = True) -> str:
    """在 tmp_path 造一个最小「Web build 目录」：入口 + 普通资源 + sw/manifest。"""
    build = root / "web"
    (build / "assets").mkdir(parents=True)
    (build / "assets" / "main.dart.js").write_text("// app js\n", encoding="utf-8")
    (build / "sw.js").write_text("// sw\n", encoding="utf-8")
    (build / "manifest.json").write_text('{"start_url": "."}\n', encoding="utf-8")
    for name in ("main.dart.js", "flutter_bootstrap.js", "offline.html"):
        (build / name).write_bytes(b"synthetic public build fixture")
    if with_index:
        (build / "index.html").write_text("<html>shell</html>\n", encoding="utf-8")
    return str(build)


def _build_app(pwa_dir: str | None, project_root) -> FastAPI:
    """最小真实壳：真实错误体 + 真实 /api/v1 路由 + 根路由 + （按需）/pwa/ 静态挂载。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(system_router)

    @app.get("/")
    async def root():
        return {"message": "AMBRACE Server is running"}

    if pwa_dir is not None:
        static_app, detail = prepare_pwa_static(pwa_dir, project_root)
        assert static_app is not None, f"准备失败：{detail}"
        app.mount(PWA_MOUNT_PATH, static_app, name="pwa")
    return app


def _raw_get(app, path: str) -> tuple[int, bytes]:
    """直接走 ASGI（绕过 httpx 的 URL 归一化），用于量 `..` / `%2e%2e` 这类原始路径。"""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    captured: dict = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return captured["status"], captured["body"]


# ── 三态诊断：默认关 / 目录缺失 / 缺入口 ────────────────────────────────

def test_默认未配置不挂载_即使本机build存在(tmp_path):
    # 造一个「本机已有 pwa/build/web」的假现场：默认配置仍不得自动启用
    (tmp_path / "pwa" / "build" / "web").mkdir(parents=True)
    (tmp_path / "pwa" / "build" / "web" / "index.html").write_text("x", encoding="utf-8")

    static_app, detail = prepare_pwa_static("", tmp_path)
    assert static_app is None
    assert detail == "not_configured", detail

    client = TestClient(_build_app(None, tmp_path))
    assert client.get("/pwa/").status_code == 404, "默认未配置却挂上了 PWA 入口"


def test_目录不存在给出诊断(tmp_path):
    missing = tmp_path / "not_built"
    static_app, detail = prepare_pwa_static(str(missing), tmp_path)
    assert static_app is None
    assert detail.startswith("directory_missing")
    assert not missing.exists(), "不得自动创建构建目录"


def test_缺index给出诊断且不挂载(tmp_path):
    build = _make_build(tmp_path, with_index=False)
    static_app, detail = prepare_pwa_static(build, tmp_path)
    assert static_app is None
    assert detail.startswith("entry_missing"), detail

    client = TestClient(_build_app(None, tmp_path))
    assert client.get("/pwa/").status_code == 404


def test_相对路径按项目根解析(tmp_path):
    assert resolve_pwa_static_dir("  ", tmp_path) == ""
    assert resolve_pwa_static_dir("pwa/build/web", tmp_path) == str(tmp_path / "pwa" / "build" / "web")


# ── 有效目录：入口 / 资源 / 尾斜杠 / 404 ────────────────────────────────

def test_有效目录的入口与相对资源(tmp_path):
    build = _make_build(tmp_path)
    client = TestClient(_build_app(build, tmp_path))

    r = client.get("/pwa/")
    assert r.status_code == 200
    assert "shell" in r.text
    assert r.headers.get("cache-control") == "no-store", "入口未禁缓存，陈旧缓存会挡住升级"

    assert client.get("/pwa/assets/main.dart.js").status_code == 200
    assert client.get("/pwa/sw.js").status_code == 200
    assert client.get("/pwa/sw.js").headers.get("cache-control") == "no-store"
    assert client.get("/pwa/manifest.json").headers.get("cache-control") == "no-store"


def test_普通资源不被打上no_store(tmp_path):
    build = _make_build(tmp_path)
    client = TestClient(_build_app(build, tmp_path))
    cc = (client.get("/pwa/assets/main.dart.js").headers.get("cache-control") or "").lower()
    assert "no-store" not in cc


def test_无尾斜杠重定向到尾斜杠(tmp_path):
    build = _make_build(tmp_path)
    client = TestClient(_build_app(build, tmp_path))
    r = client.get("/pwa", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308), f"未重定向：{r.status_code}"
    assert r.headers["location"].rstrip("/").endswith("/pwa"), r.headers.get("location")


def test_不存在的资源必须明确404(tmp_path):
    build = _make_build(tmp_path)
    client = TestClient(_build_app(build, tmp_path))
    r = client.get("/pwa/does_not_exist.js")
    assert r.status_code == 404
    assert "shell" not in r.text, "缺资源回落到了 index（不应有全站 fallback）"


# ── 穿越 / 越界软链接 / 哨兵不可达 ──────────────────────────────────────

def test_路径穿越不可达_目录外哨兵(tmp_path):
    sentinel = tmp_path / "SENTINEL-OUTSIDE.txt"
    sentinel.write_text("TOP-SECRET", encoding="utf-8")
    build = _make_build(tmp_path)
    app = _build_app(build, tmp_path)

    for raw in ("/pwa/../SENTINEL-OUTSIDE.txt", "/pwa/%2e%2e/SENTINEL-OUTSIDE.txt",
                "/pwa/....//SENTINEL-OUTSIDE.txt", "/pwa/..%2fSENTINEL-OUTSIDE.txt"):
        status, body = _raw_get(app, raw)
        assert status == 404, f"{raw} 未拒：{status}"
        assert b"TOP-SECRET" not in body, f"{raw} 读到了目录外哨兵"


def test_越界软链接不可达(tmp_path):
    outside = tmp_path / "OUTSIDE-target"
    outside.mkdir()
    (outside / "secret.txt").write_text("LINK-SECRET", encoding="utf-8")
    build = _make_build(tmp_path)
    link = os.path.join(build, "escape")
    try:
        os.symlink(str(outside), link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # 环境不支持建软链 → 如实登记
        pytest.skip(f"本环境不支持创建目录软链接，越界软链用例未执行：{exc}")

    app = _build_app(build, tmp_path)
    status, body = _raw_get(app, "/pwa/escape/secret.txt")
    assert status == 404, f"越界软链未被拒：{status}"
    assert b"LINK-SECRET" not in body


# ── API / 根路由不被吞 ─────────────────────────────────────────────────

def test_静态入口不吞API与根路由(tmp_path):
    build = _make_build(tmp_path)
    client = TestClient(_build_app(build, tmp_path))

    r = client.get("/api/v1/system/health")
    assert r.status_code == 200, "PWA 静态入口把真实 API 吞了"
    assert r.headers.get("content-type", "").startswith("application/json")

    # 未知 API 必须按 API 语义 404（而不是回落 index.html）
    r2 = client.get("/api/v1/definitely-not-a-route")
    assert r2.status_code == 404
    assert "shell" not in r2.text

    root = client.get("/")
    assert root.status_code == 200
    assert "AMBRACE Server is running" in root.text, "静态入口接管了根路径"


def test_只有index的普通目录不能当作完成构建(tmp_path):
    build = tmp_path / "ordinary"
    build.mkdir()
    (build / "index.html").write_bytes(b"ordinary index")
    app, detail = prepare_pwa_static(str(build), tmp_path)
    assert app is None
    assert detail.startswith("build_incomplete")


def test_有效build也不公开隐藏配置源码数据库和工作文件(tmp_path):
    from pathlib import Path
    build = Path(_make_build(tmp_path))
    forbidden = (".env", "server_config.json", "app.py", "database.db", "REPORT.md", "assets/.env", "assets/code.dart", "assets/database.sqlite")
    for rel in forbidden:
        path = build / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"SYNTHETIC_PRIVATE_SENTINEL")
    client = TestClient(_build_app(str(build), tmp_path))
    assert client.get("/pwa/").status_code == 200
    for rel in forbidden:
        response = client.get("/pwa/" + rel)
        assert response.status_code == 404, rel
        assert b"SYNTHETIC_PRIVATE_SENTINEL" not in response.content


def test_目录内软链接别名不能绕过私有文件拒绝(tmp_path):
    from pathlib import Path
    build = Path(_make_build(tmp_path))
    private = build / ".env"
    private.write_bytes(b"SYNTHETIC_PRIVATE_SENTINEL")
    alias = build / "assets" / "allowed_name.txt"
    try:
        alias.symlink_to(private)
    except (OSError, NotImplementedError):
        pytest.skip("platform cannot create symlinks")
    response = TestClient(_build_app(str(build), tmp_path)).get("/pwa/assets/allowed_name.txt")
    assert response.status_code == 404
    assert b"SYNTHETIC_PRIVATE_SENTINEL" not in response.content
