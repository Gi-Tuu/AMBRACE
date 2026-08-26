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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.games import engine_for, list_games
from app.agent.loop import AGENT_FLAGS
from app.games.base import GameEngine
from app.models.game import GameSession, GamePlayer
from app.utils.logger import get_logger

_logger = get_logger("api.games")

router = APIRouter(prefix="/api/v1/games", tags=["Games"])

# 进程内防重入锁（跨进程场景需 DB 行锁，MVP 单进程足够）
_ai_turn_locks: dict[int, asyncio.Lock] = {}


def _lazy_lock(session_id: int) -> asyncio.Lock:
    return _ai_turn_locks.setdefault(session_id, asyncio.Lock())


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

    non_spectator = len(player_ids) + (1 if user_as_player else 0)
    if not (engine_cls.min_players <= non_spectator <= engine_cls.max_players):
        raise HTTPException(
            400,
            f"{meta['name']}需 {engine_cls.min_players}-{engine_cls.max_players} 名玩家，"
            f"当前 {non_spectator} 名",
        )
    if game_type in ("truth_or_dare", "twenty_q") and non_spectator != 2:
        raise HTTPException(400, f"{meta['name']}需要恰好 2 名玩家")

    # 拉取 AI 角色信息（人名/人设/关系）
    all_char_ids = list(dict.fromkeys(player_ids + spectator_ids))
    from app.models.character import AICharacter
    char_map = {}
    if all_char_ids:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(AICharacter).where(AICharacter.id.in_(all_char_ids))
            )).scalars().all()
        char_map = {c.id: c for c in rows}
    # 校验角色归属（必须是该用户的角色）
    for cid in all_char_ids:
        c = char_map.get(cid)
        if c is None or c.user_id != user_id:
            raise HTTPException(400, f"角色 {cid} 不存在或不属于你")

    async with async_session_factory() as db:
        session = GameSession(
            user_id=user_id, group_id=group_id, game_type=game_type,
            player_mode=meta["player_mode"], status="created",
            round=0, phase="", trigger=data.get("trigger", "user_initiated"),
        )
        db.add(session)
        await db.flush()

        # 座次分配：先玩家（user 或 AI 依次），再观战者
        seats = []
        next_seat = 0
        user_seat = -1

        def _add_player(p_type, char_id=None, uid=None, spectator=False):
            nonlocal next_seat, user_seat
            s = next_seat
            next_seat += 1
            p = GamePlayer(
                session_id=session.id, player_type=p_type,
                user_id=uid, character_id=char_id, seat=s, is_spectator=spectator,
                alive=True, score=0, private_json="{}",
            )
            db.add(p)
            if p_type == "user" and not spectator:
                user_seat = s
            elif p_type == "user" and spectator:
                user_seat = s
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

        # 注入玩家元信息（人名/人设/关系），持久化到 state_json 供 load 恢复
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

        # 若第一个行动者是 AI，启动续跑
        if engine.current_turn_seat() is not None and engine.is_ai(engine.current_turn_seat()):
            asyncio.ensure_future(_resume_ai_turns(session.id))

        return {
            "ok": True,
            "session_id": session.id,
            "game_type": game_type,
            "player_mode": meta["player_mode"],
            "game": meta,
            "state": _build_state(engine, user_seat=input_seat(engine, user_id)),
        }


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

        if result.event:
            await engine.persist_event(db, result.event)
            await _mirror_to_group(db, engine, session, result.event)
        adv_events = await engine.advance()
        for ev in adv_events:
            await engine.persist_event(db, ev)
            await _mirror_to_group(db, engine, session, ev)

        winner = await engine.check_winner()
        if winner:
            await _settle_game(db, session, engine, winner)
            await db.commit()
            return {"ok": True, "finished": True, "winner_side": winner}

        await engine.persist_state(db)
        await db.commit()
        # 触发 AI 续跑（幂等；先响应，再异步推 AI 回合）
        asyncio.ensure_future(_resume_ai_turns(sid))
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
            asyncio.ensure_future(_resume_ai_turns(sid))
        return _build_state(engine, user_seat=view_seat)


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
async def _settle_game(db: AsyncSession, session, engine: GameEngine, winner: str) -> None:
    await engine.finish(db, winner)
    from app.games.archive import build_archive
    from app.games.memory_bridge import finalize_game
    session.archive_json = json.dumps(build_archive(session, engine), ensure_ascii=False)
    db.add(session)
    # 主记忆摘要指针 + game_memories（每 AI 角色）
    await finalize_game(db, session, engine)


# ── AI 回合调度（幂等可续跑）──
async def _resume_ai_turns(session_id: int) -> None:
    """推进所有 AI 回合，直到轮到用户或游戏结束。可被任意入口安全重复调用。"""
    lock = _lazy_lock(session_id)
    if lock.locked():
        return
    async with lock:
        try:
            async with async_session_factory() as db:
                while True:
                    session = await db.get(GameSession, session_id)
                    if session is None or session.status != "playing":
                        return
                    engine = engine_for(session.game_type)(session)
                    await engine.load(db)
                    seat = engine.current_turn_seat()
                    if seat is None or not engine.is_ai(seat):
                        return  # 轮到用户 / 已结束
                    from app.games.ai_player import ai_decide
                    decision = await ai_decide(engine, seat)
                    payload = dict(decision.get("payload") or {})
                    if decision.get("content"):
                        payload.setdefault("content", decision["content"])
                    result = await engine.apply_action(seat, decision.get("action", ""), payload)
                    if result.ok and result.event:
                        await engine.persist_event(db, result.event)
                        await _mirror_to_group(db, engine, session, result.event)
                    adv_events = await engine.advance()
                    for ev in adv_events:
                        await engine.persist_event(db, ev)
                        await _mirror_to_group(db, engine, session, ev)
                    winner = await engine.check_winner()
                    if winner:
                        await _settle_game(db, session, engine, winner)
                        await db.commit()
                        _logger.info("game finished session=%d winner=%s", session_id, winner)
                        return
                    await engine.persist_state(db)
                    await db.commit()
                    await asyncio.sleep(1.2)  # 只在提交后 sleep，避免长事务
        except Exception as e:
            _logger.warning("_resume_ai_turns failed session=%d: %s", session_id, e)


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
    from app.models.chat_group import ChatGroupMessage
    db.add(ChatGroupMessage(
        group_id=session.group_id, sender_type=sender_type, character_id=cid,
        content=content[:200], msg_type=msg_type, game_session_id=session.id,
    ))
