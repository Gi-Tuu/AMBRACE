# -*- coding: utf-8 -*-
"""A33（2026-10-07，批 2 · B.3.2）前瞻意图「事件 / 时钟显式分类」。

派单来源：`AMBRACE_Sam聊天与记忆本事件印象不更新_代码方案_20261007.md` 第二批。三件事：
① **写入期打标签**——承诺落库时把「在等什么」写进 cue_terms_json 的 `trigger`
   （arrival|medication|clock，口径沿用 A32 正则），**只加元数据、不改 terms**；
② **读侧优先用已存标签**——`_promise_awaits(content, stored=...)` / `effective_intent_trigger(row)`
   缺失（旧 list 格式、坏 JSON、非法值）时才回退按内容推断，升级前旧行行为不变；
③ **事件型不再仅因「到期日当天」就进候选**——`collect_due_promises` 对「日期型 + arrival/medication」
   先做 A32 同款现状校验：信号已出现＝已兑现 → 不进候选并就地置 discharged；未兑现 → 保留
   「当天全天可提起」既有语义。精确时刻型与无 due 刻意不受本条影响（本单只收「当天全天」这一档）。

钉住的语义：
- 采集层关闭同样**零 LLM、零发送、零 proactive_message_logs 新行**；
- trigger 随候选下发，`run_prospective_due` 的 fire 前闸门优先读它（手工 dict 无该键 ⇒ 回退正文推断）；
- 判定唯一来源是 `classify_intent_trigger` / `_promise_awaits`，采集与结算两条路径不各写一套。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；DB 用例走 tests/_dbclone.py 临时库，不碰生产库。）
"""
import asyncio
import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from _dbclone import clone_engine, make_session_factory

from app.models.chat import ChatMessage
from app.models.character import ProactiveMessageLog
from app.models.memory import ProspectiveIntent

_REPO = Path(__file__).resolve().parent.parent

# 北京 2026-10-06 12:00（库内 naive UTC 04:00）；日期型 due_end 当天有效
_FIXED_UTC = datetime(2026, 10, 6, 4, 0, 0)
_DUE_TODAY = datetime(2026, 10, 6, 23, 59)

_ARRIVAL_PROMISE = "我承诺今晚等用户到家以后把火锅订好告诉他"
_MED_PROMISE = "我承诺提醒用户吃过药再睡"
_CLOCK_PROMISE = "我承诺晚饭由我来做"              # 不等事件信号 ⇒ clock
_NO_TAG_ARRIVAL = "我承诺陪用户把体检报告取回来"      # 正文不含「到/药」类正则命中词，只靠标签判定


# ───────────────────────── 纯函数（快测档，零 DB、零 LLM）─────────────────────────

def test_classify_intent_trigger_three_kinds():
    """① 写入期分类：药优先、其次到达类、都不像则 clock（显式写出来，与 A32 正则同口径）。"""
    from app.scheduling.prospective_intent import classify_intent_trigger

    assert classify_intent_trigger(_ARRIVAL_PROMISE) == "arrival"
    assert classify_intent_trigger(_MED_PROMISE) == "medication"      # 「吃药」：药优先于到达
    assert classify_intent_trigger(_CLOCK_PROMISE) == "clock"
    assert classify_intent_trigger(_NO_TAG_ARRIVAL) == "clock"        # 正文判不出＝clock
    assert classify_intent_trigger("") == "clock"
    assert classify_intent_trigger(None) == "clock"


def test_promise_awaits_prefers_stored_tag():
    """② 读侧优先已存标签：clock＝压掉正文推断，arrival/medication＝以标签为准。"""
    from app.scheduling.prospective_intent import _promise_awaits

    assert _promise_awaits(_ARRIVAL_PROMISE, stored="clock") is None
    assert _promise_awaits(_NO_TAG_ARRIVAL, stored="arrival") == "arrival"
    assert _promise_awaits(_CLOCK_PROMISE, stored="medication") == "medication"
    # 无标签（旧行 / 手工 dict）⇒ 按正文推断，A32 行为逐字不变
    assert _promise_awaits(_ARRIVAL_PROMISE) == "arrival"
    assert _promise_awaits(_CLOCK_PROMISE, stored=None) is None
    assert _promise_awaits(_CLOCK_PROMISE, stored="banana") is None    # 非法值＝没标签


def test_effective_intent_trigger_falls_back_without_tag():
    """② 行级读取口：已存标签优先；旧 list 格式 / 坏 JSON / 非法值一律回退正文推断。"""
    from app.scheduling.prospective_intent import effective_intent_trigger, get_intent_trigger

    tagged = SimpleNamespace(content=_CLOCK_PROMISE,
                             cue_terms_json='{"terms": [], "trigger": "arrival"}')
    assert get_intent_trigger(tagged) == "arrival"
    assert effective_intent_trigger(tagged) == "arrival"                # 标签赢过正文

    old_list = SimpleNamespace(content=_ARRIVAL_PROMISE, cue_terms_json='["到家"]')
    bad = SimpleNamespace(content=_ARRIVAL_PROMISE, cue_terms_json="not-json")
    bogus = SimpleNamespace(content=_ARRIVAL_PROMISE, cue_terms_json='{"trigger": 7}')
    for row in (old_list, bad, bogus):
        assert get_intent_trigger(row) is None
        assert effective_intent_trigger(row) == "arrival"               # 回退推断
    assert effective_intent_trigger(
        SimpleNamespace(content=_CLOCK_PROMISE, cue_terms_json="")
    ) == "clock"


# ───────────────────────── DB 夹具（临时库，不碰生产库）─────────────────────────

@pytest.fixture()
def a33_db(monkeypatch, tmp_path):
    """账号 1 / 角色 11 / 私聊会话 7；预置 1 条主动消息日志做「不新增日志行」对照。"""
    engine = clone_engine(os.path.join(str(tmp_path), "a33.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.chat import ChatSession
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="u_a33", nickname="A33 用户"))
            db.add(AICharacter(id=11, user_id=1, name="sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            db.add(ChatSession(id=7, user_id=1, character_id=11))
            await db.commit()
        async with factory() as db:
            # 父行先提交再挂子行（_dbclone 默认开 FK，与生产同款 PRAGMA）
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
    # 现状锚默认置空（个别用例单独打桩），避免用例依赖真实锚内容
    async def _no_anchor(*a, **kw):
        return ""

    monkeypatch.setattr("app.scheduling.state_guard.current_state_anchor", _no_anchor)
    yield factory
    asyncio.run(engine.dispose())


async def _row(factory, pis_id):
    async with factory() as db:
        return await db.get(ProspectiveIntent, pis_id)


async def _payload(factory, pis_id):
    return json.loads((await _row(factory, pis_id)).cue_terms_json or "")


async def _counts(factory):
    """(主动消息日志条数, 会话 7 消息条数)——采集层关闭必须两个数都不涨。"""
    async with factory() as db:
        logs = (await db.execute(select(func.count()).select_from(ProactiveMessageLog))).scalar() or 0
        msgs = (await db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == 7)
        )).scalar() or 0
    return int(logs), int(msgs)


async def _add_intent(factory, **kw):
    kw.setdefault("status", "pending")
    async with factory() as db:
        row = ProspectiveIntent(user_id=1, character_id=11, **kw)
        db.add(row)
        await db.commit()
        return row.id


async def _add_user_message(factory, text, *, at=_FIXED_UTC):
    async with factory() as db:
        db.add(ChatMessage(session_id=7, sender_type="user", content=text, created_at=at))
        await db.commit()


def _ids(cands):
    return {c["pis_id"] for c in cands}


# ─────────────── ① 写入期打标签（三类各一例 ＋ cue 不打）───────────────

@pytest.mark.slow
def test_upsert_intent_writes_trigger_for_three_kinds(a33_db):
    """承诺落库即在 cue_terms_json 打 trigger：arrival / medication / clock 各钉一例。"""
    from app.scheduling.prospective_intent import upsert_intent

    ids = [asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=c, kind="promise",
        due_end=_DUE_TODAY, source_message_id=sid, chat_session_id=7,
    )) for c, sid in ((_ARRIVAL_PROMISE, 7001), (_MED_PROMISE, 7002), (_CLOCK_PROMISE, 7003))]

    triggers = [asyncio.run(_payload(a33_db, i))["trigger"] for i in ids]
    assert triggers == ["arrival", "medication", "clock"]


@pytest.mark.slow
def test_upsert_intent_trigger_is_metadata_only_and_cue_untagged(a33_db):
    """只加元数据：terms/confidence/side 与旧口径逐字一致；cue 不打 trigger。"""
    from app.scheduling.prospective_intent import upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content="下周末带用户去吃火锅", kind="promise",
        cue_terms=["火锅", "周末"], source_message_id=7011, confidence="high"))
    payload = asyncio.run(_payload(a33_db, pid))
    assert payload["terms"] == ["火锅", "周末"]
    assert payload["confidence"] == "high"
    assert payload["side"] == "user"
    assert payload["trigger"] == "clock"

    cue = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content="用户提到家要跟一声", kind="cue",
        cue_terms=["到家"], source_message_id=7012, chat_session_id=7))
    assert "trigger" not in asyncio.run(_payload(a33_db, cue))   # 线索型不属于「在等什么」


# ─────────────── ② 读取侧：优先已存标签，缺失回退推断 ───────────────

@pytest.mark.slow
def test_settle_prefers_stored_trigger_over_content(a33_db):
    """已存标签赢过正文：正文不像到达类但标了 arrival 会关；正文像但标了 clock 的不关。"""
    from app.scheduling.prospective_intent import settle_promises_on_user_message

    tagged_arrival = asyncio.run(_add_intent(
        a33_db, content=_NO_TAG_ARRIVAL, kind="promise", due_end=_DUE_TODAY,
        cue_terms_json='{"confidence": "medium", "terms": [], "side": "self", "trigger": "arrival"}'))
    tagged_clock = asyncio.run(_add_intent(
        a33_db, content=_ARRIVAL_PROMISE, kind="promise", due_end=_DUE_TODAY,
        cue_terms_json='{"confidence": "medium", "terms": [], "side": "self", "trigger": "clock"}'))
    untagged_old = asyncio.run(_add_intent(                      # 旧 list 格式 ⇒ 回退正文推断
        a33_db, content=_ARRIVAL_PROMISE, kind="promise", due_end=_DUE_TODAY,
        cue_terms_json='["到家"]'))

    settled = asyncio.run(settle_promises_on_user_message(11, 7, "我到家了"))

    assert set(settled) == {tagged_arrival, untagged_old}
    assert asyncio.run(_row(a33_db, tagged_clock)).status == "pending"   # clock 标签压掉推断
    for pid in (tagged_arrival, untagged_old):
        row = asyncio.run(_row(a33_db, pid))
        assert row.status == "discharged" and row.discharged_at is not None


# ─────────────── ③ 候选筛选层：事件型「当天全天」要先确认未兑现 ───────────────

@pytest.mark.slow
def test_collect_drops_day_event_promise_already_fulfilled(a33_db):
    """到家信号已出现 ⇒ 日期型 arrival 承诺不进候选，并就地置 discharged（零发送、零新日志）。"""
    from app.scheduling.prospective_intent import collect_due_promises, upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=7101, chat_session_id=7))
    asyncio.run(_add_user_message(a33_db, "我到家了"))
    logs0, msgs0 = asyncio.run(_counts(a33_db))

    assert pid not in _ids(asyncio.run(collect_due_promises()))
    row = asyncio.run(_row(a33_db, pid))
    assert row.status == "discharged" and row.discharged_at is not None
    assert asyncio.run(_counts(a33_db)) == (logs0, msgs0)       # 关闭口不发消息、不写主动日志


@pytest.mark.slow
def test_collect_keeps_day_event_promise_until_fulfilled(a33_db):
    """未兑现 ⇒ 「到期日当天全天可提起」既有语义保持，候选照常产出并带 trigger 标签。"""
    from app.scheduling.prospective_intent import collect_due_promises, upsert_intent

    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=7111, chat_session_id=7))
    asyncio.run(_add_user_message(a33_db, "还没呢，堵在路上"))

    cands = asyncio.run(collect_due_promises())
    assert _ids(cands) == {pid} and [c for c in cands][0]["trigger"] == "arrival"
    assert asyncio.run(_row(a33_db, pid)).status == "pending"

    # 信号出现后再采集一次：同一条即刻退场（与 fire 闸门同口径，只是提前到筛选层）
    asyncio.run(_add_user_message(a33_db, "刚到家"))
    assert pid not in _ids(asyncio.run(collect_due_promises()))
    assert asyncio.run(_row(a33_db, pid)).status == "discharged"


@pytest.mark.slow
def test_collect_clock_day_and_exact_time_promises_unchanged(a33_db):
    """刻意窄口径：clock 型日期承诺不受影响；精确时刻型维持 [now-2h, now]（trigger 不参与筛除）。"""
    from app.scheduling.prospective_intent import collect_due_promises, upsert_intent

    clock_day = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_CLOCK_PROMISE, kind="promise",
        due_end=_DUE_TODAY, source_message_id=7121, chat_session_id=7))
    exact_event = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=_ARRIVAL_PROMISE, kind="promise",
        due_end=_FIXED_UTC + timedelta(hours=20),   # 非 23:59 ⇒ 精确时刻型，且 due 还没到
        source_message_id=7122, chat_session_id=7))
    asyncio.run(_add_user_message(a33_db, "我到家了"))

    cands = asyncio.run(collect_due_promises())
    assert [c for c in cands if c["pis_id"] == clock_day][0]["trigger"] == "clock"
    assert asyncio.run(_row(a33_db, clock_day)).status == "pending"
    assert exact_event not in _ids(cands)                        # 未到窗（既有语义，与信号无关）
    assert asyncio.run(_row(a33_db, exact_event)).status == "pending"


@pytest.mark.slow
def test_collect_uses_state_anchor_when_row_has_no_session(a33_db, monkeypatch):
    """没有会话可查时仍看现状锚：锚里已写「已到家」⇒ 事件型日期承诺同样不进候选。"""
    from app.scheduling.prospective_intent import collect_due_promises

    pid = asyncio.run(_add_intent(
        a33_db, content=_ARRIVAL_PROMISE, kind="promise", due_end=_DUE_TODAY,
        cue_terms_json='{"terms": [], "trigger": "arrival"}'))    # chat_session_id 为空

    async def _anchor(*a, **kw):
        return "用户已到家，正在休息"

    monkeypatch.setattr("app.scheduling.state_guard.current_state_anchor", _anchor)
    assert pid not in _ids(asyncio.run(collect_due_promises()))
    assert asyncio.run(_row(a33_db, pid)).status == "discharged"


@pytest.mark.slow
def test_fire_gate_prefers_candidate_trigger_and_falls_back(a33_db, monkeypatch):
    """候选带 trigger ⇒ 正文判不出的承诺也能被闸门兜住；dict 缺该键 ⇒ 回退正文（A32 原行为）。"""
    from app.scheduling.prospective_intent import run_prospective_due

    prompts, sends = [], []

    async def _llm(**kw):
        prompts.append(kw["messages"][-1]["content"])
        return "体检报告拿到了，后续怎么样？"

    async def _send(session_id, character_id, user_id, content,
                    message_type="prospective_intent", **kw):
        sends.append(content)

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)

    tagged = asyncio.run(_add_intent(
        a33_db, content=_NO_TAG_ARRIVAL, kind="promise", due_end=_DUE_TODAY, chat_session_id=7,
        cue_terms_json='{"terms": [], "side": "self", "trigger": "arrival"}'))
    asyncio.run(_add_user_message(a33_db, "到家了，报告挺干净的"))
    logs0, msgs0 = asyncio.run(_counts(a33_db))

    # ① 用采集层产出的候选（已含 trigger）：闸门按标签判定 ⇒ 静默关闭、不调 LLM
    tagged_row = asyncio.run(_row(a33_db, tagged))
    assert tagged_row.status == "pending"                       # 本例直接构造 dict，未过采集层
    ok = asyncio.run(run_prospective_due({
        "pis_id": tagged, "character_id": 11, "user_id": 1, "session_id": 7,
        "content": _NO_TAG_ARRIVAL, "due_end": _DUE_TODAY, "side": "self", "trigger": "arrival",
    }))
    assert ok is True and prompts == [] and sends == []
    assert asyncio.run(_row(a33_db, tagged)).status == "discharged"
    assert asyncio.run(_counts(a33_db)) == (logs0, msgs0)

    # ② 同正文但 dict 不带 trigger：回退按正文推断 ⇒ 判不出事件 ⇒ 照常 fire
    untagged = asyncio.run(_add_intent(
        a33_db, content=_NO_TAG_ARRIVAL, kind="promise", due_end=_DUE_TODAY, chat_session_id=7,
        cue_terms_json='["到家"]'))
    ok2 = asyncio.run(run_prospective_due({
        "pis_id": untagged, "character_id": 11, "user_id": 1, "session_id": 7,
        "content": _NO_TAG_ARRIVAL, "due_end": _DUE_TODAY, "side": "self",
    }))
    assert ok2 is True and len(prompts) == 1 and len(sends) == 1
    assert _NO_TAG_ARRIVAL in prompts[0]
    assert asyncio.run(_row(a33_db, untagged)).status == "discharged"


# ─────────────── ② 存量回填脚本（纯函数 + 默认不写库）───────────────

def _load_backfill():
    spec = importlib.util.spec_from_file_location(
        "backfill_intent_triggers_mod",
        _REPO / "scripts" / "backfill_intent_triggers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_new_payload_adds_tag_only_and_is_idempotent():
    """脚本只加元数据：旧 list 原样搬进 terms、dict 其余键逐字保留、已带标签跳过、意外形态不碰。"""
    mod = _load_backfill()

    assert json.loads(mod.new_payload('["到家", "火锅"]', "arrival")) == \
        {"terms": ["到家", "火锅"], "trigger": "arrival"}
    assert json.loads(mod.new_payload('{"confidence": "high", "terms": ["周末"], "side": "self"}', "clock")) == \
        {"confidence": "high", "terms": ["周末"], "side": "self", "trigger": "clock"}
    assert mod.new_payload('{"terms": [], "trigger": "medication"}', "clock") is None   # 幂等
    assert json.loads(mod.new_payload("", "arrival")) == {"terms": [], "trigger": "arrival"}
    assert json.loads(mod.new_payload(None, "arrival")) == {"terms": [], "trigger": "arrival"}
    assert mod.new_payload("42", "clock") is None            # 标量等意外形态不碰


def test_backfill_dry_run_never_writes_and_survives_bare_schema(tmp_path, monkeypatch, capsys):
    """默认 dry-run 一个字节都不写库；表不存在的库（插件裸 schema 先例）优雅退出、不抛栈。"""
    mod = _load_backfill()
    db = tmp_path / "intent.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE prospective_intents (id INTEGER PRIMARY KEY, character_id INT, "
        "kind TEXT, status TEXT, content TEXT, cue_terms_json TEXT)")
    con.executemany(
        "INSERT INTO prospective_intents (id, character_id, kind, status, content, cue_terms_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(1, 13, "promise", "pending", _ARRIVAL_PROMISE, '["到家"]'),
         (2, 13, "promise", "matched", _CLOCK_PROMISE, '{"terms": [], "side": "self", "trigger": "clock"}'),
         (3, 13, "promise", "pending", _MED_PROMISE, "not-json"),
         (4, 13, "cue", "pending", _ARRIVAL_PROMISE, '["到家"]'),
         (5, 13, "promise", "discharged", _MED_PROMISE, "[]")])
    con.commit()
    before = db.read_bytes()
    con.close()

    monkeypatch.setattr(mod, "DB", db)
    assert mod.main([]) == 0                                 # 不加 --apply
    out = capsys.readouterr().out
    assert "将改 1 条" in out and "坏 JSON 跳过 1 条" in out and "已带标签 1 条" in out
    assert db.read_bytes() == before                          # 库逐字节未变

    bare = tmp_path / "bare.db"
    sqlite3.connect(str(bare)).close()                        # 存在但空表（插件裸 schema）
    monkeypatch.setattr(mod, "DB", bare)
    assert mod.main([]) == 0
    assert "[SKIP]" in capsys.readouterr().out
