# -*- coding: utf-8 -*-
"""主动交流 P0 修复测试（2026-08-24）：

P0-1（S）motivation / state_trigger 补最近语境：
- collect_motivation_events 候选带 last_context（复用 get_last_messages），失败静默空串；
- _execute_rule_behavior 的 prompt 注入最近聊天（get_last_messages 5 条摘要）与进行中话题
  （build_active_channel_persona），并强制「承接最近聊的或与当前状态自然衔接，别突然换话题」。

P0-2（M）承接最近语境强制化 + 扩容：
- generate_proactive_event 主指令前插入「最近聊了什么」承接块；空语境用「（暂无最近聊天）」兜底；
- get_last_messages 默认 10 条 × 120 字（扩容截断）；
- active_topics 措辞由「可自然提起，别生硬」改为「优先承接进行中的话题，别生硬」；
- arbiter 排序加权纯函数 _context_sort_bonus（带 last_context 时 +0.05）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库参照既有测试风格）
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.scheduler import arbiter
from app.scheduler import state_triggers
from app.scheduler import message_generator as mg
from app.scheduler.arbiter import _context_sort_bonus, CONTEXT_SORT_BONUS


# ---------------- P0-2：arbiter 排序加权纯函数 ----------------

def test_context_sort_bonus_with_context():
    assert _context_sort_bonus({"last_context": "用户: hi"}) == CONTEXT_SORT_BONUS == 0.05


def test_context_sort_bonus_no_context():
    assert _context_sort_bonus({"last_context": ""}) == 0.0
    assert _context_sort_bonus({"other": 1}) == 0.0
    assert _context_sort_bonus(None) == 0.0


# ---------------- P0-1：motivation 候选带 last_context ----------------

def _active_char(**over):
    base = dict(
        character_id=11, user_id=1, character_name="小阳", character_bio="",
        character_personality="活泼", current_status="在家", relationship_summary="",
        nickname="用户", username="用户",
    )
    base.update(over)
    return [base]


def test_motivation_candidate_has_last_context(monkeypatch):
    """P0-1：动机候选补最近聊天语境（复用 get_last_messages），且候选携带 session_id"""
    async def _fake_active(*a, **k):
        return _active_char()

    async def _fake_motivation(cid):
        return 0.8

    async def _fake_sid(uid, cid):
        return 100

    async def _fake_last(sid, limit=5):
        return "用户: 今天天气不错\n你: 是啊，很适合出去走走"

    monkeypatch.setattr(arbiter, "get_active_characters", _fake_active)
    monkeypatch.setattr(arbiter, "_compute_motivation", _fake_motivation)
    monkeypatch.setattr("app.services.chat_service.get_latest_session_id", _fake_sid)
    monkeypatch.setattr("app.scheduler.triggers.get_last_messages", _fake_last)

    items = asyncio.run(arbiter.collect_motivation_events())
    assert len(items) == 1
    cand = items[0]["candidate"]
    assert items[0]["type"] == "motivation"
    assert cand["session_id"] == 100
    assert cand["last_context"] == "用户: 今天天气不错\n你: 是啊，很适合出去走走"


def test_motivation_candidate_last_context_failure_empty(monkeypatch):
    """P0-1：get_last_messages 抛异常时该候选 last_context 静默回退空串（不中断采集）"""
    async def _fake_active(*a, **k):
        return _active_char()

    async def _fake_motivation(cid):
        return 0.8

    async def _fake_sid(uid, cid):
        return 100

    async def _boom_last(sid, limit=5):
        raise RuntimeError("db down")

    monkeypatch.setattr(arbiter, "get_active_characters", _fake_active)
    monkeypatch.setattr(arbiter, "_compute_motivation", _fake_motivation)
    monkeypatch.setattr("app.services.chat_service.get_latest_session_id", _fake_sid)
    monkeypatch.setattr("app.scheduler.triggers.get_last_messages", _boom_last)

    items = asyncio.run(arbiter.collect_motivation_events())
    assert len(items) == 1
    assert items[0]["candidate"]["last_context"] == ""


# ---------------- P0-1：state_trigger prompt 注入最近语境 ----------------

def test_state_trigger_prompt_injects_recent_context(monkeypatch):
    """P0-1：状态触发私聊 prompt 注入最近聊天（get_last_messages）与进行中话题（build_active_channel_persona）"""
    captured = {}

    def _boom_factory():
        raise RuntimeError("no db")

    async def _no_post(*a, **k):
        return None

    async def _fake_sid(uid, cid):
        return 100

    async def _fake_last(sid, limit=5):
        return "用户: 今天天气不错\n你: 是啊，很适合出去走走"

    async def _fake_persona(cid, uid):
        return "关系温度（自然体现，别念数据）：信任80"

    async def _fake_count(cid):
        return 0

    async def _fake_chat(**kw):
        captured["messages"] = kw.get("messages")
        return "嗯，是挺适合出去走走的。"

    monkeypatch.setattr(state_triggers, "async_session_factory", _boom_factory)
    monkeypatch.setattr(state_triggers, "_post_trigger_notes", _no_post)
    monkeypatch.setattr("app.services.chat_service.get_latest_session_id", _fake_sid)
    monkeypatch.setattr("app.scheduler.triggers.get_last_messages", _fake_last)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _fake_persona)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat)
    monkeypatch.setattr("app.scheduler.arbiter.get_hourly_active_count", _fake_count)
    monkeypatch.setattr("app.scheduler.scheduler.send_to_session", _no_post)

    rule = state_triggers._RULE_BY_KEY["fatigue_high"]  # moment=False → 走私聊消息分支
    ok = asyncio.run(state_triggers._execute_rule_behavior(11, 1, rule, "疲惫=85；心情=40"))
    assert ok is True
    user_msg = captured["messages"][1]["content"]
    assert "你们最近在聊：" in user_msg
    assert "用户: 今天天气不错" in user_msg
    assert "信任80" in user_msg                      # 进行中话题/关系温度注入
    assert "承接最近聊的或与当前状态自然衔接" in user_msg
    assert "你的当前状态：疲惫=85；心情=40" in user_msg
    # 注入发生在「你的当前状态」之前
    assert user_msg.index("你们最近在聊：") < user_msg.index("你的当前状态：")


# ---------------- P0-2：generate_proactive_event 承接块 + active_topics 措辞 ----------------

class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult()


def _fake_session_factory():
    def _factory():
        return _FakeSession()
    return _factory


def _patch_mg_preloads(monkeypatch, persona=None):
    """mock 掉 generate_proactive_event 的全部前置查询与 LLM，捕获 messages"""
    if persona is None:
        persona = {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}

    async def _noop(*a, **k):
        return ""

    async def _noop_list(*a, **k):
        return []

    async def _persona(*a, **k):
        return persona

    captured = {}

    async def _fake_llm(**kw):
        captured["messages"] = kw.get("messages")
        return "你好呀！\n今天天气不错。"

    async def _fake_level(cid):
        return 0

    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _noop)
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
    monkeypatch.setattr("app.services.weather_service.get_user_weather_line", _noop)
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_session_factory)
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
    monkeypatch.setattr(mg, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg, "load_character_reasoning_level", _fake_level)
    return captured


def test_proactive_prompt_contains_continuation_block(monkeypatch):
    """P0-2：主指令前插入「最近聊了什么」承接块，要求承接现状"""
    captured = _patch_mg_preloads(monkeypatch)
    segments = asyncio.run(mg.generate_proactive_event(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家",
        last_context="用户: 今天天气不错\n你: 是啊，很适合出去走走",
    ))
    assert segments == ["你好呀！", "今天天气不错。"]
    prompt = captured["messages"][1]["content"]
    assert "先看最近聊了什么：" in prompt
    assert "用户: 今天天气不错" in prompt
    assert "新消息必须承接最近正在聊的或与当前语境一致" in prompt
    assert "（如'对了，你上次说的……'）" in prompt
    # 承接块在「请把这一件事写成」主指令之前
    assert prompt.index("先看最近聊了什么：") < prompt.index("请把这一件事写成一条连贯的消息")


def test_proactive_prompt_empty_context_fallback(monkeypatch):
    """P0-2：last_context 为空时承接块以「（暂无最近聊天）」兜底"""
    captured = _patch_mg_preloads(monkeypatch)
    asyncio.run(mg.generate_proactive_event(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家",
        last_context="",
    ))
    prompt = captured["messages"][1]["content"]
    assert "（暂无最近聊天）" in prompt


def test_proactive_prompt_active_topics_wording(monkeypatch):
    """P0-2：进行中话题措辞由「可自然提起，别生硬」改为「优先承接进行中的话题，别生硬」"""
    captured = _patch_mg_preloads(monkeypatch, persona={
        "cognitive": True, "relationship_state": "", "active_topics": "你们在聊周末去哪儿",
        "storyline_status": "无",
    })
    asyncio.run(mg.generate_proactive_event(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家", last_context="用户: 在吗",
    ))
    prompt = captured["messages"][1]["content"]
    assert "优先承接进行中的话题，别生硬" in prompt
    assert "你们在聊周末去哪儿" in prompt
    assert "可自然提起，别生硬" not in prompt


# ---------------- P0-2：get_last_messages 扩容（10 条 × 120 字） ----------------

@pytest.fixture()
def ctx_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch triggers 的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="poc_ctx_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.scheduler.triggers as trig_mod
    monkeypatch.setattr(trig_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def test_get_last_messages_default_expanded_limit(ctx_db):
    """P0-2：默认扩容为 10 条（limit=10），每条内容截断到 120 字，最新消息在后"""
    async def _main():
        from app.models.chat import ChatMessage
        from app.scheduler.triggers import get_last_messages
        async with ctx_db() as db:
            for i in range(15):
                db.add(ChatMessage(
                    session_id=1,
                    sender_type="user" if i % 2 == 0 else "ai",
                    content=("第%d条" % i) * 80,  # 远超 120 字，用于验证截断
                    created_at=datetime(2026, 8, 1) + timedelta(minutes=i),
                ))
            await db.commit()
        return await get_last_messages(1)

    out = asyncio.run(_main())
    lines = out.splitlines()
    assert len(lines) == 10                      # 默认扩容到 10 条
    assert all(len(l.split(":", 1)[1].strip()) <= 120 for l in lines)  # 每条截断到 120 字
    assert "第14条" in lines[-1]                  # 最新一条（i=14）在末尾
    assert "第0条" not in lines                    # 最早一条已被截断掉

# ---------------- P2：观测信号（trigger_reason 记录最近聊天语境长度 [ctx=N]） ----------------

class _FakeSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class _FakeFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self._session


def test_log_trigger_candidate_records_ctx_len(monkeypatch):
    """P2：候选带 last_context 时，trigger_reason 追加 [ctx=长度]（0 不附加）"""
    fs = _FakeSession()
    monkeypatch.setattr(arbiter, "async_session_factory", _FakeFactory(fs))
    item = {
        "type": "proactive_chat",
        "candidate": {
            "character_id": 1,
            "user_id": 1,
            "trigger_reason": "日常问候",
            "last_context": "用户: 早上好" * 20,
        },
    }
    asyncio.run(arbiter.log_trigger_candidate(item, executed=True))
    logs = [o for o in fs.added if getattr(o, "trigger_reason", None) is not None]
    assert logs, "应写入 ProactiveTriggerLog"
    assert "[ctx=140]" in logs[0].trigger_reason


def test_log_trigger_candidate_no_ctx_no_suffix(monkeypatch):
    """候选无 last_context 时，trigger_reason 不带 [ctx=] 后缀（语义保持原样）"""
    fs = _FakeSession()
    monkeypatch.setattr(arbiter, "async_session_factory", _FakeFactory(fs))
    item = {
        "type": "state_trigger",
        "candidate": {"character_id": 2, "user_id": 1, "trigger_reason": "查岗"},
    }
    asyncio.run(arbiter.log_trigger_candidate(item, executed=True))
    logs = [o for o in fs.added if getattr(o, "trigger_reason", None) is not None]
    assert logs and logs[0].trigger_reason == "查岗"
