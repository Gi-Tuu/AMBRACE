"""游戏对局落库与结算服务（A22 第五刀，2026-10-02）。

本模块自 ``app/api/games.py`` 逐字节搬入（A22 第五刀，2026-10-02）。边界＝**对局落库与结算、
状态构造、崩溃/失败降级、群内镜像**；本模块**不定义路由、不持有 socket 客户端表**。

跨块调用约定：``async_session_factory`` / ``engine_for`` / ``_broadcast_game_event`` /
``_guard_stop`` 与进程内表 ``_ai_turn_locks`` / ``_game_ws_clients`` 仍留在 ``app.api.games``；
``_settle_game`` / ``_abort_game`` / ``_guard_stop_visible`` 虽已搬进本模块，但 tests 把桩打在
``app.api.games`` 上（含字符串路径打桩 "app.api.games.<name>"），故一律在函数内
``from app.api import games as _g`` 后走 ``_g.<name>``（顶层回指会与 games.py 的重导出成环，
放在模块顶层还会让打在 games 上的桩静默失效）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.games.base import GameEngine
from app.games.guardrails import get_guard, drop_guard, APPLY_FAIL_FORCE_LIMIT
from app.models.game import GameSession, GamePlayer, GameEvent
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

_logger = get_logger("api.games")


def _lazy_lock(session_id: int) -> asyncio.Lock:
    from app.api import games as _g
    return _g._ai_turn_locks.setdefault(session_id, asyncio.Lock())


async def _load_char_map(char_ids: list[int], user_id: int) -> dict:
    """校验角色归属（必须是该用户角色）并返回 {id: AICharacter}。"""
    from app.api import games as _g
    from app.models.character import AICharacter
    char_map = {}
    if char_ids:
        async with _g.async_session_factory() as db:
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
    from app.api import games as _g
    try:
        engine_cls = _g.engine_for(game_type)
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


async def _abort_game(db: AsyncSession, session, engine: GameEngine, reason: str) -> None:
    """口径2（2026-09-06 拍板）：无 draw 语义引擎的护栏末级止血=无胜负终止。

    对齐 abort_in_place 语义：session 置 aborted、不写胜负、不写终局记忆、不广播 winner；
    清理进程内锁/WS/护栏状态（与 _settle_game 收尾对齐）；logger.error + obs_event 告警。
    """
    from app.api import games as _g
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
    _g._ai_turn_locks.pop(sid, None)
    _g._game_ws_clients.pop(sid, None)
    drop_guard(sid)
    _logger.error(
        "game guard: no-draw engine aborted in place session=%d game=%s reason=%s",
        session.id, session.game_type, reason,
    )
    obs_event(None, "game_guard_no_draw_abort", {
        "session_id": session.id, "game_type": session.game_type, "reason": reason,
    })


async def _settle_game(db: AsyncSession, session, engine: GameEngine, winner: str) -> None:
    from app.api import games as _g
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

    _g._ai_turn_locks.pop(sid, None)  # v3.3.5 审查修复：结算后清理进程内锁，防长期运行内存增长
    _g._game_ws_clients.pop(sid, None)  # v3.3.6 审查修复：结算后清理 WS 空集合，防缓慢增长
    drop_guard(sid)  # 🛡️ 2026-09-04：结算后清理护栏进程内状态


# ── 投降后处理：落事件 → 结束结算 或 跳过投降者回合继续（用户/AI 共用）──
async def _run_surrender(db: AsyncSession, session, engine: GameEngine,
                         seat: int, broadcast: list) -> dict:
    from app.api import games as _g
    outcome = await engine.apply_surrender(seat)
    if not outcome.get("ok"):
        return outcome  # {"ok": False, "error": ...}

    for ev in outcome.get("events", []):
        await engine.persist_event(db, ev)
        await _mirror_to_group(db, engine, session, ev)
        broadcast.append(ev)

    if outcome.get("end"):
        winner = outcome.get("winner") or "draw"
        await _g._settle_game(db, session, engine, winner)
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
            await _g._settle_game(db, session, engine, winner)
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
    from app.api import games as _g
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
        await _g._settle_game(db, session, engine, winner)
    else:
        await engine.persist_state(db)
    await db.commit()
    for ev in broadcast:
        await _g._broadcast_game_event(session_id, ev, session.phase)
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
    from app.api import games as _g
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
        await _g._guard_stop(db, session, engine, reason=reason)
        await db.commit()
    finally:
        # 无论止血是否成功，都把"用户可见结束事件"推给在线连接，消除前端无限等待
        await _g._broadcast_game_event(session_id, end_ev, "result")
    return end_ev


async def _apply_fail_abort(db: AsyncSession, session, engine: GameEngine,
                            session_id: int, seat: int, n_fail: int) -> None:
    """连续「双重 apply 都失败」达 APPLY_FAIL_ABORT_LIMIT：止血终局 + 用户可见结束事件。

    payload 带 reason/apply_failures，作为 guardrails 计数的持久化终局记录。
    """
    from app.api import games as _g
    draws = getattr(engine, "has_draw_semantics", True)
    from app.memory.observability import obs_event
    obs_event(None, "game_guard_apply_fail_abort", {
        "session_id": session_id, "seat": seat, "apply_failures": n_fail,
        "draw_semantics": bool(draws),
    })
    await _g._guard_stop_visible(
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
    from app.api import games as _g
    err = f"{type(exc).__name__}: {exc}"[:200]
    try:
        async with _g.async_session_factory() as db:
            session = await db.get(GameSession, session_id)
            if session is None or session.status != "playing":
                return
            engine = _g.engine_for(session.game_type)(session)
            await engine.load(db)
            from app.memory.observability import obs_event
            obs_event(None, "game_guard_crash_abort", {
                "session_id": session_id, "game_type": session.game_type, "error": err,
            })
            draws = getattr(engine, "has_draw_semantics", True)
            await _g._guard_stop_visible(
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


async def _check_char_owned(db: AsyncSession, character_id: int, user_id: int) -> None:
    from app.models.character import AICharacter
    c = await db.get(AICharacter, int(character_id))
    if c is None or c.user_id != user_id:
        raise HTTPException(404, "角色不存在或不属于你")
