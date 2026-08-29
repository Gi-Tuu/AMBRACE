# -*- coding: utf-8 -*-
"""狼人杀引擎测试（群聊游戏 Phase 2，2026-08-26）。

- 4 人局完整跑通到分出胜负（含夜晚刀人/验人 private、白天发言投票）。
- 信息隔离断言：村民 AI prompt 不含狼名单/验人结果；狼 prompt 含狼名单不含预言家验人结果；
  预言家 prompt 含验人结果；fallback 不泄漏。
"""
import asyncio
import dataclasses
import json
import random

from app.games.werewolf import WerewolfEngine


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


# ---------------- 4 人局完整跑通 ----------------
def test_werewolf_4p_full_run():
    random.seed(42)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine, _ = _make(players)
    events = list(_sync(engine.setup()))
    assert engine.session.status == "playing"
    assert engine.session.phase == "night"
    roles = [p.role for p in engine.active_players()]
    assert roles.count("wolf") == 1
    assert roles.count("seer") == 1
    assert roles.count("villager") == 2

    all_events = list(events)
    winner = None
    for _ in range(300):
        seat = engine.current_turn_seat()
        if seat is None:
            break
        fb = _sync(engine.fallback_action(seat))
        res = _sync(engine.apply_action(seat, fb.get("action", ""), _payload(fb)))
        if not res.ok:
            raise AssertionError(f"fallback rejected seat={seat}: {fb} -> {res.error}")
        if res.event:
            all_events.append(res.event)
        adv = _sync(engine.advance())
        all_events.extend(adv)
        winner = _sync(engine.check_winner())
        if winner:
            engine.session.phase = "result"
            break

    assert winner in ("villagers", "werewolves", "draw"), f"winner={winner}"
    assert any(e["event_type"] == "wolf_kill" for e in all_events)
    assert any(e["event_type"] == "announce" for e in all_events)
    assert any(e["event_type"] == "vote" for e in all_events)


# ---------------- 夜晚 private 事件 ----------------
def test_werewolf_night_private_action():
    random.seed(7)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 5)
    ]
    engine, _ = _make(players)
    _sync(engine.setup())
    wolf = next(p.seat for p in engine.active_players() if p.role == "wolf")
    seer = next(p.seat for p in engine.active_players() if p.role == "seer")
    victim_candidates = [p.seat for p in engine.active_players() if p.role != "wolf"]
    victim = victim_candidates[0]
    res = _sync(engine.apply_action(wolf, "kill", {"target_seat": victim}))
    assert res.ok and res.event["visibility"] == "private" and res.event["private_to_seat"] == -1
    target = victim_candidates[1]
    cres = _sync(engine.apply_action(seer, "check", {"target_seat": target}))
    assert cres.ok and cres.event["visibility"] == "private"
    assert cres.event["private_to_seat"] == seer
    # 预言家验人结果写在 state
    assert target in engine.state["seer_results"]


# ---------------- 信息隔离 ----------------
def test_werewolf_info_isolation():
    random.seed(21)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine, _ = _make(players)
    _sync(engine.setup())
    wolf = next(p.seat for p in engine.active_players() if p.role == "wolf")
    seer = next(p.seat for p in engine.active_players() if p.role == "seer")
    villager = next(p.seat for p in engine.active_players() if p.role == "villager")
    wolves = engine.state["wolves"]

    # 村民：prompt 不含狼名单/验人结果
    vctx = engine.build_ai_prompt(villager)
    assert "wolf_team" not in vctx.my_view.private
    vtext = json.dumps({**vctx.__dict__, "my_view": dataclasses.asdict(vctx.my_view)}, ensure_ascii=False)
    for wseat in wolves:
        assert f"狼队友{wseat}" not in vtext
    assert not any(e["event_type"] == "check_result" for e in vctx.public_events)
    assert not any(e["event_type"] == "wolf_kill" for e in vctx.public_events)

    # 狼：prompt 含狼名单，不含预言家验人结果
    wctx = engine.build_ai_prompt(wolf)
    assert "wolf_team" in wctx.my_view.private
    assert set(wctx.my_view.private["wolf_team"]) == set(wolves)
    assert not any(e["event_type"] == "check_result" for e in wctx.public_events)

    # 预言家：prompt 含验人结果（夜间行动后）
    victim_candidates = [p.seat for p in engine.active_players() if p.role != "wolf"]
    kill_res = _sync(engine.apply_action(wolf, "kill", {"target_seat": victim_candidates[0]}))
    check_res = _sync(engine.apply_action(seer, "check", {"target_seat": victim_candidates[1]}))
    # 模拟 persist_event：把事件写入权威流水，build_ai_prompt 才可读取
    engine._events.append(kill_res.event)
    engine._events.append(check_res.event)
    sctx = engine.build_ai_prompt(seer)
    assert "checks" in sctx.my_view.private and sctx.my_view.private["checks"]
    assert any(e["event_type"] == "check_result" for e in sctx.public_events)


# ---------------- fallback 合法性（不泄漏不崩溃）----------------
def test_werewolf_fallback_legal_and_no_leak():
    random.seed(29)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine, _ = _make(players)
    _sync(engine.setup())
    for seat in [p.seat for p in engine.active_players()]:
        fb = _sync(engine.fallback_action(seat))
        if fb["action"] in ("skip",):
            continue
        res = _sync(engine.apply_action(seat, fb["action"], _payload(fb)))
        assert res.ok, f"fallback rejected seat={seat}: {fb} -> {res.error}"
    # 狼 fallback 一定刀非狼；村民 speak 不得含狼名单
    for p in engine.active_players():
        fb = _sync(engine.fallback_action(p.seat))
        if p.role == "wolf" and fb.get("action") == "kill":
            assert fb["payload"]["target_seat"] in [q.seat for q in engine.active_players() if q.role != "wolf"]
        if p.role == "villager" and fb.get("action") == "speak":
            json_text = json.dumps(fb, ensure_ascii=False)
            for w in engine.state["wolves"]:
                assert str(w) not in json_text


# ---------------- 观战者无 private ----------------
def test_werewolf_spectator_no_private():
    random.seed(31)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ] + [_Player(4, "ai", character_id=104)]
    players[-1].is_spectator = True
    engine, _ = _make(players)
    _sync(engine.setup())
    sp = engine.view_for(4)
    assert sp.private == {}


# ---------------- v3.3.6 审查修复：死亡玩家不能投票 ----------------
def test_werewolf_dead_player_cannot_vote():
    random.seed(13)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine, _ = _make(players)
    _sync(engine.setup())
    engine.session.phase = "day_vote"
    engine.state["votes"] = {}
    engine.player_at(0).alive = False
    res = _sync(engine.apply_action(0, "vote", {"target_seat": 1}))
    assert not res.ok
    assert "出局" in res.error
