"""游戏 API（群聊游戏 Phase 1）。

所有接口走 /api/v1/games 前缀。AI 回合调度 _resume_ai_turns：
- 进程内 asyncio.Lock 防重入；
- state_json 持久化 next_turn_seat（由引擎 current_turn_seat 推导）；
- 每事件单独落库（persist_event 只 add 不 commit，调用方统一 commit）；
- 只在 commit 后 sleep 1.2s；
- 直到轮到用户或结束；llm 失败 fallback 不阻塞；
- P2-1：LLM 决策 + fallback 双重 apply 都失败时按 guardrails.apply_failures 分级收敛
  （确定性推进 → 硬强推 → 止血终局），绝不静默 return 卡死。
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
    APPLY_FAIL_FORCE_LIMIT, APPLY_FAIL_ABORT_LIMIT,
)
from app.models.game import GameSession, GamePlayer, GameEvent
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

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
        # 账号独立 P1（09-19 审计修正）：群归属校验——旧写法不校验 group_id 归属，
        # 可把对局挂到别家群，事件经 _mirror_to_group 写进他人群并被其 LLM prompt 读到。
        from app.models.chat import ChatGroup
        from app.application.tenant_service import tenant_scope_ids
        async with async_session_factory() as _gdb:
            _owned_group = (await _gdb.execute(
                select(ChatGroup.id).where(
                    ChatGroup.id == group_id,
                    ChatGroup.user_id.in_(await tenant_scope_ids(_gdb, user_id)),
                )
            )).scalar_one_or_none()
        if _owned_group is None:
            raise HTTPException(404, "群聊不存在")

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
    # #62 Phase 3：载入用户自定义词库/题库（用户自定义 > 插件内容包 > 内置常量）。
    await engine.load_content(db)
    # P3-⑤（2026-09-19）：创建路径不走 load()，此处显式解析一次创建者语言，
    # 保证 setup() 首轮播报 / 匿名名回落用 User.lang，而不是回落 settings.default_lang。
    await engine.resolve_lang(db)

    init_events = await engine.setup()
    session.status = "playing"
    session.started_at = now_naive_utc()
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
        # 故障注入专项（2026-09-19）：玩家路径的 engine.advance() 异常同样必须止血——
        # 旧代码是裸调用，引擎抛错时请求直接 500、对局停在 playing（前端无限等待）。
        # 对齐 AI 回合路径口径：log/obs → _guard_stop_visible 落用户可见结束事件并终局。
        try:
            adv_events = await engine.advance()
        except Exception as e:
            _logger.error("game guard: player advance raised session=%d seat=%d action=%s: %s",
                          sid, seat, action, e, exc_info=True)
            from app.memory.observability import obs_event
            obs_event(None, "game_guard_player_advance_error", {
                "session_id": sid, "seat": seat, "action": action, "error": str(e)[:200],
            })
            draws = getattr(engine, "has_draw_semantics", True)
            try:
                await _guard_stop_visible(
                    db, session, engine, sid,
                    reason=f"player_advance_error@{seat}",
                    reason_tag="player_advance_error",
                    payload={"seat": seat, "action": action, "error": str(e)[:200]},
                    content=("⚠️ 对局推进异常，已自动结束并按平局结算。"
                             if draws else "⚠️ 对局推进异常，已自动终止。"),
                )
            except Exception as ge:
                # 止血自身失败不吞：先留痕再原样冒泡（显式 500 好过假装成功）。
                _logger.error("game guard: player advance stop failed session=%d: %s",
                              sid, ge, exc_info=True)
                raise
            return {"ok": True, "finished": True, "aborted": True, "winner_side": None}
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
            # P3-9：轮询频繁，只在「确有 AI 待行动 + 无续跑协程在跑」时才 spawn，
            # 等待玩家动作 / 无需推进的局直接返回，避免反复创建立即退出的空转协程。
            pending_seat = engine.current_turn_seat()
            if pending_seat is not None and engine.is_ai(pending_seat) and not _lazy_lock(sid).locked():
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
        was_playing = session.status == "playing"
        session.status = "aborted"
        session.finished_at = now_naive_utc()
        # #62 Phase 3：用户主动解散也走同一统计口径（aborted 单列，不计胜场）。
        if was_playing:
            try:
                from app.games.achievements import record_game_result
                engine = engine_for(session.game_type)(session)
                await engine.load(db)
                await record_game_result(db, session, engine, aborted=True)
            except Exception as e:  # 统计失败静默，绝不阻塞解散
                _logger.warning("abort stats record failed sid=%s: %s", sid, e)
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


# ── 内容源（#62 Phase 3：自定义词库/题库；用户自定义 > 插件内容包 > 内置常量）──
@router.get("/content")
async def get_content(game_type: str = Query(...), user_id: int = Depends(get_current_user_id)):
    """列出某游戏当前生效的内容（按 key，标注 source=user/plugin/builtin）。"""
    game_type = (game_type or "").strip()
    try:
        engine_for(game_type)
    except ValueError:
        raise HTTPException(400, f"未知游戏: {game_type}")
    from app.games import content_store
    async with async_session_factory() as db:
        items = await content_store.list_effective(db, user_id=user_id, game_type=game_type)
    return {"game_type": game_type, "items": items}


@router.put("/content")
async def put_content(data: dict, user_id: int = Depends(get_current_user_id)):
    """写入/覆盖某游戏某 key 的用户自定义内容（整段替换该 key 的生效内容）。"""
    game_type = (data.get("game_type") or "").strip()
    key = (data.get("key") or "").strip()
    values = data.get("values")
    try:
        engine_for(game_type)
    except ValueError:
        raise HTTPException(400, f"未知游戏: {game_type}")
    from app.games import content_store
    err = content_store.validate_write(game_type, key, values)
    if err:
        raise HTTPException(400, err)
    async with async_session_factory() as db:
        await content_store.upsert_user_override(
            db, user_id=user_id, game_type=game_type, key=key, values=values
        )
        await db.commit()
    return {"ok": True, "game_type": game_type, "key": key, "count": len(values)}


@router.delete("/content/{game_type}/{key}")
async def delete_content(game_type: str, key: str, user_id: int = Depends(get_current_user_id)):
    """删除用户自定义内容（回落插件内容包 / 内置常量）。"""
    game_type = (game_type or "").strip()
    key = (key or "").strip()
    from app.games import content_store
    if not content_store.game_type_re.match(game_type) or not content_store.content_key_re.match(key):
        raise HTTPException(400, "game_type/key 非法")
    async with async_session_factory() as db:
        removed = await content_store.delete_user_override(
            db, user_id=user_id, game_type=game_type, key=key
        )
        await db.commit()
    return {"ok": True, "removed": removed}


# ── 成就与统计（#62 Phase 3，纯数据只读查询）──
async def _check_char_owned(db: AsyncSession, character_id: int, user_id: int) -> None:
    from app.models.character import AICharacter
    c = await db.get(AICharacter, int(character_id))
    if c is None or c.user_id != user_id:
        raise HTTPException(404, "角色不存在或不属于你")


@router.get("/stats")
async def game_stats(
    character_id: int | None = Query(None), game_type: str | None = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    """查询某用户（或某角色）的游戏统计累计。"""
    from app.games import achievements
    async with async_session_factory() as db:
        if character_id is not None:
            await _check_char_owned(db, character_id, user_id)
        items = await achievements.list_stats(
            db, user_id=user_id, character_id=character_id, game_type=game_type
        )
    return {"items": items}


@router.get("/achievements")
async def game_achievements(
    character_id: int | None = Query(None), user_id: int = Depends(get_current_user_id)
):
    """查询某用户（或某角色）的成就（含未达成进度与解锁时间）。"""
    from app.games import achievements
    async with async_session_factory() as db:
        if character_id is not None:
            await _check_char_owned(db, character_id, user_id)
        items = await achievements.list_achievements(db, user_id=user_id, character_id=character_id)
    return {"items": items}


# ── 结算：finish + archive + memory_bridge ──
async def _abort_game(db: AsyncSession, session, engine: GameEngine, reason: str) -> None:
    """口径2（2026-09-06 拍板）：无 draw 语义引擎的护栏末级止血=无胜负终止。

    对齐 abort_in_place 语义：session 置 aborted、不写胜负、不写终局记忆、不广播 winner；
    清理进程内锁/WS/护栏状态（与 _settle_game 收尾对齐）；logger.error + obs_event 告警。
    """
    from app.memory.observability import obs_event

    sid = session.id
    engine.abort_in_place()
    db.add(session)
    # 🛡️ 2026-09-19（终局持久化解耦）：无胜负终止的终态先独立 commit——记账（成就/统计）
    # 失败不得回滚终态，否则 status 永远 playing 而结束事件已落库 → 二次止血 → 重复结束事件。
    await db.commit()
    # #62 Phase 3：无胜负终止也记账（aborted 单列，绝不计胜场）；幂等、失败静默。
    try:
        from app.games.achievements import record_game_result
        await record_game_result(db, session, engine, aborted=True)
        await db.commit()
    except Exception:
        _logger.error("game abort: record_game_result failed session=%s", sid, exc_info=True)
        await db.rollback()
        await db.refresh(session)
    _ai_turn_locks.pop(sid, None)
    _game_ws_clients.pop(sid, None)
    drop_guard(sid)
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
    sid = session.id
    await engine.persist_state(db)
    await engine.finish(db, winner)
    # 🛡️ 2026-09-19（终局持久化解耦）：终态（status/winner/finished_at）与增强步骤
    # （archive/finalize/记账）解耦——finish 后立刻独立 commit。否则增强步骤任一确定性抛错，
    # 调用方末尾的 commit 不执行 → finish 的终态随会话退出 rollback（status 永远 playing），
    # 而 _guard_stop_visible 已把 end_ev 落库 → 二次止血 → 重复结束事件（每 10 分钟再堆一条）。
    # 增强步骤各自容错：失败只 error + rollback（只回滚该步骤自身），绝不动已提交的终态。
    await db.commit()

    from app.games.archive import build_archive
    try:
        session.archive_json = json.dumps(build_archive(session, engine), ensure_ascii=False)
        db.add(session)
        await db.commit()
    except Exception:
        _logger.error("game settle: archive build/write failed session=%s", sid, exc_info=True)
        await db.rollback()
        await db.refresh(session)  # rollback 会 expire ORM 对象，后续步骤读字段前先重载

    try:
        # 主记忆摘要指针 + game_memories（每 AI 角色）
        from app.games.memory_bridge import finalize_game
        await finalize_game(db, session, engine)
        await db.commit()
    except Exception:
        _logger.error("game settle: finalize_game failed session=%s", sid, exc_info=True)
        await db.rollback()
        await db.refresh(session)

    try:
        # #62 Phase 3：终局统计 + 成就判定（幂等、失败静默、不阻塞主链路）
        from app.games.achievements import record_game_result
        await record_game_result(db, session, engine, aborted=False)
        await db.commit()
    except Exception:
        _logger.error("game settle: record_game_result failed session=%s", sid, exc_info=True)
        await db.rollback()
        await db.refresh(session)

    _ai_turn_locks.pop(sid, None)  # v3.3.5 审查修复：结算后清理进程内锁，防长期运行内存增长
    _game_ws_clients.pop(sid, None)  # v3.3.6 审查修复：结算后清理 WS 空集合，防缓慢增长
    drop_guard(sid)  # 🛡️ 2026-09-04：结算后清理护栏进程内状态


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


# ── P2-1（2026-09-18）：双重 apply 都失败的收敛（沿用 guardrails 计数，不新造机制）──
# 背景：LLM 决策 apply 失败 + fallback_action 再 apply 仍失败时，旧代码只 warning 后静默
# return——不落库、不推进、不累计 guard、不广播，对局停在 AI 回合；resume_stuck_games 只会
# 每 10 分钟空转重 spawn。这里改为按 SessionGuard.apply_failures 分级：确定性推进 → 硬强推
# → 末级止血，且任何分支都留可见痕迹（日志 + 落库/广播）。
async def _apply_fail_force_push(db: AsyncSession, session, engine: GameEngine,
                                 session_id: int, seat: int, n_fail: int) -> bool:
    """「双重 apply 都失败」未到止血阈值时的确定性推进（零 LLM、不依赖 fallback 合法性）。

    - n_fail < APPLY_FAIL_FORCE_LIMIT：阶段级确定性推进 engine.advance()（丢弃本轮 AI 动作）；
    - n_fail >= APPLY_FAIL_FORCE_LIMIT：硬强推 engine.timeout()（跳过本轮动作 + 推进；其内部
      fallback 是否合法不影响阶段推进本身）。

    事件落库/镜像/广播 + persist_state 后返回 False（调用方 continue，计数继续累计）；
    若强推过程中分出胜负则走 _settle_game 终局并返回 True。引擎完全推不动时（advance/timeout
    空且回合指针不动）本轮也无副作用，但计数每轮 +1，必然在有限轮内到达 ABORT 阈值。
    """
    hard = n_fail >= APPLY_FAIL_FORCE_LIMIT
    _logger.error(
        "game guard: ai apply failed twice session=%d seat=%d apply_failures=%d -> %s",
        session_id, seat, n_fail, "force-timeout" if hard else "advance",
    )
    from app.memory.observability import obs_event
    obs_event(None, "game_guard_apply_fail_push", {
        "session_id": session_id, "seat": seat, "apply_failures": n_fail,
        "move": "timeout" if hard else "advance",
    })
    events = []
    rp_before = (int(session.round or 0), session.phase or "")
    pushed = False
    try:
        events = await (engine.timeout() if hard else engine.advance())
        pushed = True
    except Exception as e:
        # 引擎在异常态下抛错也不能再静默退出：本轮无事件，计数仍 +1 → 有限轮内到 ABORT。
        _logger.error("game guard: apply-fail push raised session=%d n_fail=%d: %s",
                      session_id, n_fail, e, exc_info=True)
    # P3-②（2026-09-19）：确定性推进后 (round, phase) 真的移动 → 引擎已真实推进，连续失败
    # 序列成为过去式，归零 apply_failures。否则该计数会跨用户回合一直累计，用户下次入口刚
    # 失败一次就可能直接触达止血阈值（误止血）。
    if pushed and (int(session.round or 0), session.phase or "") != rp_before:
        get_guard(session_id).reset_apply_failures()
    broadcast = list(events)
    for ev in events:
        await engine.persist_event(db, ev)
        await _mirror_to_group(db, engine, session, ev)
    winner = await engine.check_winner()
    if winner:
        await _settle_game(db, session, engine, winner)
    else:
        await engine.persist_state(db)
    await db.commit()
    for ev in broadcast:
        await _broadcast_game_event(session_id, ev, session.phase)
    if winner:
        _logger.info("game finished by apply-fail push session=%d winner=%s", session_id, winner)
        drop_guard(session_id)
        return True
    return False


def _as_decision_dict(value) -> dict:
    """归一化 AI / 兜底决策：非 dict（None / list / str 等坏输出）一律视为空决策。

    故障注入专项（2026-09-18）：ai_decide 或 fallback_action 返回 None 时，旧调度层直接
    ``decision.get(...)`` 抛 AttributeError → 异常退出、对局停在 playing。空决策会走到
    apply 失败 → 引擎兜底 → guardrails 收敛，绝不会静默卡死。
    """
    return value if isinstance(value, dict) else {}


async def _guard_stop_visible(db: AsyncSession, session, engine: GameEngine, session_id: int,
                              *, reason: str, reason_tag: str, payload: dict | None = None,
                              content: str | None = None) -> dict:
    """护栏末级止血 + 用户可见结束事件（落库 + 群镜像 + WS 广播）。

    故障注入专项（2026-09-18）：止血终局必须有用户可见痕迹——旧 decisions_cap / streak 两条止血分支
    只 _guard_stop 不落事件，前端拿不到结束信号（只能干等）。这里与 _apply_fail_abort 同口径
    统一补上：先落 phase=result 公开事件，再 _guard_stop 分流（有平局语义 → _settle_game(draw)；
    无 → _abort_game），最后广播。返回结束事件 dict。引擎/DB 语义零改动，只加"可见化"。
    """
    draws = getattr(engine, "has_draw_semantics", True)
    end_ev = {
        "event_type": "win" if draws else "announce",
        "phase": "result",
        "visibility": "public",
        "content": content or ("⚠️ 对局长时间无法推进，已自动结束并按平局结算。"
                               if draws else "⚠️ 对局长时间无法推进，已自动终止。"),
        "payload": {"reason": reason_tag, **(payload or {}),
                    "winner_side": "draw" if draws else None},
    }
    from app.memory.observability import obs_event
    obs_event(None, "game_guard_stop", {
        "session_id": session_id, "reason": reason_tag, "draw_semantics": bool(draws),
    })
    # 🛡️ 2026-09-19（幂等去重）：同一 session 已有 phase="result" 结束事件时不再重复落库/镜像/
    # commit——覆盖「首次止血终局未持久化（增强步骤抛错）→ 二次止血」叠加出的重复结束事件，
    # 以及 resume_stuck 每 10 分钟重试的堆积。事件不重复落，但广播照旧（前端仍能收到结束信号）。
    dup = (await db.execute(
        select(GameEvent.id).where(
            GameEvent.session_id == session_id,
            GameEvent.phase == "result",
        ).limit(1)
    )).scalar_one_or_none()
    if dup is None:
        await engine.persist_event(db, end_ev)
        await _mirror_to_group(db, engine, session, end_ev)
        await db.commit()
    else:
        _logger.warning(
            "game guard: session=%d already has phase=result event, skip duplicate end event",
            session_id,
        )
    try:
        await _guard_stop(db, session, engine, reason=reason)
        await db.commit()
    finally:
        # 无论止血是否成功，都把"用户可见结束事件"推给在线连接，消除前端无限等待
        await _broadcast_game_event(session_id, end_ev, "result")
    return end_ev


async def _apply_fail_abort(db: AsyncSession, session, engine: GameEngine,
                            session_id: int, seat: int, n_fail: int) -> None:
    """连续「双重 apply 都失败」达 APPLY_FAIL_ABORT_LIMIT：止血终局 + 用户可见结束事件。

    payload 带 reason/apply_failures，作为 guardrails 计数的持久化终局记录。
    """
    draws = getattr(engine, "has_draw_semantics", True)
    from app.memory.observability import obs_event
    obs_event(None, "game_guard_apply_fail_abort", {
        "session_id": session_id, "seat": seat, "apply_failures": n_fail,
        "draw_semantics": bool(draws),
    })
    await _guard_stop_visible(
        db, session, engine, session_id,
        reason=f"apply_fail:{n_fail}@{seat}",
        reason_tag="apply_fail_abort",
        payload={"apply_failures": n_fail, "seat": seat},
        content=("⚠️ 对局因 AI 行动连续失败已自动结束，按平局结算。"
                 if draws else "⚠️ 对局因 AI 行动连续失败已自动终止。"),
    )
    drop_guard(session_id)
    _logger.error(
        "game guard: session=%d seat=%d stopped after %d consecutive double-apply failures",
        session_id, seat, n_fail,
    )


async def _emergency_stop_after_crash(session_id: int, exc: Exception) -> None:
    """故障注入专项（2026-09-18）：_resume_ai_turns 未预期异常后的止血网。

    背景：引擎方法（advance/timeout/check_winner/...）抛错时旧代码只 log 后经 finally 清 guard，
    session 仍是 playing、无任何事件 → 前端无限等待 + resume_stuck 每 10 分钟空转重 spawn。
    这里用**独立 DB 会话**把仍在 playing 的对局止血终局并广播可见结束事件；幂等、自身异常静默
    （绝不掩盖原始异常，也不再让对局卡死）。
    """
    err = f"{type(exc).__name__}: {exc}"[:200]
    try:
        async with async_session_factory() as db:
            session = await db.get(GameSession, session_id)
            if session is None or session.status != "playing":
                return
            engine = engine_for(session.game_type)(session)
            await engine.load(db)
            from app.memory.observability import obs_event
            obs_event(None, "game_guard_crash_abort", {
                "session_id": session_id, "game_type": session.game_type, "error": err,
            })
            draws = getattr(engine, "has_draw_semantics", True)
            await _guard_stop_visible(
                db, session, engine, session_id,
                reason=f"resume_crash:{type(exc).__name__}",
                reason_tag="resume_crash",
                payload={"error": err},
                content=("⚠️ 对局因系统异常已自动结束，按平局结算。"
                         if draws else "⚠️ 对局因系统异常已自动终止。"),
            )
            _logger.error("game guard: session=%d stopped after unhandled resume error", session_id)
    except Exception as e2:
        _logger.error("game guard: emergency stop failed session=%d: %s", session_id, e2, exc_info=True)
    finally:
        drop_guard(session_id)


# ── AI 回合调度（幂等可续跑）──
async def _resume_ai_turns(session_id: int) -> None:
    """推进所有 AI 回合，直到轮到用户或游戏结束。可被任意入口安全重复调用。

    🛡️ 失控保护（引擎无关，进程内）：单局决策总数上限 + 同动作重复三级收敛。
    """
    lock = _lazy_lock(session_id)
    if lock.locked():
        return
    async with lock:
        # P3-2（2026-09-18）：最外层 try/finally，保证异常终局 / session 已非 playing 退出时清理
        # 模块级 guardrails._REGISTRY[session_id]，避免长期运行缓慢泄漏。
        # 硬约束：正常「轮到用户、对局继续」的返回路径（_user_continues=True）绝对不清理 guard
        # —— 该路径下 session 仍为 playing，用户下一步还要继续累计 streak/decisions。
        _user_continues = False
        try:
            async with async_session_factory() as db:
                while True:
                    session = await db.get(GameSession, session_id)
                    if session is None or session.status != "playing":
                        return  # 非 playing 退出：finally 清理 guard
                    engine = engine_for(session.game_type)(session)
                    await engine.load(db)
                    seat = engine.current_turn_seat()
                    if seat is None or not engine.is_ai(seat):
                        _user_continues = True  # 轮到用户 / 已结束；guard 保留（用户下一步还要累计）
                        return
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
                        # 故障注入专项：止血必须落"用户可见结束事件"并广播，否则前端只能干等。
                        await _guard_stop_visible(
                            db, session, engine, session_id,
                            reason=f"decisions_cap:{n_decided}", reason_tag="decisions_cap",
                            payload={"decisions": n_decided, "cap": _tier.max_decisions},
                        )
                        drop_guard(session_id)
                        return
                    if pre == GuardMove.FORCE_FALLBACK:
                        _logger.warning("game guard: session=%d mode=%s over soft limit(%d), fallback without LLM",
                                        session_id, guard.mode, n_decided)
                        decision = _as_decision_dict(await engine.fallback_action(seat))
                    else:
                        decision = _as_decision_dict(await ai_decide(engine, seat))  # 仅此分支产生 LLM 计费

                    payload = dict(decision.get("payload") or {})
                    if decision.get("content"):
                        payload.setdefault("content", decision["content"])

                    # 投降是与具体游戏解耦的通用动作：AI 也可发起，走与用户相同的结算管线
                    if decision.get("action") == "surrender":
                        sbc: list = []
                        out = await _run_surrender(db, session, engine, seat, sbc)
                        if not out.get("ok"):
                            # P3-④（2026-09-19）：投降被拒不再 return（旧行为会留 10 分钟空窗，
                            # 直到 resume_stuck_games 重捞）。回落引擎兜底动作，继续走本轮统一的
                            # apply 管线——失败则由 guardrails 分级收敛，绝不空转。
                            _logger.warning(
                                "ai surrender rejected seat=%s -> %s, fallback to engine action",
                                seat, out.get("error"),
                            )
                            decision = _as_decision_dict(await engine.fallback_action(seat))
                            payload = dict(decision.get("payload") or {})
                            if decision.get("content"):
                                payload.setdefault("content", decision["content"])
                        else:
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
                        # 故障注入专项：止血落可见结束事件 + 广播（前端不再无限等待）。
                        await _guard_stop_visible(
                            db, session, engine, session_id,
                            reason=f"streak:{guard.streak}@{rp}", reason_tag="streak_abort",
                            payload={"streak": guard.streak, "round": rp[0], "phase": rp[1]},
                        )
                        drop_guard(session_id)
                        return

                    if move == GuardMove.FORCE_ADVANCE:
                        # 换目标也救不回来：确定性强推阶段（timeout 内部=fallback+advance），不调 LLM
                        _logger.warning("game guard: force-advance session=%d rp=%s", session_id, rp)
                        mark_forced_advance(guard, rp)
                        try:
                            adv = await engine.timeout()
                        except Exception as e:
                            # 故障注入专项：timeout 抛错不能让本轮直接崩出（否则停在 playing）；
                            # 本轮无事件，streak/decisions 仍继续累计 → 有限轮内到 ABORT。
                            _logger.error("game guard: force-advance timeout raised session=%d rp=%s: %s",
                                          session_id, rp, e, exc_info=True)
                            adv = []
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
                        decision = _as_decision_dict(await engine.fallback_action(seat))
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
                        fb = _as_decision_dict(await engine.fallback_action(seat))
                        if fb:
                            payload = dict(fb.get("payload") or {})
                            if fb.get("content"):
                                payload.setdefault("content", fb["content"])
                            try:
                                result = await engine.apply_action(seat, fb.get("action", ""), payload)
                            except Exception:
                                result = None
                    if result is None or not result.ok:
                        # P2-1：双重 apply 都失败——不再静默 return。按 guardrails 连续失败计数
                        # 分级收敛（计数跨 resume_stuck 重 spawn 保留，成功推进后归零）：
                        # ① 首次：确定性阶段推进；② ≥FORCE：确定性硬强推；③ ≥ABORT：止血终局。
                        n_fail = guard.bump_apply_failure()
                        if n_fail >= APPLY_FAIL_ABORT_LIMIT:
                            await _apply_fail_abort(db, session, engine, session_id, seat, n_fail)
                            return
                        if await _apply_fail_force_push(db, session, engine, session_id, seat, n_fail):
                            return
                        await asyncio.sleep(1.2)
                        continue
                    broadcast = []
                    if result.event:
                        await engine.persist_event(db, result.event)
                        await _mirror_to_group(db, engine, session, result.event)
                        broadcast.append(result.event)
                    # 故障注入专项：引擎 advance 抛错同样属于「本轮推不动」——不能让它崩出调度层把对局
                    # 留在 playing。记为一次连续失败，走与双重 apply 失败相同的分级收敛。
                    # 因此「成功推进归零」移到 advance 成功之后：否则 apply 恒成功 + advance 恒抛
                    # 异常时，失败序列会被反复清零 → 无限空转。
                    try:
                        adv_events = await engine.advance()
                    except Exception as e:
                        _logger.error("game guard: engine.advance raised session=%d seat=%d: %s",
                                      session_id, seat, e, exc_info=True)
                        n_fail = guard.bump_apply_failure()
                        if n_fail >= APPLY_FAIL_ABORT_LIMIT:
                            await _apply_fail_abort(db, session, engine, session_id, seat, n_fail)
                            return
                        if await _apply_fail_force_push(db, session, engine, session_id, seat, n_fail):
                            return
                        await asyncio.sleep(1.2)
                        continue
                    guard.reset_apply_failures()  # apply + advance 全成功才算真正推进，连续失败序列归零
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
            # 故障注入专项：未预期异常不得让对局停在 playing（前端无限等待 + resume_stuck 空转重 spawn）。
            # 用独立会话止血终局并广播可见结束事件。
            if not _user_continues:
                await _emergency_stop_after_crash(session_id, e)
        finally:
            # P3-2（2026-09-18）：除「正常轮到用户、对局继续」外，任何退出都清理 guard
            # （异常退出 / session 已非 playing / 各类终局返回）。drop_guard 幂等，终局分支已
            # 显式 drop 也不冲突；唯一不清的是 _user_continues 路径（保留累计状态）。
            if not _user_continues:
                drop_guard(session_id)


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
