# -*- coding: utf-8 -*-
"""控制台删号·第二期第二批：**到期回收站账号自动清除调度**测试（2026-09-24 派单第 4 项）。

覆盖派单逐条：
- **flag 关（默认）**：一次都不查库（``no_db`` 把开库变成断言失败 + ``query_counter`` 计数为 0）
  + 到期账号一行都没少；
- **flag 开**：到期账号被清、``account_purge_jobs`` 落 ``done``、摘要带耗时与逐表行数、
  审计的 ``actor_user_id`` 为 NULL（= 系统自动，不是任何真人删的）；
- **未到期**（``purge_after`` 在未来）不动；
- **低峰窗口**：注入「北京 12:00」→ 跳过且不查库；同一棋盘注入「北京 03:00」→ 才动手；
  ``ACCOUNT_PURGE_WINDOW`` 可覆盖（含跨零点），非法值回落默认；
- **限流**：连续两拍第二拍因最小间隔跳过（整轮只查库一次）；两个到期号一轮只清一个；
  ``ACCOUNT_PURGE_MAX_PER_TICK`` 可调 + 硬顶 ≤3 + 非法回落默认；
- **保留护栏**：唯一 server_admin 到期 → 不清、``deleted_at`` 仍非空、写 WARNING、``attempts``
  逐轮累加，到顶后**不再自动重试**；
- **幂等**：已有 ``done`` 作业的账号不再进候选（``done`` 永不重入队），``failed`` 仍会重入；
- **只做触发，不自己删**：唯一的动作是调 ``purge_account(system_actor=True)``，
  打桩后账号行仍活着即证明本模块没写任何 DELETE；同一时刻只跑一个（锁被占即跳过）；
- **HTTP 不暴露系统通道**：body 里塞 ``system_actor=true`` 不绕过 ``confirm_username``（仍 400）。

口径：一律 pytest ``tmp_path`` 私有 SQLite（``tests/_dbclone`` 克隆）+ tmp 数据目录；
``_data_dir`` / ``_run_backup`` / 向量层全部打桩到 tmp，绝不碰 backend/data（生产库连只读都不做）。
时间经 ``_now`` 注入口径固定，窗口判定读实际 ``APP_TZ_OFFSET_HOURS``（不硬编码 +8）。
"""
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory
from test_account_deletion import _hash, _patch_session_factories
from test_account_purge import _apply_jobs_migration, _job, _rows
from test_user_cascade_lock import _fill_ins

from app.api import admin as admin_api
from app.application import account_purge, account_purge_scheduler as sched
from app.auth.config import create_token
from app.db import vector_store
from app.utils.timeutil import app_tz_offset_hours

pytestmark = pytest.mark.slow

ADMIN_UID, TGT_UID, FUT_UID = 1, 2, 3        # 存活管理员 / 到期目标 / 未到期邻居
TGT2_UID, TGT3_UID = 4, 5                    # 限流与护栏用例的额外账号
PW = "rootpass123"
TGT_CID, SESS_TGT, MEM_TGT = 21, 41, 51

LONG_AGO = datetime(2000, 1, 1)              # 早已到期（真时钟同样判定为到期）
FAR_FUTURE = datetime(2999, 1, 1)            # 远未到期
DELETED_AT = datetime(2026, 9, 1)            # 进回收站时刻

LAST_ADMIN_ZH = "不能删除最后一个服务器管理员，请先授予其他账号"


def _local(hour: int, minute: int = 0) -> datetime:
    """应用本地时刻 == hour:minute 的 naive UTC 时刻（偏移取实际配置，不写死 +8）。"""
    return datetime(2026, 9, 25, hour, minute) - timedelta(hours=app_tz_offset_hours())


def _mk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


# ── 棋盘搭建（全走真 ORM 写路径：日期串格式与生产逐字节一致）──────────────────────

def _add_users(factory, rows: list[dict]):
    from app.models.user import User

    async def _go():
        async with factory() as db:
            db.add_all([User(**r) for r in rows])
            await db.commit()

    asyncio.run(_go())


def _set_bin(factory, uid: int, *, purge_after: datetime, deleted_at=DELETED_AT):
    """把账号摆进回收站并设定到期时刻。"""
    from app.models.user import User

    async def _go():
        async with factory() as db:
            u = (await db.execute(select(User).where(User.id == int(uid)))).scalar_one()
            u.deleted_at = deleted_at
            u.purge_after = purge_after
            await db.commit()

    asyncio.run(_go())


def _patch_user(factory, uid: int, **cols):
    from app.models.user import User

    async def _go():
        async with factory() as db:
            u = (await db.execute(select(User).where(User.id == int(uid)))).scalar_one()
            for k, v in cols.items():
                setattr(u, k, v)
            await db.commit()

    asyncio.run(_go())


def _alive(db_file: Path, uid: int) -> bool:
    return _rows(db_file, "users", "id=%d" % int(uid)) == 1


def _col(db_file: Path, uid: int, col: str):
    con = sqlite3.connect(str(db_file))
    try:
        row = con.execute(f"SELECT {col} FROM users WHERE id = ?", (int(uid),)).fetchone()
        return None if row is None else row[0]
    finally:
        con.close()


# ── 沙箱库 ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def sched_env(monkeypatch, tmp_path):
    """私有临时库（含 ``account_purge_jobs``）+ 三处桩：数据目录 / 备份 / 向量层。"""
    dst: Path = tmp_path / "sched.db"
    engine = clone_engine(dst)
    _apply_jobs_migration(dst)
    factory = make_session_factory(engine)

    data = tmp_path / "data"
    owned = data / "uploads" / "avatars" / str(TGT_UID)
    owned.mkdir(parents=True)
    (owned / "a.png").write_text("x", encoding="utf-8")
    monkeypatch.setattr(account_purge, "_data_dir", lambda: data)
    monkeypatch.setattr(account_purge, "_run_backup",
                        lambda: str(_mk(tmp_path / "backups" / "sched.zip")))

    vector_calls: list[dict] = []

    async def _fake_vectors(user_id: int, memory_ids=None) -> dict:
        ids = [int(m) for m in (memory_ids or [])]
        vector_calls.append({"user_id": int(user_id), "memory_ids": ids})
        return {"by_user": 1, "by_memory": len(ids), "unresolved": 0}

    monkeypatch.setattr(vector_store, "delete_memory_vectors_by_user", _fake_vectors)
    _patch_session_factories(monkeypatch, factory)

    yield SimpleNamespace(dst=dst, data=data, factory=factory, tmp_path=tmp_path,
                          vector_calls=vector_calls)
    engine.sync_engine.dispose()


@pytest.fixture()
def seeded(sched_env):
    """标准棋盘：``TGT`` 在回收站且**已到期**（名下 1 角色 / 1 会话 / 1 记忆），
    ``FUT`` 在回收站但**未到期**，``ADMIN`` 存活（唯一 server_admin），``TGT2/TGT3`` 活着不在回收站。

    需要多个到期账号的用例自己调 :func:`_set_bin`（默认棋盘保持「只有一个到期者」，
    这样候选顺序、被清者身份都是确定的）。
    """
    _add_users(sched_env.factory, [
        dict(id=ADMIN_UID, username="admin", nickname="管理员", is_admin=True,
             server_admin=True, password_hash=_hash(PW)),
        dict(id=TGT_UID, username="tgt", nickname="甲", is_admin=True, password_hash=_hash(PW)),
        dict(id=FUT_UID, username="future", nickname="乙", is_admin=True, password_hash=_hash(PW)),
        dict(id=TGT2_UID, username="tgt2", nickname="丙", is_admin=True, password_hash=_hash(PW)),
        dict(id=TGT3_UID, username="tgt3", nickname="丁", is_admin=True, password_hash=_hash(PW)),
    ])
    con = sqlite3.connect(str(sched_env.dst))
    try:
        cur = con.cursor()
        _fill_ins(cur, "ai_characters", ["id", "user_id", "name"], [(TGT_CID, TGT_UID, "小甲")])
        _fill_ins(cur, "chat_sessions", ["id", "user_id", "character_id", "title"],
                  [(SESS_TGT, TGT_UID, TGT_CID, "会话")])
        _fill_ins(cur, "memories",
                  ["id", "user_id", "character_id", "content", "importance", "speaker_id",
                   "speaker_type"], [(MEM_TGT, TGT_UID, TGT_CID, "m", 50, TGT_UID, "user")])
        con.commit()
    finally:
        con.close()
    _set_bin(sched_env.factory, TGT_UID, purge_after=LONG_AGO)
    _set_bin(sched_env.factory, FUT_UID, purge_after=FAR_FUTURE)
    return sched_env


def _three_due(env):
    """把 TGT / TGT2 / TGT3 都摆成到期，且 ``purge_after`` 错开 → 候选顺序必然 [TGT, TGT2, TGT3]。"""
    _set_bin(env.factory, TGT_UID, purge_after=LONG_AGO)
    _set_bin(env.factory, TGT2_UID, purge_after=LONG_AGO + timedelta(days=1))
    _set_bin(env.factory, TGT3_UID, purge_after=LONG_AGO + timedelta(days=2))


# ── 注入缝：开关 / 时钟 / 读库计数 / 开库禁区 / 清除器打桩 ─────────────────────────

@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每例归零进程内运行态 + 清掉三个环境变量（默认口径必须来自代码常量，不来自上例残留）。"""
    for key in (sched.ENV_WINDOW, sched.ENV_MAX_PER_TICK, sched.ENV_MIN_INTERVAL_MINUTES):
        monkeypatch.delenv(key, raising=False)
    sched.reset_state()
    yield
    sched.reset_state()


@pytest.fixture()
def flag_on(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "account_purge_scheduler", True)
    return True


@pytest.fixture()
def flag_off(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "account_purge_scheduler", False)
    return False


@pytest.fixture()
def clock(monkeypatch):
    """可调时钟（默认落在低峰窗口内的北京 03:00）。"""
    cell = {"now": _local(3, 0)}
    monkeypatch.setattr(sched, "_now", lambda: cell["now"])
    return SimpleNamespace(
        cell=cell,
        at=lambda h, m=0: cell.update(now=_local(h, m)),
        advance=lambda minutes: cell.update(now=cell["now"] + timedelta(minutes=minutes)),
    )


@pytest.fixture()
def query_counter(monkeypatch):
    """包住本模块**唯一**的读库点：只记次数，不改行为（「一次都不查库」的判据）。"""
    calls: list[datetime] = []
    original = sched._fetch_candidates

    async def _spy(db, now, *, limit=sched.CANDIDATE_FETCH_LIMIT):
        calls.append(now)
        return await original(db, now, limit=limit)

    monkeypatch.setattr(sched, "_fetch_candidates", _spy)
    return calls


@pytest.fixture()
def no_db(monkeypatch):
    """任何开库直接判失败——早退分支（flag 关 / 窗口外 / 间隔内 / 锁被占）必须一行 SQL 都不发。

    必须排在 ``seeded`` 之后请求：``_patch_session_factories`` 先把工厂换成临时库，这里再换成必炸版。
    """
    import app.db.database as db_mod

    def _boom(*_a, **_kw):
        raise AssertionError("调度器在不该开库的时候开了库（早退分支必须零查库）")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)


@pytest.fixture()
def install_purge_spy(monkeypatch):
    """拦下调度器唯一的动作：记录 kwargs、决定抛错或回报告（只验语义，不真删）。"""
    def _install(*, raises: Exception | None = None, report: dict | None = None) -> list[dict]:
        calls: list[dict] = []

        async def _spy(_db, **kw):
            calls.append(dict(kw))
            if raises is not None:
                raise raises
            return dict(report or {"job_id": 999, "status": account_purge.STATUS_DONE,
                                   "rows_deleted": 0, "elapsed_seconds": 0.1, "tables": []})

        monkeypatch.setattr(account_purge, "purge_account", _spy)
        return calls

    return _install


def _tick() -> dict:
    return asyncio.run(sched.tick())


def _purged_ids(summary: dict) -> list[int]:
    return [p["user_id"] for p in summary["purged"]]


# ═══════════════════════════════════════════════════════════════════════════════
# ① flag 关（默认）：零查库 + 零删除
# ═══════════════════════════════════════════════════════════════════════════════

def test_flag_off_zero_query_and_zero_delete(seeded, flag_off, clock, query_counter, no_db):
    """总闸关闭时：即使「时钟在低峰窗口内 + 有到期账号」，也不查库、不删任何东西。

    ``no_db`` 排在 ``seeded`` 之后，把会话工厂换成必炸版本；``tick()`` 若走到开库即测试失败。
    """
    summary = _tick()

    assert summary["flag"] is False and summary["ran"] is False
    assert summary["skipped"] == "flag_off"
    assert query_counter == [] and summary["candidates"] == [] and summary["purged"] == []
    for uid in (TGT_UID, FUT_UID, TGT2_UID, TGT3_UID, ADMIN_UID):
        assert _alive(seeded.dst, uid), uid
    assert _col(seeded.dst, TGT_UID, "deleted_at"), "关闸时回收站态不许被动过"
    assert _rows(seeded.dst, account_purge.JOB_TABLE) == 0
    assert seeded.vector_calls == []
    assert (seeded.data / "trash").exists() is False


def test_flag_key_registered_and_off_by_default():
    """总闸必须在 ``AGENT_FLAGS`` 里默认关，并在开关目录登记（中英标题成对、落在运维最近的一组）。"""
    from app.agent.loop import AGENT_FLAGS
    from app.application.flag_catalog import FLAG_CATALOG

    assert "account_purge_scheduler" in AGENT_FLAGS
    assert AGENT_FLAGS["account_purge_scheduler"] is False
    meta = FLAG_CATALOG["account_purge_scheduler"]
    assert meta["group"] == "channel", "分组：channel 是唯一的后台维护/留存类开关所在组"
    assert meta["title_zh"] and meta["title_en"] and meta["desc_zh"] and meta["desc_en"]
    assert meta["visible"] is False, "高危开关不进普通开关列表，只在服务器控制台可见"
    # 用户级覆盖集合不含本键（这是服务器级动作，不允许按账号各开各的）
    from app.application.flag_service import USER_SCOPED_FLAG_KEYS
    assert "account_purge_scheduler" not in USER_SCOPED_FLAG_KEYS


# ═══════════════════════════════════════════════════════════════════════════════
# ② flag 开：到期即清（摘要 / 作业 / 审计口径）
# ═══════════════════════════════════════════════════════════════════════════════

def test_due_account_purged_by_tick(seeded, flag_on, clock, query_counter):
    env = seeded
    summary = _tick()

    assert summary["ran"] is True and summary["in_window"] is True
    assert summary["skipped"] is None and summary["blocked"] == []
    assert summary["candidates"] == [TGT_UID], "只挑到过期的号（未到期/未进回收站都不算）"
    assert _purged_ids(summary) == [TGT_UID]
    entry = summary["purged"][0]
    assert entry["username"] == "tgt" and entry["status"] == account_purge.STATUS_DONE
    assert entry["job_id"]
    # 派单：摘要要看得懂「花了多久、每张表删了几行」
    assert isinstance(entry["elapsed_seconds"], (int, float))
    per_table = {t["table"]: t["rows"] for t in entry["tables"]}
    assert per_table["users"] == 1 and per_table["memories"] == 1
    assert per_table["ai_characters"] == 1 and per_table["chat_sessions"] == 1
    assert entry["rows_deleted"] == sum(per_table.values())

    # 库：目标没了，管理员与未到期的活着，目标名下数据一起走
    assert _alive(env.dst, TGT_UID) is False
    assert _alive(env.dst, ADMIN_UID) and _alive(env.dst, FUT_UID)
    assert _rows(env.dst, "memories", "user_id=%d" % TGT_UID) == 0
    job = _job(env.dst, TGT_UID)
    assert job["status"] == account_purge.STATUS_DONE and job["error"] is None
    assert json.loads(job["report_json"])["rows_deleted"] == entry["rows_deleted"]
    # 向量层收到的是第 0 步固化的 memory id，且整轮只此一次
    assert env.vector_calls == [{"user_id": TGT_UID, "memory_ids": [MEM_TGT]}]
    # 审计：系统身份（actor_user_id 为 NULL），不是任何真人删的
    con = sqlite3.connect(str(env.dst))
    try:
        rows = con.execute("SELECT actor_user_id, target FROM admin_audit_log "
                           "WHERE action='account.purge'").fetchall()
    finally:
        con.close()
    assert rows == [(None, "user:%d" % TGT_UID)], rows


def test_not_due_account_is_left_alone(seeded, flag_on, clock, query_counter):
    """``purge_after`` 在未来的账号绝不提前清（宽限期是回收站存在的意义）。"""
    env = seeded
    _set_bin(env.factory, TGT_UID, purge_after=FAR_FUTURE)      # 唯一到期者也改成未到期
    summary = _tick()

    assert summary["ran"] is True and summary["candidates"] == [] and summary["purged"] == []
    assert _alive(env.dst, TGT_UID)
    assert _col(env.dst, TGT_UID, "deleted_at"), "未到期必须仍停在回收站"
    assert _rows(env.dst, account_purge.JOB_TABLE) == 0 and env.vector_calls == []
    assert len(query_counter) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# ③ 低峰窗口
# ═══════════════════════════════════════════════════════════════════════════════

def test_out_of_window_skips_without_query(seeded, flag_on, clock, query_counter):
    """默认窗口（北京 02:00–06:00）之外：跳过、不查库；同一棋盘挪进窗口才动手。"""
    clock.at(12, 0)
    summary = _tick()

    assert summary["skipped"] == "out_of_window" and summary["in_window"] is False
    assert summary["window"] == "02:00-06:00" and summary["ran"] is False
    assert query_counter == [] and summary["candidates"] == []
    assert _alive(seeded.dst, TGT_UID)

    clock.at(3, 0)
    assert _purged_ids(_tick()) == [TGT_UID], "窗口判据是这里唯一的变量"


def test_window_env_override_and_invalid_falls_back(seeded, flag_on, clock, query_counter,
                                                   monkeypatch):
    """``ACCOUNT_PURGE_WINDOW`` 覆盖窗口（含跨零点）；非法值一律回落默认——绝不因配错放宽执行时段。"""
    monkeypatch.setenv(sched.ENV_WINDOW, "11:30-13:30")
    clock.at(12, 0)
    summary = _tick()
    assert summary["window"] == "11:30-13:30" and summary["ran"] is True
    assert _purged_ids(summary) == [TGT_UID]

    clock.at(3, 0)                                     # 自定义窗口外（默认窗口内）反而不许跑
    assert _tick()["skipped"] == "out_of_window"
    assert len(query_counter) == 1

    monkeypatch.setenv(sched.ENV_WINDOW, "23:00-02:00")  # 跨零点
    assert sched._in_window(_local(23, 30)) and sched._in_window(_local(1, 0))
    assert not sched._in_window(_local(12, 0)) and not sched._in_window(_local(2, 0))

    for bad in ("abc", "25:00-26:00", "02:00", "02:00-", "06:00-06:00", "", "1:2:3-4"):
        monkeypatch.setenv(sched.ENV_WINDOW, bad)
        assert sched._fmt_window() == "02:00-06:00", bad
        assert sched._in_window(_local(3, 0)) is True, bad
        assert sched._in_window(_local(12, 0)) is False, bad


# ═══════════════════════════════════════════════════════════════════════════════
# ④ 限流：最小间隔 + 每轮名额
# ═══════════════════════════════════════════════════════════════════════════════

def test_min_interval_gates_second_tick(seeded, flag_on, clock, query_counter):
    """连续两拍：第二拍因「距上次成功清除不足 60 分钟」跳过且**不查库**；够间隔才继续。"""
    env = seeded
    _set_bin(env.factory, TGT2_UID, purge_after=LONG_AGO + timedelta(days=1))
    assert _purged_ids(_tick()) == [TGT_UID]
    assert len(query_counter) == 1

    second = _tick()                                   # 同一时刻：间隔不足
    assert second["skipped"] == "min_interval" and second["ran"] is False
    assert len(query_counter) == 1, "间隔内必须零查库"
    assert _alive(env.dst, TGT2_UID)

    clock.advance(sched.DEFAULT_MIN_INTERVAL_MINUTES - 1)
    assert _tick()["skipped"] == "min_interval" and len(query_counter) == 1
    clock.advance(1)                                   # 刚好满 60 分钟
    assert _purged_ids(_tick()) == [TGT2_UID]
    assert len(query_counter) == 2


def test_blocked_purge_does_not_consume_interval(seeded, flag_on, clock, install_purge_spy):
    """被挡/失败**不更新**上次清除时刻（否则一个坏号能把整晚的低峰窗口全占掉）。"""
    calls = install_purge_spy(raises=RuntimeError("注入：清除失败"))
    first = _tick()
    assert calls and first["blocked"] and sched._RUNTIME["last_purge_at"] is None

    again = _tick()
    assert again["ran"] is True and again["skipped"] is None
    assert len(calls) == 2 and again["blocked"][0]["attempts"] == 2


def test_max_one_account_per_tick_by_default(seeded, flag_on, clock):
    """默认每轮只清 1 个（SQLite 写压力 + 影响面控制）：三个到期号一轮只带走一个。"""
    env = seeded
    _three_due(env)
    summary = _tick()

    assert summary["candidates"] == [TGT_UID, TGT2_UID, TGT3_UID]
    assert _purged_ids(summary) == [TGT_UID]
    assert _alive(env.dst, TGT2_UID) and _alive(env.dst, TGT3_UID)
    assert _alive(env.dst, TGT_UID) is False


@pytest.mark.parametrize("raw,expect", [("2", 2), ("99", sched.MAX_PER_TICK_HARD_CAP),
                                        ("0", 1), ("-3", 1), ("abc", 1), ("", 1)])
def test_max_per_tick_env_is_capped_and_falls_back(seeded, flag_on, clock, install_purge_spy,
                                                   monkeypatch, raw, expect):
    """名额可调但硬顶 ≤3；非法值回落默认 1（打桩清除，只数一轮挑了几个、按什么顺序挑）。"""
    env = seeded
    _three_due(env)
    monkeypatch.setenv(sched.ENV_MAX_PER_TICK, raw)
    calls = install_purge_spy()
    summary = _tick()

    assert len(summary["purged"]) == expect, (raw, summary)
    assert len(calls) == expect
    assert [c["target_user_id"] for c in calls] == [TGT_UID, TGT2_UID, TGT3_UID][:expect]
    assert _alive(env.dst, TGT_UID) and _alive(env.dst, TGT2_UID), "打桩即零删除"


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ 保留护栏：最后一个 server_admin 绝不清 + attempts 退避
# ═══════════════════════════════════════════════════════════════════════════════

def test_last_server_admin_is_blocked_not_purged(seeded, flag_on, clock, caplog, monkeypatch):
    """**刻意保留**的护栏：到期的那个是唯一 server_admin → 不清、保持回收站态、写 WARNING。

    另证系统身份不会踩「删自己」（``actor_user_id=0`` 不等于任何真账号），命中的只有这一条；
    以及这条护栏是「唯一」而非「永不」——补上另一个管理员后下一拍就能清掉。
    """
    env = seeded
    _set_bin(env.factory, TGT_UID, purge_after=FAR_FUTURE)      # 让位：唯一到期者是管理员本身
    _patch_user(env.factory, ADMIN_UID, server_admin=False)
    _patch_user(env.factory, TGT2_UID, server_admin=True)
    _set_bin(env.factory, TGT2_UID, purge_after=LONG_AGO)

    # 会话引导跑过 alembic（env.py 的 fileConfig 默认 disable_existing_loggers=True），
    # 早于它创建的 app 日志器会被整体 ``disabled`` —— 这里显式解禁，才能验到真日志记录。
    monkeypatch.setattr(sched._logger, "disabled", False)
    with caplog.at_level(logging.WARNING, logger=sched._logger.name):
        summary = _tick()

    assert _purged_ids(summary) == [] and len(summary["blocked"]) == 1
    blocked = summary["blocked"][0]
    assert blocked["user_id"] == TGT2_UID and blocked["username"] == "tgt2"
    assert blocked["attempts"] == 1 and blocked["reason"] == LAST_ADMIN_ZH
    assert _alive(env.dst, TGT2_UID) and _col(env.dst, TGT2_UID, "deleted_at")
    assert _job(env.dst, TGT2_UID) == {}, "护栏在任何 DELETE 之前，不留作业行"
    assert env.vector_calls == [] and summary["skipped"] is None
    warned = [(r.levelname, r.getMessage()) for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warned) == 1, warned
    level, msg = warned[0]
    assert level == "WARNING" and "tgt2" in msg and LAST_ADMIN_ZH in msg
    assert "user=%d" % TGT2_UID in msg and "attempts=1/%d" % sched.MAX_ATTEMPTS in msg, msg

    # 前提修好（另授一名 server_admin）→ 下一拍照常清掉，attempts 不残留
    _patch_user(env.factory, ADMIN_UID, server_admin=True)
    sched.reset_state()
    clock.advance(sched.DEFAULT_MIN_INTERVAL_MINUTES + 1)
    assert _purged_ids(_tick()) == [TGT2_UID]


def test_blocked_account_stops_retrying_at_cap(seeded, flag_on, clock, install_purge_spy):
    """反复被挡的号累计到 :data:`MAX_ATTEMPTS` 后不再自动重试（只留 warning 与摘要标记）。"""
    env = seeded
    calls = install_purge_spy(raises=HTTPException(status_code=400, detail=LAST_ADMIN_ZH))

    for i in range(1, sched.MAX_ATTEMPTS + 1):
        summary = _tick()
        assert summary["blocked"][0]["attempts"] == i, i
        assert _purged_ids(summary) == [] and len(calls) == i, i
    assert sched._RUNTIME["attempts"][TGT_UID] == sched.MAX_ATTEMPTS

    after = _tick()
    assert len(calls) == sched.MAX_ATTEMPTS, "到达上限后一次都不再调用清除器"
    assert after["blocked"] == [] and _purged_ids(after) == []
    assert after["skipped_over_attempts"] == [{"user_id": TGT_UID,
                                              "attempts": sched.MAX_ATTEMPTS}]
    assert _alive(env.dst, TGT_UID) and _col(env.dst, TGT_UID, "deleted_at")
    assert _rows(env.dst, account_purge.JOB_TABLE) == 0

    # 进程内计数随 reset 归零（与「重启保守从 0 起算」同口径；真清成功时 tick 会 pop 掉该账号，
    # 由 test_due_account_purged_by_tick 覆盖）
    sched.reset_state()
    assert sched._RUNTIME["attempts"] == {}


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥ 幂等：done 永不重入队
# ═══════════════════════════════════════════════════════════════════════════════

def _insert_job(env, uid: int, status: str) -> None:
    con = sqlite3.connect(str(env.dst))
    try:
        con.execute(f"INSERT INTO {account_purge.JOB_TABLE} (user_id, status) VALUES (?, ?)",
                    (int(uid), status))
        con.commit()
    finally:
        con.close()


def test_done_job_never_requeued(seeded, flag_on, clock, query_counter):
    """已有 ``done`` 作业的账号即使仍在回收站（异常现场）也不当候选（派单：done 永不再入队）。"""
    env = seeded
    _insert_job(env, TGT_UID, account_purge.STATUS_DONE)

    summary = _tick()
    assert summary["candidates"] == [] and _purged_ids(summary) == []
    assert _alive(env.dst, TGT_UID) and len(query_counter) == 1

    # 对照：failed 不算「清完了」，同一条判据下它必须重新入队并被清（退避交给 attempts，不靠漏扫）
    _insert_job(env, TGT2_UID, account_purge.STATUS_FAILED)
    _set_bin(env.factory, TGT2_UID, purge_after=LONG_AGO)
    clock.advance(sched.DEFAULT_MIN_INTERVAL_MINUTES + 1)
    second = _tick()
    assert second["candidates"] == [TGT2_UID], "done 那行仍被排除，failed 那行回到候选"
    assert _purged_ids(second) == [TGT2_UID]
    assert _alive(env.dst, TGT_UID), "done 的号一个字节都不该再动"
    assert _alive(env.dst, TGT2_UID) is False


def test_purged_account_stops_being_a_candidate(seeded, flag_on, clock):
    """真清一遍之后再拍：``users`` 行已没了，候选自然为空（幂等的第二重保险）。"""
    env = seeded
    assert _purged_ids(_tick()) == [TGT_UID]
    sched.reset_state()
    clock.advance(sched.DEFAULT_MIN_INTERVAL_MINUTES + 1)

    second = _tick()
    assert second["candidates"] == [] and _purged_ids(second) == []
    assert _job(env.dst, TGT_UID)["status"] == account_purge.STATUS_DONE


# ═══════════════════════════════════════════════════════════════════════════════
# ⑦ 只做触发：唯一动作是 purge_account(system_actor=True)
# ═══════════════════════════════════════════════════════════════════════════════

def test_tick_only_delegates_to_purge_account(seeded, flag_on, clock, install_purge_spy):
    """打桩清除器后账号行仍然活着 —— 证明本模块**没写任何 DELETE**，删除全在清除器里。"""
    env = seeded
    calls = install_purge_spy()
    _tick()

    assert len(calls) == 1
    kw = calls[0]
    assert kw["target_user_id"] == TGT_UID and kw["actor_user_id"] == sched.SYSTEM_ACTOR_ID
    assert kw["system_actor"] is True, "调度器必须走系统内部通道（无人工确认串可填）"
    assert "body" not in kw and "lang" not in kw, "不带 body：确认串无从伪造"
    assert _alive(env.dst, TGT_UID), "调度器自己动 DELETE 就是越界"
    assert _rows(env.dst, account_purge.JOB_TABLE) == 0 and env.vector_calls == []
    assert (env.data / "trash").exists() is False


def test_concurrent_tick_skips_while_purge_in_flight(seeded, flag_on, clock, query_counter):
    """同一时刻只允许一个清除在跑：锁被占时第二拍直接跳过（宁可晚一轮，不并发压 SQLite）。"""
    async def _scenario():
        await sched._PURGE_LOCK.acquire()
        try:
            return await sched.tick()
        finally:
            sched._PURGE_LOCK.release()

    summary = asyncio.run(_scenario())
    assert summary["skipped"] == "in_flight" and summary["ran"] is False
    assert query_counter == [] and _alive(seeded.dst, TGT_UID)
    assert not sched._PURGE_LOCK.locked(), "跳过时绝不能把锁占住"


def test_interval_rechecked_after_lock_acquired(seeded, flag_on, clock, install_purge_spy,
                                                no_db, monkeypatch):
    """排队到锁的下一拍**必须再核一次间隔**（否则两拍同时越过前置检查时会连着清两个）。

    脚本化 ``_interval_ok``：前置放行、拿锁后拦下 —— 拦下必须发生在开库与清除之前。
    """
    answers = iter([True, False])
    seen: list[datetime] = []

    def _scripted(now):
        seen.append(now)
        return next(answers)

    monkeypatch.setattr(sched, "_interval_ok", _scripted)
    calls = install_purge_spy()
    summary = _tick()

    assert seen and len(seen) == 2, "前置一次、拿锁后一次"
    assert summary["skipped"] == "min_interval" and summary["ran"] is False
    assert calls == [] and summary["candidates"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# ⑧ HTTP 不暴露系统通道（派单红线）
# ═══════════════════════════════════════════════════════════════════════════════

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    return TestClient(app, raise_server_exceptions=False)


def test_http_system_actor_body_cannot_bypass_confirm(seeded, monkeypatch):
    """body 里塞 ``system_actor=true`` **不**绕过 ``confirm_username``（仍 400、零写入）。

    这条钉的是工程红线：端点只转发 ``body``，永不把 body 字段喂给 ``system_actor`` 形参。
    将来谁改成 ``system_actor=body.get("system_actor")``，本用例立刻红。
    """
    env = seeded
    calls: list[dict] = []
    original = account_purge.purge_account

    async def _spy(db, **kw):
        calls.append(dict(kw))
        return await original(db, **kw)

    monkeypatch.setattr(account_purge, "purge_account", _spy)
    c = _client()
    headers = {"Authorization": f"Bearer {create_token(ADMIN_UID)}"}
    url = f"/api/v1/admin/server/accounts/{TGT_UID}/purge"

    for body, needle in (({"system_actor": True}, "confirm_username"),
                         ({"system_actor": True, "force": True}, "confirm_username"),
                         ({"system_actor": "true", "confirm_username": "TGT"}, "不一致"),
                         ({"system_actor": 1, "confirm_username": None}, "confirm_username")):
        r = c.post(url, headers=headers, json=body)
        assert r.status_code == 400, (body, r.text)
        assert needle in r.json()["detail"], (body, r.text)
    assert calls and all("system_actor" not in c0 for c0 in calls), \
        "端点绝不把 system_actor 传进清除器（默认 False 才有效，body 里那份是死键）"
    assert all(c0["actor_user_id"] == ADMIN_UID for c0 in calls)
    assert calls[0]["body"] == {"system_actor": True}, "伪造串确实进了 body，只是没人认领"
    assert _alive(env.dst, TGT_UID) and _rows(env.dst, account_purge.JOB_TABLE) == 0

    # 带正确确认串才走得通（系统通道没有改变人工路径的任何语义）
    ok = c.post(url, headers=headers, json={"confirm_username": "tgt", "system_actor": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == account_purge.STATUS_DONE
    assert "system_actor" not in calls[-1]
    assert _alive(env.dst, TGT_UID) is False


def test_min_interval_env_parsing_falls_back(monkeypatch):
    """最小间隔：非法回落 60、负数按 0（0 = 不限流，仍受「每轮 1 个」与窗口约束）。"""
    for raw, expect in (("", sched.DEFAULT_MIN_INTERVAL_MINUTES), ("abc", 60), ("10", 10),
                        ("-5", 0), ("0", 0), ("  7  ", 7)):
        monkeypatch.setenv(sched.ENV_MIN_INTERVAL_MINUTES, raw)
        assert sched._min_interval_minutes() == expect, raw
