# -*- coding: utf-8 -*-
"""M1 合同：生活结算 ΔT 化（flag `life_settle_dt_v2`，默认关＝历史逐字节等价）。

覆盖：
- `settle_energy/settle_needs` 的 hours 缩放与「hours=1 等价旧公式」
- apply_tick：flag 关仍是「一次调用＝一拍」（8 小时只 +5）
- apply_tick：flag 开按真实间隔切拍（8h→8 拍、100h→24 拍上限、60s→1 拍）
- life_loop 夜间结算在 v2 下被单入口取代（源码棘轮 + 行为）
"""
import asyncio
import json
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _seed(uid=9801, cid=9811):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"),
            {"u": uid, "n": f"m1_{uid}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": cid, "u": uid})
        await db.commit()


def _set_last_tick(cid, hours_ago):
    from app.db.session import engine
    import sqlite3
    ts = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago))
    with sqlite3.connect(engine.url.database) as db:
        db.execute("UPDATE life_states SET last_tick_at=? WHERE character_id=?",
                   (ts.strftime("%Y-%m-%d %H:%M:%S.%f"), cid))
        db.commit()


async def _state(cid):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        row = (await db.execute(text(
            "SELECT energy,needs_json,phase,last_tick_at FROM life_states WHERE character_id=:c"),
            {"c": cid})).mappings().first()
    return row


def test_settle_energy_scales_with_hours():
    from app.life.life_state import settle_energy

    # hours=1 与历史行为完全一致
    assert settle_energy(50, "sleep") == settle_energy(50, "sleep", hours=1.0) == 55
    assert settle_energy(50, "morning") == 48
    # 时长缩放
    assert settle_energy(20, "sleep", hours=2.0) == 30
    assert settle_energy(50, "morning", hours=2.0) == 46
    # 活动成本不随时长缩放
    assert settle_energy(50, "morning", activity_cost=6, hours=1.0) == 42
    # 上限 clamp
    assert settle_energy(95, "sleep", hours=4.0) == 100


def test_settle_needs_hours_one_matches_legacy_formula():
    from app.life.life_state import (NEEDS, NEEDS_BASELINE, NEEDS_DECAY,
                                     NEEDS_RECOVER_MAX, NEEDS_RECOVER_RATE,
                                     clamp, settle_needs)

    start = {k: 55 for k in NEEDS}
    random.seed(1234)
    got = settle_needs(start)
    random.seed(1234)
    expect = {}
    for k in NEEDS:
        v = 55
        v += random.randint(2, 5)
        if v > NEEDS_BASELINE:
            v -= int((v - NEEDS_BASELINE) * NEEDS_DECAY)
        else:
            v += min(NEEDS_RECOVER_MAX, int((NEEDS_BASELINE - v) * NEEDS_RECOVER_RATE))
        expect[k] = clamp(v)
    assert got == expect, "hours=1 必须与历史公式逐字一致"

    # 多小时应走更多步（同一低值起点，恢复量更大）
    random.seed(7)
    one_hour = settle_needs({k: 10 for k in NEEDS}, hours=1.0)
    random.seed(7)
    three_hours = settle_needs({k: 10 for k in NEEDS}, hours=3.0)
    assert sum(three_hours.values()) > sum(one_hour.values())


def _counting(monkeypatch):
    from app.life import life_state

    calls = {"n": 0}
    real = life_state.settle_energy

    def wrapper(energy, phase, activity_cost=0, hours=1.0):
        calls["n"] += 1
        calls.setdefault("phases", []).append(phase)
        return real(energy, phase, activity_cost, hours=hours)

    monkeypatch.setattr(life_state, "settle_energy", wrapper)
    return calls


def test_apply_tick_legacy_off_is_one_tick(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.life.life_state import apply_tick

    monkeypatch.setitem(AGENT_FLAGS, "life_settle_dt_v2", False)
    calls = _counting(monkeypatch)

    async def run():
        await _seed(9801, 9811)
        await apply_tick_db(9811, "sleep")          # 首次：建默认状态
        _set_last_tick(9811, 8)
        before = await _state(9811)
        await apply_tick_db(9811, "sleep")          # flag 关：仍然只一拍
        after = await _state(9811)
        return before, after

    async def apply_tick_db(cid, phase):
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            return await apply_tick(db, cid, phase)

    before, after = asyncio.run(run())
    assert calls["n"] == 2, f"flag 关时两次调用应只结算 2 拍，实际 {calls['n']}"
    assert after["last_tick_at"] > before["last_tick_at"]


def test_apply_tick_v2_slices_by_real_gap(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.life.life_state import apply_tick

    monkeypatch.setitem(AGENT_FLAGS, "life_settle_dt_v2", True)
    calls = _counting(monkeypatch)

    async def tick(cid, phase):
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            return await apply_tick(db, cid, phase)

    async def run():
        await _seed(9802, 9812)
        await tick(9812, "sleep")
        _set_last_tick(9812, 8)
        n0 = calls["n"]
        await tick(9812, "sleep")
        eight = calls["n"] - n0
        _set_last_tick(9812, 100)
        n1 = calls["n"]
        await tick(9812, "sleep")
        capped = calls["n"] - n1
        _set_last_tick(9812, 1 / 60)
        n2 = calls["n"]
        await tick(9812, "sleep")
        tiny = calls["n"] - n2
        return eight, capped, tiny, await _state(9812)

    eight, capped, tiny, st = asyncio.run(run())
    assert eight == 8, f"8 小时间隔应切 8 拍，实际 {eight}"
    assert capped == 24, f"100h 间隔应被 MAX_CATCHUP 夹到 24 拍，实际 {capped}"
    assert tiny == 1, f"1 分钟间隔应只 1 拍，实际 {tiny}"
    assert st["energy"] >= 20


def test_life_loop_night_settlement_is_single_entry_under_v2():
    """源码棘轮：夜间结算必须先在 _settle_dt_v2() 判据之下（flag 关才结算）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "life" / "life_loop.py").read_text(
        encoding="utf-8")
    assert "if not _settle_dt_v2():" in src, "夜间结算缺 ΔT 判据 ⇒ 双跑回归"
    idx = src.index("if not _settle_dt_v2():")
    window = src[idx:idx + 200]
    assert "apply_tick" in window, "判据后面应紧跟 apply_tick（否则判据形同虚设）"
