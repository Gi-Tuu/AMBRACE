"""游戏引擎基类。

所有游戏状态机继承此类。规则判定全部确定性代码，零 LLM；LLM 只在
ai_player.py 生成发言/选择。v1.1 接口对齐：
load / persist_event / persist_state / finish / current_turn_seat /
player_at / meta / fallback_action / all_events_ordered / view_for /
public_events / build_ai_prompt / expected_action / setup / apply_action /
advance / check_winner / timeout。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class PlayerView:
    """单个玩家的可见视图（信息隔离的核心数据结构）。"""

    seat: int
    player_type: str  # user / ai
    character_id: int | None
    name: str
    role: str  # 该玩家自己的角色（其他人看到的是 "?"）
    alive: bool
    is_spectator: bool
    private: dict = field(default_factory=dict)  # 手牌/词语等
    public_state: dict = field(default_factory=dict)  # 公开状态（是否存活等）


@dataclass
class GameContext:
    """传给 AI 玩家 LLM 的上下文——只含该玩家可见的信息。"""

    game_type: str
    rules_summary: str
    public_events: list[dict]
    players_public: list[dict]
    my_view: PlayerView
    my_persona: dict
    phase: str
    round: int
    my_turn: bool


@dataclass
class ActionResult:
    ok: bool
    event: dict | None = None
    next_phase: str | None = None
    error: str = ""


class GameEngine(ABC):
    """游戏状态机基类。

    约定：
    - `self.session`：GameSession ORM 对象（或测试中的轻量桩）。
    - `self.players`：GamePlayer ORM 列表（含观战者）。
    - `self.state`：运行时状态 dict（从 state_json 加载/持久化）。
    - `self._events`：本局事件 list[dict]（权威流水，与 game_events 表一致）。
    - `state["player_meta"]`：{seat: {name, character_id, personality, chat_style}}，
      持久化在 state_json，保证 load 后无需查库即可取人名/人设。
    """

    game_type: str = ""
    player_mode: str = ""  # single / dual / multi
    min_players: int = 2
    max_players: int = 8
    needs_gm: bool = False

    def __init__(self, session):
        self.session = session
        self.state: dict = {}
        self.players: list = []
        self._events: list[dict] = []

    # ── 生命周期（子类实现）────
    @abstractmethod
    async def setup(self, player_seats: list[dict] | None = None) -> list[dict]:
        """发牌/分配角色/初始化状态。返回初始公开事件列表（GM 播报）。"""
        raise NotImplementedError

    @abstractmethod
    async def apply_action(self, seat: int, action: str, payload: dict) -> ActionResult:
        """校验并执行一个玩家动作。非法动作返回 ok=False。"""
        raise NotImplementedError

    @abstractmethod
    async def advance(self) -> list[dict]:
        """阶段推进（所有人发言完→投票，投票完→结算）。返回新事件列表。"""
        raise NotImplementedError

    @abstractmethod
    async def check_winner(self) -> str | None:
        """返回胜利方标识，None=未结束。"""
        raise NotImplementedError

    @abstractmethod
    async def timeout(self) -> list[dict]:
        """超时自动推进（随机合法动作/跳过）。返回事件。"""
        raise NotImplementedError

    # ── 视图（信息隔离）────
    @abstractmethod
    def view_for(self, seat: int) -> PlayerView:
        """构造某玩家的可见视图。其他人 hidden 信息绝不出现。"""
        raise NotImplementedError

    def public_events(self) -> list[dict]:
        """所有 visibility=public 的事件（观战者也看这些）。"""
        return [e for e in self.all_events_ordered() if e.get("visibility", "public") == "public"]

    def public_events_for(self, seat: int) -> list[dict]:
        """该玩家可见事件：公开事件 + 指向本座次的 private 事件。"""
        out = []
        for e in self.all_events_ordered():
            if e.get("visibility", "public") == "public":
                out.append(e)
            elif e.get("private_to_seat") == seat:
                out.append(e)
        return out

    def my_events(self, seat: int) -> list[dict]:
        """该玩家自己的行动事件。"""
        return [e for e in self.all_events_ordered() if e.get("actor_seat") == seat]

    # ── AI 决策提示（子类实现）────
    @abstractmethod
    def build_ai_prompt(self, seat: int) -> GameContext:
        """组装 AI 玩家的 LLM 上下文（只含可见信息 + 人设）。"""
        raise NotImplementedError

    @abstractmethod
    def expected_action(self, seat: int) -> str:
        """当前该玩家应做什么（describe/vote/choose_truth/...）。"""
        raise NotImplementedError

    @abstractmethod
    async def fallback_action(self, seat: int) -> dict:
        """LLM 失败时的兜底合法动作（不阻塞游戏）。"""
        raise NotImplementedError

    # ── v1.1 补全接口（通用实现 + 子类实现）────
    def player_at(self, seat: int):
        """返回座位对应的玩家对象；不存在返回 None。"""
        for p in self.players:
            if p.seat == seat:
                return p
        return None

    def current_turn_seat(self) -> int | None:
        """返回当前需要行动的座次；None=轮到用户/已结束/等待阶段流转。子类实现。"""
        raise NotImplementedError

    def meta(self) -> dict:
        """游戏元信息（来自注册表）。"""
        from app.games.registry import _GAME_META
        return _GAME_META.get(self.game_type, {
            "name": self.game_type, "player_mode": self.player_mode,
            "min_players": self.min_players, "max_players": self.max_players,
            "needs_gm": self.needs_gm, "description": "",
        })

    def all_events_ordered(self) -> list[dict]:
        """全部事件按发生顺序。"""
        return list(self._events)

    # ── 持久化（基类通用实现，子类可覆盖）────
    async def load(self, db) -> None:
        """从 DB 恢复引擎：session / players / state / events。"""
        from sqlalchemy import select
        from app.models.game import GameSession, GamePlayer, GameEvent
        session = await db.get(GameSession, self.session.id)
        if session is not None:
            self.session = session
        res = await db.execute(
            select(GamePlayer).where(GamePlayer.session_id == self.session.id).order_by(GamePlayer.seat)
        )
        self.players = list(res.scalars().all())
        try:
            self.state = json.loads(self.session.state_json or "{}") or {}
        except Exception:
            self.state = {}
        evs = await db.execute(
            select(GameEvent).where(GameEvent.session_id == self.session.id).order_by(GameEvent.id)
        )
        self._events = [_event_to_dict(e) for e in evs.scalars().all()]

    async def persist_event(self, db, event: dict):
        """落一条 game_event（不 commit，由调用方统一提交）。"""
        from app.models.game import GameEvent
        row = GameEvent(
            session_id=self.session.id,
            round=int(event.get("round", self.session.round or 0)),
            phase=event.get("phase", self.session.phase or ""),
            event_type=event.get("event_type", "announce"),
            actor_seat=event.get("actor_seat"),
            target_seat=event.get("target_seat"),
            content=event.get("content", ""),
            payload_json=json.dumps(event.get("payload", {}), ensure_ascii=False),
            visibility=event.get("visibility", "public"),
            private_to_seat=event.get("private_to_seat"),
        )
        db.add(row)
        self._events.append(event)
        return row

    async def persist_state(self, db) -> None:
        """把 state / players 写回 GameSession 与 GamePlayer（不 commit）。"""
        from app.models.game import GamePlayer
        session = self.session
        session.state_json = json.dumps(self.state, ensure_ascii=False)
        session.phase = self.session.phase or ""
        session.winner_side = self.session.winner_side
        for p in self.players:
            row = await db.get(GamePlayer, p.id) if getattr(p, "id", None) else None
            if row is None:
                continue
            if getattr(p, "role", None) is not None:
                row.role = p.role
            row.alive = bool(getattr(p, "alive", True))
            row.score = int(getattr(p, "score", 0) or 0)
            priv = p.private_json
            if isinstance(priv, (dict, list)):
                row.private_json = json.dumps(priv, ensure_ascii=False)
            elif priv is not None:
                row.private_json = str(priv)
        db.add(session)

    async def finish(self, db, winner: str) -> None:
        """游戏结束：置 status/winner/finished_at（子类可覆盖补充计分）。"""
        self.session.status = "finished"
        self.session.winner_side = winner
        self.session.finished_at = datetime.now(timezone.utc)
        db.add(self.session)

    def abort_in_place(self) -> None:
        """调用方直接置 aborted（不写胜负/记忆）。"""
        self.session.status = "aborted"

    # ── 通用工具（人设/命名）────
    def _meta(self, seat: int) -> dict:
        return self.state.get("player_meta", {}).get(str(seat), {})

    def _set_meta(self, seat: int, **kw) -> None:
        pm = self.state.setdefault("player_meta", {})
        entry = pm.setdefault(str(seat), {})
        entry.update(kw)

    def name_of(self, seat: int) -> str:
        m = self._meta(seat)
        name = m.get("name")
        if name:
            return name
        p = self.player_at(seat)
        if p is None:
            return f"{seat}号"
        if p.player_type == "user":
            return "用户"
        return f"{seat}号"

    def persona_of(self, seat: int) -> dict:
        m = self._meta(seat)
        return {
            "personality": m.get("personality") or "自然",
            "chat_style": m.get("chat_style") or "口语化",
            "name": m.get("name") or self.name_of(seat),
        }

    def character_id_of(self, seat: int) -> int | None:
        return self._meta(seat).get("character_id") or getattr(self.player_at(seat), "character_id", None)

    def is_ai(self, seat: int) -> bool:
        p = self.player_at(seat)
        return p is not None and p.player_type == "ai" and not p.is_spectator

    def player_private(self, seat: int) -> dict:
        """某玩家私有信息（手牌/词语）dict，兼容列存储为 str 或 dict。"""
        p = self.player_at(seat)
        if p is None:
            return {}
        return _private_dict(p.private_json)

    def active_players(self) -> list:
        """非观战的玩家列表。"""
        return [p for p in self.players if not p.is_spectator]

    def build_player_meta(self, name_by_seat: dict[int, dict]) -> None:
        """API 创建游戏时注入玩家元信息：{seat: {name, character_id, personality, chat_style}}。"""
        self.state["player_meta"] = {str(k): v for k, v in name_by_seat.items()}


def _private_dict(v) -> dict:
    """把列的私有 JSON（str 或 dict）解析为 dict。"""
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v or "{}")
    except Exception:
        return {}


def _event_to_dict(row) -> dict:
    """GameEvent ORM 行 → dict（供视图/AI prompt 使用）。"""
    try:
        payload = json.loads(row.payload_json) if row.payload_json else {}
    except Exception:
        payload = {}
    return {
        "id": row.id,
        "round": row.round,
        "phase": row.phase,
        "event_type": row.event_type,
        "actor_seat": row.actor_seat,
        "target_seat": row.target_seat,
        "content": row.content,
        "payload": payload,
        "visibility": row.visibility,
        "private_to_seat": row.private_to_seat,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
