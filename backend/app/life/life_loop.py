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
import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.life import (
    LifeState, LifeActivityLog, LifeGoal, LifeSchedule, LifeChatIntent,
)
from app.models.proactive_settings import ProactiveSettings
from app.models.character_state import CharacterState
from app.models.pet import Pet
from app.life.life_state import (
    apply_tick, get_life_state, phase_of, beijing_hour, default_needs, clamp,
)
from app.life.decision import decide, StateSnapshot, Decision, ACTIONS
from app.life.followup import add_followup
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
            if st.location != "home":
                st.location = "home"
                st.current_room = "bedroom"
                st.location_updated_at = _now()
                await db.commit()
            return

        # 收集决策输入
        snap = await self._build_snapshot(db, char, st, needs, phase)

        # 决策
        decision = decide(snap)
        _logger.info("life loop decision: char=%d action=%s reason=%s",
                     char.id, decision.action, decision.reason)

        # 执行
        await self._execute(db, char, st, needs, decision, snap)

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
            select(Pet).where(Pet.user_id == char.user_id)
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
            from app.models.user.user_dnd import UserDndSettings
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
            from app.api.games import _create_session_in_db, _resume_ai_turns
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
                asyncio.ensure_future(_resume_ai_turns(session.id))
            _logger.info("life loop play_game started char=%d game=%s session=%d players=%d",
                         char.id, game_type, session.id, len(char_ids))
            return {"session_id": session.id, "game_type": game_type, "name": meta["name"]}
        except Exception as e:
            _logger.warning("life loop play_game start failed char=%d: %s", char.id, e)
            return None

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
        await db.commit()
        await db.refresh(log)

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
                st.location = act.location_to
                st.location_updated_at = _now()
            if act.room_to:
                st.current_room = act.room_to
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
            memory_id = None
            if act.memory:
                summary = await self._build_summary(db, char, decision, act)
                if await self._memory_allowed_today(db, char.id):
                    from app.memory.service import save_memory
                    mem = await save_memory(
                        user_id=char.user_id, character_id=char.id,
                        memory_type="event", content=summary,
                        importance=act.memory_importance,
                        sub_type="life_event", source="life",
                        speaker_type="character", speaker_id=char.id,
                        epistemic_status="FACT",
                    )
                    memory_id = mem.id if mem else None

                    # 回聊缓冲
                    if act.followup_window and act.visible:
                        await add_followup(
                            db, char.id, char.user_id, summary,
                            decision.action, memory_id, act.followup_window,
                        )

            # 出门后自动归来（2 个 tick 后）
            if act.location_to in ("world", "friend", "outside"):
                # 归来由下个 tick 的决策器处理：energy 低或时间晚时回 home
                pass

            log.status = "completed"
            log.output_json = json.dumps({
                "summary": "", "satisfied": satisfied,
                "location": st.location, "room": st.current_room,
            }, ensure_ascii=False)
            log.memory_id = memory_id
            log.completed_at = _now()
            await db.commit()

            # 事件广播（复用现有事件总线）
            self._publish_event(char, decision, act, memory_id)

        except Exception as e:
            _logger.warning("life loop execute failed: char=%d act=%s: %s",
                            char.id, decision.action, e)
            log.status = "failed"
            log.output_json = json.dumps({"error": str(e)[:200]}, ensure_ascii=False)
            await db.commit()

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

    async def _build_summary(self, db, char, decision, act) -> str:
        """生成记忆内容。life_loop_llm=False 时用模板；True 时用 LLM（每角色每日 ≤2 次）。"""
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("life_loop_llm", False):
            return self._template_summary(char, decision, act)
        # LLM 文案（每角色每日 ≤2 次，夜间禁用——由调用方计数控制）
        if not self._llm_copy_allowed(char.id):
            return self._template_summary(char, decision, act)
        try:
            from app.agent.llm_client import chat_completion
            text = await chat_completion(
                messages=[
                    {"role": "system", "content": (
                        f"你是{char.name}，用第一人称写一句生活动态（20-40字，"
                        "自然真诚，不要提AI，不要编造具体人名/地点/数据）。"
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
        return (character_id, _beijing_date_str())

    def _llm_copy_allowed(self, character_id: int) -> bool:
        return _llm_copy_counts.get(self._llm_copy_key(character_id), 0) < _DAILY_LLM_COPY_LIMIT

    def _bump_llm_copy(self, character_id: int) -> None:
        k = self._llm_copy_key(character_id)
        _llm_copy_counts[k] = _llm_copy_counts.get(k, 0) + 1

    def _publish_event(self, char, decision, act, memory_id):
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
                    "visible": act.visible, "summary": "",
                },
            )
            publish("life.activity_completed", evt)
        except Exception as e:
            _logger.warning("life loop event publish failed: %s", e)
