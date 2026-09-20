# -*- coding: utf-8 -*-
"""P3-3（2026-09-20）：/uploads 闸门对抖音私有草稿目录的归属收口回归。

被测口径（``backend/app/uploads_gate.py``）：

- ``douyin/{task_id}/...`` 不再是共享白名单，改按插件表 ``DouyinPending.tenant_id``（该列本身
  就是家庭根）回查归属；
- 兼容模式（``uploads_require_auth=False``，默认）匿名裸请求仍放行 → App 草稿预览不回归；
- 带身份：跨家庭 404，本家庭（家庭根本人 / 其子账号）放行；严格模式收口；
- 插件未加载、任务行已删（归属证不出来）→ 回兼容口径放行，核心 app 不硬依赖插件。

插件模型按内核同款方式取（插件目录入 sys.path 后 ``import douyin_models``）：**不 exec 副本**，
否则会向全局 ``plugin_metadata`` 重复注册 douyin_* 表，污染 test_plugin_isolated_metadata 的
建表断言。
"""
import asyncio
import importlib
import sys

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.application import tenant_service as tenant_svc
from app.auth.config import create_token
from app.config import settings
from app.models.user import User
from app.plugins import registry
from app.uploads_gate import TenantStaticFiles, resolve_upload_scope

_A_ROOT, _A_SUB, _B_ROOT = 1, 3, 2          # 家庭 A：root=1 + 子账号 3；家庭 B：root=2
_TASK_A, _TASK_B = 501, 502                 # 两家庭各自的抖音待发布草稿任务
_PLUGIN_DIR = registry.EXAMPLE_DIR / "douyin_mcp"


def _douyin_models():
    """取插件自有 ORM 模块（内核加载 douyin_mcp 后命中的也是这个 sys.modules 条目）。"""
    if "douyin_models" not in sys.modules and str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    return importlib.import_module("douyin_models")


def _auth(uid: int) -> dict:
    """真实 JWT（conftest 固定 AUTH_SECRET_KEY）——走闸门真实解析路径。"""
    return {"Authorization": f"Bearer {create_token(uid)}"}


@pytest.fixture(autouse=True)
def _family_scope():
    """钉住口径 A（family）：本文件「家庭内子账号放行」的断言以此为前提。"""
    prev = tenant_svc._mode_override
    tenant_svc.set_tenant_key_mode(tenant_svc.TENANT_KEY_MODE_FAMILY)
    yield
    tenant_svc._mode_override = prev


@pytest.fixture()
def gate_db(monkeypatch, tmp_path):
    """两家庭 + 各一条 douyin_pending 行的私有临时 SQLite 库，并接到闸门用的会话工厂上。"""
    dm = _douyin_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dygate.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # 插件表在独立 plugin_metadata 里，主 create_all 不建，本用例自行补建单表
            await conn.run_sync(lambda c: dm.DouyinPending.__table__.create(c, checkfirst=True))
        async with factory() as db:
            db.add_all([
                User(id=_A_ROOT, username="a_root", nickname="A主", is_admin=True),
                User(id=_A_SUB, username="a_sub", nickname="A子", is_admin=False, parent_id=_A_ROOT),
                User(id=_B_ROOT, username="b_root", nickname="B主", is_admin=True),
                dm.DouyinPending(id=_TASK_A, tenant_id=_A_ROOT, kind="image_post", title="A家草稿"),
                dm.DouyinPending(id=_TASK_B, tenant_id=_B_ROOT, kind="image_post", title="B家草稿"),
            ])
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    import app.db.session as session_mod

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(session_mod, "async_session_factory", factory, raising=False)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture()
def uploads(gate_db, tmp_path):
    """挂载带闸门的 /uploads：两家草稿各一张配图。"""
    up = tmp_path / "uploads"
    for task, name, blob in ((_TASK_A, "x.png", b"pngA"), (_TASK_B, "y.png", b"pngB")):
        d = up / "douyin" / str(task)
        d.mkdir(parents=True)
        (d / name).write_bytes(blob)
    app = FastAPI()
    app.mount("/uploads", TenantStaticFiles(directory=str(up)))
    return TestClient(app)


def test_p3_3_douyin_draft_is_out_of_shared_whitelist():
    from app.uploads_gate import _SHARED_HEADS

    assert "douyin" not in _SHARED_HEADS
    assert resolve_upload_scope(f"douyin/{_TASK_A}/x.png") == ("douyin_task", _TASK_A)
    assert resolve_upload_scope(f"douyin/{_TASK_A}") == ("douyin_task", _TASK_A)
    # 第 2 段不是 task_id：维持改动前的共享放行口径（不新增误伤面）
    assert resolve_upload_scope("douyin/t/v.mp4") == ("shared", None)


def test_p3_3_anonymous_bare_request_allowed_in_compat_mode(uploads, monkeypatch):
    """① 兼容模式（默认）：App 裸 URL 取草稿配图不回归。"""
    monkeypatch.setattr(settings, "uploads_require_auth", False)

    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png").status_code == 200


def test_p3_3_other_tenant_denied_404(uploads, monkeypatch):
    """② 带其他租户身份读别家草稿 → 404（与「不存在」同观感，防探测）。"""
    monkeypatch.setattr(settings, "uploads_require_auth", False)

    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png", headers=_auth(_B_ROOT)).status_code == 404


def test_p3_3_owner_tenant_allowed(uploads, monkeypatch):
    """③ 本租户放行：家庭根本人、家庭内子账号，以及 B 家读自己的草稿。"""
    monkeypatch.setattr(settings, "uploads_require_auth", False)

    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png", headers=_auth(_A_ROOT)).status_code == 200
    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png", headers=_auth(_A_SUB)).status_code == 200
    assert uploads.get(f"/uploads/douyin/{_TASK_B}/y.png", headers=_auth(_B_ROOT)).status_code == 200


def test_p3_3_strict_mode_closes_the_gate(uploads, monkeypatch):
    """严格模式（uploads_require_auth=True）：匿名 404，带身份按租户裁决。"""
    monkeypatch.setattr(settings, "uploads_require_auth", True)

    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png").status_code == 404
    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png", headers=_auth(_A_ROOT)).status_code == 200
    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png", headers=_auth(_B_ROOT)).status_code == 404


def test_p3_3_plugin_unloaded_falls_back_to_compat(uploads, monkeypatch):
    """插件未加载（douyin_models 不可导入）→ 回兼容口径放行：核心 app 不硬依赖插件。"""
    monkeypatch.setattr(settings, "uploads_require_auth", False)
    monkeypatch.setitem(sys.modules, "douyin_models", None)  # sys.modules 条目为 None → import 抛 ImportError

    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png", headers=_auth(_B_ROOT)).status_code == 200


def test_p3_3_deleted_task_falls_back_to_compat(uploads, gate_db, monkeypatch):
    """任务行已删（孤儿草稿文件）→ 同属证不了归属，兼容放行（与会话孤儿文件口径一致）。"""
    monkeypatch.setattr(settings, "uploads_require_auth", False)
    dm = _douyin_models()

    async def _drop_task():
        async with gate_db() as db:
            row = await db.get(dm.DouyinPending, _TASK_A)
            await db.delete(row)
            await db.commit()

    asyncio.run(_drop_task())

    assert uploads.get(f"/uploads/douyin/{_TASK_A}/x.png", headers=_auth(_B_ROOT)).status_code == 200
