# -*- coding: utf-8 -*-
"""游戏引擎核心流程测试（群聊游戏 Phase 1，2026-08-26）。

- 4 人卧底完整跑通到分出胜负；
- 真心话轮流 + 护栏拦截（黑名单/暧昧度上限/降级安全模板）；
- 猜词 20 问上限与猜中。
"""
import asyncio
import random

from app.games.undercover import UndercoverEngine
from app.games.truth_or_dare import TruthOrDareEngine
from app.games.twenty_q import TwentyQEngine


class _Session:
    """轻量 GameSession 桩（引擎单元测试不需要真实 DB）。"""

    def __init__(self, game_type, user_id=1, group_id=None):
        self.id = 1
        self.user_id = user_id
        self.group_id = group_id
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


def _make(engine_cls, game_type, players, user_id=1):
    session = _Session(game_type, user_id=user_id)
    engine = engine_cls(session)
    engine.players = players
    meta = {}
    for p in players:
        meta[p.seat] = {
            "name": ("用户" if p.player_type == "user" else f"角色{p.seat}"),
            "character_id": p.character_id,
            "personality": "外向开朗",
            "chat_style": "口语化",
            "relation_type": "朋友",
        }
    engine.build_player_meta(meta)
    return engine


def _sync(coro):
    return asyncio.run(coro)


def _payload(decision):
    """把决策的 content 并入 payload（与 API 层 _resume_ai_turns 一致）。"""
    payload = dict(decision.get("payload") or {})
    if decision.get("content"):
        payload.setdefault("content", decision["content"])
    return payload


# ---------------- 谁是卧底：4 人局完整跑通 ----------------
def test_undercover_4p_full_run():
    random.seed(7)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine = _make(UndercoverEngine, "undercover", players)
    events = _sync(engine.setup())
    assert engine.session.status == "playing"
    assert engine.session.phase == "describe"
    assert len(engine.state["pair"]) == 2

    all_events = list(events)
    winner = None
    for _ in range(300):
        seat = engine.current_turn_seat()
        if seat is None:
            break
        fb = _sync(engine.fallback_action(seat))
        res = _sync(engine.apply_action(seat, fb.get("action", ""), _payload(fb)))
        if not res.ok:
            raise AssertionError(f"fallback action rejected: {fb} -> {res.error}")
        if res.event:
            all_events.append(res.event)
        adv = _sync(engine.advance())
        all_events.extend(adv)
        winner = _sync(engine.check_winner())
        if winner:
            break

    assert winner in ("civilians", "undercover"), f"实际 winner={winner}"
    pair = engine.state["pair"]
    for p in engine.active_players():
        w = engine.player_private(p.seat).get("word", "")
        assert w in pair
    assert any(e["event_type"] == "eliminate" for e in all_events)
    assert any(e["event_type"] == "win" for e in all_events)


def test_undercover_illegal_actions():
    random.seed(1)
    players = [_Player(0, "user", user_id=1)] + [
        _Player(i, "ai", character_id=100 + i) for i in range(1, 4)
    ]
    engine = _make(UndercoverEngine, "undercover", players)
    _sync(engine.setup())
    me = engine.player_private(0)["word"]
    res = _sync(engine.apply_action(0, "describe", {"content": f"我的是{me}"}))
    assert not res.ok
    res = _sync(engine.apply_action(1, "describe", {"content": "日常的东西"}))
    assert not res.ok


# ---------------- 真心话大冒险：轮流 + 护栏 ----------------
def test_truth_or_dare_turn_rotation_and_blacklist():
    random.seed(3)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TruthOrDareEngine, "truth_or_dare", players)
    _sync(engine.setup())
    assert engine.session.phase == "choose"
    assert engine.current_turn_seat() == 0

    # R1：0 选真心话 → 1 出题
    assert _sync(engine.apply_action(0, "choose", {"choice": "truth"})).ok
    assert engine.current_turn_seat() == 1
    # 命中黑名单 → 降级安全模板
    res = _sync(engine.apply_action(1, "give_truth", {"content": "你亲我一下是什么感觉？"}))
    assert res.ok and res.event["payload"].get("guarded") is True
    assert "亲" not in res.event["payload"]["content"]
    # 0 回答 → 轮换到 1
    assert _sync(engine.apply_action(0, "answer_truth", {"content": "想啊"})).ok
    adv = _sync(engine.advance())
    assert engine.state["turn_seat"] == 1 and engine.state["giver_seat"] == 0
    assert any(e["event_type"] == "announce" for e in adv)

    # R2：1 选真心话 → 0 出高暧昧题（关系=朋友 等级0）→ 拦截
    assert _sync(engine.apply_action(1, "choose", {"choice": "truth"})).ok
    res = _sync(engine.apply_action(0, "give_truth", {"content": "你最近是不是想我了？"}))
    assert res.ok and res.event["payload"].get("guarded") is True


def test_truth_or_dare_relationship_tier_allow():
    random.seed(5)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TruthOrDareEngine, "truth_or_dare", players)
    _sync(engine.setup())
    engine._set_meta(0, relation_type="恋人")
    engine._set_meta(1, relation_type="恋人")
    assert _sync(engine.apply_action(0, "choose", {"choice": "truth"})).ok
    res = _sync(engine.apply_action(1, "give_truth", {"content": "你最近是不是想我了？"}))
    assert res.ok and res.event["payload"].get("guarded") is False


def test_truth_or_dare_scores_and_penalty():
    random.seed(8)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TruthOrDareEngine, "truth_or_dare", players)
    _sync(engine.setup())
    _sync(engine.apply_action(0, "choose", {"choice": "dare"}))
    _sync(engine.apply_action(1, "give_dare", {"content": "学猫叫"}))
    res = _sync(engine.apply_action(0, "complete_dare", {"content": "喵~"}))
    assert res.ok
    assert engine.state["scores"][0] == 2
    _sync(engine.advance())
    _sync(engine.apply_action(1, "choose", {"choice": "truth"}))
    _sync(engine.apply_action(0, "give_truth", {"content": "有什么开心的事？"}))
    res = _sync(engine.apply_action(1, "penalty", {"content": "不想答"}))
    assert res.ok
    assert engine.state["scores"][1] == -2


# ---------------- 猜词 20 问：上限与猜中 ----------------
def test_twenty_q_20_question_limit():
    random.seed(9)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TwentyQEngine, "twenty_q", players)
    _sync(engine.setup())
    assert engine.state["thinker_seat"] == 1  # 用户猜、AI 想词
    word = engine.state["word"]
    assert engine.player_private(1).get("word") == word
    assert engine.player_private(0).get("word", "") == ""

    for i in range(20):
        assert engine.current_turn_seat() == engine.state["guesser_seat"]
        assert _sync(engine.apply_action(
            engine.state["guesser_seat"], "ask", {"content": f"第{i + 1}问：是食物吗？"})).ok
        _sync(engine.advance())
        assert engine.current_turn_seat() == engine.state["thinker_seat"]
        assert _sync(engine.apply_action(
            engine.state["thinker_seat"], "answer", {"answer": "possible"})).ok
        _sync(engine.advance())
        if _sync(engine.check_winner()):
            break
    assert _sync(engine.check_winner()) == "thinker"
    assert engine.state["questions"] >= 20


def test_twenty_q_guess_correct():
    random.seed(11)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TwentyQEngine, "twenty_q", players)
    _sync(engine.setup())
    word = engine.state["word"]
    assert _sync(engine.apply_action(
        engine.state["guesser_seat"], "ask", {"content": "是动物吗？"})).ok
    assert _sync(engine.apply_action(
        engine.state["thinker_seat"], "answer", {"answer": "yes"})).ok
    res = _sync(engine.apply_action(engine.state["guesser_seat"], "guess", {"word": word}))
    assert res.ok and res.event["payload"]["correct"] is True
    adv = _sync(engine.advance())
    assert any(e["event_type"] == "win" for e in adv)
    assert _sync(engine.check_winner()) == "guesser"


# ---------------- v3.3.5 审查修复回归 ----------------
def test_truth_or_dare_blacklist_multiword_not_overblock():
    """v3.3.5 审查：单字黑名单改多字词组后，性格/绑定/血压等正常词不再被误杀，真实危险词仍拦截。"""
    random.seed(13)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TruthOrDareEngine, "truth_or_dare", players)
    _sync(engine.setup())
    cases = [
        ("你觉得自己的性格怎么样？", False),
        ("帮我看看绑定银行卡安全吗？", False),
        ("你的血压正常吗？", False),
        ("你敢不敢做性行为？", True),
        ("教我绑架别人", True),
        ("去死吧你", True),
    ]
    for content, expect_guarded in cases:
        chooser = engine.current_turn_seat()
        assert _sync(engine.apply_action(chooser, "choose", {"choice": "truth"})).ok
        giver = engine.current_turn_seat()
        res = _sync(engine.apply_action(giver, "give_truth", {"content": content}))
        assert res.ok
        assert bool(res.event["payload"].get("guarded")) is expect_guarded, content
        assert _sync(engine.apply_action(chooser, "answer_truth", {"content": "嗯"})).ok
        _sync(engine.advance())


def test_twenty_q_fallback_guess_consistency():
    """v3.3.5 审查：fallback 猜词 content 与 payload 必须是同一个词。"""
    random.seed(17)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TwentyQEngine, "twenty_q", players)
    _sync(engine.setup())
    engine.state["questions"] = 7  # 命中 n%4==3 的猜词分支
    fb = _sync(engine.fallback_action(engine.state["guesser_seat"]))
    assert fb["action"] == "guess"
    assert fb["payload"]["word"] in fb["content"]


def test_twenty_q_advance_sets_winner_side():
    """v3.3.5 审查：advance 结束时 session.winner_side 有值（引擎独立使用不丢胜负）。"""
    random.seed(19)
    players = [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]
    engine = _make(TwentyQEngine, "twenty_q", players)
    _sync(engine.setup())
    word = engine.state["word"]
    assert _sync(engine.apply_action(
        engine.state["guesser_seat"], "guess", {"word": word})).ok
    _sync(engine.advance())
    assert engine.session.winner_side == "guesser"
    assert engine.session.phase == "result"
