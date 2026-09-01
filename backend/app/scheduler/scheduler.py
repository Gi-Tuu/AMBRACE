"""主动交流调度引擎 — 后台异步循环"""
import asyncio
from datetime import date, datetime, timezone
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.proactive_settings import ProactiveMessageLog
from app.utils.logger import get_logger
from app.utils.async_tasks import spawn_background
from app.scheduler.diary_generator import generate_missing_diaries
from app.scheduler.moment_publisher import publish_pending_moments  # keep publisher
from app.services.moment_service import generate_pending_comments
from app.memory import catchup_extract_all

_logger = get_logger("scheduler.engine")

_scheduler_task: asyncio.Task | None = None
_storyline_task: asyncio.Task | None = None
_running = False

# 检查间隔（秒）— 从配置读取
from app.config import settings

IDLE_CHECK_INTERVAL = settings.scheduler_idle_interval
BIRTHDAY_CHECK_INTERVAL = settings.scheduler_birthday_interval
HOLIDAY_CHECK_INTERVAL = settings.scheduler_holiday_interval

# 活跃时段（仅在此时间段内推送主动消息）
ACTIVE_HOUR_START = settings.scheduler_active_hour_start
ACTIVE_HOUR_END = settings.scheduler_active_hour_end

# 日记/朋友圈检查间隔
DIARY_CHECK_INTERVAL = 3600  # 1 小时
MOMENT_CHECK_INTERVAL = 600  # 10 分钟

# 主动事件切片快速发送间隔（秒）
STORYLINE_FLUSH_INTERVAL = 3



async def send_to_session(
    session_id: int,
    character_id: int,
    user_id: int,
    content: str,
    message_type: str,
    holiday_name: str | None = None,
    log_proactive: bool = True,
    extra_meta: str | None = None,
):
    """将主动消息保存到数据库并通过 WS 推送（如果用户在线）"""
    msg_id = None
    # 保存到数据库
    async with async_session_factory() as db:
        msg = ChatMessage(
            session_id=session_id,
            sender_type="ai",
            content=content,
            extra_meta=extra_meta,
        )
        db.add(msg)
        await db.flush()
        await db.refresh(msg)
        msg_id = msg.id

        # 更新会话时间戳
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if session:
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # 记录主动消息日志（同一事件的后续切片不再重复计数，log_proactive=False）
        if log_proactive:
            log = ProactiveMessageLog(
                character_id=character_id,
                session_id=session_id,
                message_type=message_type,
                holiday_name=holiday_name,
                content=content[:500],
                extra_meta=extra_meta,
            )
            db.add(log)
        await db.commit()

    # 通过 WebSocket 推送（如果在线）
    from app.ws.connection_manager import push_to_session
    payload = {
        "type": "ai_response",
        "data": {
            "id": msg_id,
            "session_id": session_id,
            "character_id": character_id,
            "sender_type": "ai",
            "content": content,
            "extra_meta": extra_meta,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "memories_updated": False,
        "is_proactive": True,
    }
    pushed = await push_to_session(session_id, payload)
    if pushed:
        _logger.info("Pushed proactive msg to WS session=%d", session_id)
    else:
        _logger.info("User offline, saved proactive msg to DB session=%d", session_id)

    # #55 App 后台保活 + FCM 离线推送：WS 在线实时推送，不在线走 FCM
    try:
        from app.services.push_service import notify_user
        preview = content[:50] + ("…" if len(content) > 50 else "")
        await notify_user(
            user_id,
            title="新消息",
            body=preview,
            data={
                "route": "chat",
                "session_id": str(session_id),
                "character_id": str(character_id),
            },
            channel="chat",
            ws_payload=payload,
        )
    except Exception as e:
        _logger.warning("Push proactive msg to user failed user=%d: %s", user_id, e)


async def _check_anniversaries_today() -> None:
    """Shared Memory 纪念日（Phase C）：检查满月/周年 → 生成回忆消息（失败静默）"""
    try:
        from app.memory.shared_events import anniversary_text, check_anniversaries
        async with async_session_factory() as db:
            due = await check_anniversaries(db)
        for e in due:
            try:
                from sqlalchemy import select as _s
                from app.models.character import AICharacter
                from app.services.chat_service import get_latest_session_id
                async with async_session_factory() as _db2:
                    _c = (await _db2.execute(_s(AICharacter).where(AICharacter.id == e.character_id))).scalar_one_or_none()
                if _c is None:
                    continue
                _sid = await get_latest_session_id(e.user_id, e.character_id)
                if _sid:
                    await send_to_session(_sid, e.character_id, e.user_id,
                                          anniversary_text(e), message_type="anniversary_recall")
                    _logger.info("Anniversary recall sent char=%d event=%d", e.character_id, e.id)
            except Exception as ex:
                _logger.warning("Anniversary recall item failed: %s", ex)
    except Exception as e:
        _logger.warning("Anniversary check failed: %s", e)


async def scheduler_loop():
    """主调度循环 — 统一仲裁：定时承诺 / 生日节日 / 随机节律"""
    global _running
    _running = True
    _logger.info("Scheduler v2 started (unified arbiter)")

    from app.scheduler.arbiter import run_tick

    # 启动时恢复过期定时承诺
    try:
        from app.scheduler.arbiter import recover_on_startup
        await recover_on_startup()
    except Exception as e:
        _logger.warning("Timer recovery on startup failed: %s", e)

    comment_counter = 0
    extract_counter = 0
    diary_counter = 0
    decay_counter = 0
    state_decay_counter = 0
    moment_counter = 0
    file_cleanup_counter = 0
    life_counter = 0
    life_loop_counter = 0
    game_stuck_counter = 0
    reflection_counter = 0
    memory_counter = 0
    _diary_generated_today = False
    _reflection_done_today = False
    _memory_maintenance_done_today = False
    _last_date = date.today()
    _last_anniv_date = date.today()
    TICK = 30  # 统一 tick 间隔（秒）

    try:
        while _running:
            # 检测日期变更，重置日记标记
            if date.today() != _last_date:
                _last_date = date.today()
                _diary_generated_today = False
                _reflection_done_today = False

            await asyncio.sleep(TICK)
            comment_counter += TICK
            extract_counter += TICK
            diary_counter += TICK
            decay_counter += TICK
            state_decay_counter += TICK
            moment_counter += TICK
            file_cleanup_counter += TICK
            life_counter += TICK
            life_loop_counter += TICK
            game_stuck_counter += TICK
            reflection_counter += TICK
            memory_counter += TICK

            try:
                # 统一仲裁：定时承诺 + 生日/节日 + 随机节律（含朋友圈发布/互动）
                executed = await run_tick()
                if executed:
                    _logger.info("Arbiter executed: %s", ", ".join(executed))
            except Exception as e:
                _logger.error("Arbiter tick error: %s", e)

            local_hour = (datetime.now(timezone.utc).hour + 8) % 24

            # 插件 schedule_tick hook（每 30s tick，插件自行节流；异常隔离不影响主链路）
            try:
                from app.plugins.registry import run_hook
                await run_hook("schedule_tick", {
                    "utc_now": datetime.now(timezone.utc),
                    "local_hour": local_hour,
                })
            except Exception as e:
                _logger.warning("Plugin schedule_tick error: %s", e)

            # AI 自主发朋友圈（每 10 分钟，7:00-24:00）：发布待发动态（每日上限/间隔由 moment_service 控制）
            # 修复：_MomentPublishTask 注册后从未被执行（registry 无消费循环）→ AI 自主动态停滞
            if moment_counter >= MOMENT_CHECK_INTERVAL:
                moment_counter = 0
                if 7 <= local_hour < 24:
                    try:
                        await publish_pending_moments()
                    except Exception as e:
                        _logger.warning("Publish pending moments error: %s", e)

            # 评论兜底（每 5 分钟，P0-2 提频）：确保用户评论必被回复、0 评论动态被补评（不计上限）
            if comment_counter >= 300:
                comment_counter = 0
                if 7 <= local_hour < 24:
                    try:
                        await generate_pending_comments()
                    except Exception as e:
                        _logger.warning("Generate comments error: %s", e)

            # 记忆补采（每 15 分钟）
            if extract_counter >= 900:
                extract_counter = 0
                spawn_background(catchup_extract_all(), name="sched-catchup-extract")

            # Memory decay (every 6h): lazy decay + countdown removal
            if decay_counter >= 21600:
                decay_counter = 0
                from app.memory import run_memory_decay
                spawn_background(run_memory_decay(), name="sched-memory-decay")
                # AI 自主评星（P2，每 6h 与衰减同拍）：未评记忆批量 LLM 评星（每角色每日限额内）
                from app.memory.ai_rating import run_ai_rating
                spawn_background(run_ai_rating(), name="sched-ai-rating")

                # 记忆架构 v2.1 Phase 5：身份画像提炼（遍历活跃角色，24h 节流内部拦截；失败静默；
                # P0-1b 2026-08-16 起经统一内部工具入口执行，可观测 tool.executed 事件）
                try:
                    from app.agent.internal_runner import run_internal
                    from app.models.character import AICharacter
                    from sqlalchemy import select as _s
                    async with async_session_factory() as _db:
                        _chars = (await _db.execute(
                            _s(AICharacter).where(AICharacter.is_active == True, AICharacter.memory_v2_enabled == True)
                        )).scalars().all()
                    for _c in _chars:
                        spawn_background(
                            run_internal(
                                "memory_summary",
                                {"character_id": _c.id, "user_id": _c.user_id},
                                character_id=_c.id, user_id=_c.user_id,
                            ),
                            name=f"sched-identity-{_c.id}",
                        )
                except Exception:
                    pass
            # 状态八维惰性回落 + 趋势快照（每 1h 兜底结算并写 character_state_history；读时已惰性结算）
            if state_decay_counter >= 3600:
                state_decay_counter = 0
                from app.services.character_state_service import drift_all_character_states
                spawn_background(drift_all_character_states(), name="sched-state-drift")

            # 私聊文件保留 5 天（每 6h 清理一次，幂等：仅删超期文件并标记消息过期）
            if file_cleanup_counter >= 21600:
                file_cleanup_counter = 0
                from app.services.upload_service import cleanup_expired_files, cleanup_expired_voice
                spawn_background(cleanup_expired_files(days=5), name="sched-cleanup-files")
                # 语音/TTS 音频保留 14 天：超期删除文件并清空消息元数据（仅保留转写/回复文本）
                spawn_background(cleanup_expired_voice(days=14), name="sched-cleanup-voice")

            # AI 离线生活（每 1 小时）：状态结算 + 概率活动执行（强度档位控制频率；异常隔离不影响主链路）
            if life_counter >= 3600:
                life_counter = 0
                try:
                    from app.life.life_tick import LifeTickTask
                    await LifeTickTask().execute()
                except Exception as e:
                    _logger.warning("Life tick error: %s", e)

            # AI Life Loop v1.1（2026-08-26）：30 分钟行为决策（独立于 life_tick 的每小时结算）
            if life_loop_counter >= 1800:
                life_loop_counter = 0
                try:
                    from app.agent.loop import AGENT_FLAGS
                    if AGENT_FLAGS.get("life_loop_enabled", False):
                        from app.life.life_loop import LifeLoopTask
                        await LifeLoopTask().run()
                except Exception as e:
                    _logger.warning("Life loop error: %s", e)

            # 群聊游戏恢复（v3.3.5 审查修复，每 5 分钟）：playing 且 10 分钟以上无新事件的对局自动续跑 AI 回合（服务器重启/断线兜底）
            if game_stuck_counter >= 300:
                game_stuck_counter = 0
                try:
                    from app.agent.loop import AGENT_FLAGS
                    if AGENT_FLAGS.get("group_chat_games", False):
                        from app.api.games import resume_stuck_games
                        spawn_background(resume_stuck_games(), name="sched-resume-games")
                except Exception as e:
                    _logger.warning("Game stuck resume error: %s", e)

            # 日记（23:00 后触发一次，总结当天）
            if diary_counter >= 600:
                diary_counter = 0
                if local_hour >= 23 and not _diary_generated_today:
                    _logger.debug("Scheduler: generating diaries...")
                    await generate_missing_diaries()
                    _diary_generated_today = True

            # 每日复盘（Phase J：23:00 后触发一次，Agent 自我反思与规划；flag 默认关）
            if reflection_counter >= 600:
                reflection_counter = 0
                if local_hour >= 23 and not _reflection_done_today:
                    try:
                        from app.scheduler.daily_reflection import run_daily_reflections
                        await run_daily_reflections()
                    except Exception as e:
                        _logger.warning("Daily reflections error: %s", e)
                    _reflection_done_today = True

            # 日终记忆维护（P0-5，2026-08-16：23:00 后触发一次）：日摘要补生成 + 去重 + 置顶摘要补生成
            if memory_counter >= 600:
                memory_counter = 0
                if local_hour >= 23 and not _memory_maintenance_done_today:
                    try:
                        from app.scheduler.daily_memory_maintenance import run_daily_memory_maintenance
                        await run_daily_memory_maintenance()
                    except Exception as e:
                        _logger.warning("Daily memory maintenance error: %s", e)
                    _memory_maintenance_done_today = True

            # 纪念日检查（Phase C Shared Memory）：每日一次（原 _check_anniversaries_today 未接线死代码，2026-08-17 接入）
            if _last_anniv_date != date.today():
                _last_anniv_date = date.today()
                try:
                    from app.scheduler.scheduler import _check_anniversaries_today as _run_anniv
                    await _run_anniv()
                except Exception as _ae:
                    _logger.warning("Anniversary check error: %s", _ae)

    except asyncio.CancelledError:
        _logger.info("Scheduler cancelled")
    finally:
        _running = False
        _logger.info("Scheduler stopped")


async def storyline_sender_loop():
    """主动事件切片快速发送循环（独立于 30 秒仲裁 tick，每 3 秒检查一次）"""
    from app.scheduler.arbiter import flush_storyline_items
    _logger.info("Storyline sender loop started")
    while _running:
        try:
            await flush_storyline_items()
        except Exception as e:
            _logger.warning("Storyline flush error: %s", e)
        await asyncio.sleep(STORYLINE_FLUSH_INTERVAL)


def start():
    """启动调度器（由 lifespan 调用）"""
    global _scheduler_task, _storyline_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(scheduler_loop())
        _logger.info("Scheduler task created")
    if _storyline_task is None or _storyline_task.done():
        _storyline_task = asyncio.create_task(storyline_sender_loop())
        _logger.info("Storyline sender task created")


def stop():
    """停止调度器（由 lifespan 调用）"""
    global _running
    _running = False
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    if _storyline_task and not _storyline_task.done():
        _storyline_task.cancel()
    _logger.info("Scheduler stop requested")


def is_running() -> bool:
    """调度器是否在运行"""
    return _running and _scheduler_task is not None and not _scheduler_task.done()
