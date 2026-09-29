# -*- coding: utf-8 -*-
"""断点 #12（2026-09-29）：life_followups 死写入已删除的回归用例。

核实结论（output/AMBRACE_断点12_LifeFollowup核实_20260929.md）：
该缓冲只有写、没有任何读取方（pop_followups 全仓 0 调用），且配额闸不判过期 ⇒ 写满
3 条 pending 后 add_followup 永久静默 no-op。故删掉 life_loop 的两处写入点
（W1 play_game 结算、W2 记忆配额未满 + visible），表与模块保留作留痕。

锁四件事：
1. 两条写点路径跑完后 life_followups 不再新增行（临时真实库计数）；
2. 周围业务逻辑未动（日志 completed / 游戏名进 output_json / memory_id 回写 / 事件广播）；
3. 历史行不被这次改动波及（留痕）；followup 模块与 pop_followups 仍可调用；
4. 全仓不再有 add_followup 调用点（防「死写入」复活）。
"""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from _dbclone import clone_engine, make_session_factory

from app.life import life_writer
from app.life import life_loop
from app.life.decision import ACTIONS, Decision, StateSnapshot
from app.life.followup import _now, add_followup, pop_followups
from app.life.life_loop import LifeLoopTask
from app.life.life_state import default_needs, get_life_state
from app.models.character import AICharacter
from app.models.life import LifeActivityLog, LifeFollowup
from app.models.user import User

CHAR_ID = 101
# 非模板句、不含亲属词 ⇒ 一定通过 is_meaningful_summary（W2 旧写入点的前置条件）
SUMMARY = "今天画了一张水彩，颜色层次比上次清楚了。"
GAME = {"session_id": 555, "game_type": "liars_bar", "name": "骗子酒馆"}


def _snap() -> StateSnapshot:
    return StateSnapshot(
        character_id=CHAR_ID, user_id=1, energy=80, focus=50,
        needs=default_needs(), phase="afternoon", mood=60, fatigue=30, anger=10,
        location="home", current_room="living",
    )


def _char() -> SimpleNamespace:
    return SimpleNamespace(id=CHAR_ID, user_id=1, name="小鹿")


async def _count_followups(factory) -> int:
    async with factory() as db:
        return (await db.execute(
            select(func.count()).select_from(LifeFollowup)
        )).scalar() or 0


@pytest.fixture
def factory(monkeypatch, tmp_path):
    """临时真实库（全量 schema）；副作用短事务一律指向它，禁止回落生产库。"""
    engine = clone_engine(os.path.join(str(tmp_path), "lf.db"))
    fac = make_session_factory(engine)

    async def _init():
        async with fac() as db:
            db.add(User(id=1, username="u1", nickname="用户一"))
            db.add(AICharacter(id=CHAR_ID, user_id=1, name="小鹿", personality="外向",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            db.add(AICharacter(id=102, user_id=1, name="阿澄", personality="内向",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(life_loop, "async_session_factory", fac)
    monkeypatch.setattr("app.memory.service.async_session_factory", fac)
    yield fac
    asyncio.run(engine.dispose())


@pytest.fixture
def effects(monkeypatch):
    """替掉慢/外部副作用（开局、LLM 文案、记忆写入、事件广播），保留可观测记录。"""
    calls: dict = {"published": []}

    async def _start_group_game(self, db, char, decision, snap):
        calls["game_started"] = True
        return GAME

    async def _build_summary(self, db, char, decision, act):
        return SUMMARY

    async def _save_memory(**kwargs):
        calls["memory_saved"] = kwargs
        return SimpleNamespace(id=777)

    def _publish_event(self, char, decision, act, memory_id, summary=None):
        calls["published"].append((decision.action, memory_id, summary))

    monkeypatch.setattr(LifeLoopTask, "_start_group_game", _start_group_game)
    monkeypatch.setattr(LifeLoopTask, "_build_summary", _build_summary)
    monkeypatch.setattr(life_writer, "save_life_memory_with_retry", _save_memory)
    monkeypatch.setattr(LifeLoopTask, "_publish_event", _publish_event)
    return calls


def _run_execute(factory, action: str) -> LifeActivityLog:
    """在临时库上真实跑一轮 ``_execute``，返回本轮的活动日志。"""
    async def _do():
        async with factory() as db:
            st = await get_life_state(db, CHAR_ID)
            needs = json.loads(st.needs_json or "{}") or default_needs()
            await LifeLoopTask()._execute(
                db, _char(), st, needs, Decision(action, reason="need"), _snap())
            return (await db.execute(
                select(LifeActivityLog).where(LifeActivityLog.character_id == CHAR_ID)
                .order_by(LifeActivityLog.id.desc()).limit(1)
            )).scalars().first()

    return asyncio.run(_do())


# ── 1. W1：play_game 结算后不再写回聊缓冲 ──────────────────────
def test_play_game写点不再新增followup行(factory, effects):
    assert ACTIONS["play_game"].followup_window == "next_online"  # 旧写入条件曾成立
    assert asyncio.run(_count_followups(factory)) == 0

    log = _run_execute(factory, "play_game")

    assert asyncio.run(_count_followups(factory)) == 0
    # 周围业务逻辑不动：日志 completed、游戏名仍写进 output_json、事件仍广播
    assert log.status == "completed"
    assert GAME["name"] in log.output_json
    assert effects["game_started"] is True
    assert effects["published"] == [("play_game", None, None)]


# ── 2. W2：记忆配额未满 + visible 后不再写回聊缓冲 ─────────────
def test_记忆写点不再新增followup行(factory, effects):
    act = ACTIONS["create"]
    assert (act.memory, act.visible, act.followup_window) == (True, True, "next_online")
    assert asyncio.run(_count_followups(factory)) == 0

    log = _run_execute(factory, "create")

    assert asyncio.run(_count_followups(factory)) == 0
    # 记忆沉淀链路不受影响：memory_id 回写日志、summary 进 output_json、事件带 summary
    assert log.status == "completed"
    assert log.memory_id == 777
    assert SUMMARY in log.output_json
    assert effects["published"] == [("create", 777, SUMMARY)]


# ── 3. 历史行留痕：删写入不删既有行 ────────────────────────────
def test_历史followup行保持不动(factory, effects):
    async def _seed():
        async with factory() as db:
            await add_followup(db, CHAR_ID, 1, "刚烤了饼干", "create", None, "next_online")

    asyncio.run(_seed())
    before = asyncio.run(_count_followups(factory))
    assert before == 1

    _run_execute(factory, "play_game")
    _run_execute(factory, "create")

    async def _check():
        async with factory() as db:
            rows = (await db.execute(select(LifeFollowup))).scalars().all()
            return [(r.summary, r.status) for r in rows]

    assert asyncio.run(_check()) == [("刚烤了饼干", "pending")] * before


# ── 4. 模块/表/读取函数仍可用（不破坏既有引用）─────────────────
def test_followup模块与pop_followups仍可用(factory):
    assert LifeFollowup.__tablename__ == "life_followups"  # 表仍在 ORM 清单里

    async def _do():
        async with factory() as db:
            f = await add_followup(db, CHAR_ID, 1, "逛了美术馆", "go_out", None)
            assert f is not None and f.status == "pending"
            # add_followup 设 not_before=now+1h，这里把它挪到过去以验证取出逻辑未坏
            f.not_before = _now()
            await db.commit()
            popped = await pop_followups(db, CHAR_ID, "next_online")
            return [(r.summary, r.status, r.used_at is not None) for r in popped]

    assert asyncio.run(_do()) == [("逛了美术馆", "used", True)]
    assert asyncio.run(_count_followups(factory)) == 1


# ── 5. 防复活：全仓不再有 add_followup 调用点 ──────────────────
def test_全仓不再有add_followup写点():
    assert not hasattr(life_loop, "add_followup")
    app_dir = Path(life_loop.__file__).resolve().parents[1]  # backend/app
    sites = []
    for py in sorted(app_dir.rglob("*.py")):
        for lineno, line in enumerate(py.read_text(encoding="utf-8-sig").splitlines(), 1):
            code = line.split("#")[0]
            # 只认调用形态（带左括号）；定义本身与文档注释里的提及不算写点
            if "add_followup(" in code and "def add_followup(" not in code:
                sites.append(f"{py.relative_to(app_dir)}:{lineno}")
    assert sites == []
