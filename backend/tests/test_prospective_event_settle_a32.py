# -*- coding: utf-8 -*-
"""A32（2026-10-07）前瞻意图「事件已兑现 → 静默关闭」＋ cue 到达类变体。

真机现场（char=13，2026-10-06）：用户 12:54 说「我到家了」，15:09 反问「我不是早就到家了吗」——
期间承诺意图 171~174 在 13:06 / 14:11 / 14:12 / 15:21 连着 fire 催问。根因（派单 §0 已核）：
``match_cue_intents`` 只收 ``kind=="cue"``，promise 没有「事件兑现即静默退场」的在线路径，
唯一关闭口是 ``collect_due_promises → claim_intent_for_fire → 发消息 → mark_discharged_many``，
于是「已发生的事件」永远不能让它等的那句话退场。

本单补两条确定性关闭口（判定**零 LLM**，只用正则/字面）：
① ``settle_promises_on_user_message``——用户消息落库当时结算（接线在 chat_service._run_agent_core）；
② ``run_prospective_due`` 认领**之前**的现状校验闸门——本会话最新用户消息 + 现状锚（双保险）。

钉住的语义（派单 §2 / §5）：
- 静默关闭**不产生新的 ``proactive_message_logs`` 行、不发送任何消息、不调 LLM**（每条用例都数一遍）；
- 只对 ``kind=promise`` 且 ``status in (pending, matched)`` 且**非 stale** 的行生效（宁可漏关，不可误关）；
- promise 既有 fire 语义不变：无信号时照旧「认领 → 生成 → 发送 → discharged」；
- cue 变体只查 ``_CUE_VARIANTS`` 逐条列出的到达类形态，不做开放式语义匹配。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；DB 用例走 tests/_dbclone.py 临时库，不碰生产库。）
"""
import asyncio
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from _dbclone import clone_engine, make_session_factory

from app.models.chat import ChatMessage
from app.models.character import ProactiveMessageLog
from app.models.memory import ProspectiveIntent

# 北京 2026-10-06 12:00（库内 naive UTC 04:00）；日期型 due_end 当天有效（必须与上面同一北京日，
# 否则 collect_due_promises 的跨天硬闸会先把候选打成 stale）
_FIXED_UTC = datetime(2026, 10, 6, 4, 0, 0)
_DUE_TODAY = datetime(2026, 10, 6, 23, 59)

_ARRIVAL_PROMISE = "我承诺今晚等用户到家以后把火锅订好告诉他"
_MED_PROMISE = "我承诺提醒用户吃过药再睡"
_NEUTRAL_PROMISE = "我承诺晚饭由我来做"       # 不等任何「到家/吃药」类事件信号 ⇒ 永不误关


# ───────────────────────── 纯函数（快测档，零 DB、零 LLM）─────────────────────────

def test_promise_awaits_routes_arrival_and_medication():
    """``_promise_awaits``：只认「在等哪一类事件」，等不到事件信号的承诺返回 None（不参与结算）。"""
    from app.scheduling.prospective_intent import _promise_awaits
    assert _promise_awaits(_ARRIVAL_PROMISE) == "arrival"
    assert _promise_awaits(_MED_PROMISE) == "medication"
    assert _promise_awaits(_NEUTRAL_PROMISE) is None
    assert _promise_awaits("") is None
    assert _promise_awaits(None) is None


def test_signal_seen_is_kind_scoped():
    """``_signal_seen``：arrival 与 medication 两条正则不互串；None/空串安全；裸「吃了」只算吃药。"""
    from app.scheduling.prospective_intent import _signal_seen
    assert _signal_seen("arrival", "我到家了") is True
    assert _signal_seen("arrival", "刚回到家里") is True
    assert _signal_seen("arrival", "我在楼下了") is True
    assert _signal_seen("medication", "我到家了") is False
    assert _signal_seen("medication", "吃过药了") is True
    assert _signal_seen("medication", "吃了") is True      # ^吃了$ 整句锚定
    assert _signal_seen("arrival", "吃了") is False        # 「吃了」不得算到达信号
    assert _signal_seen("arrival", None, "") is False      # 缺文本 fail-open（判不出＝不关）
    assert _signal_seen("arrival", None, "今天的现状：用户已到家") is True


def test_cue_hit_literal_plus_arrival_variants():
    """B.3.3：原字面子串能力不退化，另加到达类高频变体；表外的词不放开式语义匹配。"""
    from app.scheduling.prospective_intent import _cue_hit
    assert _cue_hit(["到了"], "我到家了") is True           # 派单 §5.3 指定的这一档
    assert _cue_hit(["到家"], "我刚回到家里") is True
    assert _cue_hit(["回来"], "我回来了") is True
    assert _cue_hit(["上车"], "已经上车了") is True
    assert _cue_hit(["进门"], "到家啦") is True
    assert _cue_hit(["团子"], "团子饿了") is True            # 字面子串（原行为）
    assert _cue_hit(["火锅"], "我到家了") is False           # 非到达类不放大匹配
    assert _cue_hit(["到了"], "今天好累") is False
    assert _cue_hit(["到了"], "") is False
    assert _cue_hit([""], "任何东西") is False               # 单字/空线索按原规则跳过


# ───────────────────────── DB 夹具（临时库，不碰生产库）─────────────────────────

@pytest.fixture()
def a32_db(monkeypatch, tmp_path):
    """账号 1 / 角色 11 / 私聊会话 7；预置 1 条主动消息日志用于「静默关闭不新增日志行」的对照。"""
    engine = clone_engine(os.path.join(str(tmp_path), "a32.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.chat import ChatSession
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="u_a32", nickname="A32 用户"))
            db.add(AICharacter(id=11, user_id=1, name="sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            db.add(ChatSession(id=7, user_id=1, character_id=11))
            await db.commit()
        # 父行先提交再挂子行（_dbclone 默认开 FK，与生产同款 PRAGMA）
        async with factory() as db:
            # 预置 1 条主动消息日志：用来钉「静默关闭不新增日志行」（before == after）
            db.add(ProactiveMessageLog(
                character_id=11, session_id=7, message_type="prospective_intent",
                content="之前那次催问", created_at=_FIXED_UTC - timedelta(hours=1),
            ))
            await db.commit()

    asyncio.run(_seed())
    import app.db.database as db_mod
    import app.scheduling.prospective_intent as pi
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(pi, "async_session_factory", factory)
    # 钉死「现在」：_now_local_naive 内部调用 _now_naive，一处替换即北京 10-06 12:00
    monkeypatch.setattr(pi, "_now_naive", lambda: _FIXED_UTC)
    yield factory
    asyncio.run(engine.dispose())


async def _counts(factory):
    """(主动消息日志条数, 会话 7 消息条数)——静默关闭必须两个数都不涨。"""
    async with factory() as db:
        logs = (await db.execute(select(func.count()).select_from(ProactiveMessageLog))).scalar() or 0
        msgs = (await db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == 7)
        )).scalar() or 0
    return int(logs), int(msgs)


async def _row(factory, pis_id):
    async with factory() as db:
        return await db.get(ProspectiveIntent, pis_id)


async def _add_user_message(factory, text, *, at=_FIXED_UTC):
    async with factory() as db:
        db.add(ChatMessage(session_id=7, sender_type="user", content=text, created_at=at))
        await db.commit()


async def _add_intent(factory, **kw):
    kw.setdefault("status", "pending")
    async with factory() as db:
        row = ProspectiveIntent(user_id=1, character_id=11, **kw)
        db.add(row)
        await db.commit()
        return row.id


def _spy_llm_and_send(monkeypatch):
    """把 LLM 与发送出口打桩并记录调用（闸门命中时两者必须一次都不被调）。"""
    prompts, sends = [], []

    async def _llm(**kw):
        prompts.append(kw["messages"][-1]["content"])
        return "火锅我订好啦，周六晚上那顿说定了。"

    async def _send(session_id, character_id, user_id, content,
                    message_type="prospective_intent", **kw):
        sends.append(content)

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
    return prompts, sends


# ─────────────── §5.1 用户消息落库当时的静默结算（settle）───────────────

@pytest.mark.slow
def test_settle_discharges_arrival_promise_and_writes_nothing(a32_db):
    """「我到家了」→ 在等到家的那条承诺置 discharged；主动消息日志与会话消息**一条都不新增**。"""
    from app.scheduling.prospective_intent import settle_promises_on_user_message, upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5001, chat_session_id=7,
    ))
    neutral = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_NEUTRAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5002, chat_session_id=7,
    ))
    logs0, msgs0 = asyncio.run(_counts(a32_db))

    settled = asyncio.run(settle_promises_on_user_message(11, 7, "我到家了"))

    assert settled == [pid]                                  # 只关在等到家的那条
    row = asyncio.run(_row(a32_db, pid))
    assert row.status == "discharged" and row.discharged_at is not None
    assert asyncio.run(_row(a32_db, neutral)).status == "pending"  # 不等信号的承诺不动
    assert asyncio.run(_counts(a32_db)) == (logs0, msgs0)    # 零新日志、零新消息


@pytest.mark.slow
def test_settle_discharges_medication_signal_only_for_medication_promise(a32_db):
    """吃药信号只关吃药承诺；同一句「吃了」不得顺手关到家类（kind 分派不串）。"""
    from app.scheduling.prospective_intent import settle_promises_on_user_message, upsert_intent

    med = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_MED_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5101, chat_session_id=7,
    ))
    arr = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5102, chat_session_id=7,
    ))
    logs0, msgs0 = asyncio.run(_counts(a32_db))

    assert asyncio.run(settle_promises_on_user_message(11, 7, "吃了")) == [med]
    assert asyncio.run(_row(a32_db, arr)).status == "pending"
    assert asyncio.run(_counts(a32_db)) == (logs0, msgs0)


@pytest.mark.slow
def test_settle_touches_only_live_promise_rows(a32_db):
    """保守边界：stale / 终态 / cue 一律不碰（宁可漏关，不可误关）。"""
    from app.scheduling.prospective_intent import settle_promises_on_user_message

    stale_pid = asyncio.run(_add_intent(
        a32_db, content=_ARRIVAL_PROMISE, kind="promise", due_end=None,
        created_at=_FIXED_UTC - timedelta(days=40),   # 无 due 且创建超 STALE_NODUE_DAYS=30 → stale
    ))
    done_pid = asyncio.run(_add_intent(
        a32_db, content=_ARRIVAL_PROMISE, kind="promise", due_end=_DUE_TODAY, status="discharged",
    ))
    cue_pid = asyncio.run(_add_intent(
        a32_db, content="用户说到到家的事", kind="cue", cue_terms_json='["到家"]',
        due_end=_DUE_TODAY,
    ))
    live_matched = asyncio.run(_add_intent(
        a32_db, content=_ARRIVAL_PROMISE, kind="promise", due_end=_DUE_TODAY, status="matched",
    ))

    settled = asyncio.run(settle_promises_on_user_message(11, 7, "我已经到家了"))

    assert settled == [live_matched]                         # 只有 pending/matched 的非 stale promise
    assert asyncio.run(_row(a32_db, stale_pid)).status == "pending"
    assert asyncio.run(_row(a32_db, cue_pid)).status == "pending"
    assert asyncio.run(_row(a32_db, done_pid)).status == "discharged"
    assert asyncio.run(_row(a32_db, live_matched)).discharged_at is not None


@pytest.mark.slow
def test_settle_empty_or_non_signal_text_is_noop(a32_db):
    """空正文 / 无信号正文 → 返回空列表、零写入（不参与结算的正文不得误关承诺）。"""
    from app.scheduling.prospective_intent import settle_promises_on_user_message, upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5201, chat_session_id=7,
    ))
    logs0, msgs0 = asyncio.run(_counts(a32_db))
    for text in ("", "   ", None, "今天好累", "在忙吗"):
        assert asyncio.run(settle_promises_on_user_message(11, 7, text)) == []
    assert asyncio.run(_row(a32_db, pid)).status == "pending"
    assert asyncio.run(_counts(a32_db)) == (logs0, msgs0)


@pytest.mark.slow
def test_settle_scoped_to_character(a32_db):
    """只结算本角色的承诺：换个角色发「我到家了」不得关到 11 的承诺。"""
    from app.scheduling.prospective_intent import settle_promises_on_user_message, upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5301, chat_session_id=7,
    ))
    assert asyncio.run(settle_promises_on_user_message(99, None, "我到家了")) == []
    assert asyncio.run(_row(a32_db, pid)).status == "pending"


# ─────────────── §5.2 fire 前的现状校验闸门（双保险）───────────────

@pytest.mark.slow
def test_fire_gate_settles_silently_without_llm(a32_db, monkeypatch):
    """派单 §5.2：本会话最新用户消息已含到达信号 ⇒ run_prospective_due 不调 LLM、不发送、静默关闭。

    A33 ⑥（2026-10-07）口径更新：采集层已对「日期型＋事件型」先做现状校验，信号**在采集前就出现**
    的承诺根本不再进候选（见 test_prospective_trigger_a33）。本例因此把「我到家了」挪到采集之后，
    保留 A32 原本要钉的东西：候选照常产出 → 闸门在**认领之前**兜住 → 零 LLM／零发送／零新日志。
    """
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import collect_due_promises, run_prospective_due, upsert_intent

    prompts, sends = _spy_llm_and_send(monkeypatch)
    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5401, chat_session_id=7,
    ))

    cands = asyncio.run(collect_due_promises())
    assert pid in {c["pis_id"] for c in cands}               # 尚未兑现 ⇒ 候选照常产出（不改 due 计算口径）
    cand = [c for c in cands if c["pis_id"] == pid][0]

    asyncio.run(_add_user_message(a32_db, "我到家了"))       # 事件在采集之后才发生
    logs0, msgs0 = asyncio.run(_counts(a32_db))

    assert asyncio.run(run_prospective_due(cand)) is True     # True＝已处理，arbiter 不再重试
    assert prompts == [] and sends == []                     # 一次 LLM 都没调、一条消息都没发
    assert asyncio.run(_counts(a32_db)) == (logs0, msgs0)    # 零新 proactive_message_logs
    row = asyncio.run(_row(a32_db, pid))
    assert row.status == "discharged" and row.discharged_at is not None
    assert pi._now_naive() == _FIXED_UTC


@pytest.mark.slow
def test_fire_gate_also_reads_state_anchor(a32_db, monkeypatch):
    """闸门第二个来源＝现状锚：会话里没信号、但锚里已写「已到家」同样静默关闭。"""
    from app.scheduling.prospective_intent import run_prospective_due, upsert_intent

    prompts, sends = _spy_llm_and_send(monkeypatch)
    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5501, chat_session_id=7,
    ))
    asyncio.run(_add_user_message(a32_db, "今天好累"))

    async def _anchor(*a, **kw):
        return "用户已到家，正在休息"

    monkeypatch.setattr("app.scheduling.state_guard.current_state_anchor", _anchor)
    logs0, msgs0 = asyncio.run(_counts(a32_db))

    assert asyncio.run(run_prospective_due({
        "pis_id": pid, "character_id": 11, "user_id": 1, "session_id": 7,
        "content": _ARRIVAL_PROMISE, "due_end": _DUE_TODAY, "side": "self",
    })) is True
    assert prompts == [] and sends == []
    assert asyncio.run(_counts(a32_db)) == (logs0, msgs0)
    assert asyncio.run(_row(a32_db, pid)).status == "discharged"


@pytest.mark.slow
def test_fire_gate_only_looks_at_latest_user_message(a32_db, monkeypatch):
    """只看**最新一条用户消息**：角色自己问「到家了吗」不算兑现；更早的用户兑现消息也不作数。"""
    from app.scheduling.prospective_intent import run_prospective_due, upsert_intent

    prompts, sends = _spy_llm_and_send(monkeypatch)
    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5601, chat_session_id=7,
    ))

    async def _anchor(*a, **kw):
        return ""

    monkeypatch.setattr("app.scheduling.state_guard.current_state_anchor", _anchor)

    async def _seed_msgs():
        async with a32_db() as db:
            db.add(ChatMessage(session_id=7, sender_type="user", content="我到家了",
                               created_at=_FIXED_UTC - timedelta(hours=2)))
            db.add(ChatMessage(session_id=7, sender_type="ai", content="到家了吗？",
                               created_at=_FIXED_UTC - timedelta(minutes=30)))
            db.add(ChatMessage(session_id=7, sender_type="user", content="还没呢，堵在路上",
                               created_at=_FIXED_UTC - timedelta(minutes=10)))
            await db.commit()

    asyncio.run(_seed_msgs())

    assert asyncio.run(run_prospective_due({
        "pis_id": pid, "character_id": 11, "user_id": 1, "session_id": 7,
        "content": _ARRIVAL_PROMISE, "due_end": _DUE_TODAY, "side": "self",
    })) is True
    # 最新用户消息无信号 ⇒ 闸门放行 ⇒ 走既有 fire（认领 + 生成一次 + 发送一次）
    assert len(prompts) == 1 and len(sends) == 1
    assert asyncio.run(_row(a32_db, pid)).status == "discharged"


@pytest.mark.slow
def test_fire_path_unchanged_when_no_signal(a32_db, monkeypatch):
    """既有 fire 语义不变（无信号承诺）：认领幂等吃满、正文进提示词、发送一条、日志新增一行由发送侧负责。"""
    from app.scheduling.prospective_intent import collect_due_promises, run_prospective_due, upsert_intent

    prompts, sends = _spy_llm_and_send(monkeypatch)
    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_NEUTRAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=5701, chat_session_id=7,
    ))
    asyncio.run(_add_user_message(a32_db, "我到家了"))       # 有信号，但这条承诺不等这个

    cands = asyncio.run(collect_due_promises())
    cand = [c for c in cands if c["pis_id"] == pid][0]
    assert asyncio.run(run_prospective_due(cand)) is True
    assert len(prompts) == 1 and sends == ["火锅我订好啦，周六晚上那顿说定了。"]
    assert _NEUTRAL_PROMISE in prompts[0] or "晚饭" in prompts[0]
    assert asyncio.run(_row(a32_db, pid)).status == "discharged"


@pytest.mark.slow
def test_latest_user_message_picks_newest_row(a32_db):
    """``_latest_user_message`` 按时间倒序取最新一条用户正文（AI 消息不参与）。"""
    from app.scheduling.prospective_intent import _latest_user_message

    async def _seed():
        async with a32_db() as db:
            db.add(ChatMessage(session_id=7, sender_type="user", content="先说的",
                               created_at=_FIXED_UTC - timedelta(hours=1)))
            db.add(ChatMessage(session_id=7, sender_type="ai", content="我到家了",
                               created_at=_FIXED_UTC))
            db.add(ChatMessage(session_id=7, sender_type="user", content="后说的",
                               created_at=_FIXED_UTC - timedelta(minutes=1)))
            await db.commit()

    asyncio.run(_seed())
    assert asyncio.run(_latest_user_message(7)) == "后说的"
    assert asyncio.run(_latest_user_message(4242)) is None   # 会话不存在 → None（fail-open）


# ─────────────── 接线档：settle 必须挂在「落库后、生成回复前」 ───────────────

def test_settle_wired_in_agent_core_before_generation():
    """接线位钉子（派单 §1(3)）：结算必须在所有用户轮次共用的 ``_run_agent_core`` 里，
    且排在冷战拦截之后、Agent 生成（``agent.ainvoke``）之前——挂错位置＝功能死掉。"""
    import inspect

    from app.application import chat_service
    src = inspect.getsource(chat_service._run_agent_core)
    # 用「调用表达式」而非函数名当锚：函数名在 import 行里也出现，只查名字撤掉调用也判绿
    call = "settle_promises_on_user_message(character_id, session_id, content)"
    assert call in src
    assert src.index("_cold_war_block") < src.index(call)
    assert src.index(call) < src.index("agent.ainvoke")


# ─────────────── §5.3 cue：到达类变体走在线匹配 ───────────────

@pytest.mark.slow
def test_match_cue_intents_hits_arrival_variant(a32_db):
    """cue「到了」在线命中用户「我到家了」→ 返回该行并置 matched（仍零 LLM、不发消息）。"""
    from app.scheduling.prospective_intent import match_cue_intents, upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content="用户说到家要跟一声", kind="cue",
        cue_terms=["到了"], due_end=_DUE_TODAY, source_message_id=5801, chat_session_id=7,
    ))
    other = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content="火锅局", kind="cue",
        cue_terms=["火锅"], due_end=_DUE_TODAY, source_message_id=5802, chat_session_id=7,
    ))
    logs0, msgs0 = asyncio.run(_counts(a32_db))

    hit = asyncio.run(match_cue_intents(11, "我到家了"))

    assert {r.id for r in hit} == {pid}
    assert asyncio.run(_row(a32_db, pid)).status == "matched"
    assert asyncio.run(_row(a32_db, other)).status == "pending"
    assert asyncio.run(_counts(a32_db)) == (logs0, msgs0)
