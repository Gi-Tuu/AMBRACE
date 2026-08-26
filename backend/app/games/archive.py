"""游乐手札：游戏完整过程折叠为结构化卡片数据。

纯函数，零 LLM——前端拿到 JSON 直接渲染。
"""
from __future__ import annotations

from app.games.base import GameEngine


def _player_won(player, session, engine: GameEngine) -> bool:
    """判定某玩家是否获胜（纯规则，零 LLM）。

    - 多人（谁是卧底）：winner_side=civilians/undercover，按角色归属判定；
    - 双人/单人：winner_side=f"seat_{seat}"，直接对比本座次。
    """
    ws = session.winner_side
    if ws in ("civilians", "undercover"):
        role = player.role
        return (ws == "civilians" and role == "civilian") or (ws == "undercover" and role == "undercover")
    if ws == "villagers":
        return player.role in ("villager", "seer")
    if ws == "werewolves":
        return player.role == "wolf"
    if ws in ("guesser", "thinker"):
        return player.role == ws
    return ws == f"seat_{player.seat}"


def build_archive(session, engine: GameEngine) -> dict:
    players = []
    for p in engine.players:
        players.append({
            "seat": p.seat,
            "name": engine.name_of(p.seat),
            "player_type": p.player_type,
            "role": p.role,  # 结算后公开
            "alive": bool(p.alive),
            "result": "won" if _player_won(p, session, engine) else "lost",
            "score": int(getattr(p, "score", 0) or 0),
        })
    timeline = []
    for ev in engine.all_events_ordered():
        timeline.append({
            "round": ev.get("round", 0),
            "phase": ev.get("phase", ""),
            "type": ev.get("event_type", ""),
            "actor": ev.get("actor_seat"),
            "target": ev.get("target_seat"),
            "content": ev.get("content", ""),
            "ts": ev.get("created_at"),
        })
    started = session.started_at
    finished = session.finished_at
    duration = None
    if started and finished:
        try:
            duration = int((finished - started).total_seconds())
        except Exception:
            duration = None
    return {
        "game_type": session.game_type,
        "game_name": engine.meta()["name"],
        "player_mode": session.player_mode,
        "player_count": len([p for p in engine.players if not p.is_spectator]),
        "spectator_count": len([p for p in engine.players if p.is_spectator]),
        "players": players,
        "winner_side": session.winner_side,
        "rounds": session.round,
        "trigger": session.trigger,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
        "duration_seconds": duration,
        "timeline": timeline,
        "mvp": _mvp(engine, session),
    }


def _mvp(engine: GameEngine, session) -> dict | None:
    """简单 MVP：胜方中存活局数/得分最多的玩家（纯规则，零 LLM）。"""
    winners = [p for p in engine.players if _player_won(p, session, engine) and not p.is_spectator]
    if not winners:
        return None
    best = max(winners, key=lambda p: (int(getattr(p, "score", 0) or 0), getattr(p, "alive", False)))
    return {"seat": best.seat, "name": engine.name_of(best.seat)}
