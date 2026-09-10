# -*- coding: utf-8 -*-
"""AI 生活主动消息「主体归属 + 同主题复读」治理（2026-09-09，L0/L1/L2/L5）单测。

覆盖：
- L1 `classify_ai_promise_side`（AI 自理 vs 为用户）+ `extract_timer` 分流与 flag 关回退；
- L0 `topic_bucket` / `topic_closed_by_user` / `should_suppress`（含 fail-open）；
- L2 `_build_timer_hint` 三套话术 + `_build_timer_hint_legacy` 旧话术（flag 关逐字节等价）；
- L5 `life_writer` 的 locked 判定 / 重试包装 / 悬空活动收尾。

全部零 LLM；涉及 DB 的用例走 pytest 全局临时库（conftest 建表），不碰生产库。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import OperationalError

from app.agent.loop import AGENT_FLAGS
from app.life import life_writer
from app.life.life_loop import _LOCK_RETRY_DELAYS, _retry_on_lock
from app.scheduling import arbiter
from app.scheduling.proactive_topic_guard import (
    GUARD_EXEMPT_TYPES,
    should_suppress,
    topic_bucket,
    topic_closed_by_user,
)
from app.scheduling.promise_parser import classify_ai_promise_side, extract_timer


@pytest.fixture
def flags():
    """临时改 AGENT_FLAGS，用例结束自动还原（防污染其它用例）。"""
    import copy
    snapshot = copy.deepcopy(AGENT_FLAGS)
    yield AGENT_FLAGS
    AGENT_FLAGS.clear()
    AGENT_FLAGS.update(snapshot)


# ─────────────── L1：AI 承诺受益方分类 ───────────────

def test_L1_自理离开判为self():
    assert classify_ai_promise_side("我还没去吃饭，先去食堂垫两口") == "self"
    assert classify_ai_promise_side("我去吃饭了，吃完找你") == "self"
    assert classify_ai_promise_side("我去洗个澡，20分钟") == "self"
    assert classify_ai_promise_side("我去开个会，一小时") == "self"


def test_L1_为用户做判为for_user():
    assert classify_ai_promise_side("给你煮的粥20分钟就好，好了叫你") == "for_user"
    assert classify_ai_promise_side("我给你下面，15分钟出锅") == "for_user"
    assert classify_ai_promise_side("粥在锅里，帮你热着，等你回来吃") == "for_user"


def test_L1_无明确信号默认self():
    # 安全侧：AI 自述要去做某事，默认不反过来招呼用户
    assert classify_ai_promise_side("我出去办点事，半小时") == "self"
    assert classify_ai_promise_side("") == "self"


def test_L1_AI自理分流为back(flags):
    flags["promise_self_side_split"] = True
    info = extract_timer("我先去食堂吃个饭，40分钟后回来", user_id=1, character_id=13,
                         session_id=11, source_message_id=100, sender="ai")
    assert info is not None
    assert info["event_type"] == "back"        # 原命中 meal 正则，按 side 纠偏为 back
    assert info["side"] == "self"


def test_L1_flag关时保持原解析(flags):
    flags["promise_self_side_split"] = False
    info = extract_timer("我先去食堂吃个饭，40分钟后回来", user_id=1, character_id=13,
                         session_id=11, source_message_id=100, sender="ai")
    assert info is not None
    assert info["event_type"] == "meal"        # 旧行为：F1a 现状正则结果
    assert info["side"] == "user"


def test_L1_为用户做保留ready(flags):
    flags["promise_self_side_split"] = True
    info = extract_timer("给你熬了粥，20分钟好，好了叫你", user_id=1, character_id=13,
                         session_id=11, source_message_id=100, sender="ai")
    assert info is not None
    assert info["event_type"] == "ready"
    assert info["side"] == "for_user"


def test_L1_用户侧不受影响(flags):
    flags["promise_self_side_split"] = True
    info = extract_timer("我去洗20分钟澡", user_id=1, character_id=13,
                         session_id=11, source_message_id=100, sender="user")
    assert info is not None
    assert info["event_type"] == "shower"      # 用户承诺维持现状
    assert info["side"] == "user"


def test_L1_显式timer标签仍是back(flags):
    flags["promise_self_side_split"] = True
    info = extract_timer("我出去一下[timer:20m]", user_id=1, character_id=13,
                         session_id=11, source_message_id=100, sender="ai")
    assert info is not None
    assert info["event_type"] == "back"


# ─────────────── L0：主题分桶与闭环 ───────────────

def test_L0_主题分桶():
    assert topic_bucket("粥好了，快来吃吧") == "meal"
    assert topic_bucket("醒了就过来，粥要凉了") == "meal"
    assert topic_bucket("别睡了，起床吃饭") == "meal"   # meal 优先于 sleep
    assert topic_bucket("快去洗澡吧") == "shower"
    assert topic_bucket("到家了记得说一声") == "commute"
    assert topic_bucket("在干嘛呢") is None
    assert topic_bucket("") is None


def test_L0_用户闭环识别():
    assert topic_closed_by_user(["我去上课了", "中午吃过面了"], "meal") is True
    assert topic_closed_by_user(["吃过了，在饭堂"], "meal") is True
    assert topic_closed_by_user(["不用了，别催"], "meal") is True
    assert topic_closed_by_user(["在干嘛"], "meal") is False
    assert topic_closed_by_user([], "meal") is False
    assert topic_closed_by_user(["吃过了"], None) is False  # 无主题桶恒 False


def test_L0_节庆白名单豁免():
    assert "birthday" in GUARD_EXEMPT_TYPES
    assert "holiday" in GUARD_EXEMPT_TYPES
    assert "timer" not in GUARD_EXEMPT_TYPES


def test_L0_无主题不抑制():
    assert asyncio.run(should_suppress(13, "在干嘛呢")) == (False, "no-topic")


def test_L0_异常fail_open(monkeypatch):
    """闸门自身故障（DB 不可用）必须放行，绝不阻塞正常主动消息。"""
    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.scheduling.proactive_topic_guard.async_session_factory", _boom)
    assert asyncio.run(should_suppress(13, "粥好了快来吃")) == (False, "guard-error-fail-open")


# ─────────────── L2：到期话术三套 + 旧话术等价 ───────────────

def test_L2_AI自理back是自述回来():
    hint = arbiter._build_timer_hint("sam", "ai", "back", "去吃饭")
    assert "你回来了" in hint and "去吃饭" in hint
    assert "禁止招呼用户" in hint          # 明确禁止把用户当受益方
    assert "这件事是你自己去做的" in hint


def test_L2_AI为用户ready不再写死粥():
    hint = arbiter._build_timer_hint("sam", "ai", "ready", "给你熬的粥")
    assert "粥" in hint
    assert "不要凭空换成粥/饭" in hint       # 约束用真实事物
    assert "比如'粥好了'" not in hint        # 硬编码 few-shot 已删除


def test_L2_用户ready仍是询问():
    hint = arbiter._build_timer_hint("sam", "user", "ready", "饭马上好")
    assert "TA 是不是弄好了" in hint
    assert "不要替用户说" in hint


def test_L2_旧话术保留硬编码示例(flags):
    """flag 关时走 legacy：与上线前逐字节一致（含硬编码'粥好了'与他/她措辞）。"""
    legacy = arbiter._build_timer_hint_legacy("sam", "ai", "ready", "去吃饭")
    assert "比如'粥好了'" in legacy
    assert legacy == (
        "你是sam，之前你和用户说「去吃饭」，现在时间到了。请自然地告诉用户你答应弄好的事完成了"
        "（比如'粥好了'，1句话，像朋友一样）。"
    )
    assert "他/她" in arbiter._build_timer_hint_legacy("sam", "user", "ready", "")


# ─────────────── L5：life 写记忆加固 ───────────────

def test_L5_锁冲突判定():
    assert life_writer.is_locked(Exception("database is locked")) is True
    assert life_writer.is_locked(Exception("database table is locked")) is True
    assert life_writer.is_locked(Exception("no such table: x")) is False


def test_L5_重试退避间隔沿用09_08基线():
    """life_loop 原 _retry_on_lock 已收敛到 life_writer，退避间隔与调用次数不变。"""
    assert _LOCK_RETRY_DELAYS == (0.3, 0.6)
    calls = []

    async def always_locked():
        calls.append(1)
        raise OperationalError("INSERT ...", {}, Exception("database is locked"))

    with pytest.raises(OperationalError):
        asyncio.run(_retry_on_lock(always_locked, "test"))
    assert len(calls) == 3  # 首次 + 2 次重试


def test_L5_flag关时直连save_memory(monkeypatch, flags):
    """flag 关=回旧裸写路径：异常照旧上抛（不吞、不改失败语义）。"""
    flags["life_memory_write_retry"] = False
    calls = []

    async def _fake(**kw):
        calls.append(kw)
        raise OperationalError("INSERT ...", {}, Exception("database is locked"))

    monkeypatch.setattr("app.memory.service.save_memory", _fake)
    with pytest.raises(OperationalError):
        asyncio.run(life_writer.save_life_memory_with_retry(user_id=1, character_id=13))
    assert len(calls) == 1


def test_L5_flag开时写记忆失败返回None不抛出(monkeypatch, flags):
    flags["life_memory_write_retry"] = True
    monkeypatch.setattr(life_writer, "MEMORY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    calls = []

    async def _locked(**kw):
        calls.append(kw)
        raise OperationalError("INSERT ...", {}, Exception("database is locked"))

    monkeypatch.setattr("app.memory.service.save_memory", _locked)
    assert asyncio.run(life_writer.save_life_memory_with_retry(user_id=1, character_id=13)) is None
    assert len(calls) == 4  # 首次 + 3 次重试后放弃，活动仍应 completed


def test_L5_悬空活动收尾(flags):
    """started 超窗无 completed_at → 置 timeout（两套活动系统共用一张表，一处扫描）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.life import LifeActivityLog

    flags["life_memory_write_retry"] = True

    async def _run():
        from sqlalchemy import delete as _del
        from sqlalchemy import select as _sel
        from app.models.character import AICharacter
        from app.models.user import User

        # 引擎已强制外键（2026-09-09）：ai_characters.user_id 需父 users 行，先建主账号（测试后删除）
        async with async_session_factory() as db:
            if (await db.execute(_sel(User.id).where(User.id == 1))).scalar_one_or_none() is None:
                db.add(User(id=1, username="promise_admin", nickname="Promise 测试", is_admin=True))
                await db.commit()
            db.add(AICharacter(id=999991, user_id=1, name="orphan_fk_test"))
            await db.commit()
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        async with async_session_factory() as db:
            orphan = LifeActivityLog(character_id=999991, activity_type="study",
                                     status="started", started_at=old)
            db.add(orphan)
            await db.commit()
            oid = orphan.id
        try:
            n = await life_writer.close_orphan_activities(minutes=30)
            assert n >= 1
            async with async_session_factory() as db:
                row = (await db.execute(
                    select(LifeActivityLog).where(LifeActivityLog.id == oid)
                )).scalar_one()
                assert row.status == "timeout"
                assert "orphaned" in (row.output_json or "")
        finally:
            from app.models.character import AICharacter
            async with async_session_factory() as db:
                r = (await db.execute(
                    select(LifeActivityLog).where(LifeActivityLog.id == oid)
                )).scalar_one_or_none()
                if r is not None:
                    await db.delete(r)
                await db.execute(_del(AICharacter).where(AICharacter.id == 999991))
                await db.commit()

    asyncio.run(_run())


def test_L5_悬空收尾flag关时不动库(flags):
    flags["life_memory_write_retry"] = False
    assert asyncio.run(life_writer.close_orphan_activities()) == 0
