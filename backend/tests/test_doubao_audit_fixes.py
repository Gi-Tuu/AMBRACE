# -*- coding: utf-8 -*-
"""豆包审查报告修复批次（2026-08-23）新增单测。

覆盖：P1-2（版本号从 VERSION 文件读取一致）、P1-4（未配置 API Key 返回 400 而非 500）、
      P2-4（GET /ready 就绪检查：DB + 模型可用，任一失败返回 503）。
说明：不 import app.main（其模块级单实例锁会占 8766 端口，测试进程仅能加载一次且可能与
      运行中实例冲突）；改为用独立 FastAPI 实例挂载对应 router 来测端点。
"""
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api import chat as chat_api
from app.api import system as system_api
from app.auth.deps import get_current_user_id


# ---------------- P1-2：版本号一致性 ----------------

def test_get_project_version_reads_version_file():
    """get_project_version 应从项目根 VERSION 文件解析出 3.2.0。"""
    from app.utils.version import get_project_version
    assert get_project_version() == "3.2.0"


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
    assert body["version"] == "3.2.0"


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


# ---------------- P2-4：/ready 就绪检查 ----------------

class _FakeReadySession:
    async def execute(self, *a, **k):
        return None


class _FakeReadyCtx:
    async def __aenter__(self):
        return _FakeReadySession()

    async def __aexit__(self, *a):
        return False


def _patch_ready_deps(monkeypatch, db_ok=True, model_ok=True):
    if db_ok:
        monkeypatch.setattr("app.db.database.async_session_factory", lambda: _FakeReadyCtx())
    else:
        class _BadSession(_FakeReadySession):
            async def execute(self, *a, **k):
                raise RuntimeError("db down")
        class _BadCtx(_FakeReadyCtx):
            async def __aenter__(self):
                return _BadSession()
        monkeypatch.setattr("app.db.database.async_session_factory", lambda: _BadCtx())
    monkeypatch.setattr("app.memory.embedding.check_model_available", lambda: model_ok)


def test_ready_ok(monkeypatch):
    """DB 与模型均可用 → 200 {status: ok, db: True, model: True}。"""
    _patch_ready_deps(monkeypatch, db_ok=True, model_ok=True)
    r = _make_system_client().get("/api/v1/system/ready")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "db": True, "model": True}


def test_ready_db_fail_returns_503(monkeypatch):
    """DB 不可连接 → 503 + 明细（db: False）。"""
    _patch_ready_deps(monkeypatch, db_ok=False, model_ok=True)
    r = _make_system_client().get("/api/v1/system/ready")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["status"] == "error"
    assert detail["db"] is False


def test_ready_model_fail_returns_503(monkeypatch):
    """模型缺失 → 503 + 明细（model: False）。"""
    _patch_ready_deps(monkeypatch, db_ok=True, model_ok=False)
    r = _make_system_client().get("/api/v1/system/ready")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["status"] == "error"
    assert detail["model"] is False
