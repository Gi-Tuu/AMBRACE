# -*- coding: utf-8 -*-
"""M2b 合同：focus 派生只读化 + mood_delta 死字段停写（flag `life_state_dead_fields_v1`，默认关＝历史逐字节等价）。

覆盖：
- flag 注册与默认关（现网零变化）；
- apply_tick：关＝结算回写 focus（旧写入）；开＝结算不再触碰 focus（留旧值＝可判定的「停写」证据）；
- 读侧派生（snapshot / api）以源码棘轮钉住（表达式必须从 energy 现算、flag 条件必须存在）；
- mood_delta 两个写点（life_loop started 日志 / life_home completed 日志）以源码棘轮钉住条件停写。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.life.life_state import clamp, dead_fields_v1_enabled, settle_focus


def _set_flag(monkeypatch, value: bool) -> None:
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "life_state_dead_fields_v1", value)


def test_flag_registered_and_default_off():
    from app.flags.agent_flags import AGENT_FLAGS
    assert AGENT_FLAGS.get("life_state_dead_fields_v1") is False, "默认必须关，现网零变化"
    assert not dead_fields_v1_enabled()
    from app.application import flag_catalog

    assert flag_catalog.FLAG_CATALOG, "flag_catalog 不能为空"
    assert "life_state_dead_fields_v1" in flag_catalog.FLAG_CATALOG


async def _seed(uid, cid):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) VALUES (:u,:n,'n','x')"),
            {"u": uid, "n": f"m2b_{uid}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": cid, "u": uid})
        await db.commit()


async def _tick(cid, phase):
    from app.db.database import async_session_factory
    from app.life.life_state import apply_tick
    async with async_session_factory() as db:
        return await apply_tick(db, cid, phase)


async def _read_state(cid):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        row = (await db.execute(text(
            "SELECT energy, focus FROM life_states WHERE character_id=:c"),
            {"c": cid})).mappings().first()
    return dict(row) if row else None


async def _write_focus(cid, value):
    from app.db.session import engine
    import sqlite3
    with sqlite3.connect(engine.url.database) as db:
        db.execute("UPDATE life_states SET focus=? WHERE character_id=?", (value, cid))
        db.commit()


def test_flag_off_settle_still_writes_focus(monkeypatch):
    """关＝逐字节旧行为：结算后 focus 被回写为 settle_focus(energy)。"""
    _set_flag(monkeypatch, False)
    monkeypatch.setattr("app.life.life_state.random.randint", lambda a, b: 0)

    async def run():
        await _seed(9901, 9911)
        await _tick(9911, "morning")          # 建默认状态并结算一拍
        return await _read_state(9911)

    st = asyncio.run(run())
    expect = clamp(int(st["energy"] * 0.7))   # randint 被钉 0
    assert st["focus"] == expect, f"flag 关必须回写 focus：{st['focus']} != {expect}"


def test_flag_on_settle_stops_touching_focus(monkeypatch):
    """开＝结算不再写 focus：预置哨兵值 77 原样保留（可判定的停写证据）。"""
    _set_flag(monkeypatch, True)

    async def run():
        await _seed(9902, 9912)
        await _tick(9912, "morning")          # 建状态
        await _write_focus(9912, 77)          # 哨兵
        await _tick(9912, "morning")          # 再结算一拍
        return await _read_state(9912)

    st = asyncio.run(run())
    assert st["focus"] == 77, f"flag 开时结算不得回写 focus，实际被改成 {st['focus']}"


def test_read_sites_derive_focus_from_energy(monkeypatch):
    """开＝两个读点（snapshot / api）都从 energy 现算；关＝保持读库值。源码棘轮钉表达式。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]

    loop_src = (root / "app" / "life" / "life_loop.py").read_text(encoding="utf-8")
    assert "settle_focus(st.energy) if dead_fields_v1_enabled() else st.focus" in loop_src, \
        "snapshot 读侧必须按 flag 从 energy 派生 focus"
    api_src = (root / "app" / "api" / "life.py").read_text(encoding="utf-8")
    assert "settle_focus(st.energy) if dead_fields_v1_enabled() else st.focus" in api_src, \
        "api 读侧必须按 flag 从 energy 派生 focus"
    # 派生公式必须复用 settle_focus 本体，不许另造第三套口径
    state_src = (root / "app" / "life" / "life_state.py").read_text(encoding="utf-8")
    assert "if not dead_fields_v1_enabled():" in state_src
    assert state_src.count("st.focus = settle_focus(st.energy)") == 2, "两个结算写点都必须包闸"


def test_mood_delta_write_sites_gated():
    """mood_delta 两个写点必须有 flag 条件（开＝停写、关＝逐字保留旧 kwarg）。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]

    loop_src = (root / "app" / "life" / "life_loop.py").read_text(encoding="utf-8")
    assert '**({} if dead_fields_v1_enabled() else {"mood_delta": act.mood_delta})' in loop_src, \
        "life_loop started 日志必须条件停写 mood_delta"
    home_src = (root / "app" / "api" / "life_home.py").read_text(encoding="utf-8")
    assert '**({} if _dead_fields_v1() else {"mood_delta": spec["mood"]})' in home_src, \
        "life_home completed 日志必须条件停写 mood_delta"


def test_settle_focus_formula_untouched(monkeypatch):
    """派生口径＝settle_focus 本体（energy*0.7 + 抖动 0..30 后截断），不许另造第三套公式。"""
    import inspect
    src = inspect.getsource(settle_focus)
    assert "energy * 0.7" in src and "random.randint(0, 30)" in src
    monkeypatch.setattr("app.life.life_state.random.randint", lambda a, b: 0)
    assert settle_focus(100) == 70 and settle_focus(0) == 0
