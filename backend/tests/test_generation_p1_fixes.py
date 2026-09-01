# -*- coding: utf-8 -*-
"""生成链路 P1 修复测试（2026-08-18，审查 G-P1-1 / G-P1-2 / X-1）：
- G-P1-1 日摘要补生成：单次 build_context 最多补生成 1 天摘要（最早缺失天），其余缺失天以
  「共 N 条消息」占位注入，不再串行最多 7 次 LLM（_build_older_summaries）；
- G-P1-2 系统 prompt 总量硬顶（_apply_system_total_quota）+ user_info 拼接去重/整体 500 token 裁剪
  （_build_user_info）+ pending_timer 独立配额键；
- X-1 记忆注入格式统一：context_builder / message_generator / persona / shared_events 共用
  app.memory.format.format_memory_line，同一输入同一前缀结构输出一致。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库参照 test_memory_p1_fixes 风格）
"""
import asyncio
import os
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import context_builder as cb_mod
from app.memory import format as fmt
from app.memory.format import format_memory_line


@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch context_builder 的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="gen_p1_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(cb_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _msg(day: str, n: int, sender: str = "user") -> SimpleNamespace:
    """构造更早历史消息桩（仅含 helper 需要的字段）"""
    return SimpleNamespace(
        created_at=datetime.strptime(day, "%Y-%m-%d"),
        sender_type=sender,
        content=f"{day}-消息{n}",
    )


def _trim() -> dict:
    return cb_mod._trim_limits(hot=True)


def _summaries(factory) -> list:
    from sqlalchemy import select
    from app.models.daily_summary import DailySummary

    async def _main():
        async with factory() as db:
            return list((await db.execute(select(DailySummary))).scalars().all())

    return asyncio.run(_main())


# ---------------- G-P1-1：日摘要补生成（单次最多 1 天） ----------------

def test_日摘要缺失_只补生成最早1天其余占位(mem_db, monkeypatch):
    """3 天缺失历史：仅最早缺失天触发 1 次 LLM，其余 2 天注入「共 N 条消息」占位；只落库 1 条摘要。
    （占位文本与已有摘要一样会经过 _dedup_summary_lines 去重，各天消息数不同避免合并）"""
    calls = []

    async def _fake_llm(**kw):
        calls.append(kw)
        return "用户第一天聊了天气"

    monkeypatch.setattr(cb_mod, "chat_completion", _fake_llm)

    async def _main():
        older = [
            _msg("2026-08-01", 1), _msg("2026-08-01", 2, sender="ai"),
            _msg("2026-08-02", 1), _msg("2026-08-02", 2, sender="ai"), _msg("2026-08-02", 3, sender="ai"),
            _msg("2026-08-03", 1), _msg("2026-08-03", 2, sender="ai"), _msg("2026-08-03", 3, sender="ai"), _msg("2026-08-03", 4, sender="ai"),
        ]
        state = {"session_id": 7, "user_id": 1}
        text = await cb_mod._build_older_summaries(state, older, "小爱", _trim())
        return text

    text = asyncio.run(_main())
    assert len(calls) == 1                                            # 只调用 1 次补生成
    assert "2026-08-01" in calls[0]["messages"][0]["content"]          # 生成的是最早缺失天
    assert "【2026-08-01 概要】用户第一天聊了天气" in text
    assert "【2026-08-02 概要】共3条消息" in text                      # 其余天为占位文本
    assert "【2026-08-03 概要】共4条消息" in text
    rows = _summaries(mem_db)
    assert len(rows) == 1                                             # 只落库 1 条（最早缺失天）
    assert rows[0].summary_date == "2026-08-01"


def test_日摘要缺失_已有摘要天跳过只补最早缺失(mem_db, monkeypatch):
    """08-01 已有摘要：最早缺失天 08-02 补生成（1 次 LLM），08-03 占位"""
    calls = []

    async def _fake_llm(**kw):
        calls.append(kw)
        return "用户第二天聊了工作"

    monkeypatch.setattr(cb_mod, "chat_completion", _fake_llm)

    async def _main():
        from app.models.daily_summary import DailySummary
        async with mem_db() as db:
            db.add(DailySummary(session_id=7, summary_date="2026-08-01", summary_text="已有摘要"))
            await db.commit()
        older = [_msg("2026-08-01", 1), _msg("2026-08-02", 1), _msg("2026-08-03", 1)]
        text = await cb_mod._build_older_summaries({"session_id": 7, "user_id": 1}, older, "小爱", _trim())
        return text

    text = asyncio.run(_main())
    assert len(calls) == 1
    assert "【2026-08-01 概要】已有摘要" in text
    assert "【2026-08-02 概要】用户第二天聊了工作" in text
    assert "【2026-08-03 概要】共1条消息" in text
    rows = _summaries(mem_db)
    assert sorted(r.summary_date for r in rows) == ["2026-08-01", "2026-08-02"]


def test_日摘要缺失_LLM异常回退占位且只1次(mem_db, monkeypatch):
    """LLM 抛异常：生成天回退「共 N 条消息」（既有 except 兜底格式），仍只调用 1 次"""
    calls = []

    async def _boom_llm(**kw):
        calls.append(kw)
        raise RuntimeError("llm down")

    monkeypatch.setattr(cb_mod, "chat_completion", _boom_llm)

    async def _main():
        older = ([_msg("2026-08-01", i) for i in range(1, 3)]
                 + [_msg("2026-08-02", i) for i in range(1, 4)]
                 + [_msg("2026-08-03", i) for i in range(1, 5)])
        text = await cb_mod._build_older_summaries({"session_id": 7, "user_id": 1}, older, "小爱", _trim())
        return text

    text = asyncio.run(_main())
    assert len(calls) == 1
    assert "共2条消息" in text and "共3条消息" in text and "共4条消息" in text


def test_日摘要_无更早消息返回空():
    async def _main():
        return await cb_mod._build_older_summaries({"session_id": 7}, [], "小爱", _trim())

    assert asyncio.run(_main()) == ""


# ---------------- G-P1-2：总量硬顶 / user_info 拼接 / pending_timer 配额键 ----------------

def test_总量硬顶_超限按优先级裁剪结构完整(monkeypatch):
    """M1-S4：构造超限上下文——低价值块（织库4）先牺牲、同级后块先于前块、结构完整、user 不动"""
    monkeypatch.setattr(cb_mod, "TOTAL_SYSTEM_QUOTA_TOKENS", 10)  # 10 token = 20 字符
    msgs = [
        {"role": "system", "content": "甲" * 15},
        {"role": "system", "content": "乙" * 15},
        {"role": "user", "content": "用户消息" * 50},
    ]
    cb_mod._apply_system_total_quota(msgs)
    total = sum(len(m["content"]) for m in msgs if m["role"] == "system")
    assert total <= 20                                            # 总 token 不超上限
    assert [m["role"] for m in msgs] == ["system", "system", "user"]  # 消息结构完整
    assert msgs[2]["content"] == "用户消息" * 50                   # user 消息不被裁剪
    assert len(msgs[0]["content"]) == 15                          # 同级稳定序：前面的块保留
    assert msgs[1]["content"] == ""                               # 同级后面的块整块牺牲


def test_总量硬顶_低价值块先牺牲(monkeypatch):
    """M1-S4：织库/Lorebook（4）先于主模板（3）被裁；【本轮提醒】（1）最后才动"""
    monkeypatch.setattr(cb_mod, "TOTAL_SYSTEM_QUOTA_TOKENS", 10)  # 20 字符预算
    msgs = [
        {"role": "system", "content": "## 人设\n核心记忆内容"},           # 3（主模板，多行，12 字）
        {"role": "system", "content": "【设定·Lorebook】" + "设" * 12},   # 4（21 字）
        {"role": "system", "content": "【本轮提醒】回复要短"},            # 1（10 字）
        {"role": "user", "content": "x"},
    ]
    cb_mod._apply_system_total_quota(msgs)
    assert msgs[1]["content"] == ""                               # 低价值块先整块牺牲
    assert msgs[2]["content"].startswith("【本轮提醒】")            # 关键块不动
    assert msgs[0]["content"].startswith("## 人设")                # 主模板保头部（整行边界）


def test_总量硬顶_整行边界不切半句(monkeypatch):
    """M1-S4：块内裁剪落在换行边界——保留部分的后一字符是换行（或整块清空），绝无半行残片"""
    monkeypatch.setattr(cb_mod, "TOTAL_SYSTEM_QUOTA_TOKENS", 10)  # 20 字符预算
    lines = "第一行内容比较长需要截断\n第二行也很长同样要处理\n第三行"
    msgs = [{"role": "system", "content": lines}, {"role": "user", "content": "x"}]
    cb_mod._apply_system_total_quota(msgs)
    kept = msgs[0]["content"]
    assert kept == "" or lines.startswith(kept + "\n")           # 无半行残片（行边界对齐）
    assert sum(len(m["content"]) for m in msgs if m["role"] == "system") <= 20


def test_总量硬顶_无行边界整块丢弃(monkeypatch):
    """M1-S4：单行块裁剪时无行边界 → 整块放弃（保整句优先于保字数，规格明确语义）"""
    monkeypatch.setattr(cb_mod, "TOTAL_SYSTEM_QUOTA_TOKENS", 10)
    msgs = [{"role": "system", "content": "甲" * 30}, {"role": "user", "content": "x"}]
    cb_mod._apply_system_total_quota(msgs)
    assert msgs[0]["content"] == ""
    assert sum(len(m["content"]) for m in msgs if m["role"] == "system") <= 20


def test_总量硬顶_配额内零行为变化(monkeypatch):
    monkeypatch.setattr(cb_mod, "TOTAL_SYSTEM_QUOTA_TOKENS", 10)
    msgs = [{"role": "system", "content": "字" * 10}, {"role": "user", "content": "x"}]
    cb_mod._apply_system_total_quota(msgs)
    assert msgs[0]["content"] == "字" * 10
    assert msgs[1]["content"] == "x"


def test_user_info_notes为空不重复profile():
    out = cb_mod._build_user_info("画像A", "")
    assert out == "画像A"
    assert out.count("画像A") == 1                                 # 不重复拼接
    assert "画像A\n\n画像A" not in out


def test_user_info_notes非空拼接():
    out = cb_mod._build_user_info("画像A", "备忘录B")
    assert out == "画像A\n\n备忘录B"


def test_user_info_超长整体裁剪():
    out = cb_mod._build_user_info("字" * 4000, "备" * 4000)
    assert len(out) <= cb_mod.USER_INFO_QUOTA_TOKENS * cb_mod._EST_CHARS_PER_TOKEN  # 整体 500 token 裁剪


def test_pending_timer_独立配额键():
    assert cb_mod._SECTION_QUOTA_TOKENS["pending_timer"] == 300


# ---------------- X-1：记忆注入格式统一 ----------------

def test_format_函数在四个注入点同源():
    """context_builder / message_generator / persona / shared_events 均引用同一 format_memory_line"""
    from app.agent import persona as persona_mod
    from app.scheduler import message_generator as mg_mod
    from app.memory import shared_events as se_mod
    assert cb_mod.format_memory_line is fmt.format_memory_line
    assert persona_mod.format_memory_line is fmt.format_memory_line
    assert mg_mod.format_memory_line is fmt.format_memory_line
    assert se_mod.format_memory_line is fmt.format_memory_line


def test_format_主链路行结构():
    line = format_memory_line({
        "content": "用户喜欢喝美式咖啡",
        "created_at": datetime(2026, 8, 1),
        "epistemic_status": "FACT",
    })
    assert line == "- [记录于 2026-08-01] 用户喜欢喝美式咖啡"


def test_format_同一输入同一前缀结构():
    sample = {"content": "用户喜欢喝美式咖啡", "created_at": "2026-08-01", "epistemic_status": "FACT"}
    base = format_memory_line(sample)
    # persona：prefix="" 时仅缺行首 "- "，其余结构一致
    assert format_memory_line(sample, prefix="") == base[2:]
    # message_generator：max_len=80；shared_events：max_len=120（短内容下与主链路一致）
    assert format_memory_line(sample, max_len=80) == base
    assert format_memory_line(sample, max_len=120) == base
    # 截断差异仅由 max_len 控制
    assert format_memory_line(sample, max_len=5) == "- [记录于 2026-08-01] 用户喜欢喝"


def test_persona_最近情绪事件格式():
    from app.agent.persona import format_memory_line as pml
    line = pml({"content": "用户昨晚因为工作的事有点低落", "created_at": "2026-08-01 09:00:00"},
               prefix="", max_len=150)
    assert line == "[记录于 2026-08-01] 用户昨晚因为工作的事有点低落"
    # 截断长度与主链路一致（150）
    long = pml({"content": "字" * 200, "created_at": "2026-08-01"}, prefix="", max_len=150)
    assert len(long) - len("[记录于 2026-08-01] ") == 150


def test_message_generator_最近记忆格式():
    from app.scheduler.message_generator import format_memory_line as mml
    line = mml({"content": "用户喜欢喝美式咖啡", "created_at": "2026-08-01", "epistemic_status": "FACT"},
               max_len=80)
    assert line == "- [记录于 2026-08-01] 用户喜欢喝美式咖啡"
    # 认知前缀仍生效（与既有主动消息行为一致）
    inferred = mml({"content": "用户可能喜欢海边", "created_at": "2026-08-01", "epistemic_status": "INFERRED"},
                   max_len=80)
    assert inferred == "- [记录于 2026-08-01] [INFERRED] 用户可能喜欢海边"


def test_shared_recall_text_格式与公共函数一致(mem_db):
    """Shared Memory recall_text 行与 format_memory_line(max_len=120) 输出完全一致"""
    async def _main():
        from app.models.shared_event import SharedEvent
        from app.memory.shared_events import recall_text
        async with mem_db() as db:
            db.add(SharedEvent(user_id=1, character_id=1, event_type="user_marked",
                               title="第一次一起看海", description="用户和角色第一次一起看海",
                               event_time=datetime(2026, 8, 1)))
            await db.commit()
            txt = await recall_text(db, 1, 1, limit=2)
        expected = format_memory_line(
            {"content": "用户和角色第一次一起看海", "created_at": datetime(2026, 8, 1)},
            max_len=120,
        )
        return txt, expected

    txt, expected = asyncio.run(_main())
    assert txt == expected
    assert txt == "- [记录于 2026-08-01] 用户和角色第一次一起看海"
