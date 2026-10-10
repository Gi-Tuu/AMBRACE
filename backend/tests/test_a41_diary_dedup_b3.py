# -*- coding: utf-8 -*-
"""A41 批 3 收尾（交接任务 3 之日记半）：C30 的两条判据——「0 篇」不再吞掉失败、日记写完又有新对话要重写。

钉的四件事：
① **`_day_window` 只有一份日界口径**（北京日 = UTC 前一天 16 点起；`beijing_window=False` 退回 UTC 日界），
   取正文与判新鲜度必须共用它，两边各写一遍减法＝改口径时只红一边；
② **`_diary_is_stale` 真值表**：日记写完之后当天又有消息 ⇒ 过期；没有 ⇒ 不过期；
   **任一时刻读不到 ⇒ 一律判不过期**（宁可少重写一次，也不能因为读不出就每拍重跑 LLM，
   那会把「每天一篇」变成「每小时一篇」）；带 tz 的值不许把比较炸成 TypeError；
③ **分支真的接上了**（临时库端到端，不是只看纯函数）：过期 ⇒ 走生成并 update 同一行；
   未过期 ⇒ 一次模型都不调；
④ **失败不再被就地吞掉**：`generate_missing_diaries` 有失败就抛（让 `run_daily_if_due` 记失败退避重试），
   但"该日没有新对话"（skip）不算失败 ⇒ 0 篇仍正常记 done；且**每个角色每天都照样被尝试**（不是第一个失败就中断）。

纪律：临时库（`_dbclone`），绝不连生产库；`chat_completion`／`save_memory` 全部打桩＝零计费。
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from _dbclone import clone_engine, make_session_factory

import app.scheduling.diary_generator as dg

USER_ID, CHAR_ID = 7701, 7702
SESSION_ID = 7703
TARGET = date(2026, 10, 8)          # 北京日记日
DAY_START_UTC = datetime(2026, 10, 7, 16)     # 北京 10-08 0 点


@pytest.fixture(scope="module")
def diary_db(tmp_path_factory):
    db_file = (tmp_path_factory.mktemp("a41diary") / "d.db").as_posix()
    engine = clone_engine(db_file)
    factory = make_session_factory(engine)

    async def _seed():
        """分四段各自提交：同一事务里连加多张有外键关系的表，SQLite 的 FK 检查会先看到
        proactive_settings 而报"父行不存在"（本文件第一版就是这么红的）。"""
        from app.models.chat import ChatMessage, ChatSession
        from app.models.character import AICharacter, ProactiveSettings
        from app.models.user import User

        async with factory() as db:
            db.add(User(id=USER_ID, username="a41_diary_user", nickname="本人"))
            db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name="酱", personality="温柔",
                               is_active=True, self_statement="我是酱", bio=""))
            await db.commit()
        async with factory() as db:
            db.add(ProactiveSettings(character_id=CHAR_ID, diary_enabled=True))
            db.add(ChatSession(id=SESSION_ID, user_id=USER_ID, character_id=CHAR_ID, title="t"))
            await db.commit()
        async with factory() as db:
            # 日记写完（10-08 23:30 北京＝15:30 UTC）之后，当天 23:50 北京＝15:50 UTC 又有一条
            db.add(ChatMessage(session_id=SESSION_ID, sender_type="user",
                               content="对了，我周五要出差", created_at=datetime(2026, 10, 8, 15, 50)))
            await db.commit()

    asyncio.run(_seed())
    old = dg.async_session_factory
    dg.async_session_factory = factory
    yield factory
    dg.async_session_factory = old
    asyncio.run(engine.dispose())


@pytest.fixture
def no_llm(monkeypatch):
    """模型与记忆写入全部打桩：本文件一次都不该花钱。"""
    calls = {"llm": 0, "mem": 0}

    async def _fake_llm(messages=None, **kw):
        calls["llm"] += 1
        return "今天和他聊了出差的事。"

    async def _fake_save(**kw):
        calls["mem"] += 1
        return None

    async def _fake_session_id(user_id, character_id):
        return SESSION_ID

    import app.application.chat_service as chat_svc
    import app.memory as mem_pkg

    monkeypatch.setattr(dg, "chat_completion", _fake_llm)
    monkeypatch.setattr(chat_svc, "get_latest_session_id", _fake_session_id)
    monkeypatch.setattr(mem_pkg, "save_memory", _fake_save, raising=False)
    return calls


# ───────────────────────── ① 日界口径只有一份 ─────────────────────────

def test_北京日窗口逐字对齐既有口径():
    start, end = dg._day_window(TARGET, True)
    assert start == datetime(2026, 10, 8, tzinfo=timezone.utc) - timedelta(hours=8)
    assert end - start == timedelta(days=1)
    assert start.tzinfo is not None and end.tzinfo is not None


def test_UTC窗口分支还留着且没被顺手改掉():
    start, end = dg._day_window(TARGET, beijing_window=False)
    assert start == datetime(2026, 10, 8, tzinfo=timezone.utc)
    assert (end - start) == timedelta(days=1)


# ───────────────────────── ② 过期判据真值表 ─────────────────────────

@pytest.mark.parametrize("created,latest,want,why", [
    (datetime(2026, 10, 8, 15, 30), datetime(2026, 10, 8, 15, 50), True, "之后有新消息"),
    (datetime(2026, 10, 8, 15, 59), datetime(2026, 10, 8, 15, 30), False, "消息在日记之前"),
    (datetime(2026, 10, 8, 15, 30), datetime(2026, 10, 8, 15, 30), False, "同一刻不算更新"),
    (datetime(2026, 10, 8, 15, 30), None, False, "读不到最新消息⇒保守跳过"),
    (None, datetime(2026, 10, 8, 15, 50), False, "日记没有写入时刻⇒不许每拍重写"),
    (None, None, False, "两头都读不到"),
])
def test_过期判据真值表(created, latest, want, why):
    assert dg._diary_is_stale(created, latest) is want, why


def test_带时区的值不许把比较炸掉():
    aware_created = datetime(2026, 10, 8, 23, 30, tzinfo=timezone(timedelta(hours=8)))   # 15:30 UTC
    assert dg._diary_is_stale(aware_created, datetime(2026, 10, 8, 15, 50)) is True
    assert dg._diary_is_stale(aware_created, datetime(2026, 10, 8, 15, 10)) is False
    assert dg._diary_is_stale("不是时间", datetime(2026, 10, 8, 15, 50)) is False


# ───────────────────────── ③ 分支真的接上了（端到端，临时库） ─────────────────────────

def _seed_diary(factory, created_at):
    async def _w():
        from sqlalchemy import delete

        from app.models.life import AIDiary
        async with factory() as db:
            # 先清掉同一天已有的行：上一条用例刚 update 过，不清就会撞 scalar_one_or_none
            await db.execute(delete(AIDiary).where(AIDiary.character_id == CHAR_ID))
            db.add(AIDiary(character_id=CHAR_ID, diary_date=TARGET.strftime("%Y-%m-%d"),
                           content="旧日记（缺最后那句出差）", created_at=created_at))
            await db.commit()
    asyncio.run(_w())


def _read_diary(factory):
    async def _r():
        from app.models.life import AIDiary
        from sqlalchemy import select
        async with factory() as db:
            return (await db.execute(select(AIDiary).where(
                AIDiary.character_id == CHAR_ID, AIDiary.diary_date == TARGET.strftime("%Y-%m-%d")
            ))).scalars().all()
    return asyncio.run(_r())


def test_日记写完之后又有新对话_必须重写而不是跳过(diary_db, no_llm):
    _seed_diary(diary_db, datetime(2026, 10, 8, 15, 30))     # 该日 15:50 还有消息（见 fixture）
    out = asyncio.run(dg.generate_diary_for_character(CHAR_ID, TARGET))
    assert no_llm["llm"] == 1, "判据没接上：过期日记仍被当成「已有日记」跳过 ⇒ 那截对话永久丢了"
    assert out is not None
    rows = _read_diary(diary_db)
    assert len(rows) == 1, f"重写该 update 同一行，不该长出第二篇：{len(rows)}"
    assert rows[0].content == "今天和他聊了出差的事。", rows[0].content
    assert "旧日记" not in rows[0].content, "旧内容还在 ⇒ 其实没重写，只是又多读了一遍"


def test_日记已经是最新的_一次模型都不调(diary_db, no_llm):
    _seed_diary(diary_db, datetime(2026, 10, 8, 16, 30))     # 晚于当天最后一条消息
    out = asyncio.run(dg.generate_diary_for_character(CHAR_ID, TARGET))
    assert out is None
    assert no_llm["llm"] == 0, "没过期也去重写＝把「每天一篇」变成「每拍一篇」（真金白银）"


# ───────────────────────── ④ 失败不再被就地吞掉 ─────────────────────────

class _Settings(list):
    """假会话：一句 `select(ProactiveSettings)` 就返回这批设置行。"""

    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def __call__(self, *a, **k):
        return self

    async def execute(self, *a, **k):
        rows = self.rows

        class _R:
            def scalars(_s):
                class _A:
                    def all(_x):
                        return list(rows)
                return _A()
        return _R()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _settings(n):
    from types import SimpleNamespace
    return [SimpleNamespace(character_id=9000 + i) for i in range(n)]


def test_有失败就抛且每个角色都被尝试(monkeypatch):
    """失败必须冒到 `run_daily_if_due`（它才会 mark_failed_daily 退避重试）；
    但不能因为一个角色失败就中断其余角色／其余日期。"""
    attempts = []

    async def _boom(char_id, target_date, *a, **k):
        attempts.append((char_id, target_date))
        if char_id == 9001:
            raise RuntimeError("LLM 挂了")
        return None

    monkeypatch.setattr(dg, "async_session_factory", _Settings(_settings(3)))
    monkeypatch.setattr(dg, "generate_diary_for_character", _boom)
    with pytest.raises(RuntimeError) as e:
        asyncio.run(dg.generate_missing_diaries())
    assert "failed" in str(e.value), str(e.value)
    assert len(attempts) == 3 * 3, f"一个失败就中断＝其余角色今天彻底没机会：{len(attempts)}"


def test_全部跳过时不抛异常且返回计数(monkeypatch):
    """「该日没有新对话」不是失败：0 篇仍要正常记 done，否则每天空转重试。"""
    async def _skip(char_id, target_date, *a, **k):
        return None

    monkeypatch.setattr(dg, "async_session_factory", _Settings(_settings(2)))
    monkeypatch.setattr(dg, "generate_diary_for_character", _skip)
    counts = asyncio.run(dg.generate_missing_diaries())
    assert counts["failed"] == 0, counts
    assert counts["skipped"] == counts["attempted"] == 2 * 3, counts


def test_diary_仍然走每天一次的那把闸():
    """本单只改生成器，不许顺手把调度语义改掉（run_daily_if_due 才是"每天最多一次 + 失败退避"）。"""
    import pathlib

    src = pathlib.Path(dg.__file__).with_name("scheduler.py").read_text(encoding="utf-8")
    assert src.count('run_daily_if_due("diary"') == 1, "diary 的每日一次语义被挪走了"
    assert "run_if_due(\"diary\"" not in src, "退成间隔型＝一天可能跑好几篇"
