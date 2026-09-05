"""游戏 API（群聊游戏 Phase 1）。

所有接口走 /api/v1/games 前缀。AI 回合调度 _resume_ai_turns：
- 进程内 asyncio.Lock 防重入；
- state_json 持久化 next_turn_seat（由引擎 current_turn_seat 推导）；
- 每事件单独落库（persist_event 只 add 不 commit，调用方统一 commit）；
- 只在 commit 后 sleep 1.2s；
- 直到轮到用户或结束；llm 失败 fallback 不阻塞。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.games import engine_for, list_games
from app.agent.loop import AGENT_FLAGS
from app.games.base import GameEngine
from app.games.ai_player import ai_decide
from app.games.guardrails import (
    get_guard, set_guard_mode, drop_guard, guard_before_llm, guard_after_signature,
    canonical_signature, mark_forced_advance, guard_tier, GuardMove,
)
from app.models.game import GameSession, GamePlayer, GameEvent
from app.utils.logger import get_logger

_logger = get_logger("api.games")

router = APIRouter(prefix="/api/v1/games", tags=["Games"])

# 进程内防重入锁（跨进程场景需 DB 行锁，MVP 单进程足够）
_ai_turn_locks: dict[int, asyncio.Lock] = {}

# WebSocket 游戏实时推送：session_id -> set[WebSocket]（与 chat 的 connected_clients 隔离，避免 id 撞表）
_game_ws_clients: dict[int, set[WebSocket]] = {}

# 强引用后台任务，防止 asyncio.ensure_future 创建的 Task 被 GC 提前回收（Python 官方警告）
from app.utils.async_tasks import spawn_background as _spawn_background


def _lazy_lock(session_id: int) -> asyncio.Lock:
    return _ai_turn_locks.setdefault(session_id, asyncio.Lock())


async def _broadcast_game_event(session_id: int, event: dict, phase: str) -> None:
    """向该对局在线 WS 广播游戏事件（实时刷新状态用）。"""
    for ws in list(_game_ws_clients.get(session_id, set())):
        try:
            await ws.send_json({
                "type": "game_event", "event": event, "phase": phase,
                "round": event.get("round", 0),
            })
        except Exception:
            _game_ws_clients.get(session_id, set()).discard(ws)


# ── 游戏目录 ──
@router.get("/catalog")
async def catalog(user_id: int = Depends(get_current_user_id)):
    if not AGENT_FLAGS.get("group_chat_games", False):
        return {"games": []}
    games = [g for g in list_games() if AGENT_FLAGS.get(f"game_{g['game_type']}", True)]
    return {"games": games}


# ── 创建会话 ──
@router.post("/sessions")
async def create_session(data: dict, user_id: int = Depends(get_current_user_id)):
    game_type = (data.get("game_type") or "").strip()
    try:
        engine_cls = engine_for(game_type)
    except ValueError:
        raise HTTPException(400, f"未知游戏: {game_type}")
    if not AGENT_FLAGS.get("group_chat_games", False):
        raise HTTPException(400, "群聊游戏未开启")
    if not AGENT_FLAGS.get(f"game_{game_type}", True):
        raise HTTPException(400, "该游戏未开启")
    meta = engine_cls(None).meta()
    player_ids = [int(x) for x in (data.get("player_ids") or []) if str(x).strip()]
    spectator_ids = [int(x) for x in (data.get("spectator_ids") or []) if str(x).strip()]
    user_as_player = bool(data.get("user_as_player", False))
    group_id = data.get("group_id")
    if group_id is not None:
        group_id = int(group_id)

    # 拉取 AI 角色信息（人名/人设/关系）
    all_char_ids = list(dict.fromkeys(player_ids + spectator_ids))
    char_map = await _load_char_map(all_char_ids, user_id)

    async with async_session_factory() as db:
        try:
            session, engine = await _create_session_in_db(
                db, user_id=user_id, game_type=game_type,
                player_ids=player_ids, spectator_ids=spectator_ids,
                user_as_player=user_as_player, group_id=group_id,
                trigger=data.get("trigger", "user_initiated"),
                _char_map=char_map,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))

        # 若第一个行动者是 AI，启动续跑
        ts = engine.current_turn_seat()
        if ts is not None and engine.is_ai(ts):
            _spawn_background(_resume_ai_turns(session.id))

        return {
            "ok": True,
            "session_id": session.id,
            "game_type": game_type,
            "player_mode": meta["player_mode"],
            "game": meta,
            "state": _build_state(engine, user_seat=input_seat(engine, user_id)),
        }


async def _load_char_map(char_ids: list[int], user_id: int) -> dict:
    """校验角色归属（必须是该用户角色）并返回 {id: AICharacter}。"""
    from app.models.character import AICharacter
    char_map = {}
    if char_ids:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(AICharacter).where(AICharacter.id.in_(char_ids))
            )).scalars().all()
            char_map = {c.id: c for c in rows}
    for cid in char_ids:
        c = char_map.get(cid)
        if c is None or c.user_id != user_id:
            raise HTTPException(400, f"角色 {cid} 不存在或不属于你")
    return char_map


async def _create_session_in_db(
    db: AsyncSession, *, user_id: int, game_type: str, player_ids: list[int],
    spectator_ids: list[int], user_as_player: bool, group_id: int | None,
    trigger: str, _char_map: dict | None = None,
) -> tuple[GameSession, GameEngine]:
    """共享创建逻辑：HTTP /play / 自主开局都复用。

    校验人数、分配座次（玩家 + 用户观战 + 额外观战）、注入玩家元信息、setup、
    落初始事件与状态并 commit。返回 (session, engine)。人数不足抛 ValueError（非 500）。
    """
    try:
        engine_cls = engine_for(game_type)
    except ValueError:
        raise ValueError(f"未知游戏: {game_type}")
    meta = engine_cls(None).meta()
    non_spectator = len(player_ids) + (1 if user_as_player else 0)
    if not (engine_cls.min_players <= non_spectator <= engine_cls.max_players):
        raise ValueError(
            f"{meta['name']}需 {engine_cls.min_players}-{engine_cls.max_players} 名玩家，当前 {non_spectator} 名"
        )
    if game_type in ("truth_or_dare", "twenty_q", "turtle_soup") and non_spectator != 2:
        raise ValueError(f"{meta['name']}需要恰好 2 名玩家")

    from app.models.character import AICharacter
    char_map = _char_map
    if char_map is None and (player_ids or spectator_ids):
        char_map = {}
        rows = (await db.execute(
            select(AICharacter).where(AICharacter.id.in_(list(dict.fromkeys(player_ids + spectator_ids))))
        )).scalars().all()
        char_map = {c.id: c for c in rows}
    if char_map is None:
        char_map = {}

    session = GameSession(
        user_id=user_id, group_id=group_id, game_type=game_type,
        player_mode=meta["player_mode"], status="created",
        round=0, phase="", trigger=trigger,
    )
    db.add(session)
    await db.flush()

    # 座次分配：先玩家（user 或 AI 依次），再观战者
    seats = []
    next_seat = 0

    def _add_player(p_type, char_id=None, uid=None, spectator=False):
        nonlocal next_seat
        s = next_seat
        next_seat += 1
        p = GamePlayer(
            session_id=session.id, player_type=p_type,
            user_id=uid, character_id=char_id, seat=s, is_spectator=spectator,
            alive=True, score=0, private_json="{}",
        )
        db.add(p)
        seats.append(p)
        return p

    if user_as_player:
        _add_player("user", uid=user_id)
    for cid in player_ids:
        _add_player("ai", char_id=cid)
    if not user_as_player:
        _add_player("user", uid=user_id, spectator=True)
    for cid in spectator_ids:
        _add_player("ai", char_id=cid, spectator=True)
    await db.flush()  # 先分配球员 id，persist_state 才能按 id 回写（private_json dict → 字符串）

    engine = engine_cls(session)
    name_by_seat = {}
    for p in seats:
        c = char_map.get(p.character_id)
        name_by_seat[p.seat] = {
            "name": (c.name if c else "系统"),
            "character_id": p.character_id,
            "personality": (c.personality if c else ""),
            "chat_style": (c.chat_style if c else ""),
            "relation_type": (c.relation_type if c else ""),
        }
        if p.player_type == "user" and p.character_id is None:
            name_by_seat[p.seat]["name"] = "你"
    engine.build_player_meta(name_by_seat)
    engine.players = seats

    init_events = await engine.setup()
    session.status = "playing"
    session.started_at = datetime.now(timezone.utc)
    for ev in init_events:
        await engine.persist_event(db, ev)
    await engine.persist_state(db)
    await db.commit()
    return session, engine


def input_seat(engine: GameEngine, user_id: int) -> int:
    """返回用户的座次；无则 -1。"""
    for p in engine.players:
        if p.player_type == "user" and p.user_id == user_id:
            return p.seat
    return -1


def _build_user_view(engine: GameEngine, user_seat: int) -> dict | None:
    if user_seat < 0:
        return None
    v = engine.view_for(user_seat)
    return {
        "seat": v.seat,
        "role": v.role,
        "name": v.name,
        "alive": v.alive,
        "is_spectator": v.is_spectator,
        "private": v.private,
        "public_state": v.public_state,
    }


def _build_state(engine: GameEngine, user_seat: int) -> dict:
    s = engine.session
    my = _build_user_view(engine, user_seat) if user_seat >= 0 else None
    ts = engine.current_turn_seat()
    expected = engine.expected_action(user_seat) if user_seat >= 0 and ts == user_seat else None
    archive = None
    if s.status == "finished" and s.archive_json:
        try:
            archive = json.loads(s.archive_json)
        except Exception:
            archive = None
    return {
        "session_id": s.id,
        "status": s.status,
        "game_type": s.game_type,
        "phase": s.phase,
        "round": s.round,
        "winner_side": s.winner_side,
        "current_turn_seat": ts,
        "my_turn": bool(user_seat >= 0 and ts == user_seat),
        "my_expected_action": expected,
        "my": my,
        "players": [
            {
                "seat": p.seat,
                "name": engine.name_of(p.seat),
                "role": p.role if s.status == "finished" else ("?" if p.is_spectator else p.role),
                "alive": bool(p.alive),
                "is_spectator": bool(p.is_spectator),
                "player_type": p.player_type,
                "score": int(getattr(p, "score", 0) or 0),
            }
            for p in engine.players
        ],
        "events": [
            e for e in engine.public_events_for(user_seat) if user_seat >= 0
        ] if user_seat >= 0 else engine.public_events(),
        "archive": archive,
        "game": engine.meta(),
    }


# ── 玩家动作 ──
@router.post("/sessions/{sid}/action")
async def player_action(sid: int, data: dict, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        session = await db.get(GameSession, sid)
        if session is None or session.user_id != user_id:
            raise HTTPException(404, "游戏不存在或不属于你")
        if session.status != "playing":
            raise HTTPException(400, "游戏已结束")
        engine = engine_for(session.game_type)(session)
        await engine.load(db)

        seat = int(data.get("seat", -1))
        action = (data.get("action") or "").strip()
        payload = data.get("payload") or {}
        player = engine.player_at(seat)
        if player is None or player.player_type != "user" or player.user_id != user_id or player.is_spectator:
            raise HTTPException(403, "无权以该座次行动")

        result = await engine.apply_action(seat, action, payload)
        if not result.ok:
            raise HTTPException(400, result.error or "非法动作")

        broadcast = []
        if result.event:
            await engine.persist_event(db, result.event)
            await _mirror_to_group(db, engine, session, result.event)
            broadcast.append(result.event)
        adv_events = await engine.advance()
        for ev in adv_events:
            await engine.persist_event(db, ev)
            await _mirror_to_group(db, engine, session, ev)
            broadcast.append(ev)

        winner = await engine.check_winner()
        if winner:
            await _settle_game(db, session, engine, winner)
            await db.commit()
            for ev in broadcast:
                await _broadcast_game_event(sid, ev, session.phase)
            return {"ok": True, "finished": True, "winner_side": winner}

        await engine.persist_state(db)
        await db.commit()
        for ev in broadcast:
            await _broadcast_game_event(sid, ev, session.phase)
        # 触发 AI 续跑（幂等；先响应，再异步推 AI 回合）
        _spawn_background(_resume_ai_turns(sid))
        return {"ok": True, "finished": False}


# ── 投降（仅在场玩家；单人直接输 / 双人投降方输 / 多人 3+ 转观战）──
@router.post("/sessions/{sid}/surrender")
async def surrender_session(sid: int, data: dict, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        session = await db.get(GameSession, sid)
        if session is None or session.user_id != user_id:
            raise HTTPException(404, "游戏不存在或不属于你")
        if session.status != "playing":
            raise HTTPException(400, "游戏已结束")
        engine = engine_for(session.game_type)(session)
        await engine.load(db)

        seat = int(data.get("seat", -1))
        player = engine.player_at(seat)
        # 与 player_action 同样的鉴权：必须是本人、且是在场玩家（观战者不能投降）
        if (player is None or player.player_type != "user"
                or player.user_id != user_id or player.is_spectator or not player.alive):
            raise HTTPException(403, "只有在场玩家可以投降")

        broadcast: list = []
        async with _lazy_lock(sid):  # 与 _resume_ai_turns 互斥，避免投降与 AI 续跑并发
            out = await _run_surrender(db, session, engine, seat, broadcast)
            if not out.get("ok"):
                raise HTTPException(400, out.get("error") or "投降失败")
            await db.commit()

        for ev in broadcast:
            await _broadcast_game_event(sid, ev, session.phase)

        if out.get("ended"):
            return {"ok": True, "finished": True, "winner_side": out.get("winner")}
        # 多人转观战后，若下一位是 AI，继续推进
        _spawn_background(_resume_ai_turns(sid))
        return {"ok": True, "finished": False}


# ── 游戏状态（GET 亦触发 AI 续跑）──
@router.get("/sessions/{sid}/state")
async def get_state(
    sid: int, seat: int = Query(-1), user_id: int = Depends(get_current_user_id)
):
    async with async_session_factory() as db:
        session = await db.get(GameSession, sid)
        if session is None or session.user_id != user_id:
            raise HTTPException(404, "游戏不存在或不属于你")
        engine = engine_for(session.game_type)(session)
        await engine.load(db)
        my_seat = input_seat(engine, user_id)
        if seat >= 0:
            if seat != my_seat:
                raise HTTPException(403, "只能查看自己的视图或观战视角")
            view_seat = my_seat
        else:
            view_seat = -1  # 观战视角（只公开事件）
        if session.status == "playing":
            _spawn_background(_resume_ai_turns(sid))
        return _build_state(engine, user_seat=view_seat)


# ── WebSocket 实时推送 ──
@router.websocket("/ws/{session_id}")
async def games_ws(websocket: WebSocket, session_id: int):
    """游戏实时事件推送（?token= 鉴权 + session.user_id 校验）。

    客户端连上后，_resume_ai_turns / 玩家动作 / 结算等事件以
    {"type":"game_event","event":...,"phase":...} 推送给在线连接。
    """
    from jose import jwt, JWTError
    from app.auth.config import auth_settings as _as

    token = websocket.query_params.get("token", "")
    try:
        payload = jwt.decode(token, _as.secret_key, algorithms=[_as.algorithm])
        ws_user_id = payload.get("user_id")
    except JWTError:
        ws_user_id = None
    if ws_user_id is None:
        await websocket.close(code=4401)
        return
    async with async_session_factory() as db:
        session = await db.get(GameSession, session_id)
    if session is None or session.user_id != ws_user_id:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    _game_ws_clients.setdefault(session_id, set()).add(websocket)
    try:
        # 保活：客户端可发心跳；忽略内容，只保证连接存活以检测断开
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _game_ws_clients.get(session_id, set()).discard(websocket)


# ── 中途加入观战 ──
@router.post("/sessions/{sid}/join")
async def join_session(sid: int, data: dict, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        session = await db.get(GameSession, sid)
        if session is None or session.user_id != user_id:
            raise HTTPException(404, "游戏不存在或不属于你")
        if session.status != "playing":
            raise HTTPException(400, "游戏已结束")
        char_id = data.get("character_id")
        if char_id:
            from app.models.character import AICharacter
            c = await db.get(AICharacter, int(char_id))
            if c is None or c.user_id != user_id:
                raise HTTPException(400, "角色不存在或不属于你")
            exists = (await db.execute(
                select(GamePlayer).where(GamePlayer.session_id == sid, GamePlayer.character_id == int(char_id))
            )).scalar_one_or_none()
            if exists is None:
                max_seat = (await db.execute(
                    select(GamePlayer.seat).where(GamePlayer.session_id == sid).order_by(GamePlayer.seat.desc()).limit(1)
                )).scalar_one_or_none()
                db.add(GamePlayer(
                    session_id=sid, player_type="ai", character_id=int(char_id),
                    seat=(max_seat + 1) if max_seat is not None else 0, is_spectator=True,
                    alive=True, score=0, private_json="{}",
                ))
                await db.commit()
        engine = engine_for(session.game_type)(session)
        await engine.load(db)
        return _build_state(engine, user_seat=input_seat(engine, user_id))


# ── 解散（仅创建者，不写胜负/记忆）──
@router.post("/sessions/{sid}/abort")
async def abort_session(sid: int, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        session = await db.get(GameSession, sid)
        if session is None:
            raise HTTPException(404, "游戏不存在")
        if session.user_id != user_id:
            raise HTTPException(403, "只有创建者可以解散")
        session.status = "aborted"
        session.finished_at = datetime.now(timezone.utc)
        await db.commit()
        _ai_turn_locks.pop(sid, None)  # v3.3.5 审查修复：解散后清理进程内锁
        _game_ws_clients.pop(sid, None)  # #65 审查修复：解散后清理 WS 集合，防内存缓慢增长
        drop_guard(sid)  # 🛡️ 2026-09-04：解散后清理护栏进程内状态
        return {"ok": True, "status": "aborted"}


# ── 游乐手札 ──
@router.get("/sessions/{sid}/archive")
async def get_archive(sid: int, user_id: int = Depends(get_current_user_id)):
    async with async_session_factory() as db:
        session = await db.get(GameSession, sid)
        if session is None or session.user_id != user_id:
            raise HTTPException(404, "游戏不存在或不属于你")
        if session.status != "finished":
            raise HTTPException(400, "游戏尚未结束")
        try:
            return {"archive": json.loads(session.archive_json or "{}")}
        except Exception:
            return {"archive": {}}


@router.get("/history")
async def history(
    limit: int = Query(20), game_type: str | None = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    async with async_session_factory() as db:
        q = select(GameSession).where(GameSession.user_id == user_id)
        if game_type:
            q = q.where(GameSession.game_type == game_type)
        q = q.order_by(GameSession.id.desc()).limit(max(1, min(limit, 100)))
        rows = (await db.execute(q)).scalars().all()
        items = []
        for r in rows:
            ar = {}
            if r.archive_json:
                try:
                    ar = json.loads(r.archive_json)
                except Exception:
                    ar = {}
            items.append({
                "session_id": r.id,
                "game_type": r.game_type,
                "status": r.status,
                "winner_side": r.winner_side,
                "rounds": r.round,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "archive": ar,
            })
        return {"items": items}


# ── 结算：finish + archive + memory_bridge ──
async def _abort_game(db: AsyncSession, session, engine: GameEngine, reason: str) -> None:
    """口径2（2026-09-06 拍板）：无 draw 语义引擎的护栏末级止血=无胜负终止。

    对齐 abort_in_place 语义：session 置 aborted、不写胜负、不写终局记忆、不广播 winner；
    清理进程内锁/WS/护栏状态（与 _settle_game 收尾对齐）；logger.error + obs_event 告警。
    """
    from app.memory.observability import obs_event

    engine.abort_in_place()
    db.add(session)
    _ai_turn_locks.pop(session.id, None)
    _game_ws_clients.pop(session.id, None)
    drop_guard(session.id)
    _logger.error(
        "game guard: no-draw engine aborted in place session=%d game=%s reason=%s",
        session.id, session.game_type, reason,
    )
    obs_event(None, "game_guard_no_draw_abort", {
        "session_id": session.id, "game_type": session.game_type, "reason": reason,
    })


async def _guard_stop(db: AsyncSession, session, engine: GameEngine, reason: str) -> None:
    """护栏末级止血分流（口径2，2026-09-06）：

    - 引擎支持平局语义（现状四引擎 + 基类默认）→ 维持 _settle_game(draw) 不变；
    - 引擎 has_draw_semantics=False → 无胜负终止（_abort_game：aborted、不写胜负/记忆/广播）。
    """
    if getattr(engine, "has_draw_semantics", True):
        await _settle_game(db, session, engine, "draw")
    else:
        await _abort_game(db, session, engine, reason)


async def _settle_game(db: AsyncSession, session, engine: GameEngine, winner: str) -> None:
    # B5（2026-09-01）：先 persist_state 把引擎内存态（含 liars_bar 的 private_json dict）
    # 序列化写回 ORM 脏字段——后续 finalize_game 的 SELECT 会触发 autoflush，
    # 不先序列化则 dict 绑 Text 列直接打爆事务（结算失败、对局卡 playing）。
    await engine.persist_state(db)
    await engine.finish(db, winner)
    from app.games.archive import build_archive
    from app.games.memory_bridge import finalize_game
    session.archive_json = json.dumps(build_archive(session, engine), ensure_ascii=False)
    db.add(session)
    # 主记忆摘要指针 + game_memories（每 AI 角色）
    await finalize_game(db, session, engine)
    _ai_turn_locks.pop(session.id, None)  # v3.3.5 审查修复：结算后清理进程内锁，防长期运行内存增长
    _game_ws_clients.pop(session.id, None)  # v3.3.6 审查修复：结算后清理 WS 空集合，防缓慢增长
    drop_guard(session.id)  # 🛡️ 2026-09-04：结算后清理护栏进程内状态


# ── 投降后处理：落事件 → 结束结算 或 跳过投降者回合继续（用户/AI 共用）──
async def _run_surrender(db: AsyncSession, session, engine: GameEngine,
                         seat: int, broadcast: list) -> dict:
    outcome = await engine.apply_surrender(seat)
    if not outcome.get("ok"):
        return outcome  # {"ok": False, "error": ...}

    for ev in outcome.get("events", []):
        await engine.persist_event(db, ev)
        await _mirror_to_group(db, engine, session, ev)
        broadcast.append(ev)

    if outcome.get("end"):
        winner = outcome.get("winner") or "draw"
        await _settle_game(db, session, engine, winner)
        return {"ok": True, "ended": True, "winner": winner}

    # 多人转观战：若当前回合指针正好落在投降者身上，反复 advance 跳过他（有上限，防死循环）
    guard = 0
    while engine.current_turn_seat() == seat and guard < 8:
        evs = await engine.advance()
        if not evs:
            break
        for ev in evs:
            await engine.persist_event(db, ev)
            await _mirror_to_group(db, engine, session, ev)
            broadcast.append(ev)
        winner = await engine.check_winner()
        if winner:
            await _settle_game(db, session, engine, winner)
            return {"ok": True, "ended": True, "winner": winner}
        guard += 1

    await engine.persist_state(db)
    return {"ok": True, "ended": False}


# ── AI 回合调度（幂等可续跑）──
async def _resume_ai_turns(session_id: int) -> None:
    """推进所有 AI 回合，直到轮到用户或游戏结束。可被任意入口安全重复调用。

    🛡️ 失控保护（引擎无关，进程内）：单局决策总数上限 + 同动作重复三级收敛。
    """
    lock = _lazy_lock(session_id)
    if lock.locked():
        return
    async with lock:
        try:
            async with async_session_factory() as db:
                while True:
                    session = await db.get(GameSession, session_id)
                    if session is None or session.status != "playing":
                        drop_guard(session_id)  # 🛡️ 非 playing 退出时清理
                        return
                    engine = engine_for(session.game_type)(session)
                    await engine.load(db)
                    seat = engine.current_turn_seat()
                    if seat is None or not engine.is_ai(seat):
                        return  # 轮到用户 / 已结束；guard 保留（用户下一步还要累计），终局时才清
                    guard = get_guard(session_id)
                    # 口径1（2026-09-06 拍板）：首次获取 guard 时按本局 GamePlayer 判定 ai_only
                    # 并固化（registry 跨 load 保留，不逐轮重判漂移）；判定异常保守回落 NORMAL。
                    if not guard.mode_locked:
                        try:
                            # 「全部玩家 ai」判定排除观战者：纯旁观自动对打局（user 仅 spectator）
                            # 正是空转烧钱形态，必须套 AI_ONLY；真人实际参局（非 spectator user）→ NORMAL。
                            _prow = (await db.execute(
                                select(GamePlayer.player_type, GamePlayer.is_spectator).where(
                                    GamePlayer.session_id == session_id)
                            )).all()
                            set_guard_mode(session_id, ai_only=bool(_prow) and all(
                                pt == "ai" for pt, sp in _prow if not sp))
                        except Exception:
                            set_guard_mode(session_id, ai_only=False)
                    _tier = guard_tier(guard.mode)
                    rp = (int(session.round or 0), session.phase or "")

                    # 🛡️ 闸门①：每推进一个 AI 决策都计数（无论是否花 LLM 的钱），
                    # 再据计数决定：正常调 LLM / 超软上限改走确定性 fallback（不花钱）/ 超硬上限止血。
                    # 注意：计数必须每轮都 +1，否则软上限后停止计数会永远到不了硬上限。
                    n_decided = guard.bump_decision()
                    pre = guard_before_llm(guard)
                    if pre == GuardMove.ABORT_DRAW:
                        _logger.error(
                            "game guard: session=%d mode=%s AI decisions reached %d (cap %d), stop runaway",
                            session_id, guard.mode, n_decided, _tier.max_decisions,
                        )
                        await _guard_stop(db, session, engine, reason=f"decisions_cap:{n_decided}")
                        await db.commit()
                        drop_guard(session_id)
                        return
                    if pre == GuardMove.FORCE_FALLBACK:
                        _logger.warning("game guard: session=%d mode=%s over soft limit(%d), fallback without LLM",
                                        session_id, guard.mode, n_decided)
                        decision = await engine.fallback_action(seat)
                    else:
                        decision = await ai_decide(engine, seat)  # 仅此分支产生 LLM 计费

                    payload = dict(decision.get("payload") or {})
                    if decision.get("content"):
                        payload.setdefault("content", decision["content"])

                    # 投降是与具体游戏解耦的通用动作：AI 也可发起，走与用户相同的结算管线
                    if decision.get("action") == "surrender":
                        sbc: list = []
                        out = await _run_surrender(db, session, engine, seat, sbc)
                        if not out.get("ok"):
                            _logger.warning("ai surrender rejected seat=%s -> %s", seat, out.get("error"))
                            return  # 不空转；等待下一次入口触发
                        await db.commit()
                        for ev in sbc:
                            await _broadcast_game_event(session_id, ev, session.phase)
                        if out.get("ended"):
                            _logger.info("game ended by ai surrender session=%d", session_id)
                            drop_guard(session_id)  # 🛡️ 终局清理
                            return
                        await asyncio.sleep(1.2)
                        continue

                    # 🛡️ 闸门②：结构化重复检测（排除 content 自然语言）
                    sig = canonical_signature(
                        session.round, session.phase, seat,
                        decision.get("action", ""), payload,
                    )
                    move = guard_after_signature(guard, sig, rp)

                    if move == GuardMove.ABORT_DRAW:
                        _logger.error(
                            "game guard: session=%d mode=%s stuck at rp=%s sig streak=%d",
                            session_id, guard.mode, rp, guard.streak,
                        )
                        await _guard_stop(db, session, engine, reason=f"streak:{guard.streak}@{rp}")
                        await db.commit()
                        drop_guard(session_id)
                        return

                    if move == GuardMove.FORCE_ADVANCE:
                        # 换目标也救不回来：确定性强推阶段（timeout 内部=fallback+advance），不调 LLM
                        _logger.warning("game guard: force-advance session=%d rp=%s", session_id, rp)
                        mark_forced_advance(guard, rp)
                        adv = await engine.timeout()
                        broadcast = []
                        for ev in adv:
                            await engine.persist_event(db, ev)
                            await _mirror_to_group(db, engine, session, ev)
                            broadcast.append(ev)
                        winner = await engine.check_winner()
                        if winner:
                            await _settle_game(db, session, engine, winner)
                            await db.commit()
                            for ev in broadcast:
                                await _broadcast_game_event(session_id, ev, session.phase)
                            drop_guard(session_id)
                            return
                        await engine.persist_state(db)
                        await db.commit()
                        for ev in broadcast:
                            await _broadcast_game_event(session_id, ev, session.phase)
                        await asyncio.sleep(1.2)
                        continue

                    if move == GuardMove.FORCE_FALLBACK:
                        # 丢弃重复的 LLM 决策，强制换一个合法动作（如狼人换刀）
                        _logger.warning("game guard: force-fallback session=%d rp=%s seat=%d", session_id, rp, seat)
                        decision = await engine.fallback_action(seat)
                        payload = dict(decision.get("payload") or {})
                        if decision.get("content"):
                            payload.setdefault("content", decision["content"])

                    # P0 双重 apply 修复：校验与 apply 统一由调度方负责。先 apply 一次；
                    # result 不 ok 或抛异常时用 fallback_action 生成兜底 decision 再 apply 一次。
                    try:
                        result = await engine.apply_action(seat, decision.get("action", ""), payload)
                    except Exception:
                        result = None
                    if result is None or not result.ok:
                        _logger.info("ai apply rejected seat=%d, fallback_action", seat)
                        fb = await engine.fallback_action(seat)
                        if fb:
                            payload = dict(fb.get("payload") or {})
                            if fb.get("content"):
                                payload.setdefault("content", fb["content"])
                            try:
                                result = await engine.apply_action(seat, fb.get("action", ""), payload)
                            except Exception:
                                result = None
                    if result is None or not result.ok:
                        _logger.warning("ai apply still failed seat=%d, aborting resume", seat)
                        return
                    broadcast = []
                    if result.event:
                        await engine.persist_event(db, result.event)
                        await _mirror_to_group(db, engine, session, result.event)
                        broadcast.append(result.event)
                    adv_events = await engine.advance()
                    for ev in adv_events:
                        await engine.persist_event(db, ev)
                        await _mirror_to_group(db, engine, session, ev)
                        broadcast.append(ev)
                    winner = await engine.check_winner()
                    if winner:
                        await _settle_game(db, session, engine, winner)
                        await db.commit()
                        for ev in broadcast:
                            await _broadcast_game_event(session_id, ev, session.phase)
                        _logger.info("game finished session=%d winner=%s", session_id, winner)
                        drop_guard(session_id)  # 🛡️ 终局清理
                        return
                    await engine.persist_state(db)
                    await db.commit()
                    for ev in broadcast:
                        await _broadcast_game_event(session_id, ev, session.phase)
                    await asyncio.sleep(1.2)  # 只在提交后 sleep，避免长事务
        except Exception as e:
            _logger.error("_resume_ai_turns failed session=%d: %s", session_id, e, exc_info=True)


async def resume_stuck_games() -> None:
    """v3.3.5 审查修复：低频恢复 playing 但 10 分钟以上无新事件的对局（服务器重启/断线兜底）。"""
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(GameSession.id).where(GameSession.status == "playing")
            )).scalars().all()
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        for sid in rows:
            async with async_session_factory() as db2:
                session = await db2.get(GameSession, sid)
                if session is None or session.status != "playing":
                    continue
                last_ev = (await db2.execute(
                    select(GameEvent.created_at).where(GameEvent.session_id == sid)
                    .order_by(GameEvent.id.desc()).limit(1)
                )).scalar_one_or_none()
                if last_ev is not None and last_ev >= cutoff:
                    continue
                engine = engine_for(session.game_type)(session)
                await engine.load(db2)
                seat = engine.current_turn_seat()
                if seat is not None and engine.is_ai(seat):
                    _spawn_background(_resume_ai_turns(sid))
                elif seat is None:
                    # B-RCV（2026-09-01 审查）：回合指针为空但 status=playing 的异常态——
                    # 恢复逻辑只能覆盖 AI 轮，此处告警便于日志/真机发现后手动处理。
                    _logger.warning("stuck game sid=%s type=%s has no current turn seat", sid, session.game_type)
    except Exception as e:
        _logger.warning("resume_stuck_games failed: %s", e)


async def _mirror_to_group(db: AsyncSession, engine: GameEngine, session, event: dict) -> None:
    """把游戏事件镜像到群消息表（msg_type=game_say/game_event），不进群记忆。"""
    if not session.group_id:
        return
    content = (event.get("content") or "").strip()
    if not content:
        return
    actor_seat = event.get("actor_seat")
    cid = None
    sender_type = "ai"
    if actor_seat is not None:
        p = engine.player_at(actor_seat)
        if p is not None:
            cid = p.character_id
            sender_type = "user" if p.player_type == "user" else "ai"
    msg_type = "game_event" if actor_seat is None else "game_say"
    from app.models.chat import ChatGroupMessage
    db.add(ChatGroupMessage(
        group_id=session.group_id, sender_type=sender_type, character_id=cid,
        content=content[:200], msg_type=msg_type, game_session_id=session.id,
    ))
