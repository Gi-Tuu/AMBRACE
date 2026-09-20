# -*- coding: utf-8 -*-
"""终局持久化鲁棒性 + 护栏小修回归（2026-09-19）。

背景（双重异常叠加）：``_settle_game`` 里 ``persist_state`` + ``finish`` 后没有任何 commit
（commit 在调用方），而 ``_guard_stop_visible`` 先落 end_ev + commit、再 ``_guard_stop``。
一旦增强步骤（build_archive / finalize_game / record_game_result）确定性抛错：

  1. ``_guard_stop_visible`` 末尾的 commit 不执行 → ``finish()`` 的终态随会话退出 rollback
     → ``session.status`` 永远 ``playing``，而 phase=result 的 end_ev 已落库；
  2. ``resume_stuck_games`` / ``_emergency_stop_after_crash`` 见到仍在 playing 便二次止血
     → 又落一条 end_ev（重复结束事件），确定性失败时每 10 分钟再堆一条。

修复口径（只动 ``app/api/games.py``，引擎语义零改动）：
  - ``_settle_game`` / ``_abort_game``：终态先独立 commit，增强步骤各自 try/except + rollback，
    失败只告警、绝不回滚终态；
  - ``_guard_stop_visible``：落 end_ev 前按 session 幂等去重（已有 phase=result 则跳过落库/镜像/
    commit，广播照旧）；
  - ``_apply_fail_force_push``（P3-②）：确定性推进后 (round, phase) 真的移动 → apply_failures 归零；
  - AI 投降被拒（P3-④）：回落引擎兜底动作继续统一 apply 管线，不再 return 留 10 分钟空窗。

测试纪律：临时 sqlite 走 pytest ``tmp_path``、不连生产库、不调真 LLM（AI 续跑 noop）。
"""
import asyncio
import os

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import games as games_api
from app.api.games import router as games_router
from app.auth.deps import get_current_user_id
from app.games import engine_for
from app.games.guardrails import drop_guard, get_guard
from app.models.game import GameEvent, GameSession

pytestmark = pytest.mark.slow


# ────────────────────────── 夹具：临时 sqlite（tmp_path，不碰生产库）──────────────────────────
def _make_client(user_id: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(games_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _noop_ai(sid: int) -> None:
    return


@pytest.fixture
def settle_db(monkeypatch, tmp_path):
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户一"))
            for i in range(101, 106):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.api.games.async_session_factory", factory)
    monkeypatch.setattr("app.memory.service.async_session_factory", factory)
    # memory_bridge.finalize_game 内部按名字取该工厂，重定向到临时库，避免碰生产 sqlite
    monkeypatch.setattr("app.db.database.async_session_factory", factory)
    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    yield factory
    asyncio.run(engine.dispose())


def _create_all_ai_werewolf(client: TestClient) -> int:
    r = client.post("/api/v1/games/sessions", json={
        "game_type": "werewolf", "player_ids": [101, 102, 103, 104],
        "user_as_player": False,
    })
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _stop_visible(factory, sid: int):
    """在独立会话里驱动真实 ``_guard_stop_visible``（走 _guard_stop → _settle_game）。"""
    async with factory() as db:
        s = await db.get(GameSession, sid)
        eng = engine_for(s.game_type)(s)
        await eng.load(db)
        return await games_api._guard_stop_visible(
            db, s, eng, sid, reason="robust_test", reason_tag="robust_test")


async def _final_state(factory, sid: int):
    async with factory() as db:
        s = await db.get(GameSession, sid)
        evs = list((await db.execute(
            select(GameEvent).where(GameEvent.session_id == sid).order_by(GameEvent.id)
        )).scalars().all())
        results = [e for e in evs if e.phase == "result"]
        return s.status, results


# ────────────────────────── ① 增强步骤抛错不回滚终态、不冒泡、end_ev 只一条 ──────────────────────────
def test_settle_finalize_failure_keeps_terminal_and_single_end_event(settle_db, monkeypatch):
    """finalize_game 确定性抛错：对局不再 playing、异常不冒泡、phase=result 结束事件只有一条。

    修复前：finalize 抛错 → _guard_stop_visible 的 commit 不执行 → 终态 rollback → status 卡 playing。
    """
    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)

    async def _boom(*_a, **_k):
        raise RuntimeError("故障注入：finalize_game 确定性抛错")
    monkeypatch.setattr("app.games.memory_bridge.finalize_game", _boom)

    # 不抛异常即「异常不冒泡」；失败止血只走告警
    asyncio.run(_stop_visible(settle_db, sid))

    status, results = asyncio.run(_final_state(settle_db, sid))
    assert status == "finished", f"终态未持久化（不应停留在 playing），status={status}"
    assert len(results) == 1, f"应恰好 1 条 phase=result 结束事件，实际 {len(results)}"
    assert results[0].visibility == "public" and (results[0].content or "").strip()


def test_settle_archive_failure_keeps_terminal(settle_db, monkeypatch):
    """build_archive 确定性抛错（最终态 commit 之前）：终态仍独立落库，后续增强步骤继续跑。

    修复前：archive 抛错 → 异常冒泡到 _guard_stop → session 退出 rollback → status 卡 playing。
    """
    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)

    def _boom(*_a, **_k):
        raise RuntimeError("故障注入：build_archive 确定性抛错")
    monkeypatch.setattr("app.games.archive.build_archive", _boom)

    asyncio.run(_stop_visible(settle_db, sid))

    status, results = asyncio.run(_final_state(settle_db, sid))
    assert status == "finished", f"status={status}"
    assert len(results) == 1, f"应恰好 1 条 phase=result 结束事件，实际 {len(results)}"


# ────────────────────────── ② 二次止血不再新增第二条结束事件（幂等）──────────────────────────
def test_guard_stop_visible_is_idempotent_for_end_event(settle_db, monkeypatch):
    """同一 session 反复 _guard_stop_visible：phase=result 事件只落一条，但每次都广播给前端。"""
    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)

    async def _noop_guard_stop(db, session, engine, reason):
        return None
    monkeypatch.setattr("app.api.games._guard_stop", _noop_guard_stop)

    broadcasts: list = []

    async def _rec(session_id, event, phase):
        broadcasts.append((session_id, event.get("phase"), phase))
    monkeypatch.setattr("app.api.games._broadcast_game_event", _rec)

    for _ in range(3):
        asyncio.run(_stop_visible(settle_db, sid))

    _status, results = asyncio.run(_final_state(settle_db, sid))
    assert len(results) == 1, f"幂等去重失效：应恰好 1 条 phase=result，实际 {len(results)}"
    assert len(broadcasts) == 3, f"广播语义不能被去重吞掉，实际 {len(broadcasts)} 次"
    assert all(b[2] == "result" for b in broadcasts)


# ────────────────────────── ③ P3-② 真实推进后 apply_failures 归零 ──────────────────────────
def test_force_push_real_advance_resets_apply_failures(settle_db, monkeypatch):
    """确定性推进后 (round, phase) 真的移动 → apply_failures 归零；推不动则绝不归零。"""
    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)
    cls = engine_for("werewolf")

    async def _no_winner(self):
        return None
    monkeypatch.setattr(cls, "check_winner", _no_winner)

    async def _adv_moves(self):
        self.session.round = int(self.session.round or 0) + 1
        self.session.phase = "day_speak"
        return []
    monkeypatch.setattr(cls, "advance", _adv_moves)

    async def _run_moves():
        async with settle_db() as db:
            s = await db.get(GameSession, sid)
            eng = engine_for(s.game_type)(s)
            await eng.load(db)
            g = get_guard(sid)
            g.apply_failures = 5
            rp_before = (int(s.round or 0), s.phase or "")
            ended = await games_api._apply_fail_force_push(db, s, eng, sid, 0, 1)
            return ended, g.apply_failures, rp_before, (int(s.round or 0), s.phase or "")

    ended, fails, rp_before, rp_after = asyncio.run(_run_moves())
    assert ended is False
    assert rp_after != rp_before, "测试装置应真的推进 (round, phase)"
    assert fails == 0, f"真实推进后 apply_failures 应归零，实际 {fails}"

    # 反向锚定：advance 返回空且 rp 不变 → 连续失败序列保持，不得误清零
    async def _adv_stuck(self):
        return []
    monkeypatch.setattr(cls, "advance", _adv_stuck)

    async def _run_stuck():
        async with settle_db() as db:
            s = await db.get(GameSession, sid)
            eng = engine_for(s.game_type)(s)
            await eng.load(db)
            g = get_guard(sid)
            g.apply_failures = 5
            await games_api._apply_fail_force_push(db, s, eng, sid, 0, 1)
            return g.apply_failures

    assert asyncio.run(_run_stuck()) == 5, "推不动不得清零连续失败序列"
    drop_guard(sid)
