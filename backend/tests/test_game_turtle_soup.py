# -*- coding: utf-8 -*-
"""海龟汤引擎测试（群聊游戏 Phase 2，2026-08-26）。

- 提问/回答、20 问上限、猜中真相；
- 信息隔离：猜题者 prompt 不含真相/关键词。
"""
import asyncio
import dataclasses
import json
import random

from app.games.turtle_soup import TurtleSoupEngine


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
    session = _Session("turtle_soup", user_id=user_id)
    engine = TurtleSoupEngine(session)
    engine.players = players
    meta = {p.seat: {"name": ("用户" if p.player_type == "user" else f"座{p.seat}"),
                     "character_id": p.character_id, "personality": "外向开朗",
                     "chat_style": "口语化", "relation_type": "朋友"} for p in players}
    engine.build_player_meta(meta)
    return engine, session


def _sync(coro):
    return asyncio.run(coro)


def _players2():
    return [_Player(0, "user", user_id=1), _Player(1, "ai", character_id=101)]


def _players2_both_ai():
    # 两个 AI：setup 中 elif ai_players 分支 → thinker=seat0, guesser=seat1(AI)
    return [_Player(0, "ai", character_id=101), _Player(1, "ai", character_id=102)]


# ---------------- 初始状态：AI 主持人 / 用户猜题 ----------------
def test_turtle_soup_setup():
    random.seed(1)
    engine, _ = _make(_players2())
    events = _sync(engine.setup())
    assert engine.session.status == "playing"
    assert engine.state["thinker_seat"] == 1  # AI 当主持人
    assert engine.state["guesser_seat"] == 0  # 用户猜题
    assert engine.state["truth"]
    assert engine.state["keywords"]
    assert any(e["event_type"] == "announce" and "汤面" in e["content"] for e in events)
    # 猜题者视图不含真相
    v = engine.view_for(0)
    assert v.private == {}


# ---------------- 提问/回答 ----------------
def test_turtle_soup_ask_answer():
    random.seed(2)
    engine, _ = _make(_players2())
    _sync(engine.setup())
    res = _sync(engine.apply_action(0, "ask_soup", {"content": "它是活的东西吗？"}))
    assert res.ok and res.event["event_type"] == "ask"
    assert engine.current_turn_seat() == engine.state["thinker_seat"]
    bad = _sync(engine.apply_action(0, "answer_soup", {"answer": "possible"}))
    assert not bad.ok  # 主持人才能回答
    ans = _sync(engine.apply_action(1, "answer_soup", {"answer": "possible"}))
    assert ans.ok and ans.event["payload"]["answer"] == "possible"
    # 非法回答值
    assert not _sync(engine.apply_action(1, "answer_soup", {"answer": "maybe"})).ok


# ---------------- 猜中真相 → 猜题者胜 ----------------
def test_turtle_soup_guess_correct():
    random.seed(3)
    engine, _ = _make(_players2())
    _sync(engine.setup())
    word = engine.state["keywords"][0]
    res = _sync(engine.apply_action(0, "guess_soup", {"word": word}))
    assert res.ok and res.event["payload"]["correct"] is True
    adv = _sync(engine.advance())
    assert any(e["event_type"] == "win" for e in adv)
    assert _sync(engine.check_winner()) == "guesser"
    assert engine.session.winner_side == "guesser"


# ---------------- 20 问上限 → 主持人胜 ----------------
def test_turtle_soup_20_question_limit():
    random.seed(5)
    engine, _ = _make(_players2())
    _sync(engine.setup())
    for i in range(20):
        seat = engine.current_turn_seat()
        if seat != engine.state["guesser_seat"]:
            break
        assert _sync(engine.apply_action(
            engine.state["guesser_seat"], "ask_soup",
            {"content": f"第{i + 1}问：它与食物有关吗？"})).ok
        assert _sync(engine.apply_action(
            engine.state["thinker_seat"], "answer_soup", {"answer": "possible"})).ok
        _sync(engine.advance())
    assert engine.state["questions"] >= 20
    assert _sync(engine.check_winner()) == "thinker"


# ---------------- 信息隔离：猜题者 prompt 不含真相 ----------------
def test_turtle_soup_info_isolation():
    random.seed(7)
    engine, _ = _make(_players2())
    _sync(engine.setup())
    truth = engine.state["truth"]
    kw = engine.state["keywords"]
    ctx = engine.build_ai_prompt(0)  # 猜题者
    text = json.dumps({**ctx.__dict__, "my_view": dataclasses.asdict(ctx.my_view)}, ensure_ascii=False)
    assert truth not in text
    for k in kw:
        assert k not in text
    assert ctx.my_view.private == {}
    # 主持人 prompt 含真相
    tctx = engine.build_ai_prompt(1)
    ttext = json.dumps({**tctx.__dict__, "my_view": dataclasses.asdict(tctx.my_view)}, ensure_ascii=False)
    assert truth in ttext
    assert tctx.my_view.private.get("word") == truth


# ---------------- v3.3.6 审查修复：fallback 先提问，周期性猜 ----------------
def test_turtle_soup_fallback_first_asks():
    random.seed(11)
    engine, _ = _make(_players2())
    _sync(engine.setup())
    engine.state["questions"] = 0
    fb = _sync(engine.fallback_action(engine.state["guesser_seat"]))
    assert fb["action"] == "ask_soup"
    assert "吗" in fb.get("content", "")


def test_turtle_soup_fallback_guesses_on_schedule():
    random.seed(12)
    # 猜题者必须是 AI（与 expected_action 的 is_ai 守卫一致；用户猜题者不自动猜）
    engine, _ = _make(_players2_both_ai())
    _sync(engine.setup())
    engine.state["questions"] = 7
    assert engine.is_ai(engine.state["guesser_seat"])
    fb = _sync(engine.fallback_action(engine.state["guesser_seat"]))
    assert fb["action"] == "guess_soup"
    assert fb["payload"]["word"] in (engine.state["keywords"] + [engine.state["truth"]])


def test_turtle_soup_fallback_not_ai_guesser_no_auto_guess():
    """#65：非 AI（用户）猜题者不触发自动猜词（is_ai 守卫防御性对齐 expected_action）。"""
    random.seed(13)
    engine, _ = _make(_players2())
    _sync(engine.setup())
    engine.state["questions"] = 7
    fb = _sync(engine.fallback_action(engine.state["guesser_seat"]))
    assert fb["action"] == "ask_soup"
