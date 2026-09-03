"""狼人杀引擎（多人 · 4-8 人）。

主流规则简化：
- n<=5：1 狼 + 1 预言家 + 村民；n>=6：2 狼 + 1 预言家 + 村民；
- 夜晚（night）：狼人私密刀人（狼之间互见 private）、预言家私密验人（仅本人见）；
  GM 天亮播报"昨晚 X 倒下/无人"，被刀者死前不公开身份；
- 白天（day）：存活者依次发言 → 投票淘汰得票最多（随机破平），被淘汰者公开身份；
- 胜负：狼人全灭=villagers、狼人数>=非狼存活=werewolves、10 夜未分=draw；
- 信息隔离：狼知道谁是狼、预言家知道验人结果、村民只知道公开信息。
规则判定全确定性代码，零 LLM；LLM 只发言/选择（ai_player）。
"""
from __future__ import annotations

import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView
from app.games.gm import gm_announce

ALL_WOLVES = -1  # private_to_seat 约定：-1 表示"所有狼"（狼之间互认可共见 private group）


class WerewolfEngine(GameEngine):
    game_type = "werewolf"
    player_mode = "multi"
    min_players = 4
    max_players = 8
    needs_gm = True
    MAX_NIGHTS = 10

    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = [p for p in self.players if not p.is_spectator]
        n = len(players)
        n_wolf = 1 if n <= 5 else 2
        n_seer = 1
        roles = ["wolf"] * n_wolf + ["seer"] * n_seer + ["villager"] * (n - n_wolf - n_seer)
        random.shuffle(roles)
        for p, r in zip(players, roles):
            p.role = r
            p.alive = True
            p.score = 0
            p.private_json = "{}"
        wolves = [p.seat for p in players if p.role == "wolf"]
        seer = next((p.seat for p in players if p.role == "seer"), None)
        self.state["wolves"] = wolves
        self.state["seer"] = seer
        self.state["night_wolf_votes"] = {}
        self.state["night_seer_target"] = None
        self.state["seer_results"] = {}
        self.state["speak_order"] = []
        self.state["speak_idx"] = 0
        self.state["votes"] = {}
        self.session.round = 1
        self.session.phase = "night"
        self.session.status = "playing"
        return [
            {"event_type": "announce", "phase": "night",
             "content": gm_announce("werewolf", "start", players=n), "visibility": "public"},
            {"event_type": "announce", "phase": "night",
             "content": gm_announce("werewolf", "night"), "visibility": "public"},
        ]

    async def load(self, db) -> None:
        await super().load(db)
        # FIX（2026-09-04 局失控根因）：state_json 经 json 往返后，以座位号为键的 dict
        # 键会从 int 变 str（JSON 对象键只能是字符串），而 current_turn_seat/apply/advance
        # 全部用 int 座位号判断，会导致"永远没行动过"的死循环。load 后统一归一为 int。
        for _key in ("night_wolf_votes", "votes", "seer_results"):
            d = self.state.get(_key)
            if isinstance(d, dict):
                norm: dict = {}
                for sk, sv in d.items():
                    try:
                        norm[int(sk)] = sv
                    except (TypeError, ValueError):
                        norm[sk] = sv
                self.state[_key] = norm

    # ── 角色辅助 ──
    def _wolf_seats(self) -> list[int]:
        return list(self.state.get("wolves") or [])

    def _seer_seat(self) -> int | None:
        return self.state.get("seer")

    def _alive_seats(self) -> list[int]:
        return [p.seat for p in self.active_players() if p.alive]

    def _is_wolf(self, seat: int) -> bool:
        return self.player_at(seat) is not None and self.player_at(seat).role == "wolf"

    def _night_complete(self) -> bool:
        wolves = [w for w in self._wolf_seats() if self.player_at(w) is not None and self.player_at(w).alive]
        voted = self.state.get("night_wolf_votes") or {}
        for w in wolves:
            if w not in voted:
                return False
        seer = self._seer_seat()
        if seer is not None:
            sp = self.player_at(seer)
            if sp is not None and sp.alive and self.state.get("night_seer_target") is None:
                return False
        return True

    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        phase = self.session.phase
        if phase == "night":
            if action == "kill" and self._is_wolf(seat):
                if not self.player_at(seat).alive:
                    return ActionResult(ok=False, error="你已经出局")
                if seat in (self.state.get("night_wolf_votes") or {}):
                    return ActionResult(ok=False, error="你已经刀过人")
                try:
                    target = int(payload.get("target_seat", -1))
                except (TypeError, ValueError):
                    return ActionResult(ok=False, error="无效目标")
                valid = [s for s in self._alive_seats() if not self._is_wolf(s)]
                if target not in valid:
                    return ActionResult(ok=False, error="无效目标（只能刀非狼的存活玩家）")
                self.state.setdefault("night_wolf_votes", {})[seat] = target
                # 狼之间互见：private_to_seat=-1 表示"所有狼"
                return ActionResult(ok=True, event={
                    "event_type": "wolf_kill", "actor_seat": seat, "target_seat": target,
                    "phase": "night", "visibility": "private", "private_to_seat": ALL_WOLVES,
                    "content": f"🌙 狼人{self.name_of(seat)}提议今晚刀掉{self.name_of(target)}。",
                    "payload": {"target": target},
                })
            if action == "check" and seat == self._seer_seat():
                if not self.player_at(seat).alive:
                    return ActionResult(ok=False, error="你已经出局")
                if self.state.get("night_seer_target") is not None:
                    return ActionResult(ok=False, error="你已经验过人")
                try:
                    target = int(payload.get("target_seat", -1))
                except (TypeError, ValueError):
                    return ActionResult(ok=False, error="无效目标")
                if target not in self._alive_seats():
                    return ActionResult(ok=False, error="无效目标")
                is_wolf = self._is_wolf(target)
                self.state["night_seer_target"] = target
                self.state.setdefault("seer_results", {})[target] = is_wolf
                return ActionResult(ok=True, event={
                    "event_type": "check_result", "actor_seat": seat, "target_seat": target,
                    "phase": "night", "visibility": "private", "private_to_seat": seat,
                    "content": f"🔮 预言家查验{self.name_of(target)}：{'是狼人' if is_wolf else '不是狼人'}。",
                    "payload": {"target": target, "is_wolf": is_wolf},
                })
            return ActionResult(ok=False, error="夜晚只能刀人或验人")
        if phase == "day_speak":
            if action == "speak":
                if seat != self._current_speaker():
                    return ActionResult(ok=False, error="还没轮到你发言")
                content = (payload.get("content") or "").strip()
                if len(content) < 2 or len(content) > 120:
                    return ActionResult(ok=False, error="发言需 2-120 字")
                self.state["speak_idx"] = int(self.state.get("speak_idx", 0)) + 1
                return ActionResult(ok=True, event={
                    "event_type": "speak", "actor_seat": seat, "phase": "day_speak",
                    "content": content, "visibility": "public",
                })
            return ActionResult(ok=False, error="白天请发言")
        if phase == "day_vote":
            if action == "vote":
                if not self.player_at(seat).alive:
                    return ActionResult(ok=False, error="你已经出局")
                try:
                    target = int(payload.get("target_seat", -1))
                except (TypeError, ValueError):
                    return ActionResult(ok=False, error="无效投票目标")
                valid = [s for s in self._alive_seats() if s != seat]
                if target not in valid:
                    return ActionResult(ok=False, error="无效投票目标")
                if seat in (self.state.get("votes") or {}):
                    return ActionResult(ok=False, error="你已经投过票")
                self.state.setdefault("votes", {})[seat] = target
                return ActionResult(ok=True, event={
                    "event_type": "vote", "actor_seat": seat, "target_seat": target, "phase": "day_vote",
                    "content": f"🗳️ {self.name_of(seat)}投票给{self.name_of(target)}。", "visibility": "public",
                    "payload": {"target": target},
                })
            return ActionResult(ok=False, error="请投票")
        return ActionResult(ok=False, error="非法动作")

    def _current_speaker(self) -> int | None:
        order = self.state.get("speak_order") or []
        idx = int(self.state.get("speak_idx", 0))
        return order[idx] if idx < len(order) else None

    async def advance(self) -> list[dict]:
        events: list[dict] = []
        phase = self.session.phase
        if phase == "night":
            if not self._night_complete():
                return events
            # 结算夜晚
            voted = self.state.get("night_wolf_votes") or {}
            tally: dict[int, int] = {}
            for t in voted.values():
                tally[t] = tally.get(t, 0) + 1
            victim = None
            if tally:
                max_v = max(tally.values())
                tied = [t for t, c in tally.items() if c == max_v]
                victim = random.choice(tied)
            if victim is not None:
                vp = self.player_at(victim)
                if vp is not None:
                    vp.alive = False
                content = gm_announce("werewolf", "day", victim=self.name_of(victim))
            else:
                content = gm_announce("werewolf", "day", victim="无人")
            events.append({"event_type": "announce", "phase": "day_speak", "content": content, "visibility": "public"})
            winner = await self.check_winner()
            if winner:
                self.session.phase = "result"
                events.append(self._win_event(winner))
                return events
            self.state["speak_order"] = self._alive_seats()
            self.state["speak_idx"] = 0
            self.session.phase = "day_speak"
            events.append({"event_type": "announce", "phase": "day_speak",
                           "content": gm_announce("werewolf", "day_speak"), "visibility": "public"})
            return events
        if phase == "day_speak":
            if int(self.state.get("speak_idx", 0)) >= len(self.state.get("speak_order") or []):
                self.state["votes"] = {}
                self.session.phase = "day_vote"
                events.append({"event_type": "announce", "phase": "day_vote",
                               "content": gm_announce("werewolf", "vote"), "visibility": "public"})
            return events
        if phase == "day_vote":
            alive = self._alive_seats()
            votes = self.state.get("votes") or {}
            if not all(s in votes for s in alive):
                return events
            tally: dict[int, int] = {}
            for t in votes.values():
                tally[t] = tally.get(t, 0) + 1
            max_v = max(tally.values())
            tied = [t for t, c in tally.items() if c == max_v]
            out_seat = random.choice(tied)
            out_player = self.player_at(out_seat)
            if out_player is None:
                return events
            out_player.alive = False
            events.append({"event_type": "eliminate", "target_seat": out_seat, "phase": "day_vote",
                           "content": gm_announce("werewolf", "eliminated",
                                                  name=self.name_of(out_seat), role=out_player.role),
                           "visibility": "public", "payload": {"role": out_player.role}})
            winner = await self.check_winner()
            if winner:
                self.session.phase = "result"
                events.append(self._win_event(winner))
                return events
            self.session.round = int(self.session.round or 0) + 1
            self.state["night_wolf_votes"] = {}
            self.state["night_seer_target"] = None
            self.session.phase = "night"
            events.append({"event_type": "announce", "phase": "night",
                           "content": gm_announce("werewolf", "night"), "visibility": "public"})
            return events
        return events

    def _win_event(self, winner: str) -> dict:
        content = gm_announce("werewolf", f"win_{winner}", **({"draw": True} if winner == "draw" else {}))
        return {"event_type": "win", "phase": "result", "content": content,
                "visibility": "public", "payload": {"winner_side": winner}}

    async def check_winner(self) -> str | None:
        alive = [p for p in self.active_players() if p.alive]
        wolves = [p for p in alive if p.role == "wolf"]
        non_wolves = [p for p in alive if p.role != "wolf"]
        if not wolves:
            return "villagers"
        if len(wolves) >= len(non_wolves):
            return "werewolves"
        if int(self.session.round or 0) >= self.MAX_NIGHTS:
            return "draw"
        return None

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
        phase = self.session.phase
        if phase == "night":
            for w in sorted(self._wolf_seats()):
                p = self.player_at(w)
                if p is None or not p.alive:
                    continue
                if w not in (self.state.get("night_wolf_votes") or {}):
                    return w
            seer = self._seer_seat()
            if seer is not None:
                sp = self.player_at(seer)
                if sp is not None and sp.alive and self.state.get("night_seer_target") is None:
                    return seer
            return None
        if phase == "day_speak":
            return self._current_speaker()
        if phase == "day_vote":
            votes = self.state.get("votes") or {}
            for s in self._alive_seats():
                if s not in votes:
                    return s
            return None
        return None

    # ── 信息隔离：狼互见 private group（-1）──
    def public_events_for(self, seat: int) -> list[dict]:
        is_wolf = self._is_wolf(seat)
        out = []
        for e in self.all_events_ordered():
            vis = e.get("visibility", "public")
            if vis == "public":
                out.append(e)
            elif e.get("private_to_seat") == seat:
                out.append(e)
            elif vis == "private" and e.get("private_to_seat") == ALL_WOLVES and is_wolf:
                out.append(e)
        return out

    def view_for(self, seat: int) -> PlayerView:
        p = self.player_at(seat)
        if p is None:
            raise ValueError(f"no player at seat {seat}")
        priv: dict = {}
        if not p.is_spectator:
            if p.role == "wolf":
                priv = {"role": "wolf", "wolf_team": self._wolf_seats()}
            elif p.role == "seer":
                priv = {"role": "seer", "checks": dict(self.state.get("seer_results") or {}),
                        "seer_seat": self._seer_seat()}
            else:
                priv = {"role": "villager"}
        return PlayerView(
            seat=p.seat, player_type=p.player_type, character_id=p.character_id,
            name=self.name_of(p.seat), role=p.role or "villager", alive=bool(p.alive),
            is_spectator=bool(p.is_spectator), private=priv,
            public_state={"alive": bool(p.alive), "role": p.role or "villager"},
        )

    def build_ai_prompt(self, seat: int) -> GameContext:
        me = self.view_for(seat)
        others = [
            {"seat": q.seat, "name": self.name_of(q.seat), "alive": bool(q.alive)}
            for q in self.active_players() if q.seat != seat
        ]
        if me.role == "wolf":
            rules = (
                "你是狼人。你的狼队友是：" + "、".join(str(s) for s in me.private.get("wolf_team", [])) + " 号。"
                "夜晚你要和狼队友一起刀一个非狼的玩家；白天藏在村民里发言，投票淘汰疑似非你队友的人。"
                "狼人全部出局村民赢；狼人数不少于非狼存活则狼人赢。一定要隐藏自己，别暴露狼队友。"
            )
        elif me.role == "seer":
            checks = me.private.get("checks", {})
            checks_str = "；".join(f"{self.name_of(int(k))}是{'狼人' if v else '好人'}" for k, v in checks.items())
            rules = (
                "你是预言家。每晚可以查验一个玩家是否为狼人（结果见下方私人信息）。"
                "白天你要引导村民找狼人，但别太早暴露自己会被狼人刀。你已查验：" + (checks_str or "今晚还没查验") + "。"
            )
        else:
            rules = (
                "你是村民，没有特殊技能。白天和大家发言、投票找出狼人。狼人全部出局村民赢；"
                "狼人数不少于非狼存活则狼人赢。你只知道公开信息，不知道谁是狼。"
            )
        return GameContext(
            game_type="werewolf",
            rules_summary=rules,
            public_events=self.public_events_for(seat),
            players_public=others,
            my_view=me,
            my_persona=self.persona_of(seat),
            phase=self.session.phase,
            round=int(self.session.round or 0),
            my_turn=(self.current_turn_seat() == seat),
        )

    def expected_action(self, seat: int) -> str:
        phase = self.session.phase
        if phase == "night":
            if self._is_wolf(seat) and seat not in (self.state.get("night_wolf_votes") or {}):
                return "kill"
            if seat == self._seer_seat() and self.state.get("night_seer_target") is None:
                return "check"
            return "skip"
        if phase == "day_speak":
            return "speak" if seat == self._current_speaker() else "skip"
        if phase == "day_vote":
            alive = self._alive_seats()
            votes = self.state.get("votes") or {}
            return "vote" if seat in alive and seat not in votes else "skip"
        return "skip"

    async def fallback_action(self, seat: int) -> dict:
        phase = self.session.phase
        if phase == "night":
            if self._is_wolf(seat) and seat not in (self.state.get("night_wolf_votes") or {}):
                targets = [s for s in self._alive_seats() if not self._is_wolf(s)]
                tgt = random.choice(targets) if targets else None
                return {"action": "kill", "content": "今晚刀一个。", "payload": {"target_seat": tgt}}
            if seat == self._seer_seat() and self.state.get("night_seer_target") is None:
                targets = self._alive_seats()
                tgt = random.choice(targets) if targets else None
                return {"action": "check", "content": "我查验一下。", "payload": {"target_seat": tgt}}
            return {"action": "skip", "content": "", "payload": {}}
        if phase == "day_speak":
            return {"action": "speak", "content": "我还没有什么头绪，先听听大家的看法。", "payload": {}}
        if phase == "day_vote":
            targets = [s for s in self._alive_seats() if s != seat]
            tgt = random.choice(targets) if targets else None
            return {"action": "vote", "content": f"我投{tgt}号。", "payload": {"target_seat": tgt}}
        return {"action": "skip", "content": "", "payload": {}}
