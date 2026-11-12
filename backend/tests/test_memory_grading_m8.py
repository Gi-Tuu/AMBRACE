# -*- coding: utf-8 -*-
"""M8a 合同：记忆分级落地（读侧标注 / 核心区标注 / 晋升认知闸 / UNKNOWN 取值）。

flag `memory_grading_v1` 默认关 ⇒ 三处逐字旧行为；开 ⇒ 未标注按 UNVERIFIED、
推断/计划/虚构/未核/未知不得晋升核心、核心区注入带前缀。
"""
import asyncio
from pathlib import Path

from sqlalchemy import text

APP = Path(__file__).resolve().parents[1] / "app"


def test_flag_off_keeps_legacy_prefixes(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import grading
    from app.memory.format import epistemic_prefix

    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, False)
    assert epistemic_prefix(None) == ""
    assert epistemic_prefix("FACT") == ""
    assert epistemic_prefix("INFERRED") == "[INFERRED] "
    assert grading.core_allowed(None) is True
    assert grading.core_allowed("INFERRED") is True


def test_flag_on_labels_missing_and_unknown(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import grading
    from app.memory.format import epistemic_prefix

    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, True)
    assert "UNKNOWN" in grading.GRADES
    assert epistemic_prefix(None) == "[UNVERIFIED] "
    assert epistemic_prefix("") == "[UNVERIFIED] "
    assert epistemic_prefix("FACT") == ""
    assert epistemic_prefix("UNKNOWN") == "[UNKNOWN] "
    assert epistemic_prefix("INFERRED") == "[INFERRED] "


def test_flag_on_core_gate(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import grading

    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, True)
    assert grading.core_allowed("FACT") is True
    for g in ("INFERRED", "PLANNED", "FICTIONAL", "UNVERIFIED", "UNKNOWN", None, "未登记值"):
        assert grading.core_allowed(g) is False, g


def test_core_injection_uses_prefix_ratchet():
    src = (APP / "agent" / "context" / "section_memories.py").read_text(encoding="utf-8")
    assert src.count("epistemic_prefix(") >= 2, "核心/锚点两行都要接前缀"
    assert "getattr(m, 'epistemic_status', None)" in src, "核心行必须读 epistemic_status（否则前缀恒空）"


def test_promotion_gate_integration(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import grading, core
    from app.db.database import async_session_factory

    uid, cid = 9951, 9961

    async def seed():
        async with async_session_factory() as db:
            await db.execute(text(
                "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) "
                "VALUES (:u,:n,'n','x')"), {"u": uid, "n": f"m8_{uid}"})
            await db.execute(text(
                "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,"
                "talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
                "VALUES (:c,:u,'c',0,0,1,0,0)"), {"c": cid, "u": uid})
            await db.commit()

    async def make(epi: str) -> int:
        from app.models.memory import Memory
        async with async_session_factory() as db:
            m = Memory(user_id=uid, character_id=cid, memory_type="event",
                       content=f"m8 promotion {epi}", importance=95.0,
                       confirmation_count=3, is_core=False, source="chat",
                       epistemic_status=epi, scope="private")
            db.add(m)
            await db.commit()
            return int(m.id)

    async def is_core(mid: int) -> bool:
        async with async_session_factory() as db:
            return bool((await db.execute(text("SELECT is_core FROM memories WHERE id=:m"),
                                          {"m": mid})).scalar_one())

    asyncio.run(seed())
    # flag 关：INFERRED 照旧可晋升（历史行为）
    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, False)
    m1 = asyncio.run(make("INFERRED"))
    asyncio.run(core.maybe_promote_core(m1, 95.0, "event", "event"))
    assert asyncio.run(is_core(m1)) is True, "flag 关时晋升条件必须逐字不变"

    # flag 开：INFERRED 不得晋升；FACT 照旧可晋升
    monkeypatch.setitem(AGENT_FLAGS, grading.FLAG, True)
    m2 = asyncio.run(make("INFERRED"))
    asyncio.run(core.maybe_promote_core(m2, 95.0, "event", "event"))
    assert asyncio.run(is_core(m2)) is False, "flag 开时推断记忆不得进核心"
    m3 = asyncio.run(make("FACT"))
    asyncio.run(core.maybe_promote_core(m3, 95.0, "event", "event"))
    assert asyncio.run(is_core(m3)) is True, "事实仍按原判据晋升"
