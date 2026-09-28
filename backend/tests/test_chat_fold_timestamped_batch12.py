# -*- coding: utf-8 -*-
"""批 0-12 · 核实「chat 折叠是否覆盖式」——结论：**不是覆盖式，是按天分段、多条带时间戳的笔记**（本单不改生产码）。

核实证据（文件:行号，2026-09-28 复核）：
- 折叠主体 = ``app/agent/context_builder.py:669 _build_older_summaries``：
  先把 1 天以前的消息按 ``msg.created_at.strftime("%Y-%m-%d")`` 分桶（:677-682），
  逐天查 ``DailySummary``（:688-695），有则直接注入、无则**最多补生成 1 天**（:702-707）；
- 存储 = ``app/models/memory/__init__.py:90 DailySummary``：一天一行、``summary_date``（YYYY-MM-DD）
  为日期键，``UniqueConstraint(session_id, summary_date)``（:101）；另有 ``created_at`` 落库时刻；
- 写入只有 ``INSERT ... OR IGNORE``（``context_builder.py:740-744``、
  ``app/scheduling/daily_memory_maintenance.py:102-104``），**全仓没有任何 UPDATE summary_text 的路径**
  （grep ``summary_text`` 只剩读与 INSERT）⇒ 已生成那天的内容不会被后续内容改写；
- 注入形态 = 多条带时间戳行 ``【YYYY-MM-DD 概要】文本``（:698 / :704 / :747）逐行换行拼接，
  再与最近完整消息用「---」分隔（``app/agent/context/section_summaries.py:157``）。

对照（避免混淆）：记忆「置顶摘要」``app/memory/summary.py:134-141`` **才是覆盖式**（同一行 content 重写），
它属于「记忆提炼」而非「对话折叠」，本单不动；下面的对照用例把这一区别钉住。

纪律：临时库走 tests/_dbclone（禁止连生产库）；LLM 全打桩；项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import context_builder as cb_mod
from app.memory import summary as _summary
from app.models.memory import DailySummary

pytestmark = pytest.mark.slow

SESSION_A = 7
SESSION_B = 8
CHAR_ID = 101


# ─────────────────────────── 夹具与工具 ───────────────────────────

def _mk_llm(responses=None):
    """LLM 桩：记录 prompt 与调用次数；responses 用完后按序号返回可区分文本。"""
    llm = SimpleNamespace(prompts=[], calls=0, responses=list(responses or []))

    async def _run(messages=None, **_kw):
        llm.calls += 1
        llm.prompts.append((messages or [{}])[0].get("content", ""))
        if llm.responses:
            return llm.responses.pop(0)
        return f"第{llm.calls}次概括内容"

    llm.run = _run
    return llm


@pytest.fixture()
def fold_env(monkeypatch, tmp_path):
    """临时库 + 会话/角色父行 + LLM 打桩（日摘要与置顶摘要两条链各自的模块级引用都patch到）。"""
    engine = clone_engine(tmp_path / "fold.db")
    factory = make_session_factory(engine)
    llm = _mk_llm()

    async def _seed():
        from app.models.character import AICharacter
        from app.models.chat import ChatSession
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="fold_u1", nickname="折叠用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=1, name="折叠角色"))
            db.add(ChatSession(id=SESSION_A, user_id=1, character_id=CHAR_ID))
            db.add(ChatSession(id=SESSION_B, user_id=1, character_id=CHAR_ID))
            await db.commit()

    asyncio.run(_seed())
    monkeypatch.setattr(cb_mod, "async_session_factory", factory)
    monkeypatch.setattr(cb_mod, "chat_completion", llm.run)
    monkeypatch.setattr(_summary, "async_session_factory", factory)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", llm.run)
    # 存活项清单/灰度等副作用与本单无关，显式关掉以免多一次库查询
    monkeypatch.setattr(cb_mod, "survival_checklist_allowed", lambda *_a, **_kw: False)

    yield {"factory": factory, "llm": llm}
    asyncio.run(engine.dispose())


def _msg(day: str, n: int = 1, sender: str = "user", content=None) -> SimpleNamespace:
    """更早历史消息桩（只需要 helper 用到的字段）；content 缺省为可区分文本。"""
    return SimpleNamespace(
        created_at=datetime.strptime(day, "%Y-%m-%d"),
        sender_type=sender,
        content=f"{day}第{n}条消息" if content is None else content,
    )


def _msgs_of_days(days: list[str], per_day: int = 2) -> list[SimpleNamespace]:
    out = []
    for d in days:
        for i in range(1, per_day + 1):
            out.append(_msg(d, i))
            out.append(_msg(d, i, sender="ai"))
    return out


def _trim() -> dict:
    return cb_mod._trim_limits(hot=True)


def _state(session_id: int = SESSION_A) -> dict:
    return {"session_id": session_id, "user_id": 1, "character_id": CHAR_ID}


async def _fold(older_msgs: list, session_id: int = SESSION_A) -> str:
    return await cb_mod._build_older_summaries(_state(session_id), older_msgs, "折叠角色", _trim())


def _rows(factory, session_id: int | None = None) -> list:
    async def _run():
        async with factory() as db:
            q = select(DailySummary).order_by(DailySummary.summary_date, DailySummary.id)
            if session_id is not None:
                q = q.where(DailySummary.session_id == session_id)
            return list((await db.execute(q)).scalars().all())

    return asyncio.run(_run())


def _seed_rows(factory, session_id: int, pairs: list[tuple[str, str]]) -> list[int]:
    """预置日摘要行（模拟历史已折叠），返回行 id。"""
    async def _run():
        ids = []
        async with factory() as db:
            for date_str, text in pairs:
                r = DailySummary(session_id=session_id, summary_date=date_str, summary_text=text)
                db.add(r)
                await db.flush()
                ids.append(r.id)
            await db.commit()
        return ids

    return asyncio.run(_run())


DAYS3 = ["2026-08-01", "2026-08-02", "2026-08-03"]


# ─────────────── ① 分段保留：每天一条，后来的天不吞掉已有的天 ───────────────

def test_折叠_逐天补生成_每天各存一行不合并(fold_env):
    """三天历史分三次折叠调用：库里长出 3 行（每天一条），而不是把旧天重写掉。"""
    factory = fold_env["factory"]
    for day in DAYS3:
        asyncio.run(_fold([_msg(day, 1)]))
    rows = _rows(factory)
    assert [r.summary_date for r in rows] == DAYS3                  # 三条独立行，日期各不同
    assert len({r.id for r in rows}) == 3                           # 不是同一行被反复改写
    assert all(r.summary_text for r in rows)                        # 每天文本各自留存


def test_折叠_同一天重复折叠不新增也不覆盖已有内容(fold_env):
    """同一天重复调用：既不多落一行（唯一键幂等），也不把那天原文改成新内容。"""
    factory = fold_env["factory"]
    asyncio.run(_fold([_msg("2026-08-01", 1)]))
    before = _rows(factory)
    assert len(before) == 1
    fold_env["llm"].responses = ["新概括绝不应覆盖旧天"]
    asyncio.run(_fold([_msg("2026-08-01", 9, content="2026-08-01 又聊了些别的")]))
    after = _rows(factory)
    assert [r.id for r in after] == [r.id for r in before]          # 同一行，未新增
    assert after[0].summary_text == before[0].summary_text          # 未覆盖
    assert "新概括绝不应覆盖旧天" not in after[0].summary_text
    assert fold_env["llm"].calls == 1                               # 第二次调用零 LLM（直接复用已有行）


def test_折叠_窗口上限只裁注入不删历史(fold_env):
    """9 天都有摘要：注入只出最近 MAX_SUMMARY_DAYS 天，但库里 9 行全在（早期天不丢、可回查）。"""
    factory = fold_env["factory"]
    days = [f"2026-08-{d:02d}" for d in range(1, 10)]
    _seed_rows(factory, SESSION_A, [(d, f"{d} 那天聊过的内容") for d in days])
    text = asyncio.run(_fold(_msgs_of_days(days, per_day=1)))
    lines = [ln for ln in text.split("\n") if ln.strip()]
    assert len(lines) == cb_mod.MAX_SUMMARY_DAYS                    # 注入窗口不变（本单不动预算）
    assert lines[0].startswith(f"【{days[-cb_mod.MAX_SUMMARY_DAYS]} 概要】")
    assert len(_rows(factory)) == 9                                 # 早期天仍在库，未被覆盖或删除
    assert fold_env["llm"].calls == 0                               # 全命中已有摘要，不重复花 token


# ─────────────── ② 时间戳正确：日期键来自消息日期，注入行带日期前缀 ───────────────

def test_折叠_注入行带时间戳前缀且一天一行(fold_env):
    factory = fold_env["factory"]
    _seed_rows(factory, SESSION_A, [(d, f"{d}概要文本") for d in DAYS3])
    text = asyncio.run(_fold(_msgs_of_days(DAYS3, per_day=1)))
    for d in DAYS3:
        assert f"【{d} 概要】{d}概要文本" in text                    # 每条笔记自带日期
    assert text.count("概要】") == 3                                 # 三条、不是一条合并摘要


def test_折叠_日期键取自消息日期并留存落库时刻(fold_env):
    """日期键＝消息 created_at 的日期；单轮最多补生成 1 天（G-P1-1 既有约束），其余天先占位、下一轮补成独立行。"""
    text = asyncio.run(_fold([_msg("2026-09-12", 1), _msg("2026-09-13", 1)]))
    rows = _rows(fold_env["factory"])
    assert [r.summary_date for r in rows] == ["2026-09-12"]          # 只最早缺失天落库
    assert rows[0].created_at is not None and rows[0].created_at.tzinfo is None  # 落库时刻：naive UTC（全库口径）
    assert "【2026-09-12 概要】" in text
    assert "【2026-09-13 概要】共1条消息" in text                      # 未补的天以占位注入，不静默丢
    asyncio.run(_fold([_msg("2026-09-13", 1)]))
    assert [r.summary_date for r in _rows(fold_env["factory"])] == ["2026-09-12", "2026-09-13"]  # 新增一行，不覆盖


def test_折叠_跨会话同一天各存一行互不覆盖(fold_env):
    """同一日期两个会话各一行：日期键按会话维度分段，不会互相顶掉。"""
    factory = fold_env["factory"]
    asyncio.run(_fold([_msg("2026-08-05", 1)], session_id=SESSION_A))
    asyncio.run(_fold([_msg("2026-08-05", 1)], session_id=SESSION_B))
    a = _rows(factory, SESSION_A)
    b = _rows(factory, SESSION_B)
    assert [r.summary_date for r in a] == [r.summary_date for r in b] == ["2026-08-05"]
    assert a[0].id != b[0].id                                       # 两行独立
    assert len(_rows(factory)) == 2


# ─────────────── ③ 脏数据不抛（既有兜底路径保持） ───────────────

def test_折叠_脏消息不抛异常(fold_env):
    """content 为 None / 带 tzinfo 的时间 / 超长内容：分桶与拼 prompt 都不炸。"""
    dirty = [
        _msg("2026-08-20", 1, content=None),
        SimpleNamespace(created_at=datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc),
                        sender_type="user", content="字" * 5000),
        _msg("2026-08-21", 1, sender="unknown"),
    ]
    text = asyncio.run(_fold(dirty))
    assert "【2026-08-20 概要】" in text and "【2026-08-21 概要】" in text


def test_折叠_空摘要文本与LLM异常都不抛(fold_env, monkeypatch):
    """库里已有空文本行（脏数据）：不抛，其它天照常注入；LLM 抛异常时回退「共 N 条消息」并照样落库。"""
    factory = fold_env["factory"]
    _seed_rows(factory, SESSION_A, [("2026-08-01", "")])

    async def _boom(messages=None, **_kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(cb_mod, "chat_completion", _boom)
    text = asyncio.run(_fold(_msgs_of_days(["2026-08-01", "2026-08-02"], per_day=1)))
    assert isinstance(text, str)                                    # 未抛
    assert "共2条消息" in text                                       # 兜底占位仍在
    rows = _rows(factory, SESSION_A)
    assert [r.summary_date for r in rows] == ["2026-08-01", "2026-08-02"]
    assert rows[1].summary_text == "共2条消息"                        # 兜底文本会固化成该天的行


def test_折叠_无更早消息时零副作用():
    """空历史：返回空串，不查库不落库（与旧行为一致）。"""
    assert asyncio.run(_fold([])) == ""


# ─────────────── ④ 对照：覆盖式的是「置顶摘要」，不是 chat 折叠 ───────────────

def test_对照_置顶摘要仍是覆盖式重写同一行(fold_env):
    """memory/summary.py 的按类型置顶摘要：二次生成重写同一行 content（覆盖式，本单保持不动）。"""
    from app.models.memory import Memory

    factory = fold_env["factory"]

    async def _seed_memories():
        async with factory() as db:
            for i in range(3):
                db.add(Memory(user_id=1, character_id=CHAR_ID, memory_type="event",
                              content=f"用户说过第{i}件事", source="chat", importance=50.0))
            await db.commit()

    asyncio.run(_seed_memories())
    fold_env["llm"].responses = ["第一次凝练出来的置顶摘要内容", "第二次凝练出来的置顶摘要内容"]
    r1 = asyncio.run(_summary.summarize_memories(CHAR_ID, "event", force=True))
    r2 = asyncio.run(_summary.summarize_memories(CHAR_ID, "event", force=True))
    assert r1["generated"] is True and r2["generated"] is True
    assert r1["memory_id"] == r2["memory_id"]                       # 同一行（非另起一条）

    async def _pinned():
        async with factory() as db:
            rows = (await db.execute(
                select(Memory).where(Memory.character_id == CHAR_ID,
                                     Memory.memory_type == "event",
                                     Memory.is_pinned == True)  # noqa: E712
            )).scalars().all()
        return list(rows)

    pinned = asyncio.run(_pinned())
    assert len(pinned) == 1                                         # 每类型只有一条置顶摘要
    assert pinned[0].content == "第二次凝练出来的置顶摘要内容"       # 覆盖式：旧内容被重写
    assert _rows(factory) == []                                     # 该链路不写日摘要（两者互不相干）
