# -*- coding: utf-8 -*-
"""B16-a 合同（2026-11-07）：覆盖更新重写分级——内容重写＝一次新写入。

口径（用户拍板）：`memory_write_gate_v1` 开时，summary.py 两处**覆盖分支**（印象摘要/身份画像）
重写既有置顶行内容的同时必须按闸重写 `epistemic_status`（与新建同口径 INFERRED）；
关＝历史行为（不碰该字段，保持原值）。working_state 本就是「新建+superseded」语义已过闸，
不在本合同范围。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.memory import summary as _summary
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.user import User
from app.utils.timeutil import now_naive_utc

pytestmark = pytest.mark.slow

CHAR_ID = 210
USER_ID = 1
NEW_SUMMARY = "B16a 覆盖后的新摘要内容"


def _set_gate(monkeypatch, value: bool) -> None:
    from app.flags.agent_flags import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_gate_v1", value)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + LLM 打桩（摘要文本固定），summary 的会话工厂指到克隆库。"""
    engine = clone_engine(tmp_path / "b16a.db")
    factory = make_session_factory(engine)

    async def _seed_owner():
        async with factory() as db:
            db.add(User(id=USER_ID, username="b16a_u1", nickname="B16a 用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name="B16a 角色"))
            await db.commit()

    asyncio.run(_seed_owner())
    monkeypatch.setattr(_summary, "async_session_factory", factory)
    async def _llm(messages=None, **_kw):
        return NEW_SUMMARY

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)

    async def _v2_true(*a, **_kw):
        return True

    monkeypatch.setattr("app.memory.flags.memory_v2_enabled", _v2_true)
    yield {"factory": factory}
    asyncio.run(engine.dispose())


async def _v2_true(*_a, **_kw):
    return True


def _seed_pinned(sub_type: str, epistemic: str | None):
    """落一条历史置顶行（epistemic 为闸开前的旧值）＋几条画像素材（summarize_identity 需要原料）。"""
    async def _run():
        async with _summary.async_session_factory() as db:
            ts = now_naive_utc() - timedelta(hours=2)
            m = Memory(user_id=USER_ID, character_id=CHAR_ID,
                       memory_type="user_info", sub_type=sub_type,
                       source="summary", content="旧的那条内容",
                       importance=100.0, is_pinned=True,
                       epistemic_status=epistemic, created_at=ts)
            m.updated_at = ts
            db.add(m)
            for k in range(3):
                mat = Memory(user_id=USER_ID, character_id=CHAR_ID,
                             memory_type="user_info", source="chat",
                             content=f"B16a 素材 {k}：用户说了要做的事",
                             importance=60.0, created_at=ts)
                mat.updated_at = ts
                db.add(mat)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _row(memory_id: int):
    async def _run():
        async with _summary.async_session_factory() as db:
            return (await db.execute(
                select(Memory).where(Memory.id == memory_id))).scalar_one()
    return asyncio.run(_run())


def test_overwrite_pinned_summary_regrades_when_on(monkeypatch, env):
    _set_gate(monkeypatch, True)
    mid = _seed_pinned("summary", None)          # 闸开前的历史 NULL
    out = asyncio.run(_summary.summarize_memories(CHAR_ID, "user_info", force=True))
    assert out.get("generated") is True and out["memory_id"] == mid, "应走覆盖分支"
    row = _row(mid)
    assert row.content == NEW_SUMMARY
    assert row.epistemic_status == "INFERRED", \
        f"闸开时覆盖更新必须重写分级，实际 {row.epistemic_status!r}"


def test_overwrite_pinned_summary_untouched_when_off(monkeypatch, env):
    _set_gate(monkeypatch, False)
    mid = _seed_pinned("summary", None)
    out = asyncio.run(_summary.summarize_memories(CHAR_ID, "user_info", force=True))
    assert out.get("generated") is True and out["memory_id"] == mid
    row = _row(mid)
    assert row.content == NEW_SUMMARY
    assert row.epistemic_status is None, "闸关时覆盖更新不得触碰分级字段（历史行为）"


def test_overwrite_identity_regrades_when_on(monkeypatch, env):
    _set_gate(monkeypatch, True)
    mid = _seed_pinned("identity", None)
    out = asyncio.run(_summary.summarize_identity(CHAR_ID, USER_ID, force=True))
    assert out.get("generated") is True and out["memory_id"] == mid, "应走覆盖分支"
    row = _row(mid)
    assert row.content == NEW_SUMMARY
    assert row.epistemic_status == "INFERRED"


def test_overwrite_identity_untouched_when_off(monkeypatch, env):
    _set_gate(monkeypatch, False)
    mid = _seed_pinned("identity", None)
    out = asyncio.run(_summary.summarize_identity(CHAR_ID, USER_ID, force=True))
    assert out.get("generated") is True and out["memory_id"] == mid
    row = _row(mid)
    assert row.content == NEW_SUMMARY
    assert row.epistemic_status is None


def test_overwrite_branches_ratchet():
    """两处覆盖分支的接闸必须原样在位（防回退）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "memory" / "summary.py").read_text(encoding="utf-8")
    guard = "if _gate.enabled():\n                target.epistemic_status = _gate.epistemic_for(\"summary\")"
    assert src.count(guard) == 2, "印象摘要与身份画像两处覆盖分支都必须接闸"
    assert src.count("target.epistemic_status = _gate.epistemic_for(\"summary\")") == 2
