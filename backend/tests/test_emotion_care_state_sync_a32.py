# -*- coding: utf-8 -*-
"""A32（2026-10-07）情绪关怀到期执行前的「现状同步」＋ 危机 / 人设硬约束（B.3.4）。

真机现场（char=13，2026-10-06）：11:39 用户说「我脏了」→ 登记 15~45 分钟后主动关怀；11:43~12:54
剧情已推进到上锁 / 报案 / 去医院，12:15 到期执行仍拿**登记时刻**的旧 trigger 生成（``care.py``
把 trigger_msg 存进行、执行时只读这一行），结果发出「洗干净就行了，别嚎了」——既与「别清洗、
留证据」的处置直接矛盾，又与人设割裂。

本单三条改动，全部零 LLM 判定（只字面匹配）：
① 执行前重取该会话最近用户消息（新端口 ``recent_messages``），剧情已明显推进 ⇒ ``cancelled_story`` 不发；
② 危机/取证场景 ⇒ 提示词追加硬纪律（严禁建议清洗身体 / 漱口 / 更换或清洗衣物 / 丢弃物品）；
③ 输出闸门 ⇒ 命中呵斥或危机场景命中清洗类建议，按 persona 只重生成一次，仍违规即取消不发。

端口同步（派单 §1(3.4)）：协议 ``EmotionCarePorts``（domain/emotion/ports.py）与生产实现
（application/emotion_care_ports.py）一起加 ``recent_messages``；兼容层 ``_LegacyCarePorts``
经 ``__getattr__`` 转发，无需另改。

（项目未装 pytest-asyncio，统一 asyncio.run；假端口档零 DB，真 SQL 档走 tests/_dbclone.py 临时库。）
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

import app.domain.emotion.care as care_mod
from _dbclone import clone_engine, make_session_factory
from app.domain.emotion.ports import CareCharacterView, CareTaskView

_TRIGGER = "我脏了，感觉恶心"


class _Ports:
    """最小假端口：只记录关怀链路用得上的调用，chat_completion 按次序吐预设回复（可含异常哨兵）。"""

    def __init__(self, *, trigger=_TRIGGER, recent=None, replies=None, session_id=99, level=2,
                 recent_booms=False):
        self.task = CareTaskView(id=5, status="pending", trigger_msg=trigger)
        self.recent = list(recent or [])
        self._replies = list(replies if replies is not None else ["我在呢，要不要说说？"])
        self.session_id = session_id
        self.level = level
        self.recent_booms = recent_booms
        self.char = CareCharacterView(id=3, name="小阳", personality="温柔")
        self.calls = []
        self.prompts = []
        self.temps = []
        self.sent = []
        self.finished = []
        self.recent_limit = None

    async def user_in_dnd(self, user_id):
        self.calls.append("user_in_dnd")
        return False

    async def daily_care_count(self, character_id):
        self.calls.append("daily_care_count")
        return 0

    async def last_care_at(self, character_id):
        self.calls.append("last_care_at")
        return None

    async def load_care_task(self, task_id):
        self.calls.append("load_care_task")
        return self.task

    async def finish_care_task(self, task_id, status):
        self.calls.append("finish_care_task")
        self.finished.append((task_id, status))

    async def load_character(self, character_id):
        self.calls.append("load_character")
        return self.char

    async def latest_session_id(self, user_id, character_id):
        self.calls.append("latest_session_id")
        return self.session_id

    async def recent_messages(self, session_id, limit=6):
        self.calls.append("recent_messages")
        self.recent_limit = limit
        if self.recent_booms:
            raise RuntimeError("db down")
        return list(self.recent)

    async def build_identity_prompt(self, character_id, user_id):
        self.calls.append("build_identity_prompt")
        return "你是小阳，性格温柔。"

    async def build_active_persona(self, character_id, user_id):
        self.calls.append("build_active_persona")
        return ""

    async def weather_line(self, user_id):
        self.calls.append("weather_line")
        return ""

    async def state_guard_block(self, character_id, user_id):
        self.calls.append("state_guard_block")
        return "【时空纪律】GUARD\n"

    async def reasoning_level(self, character_id):
        self.calls.append("reasoning_level")
        return self.level

    async def chat_completion(self, *, messages, temperature, max_tokens, task, user_id):
        self.calls.append("chat_completion")
        self.prompts.append(messages[-1]["content"])
        self.temps.append(temperature)
        if not self._replies:
            raise AssertionError("chat_completion 被多调了一次")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def send_care_message(self, *, session_id, character_id, user_id, content,
                                message_type, extra_meta=None):
        self.calls.append("send_care_message")
        self.sent.append(content)


# ─────────────────── 纯函数档（零 IO）───────────────────

def test_messages_after_trigger只算trigger之后的现状():
    """trigger 之后才是「现状」；trigger 已滑出窗口 ⇒ 整窗都算现状（宁可少发过时关怀）。"""
    f = care_mod._messages_after_trigger
    trig = "我脏了"
    assert f([trig, "我把门反锁了"], trig) == ["我把门反锁了"]
    assert f([trig], trig) == []
    # 前缀匹配（trigger 落库时截断到 200 字，窗口里是全文）
    assert f(["开头" + trig + "后续", "报警了"], trig) == ["报警了"]
    assert f(["a", "b"], "不在窗口里") == ["a", "b"]
    assert f([], trig) == []
    assert f(["a", "b"], "") == ["a", "b"]


def test_story_advanced_命中推进标志才算推进():
    adv = care_mod._story_advanced
    assert adv([_TRIGGER, "我已经报警了"], _TRIGGER) is True
    assert adv([_TRIGGER, "现在在医院门口"], _TRIGGER) is True
    assert adv([_TRIGGER, "事情处理完了"], _TRIGGER) is True
    assert adv([_TRIGGER, "我到家了"], _TRIGGER) is True
    # trigger **之前**同类话不算推进（那是登记关怀的由头本身）
    assert adv(["在医院门口站了一会儿", _TRIGGER], _TRIGGER) is False
    # 未命中标志 ⇒ 不取消（不能因为剧情里出现「医院」二字就一律不发关怀）
    assert adv([_TRIGGER, "还是有点难受"], _TRIGGER) is False
    assert adv([], _TRIGGER) is False


def test_evidence_stage_and_text_violated_rules():
    """取证场景判定 ＋ 输出违规判定：呵斥任何场景都判；清洗类只在取证场景判（避免误伤日常关怀）。"""
    assert care_mod._evidence_stage(_TRIGGER) is True
    assert care_mod._evidence_stage("今天好累", "保留了证据") is True
    assert care_mod._evidence_stage("今天好累", "想看电影") is False
    assert care_mod._care_text_violated("洗干净就行了，别嚎了", crisis=True) is True
    assert care_mod._care_text_violated("哭什么，睡一觉就好", crisis=False) is True   # 呵斥恒判
    assert care_mod._care_text_violated("你去漱个口吧", crisis=True) is True
    assert care_mod._care_text_violated("把衣服换了", crisis=True) is True
    assert care_mod._care_text_violated("别担心，我陪着你，先去报警好吗", crisis=True) is False
    # 「洗澡」这类日常建议在非取证场景不拦（关怀里「去洗个热水澡早点睡」是正常话）
    assert care_mod._care_text_violated("去洗个热水澡早点睡", crisis=False) is False


# ─────────────────── 链路档（假端口）───────────────────

def test_剧情已推进_取消任务不调LLM不发送():
    """派单 §1(3.1)：到期时剧情已推进 ⇒ finish(cancelled_story) 并返回，绝不拿 15~45 分钟前的旧 trigger 生成。"""
    ports = _Ports(recent=[_TRIGGER, "我把门锁上了", "我刚报完警", "现在在医院"])
    ok = asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports))
    assert ok is False
    assert "chat_completion" not in ports.calls and ports.sent == []
    assert ports.finished == [(5, "cancelled_story")]
    assert ports.recent_limit == care_mod.CARE_RECENT_LIMIT


def test_旧trigger已滑出窗口同样按推进取消():
    """trigger 已不在最近窗口（说明它之后至少推进了整窗）⇒ 整窗都是现状。"""
    ports = _Ports(recent=["到家了", "把事情处理完了", "现在好多了"])
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert ports.sent == [] and ports.finished == [(5, "cancelled_story")]


def test_取现状失败按fail_open照旧发关怀():
    """recent_messages 抛 ⇒ 不判推进（宁多发一条关怀，也不因 IO 抖动漏掉关怀），链路继续。"""
    ports = _Ports(recent=["我刚报完警"], recent_booms=True)
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is True
    assert ports.sent == ["我在呢，要不要说说？"]


def test_危机场景提示词追加硬纪律_非危机逐字不变():
    """派单 §1(3.2)：取证场景在 hint 末尾追加禁令；非危机时 hint 与改动前逐字相同（纯增量）。"""
    ports = _Ports(trigger=_TRIGGER, recent=[_TRIGGER, "还是难受"])
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is True
    body = ports.prompts[0]
    assert care_mod.CRISIS_EVIDENCE_GUARD in body
    assert "严禁建议清洗身体、漱口、更换或清洗衣物、丢弃任何物品" in body
    assert "保留证据" in body

    ports2 = _Ports(trigger="今天好累", recent=["今天好累", "还是累"])
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports2)) is True
    assert care_mod.CRISIS_EVIDENCE_GUARD not in ports2.prompts[0]
    assert ports2.prompts[0].endswith("不要出现'检测情绪''系统通知'这类字眼。")


def test_危机输出违规_按persona重生成一次后发送():
    """派单 §1(3.3) ＋ §5.4：第一句违规 ⇒ 只重生成一次；第二句合规 ⇒ 发的是第二句。"""
    ports = _Ports(
        recent=[_TRIGGER, "还是难受"],
        replies=["洗干净就行了，别嚎了", "我在呢，什么都别动，那些留着当证据，我陪你报警。"],
    )
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is True
    assert len(ports.prompts) == 2
    assert care_mod._PERSONA_RETRY_SUFFIX in ports.prompts[1]
    assert ports.temps == [0.9, 0.7]                       # 重生成降温一次
    assert len(ports.sent) == 1
    out = ports.sent[0]
    # 派单 §5.4 逐字口径：输出不含 洗 / 漱口 / 换
    for bad in ("洗", "漱口", "换"):
        assert bad not in out
    # 方向：陪伴 + 保留证据/求助
    assert ("我在" in out or "陪你" in out) and ("证据" in out or "报警" in out)


def test_重生成后仍违规_取消不发():
    """两次都违规 ⇒ 宁可不发（不发有害/割裂人设的话），任务置 cancelled_story。"""
    ports = _Ports(recent=[_TRIGGER, "还是难受"],
                   replies=["洗干净就行了，别嚎了", "去冲个澡把那些都扔了吧"])
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert len(ports.prompts) == 2 and ports.sent == []
    assert ports.finished == [(5, "cancelled_story")]


def test_非危机场景呵斥同样重生成_仍呵斥则取消():
    """呵斥类（「别嚎了 / 哭什么 / 没用」）与人设一致性挂钩，不限危机场景。"""
    ports = _Ports(trigger="今天好累", recent=["今天好累"],
                   replies=["哭什么，忍忍就过去了", "别嚎了，真没用"])
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is False
    assert len(ports.prompts) == 2 and ports.sent == []
    assert ports.finished == [(5, "cancelled_story")]

    ports2 = _Ports(trigger="今天好累", recent=["今天好累"],
                    replies=["哭什么，忍忍就过去了", "累坏了吧，我在这儿，想说什么都行。"])
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports2)) is True
    assert ports2.sent == ["累坏了吧，我在这儿，想说什么都行。"]
    assert ports2.finished == [(5, "done")]


def test_合规输出只调一次LLM不触重生成():
    ports = _Ports(recent=[_TRIGGER, "还是难受"])
    assert asyncio.run(care_mod.run_emotion_care(3, 4, 5, ports=ports)) is True
    assert ports.calls.count("chat_completion") == 1
    assert ports.finished == [(5, "done")]


def test_端口协议与生产实现同步声明recent_messages():
    """派单 §1(3.4)：新增端口要「协议 + 生产实现」一起补，别只改调用侧（兼容层靠 __getattr__ 转发）。"""
    import inspect

    from app.application.emotion_care_ports import ProductionCarePorts
    from app.domain.emotion.ports import EmotionCarePorts

    assert "recent_messages" in dir(EmotionCarePorts)
    assert callable(getattr(ProductionCarePorts, "recent_messages", None))
    assert list(inspect.signature(ProductionCarePorts.recent_messages).parameters) == [
        "self", "session_id", "limit",
    ]
    # 兼容层：未显式实现，靠 __getattr__ 转发到生产实现（所以不需要第三处改动）
    legacy = care_mod._LegacyCarePorts(ProductionCarePorts())
    assert "recent_messages" not in vars(legacy) and hasattr(legacy, "recent_messages")


# ─────────────────── 真 SQL 档：recent_messages 端口行为 ───────────────────

@pytest.fixture()
def care_sql_db(monkeypatch, tmp_path):
    """临时库 + 生产端口会话工厂接该库（不碰 backend/data 与生产库）。"""
    engine = clone_engine(os.path.join(str(tmp_path), "care.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.chat import ChatMessage, ChatSession
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=3, username="u_a32_care", nickname="关怀用户"))
            db.add(AICharacter(id=13, user_id=3, name="小阳", personality="温柔"))
            db.add(ChatSession(id=91, user_id=3, character_id=13))
            await db.commit()
        # 父行先提交再挂消息行（_dbclone 默认开 FK，与生产同款 PRAGMA）
        async with factory() as db:
            base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
            rows = [
                ("user", _TRIGGER, 0),
                ("ai", "你现在安全吗？", 1),
                ("user", "我把门锁上了", 2),
                ("ai", "需要我陪你报警吗", 3),
                ("user", "我已经报警了", 4),
                ("user", "现在在医院", 5),
                ("ai", "我在医院门口等你", 6),
            ]
            for sender, content, off in rows:
                db.add(ChatMessage(session_id=91, sender_type=sender, content=content,
                                   created_at=base + timedelta(minutes=off)))
            await db.commit()

    asyncio.run(_seed())
    import app.application.emotion_care_ports as ecp
    import app.db.database as db_mod
    monkeypatch.setattr(ecp, "async_session_factory", factory)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


@pytest.mark.slow
def test_生产端口recent_messages只取用户侧且按时间正序(care_sql_db):
    """端口契约（派单 §1(3.4)）：只看用户说的话、按时间正序、limit 取最近 N 条。"""
    from app.application.emotion_care_ports import production_care_ports as ports

    got = asyncio.run(ports.recent_messages(91, limit=6))
    assert got == [_TRIGGER, "我把门锁上了", "我已经报警了", "现在在医院"]   # 无 ai 行
    assert asyncio.run(ports.recent_messages(91, limit=2)) == ["我已经报警了", "现在在医院"]
    assert asyncio.run(ports.recent_messages(91, limit=1)) == ["现在在医院"]
    assert asyncio.run(ports.recent_messages(4242)) == []                    # 会话不存在 → 空
    # 域侧据此判推进：最新用户消息里「报警 / 在医院」在 trigger 之后 ⇒ 取消旧关怀
    assert care_mod._story_advanced(got, _TRIGGER) is True
