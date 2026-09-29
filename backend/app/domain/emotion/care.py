"""AI 情绪关怀（2026-08-05）：用户低落 → 角色延迟主动关心。

数据流：
- register_care_task：聊天检测到低落情绪时登记任务（due_at = now + 15~45 分钟随机延迟）
- collect_care_events：arbiter tick 扫描到期 pending 任务（每角色 1 条候选，priority=1）
- run_emotion_care：限额/免打扰/会话检查 → LLM 生成关怀消息 → send_to_session 发送 → 任务置 done
- 护栏：每角色每日 <=2 条、同角色最小间隔 3h、免打扰不发、无活跃会话取消、超 24h 自动作废

架构地图断点 #1 样板（2026-09-29）：本模块只做判定与 prompt 组装，不再直接 import
DB / ORM 实体 / LLM / 发送出口；这些 IO 经 EmotionCarePorts（app/domain/emotion/ports.py）
由上层注入，生产实现 = app/application/emotion_care_ports.production_care_ports。
接线：agent/internal_runner.py（run_internal("emotion_care")）、application/chat_service.py、
scheduling/sources/emotion_care.py。未注入且没有兼容钩子时抛 CarePortsNotInjected，
既不 fail-open 也不 fail-closed，问题当场暴露。
"""
import random
from datetime import datetime, timedelta, timezone

from app.domain.emotion.ports import (
    CareCharacterView,
    CarePortsNotInjected,
    CareTaskView,
    EmotionCarePorts,
)
from app.utils.logger import get_logger

_logger = get_logger("scheduler.emotion_care")

CARE_TYPE = "emotion_care"
MAX_PER_DAY = 2
MIN_INTERVAL_HOURS = 3
DELAY_MIN_MINUTES = 15
DELAY_MAX_MINUTES = 45
TASK_TTL_HOURS = 24


# ── 迁移期兼容钩子（断点 #1 兜底，生产调用方请勿依赖）────────────────────────
# 这四个名字是旧调用面的 monkeypatch 点（现存的只有 tests/test_d2_df.py 情绪关怀用例）：
# 旧形态是「care 模块自己开会话 + 模块内私有查询函数」。默认全部为 None＝未安装，
# 一旦被替换，_resolve_ports 会走 _LegacyCarePorts 并打 warning，行为与改动前一致。
# 该用例改成显式注入假端口后，本段（含 _LegacyCarePorts）整体删除。
async_session_factory = None      # 旧：app.db.database.async_session_factory
_user_in_dnd_period = None        # 旧：app.utils.dnd.user_in_dnd_period(db, user_id)
_daily_count = None               # 旧：care 内 _daily_count(db, character_id)
_last_care_at = None              # 旧：care 内 _last_care_at(db, character_id)


def _legacy_hooks_installed() -> bool:
    return all(hook is not None for hook in (
        async_session_factory, _user_in_dnd_period, _daily_count, _last_care_at,
    ))


class _LegacyCarePorts:
    """兼容端口：DB 相关操作转接到上面四个钩子，其余操作原样转发给生产实现。

    只在钩子被替换（既有测试桩形态）时启用；ORM 实体的取用也是旧形态的一部分，
    因此这里保留惰性 import，删除本类时一起消失。
    """

    def __init__(self, delegate):
        self._delegate = delegate

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._delegate, name)

    async def user_in_dnd(self, user_id: int) -> bool:
        async with async_session_factory() as db:
            return await _user_in_dnd_period(db, user_id)

    async def daily_care_count(self, character_id: int) -> int:
        async with async_session_factory() as db:
            return await _daily_count(db, character_id)

    async def last_care_at(self, character_id: int):
        async with async_session_factory() as db:
            return await _last_care_at(db, character_id)

    async def load_care_task(self, task_id: int):
        from app.models.agent import EmotionCareTask
        async with async_session_factory() as db:
            task = await db.get(EmotionCareTask, task_id)
            if task is None:
                return None
            return CareTaskView(id=task.id, status=task.status, trigger_msg=task.trigger_msg)

    async def finish_care_task(self, task_id: int, status: str) -> None:
        from app.models.agent import EmotionCareTask
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with async_session_factory() as db:
            task = await db.get(EmotionCareTask, task_id)
            if task:
                task.status = status
                task.finished_at = now
                await db.commit()

    async def load_character(self, character_id: int):
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            char = await db.get(AICharacter, character_id)
            if char is None:
                return None
            return CareCharacterView(id=char.id, name=char.name, personality=char.personality)


def _resolve_ports(ports: EmotionCarePorts | None) -> EmotionCarePorts:
    """取端口实现：显式注入优先；兼容钩子被替换时兜底（打 warning）；否则清晰报错。"""
    if ports is not None:
        return ports
    if _legacy_hooks_installed():
        from app.application.emotion_care_ports import ProductionCarePorts
        _logger.warning("Emotion care ports not injected: falling back to legacy db hooks")
        return _LegacyCarePorts(ProductionCarePorts())
    raise CarePortsNotInjected(
        "emotion care IO 端口未注入：请显式传入 EmotionCarePorts（生产实现 "
        "app.application.emotion_care_ports.production_care_ports）——架构地图断点 #1"
    )


async def register_care_task(user_id: int, character_id: int, trigger_msg: str,
                             ports: EmotionCarePorts | None = None) -> bool:
    """用户发低落消息时登记一条延迟主动关怀任务（同角色已有 pending 任务则跳过）。"""
    ports = _resolve_ports(ports)
    if not await ports.proactive_enabled(character_id):
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if await ports.has_pending_care_task(user_id, character_id):
        return False
    due = now + timedelta(minutes=random.randint(DELAY_MIN_MINUTES, DELAY_MAX_MINUTES))
    await ports.create_care_task(
        user_id=user_id, character_id=character_id,
        trigger_msg=(trigger_msg or "")[:200], due_at=due,
    )
    _logger.info("Emotion care task registered char=%d", character_id)
    return True


async def collect_care_events(ports: EmotionCarePorts | None = None) -> list[dict]:
    """arbiter 事件源：到期且未过期的 pending 任务 → 每角色 1 条候选（priority=1）。"""
    ports = _resolve_ports(ports)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_before = now - timedelta(hours=TASK_TTL_HOURS)
    await ports.cancel_stale_care_tasks(now, stale_before)
    rows = await ports.fetch_due_care_tasks(now, stale_before)
    per_char: dict[int, CareTaskView] = {}
    for t in rows:
        if t.character_id not in per_char:
            per_char[t.character_id] = t
    return [
        {"type": CARE_TYPE, "priority": 1, "candidate": {
            "character_id": t.character_id, "user_id": t.user_id, "task_id": t.id,
        }}
        for t in per_char.values()
    ]


async def run_emotion_care(char_id: int, user_id: int, task_id: int,
                           ports: EmotionCarePorts | None = None) -> bool:
    """执行一次关怀：限额/免打扰/会话检查 → LLM 生成 → 发送 → 任务置 done。"""
    ports = _resolve_ports(ports)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if await ports.user_in_dnd(user_id):
        return False
    if await ports.daily_care_count(char_id) >= MAX_PER_DAY:
        _logger.info("Emotion care char=%d skipped: daily limit", char_id)
        await ports.finish_care_task(task_id, "cancelled")
        return False
    last = await ports.last_care_at(char_id)
    if last is not None:
        last = last.replace(tzinfo=None) if last.tzinfo else last
        if now_naive - last < timedelta(hours=MIN_INTERVAL_HOURS):
            return False
    task = await ports.load_care_task(task_id)
    if task is None or task.status != "pending":
        return False
    char = await ports.load_character(char_id)
    trigger_msg = task.trigger_msg
    session_id = await ports.latest_session_id(user_id, char_id)
    if session_id is None:
        await ports.finish_care_task(task_id, "cancelled")
        return False

    char_name = char.name if char else "我"
    personality = (char.personality or "友善")[:100] if char else "友善"
    try:
        identity = ""
        try:
            identity = await ports.build_identity_prompt(char_id, user_id)
        except Exception:
            identity = f"你是{char_name}，性格{personality}。"
        # 认知循环 v2.1：主动通道 persona 统一层（关系温度/剧情状态/进行中话题；开关关=空串）
        active_persona = ""
        try:
            active_persona = await ports.build_active_persona(char_id, user_id)
        except Exception:
            active_persona = ""
        persona_block = f"{active_persona}\n" if active_persona else ""
        # 天气注入（关怀时可自然结合当地天气）
        weather_line = ""
        try:
            weather_line = await ports.weather_line(user_id)
        except Exception:
            weather_line = ""
        # C16 批次A（2026-09-25）：护栏块＝【当前现状】（锚＝app.memory.current_state 里的
        # current_user_state_anchor）+【时空纪律】（state_guard.STATE_GUARD_DISCIPLINE），
        # 置于 persona_block 之后、用户原话之前；取锚与文案唯一来源 scheduling/state_guard.py，
        # 本文件刻意不复制第二份文案（一致性由 tests/test_proactive_state_guard_a7.py 钉住）。
        # 断点 #1 后该护栏经 ports.state_guard_block 注入（实现仍在 scheduling/state_guard.py）。
        guard = await ports.state_guard_block(char_id, user_id)
        hint = (
            f"{identity}\n"
            f"{persona_block}"
            + guard
            + (f"{weather_line}\n" if weather_line else "")
            + f"用户刚才跟你说：「{trigger_msg}」——听起来心情不太好。\n"
            "过了一阵子，你主动关心他一句：1-2 句话，口语化，像真的在意他。\n"
            "多共情、少讲道理；不要出现'检测情绪''系统通知'这类字眼。"
        )
        _rl = await ports.reasoning_level(char_id)
        _msgs = [
            {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
            {"role": "user", "content": hint},
        ]
        # D2-C（2026-08-18）：情绪关怀关闭深度思考——统一走挡位 1/0 的 prompt 引导分支
        # （挡位 1 保留「先在心里简短想一下」引导；_emotion_reasoning 恒为空串，extra_meta 不再带 reasoning）
        _emotion_reasoning = ""
        if _rl == 1:
            _msgs[0] = {"role": "system", "content": "先在心里简短想一下怎么说合适，然后直接输出要说的话，不要加引号和标注。"}
        text = await ports.chat_completion(messages=_msgs, temperature=0.9, max_tokens=256,
                                          task="emotion", user_id=user_id)
        text = (text or "").strip().strip('"').strip("'")
        if not text or len(text) < 2:
            await ports.finish_care_task(task_id, "cancelled")
            return False
    except Exception as e:
        _logger.warning("Emotion care LLM failed char=%d: %s", char_id, e)
        return False

    _emotion_extra = None
    if _emotion_reasoning:
        import json as _json
        _emotion_extra = _json.dumps({"reasoning": _emotion_reasoning}, ensure_ascii=False)
    await ports.send_care_message(
        session_id=session_id, character_id=char_id, user_id=user_id,
        content=text[:500], message_type=CARE_TYPE,
        extra_meta=_emotion_extra,
    )
    await ports.finish_care_task(task_id, "done")
    _logger.info("Emotion care sent char=%d", char_id)
    return True


# ── 本样板已做的抽取（架构地图断点 #1 · domain 去 IO）────────────────────────
# 1. 新增纯类型层 app/domain/emotion/ports.py：EmotionCarePorts 协议（typing.Protocol，
#    无框架）+ CareTaskView / CareCharacterView 快照 + CarePortsNotInjected；只依赖 typing。
# 2. care.py 去掉全部顶层 IO import：原 app.db.database.async_session_factory、
#    app.models.character.{AICharacter, ProactiveMessageLog}、app.models.agent.EmotionCareTask、
#    app.utils.dnd.user_in_dnd_period，以及函数体内的 app.scheduling.triggers.proactive_enabled、
#    app.scheduling.scheduler.send_to_session、app.application.chat_service.get_latest_session_id、
#    app.agent.llm_client.{chat_completion, load_character_reasoning_level}、
#    app.agent.user_profile.build_role_prompt_block、app.agent.persona.build_active_channel_persona、
#    app.application.weather_service.get_user_weather_line、app.scheduling.state_guard 全部改为
#    经 ports 参数调用；判定顺序、阈值、分支、返回结构、hint 文案与日志文案逐字未动。
# 3. 新增生产实现 app/application/emotion_care_ports.py（原 IO 代码原样搬入，SQL 语义不变；
#    唯一结构变化是原本共用一个会话的几处查询改为各自开会话，无跨操作事务）。
# 4. 调用方接线（只改注入行）：agent/internal_runner.py 的 emotion_care 分支、
#    application/chat_service.py 的 register_care_task 调用、
#    scheduling/sources/emotion_care.py 的 collect_care_events 调用。
# 5. 遗留（下一批做，本样板刻意保留以便既有测试桩继续证明行为一致）：
#    care.py 里四个「迁移期兼容钩子」+ _LegacyCarePorts + _resolve_ports 中的那条
#    惰性 app.application import，唯一使用者是 tests/test_d2_df.py 的情绪关怀用例
#    （它 monkeypatch 的是 care 模块内部名）。该用例改为显式注入假端口后，本段整体删除，
#    care.py 即达到「domain 零 IO import」终态。
#
# 同类待做 domain 文件清单（只列文件名，本批未改）：
# - app/domain/emotion/timeline.py（顶层 import app.db.database + 3 个 ORM 实体，3 处开会话）
# - app/domain/relationship/decay.py（顶层 import app.db.database + CharacterState，1 处开会话）
# - app/domain/decision/layer.py（函数内 import app.db.database，1 处开会话）
# - app/domain/proactivity/pacing.py（函数内 import app.agent.loop.AGENT_FLAGS，跨层取开关）
