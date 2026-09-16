"""游戏成就与统计（#62 Phase 3，纯数据、不改关系）。

写入点统一为 :func:`record_game_result`，在三条终局路径调用：

- ``_settle_game``（正常结算，含投降导致的对局结束）→ ``aborted=False``；
- ``_abort_game``（无 draw 语义引擎的护栏止血）→ ``aborted=True``；
- ``/abort`` 解散（用户主动中止）→ ``aborted=True``。

语义（与 ``game_stats`` 列一一对应）：
- ``games_played`` 只统计完整结算（finished，含平局）的局数；
- ``aborted`` 单列无胜负终止局数——**abort 绝不计入胜场**；
- ``total_rounds`` 累计所有终局（含 aborted）的回合数；
- 投降者（``apply_surrender`` 会把它转为观战者）按 ``state["surrendered_seats"]``
  标记补记为负场，不会因转观战而被漏记；
- 幂等：本局首次写入后把哨兵写进 ``session.config_json``，重复调用直接返回；
  失败一律静默（logger.warning），绝不阻塞主链路。

成就定义在 :data:`ACHIEVEMENTS`（``game_type=None`` = 跨游戏聚合，
``game_type="*"`` 落库；其余为单游戏成就）。达成即插入一条 ``game_achievements``
记录，部分唯一索引保证「同一成就只解锁一次」。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.games.archive import _player_won
from app.models.game import GameAchievement, GameStats
from app.utils.logger import get_logger

_logger = get_logger("games.achievements")

# 哨兵键：写在 GameSession.config_json，保证同一局统计/成就只写一次。
_STATS_FLAG = "_stats_recorded"

# 成就定义。metric 必须是 GameStats 的计数列名。
ACHIEVEMENTS: list[dict] = [
    {"key": "first_game", "game_type": None, "metric": "games_played", "target": 1,
     "title": "初次登场", "description": "完整玩完第一局游戏"},
    {"key": "first_win", "game_type": None, "metric": "wins", "target": 1,
     "title": "旗开得胜", "description": "赢下第一局游戏"},
    {"key": "games_10", "game_type": None, "metric": "games_played", "target": 10,
     "title": "游戏达人", "description": "累计完整玩完 10 局游戏"},
    {"key": "wins_10", "game_type": None, "metric": "wins", "target": 10,
     "title": "常胜将军", "description": "累计赢下 10 局游戏"},
    {"key": "rounds_100", "game_type": None, "metric": "total_rounds", "target": 100,
     "title": "百战之躯", "description": "累计对局回合数达到 100"},
    {"key": "undercover_win", "game_type": "undercover", "metric": "wins", "target": 1,
     "title": "火眼金睛", "description": "在「谁是卧底」中赢下一局"},
    {"key": "werewolf_win", "game_type": "werewolf", "metric": "wins", "target": 1,
     "title": "月下求生", "description": "在「狼人杀」中赢下一局"},
    {"key": "twenty_q_win", "game_type": "twenty_q", "metric": "wins", "target": 1,
     "title": "心有灵犀", "description": "在「猜词20问」中赢下一局"},
    {"key": "liars_bar_win", "game_type": "liars_bar", "metric": "wins", "target": 1,
     "title": "千杯不醉", "description": "在「骗子酒馆」中赢下一局"},
]

# 便捷索引：key -> 定义（供查询/测试）
ACHIEVEMENT_BY_KEY = {d["key"]: d for d in ACHIEVEMENTS}

_METRICS = ("games_played", "wins", "losses", "draws", "aborted", "total_rounds")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stats_recorded(session) -> bool:
    try:
        return bool(json.loads(getattr(session, "config_json", "{}") or "{}").get(_STATS_FLAG))
    except Exception:
        return False


def _mark_stats_recorded(session) -> None:
    try:
        cfg = json.loads(getattr(session, "config_json", "{}") or "{}")
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    cfg[_STATS_FLAG] = True
    try:
        session.config_json = json.dumps(cfg, ensure_ascii=False)
    except Exception:
        pass


def _surrendered_seats(engine) -> set:
    try:
        state = getattr(engine, "state", None) or {}
        return {int(s) for s in (state.get("surrendered_seats") or [])}
    except Exception:
        return set()


async def record_game_result(db, session, engine, *, aborted: bool = False) -> None:
    """终局统计 + 成就判定（幂等、失败静默、不 commit）。

    调用方（_settle_game / _abort_game / abort 端点）随后统一 commit。
    """
    try:
        await _record(db, session, engine, aborted=aborted)
    except Exception as e:  # 统计绝不阻塞/回滚主链路
        _logger.warning("game stats record failed session=%s: %s", getattr(session, "id", None), e)


async def _record(db, session, engine, *, aborted: bool) -> None:
    if _stats_recorded(session):
        return
    game_type = str(getattr(session, "game_type", "") or "")
    rounds = int(getattr(session, "round", 0) or 0)
    owner_id = getattr(session, "user_id", None)
    surrendered = _surrendered_seats(engine)
    winner = "" if aborted else str(getattr(session, "winner_side", "") or "")

    for p in list(getattr(engine, "players", []) or []):
        if p.is_spectator and p.seat not in surrendered:
            continue
        if p.player_type == "user":
            user_id = getattr(p, "user_id", None) or owner_id
            character_id = None
        elif p.player_type == "ai":
            user_id = owner_id
            character_id = getattr(p, "character_id", None)
            if character_id is None:
                continue
        else:
            continue
        if user_id is None:
            continue

        if aborted:
            outcome = "aborted"
        elif p.seat in surrendered:
            outcome = "lost"
        elif winner == "draw":
            outcome = "draw"
        elif _player_won(p, session, engine):
            outcome = "won"
        else:
            outcome = "lost"

        row = await _get_or_create_stat(db, int(user_id), character_id, game_type)
        _apply_outcome(row, outcome, rounds)
        await db.flush()
        await _unlock_achievements(db, int(user_id), character_id, game_type, row)

    _mark_stats_recorded(session)
    db.add(session)


def _apply_outcome(row: GameStats, outcome: str, rounds: int) -> None:
    if outcome == "aborted":
        row.aborted = int(row.aborted or 0) + 1
    else:
        row.games_played = int(row.games_played or 0) + 1
        if outcome == "won":
            row.wins = int(row.wins or 0) + 1
        elif outcome == "draw":
            row.draws = int(row.draws or 0) + 1
        else:
            row.losses = int(row.losses or 0) + 1
    row.total_rounds = int(row.total_rounds or 0) + int(rounds or 0)
    row.last_played_at = _now()


async def _get_or_create_stat(db, user_id: int, character_id, game_type: str) -> GameStats:
    stmt = select(GameStats).where(
        GameStats.user_id == user_id, GameStats.game_type == game_type
    )
    if character_id is None:
        stmt = stmt.where(GameStats.character_id.is_(None))
    else:
        stmt = stmt.where(GameStats.character_id == int(character_id))
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = GameStats(
            user_id=user_id, character_id=character_id, game_type=game_type,
            games_played=0, wins=0, losses=0, draws=0, aborted=0, total_rounds=0,
        )
        db.add(row)
    return row


async def _totals(db, user_id: int, character_id) -> dict:
    """跨游戏的合计（用于 game_type=None 的聚合成就）。"""
    cols = [func.coalesce(func.sum(getattr(GameStats, m)), 0) for m in _METRICS]
    stmt = select(*cols).where(GameStats.user_id == user_id)
    stmt = stmt.where(GameStats.character_id.is_(None) if character_id is None
                      else GameStats.character_id == int(character_id))
    row = (await db.execute(stmt)).one()
    return {m: int(v or 0) for m, v in zip(_METRICS, row)}


async def _get_achievement(db, user_id: int, character_id, game_type: str, key: str):
    stmt = select(GameAchievement).where(
        GameAchievement.user_id == user_id,
        GameAchievement.game_type == game_type,
        GameAchievement.achievement_key == key,
    )
    stmt = stmt.where(GameAchievement.character_id.is_(None) if character_id is None
                      else GameAchievement.character_id == int(character_id))
    return (await db.execute(stmt)).scalar_one_or_none()


async def _unlock_achievements(db, user_id: int, character_id, game_type: str, stat_row: GameStats) -> None:
    totals = None
    for defn in ACHIEVEMENTS:
        if defn["game_type"] is None:
            if totals is None:
                totals = await _totals(db, user_id, character_id)
            value = totals.get(defn["metric"], 0)
            store_gt = "*"
        else:
            if defn["game_type"] != game_type:
                continue
            value = int(getattr(stat_row, defn["metric"], 0) or 0)
            store_gt = game_type
        if value < int(defn["target"]):
            continue
        if await _get_achievement(db, user_id, character_id, store_gt, defn["key"]) is not None:
            continue
        db.add(GameAchievement(
            user_id=user_id, character_id=character_id, game_type=store_gt,
            achievement_key=defn["key"], title=defn["title"], description=defn["description"],
            progress=value, target=int(defn["target"]), unlocked=True, unlocked_at=_now(),
        ))
        await db.flush()


# ── 查询（供 API 只读使用）──
async def list_stats(db, *, user_id: int, character_id=None, game_type: str | None = None) -> list[dict]:
    stmt = select(GameStats).where(GameStats.user_id == int(user_id))
    stmt = stmt.where(GameStats.character_id.is_(None) if character_id is None
                      else GameStats.character_id == int(character_id))
    if game_type:
        stmt = stmt.where(GameStats.game_type == str(game_type))
    stmt = stmt.order_by(GameStats.game_type)
    rows = (await db.execute(stmt)).scalars().all()
    return [_stat_dict(r) for r in rows]


def _stat_dict(r: GameStats) -> dict:
    games = int(r.games_played or 0)
    wins = int(r.wins or 0)
    return {
        "game_type": r.game_type,
        "character_id": r.character_id,
        "games_played": games,
        "wins": wins,
        "losses": int(r.losses or 0),
        "draws": int(r.draws or 0),
        "aborted": int(r.aborted or 0),
        "total_rounds": int(r.total_rounds or 0),
        "win_rate": round(wins / games, 4) if games else 0.0,
        "last_played_at": r.last_played_at.isoformat() if r.last_played_at else None,
    }


async def list_achievements(db, *, user_id: int, character_id=None) -> list[dict]:
    """返回全部成就定义 + 当前进度 + 解锁状态（未达成也返回，progress 实时算）。"""
    rows = await list_stats(db, user_id=user_id, character_id=character_id)
    per_game = {r["game_type"]: r for r in rows}
    totals = {m: sum(int(r.get(m, 0) or 0) for r in rows) for m in _METRICS}

    stmt = select(GameAchievement).where(GameAchievement.user_id == int(user_id))
    stmt = stmt.where(GameAchievement.character_id.is_(None) if character_id is None
                      else GameAchievement.character_id == int(character_id))
    unlocked = {(a.game_type, a.achievement_key): a for a in (await db.execute(stmt)).scalars().all()}

    out: list[dict] = []
    for defn in ACHIEVEMENTS:
        if defn["game_type"] is None:
            progress = totals.get(defn["metric"], 0)
            lookup = ("*", defn["key"])
            scope = "*"
        else:
            progress = int((per_game.get(defn["game_type"]) or {}).get(defn["metric"], 0) or 0)
            lookup = (defn["game_type"], defn["key"])
            scope = defn["game_type"]
        rec = unlocked.get(lookup)
        out.append({
            "key": defn["key"],
            "game_type": scope,
            "title": defn["title"],
            "description": defn["description"],
            "metric": defn["metric"],
            "target": int(defn["target"]),
            "progress": min(int(progress), int(defn["target"])),
            "unlocked": rec is not None or progress >= int(defn["target"]),
            "unlocked_at": rec.unlocked_at.isoformat() if rec and rec.unlocked_at else None,
        })
    return out
