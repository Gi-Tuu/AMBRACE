# -*- coding: utf-8 -*-
"""M3-a 工作记忆回归测试（2026-09-01）：不打桩内部结构——真实内存库 + 真实服务链路。

覆盖：
- apply_desired 纯函数：add/update/resolve/上限裁剪/证据门控/无变化不写；
- maybe_evaluate_working_state：真实会话建行+supersede、30min 节流、证据幻觉丢弃、flag 关零行为；
- 写入侧无条件标 superseded（P1-2）。
LLM 提取是外部服务边界，测试在 service 边界 patch chat_completion（与项目既有测试一致）。
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture
def ws_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="ws_m3a_test_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=11, user_id=1, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())

    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "working_state_enabled", True)
    yield factory
    asyncio.run(engine.dispose())


def _seed_turn(factory, *, memories: list[str], memory_ids: list[int] | None = None):
    """预置历史消息 + 本轮新增记忆（真实行），返回 turn_started_at。"""
    from app.models.memory import Memory
    from app.models.chat import ChatSession
    from app.models.chat import ChatMessage

    async def _run():
        async with factory() as db:
            db.add(ChatSession(id=7, user_id=1, character_id=11))
            db.add(ChatMessage(session_id=7, sender_type="user", content="我下周要考试，最近在复习"))
            await db.commit()
            for i, content in enumerate(memories):
                db.add(Memory(id=(memory_ids or [500, 501, 502, 503])[i], user_id=1, character_id=11,
                              memory_type="event", content=content, scope="private"))
            await db.commit()
        return datetime.utcnow() - timedelta(minutes=5)

    return asyncio.run(_run())


def _patch_extraction(monkeypatch, payload: dict):
    """在 service 边界 patch LLM 提取（外部服务边界，与项目既有测试一致）。"""
    import json as _json
    from app.services import working_state_service as svc

    async def fake_completion(**kw):
        prompt = kw["messages"][-1]["content"]
        assert "当前工作记忆" in prompt
        return _json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(svc._llm, "chat_completion", fake_completion)


def _rows(factory):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(Memory).where(Memory.memory_type == "working_state").order_by(Memory.id)
            )).scalars().all()
            return [(r.id, r.status, r.superseded_by, json.loads(r.content)) for r in rows]
    import json
    return asyncio.run(_run())


import json  # noqa: E402  （供 _rows 解析 content）

from app.memory import working_state as ws  # noqa: E402
from app.services import working_state_service as svc  # noqa: E402


# ── 纯函数层 ──


def test_apply_desired_add_and_evidence_gating():
    desired = {"ongoing": [{"topic": "下周考试", "detail": "复习中", "evidence_ids": [500, 99999]}],
               "relationship_notes": [], "open_questions": []}
    new, stats = ws.apply_desired(None, desired, valid_evidence={500}, now_iso="2026-09-01T12:00:00")
    # 幻觉 id 99999 被门控剔除，有效 500 保留 → 条目成立
    assert stats["added"] == 1 and new["ongoing"][0]["evidence_ids"] == [500]
    # 完全无有效证据的条目 → 丢弃
    desired2 = {"ongoing": [{"topic": "无据条目", "evidence_ids": [88888]}],
                "relationship_notes": [], "open_questions": []}
    new2, stats2 = ws.apply_desired(None, desired2, valid_evidence={500}, now_iso="t")
    assert new2 is None and stats2["dropped_no_evidence"] == 1


def test_apply_desired_update_resolve_and_caps():
    cur = ws.empty_state()
    cur["ongoing"] = [{"topic": "旧话题", "detail": "旧", "evidence_ids": [1], "updated_at": "t0"},
                      {"topic": "将完结", "detail": "x", "evidence_ids": [2], "updated_at": "t0"}]
    desired = {"ongoing": [{"topic": "旧话题", "detail": "新进展", "evidence_ids": [3]}],
               "relationship_notes": [], "open_questions": []}
    new, stats = ws.apply_desired(cur, desired, valid_evidence={1, 2, 3}, now_iso="t1")
    # 旧话题更新（证据合并 [1,3]）；"将完结"未出现在期望 → resolve
    topics = [e["topic"] for e in new["ongoing"]]
    assert topics == ["旧话题"] and stats["updated"] == 1 and stats["resolved"] == 1
    assert new["ongoing"][0]["evidence_ids"] == [3, 1], "新证据在前、旧证据保留"
    # 无变化 → None（不写新行）
    new2, _ = ws.apply_desired(new, desired, valid_evidence={1, 2, 3}, now_iso="t1")
    assert new2 is None


def test_apply_desired_bucket_cap():
    desired = {"ongoing": [{"topic": f"话题{i}", "evidence_ids": [i + 1]} for i in range(6)],
               "relationship_notes": [], "open_questions": []}
    new, _ = ws.apply_desired(None, desired, valid_evidence=set(range(1, 10)), now_iso="t")
    assert len(new["ongoing"]) == ws.BUCKET_LIMITS["ongoing"] == 3


# ── 服务层（真实会话）──


def test_evaluate_turn_creates_row_and_supersedes(ws_db, monkeypatch):
    from app.models.memory import Memory
    turn_started = _seed_turn(ws_db, memories=["用户提到下周要考试"], memory_ids=[600])
    _patch_extraction(monkeypatch, {
        "ongoing": [{"topic": "考试复习", "detail": "下周考试", "evidence_ids": [600]}],
        "relationship_notes": [], "open_questions": [],
    })

    async def _run():
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            assert await svc.get_latest(db, 1, 11) is None
        await svc.maybe_evaluate_working_state(1, 11, 7, "我下周要考试", "加油哦")

    asyncio.run(_run())
    rows = _rows(ws_db)
    assert len(rows) == 1
    rid, status, sup, content = rows[0]
    assert status == "active" and sup is None
    assert content["ongoing"][0]["topic"] == "考试复习"
    assert content["ongoing"][0]["evidence_ids"] == [600], "evidence 门控：只保留真实 Memory.id"

    # 第二轮：30 分钟节流生效 → 不写新行
    _patch_extraction(monkeypatch, {"ongoing": [], "relationship_notes": [], "open_questions": []})
    turn2 = datetime.utcnow()

    async def _run2():
        await svc.maybe_evaluate_working_state(1, 11, 7, "又说话了", "嗯")

    asyncio.run(_run2())
    assert len(_rows(ws_db)) == 1, "节流窗口内不评估"


def test_evaluate_turn_flag_off_is_noop(ws_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "working_state_enabled", False)
    turn_started = _seed_turn(ws_db, memories=["用户提到下周要考试"], memory_ids=[601])
    _patch_extraction(monkeypatch, {"ongoing": [{"topic": "x", "evidence_ids": [601]}],
                                    "relationship_notes": [], "open_questions": []})

    async def _run():
        await svc.maybe_evaluate_working_state(1, 11, 7, "x", "y")

    asyncio.run(_run())
    assert _rows(ws_db) == [], "flag 关 = 完全无行为"


def test_evaluate_turn_supersedes_old_active_row(ws_db, monkeypatch):
    """节流窗口已过（旧行 created_at 推前 1 小时）→ 第二轮正常滚动覆盖并标 superseded。"""
    turn1 = _seed_turn(ws_db, memories=["用户提到下周要考试"], memory_ids=[610])
    _patch_extraction(monkeypatch, {"ongoing": [{"topic": "考试复习", "detail": "v1", "evidence_ids": [610]}],
                                    "relationship_notes": [], "open_questions": []})

    async def _run1():
        await svc.maybe_evaluate_working_state(1, 11, 7, "我下周要考试", "加油")

    asyncio.run(_run1())
    # 把旧行 created_at 推前 2 小时（模拟节流窗口已过）+ 本轮新增记忆 611
    from app.models.memory import Memory

    async def _age():
        async with ws_db() as db:
            row = (await db.execute(select(Memory).where(Memory.memory_type == "working_state"))).scalars().first()
            row.created_at = datetime.utcnow() - timedelta(hours=2)
            db.add(Memory(id=900, user_id=1, character_id=11, memory_type="event",
                          content="考试定在周四", scope="private"))
            await db.commit()

    asyncio.run(_age())
    _patch_extraction(monkeypatch, {"ongoing": [{"topic": "考试复习", "detail": "v2 定在周四", "evidence_ids": [900]}],
                                    "relationship_notes": [], "open_questions": []})
    turn2 = datetime.utcnow()

    async def _run2():
        await svc.maybe_evaluate_working_state(1, 11, 7, "考试定在周四了", "好好复习")

    asyncio.run(_run2())
    rows = _rows(ws_db)
    assert len(rows) == 2
    old, new = rows[0], rows[1]
    assert old[1] == "superseded" and old[2] == new[0], "旧行标 superseded 指向新行（P1-2 无条件写）"
    assert new[1] == "active"
    assert new[3]["ongoing"][0]["detail"] == "v2 定在周四"


def test_evaluate_turn_bad_json_skipped(ws_db, monkeypatch):
    turn_started = _seed_turn(ws_db, memories=["用户提到下周要考试"], memory_ids=[620])
    from app.services import working_state_service as _svc

    async def fake_bad(**kw):
        return "这不是 JSON"

    monkeypatch.setattr(_svc._llm, "chat_completion", fake_bad)

    async def _run():
        await svc.maybe_evaluate_working_state(1, 11, 7, "x", "y")

    asyncio.run(_run())
    assert _rows(ws_db) == [], "JSON 非法 → fail-open 跳过"

def test_carried_no_rewrite():
    """W3（2026-09-01）：carried 条目不刷新 updated_at——完全相同的期望三桶应返回 (None, stats)，不写新行。"""
    cur = {
        "version": 1,
        "ongoing": [{"topic": "t", "detail": "d", "evidence_ids": [1], "updated_at": "2026-09-01T08:00:00"}],
        "relationship_notes": [],
        "open_questions": [],
    }
    desired = {
        "version": 1,
        "ongoing": [{"topic": "t", "detail": "d", "evidence_ids": [1]}],
        "relationship_notes": [],
        "open_questions": [],
    }
    new, stats = ws.apply_desired(cur, desired, valid_evidence={1}, now_iso="2026-09-01T12:00:00")
    assert new is None, "carried-only 评估应判定无变化、不写新行"
    assert stats["carried"] == 1 and stats["added"] == 0 and stats["updated"] == 0
