# -*- coding: utf-8 -*-
"""断点 #4（U4，2026-09-29）：``phone_snapshots`` 归属三列 actor / character_id / confidence。

本批**只补数据面、零行为**，故用例分四组，各自钉住一条约束：

① 迁移层（``alembic/versions/d7e8f9a0b1c2``）：真跑 alembic 编程接口——老库补三列、历史行
   **不回填**（逐行 actor 仍 NULL）、只加列不重建表（原列序与主键 id 不变）、幂等（版本号退回
   重放 0 操作）、可逆（downgrade 只删三列，表/其余列/行都在）、再 upgrade 列补回、单头且新修订在链上。
   老库整链重放只做一次（module fixture），其余用例克隆该库，避免重复重放。
② 定义层：ORM 三列全部可空且无 server_default（老行无需回填），``character_id`` 挂
   ``ai_characters.id`` 且 ``ondelete=SET NULL``；三条哨兵已登记进 ``_CURRENT_SCHEMA_SENTINELS``。
③ 写入层：``api/phone.create_perception``（手机上报）落 ``actor='user'``、``character_id``
   与 ``confidence`` 留 NULL（**无角色来源就不臆造**）；三列可空不破坏既有插入路径；
   角色来源的快照与置信度在数据面上可写（为后续批次铺路），删角色时 ``character_id`` 置空。
④ 注入层（读）：``device.port`` 两个读入口的返回集合与改动前逐字一致（含角色来源行与历史
   NULL 行照样返回），并以捕获 SQL 的方式钉住「WHERE 只有 user_id、没加任何新条件」。

迁移层禁用 ``_dbclone`` 模板库（那是 create_all 出的当前 schema，跑迁移会假绿）；写入/注入层
用克隆库。全程只写 pytest ``tmp_path`` 下的临时库，绝不连生产库。
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Float, Integer, create_engine, event, inspect as sa_inspect, select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import phone as phone_api
from app.auth.deps import get_current_user_id
from app.db import database
from app.db.migrate import _CURRENT_SCHEMA_SENTINELS
from app.device import port
from app.models.character import AICharacter
from app.models.device import PhoneSnapshot
from app.models.user import User

NEW_REV = "d7e8f9a0b1c2"
PREV_REV = "a1c4e7f9b2d5"          # 本迁移 down_revision（交接前的版本链头）= 老库基线
TABLE = "phone_snapshots"
NEW_COLS = ("actor", "character_id", "confidence")
OLD_COLS = {"id", "user_id", "source", "content", "image_desc", "created_at", "payload_json"}

# 快测档：迁移层每例真跑 alembic（老库整链重放一次 + 克隆），属重量级
pytestmark = pytest.mark.slow

_NOW_UTC = datetime.now(timezone.utc).replace(tzinfo=None)


# ──────────────── ① 迁移层辅助（真跑 alembic，临时文件库） ────────────────

def _alembic_cfg() -> Config:
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    return cfg


def _sqlite_url(db_path) -> str:
    return "sqlite:///" + str(db_path).replace("\\", "/")


def _col_names(db_path) -> list[str]:
    """当前列序（PRAGMA table_info 口径：迁移加列一定追加在末尾）。"""
    eng = create_engine(_sqlite_url(db_path))
    try:
        insp = sa_inspect(eng)
        assert insp.has_table(TABLE), f"整链跑完 {TABLE} 必在（baseline 建表）"
        return [c["name"] for c in insp.get_columns(TABLE)]
    finally:
        eng.dispose()


def _alembic_version(db_path) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else ""


def _actors(db_path) -> list:
    with sqlite3.connect(str(db_path)) as conn:
        return [r[0] for r in conn.execute(f"SELECT actor FROM {TABLE} ORDER BY id")]


def _rows(db_path) -> list:
    """历史行本体（id/user_id/source/content）——用于「不重建表、不动数据」的对账。"""
    with sqlite3.connect(str(db_path)) as conn:
        return list(conn.execute(f"SELECT id, user_id, source, content FROM {TABLE} ORDER BY id"))


@pytest.fixture(scope="module")
def old_db(tmp_path_factory):
    """老库形态：整链重放到 PREV_REV（有表、无三列）+ 3 条历史快照行。

    整链重放只跑这一次（本机实测约 10 秒）；下面每个迁移用例把它 backup 成自己的文件库，
    upgrade/downgrade 互不干扰。
    """
    import app.config as app_cfg

    db_path = tmp_path_factory.mktemp("u4_old") / "old.db"
    url_path = str(db_path).replace("\\", "/")
    mp = pytest.MonkeyPatch()
    mp.setattr(app_cfg.settings, "database_url", "sqlite+aiosqlite:///" + url_path)
    try:
        command.upgrade(_alembic_cfg(), PREV_REV)
        assert not set(NEW_COLS) & set(_col_names(db_path)), "老库基线不该有归属三列"
        with sqlite3.connect(str(db_path)) as conn:
            for i in range(3):
                conn.execute(
                    f"INSERT INTO {TABLE} (user_id, source, content) VALUES (?,?,?)",
                    (1, ("clipboard", "accessibility", "notification")[i], f"历史内容{i}"),
                )
            conn.commit()
        assert len(_rows(db_path)) == 3
        yield db_path
    finally:
        mp.undo()


def _clone(old_db, tmp_path):
    """页级 backup 老库到用例自己的临时库（迁移用例之间必须互不可见）。"""
    dst = tmp_path / "u4_mig.db"
    src = sqlite3.connect(str(old_db))
    try:
        tgt = sqlite3.connect(str(dst))
        try:
            src.backup(tgt)
        finally:
            tgt.close()
    finally:
        src.close()
    return dst


@pytest.fixture()
def mig_db(old_db, tmp_path, monkeypatch):
    """本用例独占的老库副本，且把 alembic 的目标指到它（env.py 读 settings.database_url）。

    不这么做的话每个用例都在改同一份 old.db——upgrade/downgrade 互相踩，判据全废。
    """
    import app.config as app_cfg

    db = _clone(old_db, tmp_path)
    monkeypatch.setattr(
        app_cfg.settings, "database_url",
        "sqlite+aiosqlite:///" + str(db).replace("\\", "/"),
    )
    return db


def test_upgrade补三列且历史行不回填(mig_db):
    db = mig_db
    before = _rows(db)

    command.upgrade(_alembic_cfg(), NEW_REV)

    assert _alembic_version(db) == NEW_REV
    cols = _col_names(db)
    assert set(NEW_COLS) <= set(cols)
    # 只加列不重建表：原列全在且相对顺序不变，三列追加在末尾
    assert [c for c in cols if c not in NEW_COLS] == [c for c in cols if c in OLD_COLS]
    assert cols[-3:] == list(NEW_COLS)
    # 不回填、不改语义：3 行历史逐行 actor 仍 NULL
    assert _actors(db) == [None, None, None]
    assert _rows(db) == before
    # 原生 DDL 挂的 FK 必须真实落库（否则 SET NULL 只是 ORM 侧的一厢情愿）。
    # 按 PRAGMA 判定而不是按 SQLAlchemy 反射：内联（列级）REFERENCES 的 on_delete SQLAlchemy
    # 反射不回填 options（表级 FOREIGN KEY 子句才回填），PRAGMA foreign_key_list 两者都报。
    with sqlite3.connect(str(db)) as conn:
        fk_rows = [r for r in conn.execute(f"PRAGMA foreign_key_list({TABLE})") if r[3] == "character_id"]
    assert len(fk_rows) == 1, fk_rows
    _id, _seq, ref_table, _from, ref_col, on_update, on_delete, _match = fk_rows[0]
    assert (ref_table, ref_col) == ("ai_characters", "id")
    assert on_delete == "SET NULL", f"on_delete 非 SET NULL：{fk_rows[0]}"


def test_upgrade幂等_版本号退回重放不动数据(mig_db):
    db = mig_db
    command.upgrade(_alembic_cfg(), NEW_REV)
    before, cols_after = _rows(db), _col_names(db)

    # 版本号退回上一节但列还在（整链重放/重复 upgrade 的真实形态）→ 命中守卫 0 操作
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE alembic_version SET version_num=?", (PREV_REV,))
        conn.execute(f"INSERT INTO {TABLE} (user_id, source, content, actor) VALUES (1,'media','新行','user')")
        conn.commit()

    command.upgrade(_alembic_cfg(), NEW_REV)          # 不得报 "duplicate column name"
    assert _alembic_version(db) == NEW_REV
    assert _col_names(db) == cols_after               # 列不重复、顺序不变
    assert _rows(db)[:3] == before, "重放不得动历史行"
    assert _actors(db)[-1] == "user"                  # 新写入的值不被重放冲掉

    command.upgrade(_alembic_cfg(), NEW_REV)          # 已在 head 再跑一次，仍然 0 操作
    assert _col_names(db) == cols_after


def test_downgrade可逆只删三列(mig_db):
    db = mig_db
    command.upgrade(_alembic_cfg(), NEW_REV)
    command.downgrade(_alembic_cfg(), PREV_REV)

    assert _alembic_version(db) == PREV_REV
    back = set(_col_names(db))
    assert not back & set(NEW_COLS), f"downgrade 后仍残留 {back & set(NEW_COLS)}"
    assert OLD_COLS <= back, "表与其余列必须原样保留"
    assert len(_rows(db)) == 3                        # 删列不伤行


def test_回退后再upgrade列补回值为NULL(mig_db):
    db = mig_db
    command.upgrade(_alembic_cfg(), NEW_REV)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(f"UPDATE {TABLE} SET actor='user' WHERE id=1")
        conn.commit()
    assert _actors(db) == ["user", None, None]

    command.downgrade(_alembic_cfg(), PREV_REV)
    command.upgrade(_alembic_cfg(), NEW_REV)

    assert set(NEW_COLS) <= set(_col_names(db))
    assert _actors(db) == [None, None, None], "回退即丢归属记录（语义不可逆），重放不回填"


def test_版本链单头且新修订在链上():
    cfg = _alembic_cfg()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [NEW_REV], script.get_heads()
    revs = [r.revision for r in script.walk_revisions(base="base", head=NEW_REV)]
    assert NEW_REV in revs and PREV_REV in revs
    assert script.get_revision(NEW_REV).down_revision == PREV_REV


# ──────────────── ② 定义层：三列可空 / FK 级联 / 哨兵登记 ────────────────

def test_列定义可空无服务端默认():
    table = PhoneSnapshot.__table__
    for name in NEW_COLS:
        col = table.c[name]
        assert col.nullable is True, f"{name} 必须可空（老行无需回填）"
        assert col.server_default is None, f"{name} 不得有 server_default"
        assert col.default is None, f"{name} 不得有 Python 端默认值（写入侧显式传）"
    assert table.c.actor.type.length == 16
    assert isinstance(table.c.character_id.type, Integer)
    assert isinstance(table.c.confidence.type, Float)


def test_character_id外键与SET_NULL():
    fk = next(iter(PhoneSnapshot.__table__.c.character_id.foreign_keys))
    assert fk.column.table.name == "ai_characters"
    assert fk.ondelete == "SET NULL"


def test_哨兵三条已登记且不重复():
    for name in NEW_COLS:
        assert (TABLE, name) in _CURRENT_SCHEMA_SENTINELS, _CURRENT_SCHEMA_SENTINELS
    assert len(_CURRENT_SCHEMA_SENTINELS) == len(set(_CURRENT_SCHEMA_SENTINELS)), "哨兵不得有重复条目"


# ──────────────── ③④ 写入层 / 注入层辅助（克隆库＝当前 schema） ────────────────

@pytest.fixture()
def snap_db(monkeypatch, tmp_path):
    engine = clone_engine(tmp_path / "attribution.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(phone_api, "async_session_factory", factory)   # phone.py 模块级 from-import 已绑死
    monkeypatch.setattr(database, "async_session_factory", factory)    # port 延迟绑定 → patch 源头
    yield factory
    engine.sync_engine.dispose()


async def _seed(factory):
    async with factory() as db:
        db.add(User(id=1, username="u1", nickname="n1"))
        db.add(User(id=2, username="u2", nickname="n2"))
        db.add(AICharacter(id=101, user_id=1, name="角色101"))
        await db.commit()


async def _add_snap(factory, *, uid=1, source="clipboard", content="c", minutes_ago=0, **kw):
    async with factory() as db:
        db.add(PhoneSnapshot(
            user_id=uid, source=source, content=content,
            created_at=_NOW_UTC - timedelta(minutes=minutes_ago), **kw
        ))
        await db.commit()


async def _all(factory) -> list[PhoneSnapshot]:
    async with factory() as db:
        return list((await db.execute(select(PhoneSnapshot).order_by(PhoneSnapshot.id))).scalars().all())


def _make_client(user_id: int) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(phone_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_写入侧_手机上报落actor为user其余留NULL(snap_db):
    asyncio.run(_seed(snap_db))
    r = _make_client(1).post(
        "/api/v1/phone/perception",
        data={"source": "clipboard", "content": "复制了一段文字",
              "payload_json": '{"confidence": 0.9}'},
    )
    assert r.status_code == 200, r.text

    row = asyncio.run(_all(snap_db))[0]
    assert row.actor == "user", "手机上报的快照一律 user"
    assert row.character_id is None, "该入口没有角色来源，不臆造 character_id"
    # 载荷里的 confidence 属「文本」，写侧不解析入库（读侧已有 _extract_confidence，口径不变）
    assert row.confidence is None


def test_写入侧_三列可空不破坏既有插入(snap_db):
    """既有写入点（不传三列）照旧成功，取值为 NULL——老代码/老行都不受影响。"""
    asyncio.run(_seed(snap_db))
    asyncio.run(_add_snap(snap_db, source="accessibility", content="旧写法"))

    row = asyncio.run(_all(snap_db))[0]
    assert (row.actor, row.character_id, row.confidence) == (None, None, None)


def test_写入侧_角色来源与置信度在数据面可写(snap_db):
    asyncio.run(_seed(snap_db))
    asyncio.run(_add_snap(
        snap_db, source="clipboard", content="角色发起的采集",
        actor="character", character_id=101, confidence=0.75,
    ))

    row = asyncio.run(_all(snap_db))[0]
    assert (row.actor, row.character_id, row.confidence) == ("character", 101, 0.75)


def test_写入侧_删角色时character_id置空(snap_db):
    """ondelete=SET NULL 真生效（克隆库带 PRAGMA foreign_keys=ON，与生产同口径）。"""
    async def _go():
        await _seed(snap_db)
        await _add_snap(snap_db, actor="character", character_id=101, content="角色采集")
        async with snap_db() as db:
            char = await db.get(AICharacter, 101)
            await db.delete(char)
            await db.commit()
        return (await _all(snap_db))[0]

    row = asyncio.run(_go())
    assert row.character_id is None, "SET NULL：不连带删掉快照行本身"
    assert row.content == "角色采集"


# ──────────────── ④ 注入层：读侧零行为（仍只按 user 过滤） ────────────────

async def _seed_mixed(factory):
    """三种归属混排（本账号）：user 来源 / character 来源 / 历史 NULL；外加他人一行。"""
    await _seed(factory)
    await _add_snap(factory, source="clipboard", content="用户复制", minutes_ago=5, actor="user")
    await _add_snap(factory, source="notification", content="角色看的通知", minutes_ago=4,
                    actor="character", character_id=101)
    await _add_snap(factory, source="accessibility", content="老行无归属", minutes_ago=3)
    await _add_snap(factory, uid=2, source="clipboard", content="别人的剪贴板",
                    minutes_ago=1, actor="user")


def test_注入侧_read_perception_records集合与改动前一致(snap_db):
    asyncio.run(_seed_mixed(snap_db))
    records = asyncio.run(port.read_perception_records(1))

    # 角色来源行与历史 NULL 行**照样返回**（未收窄），他人行照旧被 user 过滤挡住
    assert [(r.source, r.raw_text) for r in records] == [
        ("accessibility", "老行无归属"),
        ("notification", "角色看的通知"),
        ("clipboard", "用户复制"),
    ]


def test_注入侧_read_capability照样命中角色来源的最新一条(snap_db):
    """能力读取面同样是零行为：最新一条哪怕是角色来源，也照常 ok 返回。"""
    async def _go():
        await _seed(snap_db)
        await _add_snap(snap_db, source="notification", content="用户的通知", minutes_ago=5, actor="user")
        await _add_snap(snap_db, source="notification", content="角色看的通知", minutes_ago=1,
                        actor="character", character_id=101)

    asyncio.run(_go())
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "ok", reading
    assert reading.value["raw_text"] == "角色看的通知"
    assert reading.confidence is None       # 取值口径不变（仍来自载荷，不看新列）


def test_注入侧_SQL未新增任何过滤条件(tmp_path, monkeypatch):
    """钉住「零行为」：捕获实际下发的 SQL——SELECT 列与 WHERE 里都不得出现三列的名字。

    按 SQL 文本判定而不是按源码文本：源码里那处「后续批次」注释不该被算成过滤条件。
    """
    engine = clone_engine(tmp_path / "sql_capture.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(database, "async_session_factory", factory)
    captured: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _cap(conn, cursor, statement, params, context, executemany):
        captured.append(statement)

    async def _go():
        await _seed(factory)
        await _add_snap(factory, content="一条")
        await port.read_perception_records(1)
        await port.read_capability("notifications", 1)

    try:
        asyncio.run(_go())
    finally:
        engine.sync_engine.dispose()

    reads = [s for s in captured if TABLE in s and s.strip().upper().startswith("SELECT")]
    assert len(reads) >= 2, f"没捕到两个端口读的查询，用例失效了：{reads}"
    for sql in reads:
        # 只看 WHERE 段（大小写/换行都可能变）：read_capability 整实体 select，SELECT 列里出现
        # 三列名属正常取值，判定必须落在过滤条件上
        parts = re.split(r"\s+where\s+", sql.lower(), maxsplit=1)
        assert len(parts) == 2, f"端口读出现无过滤的全表查询：{sql}"
        where = re.split(r"\s+(?:order by|limit)\s", parts[1], maxsplit=1)[0]
        assert "user_id" in where, f"user 过滤被改动：{sql}"
        for name in NEW_COLS:
            assert name not in where, f"读侧新增了过滤条件 {name}：{sql}"
