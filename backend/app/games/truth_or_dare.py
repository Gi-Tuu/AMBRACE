"""真心话大冒险引擎（双人 · 2 人）。

主流规则：轮流选"真心话"或"大冒险"；对方出题/给任务；完成或接受惩罚
（惩罚=扣分/表情，不做现实惩罚）。
位置规则微调：题目由 LLM 生成但经硬性护栏——
- 关键词黑名单（暴力/危险/性/越界称呼）直接拦截；
- 暧昧度上限按关系等级代码判定（不由 LLM 自定）；
- 超界自动降级为安全模板题。
"""
from __future__ import annotations

import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView
from app.games.gm import gm_announce

# 硬性黑名单：命中一律拦截（暴力/危险/性/越界称呼）
# v3.3.5 审查修复（2026-08-26）：单字改多字词组，避免误杀"性格/绑定/血压"等正常词
_BLOCK_WORDS = [
    "自杀", "自残", "割腕", "跳楼", "跳河", "去死", "找死", "弄死", "杀死", "杀人",
    "色情", "性行为", "性交", "做爱", "上床", "出轨", "约炮", "强奸", "侵犯", "下药",
    "赌博", "吸毒", "毒品", "下毒", "服毒", "放火", "撞车", "勒索", "绑架", "绑票",
    "裸照", "裸体", "怀孕", "强吻", "舌吻", "亲嘴", "捅刀", "拿刀", "动刀",
    "流血", "出血", "血迹", "警察局",
]
# 高暧昧度：仅在关系等级 >=2 时允许，其余拦截
_HIGH_INTIMACY = [
    "喜欢我", "爱我", "心动", "吻我", "亲我", "亲一下", "亲亲", "抱我", "想你", "想我",
    "嫁给我", "娶我", "男朋友", "女朋友", "恋爱", "一起睡", "同居", "结婚",
    "对象", "老公", "老婆",
]
# 关系等级判定：relation_type 关键词 → 等级（>=2 才允许高暧昧）
_REL_TIER = {
    "恋人": 2, "对象": 2, "伴侣": 2, "爱人": 2, "男友": 2, "女友": 2,
    "老公": 2, "老婆": 2, "女朋友": 2, "男朋友": 2,
    "闺蜜": 1, "兄弟": 1, "姐们": 1, "哥们": 1, "家人": 1, "亲人": 1,
}
_SAFE_TRUTH = [
    "最近最让你开心的一件小事是什么？",
    "如果明天放假，你最想做什么？",
    "用一个词形容你现在的心情。",
    "你最喜欢家里哪个角落？",
]
_SAFE_DARE = [
    "学猫叫一声。",
    "用三个词夸夸我。",
    "做一个你最拿手的表情。",
    "说出你今天最想感谢的人。",
]


class TruthOrDareEngine(GameEngine):
    game_type = "truth_or_dare"
    player_mode = "dual"
    min_players = 2
    max_players = 2
    needs_gm = False
    MAX_MOVES = 6  # 每轮（选+出题+回答）= 1 move；6 move 后按分定胜负

    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = [p for p in self.players if not p.is_spectator]
        first, second = players[0], players[1]
        for p in players:
            p.role = "player"
            p.alive = True
            p.score = 0
            p.private_json = "{}"
        self.state["turn_seat"] = first.seat
        self.state["giver_seat"] = second.seat
        self.state["stage"] = "choose"
        self.state["last_choice"] = ""
        self.state["scores"] = {p.seat: 0 for p in players}
        self.state["moves"] = 0
        self.state["max_moves"] = self.MAX_MOVES
        self.state["pending_round_end"] = False
        self.session.round = 1
        self.session.phase = "choose"
        self.session.status = "playing"
        return [
            {"event_type": "announce", "phase": "choose",
             "content": gm_announce("truth_or_dare", "start", p1_name=self.name_of(first.seat)),
             "visibility": "public"},
            {"event_type": "announce", "phase": "choose",
             "content": f"先由{self.name_of(first.seat)}选择真心话还是大冒险。", "visibility": "public"},
        ]

    def _tier(self, seat: int) -> int:
        rt = self._meta(seat).get("relation_type") or ""
        for k, v in _REL_TIER.items():
            if k in rt:
                return v
        return 0

    def _guard(self, text: str, want: str, tier: int) -> bool:
        for w in _BLOCK_WORDS:
            if w in text:
                return True
        if tier < 2:
            for w in _HIGH_INTIMACY:
                if w in text:
                    return True
        return False

    def _safe(self, want: str) -> str:
        pool = _SAFE_TRUTH if want == "truth" else _SAFE_DARE
        return random.choice(pool)

    def _compute_winner(self) -> str:
        scores = self.state.get("scores", {})
        if not scores:
            return "draw"
        maxs = max(scores.values())
        winners = [s for s, v in scores.items() if v == maxs]
        if len(winners) != 1:
            return "draw"
        return f"seat_{winners[0]}"

    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        stage = self.state.get("stage")
        if stage == "choose" and action == "choose":
            if seat != self.state.get("turn_seat"):
                return ActionResult(ok=False, error="还没轮到你")
            choice = payload.get("choice")
            if choice not in ("truth", "dare"):
                return ActionResult(ok=False, error="只能选真心话或大冒险")
            self.state["last_choice"] = choice
            self.state["stage"] = "give"
            return ActionResult(ok=True, next_phase="give", event={
                "event_type": f"choose_{choice}", "actor_seat": seat, "phase": "give",
                "content": gm_announce("truth_or_dare", f"choose_{choice}", name=self.name_of(seat)),
                "visibility": "public", "payload": {"choice": choice},
            })
        if stage == "give" and action in ("give_truth", "give_dare"):
            if seat != self.state.get("giver_seat"):
                return ActionResult(ok=False, error="还没轮到你出题")
            want = "truth" if action == "give_truth" else "dare"
            if self.state.get("last_choice") != want:
                return ActionResult(ok=False, error="题目类型与选择不一致")
            content = (payload.get("content") or "").strip()
            guarded = False
            if not content:
                content = self._safe(want)
                guarded = True
            elif self._guard(content, want, self._tier(seat)):
                content = self._safe(want)
                guarded = True
            content = content[:300]
            key = "last_question" if want == "truth" else "last_task"
            self.state[key] = content
            self.state["stage"] = "answer"
            return ActionResult(ok=True, next_phase="answer", event={
                "event_type": action, "actor_seat": seat,
                "target_seat": self.state.get("turn_seat"), "phase": "answer",
                "content": gm_announce("truth_or_dare", f"give_{want}",
                                       name=self.name_of(seat),
                                       target=self.name_of(self.state.get("turn_seat")),
                                       question=content if want == "truth" else "",
                                       task=content if want == "dare" else ""),
                "visibility": "public", "payload": {"content": content, "guarded": guarded},
            })
        if stage == "answer" and action in ("answer_truth", "complete_dare", "penalty"):
            if seat != self.state.get("turn_seat"):
                return ActionResult(ok=False, error="还没轮到你")
            want = self.state.get("last_choice")
            if action == "penalty":
                content = (payload.get("content") or "").strip() or "接受惩罚"
                self.state["scores"][seat] = self.state.get("scores", {}).get(seat, 0) - 2
                event_type = "penalty"
                event_content = gm_announce("truth_or_dare", "penalty",
                                            name=self.name_of(seat), penalty=2)
            elif action == "answer_truth" and want == "truth":
                content = (payload.get("content") or "").strip() or "……"
                self.state["scores"][seat] = self.state.get("scores", {}).get(seat, 0) + 2
                event_type = "answer"
                event_content = gm_announce("truth_or_dare", "answer",
                                            name=self.name_of(seat), content=content)
            elif action == "complete_dare" and want == "dare":
                content = (payload.get("content") or "").strip() or "完成任务"
                self.state["scores"][seat] = self.state.get("scores", {}).get(seat, 0) + 2
                event_type = "answer"
                event_content = gm_announce("truth_or_dare", "answer",
                                            name=self.name_of(seat), content=content)
            else:
                return ActionResult(ok=False, error="动作与当前选择不匹配")
            self.state["pending_round_end"] = True
            self.state["stage"] = "answer_done"
            return ActionResult(ok=True, next_phase="round_end", event={
                "event_type": event_type, "actor_seat": seat, "phase": "answer",
                "content": event_content, "visibility": "public",
                "payload": {"content": content, "score": self.state.get("scores", {}).get(seat, 0)},
            })
        return ActionResult(ok=False, error="非法动作")

    async def advance(self) -> list[dict]:
        if not self.state.get("pending_round_end"):
            return []
        self.state["pending_round_end"] = False
        self.state["moves"] = int(self.state.get("moves", 0)) + 1
        self.session.round = int(self.state["moves"]) + 1
        if int(self.state["moves"]) >= int(self.state.get("max_moves", self.MAX_MOVES)):
            winner = self._compute_winner()
            self.session.phase = "result"
            self.state["stage"] = "done"
            content = f"对局结束，本局胜者是：{self.name_of(self._winner_seat(winner))}。" if winner != "draw" else "对局结束，平局。"
            return [{"event_type": "win", "phase": "result", "content": content,
                     "visibility": "public", "payload": {"winner_side": winner}}]
        cp, gp = self.state["turn_seat"], self.state["giver_seat"]
        self.state["turn_seat"], self.state["giver_seat"] = gp, cp
        self.state["stage"] = "choose"
        self.state["last_choice"] = ""
        self.session.phase = "choose"
        return [{"event_type": "announce", "phase": "choose",
                 "content": f"轮到{self.name_of(self.state['turn_seat'])}选择真心话还是大冒险。",
                 "visibility": "public"}]

    def _winner_seat(self, winner: str) -> int:
        try:
            return int(winner.split("_")[-1])
        except Exception:
            return self.state.get("turn_seat", 0)

    async def check_winner(self) -> str | None:
        if int(self.state.get("moves", 0)) >= int(self.state.get("max_moves", self.MAX_MOVES)):
            return self._compute_winner()
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
        stage = self.state.get("stage")
        if stage in ("choose", "answer"):
            return self.state.get("turn_seat")
        if stage == "give":
            return self.state.get("giver_seat")
        return None

    def view_for(self, seat: int) -> PlayerView:
        p = self.player_at(seat)
        if p is None:
            raise ValueError(f"no player at seat {seat}")
        return PlayerView(
            seat=p.seat, player_type=p.player_type, character_id=p.character_id,
            name=self.name_of(p.seat), role=p.role or "player", alive=bool(p.alive),
            is_spectator=bool(p.is_spectator), private={},
            public_state={"alive": bool(p.alive), "score": self.state.get("scores", {}).get(p.seat, 0),
                          "turn": self.current_turn_seat() == seat},
        )

    def build_ai_prompt(self, seat: int) -> GameContext:
        me = self.view_for(seat)
        others = [
            {"seat": q.seat, "name": self.name_of(q.seat), "alive": bool(q.alive)}
            for q in self.active_players() if q.seat != seat
        ]
        return GameContext(
            game_type="truth_or_dare",
            rules_summary=(
                "两人轮流选真心话或大冒险。选完由对方出题给你，你回答（真心话）或完成（大冒险）；"
                "不想完成可接受惩罚扣分。完成加分、惩罚扣分，得分高者赢。"
                "出题要健康、符合关系，绝不涉及危险或越界内容。"
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
        stage = self.state.get("stage")
        if stage == "choose":
            return "choose"
        if stage == "give":
            return "give_truth" if self.state.get("last_choice") == "truth" else "give_dare"
        if stage == "answer":
            return "answer_truth" if self.state.get("last_choice") == "truth" else "complete_dare"
        return "skip"

    async def fallback_action(self, seat: int) -> dict:
        stage = self.state.get("stage")
        if stage == "choose":
            return {"action": "choose", "content": "我选真心话", "payload": {"choice": "truth"}}
        if stage == "give":
            want = self.state.get("last_choice")
            return {"action": "give_truth" if want == "truth" else "give_dare",
                    "content": self._safe(want), "payload": {}}
        if stage == "answer":
            return {"action": "answer_truth", "content": "我想想……", "payload": {}}
        return {"action": "skip", "content": "", "payload": {}}
