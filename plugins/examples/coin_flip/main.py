"""抛硬币猜正反 —— game_pack 最小示例（X1，2026-08-31）。

演示游戏扩展包的完整形态：
- manifest.json：type=http 声明型元数据（permissions/hooks 均为空）；
- main.py：实现 GameEngine 规则引擎，经 sdk.register_game 注册（source=本插件名）；
- 内核保留不变量：房间/回合/主持/游戏记忆隔离由 games 基座提供，扩展包只写规则。

玩法：座次 0 的玩家喊「正/反」→ 系统抛币 → 喊中者胜（单回合，dual 1v1）。
"""
from __future__ import annotations

import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView

_SIDES = {"heads": "正面", "tails": "反面"}


class CoinFlipEngine(GameEngine):
    game_type = "coin_flip"
    player_mode = "dual"
    min_players = 2
    max_players = 2
    needs_gm = False

    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = self.active_players()
        caller, waiter = players[0], players[1]
        for p in players:
            p.role = "caller" if p.seat == caller.seat else "watcher"
            p.alive = True
            p.score = 0
            p.private_json = "{}"
        self.state["caller_seat"] = caller.seat
        self.state["watcher_seat"] = waiter.seat
        self.state["stage"] = "call"
        self.session.round = 1
        self.session.phase = "call"
        self.session.status = "playing"
        return [
            {"event_type": "announce", "phase": "call",
             "content": f"🪙 抛硬币开始！{self.name_of(caller.seat)} 请喊「正面」或「反面」。",
             "visibility": "public"},
        ]

    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        if self.state.get("stage") != "call" or action != "call":
            return ActionResult(ok=False, error="当前请喊「正/反」（action=call）")
        if seat != self.state.get("caller_seat"):
            return ActionResult(ok=False, error="还没轮到你喊")
        side = (payload.get("side") or "").strip()
        if side not in _SIDES:
            return ActionResult(ok=False, error="只能喊 heads（正面）或 tails（反面）")
        self.state["call"] = side
        self.state["stage"] = "flip"
        return ActionResult(ok=True, next_phase="flip", event={
            "event_type": "call", "actor_seat": seat, "phase": "flip",
            "content": f"{self.name_of(seat)} 喊了「{_SIDES[side]}」，抛硬币——",
            "visibility": "public", "payload": {"side": side},
        })

    async def advance(self) -> list[dict]:
        if self.state.get("stage") != "flip":
            return []
        flip = random.choice(["heads", "tails"])
        caller_seat = self.state["caller_seat"]
        watcher_seat = self.state["watcher_seat"]
        winner = caller_seat if self.state.get("call") == flip else watcher_seat
        self.state["flip"] = flip
        self.state["stage"] = "done"
        self.state["phase_result"] = str(winner)
        self.session.phase = "result"
        self.session.winner_side = str(winner)
        return [{
            "event_type": "win", "phase": "result",
            "content": f"硬币是「{_SIDES[flip]}」——{self.name_of(winner)} 赢了！🎉",
            "visibility": "public", "payload": {"flip": flip, "winner_seat": winner},
        }]

    async def check_winner(self) -> str | None:
        w = self.state.get("phase_result")
        return w if self.state.get("stage") == "done" else None

    async def timeout(self) -> list[dict]:
        seat = self.current_turn_seat()
        if seat is None:
            return []
        fb = await self.fallback_action(seat)
        res = await self.apply_action(seat, fb.get("action", ""), dict(fb.get("payload") or {}))
        out = []
        if res.ok and res.event:
            out.append(res.event)
        out.extend(await self.advance())
        return out

    def current_turn_seat(self) -> int | None:
        if self.state.get("stage") == "call":
            return self.state.get("caller_seat")
        return None

    def view_for(self, seat: int) -> PlayerView:
        p = self.player_at(seat)
        if p is None:
            raise ValueError(f"no player at seat {seat}")
        return PlayerView(
            seat=p.seat, player_type=p.player_type, character_id=p.character_id,
            name=self.name_of(p.seat), role=p.role, alive=bool(p.alive),
            is_spectator=bool(p.is_spectator), private=self.player_private(seat),
            public_state={
                "stage": self.state.get("stage"),
                "call": self.state.get("call"),
                "flip": self.state.get("flip"),
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
            game_type="coin_flip",
            rules_summary="你和朋友玩抛硬币猜正反：你先喊「正面」或「反面」，然后抛币，喊中者赢。"
            if me.role == "caller" else
            "抛硬币猜正反：对方先喊正/反，抛币后喊中者赢。你只需等待结果。",
            public_events=self.public_events_for(seat),
            players_public=others,
            my_view=me,
            my_persona=self.persona_of(seat),
            phase=self.session.phase,
            round=int(self.session.round or 0),
            my_turn=(self.current_turn_seat() == seat),
        )

    def expected_action(self, seat: int) -> str:
        if self.state.get("stage") == "call" and seat == self.state.get("caller_seat"):
            return "call"
        return "skip"

    async def fallback_action(self, seat: int) -> dict:
        side = random.choice(["heads", "tails"])
        return {"action": "call", "content": f"那就喊「{_SIDES[side]}」吧！", "payload": {"side": side}}


# X1：游戏扩展包注册（source=本插件名 coin_flip；插件停用后自动从游戏列表隐藏）
from app.plugins import sdk  # noqa: E402  # 插件统一 SDK 导入方式

sdk.register_game("coin_flip", CoinFlipEngine, {
    "name": "抛硬币", "player_mode": "dual",
    "min_players": 2, "max_players": 2, "needs_gm": False,
    "description": "喊正反、抛硬币，喊中者赢（扩展包最小示例）",
})
