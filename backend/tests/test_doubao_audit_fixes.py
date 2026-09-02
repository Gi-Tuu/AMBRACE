# -*- coding: utf-8 -*-
"""豆包审查报告修复批次（2026-08-23）新增单测。

覆盖：P1-2（版本号从 VERSION 文件读取一致）、P1-4（未配置 API Key 返回 400 而非 500）、
      P2-4（GET /ready 就绪分级：关键组件未就绪返回 503，全就绪返回 200）。
说明：不 import app.main（其模块级单实例锁会占 8766 端口，测试进程仅能加载一次且可能与
      运行中实例冲突）；改为用独立 FastAPI 实例挂载对应 router 来测端点。
"""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api import chat as chat_api
from app.api import system as system_api
from app.auth.deps import get_current_user_id


# ---------------- P1-2：版本号一致性 ----------------

def test_get_project_version_reads_version_file():
    """get_project_version 应从项目根 VERSION 文件解析出版本号（动态，不硬编码）。"""
    from app.utils.version import get_project_version
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    expected = None
    for line in (root / "VERSION").read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith("VERSION:"):
            expected = line.split(":", 1)[1].strip()
            break
    assert expected, "VERSION 文件缺少 VERSION: 行"
    assert get_project_version() == expected


def _make_system_client() -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    return TestClient(app)


def test_status_version_matches_project_version():
    """GET /api/v1/system/status 的 version 应与 VERSION 文件一致（P1-2）。"""
    from app.utils.version import get_project_version
    r = _make_system_client().get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == get_project_version()


# ---------------- P1-4：未配置 API Key 返回 400 ----------------

def _make_chat_client(user_id: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(chat_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    # get_db 真实依赖会开连接，这里用一个不触实际库的哑占位（get_owned_session 被 mock 掉）
    app.dependency_overrides[chat_api.get_db] = lambda: _dummy_async_session()
    return TestClient(app)


class _DummyDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _dummy_async_session():
    return _DummyDB()


class _FakeOwnedSession:
    user_id = 1
    character_id = 2


def test_send_message_no_api_key_returns_400(monkeypatch):
    """未配置 LLM API Key（RuntimeError 含『API Key』）应返回 400 + 明细，而非笼统 500。"""
    async def _fake_session(*a, **k):
        return _FakeOwnedSession()  # 会话归属校验通过

    async def _fake_send(*a, **k):
        raise RuntimeError("未配置 LLM API Key：请在管理端配置服务器级 API（PUT /api/v1/system/api-config/server）")

    monkeypatch.setattr(chat_api, "get_owned_session", _fake_session)
    monkeypatch.setattr(chat_api, "send_and_receive", _fake_send)

    r = _make_chat_client().post(
        "/api/v1/chat/send", json={"session_id": 1, "content": "hi"}
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "API Key" in detail or "未配置" in detail


def test_send_message_other_exception_returns_500(monkeypatch):
    """非 Key 相关异常仍应返回 500 internal error（不误伤）。"""
    async def _fake_session(*a, **k):
        return _FakeOwnedSession()

    async def _fake_send(*a, **k):
        raise ValueError("模型加载失败")

    monkeypatch.setattr(chat_api, "get_owned_session", _fake_session)
    monkeypatch.setattr(chat_api, "send_and_receive", _fake_send)

    r = _make_chat_client().post(
        "/api/v1/chat/send", json={"session_id": 2, "content": "hi"}
    )
    assert r.status_code == 500
    assert r.json()["detail"] == "internal error"


# ---------------- P2-4：/ready 就绪分级（AMBRACE 3.5 readiness 登记） ----------------
# 3.5 将 /ready 从「运行时 DB+模型探活」重构为「启动期组件就绪登记快照」：
# 关键组件未就绪 → 503，全就绪 → 200，可选组件降级仅登记可见。
# 测试用独立 app 手动播种（main.py lifespan 实际运行时播种）。


@pytest.fixture(autouse=True)
def _reset_readiness():
    """每用例前后清空进程级就绪登记表，避免跨用例污染。"""
    from app.utils import readiness
    readiness.reset()
    yield
    readiness.reset()


def test_ready_ok():
    """全部（关键）组件就绪 → 200 {ready: True, blocking: []}。"""
    from app.utils import readiness
    readiness.mark("database", True, critical=True)
    readiness.mark("scheduler", True, critical=True)
    r = _make_system_client().get("/api/v1/system/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["blocking"] == []
    assert body["components"]["database"]["ok"] is True


def test_ready_db_fail_returns_503():
    """关键组件 database 未就绪 → 503 + blocking 含 database。"""
    from app.utils import readiness
    readiness.mark("database", False, critical=True, msg="db init failed")
    r = _make_system_client().get("/api/v1/system/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert "database" in body["blocking"]
    assert body["components"]["database"]["ok"] is False


def test_ready_model_fail_returns_503():
    """关键组件（scheduler）未就绪 → 503 + blocking 含该组件。"""
    from app.utils import readiness
    readiness.mark("database", True, critical=True)
    readiness.mark("scheduler", False, critical=True, msg="scheduler failed")
    r = _make_system_client().get("/api/v1/system/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert "scheduler" in body["blocking"]
    assert body["components"]["scheduler"]["ok"] is False
