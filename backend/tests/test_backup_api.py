# -*- coding: utf-8 -*-
# 备份一键导出 API 测试（#54，2026-08-23）：
# - 触发备份：调用真实 scripts.backup.do_backup，但把模块全局隔离到临时目录，避免写生产 data
# - 幂等：当天已存在备份时再次触发返回同一文件
# - 鉴权：非主账号 403
# - 下载：content-type zip + ascii 文件名，zip 可正常解压；无备份时 404
import io
import os
import zipfile

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api import system as system_api  # F5-c：router 壳
from app.application import system as system_svc  # F5-c：实现迁至 application，patch 须指向定义模块
from app.auth.deps import get_current_user_id

ADMIN = 1
OTHER = 200


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _isolated_backup_module(tmp_path: str, monkeypatch) -> object:
    """加载真实 scripts/backup 模块，然后把它的全局指向临时目录（不碰生产 data/日志）。"""
    mod = system_api._load_backup_module()
    mod.BACKUP_ROOT = str(tmp_path)
    mod.KEEP_DAYS = 5
    mod.SRC_DIRS = []
    mod.SRC_FILES = []
    mod.DB_FILE = os.path.join(str(tmp_path), "no.db")
    mod.CONFIG_FILE = os.path.join(str(tmp_path), "no_config.json")
    mod.LOG_DIR = os.path.join(str(tmp_path), "logs")
    mod.rotate_logs = lambda: ""
    mod.prune_trigger_logs = lambda: ""
    monkeypatch.setattr(system_svc, "_load_backup_module", lambda: mod)
    return mod


def test_trigger_backup_creates_zip(tmp_path, monkeypatch):
    _isolated_backup_module(tmp_path, monkeypatch)
    r = _make_client(ADMIN).post("/api/v1/system/backup")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["path"].endswith(".zip")
    assert data["size"] > 0
    assert data["created_at"]
    assert os.path.isfile(os.path.join(str(tmp_path), data["path"]))


def test_trigger_backup_idempotent(tmp_path, monkeypatch):
    _isolated_backup_module(tmp_path, monkeypatch)
    c = _make_client(ADMIN)
    r1 = c.post("/api/v1/system/backup")
    r2 = c.post("/api/v1/system/backup")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["path"] == r2.json()["path"]


def test_trigger_backup_forbidden():
    r = _make_client(OTHER).post("/api/v1/system/backup")
    assert r.status_code == 403


def test_download_backup(tmp_path, monkeypatch):
    _isolated_backup_module(tmp_path, monkeypatch)
    c = _make_client(ADMIN)
    c.post("/api/v1/system/backup")
    r = c.get("/api/v1/system/backup/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    cd = r.headers.get("content-disposition", "")
    assert "ambrace-backup-" in cd
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.testzip() is None


def test_download_backup_not_found(tmp_path, monkeypatch):
    _isolated_backup_module(tmp_path, monkeypatch)
    r = _make_client(ADMIN).get("/api/v1/system/backup/download")
    assert r.status_code == 404


def test_download_backup_forbidden():
    r = _make_client(OTHER).get("/api/v1/system/backup/download")
    assert r.status_code == 403
