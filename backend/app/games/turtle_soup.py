"""海龟汤引擎（双人 · 2 人）。

主流规则简化：
- AI 默认当主持人出汤面，用户当猜题者；
- 猜题者每次可问一个是非问句（主持人答 是/否/可能/无关/不知道），或直接猜真相；
- 最多 20 问；猜中真相（提交答案与真相关键词匹配）→ 猜题者胜；问满未中 → 主持人胜；
- 汤面/真相 private to 主持人：AI prompt 已知，用户 view 绝不包含真相。
规则判定全确定性代码，零 LLM；LLM 只提问/回答/猜真相。
"""
from __future__ import annotations

import json
import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView
from app.games.gm import gm_announce

_ANSWER_TEXT = {"yes": "是", "no": "否", "possible": "可能", "unrelated": "无关", "unknown": "不知道"}
_ANSWER_SET = set(_ANSWER_TEXT)

# 原创温和谜题（不含血腥/暴力/恐怖/猎奇内容；含真相与关键词用于匹配）
PUZZLES = [
    {
        "surface": "小明的闹钟每天早上 7 点准时响，他从来不去按掉，可他也从来没迟到过。为什么？",
        "truth": "闹钟其实是定给室友的。小明自己早就醒了，闹钟响时他已经坐在餐桌前了。",
        "keywords": ["室友", "定给", "别人", "提醒", "早就醒"],
    },
    {
        "surface": "小美每天下班都特地绕远路回家，朋友们都觉得奇怪，她却乐在其中。为什么？",
        "truth": "绕远路会经过她常去的那家花店，她想顺路买束花，也顺便当作下班后的散步透透气。",
        "keywords": ["花店", "散步", "透气", "买花", "顺路"],
    },
    {
        "surface": "小华把妈妈放在桌上的橡皮从桌边推掉，妈妈不仅没生气，反而笑了。为什么？",
        "truth": "橡皮掉下去正好滚到沙发底下，露出了妈妈一直在找的一串钥匙，帮妈妈找到了东西。",
        "keywords": ["钥匙", "找到", "沙发", "露出", "帮忙"],
    },
    {
        "surface": "早餐店里，一个人买了个面包，咬了一口就放下走出了门，老板还笑着目送他。为什么？",
        "truth": "他是这家店的面包师自己，来检查产品质量。尝到味道不对就走，是要回后厨调整配方。",
        "keywords": ["面包师", "检查", "配方", "产品", "后厨"],
    },
    {
        "surface": "下雨天，小林撑着一把伞站在楼下，却没有接楼上的朋友，朋友也不怪他。为什么？",
        "truth": "雨太大，朋友发消息让他先走，说怕两个人一起淋湿；朋友自己其实有另一把伞。",
        "keywords": ["先走", "雨太大", "另一把", "淋湿", "发消息"],
    },
    {
        "surface": "老张接到一个陌生号码的电话，说了句「打错了」就挂了，可他却特别开心。为什么？",
        "truth": "那是出差的儿子用一个新号码打来报平安，故意装作打错来逗父亲开心。",
        "keywords": ["儿子", "报平安", "出差", "新号码", "逗"],
    },
    {
        "surface": "小丽每次收信都先看收信人，而不是看寄信人。为什么？",
        "truth": "她家的信箱经常错投邻居的信，她得先确认是不是自己的，再决定要不要给邻居送去。",
        "keywords": ["邻居", "错投", "送去", "不是自己的", "收信人"],
    },
    {
        "surface": "小明打开冰箱，里面放着一个写了他名字的蛋糕，他却一口都没吃。为什么？",
        "truth": "那是他明天生日要带去和大家分享的蛋糕，现在就吃了明天就没得带了。",
        "keywords": ["生日", "明天", "带", "分享", "留着"],
    },
]


class TurtleSoupEngine(GameEngine):
    game_type = "turtle_soup"
    player_mode = "dual"
    min_players = 2
    max_players = 2
    needs_gm = False
    MAX_QUESTIONS = 20

    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = [p for p in self.players if not p.is_spectator]
        user_players = [p for p in players if p.player_type == "user"]
        ai_players = [p for p in players if p.player_type == "ai"]
        if ai_players and user_players:
            thinker, guesser = ai_players[0], user_players[0]
        elif ai_players:
            thinker, guesser = ai_players[0], players[1]
        else:
            thinker, guesser = players[0], players[1]
        puzzle = random.choice(PUZZLES)
        truth = puzzle["truth"]
        keywords = puzzle["keywords"]
        surface = puzzle["surface"]
        for p in players:
            p.alive = True
            p.score = 0
            p.role = "thinker" if p.seat == thinker.seat else "guesser"
            p.private_json = json.dumps(
                {"word": truth, "keywords": keywords, "surface": surface}, ensure_ascii=False
            ) if p.seat == thinker.seat else "{}"
        self.state["thinker_seat"] = thinker.seat
        self.state["guesser_seat"] = guesser.seat
        self.state["surface"] = surface
        self.state["truth"] = truth
        self.state["keywords"] = keywords
        self.state["questions"] = 0
        self.state["stage"] = "ask"
        self.state["phase_result"] = ""
        self.session.round = 1
        self.session.phase = "ask"
        self.session.status = "playing"
        return [
            {"event_type": "announce", "phase": "ask", "visibility": "public",
             "content": gm_announce("turtle_soup", "start", thinker=self.name_of(thinker.seat),
                                    guesser=self.name_of(guesser.seat))},
            {"event_type": "announce", "phase": "ask", "visibility": "public",
             "content": f"🍲 汤面：{surface}", "payload": {"surface": surface}},
            {"event_type": "announce", "phase": "ask", "visibility": "public",
             "content": f"{self.name_of(guesser.seat)}请开始提问，最多{self.MAX_QUESTIONS}问（是/否/可能/无关/不知道），也可直接猜真相。",
             "payload": {"max": self.MAX_QUESTIONS}},
        ]

    def _guess_correct(self, text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False
        truth = (self.state.get("truth") or "").lower()
        if truth and truth in t:
            return True
        for kw in self.state.get("keywords") or []:
            if kw.lower() in t:
                return True
        return False

    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        stage = self.state.get("stage")
        guesser_seat = self.state.get("guesser_seat")
        thinker_seat = self.state.get("thinker_seat")
        if stage == "ask" and action in ("ask_soup", "guess_soup"):
            if seat != guesser_seat:
                return ActionResult(ok=False, error="还没轮到你提问")
            if action == "ask_soup":
                content = (payload.get("content") or "").strip()
                if len(content) < 2 or len(content) > 200:
                    return ActionResult(ok=False, error="问题需 2-200 字")
                self.state["questions"] = int(self.state.get("questions", 0)) + 1
                self.state["stage"] = "answer"
                n = self.state["questions"]
                return ActionResult(ok=True, next_phase="answer", event={
                    "event_type": "ask", "actor_seat": seat, "phase": "answer",
                    "content": gm_announce("turtle_soup", "ask", n=n, question=content),
                    "visibility": "public", "payload": {"n": n, "question": content},
                })
            # 猜真相
            word = (payload.get("word") or "").strip()
            if len(word) < 1:
                return ActionResult(ok=False, error="请输入你猜的真相")
            correct = self._guess_correct(word)
            if correct:
                self.state["phase_result"] = "guesser"
                self.state["stage"] = "done"
                return ActionResult(ok=True, next_phase="result", event={
                    "event_type": "guess", "actor_seat": seat, "phase": "ask",
                    "content": gm_announce("turtle_soup", "guess", name=self.name_of(seat), word=word)
                               + gm_announce("turtle_soup", "guess_right"),
                    "visibility": "public", "payload": {"word": word, "correct": True},
                })
            self.state["questions"] = int(self.state.get("questions", 0)) + 1
            return ActionResult(ok=True, event={
                "event_type": "guess", "actor_seat": seat, "phase": "ask",
                "content": gm_announce("turtle_soup", "guess", name=self.name_of(seat), word=word)
                           + gm_announce("turtle_soup", "guess_wrong"),
                "visibility": "public", "payload": {"word": word, "correct": False},
            })
        if stage == "answer" and action == "answer_soup":
            if seat != thinker_seat:
                return ActionResult(ok=False, error="还没轮到你回答")
            ans = payload.get("answer")
            if ans not in _ANSWER_SET:
                return ActionResult(ok=False, error="只能回答是/否/可能/无关/不知道")
            self.state["stage"] = "ask"
            return ActionResult(ok=True, next_phase="ask", event={
                "event_type": "answer", "actor_seat": seat, "target_seat": guesser_seat, "phase": "answer",
                "content": gm_announce("turtle_soup", "answer", name=self.name_of(seat),
                                       answer=_ANSWER_TEXT[ans]),
                "visibility": "public", "payload": {"answer": ans},
            })
        return ActionResult(ok=False, error="非法动作")

    async def advance(self) -> list[dict]:
        events: list[dict] = []
        if not self.state.get("phase_result") and int(self.state.get("questions", 0)) >= int(
                self.state.get("max_questions", self.MAX_QUESTIONS)):
            self.state["phase_result"] = "thinker"
        if self.state.get("phase_result"):
            ws = self.state["phase_result"]
            self.session.phase = "result"
            self.session.winner_side = ws
            self.state["stage"] = "done"
            content = gm_announce("turtle_soup", "win_guesser") if ws == "guesser" else gm_announce("turtle_soup", "win_thinker")
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
        if p.role == "guesser":
            priv = {}  # 猜题者绝不看到真相
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
                "你是海龟汤主持人。汤面和真相只告诉了你（见下方身份信息），绝不能泄漏真相。"
                "猜题者只能问是非问句，你只能回答 是/否/可能/无关/不知道，并依据真相如实回答。"
                f"汤面：{self.state.get('surface', '')}。"
            )
        else:
            rules = (
                "你是猜题者。主持人给了一个古怪的汤面，你要用最多20个是/否/可能/无关/不知道的问句"
                "一步步缩小范围，也可以随时直接说出你猜的真相。猜中即赢；问满20问没猜中则输。"
                f"汤面：{self.state.get('surface', '')}。"
            )
        return GameContext(
            game_type="turtle_soup",
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
            return "answer_soup"
        if stage == "ask" and seat == self.state.get("guesser_seat"):
            n = int(self.state.get("questions", 0))
            if self.is_ai(seat) and n >= 4 and n % 4 == 3:
                return "guess_soup"
            return "ask_soup"
        return "skip"

    async def fallback_action(self, seat: int) -> dict:
        stage = self.state.get("stage")
        if stage == "answer" and seat == self.state.get("thinker_seat"):
            return {"action": "answer_soup", "content": "可能", "payload": {"answer": "possible"}}
        if stage == "ask" and seat == self.state.get("guesser_seat"):
            kw = self.state.get("keywords") or []
            word = random.choice(kw) if kw else (self.state.get("truth") or "")
            return {"action": "guess_soup", "content": f"我猜真相是：{word}", "payload": {"word": word}}
        return {"action": "skip", "content": "", "payload": {}}
