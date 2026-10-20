# -*- coding: utf-8 -*-
"""严格 CORS 预检守卫：`Idempotency-Key` 必须在**产品真实配置**的允许头里（M2）。

做法（按工单要求）：从 `backend/app/main.py` 用 AST 提取 `add_middleware(CORSMiddleware, ...)`
里「严格分支」的 `allow_headers` 字面量，原样用来构造一个小 FastAPI —— 不 import `app.main`
（会拉起单实例锁/全部模型），也不另写一份与产品脱节的常量自证。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
ALLOWED = "https://pwa.allowed.example"


def _strict_allow_headers() -> list[str]:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    found: list[str] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "add_middleware"):
            continue
        for kw in node.keywords:
            if kw.arg != "allow_headers":
                continue
            # 形如 ["*"] if _CORS_WILDCARD else [...]：取 orelse（严格分支）
            if isinstance(kw.value, ast.IfExp):
                found = [e.value for e in kw.value.orelse.elts if isinstance(e, ast.Constant)]
            elif isinstance(kw.value, ast.List):
                found = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
    assert found is not None, "main.py 未找到 CORSMiddleware.allow_headers 配置"
    return found


def _app(headers: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ALLOWED],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=headers,
    )

    @app.post("/api/v1/characters")
    def _create():
        return {"ok": True}

    return app


@pytest.fixture(scope="module")
def headers() -> list[str]:
    return _strict_allow_headers()


def test_严格白名单包含幂等键(headers):
    assert "Idempotency-Key" in headers, headers
    # 不能因为加头把已有的关键头挤掉
    for required in ("Authorization", "Content-Type", "lang", "x-ambrace-proof"):
        assert required in headers, f"缺少既有允许头 {required}"


def test_允许来源的预检通过且回显幂等键(headers):
    c = TestClient(_app(headers))
    r = c.options(
        "/api/v1/characters",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "idempotency-key,content-type",
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert "idempotency-key" in r.headers["access-control-allow-headers"].lower()


def test_未允许来源仍被拒绝(headers):
    c = TestClient(_app(headers))
    r = c.options(
        "/api/v1/characters",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "idempotency-key",
        },
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_实际POST带允许来源时回CORS头(headers):
    c = TestClient(_app(headers))
    r = c.post("/api/v1/characters", json={"name": "SYNTH"},
               headers={"Origin": ALLOWED, "Idempotency-Key": "k" * 8})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED
