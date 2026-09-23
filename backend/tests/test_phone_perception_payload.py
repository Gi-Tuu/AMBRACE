# -*- coding: utf-8 -*-
"""X7-M1 结构化承载（派单 P7/P8 A 段）：``phone_snapshots.payload_json`` 端到端回归。

覆盖三块：
① 接口层（``api/phone.create_perception``）：``payload_json`` 为可选 Form 字段，**只接受合法
   JSON 对象且长度 ≤ 4000**；非法（坏 JSON / 数组 / 裸量）与超长一律**只丢该字段**、快照照常写库
   （客户端脏数据不得让一次采集整体失败）；不传该字段的旧客户端行为逐字不变。
   P9 追加两块：POST 返回的 ``snapshot`` 与 ``GET /perception/recent`` 都**回显** ``payload_json``
   （原样字符串，无则 ``None``）；去重条件从「同 source + 同 content + 5 分钟」收紧为
   **四条件全同（含 ``payload_json`` 也相同，NULL 视为相同）**——同正文不同载荷不再被吞。
② 读取层（``device.port``）：``read_capability`` 的 ``value['structured']`` 与
   ``read_perception_records`` 的窗口 / 去重 / 租户 / 降级口径。
③ 迁移层（``alembic/versions/f4a5b6c7d8e9``）：**旧库/新库两种形态**——
   旧库（``phone_snapshots`` 无该列、版本停在交接点）upgrade head → 补列 + 老行不丢 + downgrade 可逆；
   新库（init_db ``create_all`` 已含该列）→ 命中守卫 0 操作；整链重放落位新 head 且仍为单头。

接口/读取层用 tests/_dbclone.py 克隆库（当前 schema）；迁移层**真跑 alembic**，故自己从空库/
create_all 库起步，不碰克隆库（_dbclone 模块 docstring：迁移类测试禁用克隆库，否则假绿）。
全程只写 pytest ``tmp_path`` 下的临时库，绝不触碰 backend/data/sqlite/ai_companion.db。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import phone as phone_api
from app.auth.deps import get_current_user_id
from app.db import database
from app.device import port
from app.models.device import PhoneSnapshot
from app.models.user import User

NEW_REV = "f4a5b6c7d8e9"
PREV_HEAD = "d1e2f3a4b5c6"   # 本迁移 down_revision（交接前的版本链头）= 老库基线
TABLE = "phone_snapshots"

# 快测档：接口/读取层每例起临时库，迁移层每例真跑整链重放，均属重量级（docs/engineering-protocol.md 十八）
pytestmark = pytest.mark.slow

_NOW_UTC = datetime.now(timezone.utc).replace(tzinfo=None)


# ── ①② 接口/读取层辅助（克隆库＝当前 schema）──

@pytest.fixture()
def phone_db(monkeypatch, tmp_path):
    """临时库（含 payload_json）：接口写侧 + 端口读侧指到同一份。"""
    engine = clone_engine(tmp_path / "payload.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(phone_api, "async_session_factory", factory)   # phone.py 模块级 from-import 已绑死
    monkeypatch.setattr(database, "async_session_factory", factory)    # port 延迟绑定 → patch 源头
    yield factory
    engine.sync_engine.dispose()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(phone_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _post(client: TestClient, source: str, content: str, payload=None):
    data = {"source": source, "content": content}
    if payload is not None:
        data["payload_json"] = payload
    return client.post("/api/v1/phone/perception", data=data)


async def _add_user(factory, uid: int):
    async with factory() as db:
        db.add(User(id=uid, username=f"u{uid}", nickname=f"n{uid}"))
        await db.commit()


async def _add_snap(factory, uid, source, content, *, minutes_ago=0, payload=None):
    ts = _NOW_UTC - timedelta(minutes=minutes_ago)
    async with factory() as db:
        db.add(PhoneSnapshot(
            user_id=uid, source=source, content=content, created_at=ts, payload_json=payload
        ))
        await db.commit()


async def _rows(factory, user_id: int | None = None):
    async with factory() as db:
        stmt = select(PhoneSnapshot)
        if user_id is not None:
            stmt = stmt.where(PhoneSnapshot.user_id == user_id)
        return list((await db.execute(stmt)).scalars().all())


def _one_row(factory, user_id: int) -> PhoneSnapshot:
    rows = asyncio.run(_rows(factory, user_id))
    assert len(rows) == 1, f"期望恰好写入 1 条快照，实际 {len(rows)}"
    return rows[0]


# ── ① 接口层：合法对象入库，非法/超长只丢字段 ──

def test_合法对象载荷入库并被端口读出(phone_db):
    asyncio.run(_add_user(phone_db, 1))
    payload = json.dumps({"app": "微信", "title": "张三", "text": "在吗"}, ensure_ascii=False)
    r = _post(_make_client(1), "notification", "张三发来一条消息", payload)
    assert r.status_code == 200, r.text

    assert _one_row(phone_db, 1).payload_json == payload
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.value == {"raw_text": "张三发来一条消息", "structured": json.loads(payload)}


@pytest.mark.parametrize("脏载荷", [
    "{不是 JSON",                          # 语法坏
    '["微信","张三"]',                      # 合法 JSON，但不是对象
    '"就一个字符串"',                        # 裸字符串
    "42",                                  # 裸数字
    "null",                                # 裸 null
    '{"k":"' + "x" * 4100 + '"}',          # 合法对象但超 4000（4108 字符）
])
def test_非法或超长载荷只丢字段快照照常写(phone_db, 脏载荷):
    asyncio.run(_add_user(phone_db, 1))
    r = _post(_make_client(1), "clipboard", "复制了一段验证码", 脏载荷)
    assert r.status_code == 200, r.text
    row = _one_row(phone_db, 1)
    assert row.content == "复制了一段验证码"      # 正文照旧入库
    assert row.payload_json is None               # 只丢结构化字段


def test_恰好4000字符的对象仍入库(phone_db):
    """上限是「≤4000」——恰好 4000 字符的对象必须放行（边界不得反）。"""
    body = '{"k":"' + "x" * (4000 - 8) + '"}'
    assert len(body) == 4000
    asyncio.run(_add_user(phone_db, 1))
    assert _post(_make_client(1), "clipboard", "边界载荷", body).status_code == 200
    assert _one_row(phone_db, 1).payload_json == body


def test_空对象载荷入库后按无载荷渲染(phone_db):
    """``{}`` 是合法 JSON 对象（入库）；但渲染按「拿不到字段」处理 → 回落旧文本行。"""
    from app.agent.context.section_phone import phone_perception_section

    asyncio.run(_add_user(phone_db, 1))
    assert _post(_make_client(1), "clipboard", "空对象载荷", "{}").status_code == 200
    assert _one_row(phone_db, 1).payload_json == "{}"
    assert asyncio.run(phone_perception_section({"user_id": 1}, {})) == "[剪贴板 0分钟前] 空对象载荷"


def test_不传载荷的旧客户端行为不变(phone_db):
    asyncio.run(_add_user(phone_db, 1))
    assert _post(_make_client(1), "accessibility", "屏幕上的文字").status_code == 200
    assert _one_row(phone_db, 1).payload_json is None
    assert asyncio.run(port.read_capability("foreground_app", 1)).value == {
        "raw_text": "屏幕上的文字", "structured": None
    }


# ── ①b 回显：接口返回结构里必须带 payload_json（P9 第 2 条）──

def test_接口回显结构化载荷字符串(phone_db):
    """POST 返回的 snapshot 与 GET /perception/recent 都原样回显入库的载荷字符串。"""
    asyncio.run(_add_user(phone_db, 1))
    payload = json.dumps({"title": "妈妈", "text": "吃饭了吗"}, ensure_ascii=False)
    r = _post(_make_client(1), "notification", "妈妈发来消息", payload)
    assert r.status_code == 200, r.text
    assert r.json()["snapshot"]["payload_json"] == payload

    snaps = _make_client(1).get("/api/v1/phone/perception/recent").json()["snapshots"]
    assert [s["payload_json"] for s in snaps] == [payload]


def test_接口回显无载荷为None(phone_db):
    """不传载荷 → None；脏载荷被丢弃后同样按 None 回显（回显口径 == 入库口径）。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    assert _post(client, "clipboard", "无载荷的正文").json()["snapshot"]["payload_json"] is None
    assert _post(client, "clipboard", "脏载荷的正文", "不是JSON").json()["snapshot"]["payload_json"] is None

    snaps = client.get("/api/v1/phone/perception/recent").json()["snapshots"]
    assert [s["payload_json"] for s in snaps] == [None, None]


# ── ①c 去重口径：载荷也是条件之一（P9 第 3 条）──

def test_同内容同载荷仍在窗口内去重(phone_db):
    """四个条件（user/source/content/payload）全同 + 5 分钟内 → 依旧去重（补传不挤出 MAX_KEEP）。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    assert _post(client, "clipboard", "同一段话", '{"k":1}').json().get("deduped") is not True
    assert _post(client, "clipboard", "同一段话", '{"k":1}').json() == {"status": "ok", "deduped": True}
    assert len(asyncio.run(_rows(phone_db, 1))) == 1


def test_同内容双方都无载荷仍去重(phone_db):
    """payload 同为 NULL 也算「相同」——旧客户端（不带载荷）的去重行为逐字不变。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    assert _post(client, "clipboard", "同一段话").json().get("deduped") is not True
    assert _post(client, "clipboard", "同一段话").json() == {"status": "ok", "deduped": True}
    assert len(asyncio.run(_rows(phone_db, 1))) == 1


def test_同内容不同载荷不被吞(phone_db):
    """正文相同但结构化载荷不同＝两次不同采集：第二条必须入库（旧口径会把它吞掉）。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    assert _post(client, "clipboard", "同一段话", '{"k":1}').json().get("deduped") is not True
    r2 = _post(client, "clipboard", "同一段话", '{"k":2}')
    assert r2.json().get("deduped") is not True
    assert r2.json()["snapshot"]["payload_json"] == '{"k":2}'
    rows = asyncio.run(_rows(phone_db, 1))
    assert len(rows) == 2
    assert {r.payload_json for r in rows} == {'{"k":1}', '{"k":2}'}


def test_同内容有无载荷不同也不被吞(phone_db):
    """一条带载荷、一条不带（NULL）：载荷不同 → 不判重。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    assert _post(client, "notification", "张三发来一条消息").json().get("deduped") is not True
    r2 = _post(client, "notification", "张三发来一条消息", '{"title":"张三"}')
    assert r2.json().get("deduped") is not True
    rows = asyncio.run(_rows(phone_db, 1))
    assert len(rows) == 2
    assert {r.payload_json for r in rows} == {None, '{"title":"张三"}'}


def test_脏载荷按入库后的NULL参与去重(phone_db):
    """比较用的是「清洗后」的载荷：两条脏载荷都被丢弃成 NULL → 第二条仍判重。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    assert _post(client, "clipboard", "同一段话", "坏JSON").json().get("deduped") is not True
    assert _post(client, "clipboard", "同一段话", '["裸数组"]').json() == {"status": "ok", "deduped": True}
    rows = asyncio.run(_rows(phone_db, 1))
    assert len(rows) == 1 and rows[0].payload_json is None


# ── ② 读取层：read_perception_records 口径 ──

def test_记录读取每来源只留最新且丢弃超时(phone_db):
    asyncio.run(_add_user(phone_db, 2))
    asyncio.run(_add_snap(phone_db, 2, "notification", "两小时前的通知", minutes_ago=120))
    asyncio.run(_add_snap(phone_db, 2, "notification", "最新通知", minutes_ago=1, payload='{"title":"妈妈"}'))
    asyncio.run(_add_snap(phone_db, 2, "notification", "夹在中间的", minutes_ago=3))
    asyncio.run(_add_snap(phone_db, 2, "accessibility", "屏幕内容", minutes_ago=2))
    asyncio.run(_add_snap(phone_db, 2, "clipboard", "刚复制的", minutes_ago=5))

    records = asyncio.run(port.read_perception_records(2))
    got = [(r.source, r.raw_text, r.structured) for r in records]
    # 超时（>30 分钟）那条被丢；同来源只留最新一条；整体按时间倒序
    assert got == [
        ("notification", "最新通知", {"title": "妈妈"}),
        ("accessibility", "屏幕内容", None),
        ("clipboard", "刚复制的", None),
    ]


def test_同一时刻的多条按id倒序保证确定性(phone_db):
    """created_at 完全相同（同一秒批量补传）时，靠 id 倒序定序，同来源只留最后写入的那条。"""
    asyncio.run(_add_user(phone_db, 5))
    same_ts = _NOW_UTC - timedelta(minutes=1)
    async def _ins():
        async with phone_db() as db:
            db.add_all([
                PhoneSnapshot(user_id=5, source="clipboard", content=t, created_at=same_ts)
                for t in ("先写的", "后写的")
            ])
            await db.commit()
    asyncio.run(_ins())
    assert [r.raw_text for r in asyncio.run(port.read_perception_records(5))] == ["后写的"]


def test_记录读取跨租户不串(phone_db):
    asyncio.run(_add_user(phone_db, 11))
    asyncio.run(_add_user(phone_db, 12))
    asyncio.run(_add_snap(phone_db, 11, "clipboard", "11 号的剪贴板", minutes_ago=1))
    asyncio.run(_add_snap(phone_db, 12, "clipboard", "12 号的剪贴板", minutes_ago=1))
    assert [r.raw_text for r in asyncio.run(port.read_perception_records(11))] == ["11 号的剪贴板"]


def test_记录读取异常不抛(phone_db, monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(database, "async_session_factory", _boom)
    assert asyncio.run(port.read_perception_records(1)) == []


def test_记录读取无user_id直接空(phone_db):
    assert asyncio.run(port.read_perception_records(None)) == []


# ── ③ 迁移层：旧库/新库两种形态（真跑 alembic）──

def _cfg():
    from app.db.migrate import _alembic_config

    return _alembic_config()


def _point_at(db, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")


def _columns(db) -> dict[str, dict]:
    con = sqlite3.connect(str(db))
    try:
        return {r[1]: {"notnull": r[3], "dflt": r[4]} for r in con.execute(f"PRAGMA table_info({TABLE})")}
    finally:
        con.close()


def _version(db) -> str | None:
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _snapshot_row(db) -> tuple | None:
    """读 (content, payload_json)——只在新列存在时可用（升级后用）。"""
    con = sqlite3.connect(str(db))
    try:
        return con.execute(f"SELECT content, payload_json FROM {TABLE} WHERE id=1").fetchone()
    finally:
        con.close()


def _snapshot_content(db) -> str | None:
    """只读 content（不含新列）——旧库形态 / 回滚后仍可查。"""
    con = sqlite3.connect(str(db))
    try:
        row = con.execute(f"SELECT content FROM {TABLE} WHERE id=1").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def test_版本链单头且本迁移挂在交接点头上():
    sd = ScriptDirectory.from_config(_cfg())
    heads = sd.get_heads()
    assert len(heads) == 1, f"版本链必须单头，实际 heads={heads}"
    assert heads[0] == NEW_REV, f"新 head 应为 {NEW_REV}，实际 {heads[0]}"
    rev = sd.get_revision(NEW_REV)
    assert rev.down_revision == PREV_HEAD, "本迁移 down_revision 应为交接前的 head"
    assert PREV_HEAD in {r.revision for r in sd.walk_revisions()}


def test_旧库补列且数据不丢_downgrade可逆(tmp_path, monkeypatch):
    """旧库形态：交接点存量库（本表无 payload_json）→ upgrade head 只跑本迁移一步。"""
    db = tmp_path / "snap_old.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), PREV_HEAD)
    assert "payload_json" not in _columns(db), "老库基线不该已有该列"

    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute(
            f"INSERT INTO {TABLE} (id, user_id, source, content, created_at) "
            "VALUES (1, 1, 'notification', '老行正文', '2026-09-22 00:00:00')"
        )
        con.commit()
    finally:
        con.close()

    assert _snapshot_content(db) == "老行正文", "旧库形态（本表无该列）既有行必须读得到"

    command.upgrade(_cfg(), "head")
    cols = _columns(db)
    assert "payload_json" in cols, "upgrade head 后应补上 payload_json"
    assert cols["payload_json"]["notnull"] == 0, "必须是 nullable（老行无需回填）"
    assert cols["payload_json"]["dflt"] is None
    assert _version(db) == NEW_REV
    assert _snapshot_row(db) == ("老行正文", None), "老行必须原样保留、新列为 NULL"

    # 可逆：退回交接点 → 列消失；再 upgrade → 又回来（重复执行安全）
    command.downgrade(_cfg(), PREV_HEAD)
    assert "payload_json" not in _columns(db)
    assert _snapshot_content(db) == "老行正文", "downgrade 只删列，不得动其它列的数据"
    command.upgrade(_cfg(), "head")
    assert _snapshot_row(db) == ("老行正文", None)


def test_新库形态命中守卫0操作且数据不丢(tmp_path, monkeypatch, capsys):
    """新库形态：create_all（模型已含该列）+ stamp 到交接点 → upgrade 只跑本迁移、守卫命中 0 操作。"""
    import app.models  # noqa: F401  确保全部模型注册
    from app.models._all import Base

    db = tmp_path / "snap_new.db"
    _point_at(db, monkeypatch)
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.create_all(eng)   # 等价于 init_db 的建表路径
        with eng.begin() as conn:
            conn.exec_driver_sql(
                f"INSERT INTO {TABLE} (id, user_id, source, content) VALUES (1, 1, 'clipboard', '新库老行')"
            )
    finally:
        eng.dispose()
    assert "payload_json" in _columns(db), "create_all 应已按当前模型建出该列"

    command.stamp(_cfg(), PREV_HEAD)
    capsys.readouterr()
    command.upgrade(_cfg(), "head")     # 物理 schema 已齐平 → 只有本迁移执行
    command.upgrade(_cfg(), "head")     # 第二次是 alembic 层 no-op
    out = capsys.readouterr()
    assert "0 操作" in out.out + out.err, f"新库应 0 操作：{out.out}{out.err}"
    assert _version(db) == NEW_REV
    assert _snapshot_row(db) == ("新库老行", None), "0 操作路径不得动数据"


def test_整链重放落位新head且列可用(tmp_path, monkeypatch):
    """全新空库整链重放（与启动期「非空老库 upgrade head」同一重放机制）直到本迁移。"""
    db = tmp_path / "snap_chain.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), "head")
    assert _version(db) == NEW_REV
    assert "payload_json" in _columns(db)
