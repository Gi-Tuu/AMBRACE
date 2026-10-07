# -*- coding: utf-8 -*-
"""架构地图断点 #1 样板回归：domain/emotion/care.py 去 IO，IO 经端口注入。

覆盖三件事：
1. care 的三个入口在**假端口**下可完整单测（登记 / 采集 / 关怀每条分支：免打扰、当日限额、
   最小间隔、任务态、无会话、挡位 1/2、生成异常回退、空文本回退、素材异常降级）；
   判定顺序、阈值、hint 文案与返回结构对齐重构前的 care.py 原实现（逐字断言）。
2. 未注入端口且没有兼容钩子时抛 CarePortsNotInjected —— 既不 fail-open 也不 fail-closed。
3. 生产实现（app/application/emotion_care_ports.py）与旧行为等价：
   「当日已发计数」「主动消息日志写入」「生成失败回退」三条路径跑真实 SQL。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；真 SQL 用例走 tests/_dbclone.py 的
临时文件库（页级克隆模板库）+ monkeypatch 会话工厂，不触碰 backend/data 与生产库。
"""
import ast
import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

import app.domain.emotion.care as care_mod
from app.domain.emotion.ports import CareCharacterView, CarePortsNotInjected, CareTaskView

_IO_PREFIXES = ("app.db", "app.models", "app.agent", "app.application", "app.scheduling",
                "app.memory", "app.utils.dnd", "sqlalchemy")


# ─────────────────── 假端口（记录调用顺序 + 可按方法名抛异常）───────────────────

class _FakePorts:
    def __init__(self, **overrides):
        self.calls: list = []
        self.proactive_on = True
        self.pending_exists = False
        self.created: list = []
        self.task = CareTaskView(id=5, status="pending", trigger_msg="今天好累")
        self.finished: list = []
        self.cancel_windows: list = []
        self.fetch_windows: list = []
        self.due_rows: list = []
        self.daily = 0
        self.prev_care_at = None
        self.dnd = False
        self.char = CareCharacterView(id=3, name="小阳", personality="活泼")
        self.session_id = 99
        self.recent: list = []
        self.recent_limit = None
        self.identity = "你是小阳，性格活泼。"
        self.persona = ""
        self.weather = ""
        self.guard = "【时空纪律】GUARD\n"
        self.level = 2
        self.reply = "别难过了，我一直都在。"
        self.sent: list = []
        self.raise_on: set = set()
        self.llm_kwargs = None
        for k, v in overrides.items():
            setattr(self, k, v)

    def _hit(self, name):
        self.calls.append(name)
        if name in self.raise_on:
            raise RuntimeError(f"boom:{name}")

    async def proactive_enabled(self, character_id):
        self._hit("proactive_enabled")
        return self.proactive_on

    async def has_pending_care_task(self, user_id, character_id):
        self._hit("has_pending_care_task")
        return self.pending_exists

    async def create_care_task(self, *, user_id, character_id, trigger_msg, due_at):
        self._hit("create_care_task")
        self.created.append({"user_id": user_id, "character_id": character_id,
                             "trigger_msg": trigger_msg, "due_at": due_at})

    async def load_care_task(self, task_id):
        self._hit("load_care_task")
        return self.task

    async def finish_care_task(self, task_id, status):
        self._hit("finish_care_task")
        self.finished.append((task_id, status))

    async def cancel_stale_care_tasks(self, now, stale_before):
        self._hit("cancel_stale_care_tasks")
        self.cancel_windows.append((now, stale_before))

    async def fetch_due_care_tasks(self, now, stale_before):
        self._hit("fetch_due_care_tasks")
        self.fetch_windows.append((now, stale_before))
        return self.due_rows

    async def daily_care_count(self, character_id):
        self._hit("daily_care_count")
        return self.daily

    async def last_care_at(self, character_id):
        self._hit("last_care_at")
        return self.prev_care_at

    async def user_in_dnd(self, user_id):
        self._hit("user_in_dnd")
        return self.dnd

    async def load_character(self, character_id):
        self._hit("load_character")
        return self.char

    async def latest_session_id(self, user_id, character_id):
        self._hit("latest_session_id")
        return self.session_id

    async def recent_messages(self, session_id, limit=6):
        self._hit("recent_messages")
        self.recent_limit = limit
        return list(self.recent)

    async def build_identity_prompt(self, character_id, user_id):
        self._hit("build_identity_prompt")
        return self.identity

    async def build_active_persona(self, character_id, user_id):
        self._hit("build_active_persona")
        return self.persona

    async def weather_line(self, user_id):
        self._hit("weather_line")
        return self.weather

    async def state_guard_block(self, character_id, user_id):
        self._hit("state_guard_block")
        return self.guard

    async def reasoning_level(self, character_id):
        self._hit("reasoning_level")
        return self.level

    async def chat_completion(self, *, messages, temperature, max_tokens, task, user_id):
        self._hit("chat_completion")
        self.llm_kwargs = {"messages": messages, "temperature": temperature,
                           "max_tokens": max_tokens, "task": task, "user_id": user_id}
        return self.reply

    async def send_care_message(self, *, session_id, character_id, user_id, content,
                                message_type, extra_meta=None):
        self._hit("send_care_message")
        self.sent.append({"session_id": session_id, "character_id": character_id,
                          "user_id": user_id, "content": content,
                          "message_type": message_type, "extra_meta": extra_meta})


def _hint(identity, persona_block, guard, weather, trigger):
    """按重构前 care.py 的拼接顺序复算期望 hint（逐字口径）。"""
    return (
        f"{identity}\n"
        f"{persona_block}"
        + guard
        + (f"{weather}\n" if weather else "")
        + f"用户刚才跟你说：「{trigger}」——听起来心情不太好。\n"
        "过了一阵子，你主动关心他一句：1-2 句话，口语化，像真的在意他。\n"
        "多共情、少讲道理；不要出现'检测情绪''系统通知'这类字眼。"
    )


# ─────────────────────────── 1. 端口注入契约 ───────────────────────────

def test_care模块顶层零IO依赖():
    """care.py 的模块级 import 不得出现 DB/ORM/LLM/上层模块（断点 #1 目标口径）。"""
    tree = ast.parse(inspect.getsource(care_mod))
    tops = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            tops += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            tops.append(node.module or "")
    bad = [m for m in tops if any(m == p or m.startswith(p + ".") for p in _IO_PREFIXES)]
    assert not bad, f"care.py 顶层仍有 IO 依赖：{bad}"
    assert "sqlalchemy" not in tops


def test_未注入端口抛清晰错误且不静默降级():
    """三个入口未注入 ports 且兼容钩子为空 → CarePortsNotInjected（不查库、不发消息）。"""
    assert care_mod.async_session_factory is None
    assert care_mod._user_in_dnd_period is None
    assert care_mod._daily_count is None
    assert care_mod._last_care_at is None
    for coro in (
        care_mod.run_emotion_care(3, 4, 5),
        care_mod.register_care_task(4, 3, "今天好累"),
        care_mod.collect_care_events(),
    ):
        with pytest.raises(CarePortsNotInjected) as ei:
            asyncio.run(coro)
        assert "emotion_care_ports" in str(ei.value)


def test_兼容钩子被替换时走兼容端口(monkeypatch):
    """迁移期兜底：旧测试桩只替换 care 模块里的四个名字，此时 _resolve_ports 给出兼容端口。"""
    seen = {}

    class _R:
        def scalar(self):
            return 7

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt, *a, **kw):
            return _R()

    async def _daily(db, character_id):
        seen["daily"] = (db, character_id)
        return 7

    async def _last(db, character_id):
        return None

    async def _dnd(db, user_id):
        return False

    monkeypatch.setattr(care_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(care_mod, "_daily_count", _daily)
    monkeypatch.setattr(care_mod, "_last_care_at", _last)
    monkeypatch.setattr(care_mod, "_user_in_dnd_period", _dnd)
    assert care_mod._legacy_hooks_installed() is True
    ports = care_mod._resolve_ports(None)
    assert isinstance(ports, care_mod._LegacyCarePorts)
    assert asyncio.run(ports.daily_care_count(3)) == 7
    assert seen["daily"][1] == 3  # 旧签名 (db, character_id) 保留
    assert asyncio.run(ports.last_care_at(3)) is None
    assert asyncio.run(ports.user_in_dnd(4)) is False


def test_兼容钩子未齐时仍然报错():
    """只替换部分钩子不算兼容形态——必须四个都在，否则仍清晰报错（不半吊子放行）。"""
    orig = care_mod.async_session_factory
    try:
        care_mod.async_session_factory = lambda: None
        assert care_mod._legacy_hooks_installed() is False
        with pytest.raises(CarePortsNotInjected):
            care_mod._resolve_ports(None)
    finally:
        care_mod.async_session_factory = orig


# ─────────────────── 2. 登记链路（纯判定 + 假端口）───────────────────

def test_登记_主动开关关闭直接返回False():
    ports = _FakePorts(proactive_on=False)
    assert asyncio.run(care_mod.register_care_task(4, 3, "今天好累", ports=ports)) is False
    assert ports.calls == ["proactive_enabled"]
    assert ports.created == []


def test_登记_同角色已有pending任务则跳过():
    ports = _FakePorts(pending_exists=True)
    assert asyncio.run(care_mod.register_care_task(4, 3, "今天好累", ports=ports)) is False
    assert ports.calls == ["proactive_enabled", "has_pending_care_task"]
    assert ports.created == []


def test_登记_延迟15到45分钟且正文截断200字(monkeypatch):
    ports = _FakePorts()
    monkeypatch.setattr(care_mod.random, "randint", lambda a, b: 30)
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    ok = asyncio.run(care_mod.register_care_task(4, 3, "累" * 300, ports=ports))
    assert ok is True
    row = ports.created[0]
    assert row["user_id"] == 4 and row["character_id"] == 3
    assert len(row["trigger_msg"]) == 200
    assert timedelta(minutes=29) < row["due_at"] - before < timedelta(minutes=31)
    assert ports.calls == ["proactive_enabled", "has_pending_care_task", "create_care_task"]


def test_登记_空正文按空串落库(monkeypatch):
    ports = _FakePorts()
    monkeypatch.setattr(care_mod.random, "randint", lambda a, b: 15)
    assert asyncio.run(care_mod.register_care_task(4, 3, None, ports=ports)) is True
    assert ports.created[0]["trigger_msg"] == ""


# ─────────────────── 3. 采集链路（纯判定 + 假端口）───────────────────

def test_采集_每角色一条候选且事件结构逐字一致():
    ports = _FakePorts(due_rows=[
        CareTaskView(id=1, character_id=7, user_id=3, status="pending"),
        CareTaskView(id=2, character_id=7, user_id=3, status="pending"),
        CareTaskView(id=3, character_id=8, user_id=4, status="pending"),
    ])
    events = asyncio.run(care_mod.collect_care_events(ports=ports))
    assert events == [
        {"type": "emotion_care", "priority": 1,
         "candidate": {"character_id": 7, "user_id": 3, "task_id": 1}},
        {"type": "emotion_care", "priority": 1,
         "candidate": {"character_id": 8, "user_id": 4, "task_id": 3}},
    ]


def test_采集_先作废超24小时任务再用同一窗口取到期():
    ports = _FakePorts()
    assert asyncio.run(care_mod.collect_care_events(ports=ports)) == []
    assert ports.calls == ["cancel_stale_care_tasks", "fetch_due_care_tasks"]
    now, stale_before = ports.cancel_windows[0]
    assert now.tzinfo is None and stale_before.tzinfo is None
    assert now - stale_before == timedelta(hours=care_mod.TASK_TTL_HOURS)
    assert ports.fetch_windows == ports.cancel_windows


# ─────────────────────────── 4. 关怀链路分支 ───────────────────────────

def test_关怀_免打扰直接返回不查限额():
    ports = _FakePorts(dnd=True)
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.calls == ["user_in_dnd"]
    assert ports.finished == [] and ports.sent == []


def test_关怀_当日满2条取消任务并停止():
    ports = _FakePorts(daily=care_mod.MAX_PER_DAY)
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.finished == [(5, "cancelled_quota")]
    assert "chat_completion" not in ports.calls and ports.sent == []


def test_关怀_距上次不足3小时不发送也不改任务态():
    ports = _FakePorts(prev_care_at=datetime.now(timezone.utc).replace(tzinfo=None)
                       - timedelta(hours=care_mod.MIN_INTERVAL_HOURS - 1))
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.finished == [] and ports.sent == []


def test_关怀_上次时间带tz也按naive比较后放行():
    ports = _FakePorts(prev_care_at=datetime.now(timezone.utc) - timedelta(hours=4))
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is True
    assert ports.sent and ports.finished == [(5, "done")]


def test_关怀_任务缺失或非pending直接返回():
    ports = _FakePorts(task=None)
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.calls[-1] == "load_care_task" and ports.finished == []
    ports2 = _FakePorts(task=CareTaskView(id=5, status="done", trigger_msg="x"))
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports2)) is False
    assert ports2.sent == []


def test_关怀_无活跃会话取消任务():
    ports = _FakePorts(session_id=None)
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.finished == [(5, "cancelled_quota")]
    assert "chat_completion" not in ports.calls


def test_关怀_成功链路提示词与发送逐字对齐旧实现():
    ports = _FakePorts(persona="你们已认识 30 天", weather="今天北京 12℃ 多云")
    ok = asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports))
    assert ok is True
    kw = ports.llm_kwargs
    assert kw["temperature"] == 0.9 and kw["max_tokens"] == 256 and kw["task"] == "emotion"
    assert kw["messages"][1]["content"] == _hint(
        ports.identity, f"{ports.persona}\n", ports.guard, ports.weather, "今天好累")
    assert kw["messages"][0]["content"] == "直接输出要说的话，不要加引号和标注。"
    assert "include_reasoning" not in kw
    assert ports.sent == [{
        "session_id": 99, "character_id": 3, "user_id": 4,
        "content": ports.reply, "message_type": "emotion_care", "extra_meta": None,
    }]
    assert ports.finished == [(5, "done")]


def test_关怀_判定顺序与旧实现一致():
    ports = _FakePorts()
    asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports))
    assert ports.calls == [
        "user_in_dnd", "daily_care_count", "last_care_at", "load_care_task", "load_character",
        "latest_session_id",
        # A32（2026-10-07）：会话确定后、拼提示词前先重取现状（剧情推进即取消）
        "recent_messages",
        "build_identity_prompt", "build_active_persona", "weather_line",
        "state_guard_block", "reasoning_level", "chat_completion", "send_care_message",
        "finish_care_task",
    ]
    assert ports.recent_limit == care_mod.CARE_RECENT_LIMIT


def test_关怀_挡位1换system引导且不回传reasoning():
    ports = _FakePorts(level=1)
    asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports))
    assert ports.llm_kwargs["messages"][0]["content"].startswith("先在心里简短想一下怎么说合适")
    assert "include_reasoning" not in ports.llm_kwargs
    assert ports.sent[0]["extra_meta"] is None


def test_关怀_生成异常回退False且不动任务态():
    ports = _FakePorts(raise_on={"chat_completion"})
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.finished == [] and ports.sent == []


def test_关怀_生成空或过短文本取消任务不发送():
    ports = _FakePorts(reply="  ")
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.finished == [(5, "cancelled_quota")] and ports.sent == []
    ports2 = _FakePorts(reply="好")
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports2)) is False
    assert ports2.finished == [(5, "cancelled_quota")] and ports2.sent == []


def test_关怀_护栏块异常按旧行为整体回退False():
    ports = _FakePorts(raise_on={"state_guard_block"})
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.sent == [] and ports.finished == []


def test_关怀_人设块异常用性格兜底文案():
    ports = _FakePorts(raise_on={"build_identity_prompt"}, char=None)
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is True
    content = ports.llm_kwargs["messages"][1]["content"]
    assert content.startswith("你是我，性格友善。\n")  # char 缺失 → 「我」+「友善」兜底


def test_关怀_persona与天气异常只降级不中断():
    ports = _FakePorts(raise_on={"build_active_persona", "weather_line"})
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is True
    assert ports.llm_kwargs["messages"][1]["content"] == _hint(
        ports.identity, "", ports.guard, "", "今天好累")


def test_关怀_超长正文只发500字():
    ports = _FakePorts(reply="啊" * 800)
    asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports))
    assert len(ports.sent[0]["content"]) == 500


# ─────────────────── 5. 生产实现与旧行为等价（真实 SQL）───────────────────

async def _fake_identity(char, uid):
    return f"你是{getattr(char, 'name', '我')}，性格活泼。"


async def _noop_str(*a, **kw):
    return ""


async def _rl2(cid):
    return 2


async def _sid(uid, cid):
    return 91


@pytest.fixture()
def care_db(monkeypatch, tmp_path):
    """临时文件库 + 生产端口/相关模块的会话工厂接到该库（不触碰 backend/data 与生产库）。"""
    from _dbclone import clone_engine, make_session_factory

    import app.application.emotion_care_ports as ecp
    import app.db.database as db_mod
    import app.scheduling.scheduler as sched_mod
    from app.models.chat import ChatSession
    from app.models.character import AICharacter
    from app.models.user import User

    engine = clone_engine(tmp_path / "care.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(ecp, "async_session_factory", factory)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(sched_mod, "async_session_factory", factory)

    async def _seed():
        # _dbclone 默认开 FK（生产同款 PRAGMA）：logs/messages 的父行逐层先建
        async with factory() as db:
            db.add(User(id=3, username="u7_care", nickname="关怀用户"))
            db.add(AICharacter(id=13, user_id=3, name="小阳", personality="活泼"))
            db.add(AICharacter(id=14, user_id=3, name="小冰", personality="安静"))
            db.add(ChatSession(id=91, user_id=3, character_id=13, title="t"))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    asyncio.run(engine.dispose())


def _patch_llm(monkeypatch, reply=None, boom=False):
    """把 LLM 出口打桩（生产端口内部是函数级 import，patch 定义模块即生效）。"""
    import app.application.push_service as push_mod

    async def _text(**kw):
        return reply

    async def _crash(**kw):
        raise RuntimeError("llm down")

    async def _noop_push(*a, **kw):
        return None

    monkeypatch.setattr(push_mod, "notify_user", _noop_push)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _crash if boom else _text)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _rl2)
    monkeypatch.setattr("app.agent.user_profile.build_role_prompt_block", _fake_identity)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _noop_str)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop_str)


async def _seed_task(factory, status="pending"):
    from app.models.agent import EmotionCareTask
    async with factory() as db:
        t = EmotionCareTask(user_id=3, character_id=13, trigger_msg="今天好累",
                           due_at=datetime.now(timezone.utc).replace(tzinfo=None),
                           status=status)
        db.add(t)
        await db.commit()
        return t.id


@pytest.mark.slow
def test_生产实现_当日已发计数与旧SQL等价(care_db):
    """北京日界 + 仅 emotion_care + 仅本角色：与重构前 care._daily_count 的查询逐条对齐。"""
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select

    from app.application.emotion_care_ports import production_care_ports as ports
    from app.models.character import ProactiveMessageLog

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async def _seed():
        async with care_db() as db:
            db.add_all([
                ProactiveMessageLog(character_id=13, session_id=91, message_type="emotion_care",
                                    content="a", created_at=now),
                ProactiveMessageLog(character_id=13, session_id=91, message_type="emotion_care",
                                    content="b", created_at=now),
                ProactiveMessageLog(character_id=13, session_id=91, message_type="memory_review",
                                    content="c", created_at=now),
                ProactiveMessageLog(character_id=13, session_id=91, message_type="emotion_care",
                                    content="d", created_at=now - timedelta(days=1)),
            ])
            await db.commit()

    async def _legacy_daily_count(character_id):
        """重构前 care.py 的原实现（对照基准，含原 SQL 与原会话形态）。"""
        cn_tz = timezone(timedelta(hours=8))
        today_start = datetime.now(cn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = today_start.astimezone(timezone.utc).replace(tzinfo=None)
        async with care_db() as db:
            return (await db.execute(
                sa_select(sa_func.count(ProactiveMessageLog.id)).where(
                    ProactiveMessageLog.character_id == character_id,
                    ProactiveMessageLog.message_type == "emotion_care",
                    ProactiveMessageLog.created_at >= today_start,
                )
            )).scalar() or 0

    asyncio.run(_seed())
    assert asyncio.run(ports.daily_care_count(13)) == asyncio.run(_legacy_daily_count(13)) == 2
    assert asyncio.run(ports.last_care_at(13)) is not None
    assert asyncio.run(ports.daily_care_count(14)) == 0


@pytest.mark.slow
def test_生产实现_主动消息日志写入与限额联动(care_db, monkeypatch):
    """send_care_message → send_to_session 落 proactive_message_logs，写进去即计入当日限额。"""
    from sqlalchemy import select as sa_select

    from app.application.emotion_care_ports import production_care_ports as ports
    from app.models.character import ProactiveMessageLog

    monkeypatch.setattr("app.application.push_service.notify_user", _noop_str)
    asyncio.run(ports.send_care_message(
        session_id=91, character_id=13, user_id=3,
        content="别难过了，我一直都在。", message_type="emotion_care", extra_meta=None,
    ))

    async def _dump():
        async with care_db() as db:
            rows = (await db.execute(
                sa_select(ProactiveMessageLog).where(
                    ProactiveMessageLog.character_id == 13,
                    ProactiveMessageLog.message_type == "emotion_care",
                )
            )).scalars().all()
            return [(r.session_id, r.content, r.extra_meta) for r in rows]

    assert asyncio.run(_dump()) == [(91, "别难过了，我一直都在。", None)]
    assert asyncio.run(ports.daily_care_count(13)) == 1


@pytest.mark.slow
def test_生产实现_生成失败回退与旧行为一致(care_db, monkeypatch):
    """真端口跑全链路：LLM 抛异常 → False、任务仍 pending、不落日志（与重构前逐条一致）。"""
    from app.application.emotion_care_ports import production_care_ports as ports
    from app.models.agent import EmotionCareTask

    task_id = asyncio.run(_seed_task(care_db))
    _patch_llm(monkeypatch, boom=True)

    assert asyncio.run(care_mod.run_emotion_care(13, 3, task_id, ports=ports)) is False

    async def _check():
        async with care_db() as db:
            t = await db.get(EmotionCareTask, task_id)
            return t.status, t.finished_at

    assert asyncio.run(_check()) == ("pending", None)


@pytest.mark.slow
def test_生产实现_成功链路置done并写主动消息日志(care_db, monkeypatch):
    """真端口全链路成功：ASCII 引号被剥掉、任务置 done、日志落库计入当日限额。"""
    from sqlalchemy import select as sa_select

    from app.application.emotion_care_ports import production_care_ports as ports
    from app.models.agent import EmotionCareTask
    from app.models.character import ProactiveMessageLog

    task_id = asyncio.run(_seed_task(care_db))
    _patch_llm(monkeypatch, reply='  "我在呢，要不要说说？"  ')

    assert asyncio.run(care_mod.run_emotion_care(13, 3, task_id, ports=ports)) is True

    async def _check():
        async with care_db() as db:
            t = await db.get(EmotionCareTask, task_id)
            logs = (await db.execute(
                sa_select(ProactiveMessageLog).where(
                    ProactiveMessageLog.message_type == "emotion_care")
            )).scalars().all()
            return t.status, t.finished_at is not None, [r.content for r in logs]

    status, finished, contents = asyncio.run(_check())
    assert (status, finished) == ("done", True)
    assert contents == ["我在呢，要不要说说？"]
    assert asyncio.run(ports.daily_care_count(13)) == 1


@pytest.mark.slow
def test_生产实现_满限额走取消分支(care_db, monkeypatch):
    """真端口：当日已发 2 条 → 新任务直接 cancelled_quota，不调用 LLM、不发消息。"""
    from app.application.emotion_care_ports import production_care_ports as ports
    from app.models.agent import EmotionCareTask

    monkeypatch.setattr("app.application.push_service.notify_user", _noop_str)
    calls = []

    async def _spy(**kw):
        calls.append(kw)
        return "不该被调用"

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _spy)
    task_id = asyncio.run(_seed_task(care_db))
    second_id = asyncio.run(_seed_task(care_db))
    assert asyncio.run(ports.daily_care_count(13)) == 0  # 干净起点

    # 用真端口写两条当日日志，再跑第三条 → 应走取消分支
    for t in (task_id, second_id):
        asyncio.run(ports.send_care_message(
            session_id=91, character_id=13, user_id=3,
            content=f"占位{t}", message_type="emotion_care", extra_meta=None))
    assert asyncio.run(ports.daily_care_count(13)) == care_mod.MAX_PER_DAY

    third_id = asyncio.run(_seed_task(care_db))
    assert asyncio.run(care_mod.run_emotion_care(13, 3, third_id, ports=ports)) is False
    assert calls == []

    async def _check():
        async with care_db() as db:
            t = await db.get(EmotionCareTask, third_id)
            return t.status

    assert asyncio.run(_check()) == "cancelled_quota"   # A37 批 1：满限额属环境类取消，与"剧情已推进"分开


@pytest.mark.slow
def test_生产实现_登记与采集读写与旧行为一致(care_db, monkeypatch):
    """真端口：登记去重/写入 + 采集窗口（超 24h 作废、每角色一条候选）。"""
    from app.application.emotion_care_ports import production_care_ports as ports

    monkeypatch.setattr("app.scheduling.triggers.proactive_enabled", _rl_true)
    monkeypatch.setattr(care_mod.random, "randint", lambda a, b: 20)

    assert asyncio.run(care_mod.register_care_task(3, 13, "今天好累", ports=ports)) is True
    assert asyncio.run(care_mod.register_care_task(3, 13, "还是累", ports=ports)) is False

    async def _rows():
        from app.models.agent import EmotionCareTask
        from sqlalchemy import select as sa_select
        async with care_db() as db:
            return (await db.execute(sa_select(EmotionCareTask))).scalars().all()

    rows = asyncio.run(_rows())
    assert len(rows) == 1 and rows[0].status == "pending"
    assert rows[0].trigger_msg == "今天好累"

    events = asyncio.run(care_mod.collect_care_events(ports=ports))
    assert events == []  # due_at 在 20 分钟后，未到期

    async def _make_due():
        from app.models.agent import EmotionCareTask
        from sqlalchemy import select as sa_select
        async with care_db() as db:
            t = (await db.execute(sa_select(EmotionCareTask))).scalars().all()[0]
            t.due_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
            db.add(EmotionCareTask(user_id=3, character_id=14, trigger_msg="过期",
                                  due_at=datetime.now(timezone.utc).replace(tzinfo=None)
                                  - timedelta(hours=25), status="pending"))
            db.add(EmotionCareTask(user_id=3, character_id=14, trigger_msg="到期",
                                  due_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                  status="pending"))
            await db.commit()

    asyncio.run(_make_due())
    events = asyncio.run(care_mod.collect_care_events(ports=ports))
    assert {(e["candidate"]["character_id"], e["type"], e["priority"]) for e in events} == {
        (13, "emotion_care", 1), (14, "emotion_care", 1)}
    assert len(events) == 2  # 角色 14 两条只出一条候选

    async def _stale_status():
        from app.models.agent import EmotionCareTask
        from sqlalchemy import select as sa_select
        async with care_db() as db:
            rows2 = (await db.execute(
                sa_select(EmotionCareTask).where(EmotionCareTask.trigger_msg == "过期")
            )).scalars().all()
            return [(r.status, r.finished_at is not None) for r in rows2]

    assert asyncio.run(_stale_status()) == [("cancelled", True)]


async def _rl_true(character_id):
    return True
