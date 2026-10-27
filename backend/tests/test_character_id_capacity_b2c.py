# -*- coding: utf-8 -*-
"""B2c：角色 ID 客户端安全上限的数据库守卫、创建错误映射与并发边界。"""
import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.characters import create_character as api_create_character
from app.application.character_creation_idempotency import create_character_once
from app.application.characters import create_character as svc_create_character
from app.db.character_id_guard import (
    CHECK_NAME,
    CLIENT_SAFE_MAX_ID,
    INSERT_TRIGGER,
    UPDATE_TRIGGER,
    CharacterIdCapacityExceeded,
    ensure_client_safe_id_guard,
    is_capacity_error,
    verify_client_safe_id_guard,
)
from app.schemas.character import CharacterCreate

BACKEND = Path(__file__).resolve().parents[1]


def _make_full_db(tmp_path: Path) -> Path:
    import app.models._all  # noqa: F401
    from app.models.base import Base

    db = tmp_path / "full.db"
    eng = create_engine("sqlite:///" + db.as_posix())
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO users(id,username,nickname) VALUES(1,'b2c','B')")
    con.commit()
    con.close()
    return db


def _seed_character(con, cid: int, name: str = "seed"):
    con.execute(
        "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
        "VALUES(?,1,?,0,1,1,1,0)",
        (cid, name),
    )


def _insert_auto(con, name: str):
    return con.execute(
        "INSERT INTO ai_characters(user_id,name,is_partner,is_active,"
        "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
        "VALUES(1,?,0,1,1,1,0)",
        (name,),
    ).lastrowid


def _verify(db: Path) -> dict:
    eng = create_engine("sqlite:///" + db.as_posix())
    try:
        with eng.connect() as conn:
            return verify_client_safe_id_guard(conn)
    finally:
        eng.dispose()


def _ensure(db: Path) -> dict:
    eng = create_engine("sqlite:///" + db.as_posix())
    try:
        with eng.begin() as conn:
            return ensure_client_safe_id_guard(conn)
    finally:
        eng.dispose()


def test_create_all_has_check_and_blocks_auto_explicit_update(tmp_path):
    db = _make_full_db(tmp_path)
    con = sqlite3.connect(str(db))
    ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    assert CHECK_NAME in ddl
    _seed_character(con, CLIENT_SAFE_MAX_ID, "max")
    con.commit()
    con.close()
    assert _verify(db)["mode"] == "check"

    con = sqlite3.connect(str(db))
    with pytest.raises(sqlite3.IntegrityError) as exc:
        _insert_auto(con, "overflow-auto")
    con.rollback()
    assert is_capacity_error(exc.value)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO ai_characters(id,user_id,name,is_partner,is_active,"
            "cognitive_loop_enabled,memory_v2_enabled,talkativeness_locked) "
            "VALUES(?,1,'explicit',0,1,1,1,0)",
            (CLIENT_SAFE_MAX_ID + 2,),
        )
    con.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE ai_characters SET id=? WHERE name='max'", (CLIENT_SAFE_MAX_ID + 2,))
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM ai_characters").fetchone()[0] == 1
    assert con.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'"
    ).fetchone()[0] == CLIENT_SAFE_MAX_ID
    con.close()


def test_existing_autoincrement_without_check_gets_after_triggers(tmp_path):
    db = tmp_path / "auto.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    con.execute("INSERT INTO ai_characters(id,name) VALUES(?, 'seed')", (CLIENT_SAFE_MAX_ID - 1,))
    con.commit()
    con.close()
    assert _verify(db)["mode"] == "none"

    first = _ensure(db)
    assert first["mode"] == "triggers", first
    con = sqlite3.connect(str(db))
    sql_before = dict(con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='ai_characters'"
    ).fetchall())
    con.close()
    second = _ensure(db)
    assert second["mode"] == "triggers" and sorted(second["triggers"]) == sorted(
        [INSERT_TRIGGER, UPDATE_TRIGGER]
    )
    con = sqlite3.connect(str(db))
    sql_after = dict(con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='ai_characters'"
    ).fetchall())
    assert sql_after == sql_before
    assert con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='ai_characters'"
    ).fetchone()[0] == 2
    assert con.execute("INSERT INTO ai_characters(name) VALUES('last')").lastrowid == CLIENT_SAFE_MAX_ID
    con.commit()
    with pytest.raises(sqlite3.IntegrityError) as exc:
        con.execute("INSERT INTO ai_characters(name) VALUES('overflow')")
    con.rollback()
    assert is_capacity_error(exc.value)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE ai_characters SET id=? WHERE name='seed'", (CLIENT_SAFE_MAX_ID + 2,))
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM ai_characters").fetchone()[0] == 2
    assert con.execute(
        "SELECT MAX(id) FROM ai_characters"
    ).fetchone()[0] == CLIENT_SAFE_MAX_ID
    con.close()


def test_existing_overlimit_data_refused_without_delete(tmp_path):
    db = tmp_path / "over.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    con.execute("INSERT INTO ai_characters(id,name) VALUES(?, 'over')", (CLIENT_SAFE_MAX_ID + 2,))
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="client-safe"):
        _ensure(db)
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT id FROM ai_characters").fetchall() == [(CLIENT_SAFE_MAX_ID + 2,)]
    con.close()
    assert _verify(db)["mode"] == "none"


def test_capacity_error_classification_is_not_generic_integrity():
    assert is_capacity_error(
        sqlite3.IntegrityError("CHECK constraint failed: ck_ai_characters_client_safe_id")
    )
    assert is_capacity_error(sqlite3.IntegrityError("character_id_capacity_exhausted"))
    assert not is_capacity_error(sqlite3.IntegrityError("FOREIGN KEY constraint failed"))
    # SQL 参数/语句里的标记不得污染分类；只认底层驱动错误。
    fk = IntegrityError(
        "INSERT INTO ai_characters(name) VALUES(?)",
        {"name": "ck_ai_characters_client_safe_id character_id_capacity_exhausted"},
        sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
    )
    assert not is_capacity_error(fk)
    unique = IntegrityError(
        "INSERT INTO ai_characters(name) VALUES(?)",
        {"name": "ck_ai_characters_client_safe_id character_id_capacity_exhausted"},
        sqlite3.IntegrityError("UNIQUE constraint failed: ai_characters.name"),
    )
    assert not is_capacity_error(unique)
    check = IntegrityError(
        "INSERT INTO ai_characters(name) VALUES(?)",
        {"name": "x"},
        sqlite3.IntegrityError(
            "CHECK constraint failed: ck_ai_characters_client_safe_id"
        ),
    )
    assert is_capacity_error(check)


def test_service_capacity_raises_domain_and_rolls_back(tmp_path):
    db = _make_full_db(tmp_path)
    con = sqlite3.connect(str(db))
    _seed_character(con, CLIENT_SAFE_MAX_ID, "max")
    con.commit()
    con.close()

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite:///" + db.as_posix())
        Session = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with Session() as session:
                with pytest.raises(CharacterIdCapacityExceeded):
                    await svc_create_character(session, CharacterCreate(name="overflow"), 1)
                await session.rollback()
        finally:
            await eng.dispose()

    asyncio.run(_run())
    con = sqlite3.connect(str(db))
    assert con.execute(
        "SELECT COUNT(*) FROM ai_characters WHERE name='overflow'"
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'"
    ).fetchone()[0] == CLIENT_SAFE_MAX_ID
    con.close()


def test_idempotent_capacity_rolls_back_receipt_and_replays_success(tmp_path):
    db = _make_full_db(tmp_path)

    async def _create_then_replay():
        eng = create_async_engine("sqlite+aiosqlite:///" + db.as_posix())
        Session = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with Session() as session:
                created = await create_character_once(
                    session, CharacterCreate(name="keep"), 1, "key-keep"
                )
                await session.commit()
                cid = created.id
                assert cid < CLIENT_SAFE_MAX_ID
            con = sqlite3.connect(str(db))
            con.execute(
                "UPDATE sqlite_sequence SET seq=? WHERE name='ai_characters'",
                (CLIENT_SAFE_MAX_ID,),
            )
            con.commit()
            con.close()
            async with Session() as session:
                replay = await create_character_once(
                    session, CharacterCreate(name="keep"), 1, "key-keep"
                )
                assert replay.id == cid
                with pytest.raises(CharacterIdCapacityExceeded):
                    await create_character_once(
                        session, CharacterCreate(name="new"), 1, "key-new"
                    )
                await session.rollback()
        finally:
            await eng.dispose()

    asyncio.run(_create_then_replay())
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM ai_characters").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM character_creation_requests").fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM character_creation_requests WHERE state='pending'"
    ).fetchone()[0] == 0
    con.close()


def test_api_maps_capacity_to_stable_409(tmp_path):
    db = _make_full_db(tmp_path)
    con = sqlite3.connect(str(db))
    _seed_character(con, CLIENT_SAFE_MAX_ID, "max")
    con.commit()
    con.close()

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite:///" + db.as_posix())
        Session = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with Session() as session:
                with pytest.raises(Exception) as exc:
                    await api_create_character(
                        CharacterCreate(name="overflow"), db=session, user_id=1,
                        idempotency_key=None,
                    )
                assert getattr(exc.value, "status_code", None) == 409
                assert getattr(exc.value, "detail", None) == "character_id_capacity_exhausted"
                await session.rollback()
            async with Session() as session:
                with pytest.raises(Exception) as exc:
                    await api_create_character(
                        CharacterCreate(name="overflow-key"), db=session, user_id=1,
                        idempotency_key="capacity-key",
                    )
                assert getattr(exc.value, "status_code", None) == 409
                assert getattr(exc.value, "detail", None) == "character_id_capacity_exhausted"
                await session.rollback()
        finally:
            await eng.dispose()

    asyncio.run(_run())
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM character_creation_requests").fetchone()[0] == 0
    con.close()


def test_two_connections_cross_boundary_exactly_one_wins(tmp_path):
    db = _make_full_db(tmp_path)
    con = sqlite3.connect(str(db))
    _seed_character(con, CLIENT_SAFE_MAX_ID - 1, "seed")
    con.commit()
    con.close()

    barrier = threading.Barrier(2)
    results = []

    def _worker(i: int):
        con = sqlite3.connect(str(db), timeout=10, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout=10000")
            barrier.wait()
            try:
                rid = _insert_auto(con, f"concurrent-{i}")
                results.append({"worker": i, "result": "ok", "id": rid})
            except sqlite3.Error as exc:
                results.append({"worker": i, "result": "error", "error": str(exc)})
        finally:
            con.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    out = BACKEND.parent / "logs" / "concurrency_b2c.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok = [r for r in results if r["result"] == "ok"]
    err = [r for r in results if r["result"] == "error"]
    assert len(ok) == 1 and len(err) == 1, results
    assert ok[0]["id"] == CLIENT_SAFE_MAX_ID
    assert "character_id_capacity_exhausted" in err[0]["error"] or CHECK_NAME in err[0]["error"]
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM ai_characters WHERE id > ?", (CLIENT_SAFE_MAX_ID,)).fetchone()[0] == 0
    assert con.execute("SELECT MAX(id) FROM ai_characters").fetchone()[0] == CLIENT_SAFE_MAX_ID
    assert con.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'"
    ).fetchone()[0] == CLIENT_SAFE_MAX_ID
    con.close()


def _strip_check(db: Path) -> None:
    """模拟 B2b 已升级但没有 B2c CHECK 的 AUTOINCREMENT 表（仅测试 setup）。"""
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys=OFF")
    ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    stripped = ddl.replace(
        'CONSTRAINT ck_ai_characters_client_safe_id CHECK (id <= 9007199254740991), ', ''
    ).replace(
        ', CONSTRAINT ck_ai_characters_client_safe_id CHECK (id <= 9007199254740991)', ''
    ).replace(
        'CONSTRAINT ck_ai_characters_client_safe_id CHECK (id <= 9007199254740991)', ''
    )
    assert "ck_ai_characters_client_safe_id" not in stripped
    stripped = stripped.replace(
        'CREATE TABLE ai_characters', 'CREATE TABLE ai_characters_no_check', 1
    )
    cols = [r[1] for r in con.execute("PRAGMA table_info(ai_characters)").fetchall()]
    col_sql = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
    con.execute(stripped)
    con.execute(f"INSERT INTO ai_characters_no_check ({col_sql}) SELECT {col_sql} FROM ai_characters")
    con.execute("DROP TABLE ai_characters")
    con.execute("ALTER TABLE ai_characters_no_check RENAME TO ai_characters")
    con.commit()
    con.close()


def test_startup_on_candidate_head_without_check_installs_triggers(tmp_path, monkeypatch):
    """B2c CHECK 缺失 ⇒ startup 装触发器（``already_at_head+guard:triggers``）。

    2026-10-23 Qwen 测试专批：这条拒绝分支**只有在 version==head 时才走**（否则
    ``_ensure_alembic_revision_sync_inner`` 会先补跑迁移）。b2a 现在有后代
    ``c4e6f8a0b2d4``（memories.write_key），真 head 已经不是 CANDIDATE——所以这里
    版本要落 **d6**（C9持久完成证据后代），函数名里的 "candidate_head" 保留成历史叫法，含义是
    「AUTOINCREMENT 已装、B2c CHECK 未装、启动链应识别并补 guard」。
    `_make_full_db` 走 create_all，schema 天然带 AUTOINCREMENT + write_key；
    `_strip_check` 只抹掉 CHECK，正对应「已到 head 但缺 B2c」这一状态。
    """
    from _memory_migration_fixture import read_chain
    from app.config import settings
    from app.db import migrate

    _chain = read_chain(BACKEND / "alembic" / "versions")
    _parents = {down for (_p, down) in _chain.values() if down is not None}
    _real_head = next(rev for rev in _chain if rev not in _parents)
    assert _real_head == "d6f8a0b2c4e6", (
        f"仓库迁移链应为 d6→c4→b2a，实得 head={_real_head}；"
        "若这条断了说明迁移链被截短，本用例的版本目标需要重审"
    )

    assert _chain[_real_head][1] == "c4e6f8a0b2d4"
    assert _chain["c4e6f8a0b2d4"][1] == "b2a0c1d2e3f4"

    db = _make_full_db(tmp_path)
    _strip_check(db)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version(version_num) VALUES(?)", (_real_head,))
    con.commit()
    con.close()
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///" + db.as_posix())
    monkeypatch.setattr(settings, "chroma_persist_dir", str((tmp_path / "vector").resolve()))
    action = migrate._ensure_alembic_revision_sync()
    assert action.startswith("already_at_head"), action
    assert "+guard:triggers" in action, action
    assert _verify(db)["mode"] == "triggers"
    before = sqlite3.connect(str(db))
    sql_before = dict(before.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='ai_characters'"
    ).fetchall())
    before.close()
    again = migrate._ensure_alembic_revision_sync()
    assert again.startswith("already_at_head"), again
    assert "+guard:triggers" in again, again
    con = sqlite3.connect(str(db))
    sql_after = dict(con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='ai_characters'"
    ).fetchall())
    assert sql_after == sql_before
    con.close()


def test_startup_on_create_all_verifies_check_and_is_idempotent(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import migrate

    db = _make_full_db(tmp_path)
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///" + db.as_posix())
    monkeypatch.setattr(settings, "chroma_persist_dir", str((tmp_path / "vector").resolve()))
    first = migrate._ensure_alembic_revision_sync()
    assert "+guard:check" in first, first
    assert _verify(db)["mode"] == "check"
    before = sqlite3.connect(str(db))
    ddl_before = before.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    before.close()
    second = migrate._ensure_alembic_revision_sync()
    assert second.startswith("already_at_head"), second
    assert "+guard:check" in second, second
    con = sqlite3.connect(str(db))
    ddl_after = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()[0]
    assert ddl_after == ddl_before
    assert con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='ai_characters'"
    ).fetchone()[0] == 0
    con.close()


def test_two_connections_trigger_guard_exactly_one_wins(tmp_path):
    db = tmp_path / "trigger_concurrent.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    con.execute("INSERT INTO ai_characters(id,name) VALUES(?, 'seed')", (CLIENT_SAFE_MAX_ID - 1,))
    con.commit()
    con.close()
    assert _ensure(db)["mode"] == "triggers"

    barrier = threading.Barrier(2)
    results = []

    def _worker(i: int):
        c = sqlite3.connect(str(db), timeout=10, isolation_level=None)
        try:
            c.execute("PRAGMA busy_timeout=10000")
            barrier.wait()
            try:
                rid = c.execute(
                    "INSERT INTO ai_characters(name) VALUES(?)", (f"t{i}",)
                ).lastrowid
                results.append({"worker": i, "result": "ok", "id": rid})
            except sqlite3.Error as exc:
                results.append({"worker": i, "result": "error", "error": str(exc)})
        finally:
            c.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    out = BACKEND.parent / "logs" / "concurrency_trigger_b2c.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = [r for r in results if r["result"] == "ok"]
    err = [r for r in results if r["result"] == "error"]
    assert len(ok) == 1 and len(err) == 1, results
    assert ok[0]["id"] == CLIENT_SAFE_MAX_ID
    assert "character_id_capacity_exhausted" in err[0]["error"]
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM ai_characters WHERE id > ?", (CLIENT_SAFE_MAX_ID,)).fetchone()[0] == 0
    assert con.execute("SELECT MAX(id) FROM ai_characters").fetchone()[0] == CLIENT_SAFE_MAX_ID
    con.close()


def test_api_does_not_map_non_capacity_integrity(tmp_path, monkeypatch):
    import app.api.characters as api_mod

    db = _make_full_db(tmp_path)
    exc = IntegrityError(
        "INSERT INTO ai_characters(name) VALUES(?)",
        {"name": "ck_ai_characters_client_safe_id character_id_capacity_exhausted"},
        sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
    )

    async def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(api_mod._svc, "create_character", _boom)
    import app.application.character_creation_idempotency as idem_mod
    monkeypatch.setattr(idem_mod, "create_character_once", _boom)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite:///" + db.as_posix())
        Session = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with Session() as session:
                with pytest.raises(IntegrityError) as caught:
                    await api_create_character(
                        CharacterCreate(name="fk"), db=session, user_id=1,
                        idempotency_key=None,
                    )
                assert not hasattr(caught.value, "status_code")
                with pytest.raises(IntegrityError) as caught2:
                    await api_create_character(
                        CharacterCreate(name="fk-key"), db=session, user_id=1,
                        idempotency_key="fk-key",
                    )
                assert not hasattr(caught2.value, "status_code")
        finally:
            await eng.dispose()

    asyncio.run(_run())
