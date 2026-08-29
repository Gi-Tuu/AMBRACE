# -*- coding: utf-8 -*-
"""骗子酒馆引擎测试（群聊游戏 Phase 2，2026-08-26）。

- 发牌 / 声明 / 跟牌 / 质疑 / 扣分 / 淘汰 / 胜负流程。
"""
import asyncio
import random

from app.games.liars_bar import LiarsBarEngine, START_SCORE


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
    def __init__(self, seat, player_type="ai", character_id=None, user_id=None):
        self.seat = seat
        self.player_type = player_type
        self.character_id = character_id
        self.user_id = user_id
        self.role = ""
        self.alive = True
        self.score = 0
        self.is_spectator = False
        self.private_json = "{}"


def _make(players, user_id=1):
    session = _Session("liars_bar", user_id=user_id)
    engine = LiarsBarEngine(session)
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


def _players3():
    return [_Player(0, "user", user_id=1)] + [_Player(i, "ai", character_id=100 + i) for i in range(1, 3)]


# ---------------- 发牌 ----------------
def test_liars_bar_setup():
    random.seed(1)
    players = _players3()
    engine, _ = _make(players)
    events = _sync(engine.setup())
    assert engine.session.status == "playing"
    assert engine.session.phase == "declare"
    for p in engine.players:
        assert len(engine._cards(p.seat)) == 3
        assert all(1 <= c <= 10 for c in engine._cards(p.seat))
        assert p.score == START_SCORE
    assert engine.state["dealer_seat"] == 0
    assert engine.current_turn_seat() == 0
    assert any(e["event_type"] == "announce" for e in events)


# ---------------- 声明 / 跟牌 ----------------
def test_declare_and_follow():
    random.seed(2)
    engine, _ = _make(_players3())
    _sync(engine.setup())
    # 庄家首声明（数字自由）
    res = _sync(engine.apply_action(0, "declare", {"number": 5}))
    assert res.ok and res.event["payload"]["number"] == 5
    assert engine.current_turn_seat() == 1
    assert engine.state["last_decl"] == 5
    # 下家声明必须 >= 上家
    bad = _sync(engine.apply_action(1, "declare", {"number": 3}))
    assert not bad.ok
    ok = _sync(engine.apply_action(1, "declare", {"number": 6}))
    assert ok.ok
    # 声明数字为 1-10 之外非法
    assert not _sync(engine.apply_action(2, "declare", {"number": 99})).ok


# ---------------- 质疑（诚实：质疑者扣分）----------------
def test_challenge_honest_penalizes_challenger():
    random.seed(3)
    engine, _ = _make(_players3())
    _sync(engine.setup())
    engine.state["last_decl"] = 5
    engine.state["last_play_card"] = 5
    engine.state["last_play_seat"] = 0
    engine.state["turn_seat"] = 1
    res = _sync(engine.apply_action(1, "challenge", {}))
    assert res.ok
    assert engine.player_at(1).score == START_SCORE - 1  # 质疑者（1）扣 1 分
    assert engine.player_at(0).score == START_SCORE
    assert res.event["payload"]["honest"] is True


# ---------------- 质疑（说谎：上家扣分并收回牌）----------------
def test_challenge_lie_penalizes_declarer():
    random.seed(4)
    engine, _ = _make(_players3())
    _sync(engine.setup())
    engine.state["last_decl"] = 5
    engine.state["last_play_card"] = 3
    engine.state["last_play_seat"] = 0
    engine.state["turn_seat"] = 1
    cards_before = len(engine._cards(0))
    res = _sync(engine.apply_action(1, "challenge", {}))
    assert res.ok
    assert engine.player_at(0).score == START_SCORE - 1  # 上家（0）扣 1 分
    assert 3 in engine._cards(0)  # 收回被质疑的牌
    assert len(engine._cards(0)) == cards_before + 1
    assert res.event["payload"]["honest"] is False


# ---------------- 淘汰（分数归 0 / 手牌清空）----------------
def test_elimination_by_score_and_cards():
    random.seed(5)
    engine, _ = _make(_players3())
    _sync(engine.setup())
    # 分数归 0
    engine.player_at(1).score = 1
    engine.state["last_decl"] = 5
    engine.state["last_play_card"] = 5
    engine.state["last_play_seat"] = 0
    engine.state["turn_seat"] = 1
    res = _sync(engine.apply_action(1, "challenge", {}))
    assert res.ok
    assert not engine.player_at(1).alive  # 质疑失败扣到 0 分 → 淘汰
    # 手牌清空
    engine.player_at(2).score = START_SCORE
    engine._set_cards(2, [])
    engine._refresh_alive()
    assert not engine.player_at(2).alive


# ---------------- 完整流程（fallback）可收敛到胜负 ----------------
def test_liars_bar_full_run():
    random.seed(7)
    engine, _ = _make(_players3())
    all_events = list(_sync(engine.setup()))
    winner = None
    for _ in range(200):
        seat = engine.current_turn_seat()
        if seat is None:
            break
        fb = _sync(engine.fallback_action(seat))
        res = _sync(engine.apply_action(seat, fb.get("action", ""), _payload(fb)))
        assert res.ok, f"fallback rejected seat={seat}: {fb} -> {res.error}"
        if res.event:
            all_events.append(res.event)
        adv = _sync(engine.advance())
        all_events.extend(adv)
        winner = _sync(engine.check_winner())
        if winner:
            break
    assert winner is not None, f"no winner, round={engine.session.round}"
    assert any(e["event_type"] == "win" for e in all_events)


# ---------------- v3.3.6 审查修复：死亡玩家不能行动 ----------------
def test_liars_bar_dead_player_cannot_act():
    random.seed(14)
    engine, _ = _make(_players3())
    _sync(engine.setup())
    engine.player_at(0).alive = False
    res = _sync(engine.apply_action(0, "declare", {"number": 5}))
    assert not res.ok
    assert "出局" in res.error
