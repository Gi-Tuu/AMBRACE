"""谁是卧底引擎（多人 · 4-8 人）。

主流规则：平民拿词 A、卧底拿相近词 B；每轮每人一句话描述（不能直接说词）→
投票淘汰得票最多者 → 卧底全灭平民胜、卧底≥平民数卧底胜；最大 15 轮平局。
信息隔离严格：view_for 只给本人词，build_ai_prompt 绝不包含他人词。
"""
from __future__ import annotations

import json
import random

from app.games.base import ActionResult, GameContext, GameEngine, PlayerView
from app.games.gm import gm_announce

# 词对库（偏角色生活/兴趣/共同经历，不选生僻词；50+ 对）
WORD_PAIRS = [
    ("咖啡", "奶茶"), ("手机", "平板"), ("猫", "狗"), ("西瓜", "哈密瓜"),
    ("自行车", "摩托车"), ("眼镜", "墨镜"), ("饺子", "馄饨"), ("红薯", "紫薯"),
    ("钢笔", "铅笔"), ("沙发", "抱枕"), ("台灯", "吊灯"), ("高铁", "动车"),
    ("火锅", "麻辣烫"), ("粽子", "青团"), ("月饼", "蛋糕"), ("相声", "小品"),
    ("健身房", "瑜伽馆"), ("羽毛球", "乒乓球"), ("图书馆", "书店"), ("地铁", "公交"),
    ("薯条", "薯片"), ("冰淇淋", "雪糕"), ("虾", "蟹"), ("企鹅", "海豹"),
    ("熊猫", "考拉"), ("香蕉", "芒果"), ("草莓", "樱桃"), ("可乐", "雪碧"),
    ("啤酒", "白酒"), ("戒指", "手镯"), ("围巾", "腰带"), ("衬衫", "毛衣"),
    ("牛仔裤", "休闲裤"), ("芭蕾", "街舞"), ("钢琴", "吉他"), ("评书", "相声"),
    ("医生", "护士"), ("老师", "教授"), ("警察", "保安"), ("爸爸", "叔叔"),
    ("白菜", "生菜"), ("豆腐", "豆干"), ("豆浆", "牛奶"), ("苹果", "梨"),
    ("兔子", "仓鼠"), ("蜗牛", "乌龟"), ("蜜蜂", "蝴蝶"), ("鲸鱼", "鲨鱼"),
    ("城堡", "宫殿"), ("邻居", "房客"), ("秋千", "滑梯"), ("雨伞", "雨衣"),
    ("闹钟", "手表"), ("口琴", "笛子"), ("围裙", "袖套"), ("面包", "馒头"),
    ("红烧肉", "糖醋排骨"), ("篮球", "足球"),
]


class UndercoverEngine(GameEngine):
    game_type = "undercover"
    player_mode = "multi"
    min_players = 4
    max_players = 8
    needs_gm = True
    MAX_ROUNDS = 15

    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        players = [p for p in self.players if not p.is_spectator]
        n = len(players)
        pair = random.choice(WORD_PAIRS)
        n_undercover = 1 if n <= 5 else 2
        words = [pair[0]] * (n - n_undercover) + [pair[1]] * n_undercover
        random.shuffle(words)
        for i, p in enumerate(players):
            p.role = "undercover" if words[i] == pair[1] else "civilian"
            p.alive = True
            p.private_json = json.dumps({"word": words[i]}, ensure_ascii=False)
        seats = [p.seat for p in players]
        self.state["pair"] = list(pair)
        self.state["describe_order"] = seats
        self.state["describe_idx"] = 0
        self.state["votes"] = {}
        self.state["descriptions"] = {}
        self.session.round = 1
        self.session.phase = "describe"
        self.session.status = "playing"
        return [
            {"event_type": "announce", "content": gm_announce("undercover", "start"), "phase": "start", "visibility": "public"},
            {"event_type": "announce", "content": gm_announce("undercover", "describe", round=self.session.round), "phase": "describe", "visibility": "public"},
        ]

    def _current_describer(self) -> int | None:
        order = self.state.get("describe_order", [])
        idx = self.state.get("describe_idx", 0)
        return order[idx] if idx < len(order) else None

    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        phase = self.session.phase
        if phase == "describe" and action == "describe":
            if seat != self._current_describer():
                return ActionResult(ok=False, error="还没轮到你")
            content = (payload.get("content") or "").strip()
            if len(content) < 2 or len(content) > 120:
                return ActionResult(ok=False, error="描述需 2-120 字")
            word = self.player_private(seat).get("word", "")
            if word and word in content:
                return ActionResult(ok=False, error="描述不能直接说出词语")
            self.state["descriptions"][seat] = content
            return ActionResult(ok=True, event={
                "event_type": "describe", "actor_seat": seat, "phase": "describe",
                "content": content, "visibility": "public",
            })
        if phase == "vote" and action == "vote":
            try:
                target = int(payload.get("target_seat", -1))
            except (TypeError, ValueError):
                return ActionResult(ok=False, error="无效投票目标")
            valid = [p.seat for p in self.active_players() if p.alive and p.seat != seat]
            if target not in valid:
                return ActionResult(ok=False, error="无效投票目标")
            if seat in self.state.get("votes", {}):
                return ActionResult(ok=False, error="你已经投过票")
            self.state["votes"][seat] = target
            return ActionResult(ok=True, event={
                "event_type": "vote", "actor_seat": seat, "target_seat": target, "phase": "vote",
                "content": f"{self.name_of(seat)}投票给{self.name_of(target)}号", "visibility": "public",
                "payload": {"target": target},
            })
        return ActionResult(ok=False, error="非法动作")

    async def advance(self) -> list[dict]:
        events = []
        phase = self.session.phase
        if phase == "describe":
            self.state["describe_idx"] = int(self.state.get("describe_idx", 0)) + 1
            if self.state["describe_idx"] >= len(self.state.get("describe_order", [])):
                self.session.phase = "vote"
                self.state["votes"] = {}
                events.append({"event_type": "announce", "phase": "vote",
                               "content": gm_announce("undercover", "vote"), "visibility": "public"})
            return events
        if phase == "vote":
            alive = [p.seat for p in self.active_players() if p.alive]
            votes = self.state.get("votes", {})
            if not all(s in votes for s in alive):
                return events
            tally: dict[int, int] = {}
            for tgt in votes.values():
                tally[tgt] = tally.get(tgt, 0) + 1
            max_votes = max(tally.values())
            tied = [t for t, c in tally.items() if c == max_votes]
            out_seat = random.choice(tied)
            out_player = self.player_at(out_seat)
            if out_player is None:
                return events
            out_player.alive = False
            word = self.player_private(out_seat).get("word", "")
            events.append({"event_type": "eliminate", "target_seat": out_seat, "phase": "vote",
                           "content": gm_announce("undercover", "eliminated",
                                                  name=self.name_of(out_seat), word=word, role=out_player.role),
                           "visibility": "public", "payload": {"word": word, "role": out_player.role}})
            winner = await self.check_winner()
            if winner:
                self.session.phase = "result"
                events.append({"event_type": "win", "phase": "result",
                               "content": gm_announce("undercover", f"win_{winner}", **({"draw": True} if winner == "draw" else {})),
                               "visibility": "public", "payload": {"winner_side": winner}})
            else:
                self.session.round = int(self.session.round or 0) + 1
                self.state["describe_idx"] = 0
                self.state["votes"] = {}
                self.state["descriptions"] = {}
                self.session.phase = "describe"
                events.append({"event_type": "announce", "phase": "describe",
                               "content": gm_announce("undercover", "describe", round=self.session.round),
                               "visibility": "public"})
            return events
        return events

    async def check_winner(self) -> str | None:
        alive = [p for p in self.active_players() if p.alive]
        wolves = [p for p in alive if p.role == "undercover"]
        civs = [p for p in alive if p.role == "civilian"]
        if not wolves:
            return "civilians"
        if len(wolves) >= len(civs):
            return "undercover"
        if int(self.session.round or 0) >= self.MAX_ROUNDS:
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
        if self.session.phase == "describe":
            return self._current_describer()
        if self.session.phase == "vote":
            alive = [p.seat for p in self.active_players() if p.alive]
            votes = self.state.get("votes", {})
            for s in alive:
                if s not in votes:
                    return s
            return None
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
            public_state={"alive": bool(p.alive)},
        )

    def build_ai_prompt(self, seat: int) -> GameContext:
        me = self.view_for(seat)
        others = [
            {"seat": p.seat, "name": self.name_of(p.seat), "alive": bool(p.alive)}
            for p in self.players if not p.is_spectator and p.seat != seat
        ]
        return GameContext(
            game_type="undercover",
            rules_summary=(
                "你和其他玩家各拿到一个相近的词。平民词相同，卧底词不同但相近。"
                "每轮每人用一句话描述自己的词（不能直接说出词），然后投票淘汰疑似卧底。"
                "卧底全部出局平民赢；卧底人数≥平民人数卧底赢。"
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
        if self.session.phase == "vote":
            return "vote"
        if self.session.phase == "describe":
            return "describe"
        return "skip"

    async def fallback_action(self, seat: int) -> dict:
        if self.session.phase == "describe":
            # 兜底描述：绝不带出词语（防泄漏，且确保 apply_action 合法）
            return {"action": "describe", "content": "我觉得它跟日常生活挺近的。", "payload": {}}
        if self.session.phase == "vote":
            alive = [p.seat for p in self.active_players() if p.alive and p.seat != seat]
            tgt = random.choice(alive) if alive else None
            return {"action": "vote", "content": f"我投{tgt}号。", "payload": {"target_seat": tgt}}
        return {"action": "skip", "content": "", "payload": {}}
