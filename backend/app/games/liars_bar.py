"""骗子酒馆引擎（多人 · 3-5 人）。

主流规则简化：
- 每人 3 张手牌（1-10 随机）；庄家按座次轮流；
- 每轮庄家出一张牌并声明数字（可真实可虚报；首声明数字 1-10 自由，后继声明必须>=当前声明）；
- 下家可选「跟牌」（出一张牌并声明 >= 当前声明）或「质疑」（翻开上一张出的牌：
  声明与牌面一致 → 质疑者扣 1 分；不一致 → 上家扣 1 分并收回被质疑的牌）；
- 任一玩家分数 0 或手牌 0 时淘汰；剩 1 人胜，或 8 轮后分数最高胜（平局 draw）；
- 手牌 private，声明/质疑结果 public；AI 只知自己手牌 + 公开声明。
规则判定全确定性代码，零 LLM；LLM 只声明/质疑选择。
"""
from __future__ import annotations

import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView
from app.games.gm import gm_announce

START_SCORE = 3
MAX_ROUNDS = 8
CARD_MIN, CARD_MAX = 1, 10
CARDS_PER_PLAYER = 3


class LiarsBarEngine(GameEngine):
    game_type = "liars_bar"
    player_mode = "multi"
    min_players = 3
    max_players = 5
    needs_gm = True

    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = [p for p in self.players if not p.is_spectator]
        for p in players:
            p.role = "player"
            p.alive = True
            p.score = START_SCORE
            cards = [random.randint(CARD_MIN, CARD_MAX) for _ in range(CARDS_PER_PLAYER)]
            p.private_json = {"cards": cards}
        seats = sorted(p.seat for p in players)
        dealer = seats[0]
        self.state["dealer_seat"] = dealer
        self.state["round"] = 1
        self.state["turn_seat"] = dealer
        self.state["last_decl"] = None
        self.state["last_play_seat"] = None
        self.state["last_play_card"] = None
        self.state["declared"] = []
        self.state["round_ending"] = False
        self.session.round = 1
        self.session.phase = "declare"
        self.session.status = "playing"
        return [
            {"event_type": "announce", "phase": "declare", "visibility": "public",
             "content": gm_announce("liars_bar", "start", players=len(players))},
            {"event_type": "announce", "phase": "declare", "visibility": "public",
             "content": gm_announce("liars_bar", "round", round=1, dealer=self.name_of(dealer))},
        ]

    def _cards(self, seat: int) -> list[int]:
        return list(self.player_private(seat).get("cards") or [])

    def _set_cards(self, seat: int, cards: list[int]) -> None:
        p = self.player_at(seat)
        if p is not None:
            p.private_json = {"cards": cards}

    def _refresh_alive(self) -> None:
        for p in self.active_players():
            if not p.alive:
                continue
            if p.score <= 0 or len(self._cards(p.seat)) == 0:
                p.alive = False

    def _alive_seats(self) -> list[int]:
        return sorted(p.seat for p in self.active_players() if p.alive)

    def _next_active_after(self, seat: int) -> int | None:
        alive = self._alive_seats()
        for s in alive:
            if s > seat:
                return s
        for s in alive:
            if s <= seat:
                return s
        return None

    def _next_turn(self, after_seat: int) -> int | None:
        """下一个还没声明过的存活玩家；None=本轮闭环（全体已声明一次）。"""
        declared = self.state.get("declared") or []
        alive = self._alive_seats()
        for s in alive:
            if s > after_seat and s not in declared:
                return s
        for s in alive:
            if s <= after_seat and s not in declared:
                return s
        return None

    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        turn = self.state.get("turn_seat")
        if seat != turn:
            return ActionResult(ok=False, error="还没轮到你")
        if not self.player_at(seat).alive:
            return ActionResult(ok=False, error="你已经出局")
        if action == "declare":
            cards = self._cards(seat)
            if not cards:
                return ActionResult(ok=False, error="你已没有手牌")
            try:
                number = int(payload.get("number", -1))
            except (TypeError, ValueError):
                return ActionResult(ok=False, error="声明需为 1-10 的数字")
            if not (CARD_MIN <= number <= CARD_MAX):
                return ActionResult(ok=False, error="声明需为 1-10 的数字")
            last_decl = self.state.get("last_decl")
            if last_decl is not None and number < last_decl:
                return ActionResult(ok=False, error=f"声明必须不小于上家声明（{last_decl}）")
            # 从手牌随机出一张（声明数字可与牌面不同，可虚报）
            card = random.choice(cards)
            cards.remove(card)
            self._set_cards(seat, cards)
            self._refresh_alive()
            self.state["last_decl"] = number
            self.state["last_play_seat"] = seat
            self.state["last_play_card"] = card
            self.state.setdefault("declared", []).append(seat)
            nxt = self._next_turn(seat)
            self.state["round_ending"] = (nxt is None)
            if nxt is not None:
                self.state["turn_seat"] = nxt
            return ActionResult(ok=True, event={
                "event_type": "declare", "actor_seat": seat, "phase": "declare",
                "content": f"🃏 {self.name_of(seat)}声明了「{number}」。", "visibility": "public",
                "payload": {"number": number},
            })
        if action == "challenge":
            last_play_seat = self.state.get("last_play_seat")
            if last_play_seat is None:
                return ActionResult(ok=False, error="还没有可质疑的声明")
            card = self.state.get("last_play_card")
            decl = self.state.get("last_decl")
            if card is None or decl is None:
                return ActionResult(ok=False, error="还没有可质疑的声明")
            honest = (decl == card)
            if honest:
                challenger = self.player_at(seat)
                challenger.score -= 1
                content = (gm_announce("liars_bar", "challenged", challenger=self.name_of(seat),
                                       target=self.name_of(last_play_seat), card=card, decl=decl)
                           + "，声明属实，"
                           + gm_announce("liars_bar", "score_minus", name=self.name_of(seat)))
            else:
                declarer = self.player_at(last_play_seat)
                declarer.score -= 1
                # 上家收回被质疑的牌
                cards = self._cards(last_play_seat)
                cards.append(card)
                self._set_cards(last_play_seat, cards)
                content = (gm_announce("liars_bar", "challenged", challenger=self.name_of(seat),
                                       target=self.name_of(last_play_seat), card=card, decl=decl)
                           + "，声明不实，"
                           + gm_announce("liars_bar", "score_minus", name=self.name_of(last_play_seat))
                           + "，收回这张牌。")
            self._refresh_alive()
            self.state["round_ending"] = True
            return ActionResult(ok=True, next_phase="result", event={
                "event_type": "challenge", "actor_seat": seat, "target_seat": last_play_seat,
                "phase": "declare", "content": content, "visibility": "public",
                "payload": {"target": last_play_seat, "card": card, "decl": decl, "honest": honest,
                            "score": self.player_at(seat).score},
            })
        return ActionResult(ok=False, error="非法动作")

    async def advance(self) -> list[dict]:
        events: list[dict] = []
        if not self.state.get("round_ending"):
            # 非回合结束时也可能已分出胜负（如只剩 1 人存活）——立即结算
            self._refresh_alive()
            winner = await self.check_winner()
            if winner:
                self.session.phase = "result"
                events.append(self._win_event(winner))
            return events
        self._refresh_alive()
        winner = await self.check_winner()
        if winner:
            self.session.phase = "result"
            events.append(self._win_event(winner))
            return events
        self.state["round"] = int(self.state.get("round", 1)) + 1
        self.session.round = int(self.state["round"])
        winner2 = await self.check_winner()
        if winner2:
            self.session.phase = "result"
            events.append(self._win_event(winner2))
            return events
        dealer = self._next_active_after(self.state.get("dealer_seat"))
        self.state["dealer_seat"] = dealer
        self.state["turn_seat"] = dealer
        self.state["last_decl"] = None
        self.state["last_play_seat"] = None
        self.state["last_play_card"] = None
        self.state["declared"] = []
        self.state["round_ending"] = False
        self.session.phase = "declare"
        events.append({"event_type": "announce", "phase": "declare", "visibility": "public",
                       "content": gm_announce("liars_bar", "round", round=int(self.state["round"]),
                                              dealer=self.name_of(dealer))})
        return events

    def _highest_score_winner(self) -> str:
        players = [p for p in self.active_players() if not p.is_spectator]
        if not players:
            return "draw"
        maxs = max(p.score for p in players)
        winners = [p.seat for p in players if p.score == maxs]
        if len(winners) == 1:
            return f"seat_{winners[0]}"
        return "draw"

    async def check_winner(self) -> str | None:
        alive = self._alive_seats()
        if len(alive) == 1:
            return f"seat_{alive[0]}"
        if len(alive) == 0:
            return "draw"
        if int(self.state.get("round", 1)) > MAX_ROUNDS:
            return self._highest_score_winner()
        return None

    def _win_event(self, winner: str) -> dict:
        if winner == "draw":
            return {"event_type": "win", "phase": "result", "content": gm_announce("liars_bar", "draw"),
                    "visibility": "public", "payload": {"winner_side": "draw"}}
        try:
            wseat = int(winner.split("_")[-1])
            content = gm_announce("liars_bar", "win", name=self.name_of(wseat))
        except Exception:
            content = gm_announce("liars_bar", "win", name="")
        return {"event_type": "win", "phase": "result", "content": content,
                "visibility": "public", "payload": {"winner_side": winner}}

    async def timeout(self) -> list[dict]:
        seat = self.current_turn_seat()
        if seat is None:
            return []
        fb = await self.fallback_action(seat)
        payload = dict(fb.get("payload") or {})
        if fb.get("content"):
            payload.setdefault("content", fb["content"])
        res = await self.apply_action(seat, fb.get("action", ""), payload)
        out = []
        if res.ok and res.event:
            out.append(res.event)
        out.extend(await self.advance())
        return out

    def current_turn_seat(self) -> int | None:
        if self.state.get("round_ending") or self.session.phase == "result":
            return None
        return self.state.get("turn_seat")

    def view_for(self, seat: int) -> PlayerView:
        p = self.player_at(seat)
        if p is None:
            raise ValueError(f"no player at seat {seat}")
        priv = {} if p.is_spectator else {"cards": self._cards(seat)}
        return PlayerView(
            seat=p.seat, player_type=p.player_type, character_id=p.character_id,
            name=self.name_of(p.seat), role=p.role or "player", alive=bool(p.alive),
            is_spectator=bool(p.is_spectator), private=priv,
            public_state={
                "alive": bool(p.alive), "score": p.score,
                "cards": len(self._cards(seat)) if not p.is_spectator else 0,
                "turn": self.current_turn_seat() == seat,
            },
        )

    def build_ai_prompt(self, seat: int) -> GameContext:
        me = self.view_for(seat)
        others = [
            {"seat": q.seat, "name": self.name_of(q.seat), "alive": bool(q.alive)}
            for q in self.active_players() if q.seat != seat
        ]
        return GameContext(
            game_type="liars_bar",
            rules_summary=(
                "你和其他玩家各拿 3 张 1-10 的手牌。每轮从庄家开始，轮到你时要么出一张牌并声明一个数字"
                "（声明可虚报，但必须不小于当前声明；首声明自由 1-10），要么质疑上一家的声明。"
                "质疑时翻开上一张牌：若声明与牌面一致则你扣 1 分，不一致则上家扣 1 分并收回那张牌。"
                "分数到 0 或手牌用光即淘汰；剩最后 1 人赢，8 轮后分数最高者赢。"
                "你只知道自己的手牌和大家公开的声明，不知道别人的牌。"
            ),
            public_events=self.public_events_for(seat),
            players_public=others,
            my_view=me,
            my_persona=self.persona_of(seat),
            phase=self.session.phase,
            round=int(self.session.round or 0),
            my_turn=(self.current_turn_seat() == seat),
        )

    def expected_action(self, seat: int) -> str:
        if self.state.get("round_ending") or seat != self.state.get("turn_seat"):
            return "skip"
        if self.state.get("last_play_seat") is None:
            return "declare"  # 首声明（庄家），只能声明
        return "follow_or_challenge"

    async def fallback_action(self, seat: int) -> dict:
        if self.state.get("round_ending") or seat != self.state.get("turn_seat"):
            return {"action": "skip", "content": "", "payload": {}}
        last_decl = self.state.get("last_decl")
        if last_decl is not None and random.random() < 0.35:
            return {"action": "challenge", "content": "我要质疑上一家。", "payload": {}}
        number = last_decl if last_decl is not None else random.randint(CARD_MIN, CARD_MAX)
        return {"action": "declare", "content": "", "payload": {"number": number}}
