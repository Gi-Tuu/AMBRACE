# -*- coding: utf-8 -*-
"""控制台删号·第二期第一批：物理清除器测试（2026-09-24 派单第 9 项）。

覆盖派单「本批范围」逐条：
- **端到端**（沙箱库 + tmp 目录）：候选表行归 0、``users`` 行没了、``PRAGMA foreign_key_check``
  为空、该账号的文件出现在 ``data/trash/<uid>/`` 下、**别人的文件一个不动**、BM25 缓存按角色
  失效、向量层收到的是**第 0 步固化的** memory id（证明「行删了才反查」这条路没走）、
  作业账本与审计都带各表行数；
- **dry-run 对照**：计划里列出的每张候选表，删完后按同一判据再数一次必须是 0；
- **幂等**：``done`` 之后重跑直接返回存量报告（不重复删、不报错、行数一字不变）；
- **护栏**：未标记回收站的账号不能清（``force`` 也不开后门）、宽限期未到需 ``force``、
  ``confirm_username`` 必须逐字符相等、非 server_admin 401/403 —— 全部零写入；
- **前置备份 fail-closed**：注入「备份必炸」→ 500 + 作业 ``failed`` + **一行都没删**；
- **向量层**：老向量缺 ``user_id`` metadata 时按 memory_id 补删；一个删不掉的 id 不拖垮整批；
  向量库整体不可用也不抛穿；
- **迁移**：``account_purge_jobs`` 幂等/可逆、哨兵已登记、版本链保持单头。

口径：一律 pytest ``tmp_path`` 私有 SQLite（``tests/_dbclone`` 克隆）+ tmp 数据目录；
``_data_dir`` / ``_run_backup`` / 向量层 / BM25 落盘根**全部换到 tmp**，绝不碰 backend/data
与真备份目录（生产库与本测试无关，连只读都不做）。
"""
import asyncio
import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory
from test_account_deletion import _hash, _patch_session_factories
from test_user_cascade_lock import _fill_ins

from app.api import admin as admin_api
from app.application import account_purge
from app.auth.config import create_token
from app.db import vector_store
from app.memory import bm25_index

pytestmark = pytest.mark.slow

ACTOR_UID = 1      # server_admin + 独立主账号（发起方）
TGT_UID = 2        # 目标：独立账号（名下无人）→ scope=delete_family_root
OTH_UID = 3        # 旁观账号：文件与数据都必须活着
TGT_CID, OTH_CID = 21, 31
SESS_TGT, SESS_OTH = 41, 42
MEM_TGT, MEM_OTH = 51, 52
MEM_TGT_B = 53      # 目标账号的第二条记忆（给「半张表被杀」的续跑用例留余量）
MSG_TGT, MSG_OTH = 61, 62          # chat_messages（无归属列，靠 FK 级联随会话走）
CSTATE_TGT, CSTATE_OTH = 71, 72    # character_states
CSHIST_TGT = 81                    # character_state_history
TASK_TGT, TASK_OTH = 7, 8          # douyin_pending（uploads/douyin/{id} 的目录名就是它）
PW = "rootpass123"

_MIG_REL = Path("alembic") / "versions" / "f7b8c9d0e1f2_add_account_purge_jobs.py"

#: 目标账号名下的 uploads 目录（真搬走进 trash）
TGT_UPLOAD_RELS = ("avatars/2/a.png", "moments/2/m.png", "images/2/i.png", "phone/2/p.json",
                   "41/s.png", "files/41/f.png", "voice/41/v.mp3",
                   "emojis/user/2/e.png", "douyin/7/d.png")
#: 必须原地不动的：别人的、共享资源、判不出归属的未知布局
KEEP_UPLOAD_RELS = ("avatars/3/a.png", "42/s.png", "files/42/f.png",
                    "emojis/user/3/e.png", "douyin/8/d.png",
                    "pets/p.png", "tts/t.mp3", "emojis/market/e.png",
                    "unknown_layout/x.bin")
#: 「按这列等于目标 uid 就该归 0」的列（不含 speaker_id/updated_by：两者都不作为删除依据）
OWNERSHIP_COLS = ("user_id", "owner_user_id", "tenant_id", "creator_id", "group_owner_id",
                  "actor_user_id")


def _mk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def _load_jobs_migration(name: str = "mig_account_purge_jobs"):
    path = Path(__file__).resolve().parents[1] / _MIG_REL
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply_jobs_migration(db_path: Path):
    """把 ``account_purge_jobs`` 建进克隆库（该表刻意不在 ``Base.metadata``，模板库里没有）。

    走**迁移脚本本身**而不是手写 DDL：顺带证明生产建表路径真的可用（幂等守卫 + 事后回验）。
    """
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_jobs_migration()
    eng = sa.create_engine(f"sqlite:///{db_path.as_posix()}")
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    eng.dispose()
    return mig


@pytest.fixture()
def purge_env(monkeypatch, tmp_path):
    """一个可清的账号 + 两族数据行 + 假 uploads/bm25 缓存 + 桩向量层 + 桩备份。"""
    dst: Path = tmp_path / "purge.db"
    engine = clone_engine(dst, with_plugins=("douyin_mcp",))
    _apply_jobs_migration(dst)
    factory = make_session_factory(engine)

    async def _seed_users():
        from app.models.user import User
        async with factory() as db:
            db.add_all([
                User(id=ACTOR_UID, username="actor", nickname="控制台", is_admin=True,
                     server_admin=True, password_hash=_hash(PW)),
                User(id=TGT_UID, username="tgt", nickname="待删", is_admin=True,
                     password_hash=_hash(PW)),
                User(id=OTH_UID, username="other", nickname="邻居", is_admin=True,
                     password_hash=_hash(PW)),
            ])
            await db.commit()

    asyncio.run(_seed_users())
    con = sqlite3.connect(str(dst))
    try:
        cur = con.cursor()
        _fill_ins(cur, "ai_characters", ["id", "user_id", "name"],
                  [(TGT_CID, TGT_UID, "小甲"), (OTH_CID, OTH_UID, "邻家")])
        _fill_ins(cur, "chat_sessions", ["id", "user_id", "character_id", "title"],
                  [(SESS_TGT, TGT_UID, TGT_CID, "目标会话"),
                   (SESS_OTH, OTH_UID, OTH_CID, "邻家会话")])
        _fill_ins(cur, "chat_messages", ["id", "session_id", "sender_type", "content"],
                  [(MSG_TGT, SESS_TGT, "user", "hi"), (MSG_OTH, SESS_OTH, "user", "hi")])
        _fill_ins(cur, "memories",
                  ["id", "user_id", "character_id", "content", "importance", "speaker_id",
                   "speaker_type"],
                  [(MEM_TGT, TGT_UID, TGT_CID, "m", 50, TGT_UID, "user"),
                   (MEM_TGT_B, TGT_UID, TGT_CID, "m", 50, TGT_UID, "user"),
                   (MEM_OTH, OTH_UID, OTH_CID, "m", 50, OTH_UID, "user")])
        _fill_ins(cur, "character_states", ["id", "character_id"],
                  [(CSTATE_TGT, TGT_CID), (CSTATE_OTH, OTH_CID)])
        _fill_ins(cur, "character_state_history", ["id", "character_id"], [(CSHIST_TGT, TGT_CID)])
        _fill_ins(cur, "douyin_pending", ["id", "tenant_id", "kind"],
                  [(TASK_TGT, TGT_UID, "image_post"), (TASK_OTH, OTH_UID, "image_post")])
        con.commit()
    finally:
        con.close()

    # ── 假数据目录（uploads 根真来源是 PROJECT_ROOT/data/uploads，这里整体挪到 tmp_path）──
    data = tmp_path / "data"
    uploads = data / "uploads"
    for rel in TGT_UPLOAD_RELS + KEEP_UPLOAD_RELS:
        _mk(uploads / rel)
    _mk(data / "mcp" / f"user_{TGT_UID}" / "cfg.json")
    _mk(data / "mcp" / f"user_{OTH_UID}" / "cfg.json")
    monkeypatch.setattr(account_purge, "_data_dir", lambda: data)

    # ── 桩：备份（真 do_backup() 会去拷生产库与源码，测试里绝对不许跑）──
    backups: list[str] = []

    def _fake_backup() -> str:
        zip_path = _mk(tmp_path / "backups" / "20260924.zip")
        backups.append(str(zip_path))
        return str(zip_path)

    monkeypatch.setattr(account_purge, "_run_backup", _fake_backup)

    # ── 桩：向量层（只记录收到什么，不碰 Chroma）──
    vector_calls: list[dict] = []

    async def _fake_vectors(user_id: int, memory_ids=None) -> dict:
        ids = [int(m) for m in (memory_ids or [])]
        vector_calls.append({"user_id": int(user_id), "memory_ids": ids})
        return {"by_user": 1, "by_memory": len(ids), "unresolved": 0}

    monkeypatch.setattr(vector_store, "delete_memory_vectors_by_user", _fake_vectors)

    # ── 假 BM25 落盘缓存（调真 invalidate，断言文件真被删）──
    bm25_root = tmp_path / "bm25_cache"
    bm25_root.mkdir()
    for cid in (TGT_CID, OTH_CID):
        (bm25_root / f"{cid}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bm25_index, "_persist_root", bm25_root)

    _patch_session_factories(monkeypatch, factory)
    yield SimpleNamespace(dst=dst, data=data, uploads=uploads, factory=factory, bm25_root=bm25_root,
                          backups=backups, vector_calls=vector_calls, tmp_path=tmp_path)
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


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _purge(c: TestClient, uid: int = TGT_UID, **body):
    return c.post(f"/api/v1/admin/server/accounts/{uid}/purge",
                  headers=_auth(ACTOR_UID), json=body)


def _mark_deleted(c: TestClient, uid: int = TGT_UID, *, purge_now: bool = False):
    username = "tgt" if uid == TGT_UID else "other"
    body = {"confirm_username": username}
    if purge_now:
        body["purge_now"] = True
    return c.post(f"/api/v1/admin/server/accounts/{uid}/delete", headers=_auth(ACTOR_UID), json=body)


def _rows(db_file: Path, table: str, where: str = "") -> int:
    con = sqlite3.connect(str(db_file))
    try:
        sql = f'SELECT COUNT(*) FROM "{table}"' + (f" WHERE {where}" if where else "")
        return int(con.execute(sql).fetchone()[0] or 0)
    finally:
        con.close()


def _all_counts(db_file: Path) -> dict:
    """逐表行数（排除两张「本来就会变」的表：作业账本与 append-only 审计）。"""
    con = sqlite3.connect(str(db_file))
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        out = {}
        for t in tables:
            if t in (account_purge.JOB_TABLE, "admin_audit_log"):
                continue
            out[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        return out
    finally:
        con.close()


def _job(db_file: Path, uid: int = TGT_UID) -> dict:
    con = sqlite3.connect(str(db_file))
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(f"SELECT * FROM {account_purge.JOB_TABLE} WHERE user_id = ?",
                          (int(uid),)).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _fk_check(db_file: Path) -> list:
    con = sqlite3.connect(str(db_file))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        return con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()


def _audit(c: TestClient, action: str = "account.purge") -> list:
    r = c.get("/api/v1/admin/server/audit", headers=_auth(ACTOR_UID))
    assert r.status_code == 200, r.text
    return [e for e in r.json()["entries"] if e["action"] == action]


def _has_col(db_file: Path, table: str, col: str) -> bool:
    con = sqlite3.connect(str(db_file))
    try:
        return col in {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 端到端：清得干净、文件进回收目录、别人与共享资源不动
# ═══════════════════════════════════════════════════════════════════════════════

def test_purge_end_to_end(purge_env):
    env = purge_env
    c = _client()
    assert _mark_deleted(c).status_code == 200
    r = _purge(c, confirm_username="tgt", force=True)
    assert r.status_code == 200, r.text
    rep = r.json()

    assert rep["status"] == "done" and rep["resumed"] is False and rep["idempotent"] is False
    assert rep["mode"] == "delete_family_root" and rep["forced"] is True
    assert isinstance(rep["elapsed_seconds"], (int, float))
    # 第 0 步固化的三份集合（回包看得见，运维才知道「按什么删的」）
    assert rep["frozen"]["character_ids"] == [TGT_CID]
    assert rep["frozen"]["session_ids"] == [SESS_TGT]
    assert rep["frozen"]["memory_count"] == 2
    assert rep["backup_zip"].endswith(".zip") and Path(rep["backup_zip"]).is_file()
    assert len(env.backups) == 1

    # 库：账号行与两族数据行都没了，邻居的一行不少
    assert _rows(env.dst, "users", "id=%d" % TGT_UID) == 0
    assert _rows(env.dst, "users", "id=%d" % OTH_UID) == 1
    for table, where in (("ai_characters", "user_id=%d" % TGT_UID),
                         ("chat_sessions", "user_id=%d" % TGT_UID),
                         ("memories", "user_id=%d" % TGT_UID),
                         ("character_states", "character_id=%d" % TGT_CID),
                         ("character_state_history", "character_id=%d" % TGT_CID),
                         ("douyin_pending", "tenant_id=%d" % TGT_UID),
                         ("chat_messages", "session_id=%d" % SESS_TGT)):
        assert _rows(env.dst, table, where) == 0, table
    for table, where in (("ai_characters", "user_id=%d" % OTH_UID),
                         ("chat_sessions", "user_id=%d" % OTH_UID),
                         ("memories", "user_id=%d" % OTH_UID),
                         ("character_states", "character_id=%d" % OTH_CID),
                         ("douyin_pending", "tenant_id=%d" % OTH_UID),
                         ("chat_messages", "session_id=%d" % SESS_OTH)):
        assert _rows(env.dst, table, where) == 1, table

    # 逐表计数进报告（派单：报告要有「每张表删了几行」）
    per_table = {t["table"]: t["rows"] for t in rep["tables"]}
    assert per_table["memories"] == 2 and per_table["chat_sessions"] == 1
    assert per_table["character_state_history"] == 1 and per_table["douyin_pending"] == 1
    assert per_table["ai_characters"] == 1 and per_table["users"] == 1
    assert rep["rows_deleted"] == sum(per_table.values())
    assert len(rep["tables"]) >= 30, "候选表数异常偏少：cascade 清单没被完整执行？"
    # 收尾核对：外键检查为空（留下孤儿就是级联缺口，必须被这一条抓到）
    assert rep["foreign_key_check_rows"] == 0 and rep["foreign_key_check"] == []
    assert _fk_check(env.dst) == []

    # 文件：该账号的都在 trash 下且源目录消失；别人的与共享的原地不动
    trash = env.data / "trash" / str(TGT_UID)
    assert rep["files"]["trash_dir"] == str(trash)
    assert rep["files"]["upload_dir_count"] == len(TGT_UPLOAD_RELS)
    assert rep["files"]["files_moved"] == len(TGT_UPLOAD_RELS) + 1   # +1 = mcp/user_2/cfg.json
    assert rep["files"]["mcp_files_moved"] == 1
    assert rep["files"]["partial_dirs"] == []
    for rel in TGT_UPLOAD_RELS:
        assert (trash / "uploads" / rel).is_file(), rel
        assert not (env.uploads / rel).exists(), rel
    assert (trash / "mcp" / f"user_{TGT_UID}" / "cfg.json").is_file()
    for rel in KEEP_UPLOAD_RELS:
        assert (env.uploads / rel).is_file(), rel
    assert (env.data / "mcp" / f"user_{OTH_UID}" / "cfg.json").is_file()
    assert not (env.data / "trash" / str(OTH_UID)).exists()

    # BM25：只按该账号的角色失效，邻居的缓存留着
    assert rep["bm25"]["invalidated"] == 1 and rep["bm25"]["character_ids"] == [TGT_CID]
    assert not (env.bm25_root / f"{TGT_CID}.json").exists()
    assert (env.bm25_root / f"{OTH_CID}.json").is_file()

    # 向量：收到的是第 0 步固化的 memory id（memories 行已删，这批 id 只能来自固化集合）
    assert env.vector_calls == [{"user_id": TGT_UID, "memory_ids": [MEM_TGT, MEM_TGT_B]}]
    assert rep["vectors"] == {"by_user": 1, "by_memory": 2, "unresolved": 0}

    # 作业账本 + 审计
    job = _job(env.dst)
    assert job["status"] == "done" and job["error"] is None
    assert job["started_at"] and job["finished_at"]
    cursor = json.loads(job["cursor_json"])
    assert cursor["frozen"]["session_ids"] == [SESS_TGT]
    assert cursor["tables_done"]["users"] == 1
    assert json.loads(job["report_json"])["rows_deleted"] == rep["rows_deleted"]
    entries = _audit(c)
    assert entries and entries[0]["target"] == f"user:{TGT_UID}"
    assert entries[0]["after"]["username"] == "tgt"
    assert entries[0]["after"]["rows_deleted"] == rep["rows_deleted"]
    assert entries[0]["after"]["job_id"] == rep["job_id"]
    # 审计是 append-only：删号不把自己的操作记录删掉
    assert _rows(env.dst, "admin_audit_log") >= 2


def test_purge_deletes_every_table_the_plan_listed(purge_env):
    """独立判据：dry-run 说会带走哪些表，真删之后那些表按同一列再数一次必须是 0。

    刻意不从清除器的报告里取清单（自证），而是取第一期 ``delete-dry-run`` 的清单——
    两期共用同一份 cascade 发现（``user_cascade.discover_purge_plan``），对不上就是 bug。
    """
    env = purge_env
    c = _client()
    dry = c.post(f"/api/v1/admin/server/accounts/{TGT_UID}/delete-dry-run",
                 headers=_auth(ACTOR_UID)).json()
    listed = sorted(t["table"] for t in dry["tables"]
                    if any(col["deletable"] for col in t["columns"]))
    assert "memories" in listed and "chat_sessions" in listed and "ai_characters" in listed
    assert _mark_deleted(c).status_code == 200
    assert _purge(c, confirm_username="tgt", force=True).status_code == 200
    for table in listed:
        for col in OWNERSHIP_COLS:
            if _has_col(env.dst, table, col):
                assert _rows(env.dst, table, f'"{col}" = {TGT_UID}') == 0, (table, col)
        if _has_col(env.dst, table, "character_id"):
            assert _rows(env.dst, table, f'"character_id" = {TGT_CID}') == 0, table
    # 别人的行不能陪葬
    assert _rows(env.dst, "users", "id=%d" % OTH_UID) == 1
    assert _rows(env.dst, "ai_characters", "user_id=%d" % OTH_UID) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 幂等：done 之后重跑
# ═══════════════════════════════════════════════════════════════════════════════

def test_purge_done_rerun_returns_stored_report(purge_env):
    env = purge_env
    c = _client()
    assert _mark_deleted(c).status_code == 200
    first = _purge(c, confirm_username="tgt", force=True).json()
    assert first["already_done"] is False
    before = _all_counts(env.dst)
    trash = env.data / "trash" / str(TGT_UID)
    trash_files = sum(1 for p in trash.rglob("*") if p.is_file())

    # 账号行已经没了 → 端点仍按账本认出「这个号早删完了」：直接返回、不报错、不再删
    again = _purge(c, confirm_username="tgt", force=True)
    assert again.status_code == 200, again.text
    second = again.json()
    assert second["already_done"] is True and second["status"] == "done"
    assert second["resumed"] is False
    assert second["tables"] == first["tables"]
    assert second["rows_deleted"] == first["rows_deleted"]
    assert _all_counts(env.dst) == before
    assert sum(1 for p in trash.rglob("*") if p.is_file()) == trash_files
    assert len(env.backups) == 1, "幂等重跑不该再备份一次"
    assert len(env.vector_calls) == 1, "幂等重跑不该再动向量层"
    assert len(_audit(c)) == 1, "幂等重跑不落新审计（什么都没改）"


# ═══════════════════════════════════════════════════════════════════════════════
# 护栏：全部 4xx 且零写入
# ═══════════════════════════════════════════════════════════════════════════════

def test_purge_requires_server_admin(purge_env):
    c = _client()
    assert _mark_deleted(c).status_code == 200
    body = {"confirm_username": "tgt", "force": True}
    for uid in (OTH_UID, TGT_UID):
        r = c.post(f"/api/v1/admin/server/accounts/{TGT_UID}/purge", headers=_auth(uid), json=body)
        assert r.status_code == 403, (uid, r.text)
    assert c.post(f"/api/v1/admin/server/accounts/{TGT_UID}/purge", json=body).status_code == 401


def test_purge_rejects_unmarked_account_even_with_force(purge_env):
    """没进回收站的账号一律不许清；``force`` 只放宽「宽限期未到」，不给未标记账号开后门。"""
    env = purge_env
    c = _client()
    before = _all_counts(env.dst)
    for body in ({"confirm_username": "other"}, {"confirm_username": "other", "force": True}):
        r = c.post(f"/api/v1/admin/server/accounts/{OTH_UID}/purge", headers=_auth(ACTOR_UID),
                   json=body)
        assert r.status_code == 400, r.text
        assert "回收站" in r.json()["detail"], r.text
    assert _all_counts(env.dst) == before
    assert _rows(env.dst, "users", "id=%d" % OTH_UID) == 1
    assert _job(env.dst, OTH_UID) == {}, "被拒的调用不留作业行"
    assert env.backups == [] and env.vector_calls == []


def test_purge_confirm_username_required_and_exact(purge_env):
    env = purge_env
    c = _client()
    assert _mark_deleted(c).status_code == 200
    before = _all_counts(env.dst)
    for bad in ({}, {"confirm_username": ""}, {"confirm_username": None},
                {"confirm_username": "TGT"}, {"confirm_username": " tgt"},
                {"confirm_username": "tg"}, {"confirm_username": "tgt "}):
        r = _purge(c, force=True, **bad)
        assert r.status_code == 400, bad
        assert _all_counts(env.dst) == before
    assert _rows(env.dst, "users", "id=%d" % TGT_UID) == 1
    assert _job(env.dst) == {}, "确认串不匹配时不留作业行"


def test_purge_respects_grace_period_unless_forced(purge_env):
    """``delete`` 只标记（purge_after = now+7d）→ purge 未到期拒绝；带 ``force`` 才放行。"""
    env = purge_env
    c = _client()
    marked = _mark_deleted(c).json()
    assert datetime.fromisoformat(marked["purge_after"]) > datetime.now(timezone.utc).replace(tzinfo=None)
    r = _purge(c, confirm_username="tgt")
    assert r.status_code == 400 and "宽限期" in r.json()["detail"], r.text
    assert _rows(env.dst, "users", "id=%d" % TGT_UID) == 1
    assert _purge(c, confirm_username="tgt", force=True).status_code == 200
    assert _rows(env.dst, "users", "id=%d" % TGT_UID) == 0


def test_purge_unknown_target_404(purge_env):
    r = _purge(_client(), 999, confirm_username="nobody")
    assert r.status_code == 404, r.text


# ═══════════════════════════════════════════════════════════════════════════════
# 第一期端点语义没被动过（派单红线）
# ═══════════════════════════════════════════════════════════════════════════════

def test_delete_purge_now_still_only_marks(purge_env):
    """``purge_now=true`` 仍然**只是**把 ``purge_after`` 设为 now，真删由 purge 端点执行。"""
    env = purge_env
    c = _client()
    r = _mark_deleted(c, purge_now=True)
    assert r.status_code == 200 and r.json()["purge_now"] is True
    assert _rows(env.dst, "users", "id=%d" % TGT_UID) == 1     # 一行没删
    assert _job(env.dst) == {}                                  # 清除器没被触发
    assert (env.data / "trash" / str(TGT_UID)).exists() is False
    # 到期之后不带 force 也能清 —— 这正是第二期调度器的调用形态
    ok = _purge(c, confirm_username="tgt")
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "done"


# ═══════════════════════════════════════════════════════════════════════════════
# 前置备份 fail-closed
# ═══════════════════════════════════════════════════════════════════════════════

def test_purge_aborts_when_backup_fails_and_deletes_nothing(purge_env, monkeypatch):
    """备份必炸 → 500 + 作业 failed + **一行都没删**（文件也没搬、向量没碰）。"""
    env = purge_env
    c = _client()
    assert _mark_deleted(c).status_code == 200
    before = _all_counts(env.dst)

    def _boom() -> str:
        raise RuntimeError("注入：磁盘写不进，备份失败")

    monkeypatch.setattr(account_purge, "_run_backup", _boom)
    r = _purge(c, confirm_username="tgt", force=True)
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "purge_failed" and "备份失败" in detail["error"]
    assert detail["report"]["status"] == "failed" and detail["report"]["rows_deleted"] == 0
    job = _job(env.dst)
    assert job["status"] == "failed" and "备份失败" in job["error"]
    assert _all_counts(env.dst) == before
    assert _rows(env.dst, "users", "id=%d" % TGT_UID) == 1
    for rel in TGT_UPLOAD_RELS:
        assert (env.uploads / rel).is_file(), rel
    assert not (env.data / "trash" / str(TGT_UID)).exists()
    assert env.vector_calls == []
    assert (env.bm25_root / f"{TGT_CID}.json").is_file(), "备份失败就不该走到 BM25"
    # 失败也落审计（谁在什么时候试过、为什么没成）
    assert _audit(c)[0]["after"]["status"] == "failed"

    # 备份恢复后同一条路走得通（failed 不是终态，重跑/续跑都允许）
    monkeypatch.setattr(account_purge, "_run_backup",
                        lambda: str(_mk(env.tmp_path / "backups" / "ok.zip")))
    ok = _purge(c, confirm_username="tgt", force=True)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "done" and ok.json()["resumed"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 向量层：桩之外的另一头（真实现的三条语义）
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeCollection:
    """最小 Chroma 面：``get(where|ids, include=)`` + ``delete(where|ids)``。

    向量文档 id 就是 ``str(memory_id)``。这里 60 号是「A1 之前入库、metadata 里没有
    ``user_id`` 键」的老向量：``where`` 永远匹配不到它，只能按 memory_id 反查补删；
    而它单条删时必抛错 → 同时验证「一个删不掉的 id 不拖垮整批」。
    """

    def __init__(self):
        self.ids = ["51", "52", "60"]
        self.by_user = ["51"]      # 只有 51 带 user_id metadata
        self.gets: list[dict] = []

    def get(self, where=None, ids=None, include=None):
        self.gets.append({"where": where, "ids": ids, "include": include})
        if where is not None:
            assert where == {"user_id": 2}, where
            return {"ids": [i for i in self.ids if i in self.by_user]}
        return {"ids": [i for i in (ids or []) if i in self.ids]}

    def delete(self, where=None, ids=None):
        if where is not None:
            self.ids = [i for i in self.ids if i not in self.by_user]
            self.by_user = []
            return
        if len(ids) > 1:
            raise RuntimeError("注入：整批删不动")
        if ids[0] == "60":
            raise RuntimeError("注入：这条 id 删不掉")
        self.ids.remove(ids[0])


def test_delete_memory_vectors_by_user_backfills_and_survives_poison(monkeypatch):
    coll = _FakeCollection()

    async def _open():
        return coll

    monkeypatch.setattr(vector_store, "get_or_create_collection", _open)
    out = asyncio.run(
        vector_store.delete_memory_vectors_by_user(2, memory_ids=[51, 52, 60, 999]))
    assert out["by_user"] == 1                       # 只有 51 带 user_id metadata
    assert out["by_memory"] == 1                     # 52 靠逐 id 兜底删掉了
    assert out["unresolved"] == 1                    # 60 删不掉 → 计入残留，不抛穿
    assert coll.ids == ["60"], coll.ids              # 毒 id 没拖垮同批的 52
    assert coll.gets[0]["include"] == []             # get 只要 id，别把向量文本读回来
    # 999 本就没有向量（get 里查不到）→ 不算残留


def test_delete_memory_vectors_by_user_never_raises(monkeypatch):
    """向量库整体不可用也不抛穿（删号主链不能被旁路存储拖死），计数全 0。"""
    async def _bad():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(vector_store, "get_or_create_collection", _bad)
    out = asyncio.run(vector_store.delete_memory_vectors_by_user(2, memory_ids=[1, 2]))
    assert out == {"by_user": 0, "by_memory": 0, "unresolved": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# uploads 根取的是真来源（静态挂载那一份），不是测试自造常量
# ═══════════════════════════════════════════════════════════════════════════════

def test_uploads_root_is_the_real_static_mount():
    from app.config import settings
    assert account_purge._data_dir() == Path(settings.PROJECT_ROOT) / "data"
    assert (account_purge._data_dir() / "uploads").name == "uploads"


# ═══════════════════════════════════════════════════════════════════════════════
# 迁移：account_purge_jobs 幂等 / 可逆 / 哨兵 / 版本链单头
# ═══════════════════════════════════════════════════════════════════════════════

def test_jobs_migration_adds_table_idempotent_and_reversible(tmp_path):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_jobs_migration()
    assert mig.revision == "f7b8c9d0e1f2" and mig.down_revision == "f6a7b8c9d0e1"

    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'mig.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50))"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
            mig.upgrade()          # 幂等：老库整链重放命中 has_table 守卫，不报错
        cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(account_purge_jobs)"))}
        assert set(mig.COLUMNS) == cols
        conn.execute(sa.text("INSERT INTO account_purge_jobs (user_id, status) VALUES (7,'running')"))
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("INSERT INTO account_purge_jobs (user_id) VALUES (7)"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            mig.downgrade()        # 幂等：表已不在也不报错
        assert not sa.inspect(conn).has_table("account_purge_jobs")
    engine.dispose()


def test_jobs_table_is_sentinel_and_chain_keeps_single_head():
    """新表必须登记进「当前 schema 哨兵」，否则老库会被 stamp 成「已当前」而永久缺表；
    版本链也要保持单头（分叉会让 ``upgrade head`` 直接报错）。"""
    from alembic.script import ScriptDirectory

    from app.db.migrate import _alembic_config, _migration_chain_tables

    assert ("account_purge_jobs", "user_id") in _get_sentinels()
    assert "account_purge_jobs" in _migration_chain_tables(_alembic_config())
    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == ["f7b8c9d0e1f2"]
    # 从 head 能回溯到第一期之前的既有修订（确认这条链没被截断）
    assert script.get_revision("f6a7b8c9d0e1") is not None


def _get_sentinels():
    from app.db import migrate
    return migrate._CURRENT_SCHEMA_SENTINELS
