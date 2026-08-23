# -*- coding: utf-8 -*-
"""生成链路 P2 修复测试（2026-08-18，审查 G-P2-2 / G-P2-3 / G-P2-4 / X-3）：
- G-P2-2 主动消息前置查询并行：画像/persona/天气/查岗/记忆检索/反思 一次 asyncio.gather 并发执行
  （屏障法：6 项全部启动后才放行，若串行会超时失败）；单查询失败不影响其他项；
- G-P2-3 禁用词词组化：裸词 AI/模型/算法 不再误伤「AI 绘画」「这个模型跑得慢」等生活化表达，
  「我是AI/作为AI/AI助手/AI模型/根据系统」等身份暴露词组仍整段剔除；
- G-P2-4 用户手动八维状态独立 system 块：与规则器情绪提示分离、独立配额
  （_build_user_manual_state_text 纯函数 + 模板分区断言）；
- X-3 派生查询 embedding 进程内 LRU 缓存：相同 character+query 第二次命中（mock embedding 计数），
  不同 character 不共享，TTL 过期后重新推理，检索层派生查询走缓存而主查询不缓存。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库参照 test_memory_p2_fixes 风格）
"""
import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.agent.context_builder as cb_mod
import app.agent.persona as persona_mod
import app.agent.user_profile as up_mod
import app.db.database as db_mod
import app.memory as mem_mod
import app.memory.bm25_index as bm25
import app.memory.embedding_cache as emb_cache
import app.memory.service as memsvc
import app.scheduler.message_generator as mg_mod
import app.services.weather_service as weather_mod
from app.memory.embedding_cache import get_cached_embedding


# ---------------- G-P2-2：主动消息前置查询并行 ----------------

class _Barrier:
    """并发屏障：n 个参与者全部启动后才放行（串行执行时永远等不到，wait_for 超时失败）"""

    def __init__(self, n):
        self.n = n
        self.count = 0
        self.started = asyncio.Event()

    async def wait_all(self):
        self.count += 1
        if self.count >= self.n:
            self.started.set()
        await self.started.wait()


def _barrier_mock(barrier, result=None):
    async def _side(*a, **k):
        await barrier.wait_all()
        return result

    return AsyncMock(side_effect=_side)


class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    def __init__(self, barrier=None):
        self._barrier = barrier

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        if self._barrier is not None:
            await self._barrier.wait_all()
        return _FakeResult()


def _fake_session_factory(barrier=None):
    def _factory():
        return _FakeSession(barrier)

    return _factory


def _patch_proactive_queries(monkeypatch, barrier=None):
    """把 generate_proactive_event 的前置查询全部 mock 掉（barrier 非空时各查询参与并发屏障）"""
    monkeypatch.setattr(up_mod, "build_user_profile_text", _barrier_mock(barrier, "画像：用户叫小明"))
    monkeypatch.setattr(persona_mod, "assemble_persona_context",
                        _barrier_mock(barrier, {"cognitive": True, "relationship_state": "信任80"}))
    monkeypatch.setattr(weather_mod, "get_user_weather_line", _barrier_mock(barrier, "天气：晴，20度"))
    monkeypatch.setattr(mem_mod, "search_memories", _barrier_mock(barrier, []))
    monkeypatch.setattr(mg_mod, "_load_recent_reflection", _barrier_mock(barrier, ""))
    monkeypatch.setattr(db_mod, "async_session_factory", _fake_session_factory(barrier))

    async def _fake_llm(**kw):
        return "你好呀！\n今天天气不错。"

    async def _fake_level(cid):
        return 0

    monkeypatch.setattr(mg_mod, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg_mod, "load_character_reasoning_level", _fake_level)


def test_主动消息_前置查询并行gather全部启动(monkeypatch):
    barrier = _Barrier(6)
    _patch_proactive_queries(monkeypatch, barrier)

    async def _main():
        return await asyncio.wait_for(
            mg_mod.generate_proactive_event(
                character_name="小爱", character_bio="", character_personality="友善",
                character_id=1, user_id=1, current_status="在家",
            ),
            timeout=10,
        )

    segments = asyncio.run(_main())
    assert barrier.started.is_set()          # 6 项前置查询均并发启动（串行会超时失败）
    assert segments == ["你好呀！", "今天天气不错。"]


def test_主动消息_单查询失败不影响其他项(monkeypatch):
    calls = []

    async def _profile_boom(*a, **k):
        calls.append("profile")
        raise RuntimeError("profile down")

    async def _persona_ok(*a, **k):
        calls.append("persona")
        return {"cognitive": True, "relationship_state": "信任80", "active_topics": ""}

    async def _weather_ok(*a, **k):
        calls.append("weather")
        return "天气：晴"

    async def _memories_ok(*a, **k):
        calls.append("memories")
        return [{"content": "用户喜欢喝美式咖啡", "created_at": "2026-08-01", "epistemic_status": "FACT"}]

    async def _reflect_ok(*a, **k):
        calls.append("reflect")
        return "最近的复盘：周末一起去爬山"

    async def _fake_llm(**kw):
        return "哈哈，咖啡确实好喝。"

    async def _fake_level(cid):
        return 0

    monkeypatch.setattr(up_mod, "build_user_profile_text", _profile_boom)
    monkeypatch.setattr(persona_mod, "assemble_persona_context", _persona_ok)
    monkeypatch.setattr(weather_mod, "get_user_weather_line", _weather_ok)
    monkeypatch.setattr(mem_mod, "search_memories", _memories_ok)
    monkeypatch.setattr(mg_mod, "_load_recent_reflection", _reflect_ok)
    monkeypatch.setattr(db_mod, "async_session_factory", _fake_session_factory())
    monkeypatch.setattr(mg_mod, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg_mod, "load_character_reasoning_level", _fake_level)

    async def _main():
        return await mg_mod.generate_proactive_event(
            character_name="小爱", character_bio="", character_personality="友善",
            character_id=1, user_id=1, current_status="在家",
        )

    segments = asyncio.run(_main())
    assert "profile" in calls and "persona" in calls and "weather" in calls
    assert "memories" in calls and "reflect" in calls     # 画像失败不影响其余各项
    assert segments == ["哈哈，咖啡确实好喝。"]


# ---------------- G-P2-3：禁用词词组化 ----------------

def test_禁用词_生活化表述不剔除():
    segs = ["我今天用 AI 绘画画了一幅风景", "这个模型跑得慢，加载要半天", "算法不行，我换了个思路"]
    ok, cleaned = mg_mod._validate_segments(segs)
    assert ok is True
    assert cleaned == segs


def test_禁用词_裸词AI模型算法不再误伤():
    ok, cleaned = mg_mod._validate_segments(["这个 AI 画得真不错", "模型推理确实快"])
    assert ok is True
    assert cleaned == ["这个 AI 画得真不错", "模型推理确实快"]


def test_禁用词_身份暴露词组仍整段剔除():
    ok, cleaned = mg_mod._validate_segments(["我是AI，请多指教", "作为AI我觉得这样", "根据系统设定，我不能说"])
    assert ok is False
    assert cleaned == []


def test_禁用词_新增词组覆盖AI助手与AI模型():
    ok, cleaned = mg_mod._validate_segments(["AI 助手提醒我该休息了", "我是一个AI模型，无法回答"])
    assert ok is False
    assert cleaned == []


# ---------------- G-P2-4：用户手动八维状态独立分区 ----------------

def test_八维状态_文本构建纯函数():
    text = cb_mod._build_user_manual_state_text(["心情90", "体温38"])
    assert text == "用户手动设置的当前状态：心情90、体温38（据此体会用户此刻的状态）"


def test_八维状态_全默认不注入():
    assert cb_mod._build_user_manual_state_text([]) == ""
    assert cb_mod._build_user_manual_state_text(None) == ""


def test_八维状态_独立system块与情绪提示分离():
    tpl = cb_mod.SYSTEM_PROMPT_TEMPLATE
    assert "## 用户此刻的状态（据此调整语气/篇幅；没有忽略）" in tpl
    assert "## 用户手动设置的状态（数值仅供参考，不要念数据）" in tpl
    assert "{user_emotion}" in tpl                       # 情绪提示仍在其分区
    assert "{user_manual_state}" in tpl                  # 手动状态独立占位符
    assert tpl.index("## 用户此刻的状态") < tpl.index("## 用户手动设置的状态")


def test_八维状态_独立配额键():
    assert cb_mod._SECTION_QUOTA_TOKENS["user_manual_state"] == 300


# ---------------- X-3：派生查询 embedding 进程内 LRU 缓存 ----------------

@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch memory.service 的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="gen_p2_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    bm25._persist_root = Path(tmp)  # 2026-08-23 深化：索引持久化隔离到临时目录，不写生产/不跨测试泄漏
    bm25.clear_cache()  # 检索增强（2026-08-23）：BM25 索引为进程内全局缓存，避免跨测试的 character_id 复用污染
    yield factory
    bm25.clear_cache()          # persist_root 仍指向临时目录，清内存+清盘
    bm25._persist_root = None
    asyncio.run(engine.dispose())


def test_embedding缓存_相同char与query第二次命中(monkeypatch):
    calls = []

    async def _fake_embed(text):
        calls.append(text)
        return [float(len(text))] * 8

    monkeypatch.setattr(emb_cache, "text_embedding", _fake_embed)
    emb_cache.clear_cache()
    try:
        v1 = asyncio.run(get_cached_embedding(1, "低落难过"))
        v2 = asyncio.run(get_cached_embedding(1, "低落难过"))
        assert v1 == v2
        assert calls == ["低落难过"]                      # 第二次命中缓存，embedding 只推理一次
    finally:
        emb_cache.clear_cache()


def test_embedding缓存_不同character不共享(monkeypatch):
    calls = []

    async def _fake_embed(text):
        calls.append(text)
        return [float(len(text))] * 8

    monkeypatch.setattr(emb_cache, "text_embedding", _fake_embed)
    emb_cache.clear_cache()
    try:
        asyncio.run(get_cached_embedding(1, "开心高兴"))
        asyncio.run(get_cached_embedding(2, "开心高兴"))
        assert calls == ["开心高兴", "开心高兴"]          # key 含 character_id，不同角色不共享
    finally:
        emb_cache.clear_cache()


def test_embedding缓存_TTL过期后重新推理(monkeypatch):
    calls = []
    now = [1000.0]

    async def _fake_embed(text):
        calls.append(text)
        return [float(len(text))] * 8

    monkeypatch.setattr(emb_cache, "text_embedding", _fake_embed)
    monkeypatch.setattr(emb_cache, "_now", lambda: now[0])
    emb_cache.clear_cache()
    try:
        asyncio.run(get_cached_embedding(1, "困惑"))
        asyncio.run(get_cached_embedding(1, "困惑"))     # TTL 内命中
        now[0] += 301.0                                   # 超过 5 分钟
        asyncio.run(get_cached_embedding(1, "困惑"))     # 过期 → 重新推理
        assert calls == ["困惑", "困惑"]
    finally:
        emb_cache.clear_cache()


def test_检索_派生查询走缓存主查询不缓存(mem_db, monkeypatch):
    main_calls = []
    derived_calls = []

    async def _fake_main_embed(text):
        main_calls.append(text)
        return [1.0] * 8

    async def _fake_derived_embed(text):
        derived_calls.append(text)
        return [1.0] * 8

    async def _no_vec(*a, **k):
        return []

    monkeypatch.setattr(memsvc, "text_embedding", _fake_main_embed)     # service 内主查询引用
    monkeypatch.setattr(emb_cache, "text_embedding", _fake_derived_embed)  # 缓存模块内派生查询引用
    monkeypatch.setattr(memsvc, "vector_search", _no_vec)
    emb_cache.clear_cache()
    try:
        async def _main():
            await memsvc.search_memories(character_id=1, query="今天聊了什么", queries=["低落难过"], limit=3)
            await memsvc.search_memories(character_id=1, query="今天聊了什么", queries=["低落难过"], limit=3)

        asyncio.run(_main())
        # 主查询（用户原话）每轮都推理（2 次）；派生查询第二次命中缓存（1 次）→ 共 3 次而非 4 次
        assert main_calls == ["今天聊了什么", "今天聊了什么"]
        assert derived_calls == ["低落难过"]
    finally:
        emb_cache.clear_cache()
