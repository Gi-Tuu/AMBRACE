# -*- coding: utf-8 -*-
"""P2-1（2026-09-18）回归：游戏 AI 回合「双重 apply 失败」静默卡死。

覆盖：
- 纯函数：SessionGuard.apply_failures 连续计数阈值/归零语义（FORCE=2 / ABORT=3）；
- ai_player：action 为空/非字符串/与当前 expected 不符/引擎动作集外 → 直接 fallback_action；
- 故障注入集成：fallback_action 恒返回非法动作 + apply_action 恒拒绝 →
  有限次内（≤ ABORT 阈值）必然止血终局，并落「用户可见结束事件 + 计数」持久化记录；
- 故障注入集成：apply 连续失败但确定性硬强推可自愈 → 达 FORCE 阈值强推，随后 apply 成功
  计数归零、对局正常分出胜负，不落止血事件；
- 零行为变化锚定：正常 apply 成功路径不涨 apply_failures、不产生止血事件。

不连真实库、不调真 LLM；集成用例走临时 sqlite。
"""
import asyncio
import json
import os

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import games as games_api
from app.api.games import router as games_router
from app.auth.deps import get_current_user_id
from app.games import guardrails
from app.games.base import ActionResult, GameContext, PlayerView
from app.games.guardrails import (
    APPLY_FAIL_ABORT_LIMIT, APPLY_FAIL_FORCE_LIMIT, SessionGuard, drop_guard, get_guard,
)
from app.models.game import GameEvent, GameSession

# 模块加载时捕获真实 resume 实现（fixture 会 noop 掉模块属性，供集成用例直接驱动）
_REAL_RESUME = games_api._resume_ai_turns


# ---------------- 纯函数：apply_failures 计数语义 ----------------
def test_apply_failure_counter_thresholds_and_reset():
    """第 1 次失败=确定性阶段推进（<FORCE）；第 2 次达 FORCE；第 3 次达 ABORT；成功后归零。"""
    assert APPLY_FAIL_FORCE_LIMIT == 2 and APPLY_FAIL_ABORT_LIMIT == 3
    g = SessionGuard()
    assert g.apply_failures == 0
    assert g.bump_apply_failure() == 1 < APPLY_FAIL_FORCE_LIMIT
    assert g.bump_apply_failure() == APPLY_FAIL_FORCE_LIMIT
    assert g.bump_apply_failure() == APPLY_FAIL_ABORT_LIMIT
    g.reset_apply_failures()
    assert g.apply_failures == 0


# ---------------- ai_player：轻量动作校验（不连库、不连 LLM）----------------
class _StubEngine:
    """只实现 ai_decide 需要的三个接口，用来验证 action 校验 → fallback 的分流。"""

    def __init__(self, expected: str):
        self._expected = expected
        self.fallback_calls = 0
        self.session = type("_S", (), {"user_id": 1})()

    def build_ai_prompt(self, seat: int) -> GameContext:
        return GameContext(
            game_type="stub", rules_summary="", public_events=[], players_public=[],
            my_view=PlayerView(seat=seat, player_type="ai", character_id=None, name="AI",
                               role="r", alive=True, is_spectator=False),
            my_persona={}, phase="p", round=1, my_turn=True,
        )

    def expected_action(self, seat: int) -> str:
        return self._expected

    async def fallback_action(self, seat: int) -> dict:
        self.fallback_calls += 1
        return {"action": self._expected, "content": "兜底", "payload": {}}


def _patch_llm(monkeypatch, raw: str) -> None:
    async def _fake_chat_completion(**_kwargs):
        return raw
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)


@pytest.mark.parametrize("expected,raw,accepted", [
    # 与当前期望一致 → 采纳 LLM 决策
    ("kill", '{"action": "kill", "content": "刀2号", "payload": {"target_seat": 2}}', True),
    # 通用动作：任何阶段都放行（由调度方分流结算）
    ("vote", '{"action": "surrender", "content": "我认输", "payload": {}}', True),
    # 该 expected 下引擎同样放行的替代动作（liars_bar 跟牌/质疑）
    ("follow_or_challenge", '{"action": "challenge", "content": "质疑", "payload": {}}', True),
    # 空 action / 非字符串 action → 弃用
    ("kill", '{"action": "", "content": "x", "payload": {}}', False),
    ("kill", '{"action": 5, "content": "x", "payload": {}}', False),
    ("kill", '{"action": null, "content": "x"}', False),
    # 与当前阶段不符（夜晚只能刀人，却给了投票）→ 弃用
    ("kill", '{"action": "vote", "content": "投2号", "payload": {"target_seat": 2}}', False),
    # 引擎动作集外 → 弃用
    ("vote", '{"action": "teleport", "content": "x", "payload": {}}', False),
])
def test_ai_decide_validates_action_then_fallback(monkeypatch, expected, raw, accepted):
    from app.games.ai_player import ai_decide

    engine = _StubEngine(expected)
    _patch_llm(monkeypatch, raw)
    out = asyncio.run(ai_decide(engine, 1))

    if accepted:
        assert engine.fallback_calls == 0, f"合法动作不应走兜底: {out}"
        assert out["action"] == json.loads(raw)["action"]
    else:
        assert engine.fallback_calls == 1, f"非法动作应走 fallback，实际 {out}"
        assert out == {"action": expected, "content": "兜底", "payload": {}}


# ---------------- 集成夹具（临时库，与 games/guardrails 既有用例同法）----------------
def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(games_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _noop_ai(sid: int) -> None:
    return


async def _fast_sleep(*_a, **_k):
    return None


@pytest.fixture
def p21_db(monkeypatch, tmp_path):
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户一"))
            for i in range(101, 107):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.api.games.async_session_factory", factory)
    monkeypatch.setattr("app.memory.service.async_session_factory", factory)
    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    yield factory
    asyncio.run(engine.dispose())


def _create_all_ai_werewolf(client) -> int:
    """4 AI 席 + 1 真人旁观（纯 AI 对打形态）。"""
    r = client.post("/api/v1/games/sessions", json={
        "game_type": "werewolf", "player_ids": [101, 102, 103, 104],
        "user_as_player": False,
    })
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _drive_resume(session_id: int, timeout: float = 30.0) -> None:
    """驱动真实 _resume_ai_turns；加超时护栏，坏掉时快速失败而不是把用例挂死。"""
    await asyncio.wait_for(_REAL_RESUME(session_id), timeout=timeout)


async def _events(factory, sid: int) -> list[GameEvent]:
    async with factory() as db:
        return list((await db.execute(
            select(GameEvent).where(GameEvent.session_id == sid).order_by(GameEvent.id)
        )).scalars().all())


async def _abort_events(factory, sid: int) -> list[GameEvent]:
    return [e for e in await _events(factory, sid)
            if (json.loads(e.payload_json or "{}") or {}).get("reason") == "apply_fail_abort"]


# ---------------- 故障注入 ①：彻底推不动 → 有限步内止血终局 ----------------
@pytest.mark.slow
def test_apply_failure_aborts_terminally_with_visible_event(p21_db, monkeypatch):
    """把 fallback_action 临时改成恒返回非法动作，且引擎 apply/advance/timeout 全部推不动：
    - _resume_ai_turns 不再静默 return，有限次（≤ ABORT 阈值）内必然终局；
    - 落一条 phase=result 的用户可见结束事件，payload 带 guardrails 计数；
    - 护栏进程内状态终局清理。"""
    from app.games.werewolf import WerewolfEngine

    monkeypatch.setattr(WerewolfEngine, "current_turn_seat", lambda self: 0)

    async def _fail_apply(self, seat, action, payload):
        return ActionResult(ok=False, error="非法动作（故障注入）")

    async def _no_events(self):
        return []

    async def _no_winner(self):
        return None

    async def _illegal_fallback(self, seat):  # 交接文档要求的故障注入点
        return {"action": "not_a_real_action", "content": "非法兜底", "payload": {}}

    monkeypatch.setattr(WerewolfEngine, "apply_action", _fail_apply)
    monkeypatch.setattr(WerewolfEngine, "advance", _no_events)
    monkeypatch.setattr(WerewolfEngine, "timeout", _no_events)
    monkeypatch.setattr(WerewolfEngine, "check_winner", _no_winner)
    monkeypatch.setattr(WerewolfEngine, "fallback_action", _illegal_fallback)
    monkeypatch.setattr("asyncio.sleep", _fast_sleep)
    obs: list[tuple] = []
    monkeypatch.setattr("app.memory.observability.obs_event",
                        lambda *a, **k: obs.append(a), raising=False)

    calls: list[int] = []

    async def _counting_ai(engine, seat):
        calls.append(seat)
        return {"action": "not_a_real_action", "content": "非法决策", "payload": {}}
    monkeypatch.setattr("app.api.games.ai_decide", _counting_ai)

    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)
    guard = get_guard(sid)  # 持引用：终局 drop_guard 后仍能读到计数终值

    asyncio.run(_drive_resume(sid))

    async def _state():
        async with p21_db() as db:
            s = await db.get(GameSession, sid)
            return s.status, s.winner_side

    status, winner = asyncio.run(_state())
    # ① 有限步内必然终局（不再是「停在 playing 的 AI 回合」）
    assert status == "finished", f"应止血终局，实际 status={status}"
    assert winner == "draw"
    assert len(calls) == APPLY_FAIL_ABORT_LIMIT, f"决策次数应被止血阈值硬顶，实际 {len(calls)}"
    # ② guardrails 计数：连续失败到 ABORT 阈值（in-process 记录）
    assert guard.apply_failures == APPLY_FAIL_ABORT_LIMIT
    # ③ 持久化终局记录：用户可见结束事件 + reason/apply_failures
    aborts = asyncio.run(_abort_events(p21_db, sid))
    assert len(aborts) == 1, f"应恰好 1 条止血事件，实际 {len(aborts)}"
    payload = json.loads(aborts[0].payload_json)
    assert payload["apply_failures"] == APPLY_FAIL_ABORT_LIMIT
    assert aborts[0].phase == "result" and aborts[0].visibility == "public"
    assert aborts[0].content.strip(), "结束事件必须有用户可见文案"
    # ③' 可观测痕迹：obs_event 终局记录（含计数）
    ab_obs = [a for a in obs if a[1] == "game_guard_apply_fail_abort"]
    assert len(ab_obs) == 1 and ab_obs[0][2]["apply_failures"] == APPLY_FAIL_ABORT_LIMIT
    # ④ 终局清理护栏进程内状态
    assert sid not in guardrails._REGISTRY
    drop_guard(sid)


# ---------------- 故障注入 ②：确定性强推可自愈 → 对局继续并正常终局 ----------------
@pytest.mark.slow
def test_apply_failure_force_push_recovers_without_abort(p21_db, monkeypatch):
    """apply 连续失败但确定性硬强推（timeout）能修复引擎：
    - 第 1 次失败走 advance（阶段级确定性推进），第 2 次（≥FORCE）走 timeout 硬强推；
    - 强推后 apply 成功 → apply_failures 归零、对局正常分出胜负；
    - 不落任何止血事件。"""
    from app.games.werewolf import WerewolfEngine

    st = {"broken": True, "healed_applied": False, "advance_calls": 0, "timeout_calls": 0}
    monkeypatch.setattr(WerewolfEngine, "current_turn_seat", lambda self: 0)

    async def _apply(self, seat, action, payload):
        if st["broken"]:
            return ActionResult(ok=False, error="非法动作（故障注入）")
        st["healed_applied"] = True
        return ActionResult(ok=True, event={
            "event_type": "vote", "actor_seat": seat, "phase": "day_vote",
            "content": "🗳️ 正常投票", "visibility": "public", "payload": {},
        })

    async def _advance(self):
        st["advance_calls"] += 1
        return []

    async def _timeout(self):
        st["timeout_calls"] += 1
        st["broken"] = False  # 确定性硬强推修复引擎（不依赖 fallback 合法性）
        return [{"event_type": "announce", "phase": "day_vote",
                 "content": "系统强制推进。", "visibility": "public"}]

    async def _winner(self):
        return "villagers" if st["healed_applied"] else None

    async def _illegal_fallback(self, seat):
        return {"action": "not_a_real_action", "content": "非法兜底", "payload": {}}

    async def _illegal_ai(engine, seat):
        return {"action": "not_a_real_action", "content": "非法决策", "payload": {}}

    monkeypatch.setattr(WerewolfEngine, "apply_action", _apply)
    monkeypatch.setattr(WerewolfEngine, "advance", _advance)
    monkeypatch.setattr(WerewolfEngine, "timeout", _timeout)
    monkeypatch.setattr(WerewolfEngine, "check_winner", _winner)
    monkeypatch.setattr(WerewolfEngine, "fallback_action", _illegal_fallback)
    monkeypatch.setattr("app.api.games.ai_decide", _illegal_ai)
    monkeypatch.setattr("asyncio.sleep", _fast_sleep)
    obs: list[tuple] = []
    monkeypatch.setattr("app.memory.observability.obs_event",
                        lambda *a, **k: obs.append(a), raising=False)

    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)
    guard = get_guard(sid)

    asyncio.run(_drive_resume(sid))

    async def _state():
        async with p21_db() as db:
            s = await db.get(GameSession, sid)
            return s.status, s.winner_side

    status, winner = asyncio.run(_state())
    assert status == "finished" and winner == "villagers", (status, winner)
    assert st["advance_calls"] >= 1, "首次失败应走确定性阶段推进"
    assert st["timeout_calls"] == 1, "达 FORCE 阈值应走确定性硬强推 timeout"
    assert guard.apply_failures == 0, "成功推进后连续失败序列应归零"
    assert asyncio.run(_abort_events(p21_db, sid)) == [], "自愈路径不应产生止血事件"
    push_obs = [a for a in obs if a[1] == "game_guard_apply_fail_push"]
    assert [a[2]["move"] for a in push_obs] == ["advance", "timeout"], push_obs
    drop_guard(sid)


# ---------------- 零行为变化锚定：正常 apply 成功路径 ----------------
@pytest.mark.slow
def test_normal_apply_success_path_keeps_counter_zero(p21_db, monkeypatch):
    """正常对局（apply 成功）不涨 apply_failures、不落止血事件，既有事件行为不变。"""
    async def _valid_ai(engine, seat):
        return {"action": "answer_soup", "content": "可能", "payload": {"answer": "possible"}}

    monkeypatch.setattr("app.api.games.ai_decide", _valid_ai)
    monkeypatch.setattr("asyncio.sleep", _fast_sleep)

    client = _make_client(1)
    r = client.post("/api/v1/games/sessions", json={
        "game_type": "turtle_soup", "player_ids": [101], "user_as_player": True,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    r = client.post(f"/api/v1/games/sessions/{sid}/action",
                    json={"seat": 0, "action": "ask_soup", "payload": {"content": "它与真相里的人有关吗？"}})
    assert r.json()["ok"] is True, r.text

    guard = get_guard(sid)
    asyncio.run(_drive_resume(sid))

    evs = asyncio.run(_events(p21_db, sid))
    assert sum(1 for e in evs if e.event_type == "answer" and e.actor_seat == 1) == 1
    assert guard.apply_failures == 0
    assert asyncio.run(_abort_events(p21_db, sid)) == []
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    assert st["status"] == "playing" and st["current_turn_seat"] == 0
    drop_guard(sid)
