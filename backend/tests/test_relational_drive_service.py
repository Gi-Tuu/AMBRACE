# -*- coding: utf-8 -*-
"""A4 批 3 / T1 M1b1（2026-09-27）：水位仓储层 relational_drive_service 单测 ＋ flag 登记。

被测（派单 M1b1，零钩子、零调用方）：
1. ``app/application/relational_drive_service.py``（load_levels / settle / release_open / release_full）；
2. flag 键 ``relational_drive_shadow`` 的两处登记（AGENT_FLAGS 有 bool 键 + catalog 有条目不直显；
   全量对账由 tests/test_flag_catalog_metadata.py 兜底）。

覆盖派单 2.3 的 7 条：flag 关＝四入口零查询零写入（空表与预置行两种形态）；settle 幂等 /
跨北京日边界 / 夜间倍率 / 封顶 100；懒建行只建最小集合；release_open 写回**剩余水位**；
release_full 归零且缺行不建行；多用户不串味；脏值按 0。另加一条「本层不自行 commit」。

口径与纪律：临时库一律 pytest ``tmp_path``（``_dbclone`` 克隆，不碰 backend/data 生产库）；
不调模型、不走网络。纯算法口径本身已在 tests/test_relational_drives.py 覆盖，这里只测落库。
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app.application import relational_drive_service as svc
from app.domain.relational import drives as d
from app.utils.timeutil import shift_utc_naive

CHAR = 7
USER_A = 11
USER_B = 12


def _bj(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    """北京时间 hour:minute → naive UTC（库内口径：北京 = UTC+8）。"""
    return shift_utc_naive(datetime(2026, 9, day, hour, minute), -8)


def _flag(monkeypatch, on: bool) -> None:
    """翻影子总闸（消费口唯一读的就是 AGENT_FLAGS 这一项）。"""
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, bool(on))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """engine + session_factory + 库文件路径（两个用户、一个角色）；默认把总闸拨到开。"""
    from _dbclone import clone_engine, make_session_factory

    from app.models.character import AICharacter
    from app.models.user import User

    db_path = tmp_path / "m1b1.db"
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        async with factory() as db:
            for uid in (USER_A, USER_B):
                db.add(User(id=uid, username=f"rdsvc_u{uid}", nickname=f"u{uid}"))
            db.add(AICharacter(id=CHAR, user_id=USER_A, name="小暖", is_active=True))
            await db.commit()

    asyncio.run(_init())
    _flag(monkeypatch, on=True)
    yield SimpleNamespace(engine=engine, factory=factory, path=db_path)
    engine.sync_engine.dispose()


def _stmt_counter(engine) -> dict:
    """SQL 计数（before_cursor_execute 挂在同步引擎上，异步连接同样触发）。"""
    counter = {"n": 0}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _tally(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    return counter


def _raw_rows(env, *, character_id: int = CHAR) -> list[tuple]:
    """绕过 ORM 读全表（flag 关时本层不查库，用「表原样」作断言）。"""
    with sqlite3.connect(str(env.path)) as conn:
        return conn.execute(
            "SELECT user_id, drive_key, level, last_settled_at, last_released_at, last_released_ratio "
            "FROM relational_drives WHERE character_id=? ORDER BY user_id, drive_key",
            (character_id,),
        ).fetchall()


def _seed(env, rows) -> None:
    """预置行：rows = [(user_id, drive_key, level, last_settled_at)]，释放两列走库默认值。"""
    from app.models.character import RelationalDrive

    async def _run():
        async with env.factory() as db:
            for user_id, key, level, cursor in rows:
                db.add(RelationalDrive(
                    character_id=CHAR, user_id=user_id, drive_key=key,
                    level=level, last_settled_at=cursor,
                ))
            await db.commit()

    asyncio.run(_run())


def _rows_of(env, user_id: int = USER_A) -> dict[str, tuple]:
    """{drive_key: (level, 游标, last_released_at, 比例)}——断言落库结果的唯一口径。"""
    from app.models.character import RelationalDrive

    async def _run():
        async with env.factory() as db:
            got = (await db.execute(
                select(RelationalDrive).where(
                    RelationalDrive.character_id == CHAR, RelationalDrive.user_id == user_id
                )
            )).scalars().all()
            return {r.drive_key: (r.level, r.last_settled_at, r.last_released_at, r.last_released_ratio)
                    for r in got}

    return asyncio.run(_run())


def _call_all_four(env, *, user_id: int = USER_A, now=None) -> dict:
    """一次跑完四个入口（flag 关时＝验证「什么都不发生」）。"""

    async def _run():
        async with env.factory() as db:
            got = {
                "levels": await svc.load_levels(db, CHAR, user_id),
                "settled": await svc.settle(db, CHAR, user_id, now),
            }
            for key in d.DRIVE_ALL_KEYS:
                await svc.release_open(db, CHAR, user_id, key, now)
                await svc.release_full(db, CHAR, user_id, key, now)
            return got

    return asyncio.run(_run())


async def _settle(env, now, user_id: int = USER_A):
    async with env.factory() as db:
        out = await svc.settle(db, CHAR, user_id, now)
        await db.commit()  # 调用方负责提交（本层只 add/flush）
        return out


async def _load(env, user_id: int = USER_A):
    async with env.factory() as db:
        return await svc.load_levels(db, CHAR, user_id)


async def _release_open(env, key, now):
    async with env.factory() as db:
        out = await svc.release_open(db, CHAR, USER_A, key, now)
        await db.commit()
        return out


async def _release_full(env, key, now, user_id: int = USER_A):
    async with env.factory() as db:
        out = await svc.release_full(db, CHAR, user_id, key, now)
        await db.commit()
        return out


# ══════════════════════════════════════════════ 1. flag 关 ⇒ 零查询零写入

def test_flag关四入口一次库都不查(env, monkeypatch):
    """派单 2.3 第 1 条：空表与「预置行」两种形态下，关＝不发 SQL、表逐字节原样。"""
    _flag(monkeypatch, on=False)

    counter = _stmt_counter(env.engine)
    assert _call_all_four(env) == {"levels": {}, "settled": {}}
    assert counter["n"] == 0, "flag 关时本层不得发出任何 SQL"
    assert _raw_rows(env) == []

    cursor = _bj(10)
    _seed(env, [(USER_A, d.DRIVE_LONGING, 40.0, cursor)])
    before = _raw_rows(env)
    counter = _stmt_counter(env.engine)
    assert _call_all_four(env, now=_bj(14)) == {"levels": {}, "settled": {}}
    assert counter["n"] == 0
    assert _raw_rows(env) == before, "预置行的水位/游标/释放两列必须原样"


def test_flag开才发SQL(env, monkeypatch):
    """同一个库上：关＝一条 SQL 都不发，开＝真读写。"""
    _flag(monkeypatch, on=False)
    counter = _stmt_counter(env.engine)
    assert _call_all_four(env) == {"levels": {}, "settled": {}}
    assert counter["n"] == 0

    _flag(monkeypatch, on=True)
    counter = _stmt_counter(env.engine)
    assert asyncio.run(_settle(env, _bj(10)))  # 建出最小集合 ⇒ 非空
    assert counter["n"] > 0, "flag 开时本层应正常读写库"
    assert len(_raw_rows(env)) == len(d.DRIVE_CANDIDATE_KEYS)


def test_flag两处登记齐全():
    """派单 2.1：AGENT_FLAGS 缺省关 + catalog 有条目且不直显（不进 env 夹具，读的是声明期默认值）。"""
    from app.agent.loop import AGENT_FLAGS
    from app.application.flag_catalog import FLAG_CATALOG

    assert svc.FLAG_KEY in AGENT_FLAGS, "未登记进 AGENT_FLAGS ⇒ runtime_flags 里开了也不生效"
    assert AGENT_FLAGS[svc.FLAG_KEY] is False and isinstance(AGENT_FLAGS[svc.FLAG_KEY], bool)
    meta = FLAG_CATALOG[svc.FLAG_KEY]
    assert meta["visible"] is False and meta["group"] == "outreach_natural"
    assert meta["title_zh"].strip() and meta["desc_zh"].strip()
    assert meta["title_en"].strip() and meta["desc_en"].strip()


# ══════════════════════════════════════════════ 2. settle：懒建行 / 幂等 / 时段 / 封顶

def test_懒建行首次settle只建最小集合(env):
    """空表 settle ⇒ 只建候选 5 键（intimacy 不留垃圾行），level=0、游标＝now。"""
    now = _bj(9)
    out = asyncio.run(_settle(env, now))
    assert set(out) == set(d.DRIVE_ALL_KEYS), "返回值与 load_levels 同形（六键）"
    assert all(v == 0.0 for v in out.values()), "首次建行只定游标，不补算历史"

    rows = _rows_of(env)
    assert set(rows) == set(d.DRIVE_CANDIDATE_KEYS) and d.DRIVE_INTIMACY not in rows
    assert all(r[1] == now for r in rows.values())
    # 没建行的键由 load_levels 按 0.0 补齐（六键口径完整，不因不建 intimacy 而少键）
    assert asyncio.run(_load(env)) == {k: 0.0 for k in d.DRIVE_ALL_KEYS}


def test_settle幂等同now不重复加量(env):
    """同一 now 调两次 ⇒ 只加一次增量（游标是唯一事实）。"""
    cursor, now = _bj(10), _bj(12)
    _seed(env, [(USER_A, d.DRIVE_LONGING, 10.0, cursor)])
    g = d.DRIVE_GROWTH_PER_HOUR[d.DRIVE_LONGING]

    first = asyncio.run(_settle(env, now))
    assert first[d.DRIVE_LONGING] == pytest.approx(10.0 + 2 * g)
    assert asyncio.run(_settle(env, now))[d.DRIVE_LONGING] == pytest.approx(10.0 + 2 * g)
    assert _rows_of(env)[d.DRIVE_LONGING][0] == pytest.approx(10.0 + 2 * g)


def test_settle跨北京日边界与夜间倍率(env):
    """北京 22:30→次日 00:30：半小时白天 + 1.5 小时夜间（longing 夜倍率 0.6）。"""
    cursor, now = _bj(22, 30), _bj(0, 30, day=3)
    _seed(env, [(USER_A, d.DRIVE_LONGING, 10.0, cursor)])
    g, n = d.DRIVE_GROWTH_PER_HOUR[d.DRIVE_LONGING], d.DRIVE_NIGHT_MULTIPLIER[d.DRIVE_LONGING]
    out = asyncio.run(_settle(env, now))
    assert out[d.DRIVE_LONGING] == pytest.approx(10.0 + 0.5 * g + 1.5 * g * n)
    assert _rows_of(env)[d.DRIVE_LONGING][1] == now  # 游标推进到 now


def test_settle超长静默封顶一百(env):
    """一年静默也只到 100（长静默不无限累积）。"""
    _seed(env, [(USER_A, d.DRIVE_LONGING, 50.0, datetime(2025, 9, 1, 2, 0))])
    out = asyncio.run(_settle(env, _bj(10)))
    assert out[d.DRIVE_LONGING] == d.LEVEL_MAX == 100.0
    assert _rows_of(env)[d.DRIVE_LONGING][0] == 100.0


def test_settle不自行提交调用方回滚即撤销(env):
    """派单事务口径：本层只 add/flush，是否 commit 由调用方决定（回滚 ⇒ 库里什么都没发生）。"""
    async def _settle_then_rollback():
        async with env.factory() as db:
            await svc.settle(db, CHAR, USER_A, _bj(10))
            await db.rollback()

    asyncio.run(_settle_then_rollback())
    assert _raw_rows(env) == [], "本层不得自行 commit（改动留在调用方事务里）"


# ══════════════════════════════════════════════ 3. load_levels

def test_load缺行按零并丢掉未知键(env):
    """六键固定返回；库里没有的键＝0.0，历史脏键（非六键）不进结果。"""
    _seed(env, [
        (USER_A, d.DRIVE_LONGING, 30.0, _bj(10)),
        (USER_A, "no_such_drive", 77.0, _bj(10)),
    ])
    got = asyncio.run(_load(env))
    assert set(got) == set(d.DRIVE_ALL_KEYS)
    assert got[d.DRIVE_LONGING] == 30.0
    assert got[d.DRIVE_INTIMACY] == 0.0 and got[d.DRIVE_SHARING] == 0.0


# ══════════════════════════════════════════════ 4. 释放两档

def test_release_open写回剩余水位(env):
    """派单点名的坑：写回的是**剩下的**水位（50 → 32.5），比例入列，last_released_at 不动。"""
    cursor = _bj(10)
    _seed(env, [(USER_A, d.DRIVE_LONGING, 50.0, cursor)])
    assert asyncio.run(_release_open(env, d.DRIVE_LONGING, _bj(12))) is None

    level, cur, released_at, ratio = _rows_of(env)[d.DRIVE_LONGING]
    assert level == pytest.approx(32.5)
    assert ratio == pytest.approx(d.DRIVE_OPEN_RELEASE_RATIO[d.DRIVE_LONGING])
    assert released_at is None, "部分释放不写 last_released_at"
    assert cur == cursor, "释放不动结算游标"


def test_release_open未知键不释放且缺行不建(env):
    _seed(env, [(USER_A, d.DRIVE_LONGING, 20.0, _bj(10))])
    assert asyncio.run(_release_open(env, "no_such_drive", _bj(12))) is None
    rows = _rows_of(env)
    assert set(rows) == {d.DRIVE_LONGING}
    assert rows[d.DRIVE_LONGING][0] == pytest.approx(20.0), "未知键比例按 0 ⇒ 不释放"


def test_release_full归零并记时刻(env):
    moment = _bj(15)
    _seed(env, [(USER_A, d.DRIVE_LONGING, 50.0, _bj(10))])
    assert asyncio.run(_release_full(env, d.DRIVE_LONGING, moment)) is None
    level, _, released_at, ratio = _rows_of(env)[d.DRIVE_LONGING]
    assert level == 0.0 and released_at == moment and ratio == 1.0


def test_release_full缺行不动(env):
    _seed(env, [(USER_A, d.DRIVE_LONGING, 50.0, _bj(10))])
    assert asyncio.run(_release_full(env, d.DRIVE_SHARING, _bj(15))) is None
    assert _rows_of(env) == {d.DRIVE_LONGING: (50.0, _bj(10), None, 0.0)}


# ══════════════════════════════════════════════ 5. 多用户不串味 / 脏值

def test_多用户互不影响(env):
    """家庭共享租户口径：同一角色对 A/B 两套行各自结算、各自释放。"""
    cursor, now = _bj(10), _bj(12)
    _seed(env, [
        (USER_A, d.DRIVE_LONGING, 10.0, cursor), (USER_A, d.DRIVE_CONCERN, 10.0, cursor),
        (USER_B, d.DRIVE_LONGING, 10.0, cursor), (USER_B, d.DRIVE_CONCERN, 10.0, cursor),
    ])

    a = asyncio.run(_settle(env, now))
    b = _rows_of(env, USER_B)
    assert a[d.DRIVE_LONGING] > 10.0 and a[d.DRIVE_CONCERN] > 10.0
    assert all(r[0] == pytest.approx(10.0) and r[1] == cursor for r in b.values()), \
        "B 的水位与游标不得被 A 的结算带走"

    asyncio.run(_release_full(env, d.DRIVE_LONGING, now, user_id=USER_B))
    a_rows, b_rows = _rows_of(env), _rows_of(env, USER_B)
    assert a_rows[d.DRIVE_LONGING][0] == pytest.approx(a[d.DRIVE_LONGING])
    assert a_rows[d.DRIVE_LONGING][2] is None, "A 的释放时刻不得被 B 的回复写脏"
    assert b_rows[d.DRIVE_LONGING][0] == 0.0 and b_rows[d.DRIVE_LONGING][2] == now


def test_脏值按零处理不抛(env):
    """NULL 被 NOT NULL 挡在库外；非数字文本 SQLite 能存 ⇒ 读出按 0、不抛、下次释放也不报错。"""
    assert svc._level_of(None) == 0.0
    assert svc._level_of("abc") == 0.0
    assert svc._level_of("12.5") == pytest.approx(12.5)

    with sqlite3.connect(str(env.path)) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO relational_drives (character_id, user_id, drive_key, level, "
            "last_settled_at, last_released_ratio) VALUES (?,?,?,?,?,?)",
            (CHAR, USER_A, d.DRIVE_LONGING, "abc", "2026-09-02 02:00:00", 0.0),
        )
        conn.commit()

    assert asyncio.run(_load(env))[d.DRIVE_LONGING] == 0.0
    assert asyncio.run(_release_open(env, d.DRIVE_LONGING, _bj(12))) is None
    assert _rows_of(env)[d.DRIVE_LONGING][0] == 0.0
