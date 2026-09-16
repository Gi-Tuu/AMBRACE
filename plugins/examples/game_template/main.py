"""猜数字 —— game_pack 模板（X1，2026-08-31）。

本文件同时是「可真跑的最小游戏」与「改造成自己游戏的模板」：
- 顶部 Engine 类实现 GameEngine 的**全部必需接口**（以 backend/app/games/base.py 为准）；
- 文件末尾经 sdk.register_game(...) 注册（source=本插件名），内核负责房间/回合/主持/记忆隔离；
- 演示点：私有信息隔离——秘密数字只出现在「出题人」的 PlayerView.private，「猜题人」看不到。

玩法（2 人 dual）：座次 0 出题人喊一个数字（1-100）作为秘密 → 座次 1 猜题人逐次猜 →
  每次提示「大了/小了」，限定次数内猜中则猜题人胜、猜题人用完次数则出题人胜。
规则判定全程确定性代码、零 LLM（AI 玩家发言才走 LLM，与本引擎无关）。

复制本目录改名后改造清单（详见同目录 README.md）：
  1. 改 manifest.json 的 name / description；
  2. 改 class 名与 game_type、player_mode/min_players/max_players；
  3. 改 setup / apply_action / advance / check_winner / timeout / view_for / build_ai_prompt /
     expected_action / fallback_action / current_turn_seat 九个方法；
  4. 游戏内存放状态用 self.state（会随局持久化），私有信息用玩家 private_json（仅本人可见）。
"""
from __future__ import annotations

import json
import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView

_MAX_GUESSES = 7  # 猜题人最多猜几次


class NumberGuessEngine(GameEngine):
    game_type = "number_guess"
    player_mode = "dual"
    min_players = 2
    max_players = 2
    needs_gm = False

    # ── 生命周期：发牌/初始化 ───────────────────────────────────────────────
    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = self.active_players()
        picker, guesser = players[0], players[1]
        for p in players:
            p.role = "picker" if p.seat == picker.seat else "guesser"
            p.alive = True
            p.score = 0
            p.private_json = "{}"  # 秘密稍后由出题人写入自己的 private
        self.state["picker_seat"] = picker.seat
        self.state["guesser_seat"] = guesser.seat
        self.state["secret"] = None           # 引擎逻辑用：正确答案
        self.state["stage"] = "set_secret"    # set_secret → guess → done
        self.state["guesses_used"] = 0
        self.state["max_guesses"] = _MAX_GUESSES
        self.state["last_hint"] = ""
        self.session.round = 1
        self.session.phase = "set_secret"
        self.session.status = "playing"
        return [{
            "event_type": "announce", "phase": "set_secret",
            "content": f"🔢 猜数字开始！{self.name_of(picker.seat)} 请先喊一个 1-100 的秘密数字。",
            "visibility": "public",
        }]

    # ── 玩家动作 ───────────────────────────────────────────────────────────
    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        stage = self.state.get("stage")

        # 出题人设定秘密
        if stage == "set_secret" and action == "set_secret":
            if seat != self.state.get("picker_seat"):
                return ActionResult(ok=False, error="还没轮到你出题")
            number = payload.get("number")
            if not isinstance(number, int) or not (1 <= number <= 100):
                return ActionResult(ok=False, error="秘密数字必须是 1-100 的整数")
            self.state["secret"] = number
            picker = self.player_at(seat)
            picker.private_json = json.dumps({"secret": number}, ensure_ascii=False)
            self.state["stage"] = "guess"
            self.session.phase = "guess"
            return ActionResult(ok=True, next_phase="guess", event={
                "event_type": "set_secret", "actor_seat": seat, "phase": "guess",
                "content": f"{self.name_of(seat)} 已设定秘密数字，轮到 {self.name_of(self.state['guesser_seat'])} 猜了。",
                "visibility": "public", "payload": {"set": True},
            })

        # 猜题人猜数字
        if stage == "guess" and action == "guess":
            if seat != self.state.get("guesser_seat"):
                return ActionResult(ok=False, error="还没轮到你猜")
            secret = self.state.get("secret")
            if secret is None:
                return ActionResult(ok=False, error="出题人还没设定秘密数字")
            number = payload.get("number")
            if not isinstance(number, int) or not (1 <= number <= 100):
                return ActionResult(ok=False, error="猜测必须是 1-100 的整数")
            self.state["guesses_used"] = int(self.state.get("guesses_used", 0)) + 1
            used = self.state["guesses_used"]

            if number == secret:
                return self._finish(self.state["guesser_seat"],
                                    f"🎉 {self.name_of(self.state['guesser_seat'])} 第 {used} 次猜中 {secret}，猜题人获胜！")
            if used >= int(self.state.get("max_guesses", _MAX_GUESSES)):
                return self._finish(self.state["picker_seat"],
                                    f"💡 {self.name_of(self.state['guesser_seat'])} 用完 {used} 次机会没猜中，出题人获胜（答案是 {secret}）。")

            hint = "小了" if number < secret else "大了"
            self.state["last_hint"] = hint
            return ActionResult(ok=True, event={
                "event_type": "guess", "actor_seat": seat, "phase": "guess",
                "content": f"{self.name_of(seat)} 猜了 {number} —— {hint}（已用 {used}/{self.state['max_guesses']} 次）。",
                "visibility": "public", "payload": {"guess": number, "hint": hint, "used": used},
            })

        return ActionResult(ok=False, error="非法动作或当前阶段不可操作")

    def _finish(self, winner_seat: int, content: str) -> ActionResult:
        self.state["stage"] = "done"
        self.state["phase_result"] = str(winner_seat)
        self.session.phase = "result"
        self.session.winner_side = str(winner_seat)
        return ActionResult(ok=True, next_phase="result", event={
            "event_type": "win", "phase": "result",
            "content": content,
            "visibility": "public", "payload": {"winner_seat": winner_seat},
        })

    # ── 阶段推进（本游戏每步动作即裁决，advance 无需额外动作）─────────────────
    async def advance(self) -> list[dict]:
        return []

    # ── 胜负判定 ───────────────────────────────────────────────────────────
    async def check_winner(self) -> str | None:
        if self.state.get("stage") == "done":
            return self.state.get("phase_result")
        return None

    # ── 超时自动推进（调用兜底动作，不阻塞游戏）──────────────────────────────
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

    # ── 视图（私有信息隔离核心）─────────────────────────────────────────────
    def current_turn_seat(self) -> int | None:
        if self.state.get("stage") == "set_secret":
            return self.state.get("picker_seat")
        if self.state.get("stage") == "guess":
            return self.state.get("guesser_seat")
        return None

    def view_for(self, seat: int) -> PlayerView:
        p = self.player_at(seat)
        if p is None:
            raise ValueError(f"no player at seat {seat}")
        # 私有信息：秘密数字只有出题人本人可见；猜题人 private 必须为空
        private = {}
        if seat == self.state.get("picker_seat") and self.state.get("secret") is not None:
            private = {"secret": self.state["secret"]}
        return PlayerView(
            seat=p.seat, player_type=p.player_type, character_id=p.character_id,
            name=self.name_of(p.seat), role=p.role or "player", alive=bool(p.alive),
            is_spectator=bool(p.is_spectator), private=private,
            public_state={
                "stage": self.state.get("stage"),
                "guesses_used": self.state.get("guesses_used", 0),
                "max_guesses": self.state.get("max_guesses", _MAX_GUESSES),
                "last_hint": self.state.get("last_hint", ""),
                "turn": self.current_turn_seat() == seat,
            },
        )

    # ── AI 决策上下文（只含该玩家可见信息）──────────────────────────────────
    def build_ai_prompt(self, seat: int) -> GameContext:
        me = self.view_for(seat)
        others = [
            {"seat": q.seat, "name": self.name_of(q.seat), "alive": bool(q.alive)}
            for q in self.active_players() if q.seat != seat
        ]
        return GameContext(
            game_type="number_guess",
            rules_summary=(
                "两人玩猜数字：出题人先想一个 1-100 的秘密数字（只有出题人自己看得到），"
                "猜题人逐次猜，每次会得到「大了/小了」的提示；"
                "限定次数内猜中则猜题人胜，猜题人用完次数则出题人胜。"
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
        if self.state.get("stage") == "set_secret" and seat == self.state.get("picker_seat"):
            return "set_secret"
        if self.state.get("stage") == "guess" and seat == self.state.get("guesser_seat"):
            return "guess"
        return "skip"

    async def fallback_action(self, seat: int) -> dict:
        if self.state.get("stage") == "set_secret":
            n = random.randint(1, 100)
            return {"action": "set_secret", "content": f"那就选 {n} 吧", "payload": {"number": n}}
        n = random.randint(1, 100)
        return {"action": "guess", "content": f"我猜 {n}", "payload": {"number": n}}


# ── X1：游戏扩展包注册（source=本插件名 game_template；插件停用后自动从游戏列表隐藏）──
from app.plugins import sdk  # noqa: E402  # 插件统一 SDK 导入方式

sdk.register_game("number_guess", NumberGuessEngine, {
    "name": "猜数字", "player_mode": "dual",
    "min_players": 2, "max_players": 2, "needs_gm": False,
    "description": "出题人设秘密数字、猜题人限时猜，演示 GameEngine 全接口与私有信息隔离（扩展包模板）",
})
