# -*- coding: utf-8 -*-
"""控制台删号·第一期第一批：端点 + 护栏 + 回收站行为测试（2026-09-24 派单第 4 项）。

覆盖契约（派单「本批范围」第 3 项 + 方案 v2 §3.2 护栏表 / §四）：
- 端点：``POST /api/v1/admin/server/accounts/{id}/delete-dry-run`` / ``/delete`` / ``/restore``
  一律 ``require_server_admin``（非 server_admin 403、未登录 401）；
- dry-run **零写入**：跑之前/之后逐表行数一字不变（不是「差不多」，是 dict 相等）；
- 标记删除：``deleted_at`` = ``disabled_at`` = now、``purge_after`` = now+7 天；账号从
  ``GET /server/accounts`` 默认视图消失、``include_deleted=true`` 可见；登录与既有 token 都 403；
- 护栏：删自己 / 最后一个 server_admin（含「另一个已被标记删除」的口径）/ 家庭最后一个主账号
  而家庭仍有人 / 家庭根名下有子账号 / ``confirm_username`` 不匹配 / ``purge_now`` 超阈值
  ——**全部 4xx 且库里一字未改**；
- restore 幂等；审计每条写动作都可见（含用户名 + 体量快照）。

口径：临时库一律 pytest ``tmp_path`` 私有 SQLite（``tests/_dbclone`` 克隆），绝不碰 backend/data；
每个用例前后清 permission_service 的进程内缓存（禁用态跨用例残留会凭空造 403）。
"""
import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory
from test_user_cascade_lock import _fill_ins  # 裸 INSERT 补 NOT NULL 的同一份口径

from app.api import admin as admin_api
from app.api import system as system_api
from app.auth.config import create_token
from app.auth.router import router as auth_router

pytestmark = pytest.mark.slow

ROOT_UID = 1        # server_admin + 家庭根（有子账号）→ 发起删除的管理员
SUB_UID = 2         # ROOT 的子账号 → 最常见的可删对象
SOLO_UID = 3        # 独立账号：server_admin + is_admin，名下无人 → 可直接删
FAMROOT_UID = 4     # 家庭根（非 server_admin），名下 2 个子账号 → 护栏 4
FAMADMIN_UID = 5    # FAMROOT 的子账号，且是家庭内唯一 is_admin → 护栏 3
FAMSUB_UID = 6      # FAMROOT 的另一个子账号 → 可删
PW = "rootpass123"

TOLERANCE = timedelta(minutes=2)


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory``（含 import 期早绑定）。"""
    import sys

    import app.db.database as db_mod
    import app.db.session as session_mod

    original = db_mod.async_session_factory
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(session_mod, "async_session_factory", factory, raising=False)
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")):
            continue
        try:
            if getattr(mod, "async_session_factory", None) is original:
                monkeypatch.setattr(mod, "async_session_factory", factory)
        except Exception:
            continue


@pytest.fixture()
def del_db(monkeypatch, tmp_path):
    """六个账号 + 少量业务行（memories/ai_characters/chat_sessions），够护栏各就各位。"""
    dst: Path = tmp_path / "del.db"
    engine = clone_engine(dst)
    factory = make_session_factory(engine)

    async def _init():
        from app.models.user import User
        async with factory() as db:
            db.add_all([
                User(id=ROOT_UID, username="root", nickname="根", is_admin=True,
                     server_admin=True, password_hash=_hash(PW)),
                User(id=SUB_UID, username="sub", nickname="子", parent_id=ROOT_UID,
                     password_hash=_hash(PW)),
                User(id=SOLO_UID, username="solo", nickname="独立根", is_admin=True,
                     server_admin=True, password_hash=_hash(PW)),
                User(id=FAMROOT_UID, username="famroot", nickname="户主", parent_id=None,
                     is_admin=False, password_hash=_hash(PW)),
                User(id=FAMADMIN_UID, username="famadmin", nickname="户内主账号",
                     parent_id=FAMROOT_UID, is_admin=True, password_hash=_hash(PW)),
                User(id=FAMSUB_UID, username="famsub", nickname="户内子号",
                     parent_id=FAMROOT_UID, password_hash=_hash(PW)),
            ])
            await db.commit()

    asyncio.run(_init())
    con = sqlite3.connect(str(dst))
    try:
        cur = con.cursor()
        _fill_ins(cur, "ai_characters", ["id", "user_id", "name"],
                  [(2, FAMROOT_UID, "撞号角色"),      # id 正好等于 SUB_UID → speaker_id=2 判定不出
                   (11, ROOT_UID, "甲"), (12, SUB_UID, "乙")])
        # memories：1/2 归 sub（第 2 行发言人是 root，也不改变行主人），3 归 root → 删 sub 带走 2 行
        _fill_ins(cur, "memories",
                  ["id", "user_id", "character_id", "content", "importance", "speaker_id",
                   "speaker_type"],
                  [(1, SUB_UID, 11, "m", 50, SUB_UID, "user"),
                   (2, SUB_UID, 11, "m", 50, ROOT_UID, "user"),
                   (3, ROOT_UID, 11, "m", 50, ROOT_UID, "user")])
        _fill_ins(cur, "chat_sessions", ["id", "user_id", "character_id", "title"],
                  [(1, SUB_UID, 11, "t")])
        _fill_ins(cur, "llm_usage", ["id", "user_id", "model", "prompt_tokens",
                                     "completion_tokens", "total_tokens", "group_owner_id"],
                  [(1, SUB_UID, "m", 1, 1, 2, ROOT_UID)])
        _fill_ins(cur, "chat_groups", ["id", "user_id", "name"],
                  [(1, ROOT_UID, "根的群"), (2, FAMROOT_UID, "户主的群")])
        _fill_ins(cur, "admin_audit_log", ["id", "actor_user_id", "action"], [(1, ROOT_UID, "seed")])
        con.commit()
    finally:
        con.close()
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_caches():
    from app.application import permission_service as perm
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()
    yield
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    app.include_router(system_api.router)
    app.include_router(auth_router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _accounts(c: TestClient, *, include_deleted: bool = False) -> dict:
    url = "/api/v1/admin/server/accounts" + ("?include_deleted=true" if include_deleted else "")
    r = c.get(url, headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    return {a["id"]: a for a in r.json()["accounts"]}


def _audit(c: TestClient) -> list:
    r = c.get("/api/v1/admin/server/audit", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    return r.json()["entries"]


def _user_field(factory, uid: int, field: str):
    """直接读库取 users 的某个标记字段（绕开 ORM 身份映射，保证看到的是磁盘上的值）。"""
    from app.models.user import User

    async def _run():
        async with factory() as db:
            row = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
            return None if row is None else getattr(row, field)
    return asyncio.run(_run())


def _deleted_at(factory, uid: int):
    return _user_field(factory, uid, "deleted_at")


def _all_counts(db_file: Path) -> dict:
    con = sqlite3.connect(str(db_file))
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 鉴权：三个新端点一律 require_server_admin
# ═══════════════════════════════════════════════════════════════════════════════

DELETION_POSTS = [
    (f"/api/v1/admin/server/accounts/{SUB_UID}/delete-dry-run", None),
    (f"/api/v1/admin/server/accounts/{SUB_UID}/delete", {"confirm_username": "sub"}),
    (f"/api/v1/admin/server/accounts/{SUB_UID}/restore", None),
]


def test_deletion_endpoints_require_server_admin(del_db):
    """非 server_admin（含家庭内 is_admin、子账号）→ 403；未登录 → 401。"""
    c = _client()
    for path, body in DELETION_POSTS:
        for uid in (FAMADMIN_UID, SUB_UID, FAMROOT_UID):  # 有 is_admin 也不是控制台管理员
            assert c.post(path, headers=_auth(uid), json=body or {}).status_code == 403, (path, uid)
        assert c.post(path, json=body or {}).status_code == 401, path
    # server_admin 放行
    for path, body in DELETION_POSTS:
        assert c.post(path, headers=_auth(ROOT_UID), json=body or {}).status_code == 200, path


def test_delete_unknown_user_404(del_db):
    c = _client()
    assert c.post("/api/v1/admin/server/accounts/999/delete-dry-run",
                  headers=_auth(ROOT_UID)).status_code == 404
    assert c.post("/api/v1/admin/server/accounts/999/delete",
                  headers=_auth(ROOT_UID), json={"confirm_username": "x"}).status_code == 404
    assert c.post("/api/v1/admin/server/accounts/999/restore",
                  headers=_auth(ROOT_UID)).status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# dry-run：体量清单可用 + 零写入零状态变化
# ═══════════════════════════════════════════════════════════════════════════════

def test_dry_run_reports_volume_and_writes_nothing(del_db, tmp_path):
    dst = tmp_path / "del.db"
    before = _all_counts(dst)
    assert sum(before.values()) > 0, "种子未落库"
    c = _client()
    r = c.post(f"/api/v1/admin/server/accounts/{SUB_UID}/delete-dry-run", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["dry_run"] is True and body["user_id"] == SUB_UID and body["username"] == "sub"
    assert body["mode"] == "delete_sub_account"          # 子账号 → 只删这个成员
    assert body["may_delete"] is True and body["guards"] == []
    assert body["grace_days"] == 7
    tables = {t["table"]: t for t in body["tables"]}
    assert {"memories", "chat_sessions", "llm_usage", "ai_characters"} <= set(tables)
    assert tables["memories"]["rows"] == 2               # user_id=sub 的两行
    assert tables["chat_sessions"]["rows"] == 1
    assert tables["ai_characters"]["rows"] == 1          # 名下角色 12
    assert body["totals"]["row_count"] == sum(t["rows"] for t in body["tables"]) > 1
    # tables = 清除器实际会发 DELETE 的表数（含当前 0 行的候选表），与「有删除依据列」严格一致
    assert body["totals"]["tables"] == sum(
        1 for t in body["tables"] if any(col["deletable"] for col in t["columns"]))
    # 家庭根的数据不在这次删除范围内（子账号只删自己）
    assert "chat_groups" not in tables
    assert {e["table"] for e in body["exceptions"]} >= {"chat_groups", "admin_audit_log"}
    # 判定不出归属的行必须报出来（不是被静默吞掉）：memories 第 1 行 speaker_id=2 与
    # 「别人名下的角色 id=2」撞号 → 该列不作为删除依据；该行仍因 user_id=sub 被带走（行主人是 sub）。
    assert body["undetermined_speaker_rows"] == [
        {"table": "memories", "column": "speaker_id", "rows": 1}]
    assert body["totals"]["undetermined_rows"] == 1
    assert body["character_ids"] == [12]
    assert body["purge_now_allowed_below"] == 2000

    # 零写入：逐表行数一字不变，且账号标记字段没被碰
    assert _all_counts(dst) == before
    assert _deleted_at(del_db, SUB_UID) is None
    assert c.post(f"/api/v1/admin/server/accounts/{SUB_UID}/delete-dry-run",
                  headers=_auth(ROOT_UID)).json() == body  # 幂等（同一份回包）
    # dry-run 不是写动作 → 不进审计（审计只留真改了什么）
    assert not [e for e in _audit(c) if "dry" in str(e["action"])]


def test_dry_run_reports_guard_reasons_without_raising(del_db):
    """家庭根名下有子账号：dry-run 仍 200，但把拒绝理由和「会带走家庭共享数据」一起报出来。"""
    c = _client()
    r = c.post(f"/api/v1/admin/server/accounts/{FAMROOT_UID}/delete-dry-run", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["may_delete"] is False
    assert any("家庭根" in g for g in body["guards"]), body["guards"]
    assert body["mode"] == "delete_family_root"
    assert {w["table"] for w in body["warnings"]} >= {"chat_groups"}


# ═══════════════════════════════════════════════════════════════════════════════
# 标记删除 / 回收站可见性 / 恢复
# ═══════════════════════════════════════════════════════════════════════════════

def test_mark_deleted_then_include_deleted_then_restore(del_db):
    c = _client()
    assert SUB_UID in _accounts(c)
    r = c.post(f"/api/v1/admin/server/accounts/{SUB_UID}/delete",
               headers=_auth(ROOT_UID), json={"confirm_username": "sub"})
    assert r.status_code == 200, r.text
    marked = r.json()
    deleted_at = datetime.fromisoformat(marked["deleted_at"])
    purge_after = datetime.fromisoformat(marked["purge_after"])
    assert marked["mode"] == "delete_sub_account"
    assert abs(purge_after - (deleted_at + timedelta(days=7))) < TOLERANCE
    assert marked["disabled_at"] == marked["deleted_at"]  # 进回收站即强制禁用

    # 默认视图隐藏；include_deleted 才可见，且带两个新字段
    assert SUB_UID not in _accounts(c)
    shown = _accounts(c, include_deleted=True)
    assert shown[SUB_UID]["deleted_at"] and shown[SUB_UID]["purge_after"]
    assert shown[SUB_UID]["disabled_at"]  # 门禁仍生效（普通客户端不可用）

    # 重复标记被拒：不然宽限期会被悄悄重置回 7 天（物理清除一直不发生）
    again = c.post(f"/api/v1/admin/server/accounts/{SUB_UID}/delete",
                   headers=_auth(ROOT_UID), json={"confirm_username": "sub"})
    assert again.status_code == 400 and "回收站" in again.json()["detail"]
    assert _user_field(del_db, SUB_UID, "purge_after").isoformat() == marked["purge_after"]

    # 回收站里的账号：登录 403、既有 token 请求 403（复用 P2 门禁）
    assert c.post("/api/v1/auth/login", json={"username": "sub", "password": PW}).status_code == 403
    assert c.get("/api/v1/system/status/detail", headers=_auth(SUB_UID)).status_code == 403

    # 审计：动作 + 用户名 + 体量快照（§6-4 口径）
    entries = [e for e in _audit(c) if e["action"] == "account.delete"]
    assert entries and entries[0]["target"] == f"user:{SUB_UID}"
    assert entries[0]["actor_username"] == "root"
    assert entries[0]["after"]["username"] == "sub"
    assert entries[0]["after"]["volume"]["row_count"] == marked["totals"]["row_count"]
    assert entries[0]["before"]["deleted_at"] is None

    r = c.post(f"/api/v1/admin/server/accounts/{SUB_UID}/restore", headers=_auth(ROOT_UID))
    assert r.status_code == 200, r.text
    assert SUB_UID in _accounts(c)
    assert _accounts(c, include_deleted=True)[SUB_UID]["deleted_at"] is None
    assert c.post("/api/v1/auth/login", json={"username": "sub", "password": PW}).status_code == 200
    assert any(e["action"] == "account.restore" for e in _audit(c))


def test_restore_is_idempotent(del_db):
    c = _client()
    assert c.post(f"/api/v1/admin/server/accounts/{SUB_UID}/restore",
                  headers=_auth(ROOT_UID)).status_code == 200
    again = c.post(f"/api/v1/admin/server/accounts/{SUB_UID}/restore", headers=_auth(ROOT_UID))
    assert again.status_code == 200 and again.json()["restored"] is False
    assert _deleted_at(del_db, SUB_UID) is None
    assert SUB_UID in _accounts(c)


# ═══════════════════════════════════════════════════════════════════════════════
# 护栏：全部拒绝，且库里一字未改
# ═══════════════════════════════════════════════════════════════════════════════

def test_guardrails_reject_and_write_nothing(del_db, tmp_path):
    c = _client()
    before = _all_counts(tmp_path / "del.db")
    cases = [
        (ROOT_UID, "root", "自己"),                     # 护栏 1
        (FAMROOT_UID, "famroot", "家庭根"),              # 护栏 4：名下有子账号
        (FAMADMIN_UID, "famadmin", "主账号"),            # 护栏 3：家庭最后一个 is_admin
    ]
    for uid, username, needle in cases:
        r = c.post(f"/api/v1/admin/server/accounts/{uid}/delete",
                   headers=_auth(ROOT_UID), json={"confirm_username": username})
        assert r.status_code == 400, (uid, r.text)
        assert needle in r.json()["detail"], (uid, r.text)
        assert _deleted_at(del_db, uid) is None, uid
    assert _all_counts(tmp_path / "del.db") == before    # 被拒的删除不留痕（除审计）
    assert _deleted_at(del_db, SUB_UID) is None


def test_cannot_delete_last_server_admin_even_counting_recycle_bin(del_db):
    """护栏 2：另一个 server_admin 已在回收站里 → 不算存活管理员，最后一个不许删。"""
    c = _client()
    assert c.post(f"/api/v1/admin/server/accounts/{SOLO_UID}/delete",
                  headers=_auth(ROOT_UID), json={"confirm_username": "solo"}).status_code == 200
    r = c.post(f"/api/v1/admin/server/accounts/{ROOT_UID}/delete-dry-run", headers=_auth(ROOT_UID))
    assert any("服务器管理员" in g for g in r.json()["guards"]), r.json()["guards"]
    # 恢复 solo → 这条护栏解除，剩下的拒绝理由变成「家庭根名下有子账号」
    assert c.post(f"/api/v1/admin/server/accounts/{SOLO_UID}/restore",
                  headers=_auth(ROOT_UID)).status_code == 200
    body = c.post(f"/api/v1/admin/server/accounts/{ROOT_UID}/delete-dry-run",
                  headers=_auth(ROOT_UID)).json()
    assert not any("服务器管理员" in g for g in body["guards"]), body["guards"]
    assert any("家庭根" in g for g in body["guards"])


def test_deletable_standalone_admin_account(del_db):
    """独立账号（名下无人、不是最后一个 server_admin）→ 正常标记删除（最常见场景不被护栏误伤）。

    但 dry-run 仍必须警告「家庭根会带走整户共享数据」——名下无人时这是运维唯一的提示位。
    """
    c = _client()
    url = f"/api/v1/admin/server/accounts/{SOLO_UID}"
    dry = c.post(f"{url}/delete-dry-run", headers=_auth(ROOT_UID)).json()
    assert dry["mode"] == "delete_family_root" and dry["may_delete"] is True, dry["guards"]
    assert {w["table"] for w in dry["warnings"]} >= {"chat_groups"}, dry["warnings"]
    assert all("家庭根" in w["reason"] for w in dry["warnings"])

    r = c.post(f"{url}/delete", headers=_auth(ROOT_UID), json={"confirm_username": "solo"})
    assert r.status_code == 200, r.text
    assert SOLO_UID not in _accounts(c)
    assert _deleted_at(del_db, SOLO_UID) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# confirm_username / purge_now 阈值
# ═══════════════════════════════════════════════════════════════════════════════

def test_confirm_username_required_and_exact(del_db):
    c = _client()
    url = f"/api/v1/admin/server/accounts/{SUB_UID}/delete"
    assert c.post(url, headers=_auth(ROOT_UID), json={}).status_code == 400
    assert c.post(url, headers=_auth(ROOT_UID), json={"confirm_username": ""}).status_code == 400
    for wrong in ("sub2", "SUB", " sub", "su"):
        r = c.post(url, headers=_auth(ROOT_UID), json={"confirm_username": wrong})
        assert r.status_code == 400, wrong
        assert "不一致" in r.json()["detail"] or "确认" in r.json()["detail"]
    assert _deleted_at(del_db, SUB_UID) is None  # 每次拒绝都不留状态


def test_purge_now_row_threshold_boundary_and_dry_run_agreement(del_db, monkeypatch):
    """``purge_now`` 判据 = 清除器实际会删的行数（按表去重），且 **严格小于** 阈值才放行。

    同一阈值下 dry-run 的 ``purge_now_would_be_allowed`` 必须与 ``delete`` 的真实结论一致
    （预览说能删、真删却拒 → 运维会在控制台踩坑）；阈值可用环境变量收（紧急关闭）。
    """
    c = _client()
    url = f"/api/v1/admin/server/accounts/{SUB_UID}"
    rows = c.post(f"{url}/delete-dry-run", headers=_auth(ROOT_UID)).json()["totals"]["row_count"]
    assert rows > 1, "种子行数须 > 1，否则阈值边界测不出来"

    for threshold, allowed in ((str(rows - 1), False), (str(rows), False), (str(rows + 1), True)):
        monkeypatch.setenv("ADMIN_PURGE_NOW_ROW_THRESHOLD", threshold)
        dry = c.post(f"{url}/delete-dry-run", headers=_auth(ROOT_UID)).json()
        assert dry["purge_now_allowed_below"] == int(threshold)
        assert dry["purge_now_would_be_allowed"] is allowed, threshold
        r = c.post(f"{url}/delete", headers=_auth(ROOT_UID),
                   json={"confirm_username": "sub", "purge_now": True})
        if allowed:
            assert r.status_code == 200, r.text
            assert r.json()["purge_now"] is True
            # purge_now → 到期时间就是当下（第二期清除器据此立即回收）
            assert abs(datetime.fromisoformat(r.json()["purge_after"])
                       - datetime.now(timezone.utc).replace(tzinfo=None)) < TOLERANCE
            assert c.post(f"{url}/restore", headers=_auth(ROOT_UID)).status_code == 200
        else:
            assert r.status_code == 400, (threshold, r.text)
            assert "阈值" in r.json()["detail"]
            assert _deleted_at(del_db, SUB_UID) is None, threshold


def test_purge_now_default_threshold_allows_small_account(del_db, monkeypatch):
    """默认阈值 2000：沙箱体量远小于它 → 预览与真删口径一致地放行。"""
    monkeypatch.delenv("ADMIN_PURGE_NOW_ROW_THRESHOLD", raising=False)
    c = _client()
    url = f"/api/v1/admin/server/accounts/{SUB_UID}"
    dry = c.post(f"{url}/delete-dry-run", headers=_auth(ROOT_UID)).json()
    assert dry["purge_now_allowed_below"] == 2000 and dry["purge_now_would_be_allowed"] is True
    assert c.post(f"{url}/delete", headers=_auth(ROOT_UID),
                  json={"confirm_username": "sub", "purge_now": True}).status_code == 200


def test_threshold_env_garbage_falls_back_to_default(monkeypatch):
    """环境变量写错 → 回落默认 2000，绝不因配置错误放开护栏。"""
    monkeypatch.setenv("ADMIN_PURGE_NOW_ROW_THRESHOLD", "unlimited")
    from app.application.account_deletion import purge_now_row_threshold
    assert purge_now_row_threshold() == 2000
    monkeypatch.setenv("ADMIN_PURGE_NOW_ROW_THRESHOLD", "-5")
    assert purge_now_row_threshold() == 0
    monkeypatch.delenv("ADMIN_PURGE_NOW_ROW_THRESHOLD", raising=False)
    assert purge_now_row_threshold() == 2000
    assert os.environ.get("ADMIN_PURGE_NOW_ROW_THRESHOLD") is None
