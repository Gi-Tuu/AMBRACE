"""Life Loop：30 分钟行为决策循环（设计稿 §0-§9）。

与现有 life_tick（每小时）的关系：
- life_tick 保留：负责 apply_tick 自然结算 + 旧活动系统（rest/organize_memory/...）
- life_loop 新增：负责 §2 分层决策器产出的新动作（sleep/eat/walk/go_out/...）
- 两者都写 life_activity_logs，life_loop 通过 input_json 带 "origin":"life_loop" 区分

修正（2026-08-26）：
- 写 LifeActivityLog 时 input_json 带 "origin":"life_loop"；last_action 冷却查询只取
  input_json like %life_loop%（与旧活动系统隔离，同名动作 create/browse 不再混淆）；
- 记忆节流：每角色每天 life_loop 记忆写入 ≤5 条，超出只写活动日志不写记忆；
   LLM 文案每角色每日 ≤2 次；
- 宠物报警只派给同用户最近互动角色，避免多角色重复响应同一宠物；
- 提供模块级 run_character_tick(character_id, user_id) 单角色立即执行（即时聊天指令用）。
"""
import json
import random
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.life import (
    LifeState, LifeActivityLog, LifeGoal, LifeSchedule, LifeChatIntent,
)
from app.models.character import ProactiveSettings
from app.models.character import CharacterState
from app.models.pet import Pet
from app.life.life_state import (
    apply_tick, get_life_state, phase_of, beijing_hour, default_needs, clamp,
)
from app.life.decision import decide, StateSnapshot, Decision, ACTIONS, INTENT_ACTION_MAP
from app.life.followup import add_followup
from app.life import space as _space          # 批次三(2026-09-16) 空间模型
from app.life import relations as _relations   # 批次三(2026-09-16) 亲属守卫
from app.utils.logger import get_logger

_logger = get_logger("life.loop")

TICK_SECONDS = 1800       # 30 分钟
NIGHT_TICK_SECONDS = 3600 # 夜间 60 分钟

# 记忆节流（修正 2026-08-26）：每角色每天 life_loop 记忆 ≤5 条；LLM 文案 ≤2 次
_DAILY_MEMORY_LIMIT = 5
_DAILY_LLM_COPY_LIMIT = 2
_llm_copy_counts: dict[tuple[int, str], int] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _beijing_date_str() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _prune_llm_copy_counts() -> None:
    """§4.4B（2026-09-09）：清理 _llm_copy_counts 里非今日的 key。

    key 本身已带北京日期（``(character_id, 'YYYY-MM-DD')``），限额语义天然跨天重置；
    但进程长跑时旧日期 key 会缓慢累积（每角色每天一个）。每次取 key 前顺手按当日清理一次，
    成本 O(角色数)，无需额外调度。
    """
    today = _beijing_date_str()
    stale = [k for k in _llm_copy_counts if k[1] != today]
    for k in stale:
        _llm_copy_counts.pop(k, None)


# F5（2026-09-08）：写库遇 database is locked 的有限退避重试（0.3s/0.6s，共 2 次重试后放弃）
# L5（2026-09-09）：实现收敛到 app/life/life_writer.py（life_tick/activity 与 life_loop 共用一套），
#   此处保留同名入口，退避间隔与语义不变。
from app.life.life_writer import LOCK_RETRY_DELAYS as _LOCK_RETRY_DELAYS  # noqa: F401
from app.life.life_writer import retry_on_lock as _retry_on_lock_impl


async def _retry_on_lock(fn, what: str):
    """写库协程遇 OperationalError(database is locked) 退避重试；非锁错误或重试耗尽直接抛出。"""
    return await _retry_on_lock_impl(fn, what)


async def run_character_tick(character_id: int, user_id: int) -> None:
    """单角色立即执行一个 Life Loop 回合（修正 2026-08-26：即时聊天指令用，不等 30min tick）。"""
    try:
        task = LifeLoopTask()
        async with async_session_factory() as db:
            char = await db.get(AICharacter, character_id)
            if char is None or char.user_id != user_id or not char.is_active:
                return
            await task._tick_character(db, char, phase_of(beijing_hour()), False)
    except Exception as e:
        _logger.warning("life loop instant tick failed char=%d: %s", character_id, e)


class LifeLoopTask:
    """30 分钟 Life Loop。由 scheduler.py 主循环驱动（非 BaseTask，用计数器触发）。"""

    async def run(self):
        hour = beijing_hour()
        phase = phase_of(hour)
        is_night = phase == "sleep"
        # L5（2026-09-09）：每轮开头收尾一次悬空 started（>30min 无 completed_at），失败静默
        try:
            from app.life.life_writer import close_orphan_activities
            await close_orphan_activities()
        except Exception as e:
            _logger.warning("life loop orphan cleanup failed: %s", e)
        try:
            async with async_session_factory() as db:
                chars = (
                    await db.execute(select(AICharacter).where(AICharacter.is_active.is_(True)))
                ).scalars().all()
                for c in chars:
                    try:
                        await self._tick_character(db, c, phase, is_night)
                    except Exception as e:
                        _logger.warning("life loop char=%d failed: %s", c.id, e)
        except Exception as e:
            _logger.warning("life loop run failed: %s", e)

    async def _tick_character(self, db, char, phase: str, is_night: bool):
        # 开关检查
        ps = (await db.execute(
            select(ProactiveSettings).where(ProactiveSettings.character_id == char.id)
        )).scalar_one_or_none()
        if ps is not None and not ps.life_enabled:
            return

        st = await get_life_state(db, char.id)
        needs = json.loads(st.needs_json or "{}") or default_needs()

        # 夜间：只做恢复性结算，不决策
        if is_night:
            await apply_tick(db, char.id, "sleep")
            # 批次三(2026-09-16)：睡眠落点随住校/假期（住校→宿舍，假期→东莞家），
            # 不再恒写 home/bedroom（角色一边说「去食堂」一边系统里 home/bedroom 的矛盾）。
            sleep_loc, sleep_room = _space.sleep_location()
            if st.location != sleep_loc or st.current_room != sleep_room:
                st.location = sleep_loc
                st.current_room = sleep_room
                st.location_updated_at = _now()
                await db.commit()
            return

        # 批次三 P0-5(2026-09-16)：白天按作息把「在家/在宿舍」的角色放到真实空间
        # （宿舍→教学楼→食堂→图书馆，见 space.student_day_location）；外出中不干预。
        await self._sync_day_space(db, st, phase)

        # 收集决策输入
        snap = await self._build_snapshot(db, char, st, needs, phase)

        # 决策
        decision = decide(snap)
        _logger.info("life loop decision: char=%d action=%s reason=%s",
                     char.id, decision.action, decision.reason)

        # F3a（2026-09-08）：不可映射的 pending 意图直接置 consumed——mapping 外类型
        # （历史错误固化的自述意图等）不再永挂阻塞后续真实意图
        if snap.pending_intents and decision.reason != "chat_intent":
            await self._consume_unmappable_intents(db, char.id)

        # 执行
        await self._execute(db, char, st, needs, decision, snap)

    async def _sync_day_space(self, db, st, phase: str) -> None:
        """批次三 P0-5(2026-09-16)：白天把本地基地内的角色同步到作息对应的空间。

        只改写「本地基地」地点（home/dorm/campus/canteen/library）：外出中（world/friend/
        outside/exit）保持不动，由决策器门控决定是否回家。住校学期按
        ``space.student_day_location`` 得到 宿舍/教学楼/食堂/图书馆；假期回落 home/bedroom。
        """
        if not _space.at_base(st.location):
            return
        loc, room = _space.student_day_location(phase, beijing_hour())
        if st.location == loc and st.current_room == room:
            return
        st.location = loc
        st.current_room = room
        st.location_updated_at = _now()
        await db.commit()

    async def _build_snapshot(self, db, char, st, needs, phase) -> StateSnapshot:
        cs = (await db.execute(
            select(CharacterState).where(CharacterState.character_id == char.id)
        )).scalar_one_or_none()

        # 进行中目标
        goals = (await db.execute(
            select(LifeGoal).where(
                LifeGoal.character_id == char.id, LifeGoal.status == "active"
            ).order_by(LifeGoal.priority.desc()).limit(3)
        )).scalars().all()

        # 到点日程
        now = _now()
        scheds = (await db.execute(
            select(LifeSchedule).where(
                LifeSchedule.character_id == char.id,
                LifeSchedule.status.in_(["scheduled", "active"]),
                LifeSchedule.start_time <= now,
            ).order_by(LifeSchedule.priority.desc()).limit(3)
        )).scalars().all()

        # 宠物报警（修正 2026-08-26：同用户多角色时只派给最近互动的角色，避免多角色重复响应同一宠物）
        pets = (await db.execute(
            select(Pet).where(Pet.user_id == char.user_id, Pet.abandoned_at.is_(None))
        )).scalars().all()
        recent_char = await self._recent_interacted_character(db, char.user_id)
        pet_alerts = []
        if recent_char is None or recent_char == char.id:
            pet_alerts = [{"hungry": (p.hunger or 50) < 20} for p in pets if (p.hunger or 50) < 20]

        # 聊天驱动意图（缓冲表）
        intents = (await db.execute(
            select(LifeChatIntent).where(
                LifeChatIntent.character_id == char.id,
                LifeChatIntent.status == "pending",
            ).order_by(LifeChatIntent.priority.desc(), LifeChatIntent.created_at.desc()).limit(3)
        )).scalars().all()

        # 用户在场判定
        user_active = False
        if st.last_user_interaction_at:
            user_active = (_now() - st.last_user_interaction_at) < timedelta(minutes=30)

        # Phase 2（2026-08-26）：自主开局可用性
        peers = await self._count_active_peers(db, char.user_id)
        played_today = await self._play_game_played_today(db, char.id)
        in_dnd = await self._in_dnd(db, char.user_id)
        play_game_available = (played_today < 1 and peers >= 2 and not in_dnd and not user_active)

        # 上一动作距今 tick 数
        # 只取 life_loop 来源日志做冷却（修正 2026-08-26：与旧 life_tick 活动系统 origin 隔离，避免同名动作混淆）
        last_log = (await db.execute(
            select(LifeActivityLog).where(
                LifeActivityLog.character_id == char.id,
                LifeActivityLog.status == "completed",
                LifeActivityLog.input_json.like("%life_loop%"),
            ).order_by(LifeActivityLog.completed_at.desc()).limit(1)
        )).scalar_one_or_none()
        last_action = last_log.activity_type if last_log else None
        last_ticks = 99
        if last_log and last_log.completed_at:
            last_ticks = int((_now() - last_log.completed_at).total_seconds() // TICK_SECONDS)

        return StateSnapshot(
            character_id=char.id, user_id=char.user_id,
            energy=st.energy, focus=st.focus, needs=needs,
            phase=phase, mood=cs.mood if cs else 50,
            fatigue=cs.fatigue if cs else 30, anger=cs.anger if cs else 10,
            location=st.location or "home", current_room=st.current_room or "living",
            last_action=last_action, last_action_tick=last_ticks,
            active_goals=[{"id": g.id, "type": g.type} for g in goals],
            due_schedules=[{"id": s.id, "title": s.title} for s in scheds],
            pet_alerts=pet_alerts,
            pending_intents=[{"id": i.id, "action_type": i.action_type} for i in intents],
            user_active_recently=user_active,
            dnd=in_dnd,
            play_game_available=play_game_available,
        )

    async def _recent_interacted_character(self, db, user_id: int) -> int | None:
        """同用户多角色：返回最近互动的角色 id。

        按 life_states.last_user_interaction_at 最新者；无记录时按 ai_characters.updated_at 兜底。
        仅用于宠物报警归属（修正 2026-08-26），失败静默返回 None。
        """
        try:
            chars = (await db.execute(
                select(AICharacter).where(
                    AICharacter.user_id == user_id, AICharacter.is_active.is_(True)
                )
            )).scalars().all()
            if not chars:
                return None
            best = None
            best_t = None
            for c in chars:
                st = (await db.execute(
                    select(LifeState).where(LifeState.character_id == c.id)
                )).scalar_one_or_none()
                t = st.last_user_interaction_at if st and st.last_user_interaction_at else c.updated_at
                if t is None:
                    continue
                if best_t is None or t > best_t:
                    best_t = t
                    best = c.id
            if best is None:
                first = (await db.execute(
                    select(AICharacter).where(
                        AICharacter.user_id == user_id, AICharacter.is_active.is_(True)
                    ).order_by(AICharacter.updated_at.desc()).limit(1)
                )).scalar_one_or_none()
                return first.id if first else None
            return best
        except Exception as e:
            _logger.warning("life loop recent-interacted-char failed: %s", e)
            return None

    async def _count_active_peers(self, db, user_id: int) -> int:
        """同用户活跃 AI 角色总数（含自己），用于自主开局人数校验。"""
        try:
            cnt = (await db.execute(
                select(func.count()).select_from(AICharacter).where(
                    AICharacter.user_id == user_id, AICharacter.is_active.is_(True)
                )
            )).scalar() or 0
            return int(cnt)
        except Exception as e:
            _logger.warning("life loop count peers failed: %s", e)
            return 0

    async def _play_game_played_today(self, db, character_id: int) -> int:
        """今日该角色自主开局（trigger=character_suggested）局数，用于每日限额。"""
        try:
            from app.models.game import GameSession, GamePlayer
            day_start = self._day_start_utc()
            cnt = (await db.execute(
                select(func.count()).select_from(GamePlayer).join(
                    GameSession, GameSession.id == GamePlayer.session_id
                ).where(
                    GamePlayer.character_id == character_id,
                    GameSession.trigger == "character_suggested",
                    GameSession.created_at >= day_start,
                )
            )).scalar() or 0
            return int(cnt)
        except Exception as e:
            _logger.warning("life loop play-game budget failed: %s", e)
            return 0

    def _day_start_utc(self) -> datetime:
        """北京时间当日 00:00 对应的 UTC 时刻。"""
        bj_now = datetime.now(timezone(timedelta(hours=8)))
        day_start_bj = bj_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (day_start_bj - timedelta(hours=8)).replace(tzinfo=None)

    async def _in_dnd(self, db, user_id: int) -> bool:
        """用户免打扰时段判断（dnd_enabled 且当前北京时间在时段内）。"""
        try:
            from app.models.user import UserDndSettings
            row = (await db.execute(
                select(UserDndSettings).where(UserDndSettings.user_id == user_id)
            )).scalar_one_or_none()
            if row is None or not row.dnd_enabled:
                return False
            hour = datetime.now(timezone(timedelta(hours=8))).hour
            start = int(getattr(row, "start_hour", 22) or 22)
            end = int(getattr(row, "end_hour", 8) or 8)
            if start <= end:
                return start <= hour < end
            return hour >= start or hour < end  # 跨夜时段
        except Exception as e:
            _logger.warning("life loop dnd check failed: %s", e)
            return False

    async def _start_group_game(self, db, char, decision: Decision, snap: StateSnapshot) -> dict | None:
        """自主开局：按同用户活跃角色数随机选游戏，创建 GameSession(trigger=character_suggested)。

        发起角色 + 1-4 个其他角色入座；用户观战。setup 后调度 _resume_ai_turns 自动推进。
        每日每角色限额由决策器 play_game_available 保证。
        """
        try:
            from app.api.games import _create_session_in_db, _resume_ai_turns, _spawn_background
            from app.games.registry import engine_for
            from app.models.game import GameSession
            # v3.3.6 审查修复：同用户已有进行中自主对局则不再重复开
            active = (await db.execute(
                select(GameSession.id).where(
                    GameSession.user_id == char.user_id,
                    GameSession.status == "playing",
                    GameSession.trigger == "character_suggested",
                ).limit(1)
            )).scalar_one_or_none()
            if active is not None:
                return None
            pool = (await db.execute(
                select(AICharacter).where(
                    AICharacter.user_id == char.user_id, AICharacter.is_active.is_(True)
                )
            )).scalars().all()
            pool = [p for p in pool if p.id]
            if len(pool) < 2:
                return None
            total = len(pool)
            if total >= 4:
                game_types = ["werewolf", "liars_bar"]
            elif total >= 3:
                game_types = ["liars_bar", "turtle_soup"]
            else:
                game_types = ["turtle_soup"]
            game_type = random.choice(game_types)
            meta = engine_for(game_type)(None).meta()
            seat_count = min(total, meta["max_players"])
            if seat_count < meta["min_players"]:
                return None
            # 发起角色必须入座；其余随机补齐
            others = [p for p in pool if p.id != char.id]
            random.shuffle(others)
            players = [char] + others[:seat_count - 1]
            char_ids = [p.id for p in players]
            session, engine = await _create_session_in_db(
                db, user_id=char.user_id, game_type=game_type,
                player_ids=char_ids, spectator_ids=[], user_as_player=False,
                group_id=None, trigger="character_suggested",
            )
            ts = engine.current_turn_seat()
            if ts is not None and engine.is_ai(ts):
                _spawn_background(_resume_ai_turns(session.id))
            _logger.info("life loop play_game started char=%d game=%s session=%d players=%d",
                         char.id, game_type, session.id, len(char_ids))
            return {"session_id": session.id, "game_type": game_type, "name": meta["name"]}
        except Exception as e:
            _logger.warning("life loop play_game start failed char=%d: %s", char.id, e)
            return None

    async def _consume_unmappable_intents(self, db, character_id: int) -> None:
        """F3a（2026-09-08）：把映射外（不可执行）的 pending 聊天意图置 consumed，防永挂。"""
        try:
            intents = (await db.execute(
                select(LifeChatIntent).where(
                    LifeChatIntent.character_id == character_id,
                    LifeChatIntent.status == "pending",
                )
            )).scalars().all()
            stale = [i for i in intents if i.action_type not in INTENT_ACTION_MAP]
            for i in stale:
                i.status = "consumed"
                i.consumed_at = _now()
            if stale:
                await db.commit()
                _logger.info("life loop consumed %d unmappable intents char=%d",
                             len(stale), character_id)
        except Exception as e:
            _logger.warning("life loop consume unmappable intents failed char=%d: %s",
                            character_id, e)

    async def _execute(self, db, char, st, needs, decision: Decision, snap: StateSnapshot):
        act = ACTIONS.get(decision.action)
        if act is None:
            return

        # 写日志（started）——input_json 带 "origin":"life_loop"
        log = LifeActivityLog(
            character_id=char.id, activity_type=decision.action, status="started",
            input_json=json.dumps({"reason": decision.reason, "phase": snap.phase,
                                   "origin": "life_loop"}, ensure_ascii=False),
            energy_cost=max(0, act.energy_cost),
            mood_delta=act.mood_delta,
        )
        db.add(log)
        await _retry_on_lock(lambda: db.commit(), f"log-start char={char.id}")
        await db.refresh(log)

        # §4.4（2026-09-09）：活动前状态快照——失败时按它回写 life_states。
        # 不能用 rollback 撤销：started 日志与本轮 L5「pre-memory commit」都已单独提交，
        # 会话内回滚已无意义（详见 _restore_state_after_failure）。
        snapshot = {
            "energy": st.energy,
            "location": st.location,
            "current_room": st.current_room,
            "location_updated_at": st.location_updated_at,
            "needs": dict(needs),
        }

        try:
            # Phase 2（2026-08-26）：自主开局——创建游戏会话并调度 AI 回合，不走标准状态回流
            if decision.action == "play_game":
                game = await self._start_group_game(db, char, decision, snap)
                log.status = "completed"
                log.output_json = json.dumps(
                    {"game": game.get("name", "") if game else ""}, ensure_ascii=False)
                log.completed_at = _now()
                if game:
                    await add_followup(
                        db, char.id, char.user_id,
                        f"{char.name}和伙伴们玩了一局{game['name']}，战绩已记入游乐手札。",
                        "play_game", None, "next_online",
                    )
                await db.commit()
                self._publish_event(char, decision, act, None)
                return

            # 状态回流
            satisfied = dict(act.needs_satisfied)
            st.energy = clamp(st.energy - act.energy_cost)
            if act.location_to:
                # 批次三(2026-09-16)：决策器落点是字面 home，这里按住校/假期归一到真实住处
                st.location = _space.normalize_location(act.location_to)
                st.location_updated_at = _now()
            if act.room_to:
                # 批次三(2026-09-16)：房间必须在该空间真实存在——宿舍/教学楼/食堂/图书馆没有
                # 厨房，eat 落 canteen 而不是凭空 kitchen 炖肉；图书馆里也不会出现卧室。
                st.current_room = _space.room_for(st.location, act.room_to, decision.action)
            # 需求结算
            for k, v in satisfied.items():
                needs[k] = clamp(needs.get(k, 50) - v)
            st.needs_json = json.dumps(needs, ensure_ascii=False)

            # 目标推进
            if decision.action in ("study", "create", "browse"):
                try:
                    from app.life.goal import advance_goal
                    await advance_goal(db, char.id, decision.action)
                except Exception:
                    pass

            # 日程标记完成
            if decision.reason == "schedule_due" and decision.params.get("schedule_title"):
                pass  # schedule_tick 会处理状态流转

            # 聊天意图标记 consumed
            if decision.reason == "chat_intent" and decision.params.get("intent_id"):
                intent = await db.get(LifeChatIntent, decision.params["intent_id"])
                if intent:
                    intent.status = "consumed"
                    intent.consumed_at = _now()

            # 记忆沉淀（仅值得记的动作）——记忆节流：每角色每天 life_loop ≤5 条
            # 批次三 P0-1/P0-5 止血(2026-09-16)：
            # - study 产出「真实主题 + 收获」的具体总结（任务1-A），不再用「学了一会儿新东西」
            #   这种恒定模板句；
            # - 其余动作只有在拿到真实内容（非模板句、无凭空亲属）时才落记忆，否则
            #   memory_id=null；output_json.summary 用 null 而非空串占位；
            # - 记忆写入统一 skip_dedup（life_writer），不同活动不会复用同一 memory_id。
            memory_id = None
            memory_failed = False
            summary_out = None
            if act.memory:
                summary = await self._build_summary(db, char, decision, act)
                tpl = self._template_summary(char, decision, act)
                known = await _relations.resolve_known_people(db, char.id)
                has_relation = _relations.mentions_unspecified_relation(summary, known)
                meaningful = self.is_meaningful_summary(summary, tpl, has_relation)
                if meaningful:
                    summary_out = summary
                    if await self._memory_allowed_today(db, char.id):
                        # L5（2026-09-09）：**写记忆前先提交本轮状态回流，释放 SQLite 唯一写锁**。
                        # 原顺序：st.energy/needs 改了未提交 → _memory_allowed_today 查询触发
                        # autoflush → 外部 session 拿到写锁并持有到本函数末尾 → save_memory 另开连接
                        # 只能在 busy_timeout(10s) 后报 locked，而锁的持有者正是等待方自己，
                        # 单靠 0.3/0.6s 重试永远等不到（09-09 每 30min 一批 5 条 failed 的真正根因）。
                        await _retry_on_lock(lambda: db.commit(), f"pre-memory flush char={char.id}")
                        from app.life.life_writer import save_life_memory_with_retry
                        mem = await save_life_memory_with_retry(
                            user_id=char.user_id, character_id=char.id,
                            memory_type="event", content=summary,
                            importance=act.memory_importance,
                            sub_type="life_event", source="life",
                            speaker_type="character", speaker_id=char.id,
                            epistemic_status="FACT",
                        )
                        memory_id = mem.id if mem else None
                        memory_failed = mem is None

                        # 回聊缓冲
                        if act.followup_window and act.visible:
                            await add_followup(
                                db, char.id, char.user_id, summary,
                                decision.action, memory_id, act.followup_window,
                            )
                    # 配额已满：summary_out 已记录真实摘要，仅不写记忆
                else:
                    if summary and summary.strip():
                        _logger.info(
                            "life loop skip meaningless memory char=%d act=%s template=%s relation=%s",
                            char.id, decision.action, summary == tpl, has_relation)

            # 出门后自动归来（2 个 tick 后）
            if act.location_to in ("world", "friend", "outside"):
                # 归来由下个 tick 的决策器处理：energy 低或时间晚时回 home/dorm
                pass

            log.status = "completed"
            log.output_json = json.dumps({
                # 批次三(2026-09-16)：summary 不再用空串占位——有真实内容给内容，否则显式 null
                "summary": summary_out, "satisfied": satisfied,
                "location": st.location, "room": st.current_room,
                "memory_failed": memory_failed,
            }, ensure_ascii=False)
            log.memory_id = memory_id
            log.completed_at = _now()
            await _retry_on_lock(lambda: db.commit(), f"complete char={char.id}")

            # 事件广播（复用现有事件总线）
            self._publish_event(char, decision, act, memory_id, summary_out)

        except Exception as e:
            _logger.warning("life loop execute failed: char=%d act=%s: %s",
                            char.id, decision.action, e)
            try:
                log.status = "failed"
                log.output_json = json.dumps({"error": str(e)[:200]}, ensure_ascii=False)
                await db.commit()
            except Exception as ce:
                # 标 failed 失败不得阻断下面的状态回写
                _logger.warning("life loop mark-failed commit error char=%d: %s", char.id, ce)
            # 活动失败 → 状态回到活动前（state 本体的脏改必须撤销）
            await self._restore_state_after_failure(char.id, snapshot)

    async def _restore_state_after_failure(self, character_id: int, snapshot: dict) -> None:
        """§4.4（2026-09-09）：活动失败后按快照回写 life_states（独立短事务，幂等）。

        背景：_execute 里 started 日志先单独 commit，L5 又在写记忆前加了一次
        「pre-memory commit」释放 SQLite 写锁——因此慢操作（_build_summary/LLM、
        add_followup、记忆写入）抛错时，状态回流（energy/needs/location）早已固化，
        只把日志标 failed 会留下「活动失败但状态按活动进行过落库」的脏状态。
        本函数用独立短事务按快照回写被改字段；自身失败只记 warning，不二次抛。

        注：目标推进/日程/意图 consumed 等副作用发生在失败段之前且已提交，本期不回滚
        （其本身幂等/低害）；生活状态本体必须回写。
        """
        try:
            async with async_session_factory() as db:
                st = (await db.execute(
                    select(LifeState).where(LifeState.character_id == character_id)
                )).scalar_one_or_none()
                if st is None:
                    return
                st.energy = snapshot["energy"]
                st.location = snapshot["location"]
                st.current_room = snapshot["current_room"]
                st.location_updated_at = snapshot["location_updated_at"]
                st.needs_json = json.dumps(snapshot["needs"], ensure_ascii=False)
                await db.commit()
                _logger.info("life loop state restored char=%d after failed activity",
                             character_id)
        except Exception as e:
            _logger.warning("life loop state restore failed char=%d: %s", character_id, e)

    async def _memory_allowed_today(self, db, character_id: int) -> bool:
        """记忆节流（修正 2026-08-26）：每角色每天 life_loop 记忆 ≤5 条。"""
        now = _now()
        bj_now = now + timedelta(hours=8)
        day_start_bj = bj_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = (day_start_bj - timedelta(hours=8)).replace(tzinfo=None)
        count = (await db.execute(
            select(func.count()).select_from(LifeActivityLog).where(
                LifeActivityLog.character_id == character_id,
                LifeActivityLog.status == "completed",
                LifeActivityLog.input_json.like("%life_loop%"),
                LifeActivityLog.memory_id.is_not(None),
                LifeActivityLog.completed_at >= day_start_utc,
            )
        )).scalar() or 0
        return int(count) < _DAILY_MEMORY_LIMIT

    @staticmethod
    def is_meaningful_summary(summary: str | None, template: str | None = None,
                              has_unspecified_relation: bool = False) -> bool:
        """批次三 P0-1(2026-09-16)：这条总结是否值得沉淀为记忆。

        只有同时满足才算真实内容：
        - 非空；
        - 不等于恒定模板句（如 study 的「学了一会儿新东西，感觉有收获。」——它曾被
          save_memory 去重合并反复挂到同一条 memory_id）；
        - 不含凭空生成的亲属内容（P0-5）。

        否则 ``memory_id=null``、``output_json.summary=null``，宁可不沉淀也不写垃圾记忆。
        """
        if not summary or not summary.strip():
            return False
        if template is not None and summary == template:
            return False
        return not has_unspecified_relation

    async def _build_summary(self, db, char, decision, act) -> str:
        """生成记忆内容。life_loop_llm=False 时用模板；True 时用 LLM（每角色每日 ≤2 次）。

        批次三 P0-1(2026-09-16)：study 不再返回恒定模板句——优先用「真实主题 + 一句收获」
        的具体总结（任务1-A），让 study 真正沉淀且不会因高度相似被合并到同一条 memory_id。
        批次三 P0-5：LLM 文案注入当前空间约束（宿舍没厨房就不写做饭/等你回家）。
        """
        if decision.action == "study":
            specific = await self._study_summary(db, char)
            if specific:
                return specific
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("life_loop_llm", False):
            return self._template_summary(char, decision, act)
        # LLM 文案（每角色每日 ≤2 次，夜间禁用——由调用方计数控制）
        if not self._llm_copy_allowed(char.id):
            return self._template_summary(char, decision, act)
        try:
            from app.agent.llm_client import chat_completion
            location = await self._current_location(db, char.id)
            text = await chat_completion(
                messages=[
                    {"role": "system", "content": (
                        f"你是{char.name}，用第一人称写一句生活动态（20-40字，"
                        "自然真诚，不要提AI，不要编造具体人名/地点/数据）。"
                        + _space.space_guard(location)
                    )},
                    {"role": "user", "content": f"你刚{act.label}了。"},
                ],
                temperature=0.9, max_tokens=80, task="life_loop",
                user_id=char.user_id,
            )
            self._bump_llm_copy(char.id)
            return (text or "").strip()[:200]
        except Exception:
            return self._template_summary(char, decision, act)

    @staticmethod
    async def _current_location(db, character_id: int) -> str:
        """角色当前空间（读不到时按住校/假期回落真实住处）；供内容/话术约束使用。"""
        try:
            st = (await db.execute(
                select(LifeState).where(LifeState.character_id == character_id)
            )).scalar_one_or_none()
            if st is not None and (st.location or "").strip():
                return st.location
        except Exception:
            pass
        return _space.home_base()[0]

    async def _study_summary(self, db, char) -> str:
        """study 具体总结：真实学习主题（目标/兴趣/日程）+ 一句收获（句式轮换，非恒定模板）。

        取不到真实主题时返回 ""（调用方回落到模板 → 模板被判无意义 → 不写记忆），
        宁可不沉淀也不写恒定模板句（任务1-B 兜底）。
        """
        topic = await self._study_topic(db, char.id)
        if not topic:
            return ""
        takeaway = random.choice((
            "弄明白了一个之前卡住的点，顺手记了几笔。",
            "把之前模糊的地方理清了，有点收获。",
            "对其中一处细节想通了，记下来备查。",
            "整理了一遍要点，比昨天清楚一些。",
        ))
        return f"{char.name}学了「{topic}」，{takeaway}"

    async def _study_topic(self, db, character_id: int) -> str:
        """学习主题（真实数据，不编造）：进行中目标标题 → 最高兴趣 → 到点日程标题。"""
        try:
            goals = (await db.execute(
                select(LifeGoal).where(
                    LifeGoal.character_id == character_id, LifeGoal.status == "active"
                ).order_by(LifeGoal.priority.desc(), LifeGoal.id.desc()).limit(1)
            )).scalars().all()
            if goals and (goals[0].title or "").strip():
                return goals[0].title.strip()[:40]
            from app.models.life import LifeInterest
            interests = (await db.execute(
                select(LifeInterest).where(
                    LifeInterest.character_id == character_id, LifeInterest.level >= 20
                ).order_by(LifeInterest.level.desc()).limit(1)
            )).scalars().all()
            if interests and (interests[0].name or "").strip():
                return f"{interests[0].name.strip()[:20]}相关的内容"
            now = _now()
            scheds = (await db.execute(
                select(LifeSchedule).where(
                    LifeSchedule.character_id == character_id,
                    LifeSchedule.status.in_(["scheduled", "active"]),
                    LifeSchedule.title.like("%学%"),
                    LifeSchedule.end_time >= now,
                ).order_by(LifeSchedule.start_time.desc()).limit(1)
            )).scalars().all()
            if scheds and (scheds[0].title or "").strip():
                return scheds[0].title.strip()[:40]
        except Exception as e:
            _logger.warning("life loop study topic failed char=%d: %s", character_id, e)
        return ""

    def _template_summary(self, char, decision, act) -> str:
        """模板记忆文案（零 LLM；LLM 关闭或超限时兜底）。"""
        templates = {
            "study": f"{char.name}学了一会儿新东西，感觉有收获。",
            "create": f"{char.name}花时间做了点创作，心情不错。",
            "browse": f"{char.name}浏览了一些感兴趣的内容。",
            "walk": f"{char.name}出门散了会儿步，放松了一下。",
            "go_out": f"{char.name}出门了一趟，看到些新鲜事。",
            "visit_friend": f"{char.name}去拜访了朋友，聊得很开心。",
            "pet_play": f"{char.name}陪宠物玩了一会儿。",
        }
        return templates.get(decision.action, f"{char.name}做了「{act.label}」。")

    def _llm_copy_key(self, character_id: int):
        _prune_llm_copy_counts()
        return (character_id, _beijing_date_str())

    def _llm_copy_allowed(self, character_id: int) -> bool:
        return _llm_copy_counts.get(self._llm_copy_key(character_id), 0) < _DAILY_LLM_COPY_LIMIT

    def _bump_llm_copy(self, character_id: int) -> None:
        k = self._llm_copy_key(character_id)
        _llm_copy_counts[k] = _llm_copy_counts.get(k, 0) + 1

    def _publish_event(self, char, decision, act, memory_id, summary: str | None = None):
        try:
            from app.events import publish
            from app.events.schema import make_event
            evt = make_event(
                "life.activity_completed",
                speaker={"type": "character", "id": char.id},
                target={"type": "user", "id": char.user_id},
                audience=[char.id, char.user_id],
                provenance={"origin": "life_loop"},
                data={
                    "user_id": char.user_id, "character_id": char.id,
                    "activity_type": decision.action, "memory_id": memory_id,
                    "visible": act.visible, "summary": summary or "",
                },
            )
            publish("life.activity_completed", evt)
        except Exception as e:
            _logger.warning("life loop event publish failed: %s", e)
