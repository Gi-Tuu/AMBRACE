# -*- coding: utf-8 -*-
"""受邀码清理 tick（scheduler._invite_cleanup_tick）回归 —— P3-4，2026-09-26 批 C/D。

account_invites 此前只增不清（5 分钟有效、一次性）。本用例造 5 类行跑一次 tick，断言
**只删**「过期未用」与「已用且 used_at 早于 7 天」两类，其余全留；再跑一次断言幂等（0 删除）。

第 5 类（已用 + 已过期但未满 7 天）专门盯「①条件里的 used_by IS NULL」——写错成
「expires_at < now 就删」会把刚兑换的码连带抹掉（审计留痕丢失）。

隔离口径：临时库走 tests/_dbclone 模板克隆；时间用 monkeypatch 钉在 NOW；不触生产库、不写 backend/data/。
"""
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.scheduling.scheduler as scheduler_mod

pytestmark = pytest.mark.slow

_SCHEDULER_PY = Path(__file__).resolve().parent.parent / "app" / "scheduling" / "scheduler.py"

# 钉住的「本拍时间」（UTC naive，与 now_naive_utc 同口径）
NOW = datetime(2026, 9, 26, 12, 0, 0)
ROOT_UID = 8801          # creator_id 有 FK → users，必须先种一行
SUB_UID = 8802           # used_by 无 FK（P3-4 同源事实），不需要真实用户

# (code, used_by, used_at, expires_at, 应删?)
ROWS = [
    ("ALIVE001", None,    None,                        NOW + timedelta(minutes=4), False),  # 未过期未用
    ("EXPR0001", None,    None,                        NOW - timedelta(minutes=1), True),   # 过期未用
    ("USEDOLD1", SUB_UID, NOW - timedelta(days=8),     NOW - timedelta(days=8),    True),   # 已用超 7 天
    ("USEDNEW1", SUB_UID, NOW - timedelta(days=1),     NOW - timedelta(days=1),    False),  # 已用未满 7 天
    ("USEDBUT1", SUB_UID, NOW - timedelta(hours=6),    NOW - timedelta(hours=7),   False),  # 已用+已过期但未满 7 天
]
KEPT = {r[0] for r in ROWS if not r[4]}
ALL_CODES = {r[0] for r in ROWS}


@pytest.fixture()
def invite_env(tmp_path, monkeypatch):
    """临时库 + 把 scheduler 的会话工厂与本拍时间指过去（tick 用的是模块级名字）。"""
    engine = clone_engine(tmp_path / "invites.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(scheduler_mod, "async_session_factory", factory)
    monkeypatch.setattr(scheduler_mod, "now_naive_utc", lambda: NOW)
    yield factory
    engine.sync_engine.dispose()


def _seed(factory) -> None:
    from app.models.user import AccountInvite, User

    async def _run():
        async with factory() as db:
            db.add(User(id=ROOT_UID, username="inv_root", nickname="受邀码主账号"))
            await db.flush()
            for code, used_by, used_at, expires_at in (r[:4] for r in ROWS):
                db.add(AccountInvite(code=code, creator_id=ROOT_UID, expires_at=expires_at,
                                     used_by=used_by, used_at=used_at))
            await db.commit()

    asyncio.run(_run())


def _codes(factory) -> set[str]:
    from app.models.user import AccountInvite

    async def _run():
        async with factory() as db:
            return set((await db.execute(select(AccountInvite.code))).scalars().all())

    return asyncio.run(_run())


def test_只删过期未用与已用超7天其余保留(invite_env, caplog):
    """三类保留行一类都不能动；两类目标行必须消失，且 tick 真的跑到了（INFO 里 deleted=2）。"""
    _seed(invite_env)
    assert _codes(invite_env) == ALL_CODES

    # 接线自查：tick 必须挂在每日一次台账上（挨着 anniversary），否则本用例只测了个没人调的函数
    src = _SCHEDULER_PY.read_text(encoding="utf-8-sig")
    assert 'await run_daily_if_due("invite_cleanup", _invite_cleanup_tick, reason="tick")' in src

    with caplog.at_level(logging.INFO, logger="scheduler.engine"):
        asyncio.run(scheduler_mod._invite_cleanup_tick())

    left = _codes(invite_env)
    assert left == KEPT, f"多删={left - KEPT} 漏删={KEPT - left}"
    assert any("deleted=2" in r.getMessage() for r in caplog.records), \
        "必须看到清理留痕：查库失败被 except 吞掉时也是 0 行受影响，会伪装成通过"


def test_再跑一次幂等零删除(invite_env, caplog):
    """第二次 0 删除、保留行不变（台账每日一次 + tick 自身幂等）。"""
    _seed(invite_env)
    with caplog.at_level(logging.INFO, logger="scheduler.engine"):
        asyncio.run(scheduler_mod._invite_cleanup_tick())
        caplog.clear()
        asyncio.run(scheduler_mod._invite_cleanup_tick())

    assert _codes(invite_env) == KEPT
    assert any("deleted=0" in r.getMessage() for r in caplog.records), "第二次必须报告 0 删除"
