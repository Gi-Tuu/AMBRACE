# -*- coding: utf-8 -*-
"""#70 方案A：记忆分层检索与注入（L0/L1/L2）测试（2026-08-30）。

覆盖方案 7.2 中方案 A 相关用例（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；
临时 SQLite 文件库 + monkeypatch，与现有记忆测试同法）：
- ``test_tiers_extract_l0``：有 why 用 why；无 why 取首句；空串/缺字段安全；短句不切碎。
- ``test_tiered_lines_order``：Top1 截 240、Top2/3 为 L0；format_memory_line 标注（时间/说话人）不丢。
- ``test_l1_join``：DailySummary 经 ChatSession join 取到正确日期；无摘要/空日期/他角色返回 None。
- ``test_flag_off_byte_identical``：flag 关时检索区行与旧链路（format_memory_line + N 轮去重）逐字节一致（回归保护）。
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.memory.tiers import (
    extract_l0,
    first_sentence,
    build_vector_text,
    tiered_memory_lines,
    load_l1_summary,
    L0_MAX_CHARS,
    L1_MAX_CHARS,
)


# ---------------- 纯函数：extract_l0 / first_sentence / build_vector_text ----------------

def test_tiers_extract_l0():
    # 有 why → why 优先（不复述 content）
    assert extract_l0({"why_it_matters": "这是意义", "content": "这是事实"}) == "这是意义"
    # 有 why 超长 → 截到 L0_MAX_CHARS
    assert extract_l0({"why_it_matters": "长" * 100, "content": "x"}) == "长" * L0_MAX_CHARS
    # 无 why → content 规则首句
    assert extract_l0({"content": "用户喜欢喝美式咖啡。还喜欢喝茶"}) == "用户喜欢喝美式咖啡。"
    # 无 why 无 content → title 兜底
    assert extract_l0({"content": "", "title": "备用标题。"}) == "备用标题。"
    # 空串 / 缺字段安全
    assert extract_l0({}) == ""
    assert extract_l0({"content": ""}) == ""
    assert extract_l0({"why_it_matters": None}) == ""
    assert extract_l0({"content": None, "why_it_matters": ""}) == ""


def test_first_sentence_短句不切碎():
    # 短于 _MIN_SENTENCE_LEN 的句读不当作句末（避免「嗯。」被切碎）
    assert first_sentence("嗯。") == "嗯。"
    assert first_sentence("好。") == "好。"
    assert first_sentence("") == ""
    assert first_sentence("   ") == ""


def test_build_vector_text():
    # 有 why → "why content"；无 why → content 原文；兼容 ORM
    assert build_vector_text({"content": "用户喜欢咖啡", "why_it_matters": "因为重要"}) == "因为重要 用户喜欢咖啡"
    assert build_vector_text({"content": "用户喜欢咖啡"}) == "用户喜欢咖啡"

    class _O:
        content = "用户喜欢咖啡"
        why_it_matters = "因为重要"

    assert build_vector_text(_O()) == "因为重要 用户喜欢咖啡"


# ---------------- 分层注入：tiered_memory_lines ----------------

def test_tiered_lines_order():
    top = {"id": 1, "content": "A" * 300, "created_at": "2026-08-18",
           "epistemic_status": "FACT", "contradiction_count": 0}
    mid = {"id": 2, "content": "用户喜欢喝美式咖啡。还喜欢喝茶", "created_at": "2026-08-18",
           "epistemic_status": "FACT", "contradiction_count": 0, "speaker_type": "user"}
    bot = {"id": 3, "content": "用户喜欢爬山", "why_it_matters": "因为那是你们第一次一起爬山",
           "created_at": "2026-08-18", "epistemic_status": "FACT", "contradiction_count": 0}
    lines = tiered_memory_lines([top, mid, bot], include_speaker=True)
    assert len(lines) == 3
    # Top1 → L2：截 240 + 时间/前缀标注完整
    assert lines[0].startswith("- [记录于 2026-08-18] ")
    assert "A" * 240 in lines[0]
    assert "A" * 241 not in lines[0]
    # Top2 → L0：首句（无 why），说话人标注保留
    assert "[你说的]" in lines[1]
    assert "用户喜欢喝美式咖啡。" in lines[1]
    assert "还喜欢喝茶" not in lines[1]
    # Top3 → L0：why 优先
    assert "因为那是你们第一次一起爬山" in lines[2]
    assert "用户喜欢爬山" not in lines[2]


# ---------------- L1：DailySummary 经 ChatSession join ----------------

@pytest.fixture()
def l1_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch app.db.database.async_session_factory（load_l1_summary 内延迟 import）。"""
    import app.db.database as db_mod
    tmp = tempfile.mkdtemp(prefix="memory_tiers_l1_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型（含 ChatSession / DailySummary）
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def test_l1_join(l1_db):
    from app.models.chat.session import ChatSession
    from app.models.memory.daily_summary import DailySummary

    async def _main():
        async with l1_db() as db:
            db.add(ChatSession(id=1, user_id=1, character_id=7, title="c7"))
            db.add(ChatSession(id=2, user_id=1, character_id=9, title="c9"))
            await db.commit()
        async with l1_db() as db:
            db.add(DailySummary(session_id=1, summary_date="2026-08-18", summary_text="那天我们聊了关于工作的规划。"))
            db.add(DailySummary(session_id=2, summary_date="2026-08-18", summary_text="另一角色的摘要。"))
            await db.commit()
        hit = await load_l1_summary(7, "2026-08-18")
        miss = await load_l1_summary(7, "2026-08-19")       # 无该日摘要
        empty = await load_l1_summary(7, "")                # 日期为空
        other_char = await load_l1_summary(99, "2026-08-18")  # 该角色无会话
        return hit, miss, empty, other_char

    hit, miss, empty, other_char = asyncio.run(_main())
    assert hit == "那天我们聊了关于工作的规划。"
    assert miss is None
    assert empty is None
    assert other_char is None


def test_l1_join_超长摘要截到L1配额(l1_db):
    from app.models.chat.session import ChatSession
    from app.models.memory.daily_summary import DailySummary

    long_text = "天" * 300

    async def _main():
        async with l1_db() as db:
            db.add(ChatSession(id=1, user_id=1, character_id=7, title="c7"))
            await db.commit()
        async with l1_db() as db:
            db.add(DailySummary(session_id=1, summary_date="2026-08-18", summary_text=long_text))
            await db.commit()
        return await load_l1_summary(7, "2026-08-18")

    assert asyncio.run(_main()) == "天" * L1_MAX_CHARS


# ---------------- flag 关：字节一致回归保护 ----------------

def test_flag_off_byte_identical(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    from app.agent.context import section_memories
    from app.memory.format import format_memory_line

    # 保护：限制 memory_tiered_inject 默认不得为 True；此处显式关（并自动还原）
    assert AGENT_FLAGS.get("memory_tiered_inject", False) is False
    monkeypatch.setitem(AGENT_FLAGS, "memory_tiered_inject", False)

    section_memories._memory_char_rounds.clear()
    section_memories._memory_inject_rounds.clear()
    section_memories._bump_memory_round(11)
    mems = [
        {"id": 1, "content": "用户喜欢喝美式咖啡", "created_at": "2026-08-18",
         "epistemic_status": "FACT", "contradiction_count": 0},
        {"id": 2, "content": "用户是程序员", "created_at": "2026-08-18",
         "epistemic_status": "INFERRED", "contradiction_count": 0, "speaker_type": "user"},
    ]
    lines = section_memories._build_retrieved_memory_lines(11, mems)
    expected = [format_memory_line(m, include_speaker=True) for m in mems]
    assert lines == expected  # 与旧链路逐字节一致

    # 旧链路 N 轮去重语义保留（5 轮内不重复注入）
    section_memories._bump_memory_round(11)
    assert section_memories._build_retrieved_memory_lines(11, mems) == []


def test_flag_on_分层注入不改变flag_off缺省(monkeypatch):
    """开 flag 后检索区行走分层；关 flag 仍逐字节一致（双向回归保护）。"""
    from app.agent.loop import AGENT_FLAGS
    from app.agent.context import section_memories
    from app.memory.format import format_memory_line

    section_memories._memory_char_rounds.clear()
    section_memories._memory_inject_rounds.clear()
    section_memories._bump_memory_round(11)
    mems = [
        {"id": 1, "content": "A" * 300, "created_at": "2026-08-18",
         "epistemic_status": "FACT", "contradiction_count": 0},
        {"id": 2, "content": "用户喜欢喝美式咖啡。还喜欢喝茶", "created_at": "2026-08-18",
         "epistemic_status": "FACT", "contradiction_count": 0},
    ]

    monkeypatch.setitem(AGENT_FLAGS, "memory_tiered_inject", True)
    on_lines = section_memories._build_retrieved_memory_lines(11, mems)

    monkeypatch.setitem(AGENT_FLAGS, "memory_tiered_inject", False)
    section_memories._memory_char_rounds.clear()
    section_memories._memory_inject_rounds.clear()
    section_memories._bump_memory_round(11)
    off_lines = section_memories._build_retrieved_memory_lines(11, mems)

    assert len(on_lines) == 2
    assert "A" * 240 in on_lines[0]            # Top1 L2（240）
    assert "用户喜欢喝美式咖啡。" in on_lines[1]  # Top2 L0：首句
    assert "还喜欢喝茶" not in on_lines[1]
    # flag 开时 N 轮去重语义同样保留（与关 flag 一致）
    section_memories._bump_memory_round(11)
    assert section_memories._build_retrieved_memory_lines(11, mems) == []

    assert on_lines != off_lines               # 开/关确实不同
    assert off_lines == [format_memory_line(m, include_speaker=True) for m in mems]  # 关=旧链路
