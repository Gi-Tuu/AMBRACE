# -*- coding: utf-8 -*-
"""AMBRACE 3.5 readiness：启动期组件就绪登记 + /ready 端点分级（2026-09-02）。

覆盖：
- mark()/snapshot() 纯函数：全好→ready、critical 坏→blocking、可选组件降级仍 ready 且 components 可见。
- /api/v1/system/ready 端点：ready→200、critical 坏→503，响应含 components/blocking。

说明：不 import app.main（其模块级单实例锁会占端口，且 lifespan 只在真实运行时播种）；
用独立 FastAPI 实例挂载 system router，配合 readiness.mark 手动播种状态测端点。
"""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api import system as system_api
from app.utils import readiness


@pytest.fixture(autouse=True)
def _reset_readiness():
    """每用例前后清空进程级就绪登记表，避免跨用例污染。"""
    readiness.reset()
    yield
    readiness.reset()


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    return TestClient(app)


# ------------------------- mark/snapshot 纯函数 -------------------------

def test_snapshot_empty_is_ready():
    """空登记（无 critical 组件）→ ready True、blocking 空、components 空。"""
    snap = readiness.snapshot()
    assert snap["ready"] is True
    assert snap["blocking"] == []
    assert snap["components"] == {}


def test_snapshot_all_ok_ready():
    """全部组件就绪（含 critical）→ ready True、blocking 空、components 完整可见。"""
    readiness.mark("database", True, critical=True)
    readiness.mark("scheduler", True, critical=True)
    readiness.mark("plugins", True)
    snap = readiness.snapshot()
    assert snap["ready"] is True
    assert snap["blocking"] == []
    assert snap["components"]["database"]["ok"] is True
    assert snap["components"]["database"]["critical"] is True
    assert snap["components"]["plugins"]["ok"] is True
    assert snap["components"]["plugins"]["critical"] is False


def test_critical_bad_blocking_and_not_ready():
    """任一 critical 组件 bad → ready False，blocking 列出该组件。"""
    readiness.mark("database", True, critical=True)
    readiness.mark("alembic", False, critical=True, msg="schema misaligned")
    snap = readiness.snapshot()
    assert snap["ready"] is False
    assert snap["blocking"] == ["alembic"]
    assert snap["components"]["alembic"]["ok"] is False
    assert snap["components"]["alembic"]["msg"] == "schema misaligned"


def test_optional_component_degrade_keeps_ready():
    """可选组件降级（ok=False、非 critical）→ 仍 ready，且降级状态在 components 可见。"""
    readiness.mark("database", True, critical=True)
    readiness.mark("plugins", False, msg="plugin load failed")
    snap = readiness.snapshot()
    assert snap["ready"] is True
    assert snap["blocking"] == []
    assert snap["components"]["plugins"] == {"ok": False, "critical": False, "msg": "plugin load failed"}


def test_reset_clears_state():
    """reset() 清空已登记状态。"""
    readiness.mark("database", True, critical=True)
    readiness.reset()
    assert readiness.snapshot()["components"] == {}


def test_snapshot_returns_copy_not_shared_state():
    """snapshot 返回 components 拷贝，调用方修改不影响登记表。"""
    readiness.mark("database", True, critical=True)
    snap = readiness.snapshot()
    snap["components"]["database"]["ok"] = False
    assert readiness.snapshot()["components"]["database"]["ok"] is True


# ------------------------- /api/v1/system/ready 端点 -------------------------

def test_ready_endpoint_ok_200():
    """全部关键组件就绪 → 200 {ready: True, blocking: [], components 含数据库}。"""
    readiness.mark("database", True, critical=True)
    readiness.mark("scheduler", True, critical=True)
    r = _make_client().get("/api/v1/system/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["blocking"] == []
    assert "database" in body["components"]
    assert body["components"]["database"]["ok"] is True


def test_ready_endpoint_critical_bad_503():
    """关键组件未就绪 → 503 + blocking 含该组件。"""
    readiness.mark("database", True, critical=True)
    readiness.mark("alembic", False, critical=True, msg="bad")
    r = _make_client().get("/api/v1/system/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert "alembic" in body["blocking"]
    assert body["components"]["alembic"]["ok"] is False
    assert body["components"]["alembic"]["msg"] == "bad"


def test_ready_endpoint_empty_is_200():
    """独立挂载/未播种（等价于无关键组件登记）→ 200 就绪。"""
    r = _make_client().get("/api/v1/system/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is True
