# -*- coding: utf-8 -*-
"""游戏失控护栏测试（2026-09-04）。

- §5.1 纯函数：签名忽略 content 措辞 / 重复三级收敛 / 换签名复位 / 总数硬上限。
- §5.2 根因回归：werewolf 夜间投票经 state_json 往返重载后键归一、回合指针不再卡在同一狼。
- §5.3 正常局锚定：正常 fallback 对局全程 guard_after_signature 恒 NORMAL，不误伤。
- 恒同决策集成 stub（临时库）：_resume_ai_turns 在引擎"原地打转"时被护栏收敛为 draw，
  ai_decide 调用次数被硬顶、护栏 _REGISTRY 被清。

不连真实库、不调真 LLM；DB 用例走临时 sqlite。
"""
import asyncio
import os
import random
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import games as games_api
from app.api.games import router as games_router
from app.auth.deps import get_current_user_id
from app.games.guardrails import (
    SessionGuard, canonical_signature, guard_before_llm, guard_after_signature,
    GuardMove, SAME_ACTION_STREAK_LIMIT, MAX_AI_DECISIONS_PER_SESSION,
    mark_forced_advance,
)
from app.models.game import GameSession

# 模块加载时捕获真实 resume 实现（fixture 会 noop 掉它，供集成 stub 直接驱动真正的 _resume_ai_turns）
_REAL_RESUME = games_api._resume_ai_turns


# ---------------- 纯函数：§5.1 ----------------
def _sig(target=2):
    return canonical_signature(1, "night", 2, "kill", {"target_seat": target})


def test_signature_ignores_content_wording():
    # content 措辞不同不算新动作（防 LLM 换说法绕过）
    a = canonical_signature(1, "night", 2, "kill", {"target_seat": 3, "content": "今晚刀3"})
    b = canonical_signature(1, "night", 2, "kill", {"target_seat": 3, "content": "我决定刀3号"})
    assert a == b
    # 目标不同才算不同
    c = canonical_signature(1, "night", 2, "kill", {"target_seat": 4})
    assert a != c


def test_repeat_escalates_fallback_then_advance_then_draw():
    g = SessionGuard()
    sig, rp = _sig(), (1, "night")
    moves = []
    for _ in range(SAME_ACTION_STREAK_LIMIT - 1):
        moves.append(guard_after_signature(g, sig, rp))
    assert all(m == GuardMove.NORMAL for m in moves)
    # 第 5 次：强制换目标
    assert guard_after_signature(g, sig, rp) == GuardMove.FORCE_FALLBACK
    # 换过仍重复 → 强推
    assert guard_after_signature(g, sig, rp) == GuardMove.FORCE_ADVANCE
    mark_forced_advance(g, rp)
    # 强推后持续重复到 HARD_LIMIT → draw
    last = None
    for _ in range(10):
        last = guard_after_signature(g, sig, rp)
    assert last == GuardMove.ABORT_DRAW


def test_signature_change_resets_streak():
    g = SessionGuard()
    for _ in range(SAME_ACTION_STREAK_LIMIT):
        guard_after_signature(g, _sig(3), (1, "night"))
    # 换成另一目标 → 序列重置回 NORMAL
    assert guard_after_signature(g, _sig(4), (1, "night")) == GuardMove.NORMAL
    assert g.streak == 1


def test_total_decision_hard_cap():
    g = SessionGuard()
    g.decisions = MAX_AI_DECISIONS_PER_SESSION
    assert guard_before_llm(g) == GuardMove.ABORT_DRAW
    # 硬上限前一档：仍在软上限之上，不再花 LLM 的钱、改走确定性 fallback（仍是收敛路径）
    g.decisions = MAX_AI_DECISIONS_PER_SESSION - 1
    assert guard_before_llm(g) == GuardMove.FORCE_FALLBACK
    # 低于软上限：正常调 LLM
    g.decisions = 10
    assert guard_before_llm(g) == GuardMove.NORMAL


def test_soft_limit_forces_fallback_without_llm():
    g = SessionGuard()
    g.decisions = 450  # AI_DECISION_SOFT_LIMIT
    assert guard_before_llm(g) == GuardMove.FORCE_FALLBACK


# ---------------- §5.3 正常局锚定（复用 werewolf 正常打满，护栏全程 NORMAL）----------------
class _Session:
    def __init__(self, game_type, user_id=1):
        self.id = 1
        self.user_id = user_id
        self.group_id = None
        self.game_type = game_type
        self.player_mode = ""
        self.status = "created"
        self.round = 0
        self.phase = ""
        self.state_json = "{}"
        self.winner_side = None
        self.trigger = "user_initiated"
        self.archive_json = "{}"
        self.started_at = None
        self.finished_at = None


class _Player:
    def __init__(self, seat, player_type="ai", character_id=None, user_id=None, spectator=False):
        self.seat = seat
        self.player_type = player_type
        self.character_id = character_id
        self.user_id = user_id
        self.role = ""
        self.alive = True
        self.score = 0
        self.is_spectator = spectator
        self.private_json = "{}"


def _make(players, user_id=1):
    from app.games.werewolf import WerewolfEngine
    session = _Session("werewolf", user_id=user_id)
    engine = WerewolfEngine(session)
    engine.players = players
    meta = {p.seat: {"name": ("用户" if p.player_type == "user" else f"座{p.seat}"),
                     "character_id": p.character_id, "personality": "外向开朗",
                     "chat_style": "口语化", "relation_type": "朋友"} for p in players}
    engine.build_player_meta(meta)
    return engine, session


def _sync(coro):
    return asyncio.run(coro)


def _payload(decision):
    payload = dict(decision.get("payload") or {})
    if decision.get("content"):
        payload.setdefault("content", decision["content"])
    return payload


def test_normal_werewolf_run_guard_never_escalates():
    """正常 fallback 推进的一局：签名随 round/phase/seat 变化，护栏全程 NORMAL（不误伤）。"""
    random.seed(42)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine, _ = _make(players)
    _sync(engine.setup())
    guard = SessionGuard()
    runs = 0
    winner = None
    for _ in range(300):
        seat = engine.current_turn_seat()
        if seat is None:
            break
        fb = _sync(engine.fallback_action(seat))
        res = _sync(engine.apply_action(seat, fb.get("action", ""), _payload(fb)))
        if not res.ok:
            raise AssertionError(f"fallback rejected seat={seat}: {fb} -> {res.error}")
        # 每步把决策签名喂进护栏，断言恒 NORMAL
        payload = dict(fb.get("payload") or {})
        sig = canonical_signature(
            engine.session.round, engine.session.phase, seat, fb.get("action", ""), payload
        )
        rp = (int(engine.session.round or 0), engine.session.phase or "")
        assert guard_after_signature(guard, sig, rp) == GuardMove.NORMAL, f"rp={rp} sig={sig}"
        guard.bump_decision()
        _sync(engine.advance())
        winner = _sync(engine.check_winner())
        runs += 1
        if winner:
            engine.session.phase = "result"
            break
    assert winner in ("villagers", "werewolves", "draw"), f"winner={winner}"


# ---------------- 临时库夹具（用于 wasm 状态重载回归 + 恒同决策集成 stub）----------------
def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(games_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _noop_ai(sid: int) -> None:
    return


@pytest.fixture
def game_guard_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="game_guard_test_")
    db_path = os.path.join(tmp, "t.db")
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
            for i in range(101, 106):
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
    """4 AI 席 + 1 真人旁观（character_suggested / 纯 AI 对打形态）。"""
    r = client.post("/api/v1/games/sessions", json={
        "game_type": "werewolf", "player_ids": [101, 102, 103, 104],
        "user_as_player": False,
    })
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


# ---------------- §5.2 根因回归：夜间投票状态重载后键归一 ----------------
def test_werewolf_night_votes_survive_state_roundtrip(game_guard_db):
    """json.dumps→loads 后夜间投票经 WerewolfEngine.load 键归一，同一狼不再被反复选中（session11 回归）。"""
    from app.games import engine_for
    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)

    async def _roundtrip():
        async with game_guard_db() as db:
            s = await db.get(GameSession, sid)
            eng = engine_for(s.game_type)(s)
            await eng.load(db)
            wolf = next(w for w in eng.state["wolves"]
                        if eng.player_at(w) is not None and eng.player_at(w).alive)
            victim = next(p.seat for p in eng.active_players() if p.role != "wolf")
            res = await eng.apply_action(wolf, "kill", {"target_seat": victim})
            assert res.ok, res.error
            # 模拟 persist→load 的 JSON 往返（apply 只写内存 int 键；落库即字符串化）
            await eng.persist_state(db)
            await db.commit()
            # 重新构造引擎并走【真实 load】（含 2026-09-04 键归一修复）
            s2 = await db.get(GameSession, sid)
            eng2 = engine_for(s2.game_type)(s2)
            await eng2.load(db)
            assert isinstance(wolf, int)
            assert wolf in eng2.state["night_wolf_votes"]  # 归一后该狼仍被视为"已刀"
            # 因此回合指针应跳过这只狼（轮到预言家或 None），而不是再次返回该狼
            assert eng2.current_turn_seat() != wolf
    asyncio.run(_roundtrip())


# ---------------- 恒同决策集成 stub：护栏把"原地打转"收敛为 draw ----------------
def test_resume_ai_turns_hard_stop_runaway_at_draw(game_guard_db, monkeypatch):
    """恒同决策（同一狼同目标反复 apply）在引擎"原地打转"时被护栏收敛：
    - ai_decide 调用次数被硬顶（远小于硬上限 + 缓冲）；
    - 最终 session.status == finished 且 winner_side == draw；
    - 护栏 _REGISTRY 中该 sid 被清。"""
    from app.games import guardrails
    from app.games.base import ActionResult
    from app.games.werewolf import WerewolfEngine

    # 让引擎确定性"无法推进"：current_turn 恒返回 AI 席 0、apply 恒接受、advance 不推进、无胜负
    monkeypatch.setattr(WerewolfEngine, "current_turn_seat", lambda self: 0)

    async def _p_apply(self, seat, action, payload):
        return ActionResult(ok=True, event={
            "event_type": "wolf_kill", "actor_seat": seat,
            "target_seat": payload.get("target_seat"), "phase": "night",
            "visibility": "public", "content": "恒同刀",
            "payload": {"target": payload.get("target_seat")},
        })

    async def _p_advance(self):
        return []

    async def _p_winner(self):
        return None

    monkeypatch.setattr(WerewolfEngine, "apply_action", _p_apply)
    monkeypatch.setattr(WerewolfEngine, "advance", _p_advance)
    monkeypatch.setattr(WerewolfEngine, "check_winner", _p_winner)

    calls = []

    async def _fixed_ai(engine, seat):
        calls.append(seat)
        return {"action": "kill", "content": "🔪 恒同刀3", "payload": {"target_seat": 3}}

    async def _fixed_fallback(self, seat):
        return {"action": "kill", "content": "🔪 恒同刀3", "payload": {"target_seat": 3}}

    monkeypatch.setattr(WerewolfEngine, "fallback_action", _fixed_fallback)
    monkeypatch.setattr("app.api.games.ai_decide", _fixed_ai)
    async def _fast_sleep(*_a, **_k):
        return None
    monkeypatch.setattr("asyncio.sleep", _fast_sleep)

    client = _make_client(1)
    sid = _create_all_ai_werewolf(client)

    asyncio.run(_REAL_RESUME(sid))

    async def _query():
        async with game_guard_db() as db:
            s = await db.get(GameSession, sid)
            return s.status, s.winner_side

    status, winner = asyncio.run(_query())
    assert status == "finished"
    assert winner == "draw"
    assert len(calls) >= 1, "恒同决策 stub 应被触发"
    assert len(calls) <= MAX_AI_DECISIONS_PER_SESSION, f"ai_decide 被调用 {len(calls)} 次，超出硬上限"
    assert sid not in guardrails._REGISTRY, "护栏进程内状态应在终局后清理"
