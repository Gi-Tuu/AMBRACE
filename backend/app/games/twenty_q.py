"""猜词20问引擎（单人 · 1v1）。

主流规则：一方想词（人/事/物/地点），另一方用最多 20 个是非问句猜；只能回答
是/否/可能/不确定。猜测正确→猜方赢；问满 20 问未猜中→想词方赢。
关系微调：AI 想词优先选和角色生活/兴趣相关的词（MVP 从内置词池选择）。
信息隔离：想词方的词对猜词方 hidden。
"""
from __future__ import annotations

import json
import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView
from app.games.gm import gm_announce

_WORD_POOL = [
    "咖啡", "蛋糕", "火锅", "西瓜", "苹果", "汉堡", "冰淇淋", "奶茶", "饺子", "薯片",
    "猫", "狗", "兔子", "熊猫", "企鹅", "鹦鹉", "仓鼠", "金鱼", "乌龟", "考拉",
    "手机", "平板", "电视", "冰箱", "空调", "洗衣机", "电风扇", "台灯", "雨伞", "眼镜",
    "北京", "上海", "广州", "公园", "学校", "图书馆", "电影院", "游乐场", "海边", "雪山",
    "钢琴", "吉他", "篮球", "羽毛球", "游泳", "跑步", "瑜伽", "骑行", "跳绳", "滑冰",
    "唱歌", "跳舞", "画画", "写诗", "阅读", "看电影", "下棋", "旅行", "钓鱼", "做家务",
    "医生", "老师", "警察", "厨师", "程序员", "设计师", "司机", "宇航员", "记者", "画家",
]

_ANSWER_TEXT = {"yes": "是", "no": "否", "possible": "可能", "uncertain": "不确定"}


class TwentyQEngine(GameEngine):
    game_type = "twenty_q"
    player_mode = "single"
    min_players = 2
    max_players = 2
    needs_gm = False
    MAX_QUESTIONS = 20

    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = [p for p in self.players if not p.is_spectator]
        user_players = [p for p in players if p.player_type == "user"]
        ai_players = [p for p in players if p.player_type == "ai"]
        if user_players and ai_players:
            thinker, guesser = ai_players[0], user_players[0]
        else:
            thinker, guesser = players[0], players[1]
        word = random.choice(_WORD_POOL)
        for p in players:
            p.role = "thinker" if p.seat == thinker.seat else "guesser"
            p.alive = True
            p.score = 0
            p.private_json = json.dumps({"word": word}, ensure_ascii=False) if p.seat == thinker.seat else "{}"
        self.state["thinker_seat"] = thinker.seat
        self.state["guesser_seat"] = guesser.seat
        self.state["word"] = word
        self.state["questions"] = 0
        self.state["max_questions"] = self.MAX_QUESTIONS
        self.state["stage"] = "ask"
        self.state["last_question"] = ""
        self.state["phase_result"] = ""
        self.session.round = 1
        self.session.phase = "ask"
        self.session.status = "playing"
        return [
            {"event_type": "announce", "phase": "ask",
             "content": gm_announce("twenty_q", "start",
                                    thinker_name=self.name_of(thinker.seat),
                                    guesser_name=self.name_of(guesser.seat)),
             "visibility": "public"},
            {"event_type": "announce", "phase": "ask",
             "content": f"{self.name_of(guesser.seat)}请开始提问，最多{self.MAX_QUESTIONS}问。",
             "visibility": "public"},
        ]

    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        stage = self.state.get("stage")
        guesser_seat = self.state.get("guesser_seat")
        thinker_seat = self.state.get("thinker_seat")
        if stage == "ask" and action == "ask":
            if seat != guesser_seat:
                return ActionResult(ok=False, error="还没轮到你提问")
            content = (payload.get("content") or "").strip()
            if len(content) < 2 or len(content) > 200:
                return ActionResult(ok=False, error="问题需 2-200 字")
            self.state["questions"] = int(self.state.get("questions", 0)) + 1
            self.state["last_question"] = content
            self.state["stage"] = "answer"
            n = self.state["questions"]
            return ActionResult(ok=True, next_phase="answer", event={
                "event_type": "ask", "actor_seat": seat, "phase": "answer",
                "content": gm_announce("twenty_q", "ask", n=n, question=content),
                "visibility": "public", "payload": {"n": n, "question": content},
            })
        if stage == "ask" and action == "guess":
            if seat != guesser_seat:
                return ActionResult(ok=False, error="还没轮到你猜")
            word = (payload.get("word") or "").strip()
            if len(word) < 1:
                return ActionResult(ok=False, error="请输入你猜的词")
            correct = word == self.state.get("word")
            if correct:
                self.state["phase_result"] = "guesser"
                self.state["stage"] = "done"
            return ActionResult(ok=True, next_phase=("result" if correct else "ask"), event={
                "event_type": "guess", "actor_seat": seat, "phase": "ask",
                "content": gm_announce("twenty_q", "guess", name=self.name_of(seat), word=word)
                           + (gm_announce("twenty_q", "guess_right") if correct else gm_announce("twenty_q", "guess_wrong")),
                "visibility": "public",
                "payload": {"word": word, "correct": correct, "winner_side": ("guesser" if correct else "")},
            })
        if stage == "answer" and action == "answer":
            if seat != thinker_seat:
                return ActionResult(ok=False, error="还没轮到你回答")
            ans = payload.get("answer")
            if ans not in _ANSWER_TEXT:
                return ActionResult(ok=False, error="只能回答是/否/可能/不确定")
            self.state["stage"] = "ask"
            return ActionResult(ok=True, next_phase="ask", event={
                "event_type": "answer", "actor_seat": seat, "target_seat": guesser_seat, "phase": "answer",
                "content": gm_announce("twenty_q", "answer", name=self.name_of(seat), answer=_ANSWER_TEXT[ans]),
                "visibility": "public", "payload": {"answer": ans},
            })
        return ActionResult(ok=False, error="非法动作")

    async def advance(self) -> list[dict]:
        events = []
        if (self.state.get("stage") == "ask"
                and int(self.state.get("questions", 0)) >= int(self.state.get("max_questions", self.MAX_QUESTIONS))
                and not self.state.get("phase_result")):
            self.state["phase_result"] = "thinker"
            self.state["stage"] = "done"
        if self.state.get("phase_result"):
            ws = self.state["phase_result"]
            self.session.phase = "result"
            self.session.winner_side = ws  # v3.3.5 审查修复：引擎独立使用时 winner_side 不丢失
            self.state["stage"] = "done"
            content = gm_announce("twenty_q", "win_guesser") if ws == "guesser" else gm_announce("twenty_q", "win_thinker")
            events.append({"event_type": "win", "phase": "result", "content": content,
                           "visibility": "public", "payload": {"winner_side": ws}})
        return events

    async def check_winner(self) -> str | None:
        ws = self.state.get("phase_result")
        return ws if ws in ("guesser", "thinker") else None

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
        if self.state.get("stage") == "ask":
            return self.state.get("guesser_seat")
        if self.state.get("stage") == "answer":
            return self.state.get("thinker_seat")
        return None

    def view_for(self, seat: int) -> PlayerView:
        p = self.player_at(seat)
        if p is None:
            raise ValueError(f"no player at seat {seat}")
        priv = {} if p.is_spectator else self.player_private(seat)
        return PlayerView(
            seat=p.seat, player_type=p.player_type, character_id=p.character_id,
            name=self.name_of(p.seat), role=p.role, alive=bool(p.alive),
            is_spectator=bool(p.is_spectator), private=priv,
            public_state={
                "alive": bool(p.alive),
                "questions": self.state.get("questions", 0),
                "max_questions": self.state.get("max_questions", self.MAX_QUESTIONS),
                "turn": self.current_turn_seat() == seat,
            },
        )

    def build_ai_prompt(self, seat: int) -> GameContext:
        me = self.view_for(seat)
        others = [
            {"seat": q.seat, "name": self.name_of(q.seat), "alive": bool(q.alive)}
            for q in self.active_players() if q.seat != seat
        ]
        if me.role == "thinker":
            rules = (
                "你心里想了一个词（下面只告诉你，绝不能泄露给猜方）。猜方用是/否/可能/不确定的问句来猜，"
                "你根据这个词如实回答；最多次数用完猜方还没猜中就算你赢。"
            )
        else:
            rules = (
                "对方想了一个词（人/事/物/地点）。你最多用20个是/否/可能/不确定的问句来缩小范围，"
                "也可以随时直接猜词。猜中即赢；问满20问没猜中则输。"
            )
        return GameContext(
            game_type="twenty_q",
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
        if self.state.get("phase_result"):
            return "skip"
        stage = self.state.get("stage")
        if stage == "answer" and seat == self.state.get("thinker_seat"):
            return "answer"
        if stage == "ask" and seat == self.state.get("guesser_seat"):
            n = int(self.state.get("questions", 0))
            # 让 AI 猜方周期性偶尔猜词（增强可玩性），人类猜方在前端可自由提问/猜词。
            if n >= 4 and n % 4 == 3:
                return "guess"
            return "ask"
        return "skip"

    async def fallback_action(self, seat: int) -> dict:
        stage = self.state.get("stage")
        if stage == "answer" and seat == self.state.get("thinker_seat"):
            return {"action": "answer", "content": "是", "payload": {"answer": "yes"}}
        if stage == "ask" and seat == self.state.get("guesser_seat"):
            n = int(self.state.get("questions", 0))
            if n >= 4 and n % 4 == 3:
                gw = random.choice(_WORD_POOL)
                return {"action": "guess", "content": f"我猜是{gw}", "payload": {"word": gw}}
            return {"action": "ask", "content": "是生活里常见的东西吗？", "payload": {}}
        return {"action": "skip", "content": "", "payload": {}}
