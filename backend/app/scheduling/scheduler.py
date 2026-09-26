"""主动交流调度引擎 — 后台异步循环"""
import asyncio
from datetime import date, datetime, timezone
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.chat import ChatMessage
from app.models.chat import ChatSession
from app.models.character import ProactiveMessageLog
from app.utils.logger import get_logger
from app.utils.async_tasks import spawn_background
from app.utils.timeutil import app_local_hour, now_naive_utc
from app.scheduling.diary_generator import generate_missing_diaries
from app.scheduling.moment_publisher import publish_pending_moments  # keep publisher
from app.application.moment_service import generate_pending_comments
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

# 控制台删号·回收站到期自动清除检查间隔（第二期第二批）：每 10 分钟看一眼窗口与到期，
# 不满足条件 tick 内立刻返回（关 flag / 窗口外都零查库零行为），满足才清除（内部批间让出事件循环）。
ACCOUNT_PURGE_CHECK_INTERVAL = 600

# 主动事件切片快速发送间隔（秒）
STORYLINE_FLUSH_INTERVAL = 3

# 周期任务持久化台账（2026-09-26 批次 PT）：判据从「进程内 tick 计数」换成「上次成功时间戳」
# 的两个任务；阈值与迁走前按秒累加的计数阈值等价（6h / 1h）。
from datetime import timedelta

FILE_CLEANUP_INTERVAL = timedelta(hours=6)
PIS_STALE_INTERVAL = timedelta(hours=1)

# ── 长周期记忆维护（记忆衰减 + AI 自主评星）独立循环参数 ──
# 每 300 秒问一次「到期没」：run_if_due() 内部只读状态文件 + 比时间（微秒级），
# 不到期立刻返回 ⇒ 高频问零行为、零成本，不需要在外面再加任何判断。
MEMORY_MAINTENANCE_INTERVAL = 300
# 本循环的 stall 阈值必须显著大于间隔，不能照抄 scheduler 的 180s：到期那一拍要 await
# 整轮维护（每角色 1 次批量 LLM 调用，单次 90s 超时上限 × 十余活跃角色，最坏约 20 分钟），
# 于是「心跳间隔 = 300s + 单轮维护耗时」。阈值取 1800s 才不会让新循环自己又被误判 stalled。
MEMORY_MAINTENANCE_STALL_SEC = 1800



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
    # L3（2026-09-09 主体归属治理）：主题熔断统一兜底——state_trigger / memory_review /
    # life_regression / storyline / pet_care / life_share 等所有经本出口的主动通道都覆盖
    # （timer 已在 arbiter 内自行判并 mark_fired，保证承诺状态正确流转，此处不重复判）。
    # 节庆/纪念日等必须送达的类型白名单豁免；同一事件的后续切片（log_proactive=False）不判。
    # 异常一律 fail-open：宁可不拦也不阻塞正常主动消息。
    if log_proactive:
        try:
            from app.agent.loop import AGENT_FLAGS
            if AGENT_FLAGS.get("proactive_topic_guard", False):
                from app.scheduling.proactive_topic_guard import (
                    GUARD_EXEMPT_TYPES as _EXEMPT_TYPES, should_suppress as _should_suppress,
                )
                if message_type not in _EXEMPT_TYPES:
                    _sup, _reason = await _should_suppress(character_id, content)
                    if _sup:
                        _logger.info("Proactive msg suppressed char=%d type=%s: %s",
                                     character_id, message_type, _reason)
                        return  # 不写库、不推送、不发 FCM
        except Exception as e:
            _logger.warning("send_to_session topic guard fail-open: %s", e)
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
        from app.application.push_service import notify_user
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
                from app.application.chat_service import get_latest_session_id
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


async def _cleanup_files_tick():
    """私聊文件保留 5 天 / 语音 14 天 / 事件流水（每 6h，幂等）。

    2026-09-26 批次 PT：函数体逐字搬自原「每 21600 秒一拍」的文件清理分支，
    只是判据从进程内计数换成持久化台账（app/scheduling/periodic_state.py）。
    """
    from app.application.upload_service import cleanup_expired_files, cleanup_expired_voice
    spawn_background(cleanup_expired_files(days=5), name="sched-cleanup-files")
    # 语音/TTS 音频保留 14 天：超期删除文件并清空消息元数据（仅保留转写/回复文本）
    spawn_background(cleanup_expired_voice(days=14), name="sched-cleanup-voice")
    # 3.10 事件流水保留策略（P1，方案 §8.5）：flag domain_event_retention_days=0 时
    # 内部直接返回 0（不删），本地优先默认永久保留。
    from app.events.store import purge_expired_domain_events
    spawn_background(purge_expired_domain_events(), name="sched-purge-domain-events")


async def _pis_stale_tick():
    """前瞻约定时效治理（每 1h 幂等清扫）。

    2026-09-26 批次 PT：函数体逐字搬自原「每 3600 秒一拍」的约定清扫分支
    （含原有 try/except 与日志文案），只是判据换成持久化台账。
    """
    try:
        from app.scheduling.prospective_intent import (
            expire_overdue as _pis_expire, mark_stale_overdue as _pis_stale,
            mark_stale_cues as _pis_stale_cue,
        )
        _n_stale = await _pis_stale()
        _n_exp = await _pis_expire()
        _n_cue = await _pis_stale_cue()
        if _n_stale or _n_exp or _n_cue:
            _logger.info(
                "Prospective intent sweep: stale=%d expired=%d stale_cue=%d",
                _n_stale, _n_exp, _n_cue,
            )
    except Exception as e:
        _logger.warning("Prospective intent stale sweep error: %s", e)


async def scheduler_loop():
    """主调度循环 — 统一仲裁：定时承诺 / 生日节日 / 随机节律"""
    global _running
    _running = True
    _logger.info("Scheduler v2 started (unified arbiter)")

    from app.scheduling.arbiter import run_tick

    # 启动时恢复过期定时承诺
    try:
        from app.scheduling.arbiter import recover_on_startup
        await recover_on_startup()
    except Exception as e:
        _logger.warning("Timer recovery on startup failed: %s", e)

    # 长周期记忆维护（记忆衰减 + AI 自主评星）「按时间戳补齐」（2026-09-25）：
    # 进程内 6 小时计数在频繁重启下可能永远到不了阈值（实测评星因此从 09-20 起停摆 5 天），
    # 故启动即检查一次、到期就补跑；交给后台任务，不阻塞启动。
    try:
        from app.memory.maintenance_schedule import run_if_due as _run_maintenance
        spawn_background(_run_maintenance(reason="startup"), name="sched-memory-maintenance-startup")
    except Exception as e:
        _logger.warning("Memory maintenance startup check failed: %s", e)

    comment_counter = 0
    extract_counter = 0
    diary_counter = 0
    identity_counter = 0
    state_decay_counter = 0
    moment_counter = 0
    life_counter = 0
    life_loop_counter = 0
    game_stuck_counter = 0
    reflection_counter = 0
    memory_counter = 0
    purge_counter = 0
    _diary_generated_today = False
    _reflection_done_today = False
    _memory_maintenance_done_today = False
    _group_memory_compact_done_today = False
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
                _group_memory_compact_done_today = False

            await asyncio.sleep(TICK)
            from app.utils.supervisor import supervisor
            supervisor.heartbeat("scheduler")
            comment_counter += TICK
            extract_counter += TICK
            diary_counter += TICK
            identity_counter += TICK
            state_decay_counter += TICK
            moment_counter += TICK
            life_counter += TICK
            life_loop_counter += TICK
            game_stuck_counter += TICK
            reflection_counter += TICK
            memory_counter += TICK
            purge_counter += TICK

            try:
                # 统一仲裁：定时承诺 + 生日/节日 + 随机节律（含朋友圈发布/互动）
                executed = await run_tick()
                if executed:
                    _logger.info("Arbiter executed: %s", ", ".join(executed))
            except Exception as e:
                _logger.error("Arbiter tick error: %s", e)

            # B1（2026-09-06）：用户可感知窗口已迁「应用时区」（APP_TZ_OFFSET_HOURS 默认 +8）。
            # 其余 ~44 文件仍硬编码 UTC+8 的「用户可感知」换算，按批次渐进迁移（见源方案 §7 B1）；
            # 库内存储口径保持 UTC-naive 不动（零数据迁移）。
            local_hour = app_local_hour()

            # 插件 schedule_tick hook（每 30s tick，插件自行节流；异常隔离不影响主链路）
            try:
                from app.plugins.registry import run_hook
                await run_hook("schedule_tick", {
                    "utc_now": now_naive_utc(),
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

            # 身份画像提炼（记忆架构 v2.1 Phase 5，每 5 分钟问一次）：遍历活跃角色，24h 节流由内部拦截；
            # 失败静默；P0-1b 2026-08-16 起经统一内部工具入口执行，可观测 tool.executed 事件。
            # 注：长周期记忆维护已于 2026-09-26 挪进独立 memory_maintenance_loop，本分支只留画像
            # 提炼（两件事历史上挤在同一个计数分支里，现各归各处、节奏不变）。
            if identity_counter >= 300:
                identity_counter = 0
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
                except Exception as _ipe:
                    # B2（2026-09-06）：身份画像提炼失败不得完全静默——补日志以免「静默停摆」难定位。
                    _logger.warning("Identity profile extraction failed: %s", _ipe)
            # 状态八维惰性回落 + 趋势快照（每 1h 兜底结算并写 character_state_history；读时已惰性结算）
            if state_decay_counter >= 3600:
                state_decay_counter = 0
                from app.application.character_state_service import drift_all_character_states
                spawn_background(drift_all_character_states(), name="sched-state-drift")

            # 私聊文件保留 5 天 / 语音 14 天 / 事件流水保留（2026-09-26 批次 PT：
            # 判据由「进程内 tick 计数」改为持久化台账 —— 计数会在重启/卡顿重建后归零）
            from app.scheduling.periodic_state import run_if_due
            await run_if_due("file_cleanup", FILE_CLEANUP_INTERVAL, _cleanup_files_tick, reason="tick")

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
                        from app.scheduling.daily_reflection import run_daily_reflections
                        await run_daily_reflections()
                    except Exception as e:
                        _logger.warning("Daily reflections error: %s", e)
                    _reflection_done_today = True

            # 日终记忆维护（P0-5，2026-08-16：23:00 后触发一次）：日摘要补生成 + 去重 + 置顶摘要补生成
            if memory_counter >= 600:
                memory_counter = 0
                if local_hour >= 23 and not _memory_maintenance_done_today:
                    try:
                        from app.scheduling.daily_memory_maintenance import run_daily_memory_maintenance
                        await run_daily_memory_maintenance()
                    except Exception as e:
                        _logger.warning("Daily memory maintenance error: %s", e)
                    _memory_maintenance_done_today = True

                # 群记忆日终合并收敛（#72 PR-C P5，2026-09-16：23:00 后触发一次，受 group_memory_compact 闸控）
                # 与记忆维护同一段（memory_counter>=600）；每天最多跑一次；关 flag 时零行为变化。
                if local_hour >= 23 and not _group_memory_compact_done_today:
                    try:
                        from app.agent.loop import AGENT_FLAGS
                        if AGENT_FLAGS.get("group_memory_compact", False):
                            from app.memory.group_memory import compact_group_memories
                            spawn_background(compact_group_memories(), name="sched-group-memory-compact")
                    except Exception as e:
                        _logger.warning("Group memory compact schedule error: %s", e)
                    _group_memory_compact_done_today = True

            # 前瞻约定时效治理（2026-09-13 ②；2026-09-15 扩到 cue，plans #72；2026-09-16 批次一任务1/2）：
            # 每小时把超窗/跨天/超龄的 pending 约定置 stale——promise 走 due_end 活性窗口（2h，日期型 23:59 豁免、
            # 只在当天有效、跨天作废），cue 走「日期型跨天 + 无 due 30 天」，无 due 的 promise 同样按 30 天超龄清退。
            # 留痕不删，仍可检索/回忆，但不进主动提起/线索注入。幂等、异常隔离。
            await run_if_due("pis_stale", PIS_STALE_INTERVAL, _pis_stale_tick, reason="tick")

            # 控制台删号·回收站到期自动清除（第二期第二批，2026-09-24，flag 默认关=零行为）：
            # 每 10 分钟看一眼低峰窗口与到期账号；关 flag / 窗口外 / 未到间隔都立刻返回不查库，
            # 满足才交给 account_purge.purge_account 清除（进程内串行 + 批间让出事件循环）。
            # 经 spawn_background 派发不阻塞主循环；account_purge_scheduler 内部 _PURGE_LOCK 保证
            # 同一时刻只跑一个（上一拍没跑完则本拍直接跳过）。异常一律隔离，绝不掀翻主循环。
            if purge_counter >= ACCOUNT_PURGE_CHECK_INTERVAL:
                purge_counter = 0
                try:
                    from app.application.account_purge_scheduler import tick as _purge_tick
                    spawn_background(_purge_tick(), name="sched-account-purge")
                except Exception as e:
                    _logger.warning("Account purge scheduler tick error: %s", e)

            # 纪念日检查（Phase C Shared Memory）：每日一次（原 _check_anniversaries_today 未接线死代码，2026-08-17 接入）
            if _last_anniv_date != date.today():
                _last_anniv_date = date.today()
                try:
                    from app.scheduling.scheduler import _check_anniversaries_today as _run_anniv
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
    from app.scheduling.arbiter import flush_storyline_items
    _logger.info("Storyline sender loop started")
    while _running:
        try:
            await flush_storyline_items()
        except Exception as e:
            _logger.warning("Storyline flush error: %s", e)
        from app.utils.supervisor import supervisor
        supervisor.heartbeat("storyline")
        await asyncio.sleep(STORYLINE_FLUSH_INTERVAL)


async def memory_maintenance_loop():
    """长周期记忆维护（记忆衰减 + AI 自主评星）独立循环 —— 不再挂主调度循环的 tick 计数。

    为什么要独立（2026-09-26 实测）：主循环单轮耗时并不稳定，同轮里要发主动消息时会直接在
    循环内 await 多次 LLM 调用，单轮从 31 秒涨到分钟级（arbiter 日志条数按小时
    06→3816 / 07→487 / 09→185 / 10→3）。后果有两个：① 单轮 >180 秒被监督者判 stalled，
    10:48 实测 `supervisor stall detected target=scheduler` 后取消重建；② 重建把主循环里所有
    tick 计数归零。挂在主循环上的长周期任务因此在忙时段严重延迟甚至长期不跑。
    本循环只做这一件事，主循环忙不忙与它无关；间隔见 MEMORY_MAINTENANCE_INTERVAL。
    """
    from app.memory.maintenance_schedule import run_if_due
    _logger.info("Memory maintenance loop started (interval=%ds)", MEMORY_MAINTENANCE_INTERVAL)
    while _running:
        await asyncio.sleep(MEMORY_MAINTENANCE_INTERVAL)
        from app.utils.supervisor import supervisor
        supervisor.heartbeat("memory_maintenance")
        try:
            if await run_if_due(reason="loop"):
                _logger.info("Memory maintenance executed by independent loop")
        except Exception as e:
            # 异常隔离：单次失败只记 WARNING，绝不掀翻循环（判据在状态文件里，下一拍再问会补上）
            _logger.warning("Memory maintenance loop error: %s", e)


def start():
    """启动调度器（由 lifespan 调用）：登记到 supervisor 统一监督，支持崩溃/卡死后自愈重建。

    对外语义不变（start()/is_running() 签名与含义保持）；三个常驻 loop 由 supervisor 重建，
    并每轮上报心跳供 /liveness 判断「是否还在前进」。
    """
    global _scheduler_task, _storyline_task
    from app.utils.supervisor import supervisor

    # 适配器工厂：把现有协程包成「每次重建都重新读全局 _running」的工厂；
    # 同时在每次启动时刷新模块级 task 引用，使 is_running() 始终反映当前被监督的存活 task。
    async def _sched_factory():
        global _running, _scheduler_task
        _running = True
        _scheduler_task = asyncio.current_task()
        await scheduler_loop()

    async def _story_factory():
        global _running, _storyline_task
        _running = True
        _storyline_task = asyncio.current_task()
        await storyline_sender_loop()

    async def _maintenance_factory():
        global _running
        _running = True
        await memory_maintenance_loop()

    supervisor.register("scheduler", _sched_factory, stall_sec=180)  # 30s TICK × 6
    # 阈值理由见 MEMORY_MAINTENANCE_STALL_SEC 处注释（到期那一拍要 await 整轮维护）
    supervisor.register("memory_maintenance", _maintenance_factory,
                        stall_sec=MEMORY_MAINTENANCE_STALL_SEC)
    supervisor.register("storyline", _story_factory, stall_sec=60)   # 3s 间隔，60s 无心跳即卡
    supervisor.start()
    # 回填模块级 task 引用，兼容旧代码对模块全局 _scheduler_task/_storyline_task 的读取
    _scheduler_task = supervisor._targets["scheduler"].task
    _storyline_task = supervisor._targets["storyline"].task
    _logger.info("Scheduler tasks registered under supervisor")


def stop():
    """停止调度器（同步壳，保留旧签名供非 async 调用；行为等价 await stop_async()）。

    main.py lifespan 是 async，推荐改用 await stop_async()。
    """
    global _running
    _running = False
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(stop_async())
            return
    except Exception as _se:
        # B2：同步壳在无事件循环时降级（此时 stop_async 由调用方负责），仍留痕便于排查
        _logger.debug("Scheduler sync stop skipped (no running loop): %s", _se)


async def stop_async():
    """异步停止调度器（推荐在 async lifespan 中 await，确保 supervisor 关停时序完整）。"""
    global _running
    _running = False
    from app.utils.supervisor import supervisor
    await supervisor.stop()


def is_running() -> bool:
    """调度器是否在运行"""
    return _running and _scheduler_task is not None and not _scheduler_task.done()
