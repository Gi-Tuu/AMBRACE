# -*- coding: utf-8 -*-
"""状态机故障注入矩阵（P2-2，2026-09-18）：『动作根本落不下去』在全部引擎上的有限步收敛。

背景：09-18 全量检查报告指出——此前的失控保护都建立在「动作能被 apply」之上。P2-1（提交
5be308cd）已在调度层补了 apply_failures 计数 + 确定性推进/硬强推/止血终局，但当时只在狼人杀
路径验证过。本文件把该保证扩展到注册表内**全部 6 款内置引擎**（以 app/games/registry.py 为准：
werewolf / liars_bar / undercover / twenty_q / truth_or_dare / turtle_soup），并覆盖 P2-1 未覆盖
的三条失效路径：

  1. ``engine.advance()`` 抛异常：旧调度层未捕获 → 异常跳出 ``_resume_ai_turns``，session 停在
     playing、零事件 → 前端无限等待 + ``resume_stuck_games`` 每 10 分钟空转重 spawn。
  2. ``ai_decide()`` 返回 None（LLM 坏输出极端形态）：旧代码 ``decision.get`` 抛 AttributeError，
     同上。
  3. ``decisions_cap`` / ``streak`` 两条止血分支只 ``_settle_game`` 不落事件：status 变
     finished/aborted 但前端收不到任何用户可见结束信号。

修复全部落在调度层与 helper（``app/api/games.py``），**不改任何引擎的胜负规则/动作语义**：
  - ``_as_decision_dict``：非 dict 决策归一化为空决策，交回 apply 失败 → 兜底 → guardrails 收敛；
  - ``advance()`` 异常纳入同一套 apply_failures 分级收敛，且「成功推进归零」移到 advance 成功之后
    （否则 apply 恒成功 + advance 恒抛异常会被反复清零 → 无限空转）；
  - ``_guard_stop_visible``：止血终局统一落 phase=result 公开事件 + 群镜像 + WS 广播；
  - ``_emergency_stop_after_crash``：任何未预期异常后用独立会话止血终局，异常退出不再等于卡死。

注入矩阵（每引擎 × 8 个故障，共 48 例）：
  a apply_fail        apply_action 恒 ActionResult(ok=False)
  b fallback_illegal  fallback_action 恒返回引擎动作集外非法动作（LLM 决策也非法以逼出兜底）
  c timeout_empty     timeout() 恒返回 []
  d advance_raise     advance() 恒抛异常
  e pinned_ai_turn    current_turn_seat() 恒返回同一 AI 座位（永久 AI 回合）
  f1 ai_none          AI 决策返回 None
  f2 ai_empty         AI 决策返回 {}
  f3 ai_illegal       AI 决策返回引擎动作集外 action

测试纪律：临时 sqlite（tmp_path_factory）、不连生产库、不调真 LLM（ai_decide 全部替身）、
``asyncio.sleep`` noop；集成用例统一打 ``slow``。断言：
  ① ``asyncio.wait_for`` 包住真实 ``_resume_ai_turns``，有限步内必然返回（超时=失败）；
  ② 结束后 status 明确（finished/aborted）且落库至少一条 phase=result 的公开结束事件；
  ③ ``guardrails._REGISTRY[sid]`` 终局清理，不泄漏；
  ④ ``apply_failures`` / ``decisions`` 不超过设计阈值。
"""
import asyncio
import os

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import games as games_api
from app.api.games import _as_decision_dict, router as games_router
from app.auth.deps import get_current_user_id
from app.games import engine_for, guardrails
from app.games.base import ActionResult
from app.games.guardrails import (
    AI_ONLY_MAX_DECISIONS, APPLY_FAIL_ABORT_LIMIT, drop_guard, get_guard, guard_tier,
    set_guard_mode,
)
from app.models.game import GameEvent, GameSession

# 模块加载时捕获真实 resume 实现（fixture 会 noop 掉模块属性，供集成用例直接驱动）
_REAL_RESUME = games_api._resume_ai_turns

# 以 registry 为准的 6 款内置引擎 × 满足其 min/max 人数约束的纯 AI 席位（user 仅观战）。
ENGINE_PLAYER_IDS: dict[str, list[int]] = {
    "werewolf": [101, 102, 103, 104],       # multi 4-8
    "undercover": [101, 102, 103, 104],     # multi 4-8
    "liars_bar": [101, 102, 103],           # multi 3-5
    "twenty_q": [101, 102],                 # 恰好 2 人
    "truth_or_dare": [101, 102],            # 恰好 2 人
    "turtle_soup": [101, 102],              # 恰好 2 人
}

FAULTS = [
    "apply_fail", "fallback_illegal", "timeout_empty", "advance_raise",
    "pinned_ai_turn", "ai_none", "ai_empty", "ai_illegal",
]

MATRIX = [(gt, fault) for gt in ENGINE_PLAYER_IDS for fault in FAULTS]


# ────────────────────────── 夹具：临时 sqlite（模块级，避免 48 例各建一次库）──────────────────────────
def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(games_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _noop_ai(sid: int) -> None:
    return


async def _fast_sleep(*_a, **_k) -> None:
    return None


@pytest.fixture(scope="module")
def matrix_db(tmp_path_factory):
    db_path = os.path.join(str(tmp_path_factory.mktemp("fault_matrix")), "t.db")
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
            for i in range(101, 109):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture
def client(matrix_db, monkeypatch):
    monkeypatch.setattr("app.api.games.async_session_factory", matrix_db)
    monkeypatch.setattr("app.memory.service.async_session_factory", matrix_db)
    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    return _make_client(1)


# ────────────────────────── 引擎探测：自然 AI 回合（不依赖注入）──────────────────────────
async def _probe_turn(factory, sid: int) -> tuple[int | None, bool]:
    async with factory() as db:
        s = await db.get(GameSession, sid)
        eng = engine_for(s.game_type)(s)
        await eng.load(db)
        seat = eng.current_turn_seat()
        return seat, (eng.is_ai(seat) if seat is not None else False)


async def _final_state(factory, sid: int) -> tuple[str, str | None, list[GameEvent]]:
    async with factory() as db:
        s = await db.get(GameSession, sid)
        evs = list((await db.execute(
            select(GameEvent).where(GameEvent.session_id == sid).order_by(GameEvent.id)
        )).scalars().all())
        return s.status, s.winner_side, evs


async def _drive(sid: int, timeout: float = 20.0) -> None:
    """驱动真实 _resume_ai_turns；wait_for 作为「有限步」上限保护。"""
    await asyncio.wait_for(_REAL_RESUME(sid), timeout=timeout)


# ────────────────────────── 故障注入 ──────────────────────────
def _inject_fault(monkeypatch, game_type: str, fault: str) -> None:
    """按 fault 注入引擎级故障。

    除 f 类（AI 坏输出）与 b（逼出非法兜底）外，统一把 ai_decide 换成「引擎自身兜底动作」，
    既保证确定性、又完全不连真 LLM（真实游戏链路由 ai_player 自己的用例覆盖）。
    """
    cls = engine_for(game_type)
    monkeypatch.setattr("asyncio.sleep", _fast_sleep)

    async def _llm_ok(engine, seat):
        return await engine.fallback_action(seat)

    monkeypatch.setattr("app.api.games.ai_decide", _llm_ok)

    if fault == "apply_fail":
        async def _fail(self, seat, action, payload):
            return ActionResult(ok=False, error="故障注入：apply 恒失败")
        monkeypatch.setattr(cls, "apply_action", _fail)
    elif fault == "fallback_illegal":
        async def _illegal_fb(self, seat):
            return {"action": "not_a_real_action", "content": "非法兜底", "payload": {}}

        async def _illegal_ai(engine, seat):
            return {"action": "not_a_real_action", "content": "非法决策", "payload": {}}
        monkeypatch.setattr(cls, "fallback_action", _illegal_fb)
        monkeypatch.setattr("app.api.games.ai_decide", _illegal_ai)
    elif fault == "timeout_empty":
        async def _empty(self):
            return []
        monkeypatch.setattr(cls, "timeout", _empty)
    elif fault == "advance_raise":
        async def _raise(self):
            raise RuntimeError("故障注入：advance 恒抛异常")
        monkeypatch.setattr(cls, "advance", _raise)
    elif fault == "pinned_ai_turn":
        pass  # 仅靠 current_turn_seat 恒返回同一 AI 座位（在 _run_scenario 中统一 pin）
    elif fault in ("ai_none", "ai_empty", "ai_illegal"):
        async def _bad(engine, seat, _fault=fault):
            if _fault == "ai_none":
                return None
            if _fault == "ai_empty":
                return {}
            return {"action": "not_a_real_action", "content": "非法决策", "payload": {}}
        monkeypatch.setattr("app.api.games.ai_decide", _bad)
    else:
        raise AssertionError(f"unknown fault: {fault}")


def _run_scenario(client, factory, monkeypatch, game_type: str, fault: str, *,
                  guard_setup=None, overrides=None):
    """建局 → pin 自然 AI 回合 → 注入故障 → 驱动真实 resume → 返回终局观测。

    pin ``current_turn_seat`` 是**测试装置**：保证调度层持续处于「AI 回合」而不是一轮就交还
    用户/结束，从而让每个故障组合都能被持续施加（fault e 本身就是这个「永久 AI 回合」形态）。
    """
    cls = engine_for(game_type)
    r = client.post("/api/v1/games/sessions", json={
        "game_type": game_type, "player_ids": ENGINE_PLAYER_IDS[game_type], "user_as_player": False,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    seat, is_ai = asyncio.run(_probe_turn(factory, sid))
    assert seat is not None, f"{game_type}: setup 后无当前行动座次"
    assert is_ai, f"{game_type}: 自然行动座次 {seat} 不是 AI"
    monkeypatch.setattr(cls, "current_turn_seat", lambda self: seat)

    _inject_fault(monkeypatch, game_type, fault)
    if overrides is not None:
        overrides(monkeypatch, cls)

    obs: list[tuple] = []
    monkeypatch.setattr("app.memory.observability.obs_event",
                        lambda *a, **k: obs.append(a), raising=False)

    guard = get_guard(sid)
    if guard_setup is not None:
        guard_setup(guard, sid)

    try:
        asyncio.run(_drive(sid))
    except TimeoutError:
        drop_guard(sid)
        pytest.fail(f"{game_type}/{fault}: _resume_ai_turns 20s 内未返回（疑似无限循环/空转）")

    status, winner, events = asyncio.run(_final_state(factory, sid))
    terminal = [e for e in events if e.phase == "result"]
    return sid, guard, status, winner, terminal, obs


# ────────────────────────── 主矩阵：6 引擎 × 8 故障 ──────────────────────────
@pytest.mark.slow
@pytest.mark.parametrize("game_type,fault", MATRIX, ids=[f"{g}-{f}" for g, f in MATRIX])
def test_fault_matrix_converges_terminally(client, matrix_db, monkeypatch, game_type, fault):
    """① 有限步内终止 ② 状态明确 + 用户可见终局事件 ③ guard 清理 ④ 计数有界。"""
    sid, guard, status, _winner, terminal, _obs = _run_scenario(
        client, matrix_db, monkeypatch, game_type, fault)

    # ① 已从 _drive 正常返回（wait_for 未超时）；④ 计数有界且确实跑过 AI 回合
    tier = guard_tier(guard.mode)
    assert guard.decisions >= 1, f"{game_type}/{fault}: 未进入 AI 决策"
    assert guard.decisions <= tier.max_decisions, (
        f"{game_type}/{fault}: decisions={guard.decisions} 超过硬上限 {tier.max_decisions}")
    assert guard.apply_failures <= APPLY_FAIL_ABORT_LIMIT, (
        f"{game_type}/{fault}: apply_failures={guard.apply_failures} 超过止血阈值")

    # ② 终局明确 + 用户可见结束事件落库（前端不会无限等待）
    assert status in ("finished", "aborted"), f"{game_type}/{fault}: 结束状态不明确 status={status}"
    assert terminal, f"{game_type}/{fault}: 无 phase=result 终局事件（前端收不到结束信号）"
    assert any((e.visibility or "public") == "public" for e in terminal), (
        f"{game_type}/{fault}: 终局事件不是公开可见")
    assert any((e.content or "").strip() for e in terminal), f"{game_type}/{fault}: 终局事件文案为空"

    # ③ guardrails 进程内状态终局清理
    assert sid not in guardrails._REGISTRY, f"{game_type}/{fault}: guard 泄漏"
    drop_guard(sid)


# ────────────────────────── 回归：d advance() 抛异常 ──────────────────────────
@pytest.mark.slow
def test_regression_advance_exception_routes_to_apply_failure_convergence(
        client, matrix_db, monkeypatch):
    """修复前：advance 抛异常 → 异常跳出调度层，session 停在 playing、零事件。
    修复后：纳入 apply_failures 分级收敛（apply 恒成功也不归零失败序列）→ 止血终局 + 可见事件。"""
    sid, guard, status, _winner, terminal, obs = _run_scenario(
        client, matrix_db, monkeypatch, "werewolf", "advance_raise")

    assert status == "finished", f"应止血终局，实际 status={status}"
    assert guard.apply_failures == APPLY_FAIL_ABORT_LIMIT
    aborts = [e for e in terminal if (e.payload_json or "").find("apply_fail_abort") != -1]
    assert len(aborts) == 1, f"应有且仅有 1 条 apply_fail_abort 结束事件，实际 {len(aborts)}"
    assert any(a[1] == "game_guard_apply_fail_abort" for a in obs)
    assert sid not in guardrails._REGISTRY
    drop_guard(sid)


# ────────────────────────── 回归：f1 ai_decide 返回 None ──────────────────────────
@pytest.mark.slow
@pytest.mark.parametrize("game_type", list(ENGINE_PLAYER_IDS))
def test_regression_non_dict_ai_decision_does_not_crash(client, matrix_db, monkeypatch, game_type):
    """修复前：decision=None → AttributeError 跳出，对局停在 playing。
    修复后：_as_decision_dict 归一化空决策，走 apply 失败 → 兜底 → 收敛，前端拿到结束事件。"""
    sid, guard, status, _winner, terminal, _obs = _run_scenario(
        client, matrix_db, monkeypatch, game_type, "ai_none")

    assert status in ("finished", "aborted"), f"{game_type}: 结束状态不明确 status={status}"
    assert terminal, f"{game_type}: ai_decide=None 后无可见终局事件"
    assert guard.decisions <= guard_tier(guard.mode).max_decisions
    assert sid not in guardrails._REGISTRY
    drop_guard(sid)


# ────────────────────────── 回归：③ 止血分支必须落可见事件 ──────────────────────────
def _stall_overrides(monkeypatch, cls, *, winner=None):
    """让引擎「apply 恒成功、advance 不推进、无胜负」= 原地打转，用于逼出护栏止血分支。"""
    async def _ok_apply(self, seat, action, payload):
        return ActionResult(ok=True, event={
            "event_type": "stall", "actor_seat": seat, "phase": "stall",
            "visibility": "public", "content": "原地打转", "payload": {},
        })

    async def _no_events(self):
        return []

    async def _no_winner(self):
        return winner
    monkeypatch.setattr(cls, "apply_action", _ok_apply)
    monkeypatch.setattr(cls, "advance", _no_events)
    monkeypatch.setattr(cls, "check_winner", _no_winner)


def _preset_ai_only_near_cap(guard, sid):
    set_guard_mode(sid, ai_only=True)
    guard.decisions = AI_ONLY_MAX_DECISIONS - 1  # 下一次 bump 即触顶


@pytest.mark.slow
def test_regression_decisions_cap_stop_emits_visible_event(client, matrix_db, monkeypatch):
    """修复前：decisions_cap 止血只 _settle_game，不落事件 → 前端收不到结束信号。
    修复后：_guard_stop_visible 落 phase=result 公开事件（payload.reason=decisions_cap）并广播。"""
    sid, guard, status, _winner, terminal, obs = _run_scenario(
        client, matrix_db, monkeypatch, "werewolf", "pinned_ai_turn",
        guard_setup=_preset_ai_only_near_cap,
        overrides=lambda mp, cls: _stall_overrides(mp, cls))

    assert status == "finished" and guard.decisions == AI_ONLY_MAX_DECISIONS
    cap_events = [e for e in terminal if "decisions_cap" in (e.payload_json or "")]
    assert len(cap_events) == 1, f"应恰好 1 条 decisions_cap 可见事件，实际 {len(cap_events)}"
    assert cap_events[0].visibility == "public" and cap_events[0].content.strip()
    assert any(a[1] == "game_guard_stop" for a in obs)
    assert sid not in guardrails._REGISTRY
    drop_guard(sid)


@pytest.mark.slow
def test_regression_streak_abort_stop_emits_visible_event(client, matrix_db, monkeypatch):
    """修复前：streak 止血只 _settle_game，不落事件。
    修复后：原地打转被收敛时同样落 phase=result 公开事件（payload.reason=streak_abort）并广播。"""
    async def _fixed(self, seat):
        return {"action": "kill", "content": "恒同动作", "payload": {"target_seat": 2}}

    def _overrides(mp, cls):
        _stall_overrides(mp, cls)  # apply 恒成功 + advance 不推进 + 无胜负
        mp.setattr(cls, "fallback_action", _fixed)

        async def _fixed_ai(engine, seat):
            return {"action": "kill", "content": "恒同动作", "payload": {"target_seat": 2}}
        mp.setattr("app.api.games.ai_decide", _fixed_ai)

    sid, guard, status, _winner, terminal, obs = _run_scenario(
        client, matrix_db, monkeypatch, "werewolf", "pinned_ai_turn",
        overrides=_overrides)

    assert status == "finished" and guard.mode == "ai_only"
    streak_events = [e for e in terminal if "streak_abort" in (e.payload_json or "")]
    assert len(streak_events) == 1, f"应恰好 1 条 streak_abort 可见事件，实际 {len(streak_events)}"
    assert streak_events[0].visibility == "public" and streak_events[0].content.strip()
    assert any(a[1] == "game_guard_stop" for a in obs)
    assert sid not in guardrails._REGISTRY
    drop_guard(sid)


# ────────────────────────── 回归：未预期异常 → 崩溃止血网 ──────────────────────────
@pytest.mark.slow
def test_regression_unhandled_engine_error_emergency_stops_with_visible_event(
        client, matrix_db, monkeypatch):
    """修复前：check_winner（矩阵外）抛异常 → 只 log 后清 guard，session 停在 playing。
    修复后：_emergency_stop_after_crash 用独立会话止血终局 + 落可见事件（reason=resume_crash）。"""
    async def _raise_winner(self):
        raise RuntimeError("故障注入：check_winner 恒抛异常")

    def _overrides(mp, cls):
        _stall_overrides(mp, cls)
        mp.setattr(cls, "check_winner", _raise_winner)

    sid, _guard, status, _winner, terminal, obs = _run_scenario(
        client, matrix_db, monkeypatch, "werewolf", "pinned_ai_turn",
        overrides=_overrides)

    assert status == "finished", f"崩溃后应止血终局，实际 status={status}"
    crash_events = [e for e in terminal if "resume_crash" in (e.payload_json or "")]
    assert len(crash_events) == 1, f"应恰好 1 条 resume_crash 可见事件，实际 {len(crash_events)}"
    assert crash_events[0].visibility == "public" and crash_events[0].content.strip()
    assert any(a[1] == "game_guard_crash_abort" for a in obs)
    assert sid not in guardrails._REGISTRY
    drop_guard(sid)


# ────────────────────────── 快测档：决策归一化纯函数 ──────────────────────────
def test_as_decision_dict_normalizes_non_dict():
    """AI/兜底坏输出（None/list/str/int）一律归一化为空决策 dict，绝不 AttributeError。"""
    assert _as_decision_dict(None) == {}
    assert _as_decision_dict([]) == {}
    assert _as_decision_dict("vote") == {}
    assert _as_decision_dict(5) == {}
    d = {"action": "vote", "payload": {}}
    assert _as_decision_dict(d) is d
