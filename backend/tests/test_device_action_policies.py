# -*- coding: utf-8 -*-
"""C1b（X7 遗留②）：行动确认策略按账号服务端持久化 —— ``device_action_policies``。

覆盖派单 §3 三块：
① 端点层（``GET/PUT /api/v1/device/actions/policy``，挂 ``get_current_user_id``）：
   PUT 幂等 upsert（同 ``(账号, 能力)`` 重复写只有一行）；GET 只回**有行的**能力（空数组＝没配过，
   App 回落缺省档 ``first_per_type``）；跨账号隔离（两个 user_id 互不可见）；非法 capability /
   非法 policy / 缺字段一律 **400 + 机器可读 detail**（不许 500、不许静默落库）；
   读库失败 GET 照样 200 + 空数组（fail-safe，绝不清空 App 现值）。
② 表形态：ORM 层唯一约束同样挡住第二行（与迁移建出的库同一判据）。
③ 迁移层（``alembic/versions/f8a9b0c1d2e3``）：**真跑 alembic**——版本链单头且本迁移挂在交接点
   （``f7b8c9d0e1f2``）上；老库（无本表）upgrade head 建表 + 列形态 + 唯一约束生效 + downgrade 可逆；
   新库（create_all 已含本表）命中守卫 0 操作。

①② 用 tests/_dbclone.py 克隆库（当前 schema）；③ 自己从空库/create_all 库起步，不用克隆库
（_dbclone 模块 docstring：迁移类测试禁用克隆库，否则假绿）。
全程只写 pytest ``tmp_path`` 下的临时库，绝不触碰 backend/data/sqlite/ai_companion.db。
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import device_actions as device_actions_api
from app.auth.deps import get_current_user_id
from app.db import database
from app.device import actions
from app.models.device import DeviceActionPolicy

NEW_REV = "f8a9b0c1d2e3"
PREV_HEAD = "f7b8c9d0e1f2"      # 本迁移 down_revision（交接前的版本链头）= 老库基线
TABLE = "device_action_policies"

USER_A, USER_B = 21, 22
CAPS = ("action_open_app", "action_tap", "action_set_text")

# 建临时库属重量级/集成型用例（docs/engineering-protocol.md 十八）
pytestmark = pytest.mark.slow


# ── ①② 端点/表形态辅助（克隆库＝当前 schema）──

@pytest.fixture()
def pol_db(monkeypatch, tmp_path):
    """私有临时库，并把会话工厂接到 ``app.db.database``（actions.py 按属性访问它）。"""
    engine = clone_engine(tmp_path / "policies.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(database, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(device_actions_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app, raise_server_exceptions=False)


def _items(factory, user_id: int) -> dict[str, str]:
    """直读库里的行（绕开端点，验证「只有一行」这件事不靠回显）。"""
    async def _go():
        async with factory() as db:
            rows = (await db.execute(
                select(DeviceActionPolicy).where(DeviceActionPolicy.user_id == user_id)
            )).scalars().all()
            return {r.capability: r.policy for r in rows}
    return asyncio.run(_go())


def _count(factory) -> int:
    async def _go():
        async with factory() as db:
            return len((await db.execute(select(DeviceActionPolicy))).scalars().all())
    return asyncio.run(_go())


def test_GET无行时回空数组而非缺省档行(pol_db):
    """没配过＝库里零行 → ``items`` 空数组（App 据此回落缺省档；服务端不得替用户预写一行）。"""
    r = _client(USER_A).get("/api/v1/device/actions/policy")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "items": []}
    assert _count(pol_db) == 0


def test_PUT幂等upsert且同能力永远只有一行(pol_db):
    c = _client(USER_A)
    for _ in range(3):
        r = c.put("/api/v1/device/actions/policy",
                  json={"capability": "action_tap", "policy": "every_time"})
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok", "capability": "action_tap", "policy": "every_time"}
    assert _count(pol_db) == 1, "重复写不得产生第二行"

    # 换档＝改同一行（幂等 upsert 的另一半：值真的变了，而不是「已有行就什么也不做」）
    assert c.put("/api/v1/device/actions/policy",
                 json={"capability": "action_tap", "policy": "once_ever"}).status_code == 200
    assert _items(pol_db, USER_A) == {"action_tap": "once_ever"}
    assert _count(pol_db) == 1


def test_GET只回本账号已配过的能力且排序(pol_db):
    c = _client(USER_A)
    for cap, tier in (("action_set_text", "every_time"), ("action_tap", "once_ever")):
        assert c.put("/api/v1/device/actions/policy",
                     json={"capability": cap, "policy": tier}).status_code == 200
    body = c.get("/api/v1/device/actions/policy").json()
    assert body["status"] == "ok"
    assert body["items"] == [
        {"capability": "action_set_text", "policy": "every_time"},
        {"capability": "action_tap", "policy": "once_ever"},
    ]
    assert len(body["items"]) == 2, "没配的第三条不得出现在回包里（无行＝没配过）"


def test_跨账号彼此不可见(pol_db):
    assert _client(USER_A).put("/api/v1/device/actions/policy",
                               json={"capability": "action_tap", "policy": "every_time"}
                               ).status_code == 200
    b = _client(USER_B)
    assert b.get("/api/v1/device/actions/policy").json()["items"] == [], "别人配的档位看不到"
    # B 写同一条能力 → 各自一行，互不覆盖
    assert b.put("/api/v1/device/actions/policy",
                 json={"capability": "action_tap", "policy": "once_ever"}).status_code == 200
    assert b.get("/api/v1/device/actions/policy").json()["items"] == [
        {"capability": "action_tap", "policy": "once_ever"}]
    assert _client(USER_A).get("/api/v1/device/actions/policy").json()["items"] == [
        {"capability": "action_tap", "policy": "every_time"}]
    assert _count(pol_db) == 2


@pytest.mark.parametrize("脏能力", [
    "foreground_app",          # 只读能力（kind="read"）不得配档位
    "notifications",
    "no_such_capability",
    "ACTION_TAP",              # 大小写敏感（能力名是字面量）
    "",
])
def test_非法capability一律400且不落库(pol_db, 脏能力):
    c = _client(USER_A)
    r = c.put("/api/v1/device/actions/policy", json={"capability": 脏能力, "policy": "every_time"})
    assert r.status_code == 400, f"{脏能力!r} → {r.status_code} {r.text}"
    assert "unknown_capability" in r.json()["detail"] or "field_required" in r.json()["detail"]
    assert _count(pol_db) == 0, "非法入参绝不允许静默落库"


def test_capability首尾空格按strip后判定(pol_db):
    """与两份名单同口径：写入前 strip，``" action_tap "`` 视同 ``action_tap``。"""
    c = _client(USER_A)
    r = c.put("/api/v1/device/actions/policy",
              json={"capability": "  action_tap  ", "policy": "every_time"})
    assert r.status_code == 200, r.text
    assert r.json()["capability"] == "action_tap"
    assert _items(pol_db, USER_A) == {"action_tap": "every_time"}


@pytest.mark.parametrize("脏档位", [
    "every_day",       # M4c-5 之前旧口径里的脏名（Flutter 侧 policyFromName 会回落中档）
    "EVERY_TIME",      # 大小写敏感
    "none",            # capabilities.VALID_CONFIRMATIONS 里的「只读档」，不是 App 三档之一
    "first_per_type_",
    "",
])
def test_非法policy一律400且不落库(pol_db, 脏档位):
    c = _client(USER_A)
    r = c.put("/api/v1/device/actions/policy",
              json={"capability": "action_tap", "policy": 脏档位})
    assert r.status_code == 400, f"{脏档位!r} → {r.status_code} {r.text}"
    assert ("invalid_policy" in r.json()["detail"]
            or "field_required" in r.json()["detail"])
    assert _count(pol_db) == 0


def test_缺字段与空请求体400(pol_db):
    c = _client(USER_A)
    r = c.put("/api/v1/device/actions/policy", json={"capability": "action_tap"})
    assert r.status_code == 400 and "field_required:policy" in r.json()["detail"], r.text
    r = c.put("/api/v1/device/actions/policy", json={"policy": "every_time"})
    assert r.status_code == 400 and "field_required:capability" in r.json()["detail"], r.text
    r = c.put("/api/v1/device/actions/policy", json={})
    assert r.status_code == 400, r.text
    assert _count(pol_db) == 0


def test_三条行动能力都可配且档位集合与后端常量同源(pol_db):
    """能力清单只有一份：端点校验用的就是 ``capabilities`` 里 ``kind="act"`` 的那三条。"""
    assert set(actions.ACTION_POLICY_TIERS) == {"once_ever", "first_per_type", "every_time"}
    assert frozenset(CAPS) == device_actions_api.ACTION_POLICY_CAPABILITIES
    c = _client(USER_A)
    for cap in CAPS:
        r = c.put("/api/v1/device/actions/policy",
                  json={"capability": cap, "policy": actions.ACTION_POLICY_DEFAULT})
        assert r.status_code == 200, f"{cap}: {r.text}"
    assert set(_items(pol_db, USER_A)) == set(CAPS)


def test_GET读库失败仍200且回空数组(pol_db, monkeypatch):
    """后端抖动不得让 App 现值被清空：读失败＝「服务端没配过」，端点不回 500。"""
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(database, "async_session_factory", _boom)
    r = _client(USER_A).get("/api/v1/device/actions/policy")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "items": []}


def test_唯一约束挡住第二行直插(pol_db):
    """约束在（不是只在应用层判重）：绕开端点直插同 ``(user_id, capability)`` 必须失败。"""
    async def _ins(cap: str, tier: str):
        async with pol_db() as db:
            db.add(DeviceActionPolicy(user_id=USER_A, capability=cap, policy=tier))
            await db.commit()

    asyncio.run(_ins("action_tap", "every_time"))
    with pytest.raises(IntegrityError):
        asyncio.run(_ins("action_tap", "once_ever"))
    assert _items(pol_db, USER_A) == {"action_tap": "every_time"}


# ── ③ 迁移层：真跑 alembic（老库建表 / 新库 0 操作 / 可逆）──

def _cfg():
    from app.db.migrate import _alembic_config

    return _alembic_config()


def _head() -> str:
    """当前迁移链 head（**动态读取**：每加一个迁移都会前移，硬编码必红）。"""
    return ScriptDirectory.from_config(_cfg()).get_current_head()


def _point_at(db, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")


def _columns(db) -> dict[str, dict]:
    con = sqlite3.connect(str(db))
    try:
        return {r[1]: {"type": r[2], "notnull": r[3], "dflt": r[4]}
                for r in con.execute(f"PRAGMA table_info({TABLE})")}
    finally:
        con.close()


def _indexes(db) -> set[str]:
    con = sqlite3.connect(str(db))
    try:
        return {r[1] for r in con.execute(f"PRAGMA index_list({TABLE})")}
    finally:
        con.close()


def _has_unique_user_cap(db) -> bool:
    """库里是否存在「恰好覆盖 (user_id, capability) 且唯一」的索引（联合唯一约束的落地形态）。"""
    con = sqlite3.connect(str(db))
    try:
        for idx in con.execute(f"PRAGMA index_list({TABLE})"):
            name, unique = idx[1], idx[2]
            cols = [r[2] for r in con.execute(f"PRAGMA index_info('{name}')")]
            if unique and cols == ["user_id", "capability"]:
                return True
        return False
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


def _table_exists(db) -> bool:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone() is not None
    finally:
        con.close()


def test_版本链单头且本迁移挂在交接点头上():
    sd = ScriptDirectory.from_config(_cfg())
    heads = sd.get_heads()
    assert len(heads) == 1, f"版本链必须单头，实际 heads={heads}"
    revs = {r.revision: r for r in sd.walk_revisions()}
    assert NEW_REV in revs, "本迁移未挂进链（从 head 走不到）"
    assert revs[NEW_REV].down_revision == PREV_HEAD, (
        f"down_revision 应指向交接前的链头 {PREV_HEAD}，实际 {revs[NEW_REV].down_revision}")
    kids = [rid for rid, r in revs.items() if r.down_revision == PREV_HEAD]
    assert kids == [NEW_REV], f"交接点 {PREV_HEAD} 只允许一个子节点（多子节点＝双头），实际 {kids}"


def test_老库upgrade建表列形态正确且downgrade可逆(tmp_path, monkeypatch):
    """老库形态（停在交接点、本表压根不存在）→ upgrade head 真正走 create_table 那一条路径。"""
    db = tmp_path / "pol_old.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), PREV_HEAD)
    assert not _table_exists(db), "老库基线不该已有本表"

    command.upgrade(_cfg(), "head")
    assert _table_exists(db)
    assert _version(db) == _head()
    cols = _columns(db)
    assert set(cols) == {"id", "user_id", "capability", "policy", "created_at", "updated_at"}
    for c in ("user_id", "capability", "policy", "created_at", "updated_at"):
        assert cols[c]["notnull"] == 1, f"{c} 必须 not null"
    assert cols["user_id"]["type"].upper() == "INTEGER"
    assert cols["capability"]["type"].upper().startswith("VARCHAR")
    assert cols["policy"]["type"].upper().startswith("VARCHAR")

    # 唯一约束（联合）+ user_id 索引都在位，且与 ORM 隐式索引名一致
    assert _has_unique_user_cap(db), "缺少覆盖 (user_id, capability) 的唯一索引"
    assert "ix_device_action_policies_user_id" in _indexes(db)

    # 迁移建出的库形态必须与 create_all 直建的新库一致（否则两份 DDL 会各自漂移）
    fresh = tmp_path / "pol_shape.db"
    eng = create_engine(f"sqlite:///{fresh.as_posix()}")
    try:
        import app.models  # noqa: F401  确保全部模型注册（表形态与当前模型同源）

        DeviceActionPolicy.__table__.create(eng)
    finally:
        eng.dispose()
    assert _columns(fresh) == cols, "迁移建出的表形态与当前模型不一致"
    assert _indexes(fresh) == _indexes(db)

    # 可逆：退回交接点 → 表消失；再 upgrade → 又回来（重复执行安全）
    command.downgrade(_cfg(), PREV_HEAD)
    assert not _table_exists(db)
    assert _version(db) == PREV_HEAD
    command.upgrade(_cfg(), "head")
    assert _table_exists(db) and _has_unique_user_cap(db)


def test_老库升级后唯一约束真的挡住第二行(tmp_path, monkeypatch):
    """表形态断言之外再来一发实写：迁移建的约束必须可执行（同 user+cap 第二行失败）。"""
    db = tmp_path / "pol_constraint.db"
    _point_at(db, monkeypatch)
    command.upgrade(_cfg(), PREV_HEAD)
    command.upgrade(_cfg(), "head")

    con = sqlite3.connect(str(db))
    try:
        con.execute(
            f"INSERT INTO {TABLE} (user_id, capability, policy) VALUES (1, 'action_tap', 'every_time')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                f"INSERT INTO {TABLE} (user_id, capability, policy) VALUES (1, 'action_tap', 'once_ever')"
            )
        # 别的账号 / 别的能力各占一行，不受约束影响
        con.execute(
            f"INSERT INTO {TABLE} (user_id, capability, policy) VALUES (2, 'action_tap', 'once_ever')"
        )
        con.execute(
            f"INSERT INTO {TABLE} (user_id, capability, policy) "
            "VALUES (1, 'action_open_app', 'once_ever')"
        )
        con.commit()
        assert con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == 3
    finally:
        con.close()


def test_新库命中守卫0操作且不写任何行(tmp_path, monkeypatch, capsys):
    """新库形态（init_db create_all 已含本表）→ 升级命中 has_table 守卫，0 操作、不预写行。"""
    db = tmp_path / "pol_new.db"
    _point_at(db, monkeypatch)
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        import app.models  # noqa: F401
        from app.models._all import Base

        Base.metadata.create_all(eng)     # 等价于 init_db 的建表路径
    finally:
        eng.dispose()
    assert _table_exists(db)

    command.stamp(_cfg(), PREV_HEAD)
    capsys.readouterr()
    command.upgrade(_cfg(), "head")
    command.upgrade(_cfg(), "head")       # 第二次是 alembic 层 no-op
    out = capsys.readouterr()
    assert "幂等跳过" in out.out + out.err, f"新库应命中守卫 0 操作：{out.out}{out.err}"
    assert _version(db) == _head()

    con = sqlite3.connect(str(db))
    try:
        assert con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == 0, "不得预写任何行"
    finally:
        con.close()
