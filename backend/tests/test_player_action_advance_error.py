# -*- coding: utf-8 -*-
"""玩家动作路径的 ``engine.advance()`` 异常止血回归（2026-09-19）。

背景：``player_action()`` 里的 ``adv_events = await engine.advance()`` 是裸调用——引擎
advance 抛异常时请求直接 500，对局停在 ``playing``、零终局事件（前端无限等待 +
``resume_stuck_games`` 每 10 分钟空转重 spawn）。AI 回合路径（``_resume_ai_turns``）早已
把 advance 异常纳入「连续未推进 → 确定性推进 → 止血终局 + 用户可见事件」，玩家路径没对齐。

修复口径（只动 ``app/api/games.py`` 的 player_action，引擎语义零改动）：
  1. try 只包住 advance 本身（apply_action / persist_state / check_winner 语义不变）；
  2. 异常 → logger.error + obs ``game_guard_player_advance_error`` → ``_guard_stop_visible``
     落 phase=result 公开事件 + 群镜像 + 止血终局 + WS 广播，reason_tag=player_advance_error；
  3. 返回 ``{"ok": True, "finished": True, "aborted": True, "winner_side": None}``
     （与既有 finished 分支同结构，止血语义用 aborted 标记）；
  4. 止血自身再抛异常：先 log 再原样冒泡（不吞），由 FastAPI 显式 500。

测试纪律：临时 sqlite 走 pytest ``tmp_path``、不连生产库、不调真 LLM（AI 续跑 noop）。
真人席位被固定为唯一狼人，使夜 phase 的 ``kill`` 在**真实 apply_action** 下合法——
只有 advance 被打桩抛异常，故障面精确。
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
from app.games.guardrails import drop_guard
from app.games.werewolf import WerewolfEngine
from app.models.game import GameEvent, GameSession

_REASON_TAG = "player_advance_error"


class _GameRuleError(Exception):
    """引擎规则态非法类的「业务异常」替身（区别于 RuntimeError 这类运行时异常）。"""


class _LoggerRecorder:
    """``api.games._logger`` 替身：只记录 error 文案，用于断言「先 log 再冒泡」。"""

    def __init__(self):
        self.errors: list[str] = []

    def error(self, msg, *args, **_kwargs):
        self.errors.append(msg % args if args else msg)

    def warning(self, *_a, **_k):
        return None

    def info(self, *_a, **_k):
        return None


# ────────────────────────── 夹具：临时 sqlite（tmp_path）──────────────────────────
def _make_client(user_id: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(games_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _noop_ai(sid: int) -> None:
    return


@pytest.fixture
def player_action_db(monkeypatch, tmp_path):
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        from app.models.character import AICharacter
        from app.models.user import User
        # 拆种子提交：克隆库默认开 FK（生产同款 PRAGMA），父行 users 先落库再插 ai_characters
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户一"))
            await db.commit()
        async with factory() as db:
            for i in range(101, 104):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.api.games.async_session_factory", factory)
    monkeypatch.setattr("app.memory.service.async_session_factory", factory)
    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    yield factory
    asyncio.run(engine.dispose())


# ────────────────────────── 装置：建局 / 故障注入 / 观测 ──────────────────────────
def _create_human_werewolf(client) -> int:
    """3 AI 席 + 真人参局（werewolf 4 人起步）。"""
    r = client.post("/api/v1/games/sessions", json={
        "game_type": "werewolf", "player_ids": [101, 102, 103], "user_as_player": True,
    })
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _arm_user_as_wolf(factory, sid: int) -> tuple[int, dict]:
    """把真人席位固定为唯一狼人，使夜 phase 的 ``kill`` 在真实 apply_action 下合法。

    返回 (真人座次, fallback_action 给出的合法动作)。其余席位统一置 villager / 无预言家，
    保证「刀非狼存活玩家」的校验必然通过（不靠给 apply_action 打桩）。
    """
    async with factory() as db:
        s = await db.get(GameSession, sid)
        eng = engine_for(s.game_type)(s)
        await eng.load(db)
        seat = next(p.seat for p in eng.players
                    if p.player_type == "user" and not p.is_spectator)
        for p in eng.players:
            if p.is_spectator:
                continue
            p.role = "wolf" if p.seat == seat else "villager"
        eng.state["wolves"] = [seat]
        eng.state["seer"] = None
        eng.state["night_wolf_votes"] = {}
        await eng.persist_state(db)
        await db.commit()
        return seat, await eng.fallback_action(seat)


def _inject_advance_raise(monkeypatch, exc: Exception) -> None:
    async def _raise(self):
        raise exc
    monkeypatch.setattr(WerewolfEngine, "advance", _raise)


def _post_action(client, sid: int, seat: int, decision: dict):
    return client.post(f"/api/v1/games/sessions/{sid}/action", json={
        "seat": seat,
        "action": decision.get("action", ""),
        "payload": decision.get("payload") or {},
    })


async def _observe(factory, sid: int) -> tuple[str, str | None, list[GameEvent]]:
    async with factory() as db:
        s = await db.get(GameSession, sid)
        evs = list((await db.execute(
            select(GameEvent).where(GameEvent.session_id == sid).order_by(GameEvent.id)
        )).scalars().all())
        return s.status, s.winner_side, evs


# ────────────────────────── 用例 1：advance 抛运行时异常 ──────────────────────────
@pytest.mark.slow
def test_player_action_advance_error_returns_terminal_response(player_action_db, monkeypatch):
    """修复前：advance 抛异常 → 500、对局停在 playing。
    修复后：止血终局并返回 finished/aborted，错误信息进 payload 便于排查。"""
    _inject_advance_raise(monkeypatch, RuntimeError("故障注入：advance 恒抛异常"))
    client = _make_client()
    sid = _create_human_werewolf(client)
    seat, decision = asyncio.run(_arm_user_as_wolf(player_action_db, sid))

    r = _post_action(client, sid, seat, decision)
    assert r.status_code == 200, f"不应 500：{r.text}"
    body = r.json()
    assert body["ok"] is True
    assert body["finished"] is True
    assert body["aborted"] is True
    assert body.get("winner_side") is None

    status, _winner, events = asyncio.run(_observe(player_action_db, sid))
    assert status != "playing", f"对局不应停在 playing，实际 {status}"
    stops = [e for e in events if _REASON_TAG in (e.payload_json or "")]
    assert len(stops) == 1, f"应恰好 1 条 player_advance_error 结束事件，实际 {len(stops)}"
    assert "故障注入" in (stops[0].payload_json or ""), "错误信息应写入 payload 便于排查"
    drop_guard(sid)


# ────────────────────────── 用例 2：业务异常 + 止血后状态正确 ──────────────────────────
@pytest.mark.slow
def test_player_action_advance_error_settles_with_visible_stop(player_action_db, monkeypatch):
    """业务异常（规则态非法）同样止血：状态明确 + 用户可见终局事件 + 之后无法再行动。"""
    _inject_advance_raise(monkeypatch, _GameRuleError("故障注入：规则态非法"))
    client = _make_client()
    sid = _create_human_werewolf(client)
    seat, decision = asyncio.run(_arm_user_as_wolf(player_action_db, sid))

    r = _post_action(client, sid, seat, decision)
    assert r.status_code == 200, f"不应 500：{r.text}"
    assert r.json().get("aborted") is True

    status, winner, events = asyncio.run(_observe(player_action_db, sid))
    # werewolf 有平局语义 → 止血终局走 _settle_game(draw)
    assert status == "finished", f"应止血终局，实际 status={status}"
    assert winner == "draw", f"有平局语义的引擎应按平局结算，实际 winner={winner}"

    terminal = [e for e in events if e.phase == "result"]
    assert terminal, "无 phase=result 可见终局事件（前端收不到结束信号）"
    assert any((e.visibility or "public") == "public" for e in terminal), "终局事件不是公开可见"
    assert any((e.content or "").strip() for e in terminal), "终局事件文案为空"

    # 止血后：会话已结束，再行动应被拒（不再停在 playing 让人无限等）
    r2 = _post_action(client, sid, seat, decision)
    assert r2.status_code == 400, f"终局后不应还能行动：{r2.text}"
    drop_guard(sid)


# ────────────────────────── 用例 3：止血自身失败 → 不吞，冒泡回 500 ──────────────────────────
@pytest.mark.slow
def test_player_action_advance_error_stop_failure_bubbles(player_action_db, monkeypatch):
    """止血本身再抛异常时不吞：原样冒泡（TestClient 下即抛出，生产即 500），且已先留痕。"""
    _inject_advance_raise(monkeypatch, RuntimeError("故障注入：advance 恒抛异常"))

    async def _boom(*_a, **_k):
        raise RuntimeError("故障注入：止血失败")

    monkeypatch.setattr(games_api, "_guard_stop_visible", _boom)
    recorder = _LoggerRecorder()
    monkeypatch.setattr(games_api, "_logger", recorder)
    monkeypatch.setattr("app.memory.observability.obs_event", lambda *a, **k: None, raising=False)
    client = _make_client()
    sid = _create_human_werewolf(client)
    seat, decision = asyncio.run(_arm_user_as_wolf(player_action_db, sid))

    with pytest.raises(RuntimeError, match="止血失败"):
        _post_action(client, sid, seat, decision)
    assert any("player advance stop failed" in m for m in recorder.errors), \
        f"止血失败前必须先留痕，实际 logger: {recorder.errors}"

    status, _winner, _events = asyncio.run(_observe(player_action_db, sid))
    assert status == "playing", "止血失败不应假装成功（对局仍 playing，等上层介入）"
    drop_guard(sid)
