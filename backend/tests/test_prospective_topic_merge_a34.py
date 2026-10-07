# -*- coding: utf-8 -*-
"""A34 批 3 回归：⑦ 同主题承诺合并（B.3.2）＋ ⑧ 承诺双发抑制 ＋ ⑨ 回忆式开场分家（B.3.5）。

钉住三件事：
⑦ 写入期把「同角色 + 同 trigger + 同提起档」的事件型承诺并进**更早创建**的那条（正文无损合成、
   cue_terms 取并集、INFO 留痕）；clock 型 / cue / 不同 side / 不同提起档 / 装不下 一律不并；
⑧ 同会话 N 秒内已有 AI 消息 ⇒ 承诺本轮**不抢发**：不认领（状态仍 pending）、不调 LLM、不发送、
   不写发送日志；即时回复路径完全不经过这道闸；
⑨ 「我记得你之前说过…」这类**回忆式开场只留给回忆通道**：句首命中即拦回（与一小时冷却无关），
   句中正常引用不误杀。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库一律 tmp_path + 模板克隆，不连生产库。）
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from tests._dbclone import clone_engine, make_session_factory

# 固定「现在」：UTC 2026-10-07 02:00 ＝ 北京 2026-10-07 10:00（日期型 due=当天 23:59，未跨天）
_FIXED_UTC = datetime(2026, 10, 7, 2, 0)
_DUE_TODAY = datetime(2026, 10, 7, 23, 59)
_YESTERDAY_EOD = datetime(2026, 10, 6, 23, 59)

# 现场 171-174：一次「到家/接人」事件挂了 4 条互不相似的承诺，同一天被逐条 fire ⇒ 连催 4 次
_EVENT_PROMISES = (
    "到了发定位",
    "在门口等我",
    "下楼接你去急诊",
    "上了车把定位甩过来",
)
# 第 5 条同事件义务：并进前四条已超并列段数上限 ⇒ 应当各自成行（不截断丢字）
_OVERFLOW_PROMISE = "到家前先把快递取回来"
# clock 型（正文不含「到/接/门口/定位/药」），不 participation ⑦
_CLOCK_PROMISES = ("明早八点叫我起床", "晚上十点提醒我关窗")


@pytest.fixture()
def a34_db(monkeypatch, tmp_path):
    """临时库（模板克隆）＋ 工厂指向临时工厂 ＋ 钉住时钟；现状锚恒为空。"""
    engine = clone_engine(os.path.join(str(tmp_path), "a34.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.chat import ChatSession
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=11, user_id=1, name="sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            db.add(ChatSession(id=7, user_id=1, character_id=11))
            await db.commit()

    asyncio.run(_seed())
    import app.db.database as db_mod
    import app.scheduling.prospective_intent as pi
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(pi, "async_session_factory", factory)
    monkeypatch.setattr(pi, "_now_naive", lambda: _FIXED_UTC)
    # 锚置空：本文件只想钉 ⑦⑧⑨ 自己的闸门，不让 A32 事件兑现闸门被锚里的文本抢跑
    async def _no_anchor(*a, **kw):
        return ""
    monkeypatch.setattr("app.scheduling.state_guard.current_state_anchor", _no_anchor)
    yield factory
    asyncio.run(engine.dispose())


def _rows(factory):
    from app.models.memory import ProspectiveIntent

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(ProspectiveIntent).order_by(ProspectiveIntent.id))).scalars().all()
            return [(r.id, r.content, r.kind, r.status, r.cue_terms_json) for r in rows]
    return asyncio.run(_run())


def _upsert(factory, content, *, kind="promise", due_end=_DUE_TODAY, cue_terms=None, src=None):
    from app.scheduling.prospective_intent import upsert_intent
    pid = asyncio.run(upsert_intent(
        user_id=1, character_id=11, content=content, kind=kind, due_end=due_end,
        cue_terms=cue_terms, source_message_id=src))
    assert pid is not None, f"承诺没落库：{content}"
    return pid


def _spy_llm(monkeypatch, reply):
    prompts = []

    async def _llm(**kw):
        prompts.append(kw["messages"][-1]["content"])
        return reply
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)
    return prompts


def _spy_send(monkeypatch):
    sends = []

    async def _send(session_id, character_id, user_id, content,
                    message_type="prospective_intent", **kw):
        sends.append(content)
        return None
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
    return sends


def _log_count(factory):
    from app.models.character import ProactiveMessageLog

    async def _run():
        async with factory() as db:
            return len((await db.execute(select(ProactiveMessageLog.id))).all())
    return asyncio.run(_run())


def _add_ai_message(factory, content, *, at=_FIXED_UTC, session_id=7):
    from app.models.chat import ChatMessage

    async def _run():
        async with factory() as db:
            db.add(ChatMessage(session_id=session_id, sender_type="ai", content=content,
                               created_at=at))
            await db.commit()
    asyncio.run(_run())


def _candidate(pid, content, **kw):
    cand = {"pis_id": pid, "character_id": 11, "user_id": 1, "session_id": 7,
            "content": content, "due_end": _DUE_TODAY, "side": "user"}
    cand.update(kw)
    return cand


# ───────────────────────── ⑦ 纯函数：合成与提起档 ─────────────────────────

def test_compose_topic_content_verbatim_when_same_said():
    from app.scheduling.prospective_intent import compose_topic_content
    # 任一侧为空 → 不并，也不制造空段
    assert compose_topic_content("", "到了发定位") is None
    assert compose_topic_content("到了发定位", "  ") is None
    # 同义/包含 → 取信息更全（更长）的一条，逐字不动（不越拼越长）
    assert compose_topic_content("到了发定位", "到了发定位。") == "到了发定位。"
    assert compose_topic_content("用户到家要发定位", "到家要发定位") == "用户到家要发定位"


def test_compose_topic_content_joins_distinct_obligations():
    from app.scheduling.prospective_intent import (
        TOPIC_MERGE_JOIN, _split_topic_parts, compose_topic_content,
    )
    merged = compose_topic_content("到了发定位", "在门口等我")
    assert merged == "到了发定位" + TOPIC_MERGE_JOIN + "在门口等我"
    assert _split_topic_parts(merged) == ["到了发定位", "在门口等我"]
    # 已并过一次的行再并下一条：段数累加
    assert len(_split_topic_parts(compose_topic_content(merged, "下楼接你去急诊"))) == 3


def test_compose_topic_content_refuses_when_over_capacity():
    """装不下就不并（宁可两条各自挂着，也绝不截断丢字）——段数与总长两道上限。"""
    from app.scheduling.prospective_intent import (
        TOPIC_MERGE_JOIN, TOPIC_MERGE_MAX_CHARS, TOPIC_MERGE_MAX_PARTS,
        compose_topic_content,
    )
    kept = TOPIC_MERGE_JOIN.join(f"第{i}件到家要办的事" for i in range(TOPIC_MERGE_MAX_PARTS))
    assert compose_topic_content(kept, "再加一件") is None              # 超并列段数上限
    long_new = "锁" * (TOPIC_MERGE_MAX_CHARS - len(kept) + 1)
    assert compose_topic_content(kept, long_new) is None                # 超总长上限


def test_same_raise_window_pairs():
    """「两道闸」：日期型看北京日历日、精确时刻型看 ≤2h 扫描窗；混档/一空一有不并。"""
    from app.scheduling.prospective_intent import _same_raise_window
    now_local = _FIXED_UTC + timedelta(hours=8)                          # 北京 2026-10-07 10:00
    assert _same_raise_window(None, None, now_local=now_local) is True
    assert _same_raise_window(None, _DUE_TODAY, now_local=now_local) is False
    assert _same_raise_window(_DUE_TODAY, None, now_local=now_local) is False
    # 日期型：同一个北京日历日才算同档
    assert _same_raise_window(_DUE_TODAY, datetime(2026, 10, 7, 23, 59), now_local=now_local) is True
    assert _same_raise_window(_YESTERDAY_EOD, _DUE_TODAY, now_local=now_local) is False
    # 日期型 vs 精确时刻型 → 不同档
    assert _same_raise_window(_DUE_TODAY, datetime(2026, 10, 7, 18, 0), now_local=now_local) is False
    # 都精确型：相距 ≤ 扫描窗（2h）才会被同一轮捞到
    assert _same_raise_window(datetime(2026, 10, 7, 18, 0),
                              datetime(2026, 10, 7, 19, 30), now_local=now_local) is True
    assert _same_raise_window(datetime(2026, 10, 7, 18, 0),
                              datetime(2026, 10, 7, 21, 0), now_local=now_local) is False


# ───────────────────────── ⑦ DB：写入期合并 ─────────────────────────

@pytest.mark.slow
def test_topic_merge_absorbs_four_event_promises(a34_db, caplog):
    """现场 171-174：四条互不相似的「到家/接人」承诺 ⇒ 收成一条（四段并列）＋ INFO 留痕。"""
    caplog.set_level(logging.INFO)
    ids = [_upsert(a34_db, text, cue_terms=[text[:2]], src=700 + i)
           for i, text in enumerate(_EVENT_PROMISES)]

    rows = _rows(a34_db)
    assert len(rows) == 1, f"同主题承诺没收敛成一条：{[r[1] for r in rows]}"
    assert set(ids) == {rows[0][0]}                               # 全部复用**最早**那行的 id
    kept_id, content, kind, status, meta_json = rows[0]
    assert kind == "promise" and status == "pending"
    for text in _EVENT_PROMISES:
        assert text in content                                    # 正文无损：一条都没丢
    meta = json.loads(meta_json)
    assert meta["trigger"] == "arrival" and meta["side"] == "user"
    assert set(meta["terms"]) >= {t[:2] for t in _EVENT_PROMISES}  # terms 取并集
    assert len(meta["terms"]) <= 6
    assert any("same-topic merged" in r.message and f"id={kept_id}" in r.message
               for r in caplog.records), "合并没留 INFO 痕迹"


@pytest.mark.slow
def test_topic_merge_skips_clock_promises(a34_db):
    """clock 型不参与同主题合并（否则会把「明早叫你」并进「晚上问你」）。"""
    a, b = (_upsert(a34_db, t, src=801 + i) for i, t in enumerate(_CLOCK_PROMISES))
    assert a != b
    assert len(_rows(a34_db)) == 2


@pytest.mark.slow
def test_topic_merge_skips_cue_and_other_side(a34_db):
    """cue 不是承诺（按设计可长期复用）；side 不同＝不是同一件事 ⇒ 都不并。"""
    cue = _upsert(a34_db, "用户到家要跟一声", kind="cue", cue_terms=["到家"], src=811)
    self_p = _upsert(a34_db, "我承诺到家就把门打开", src=812)
    user_p = _upsert(a34_db, "到了在门口等我", src=813)
    assert len({cue, self_p, user_p}) == 3
    rows = _rows(a34_db)
    assert len(rows) == 3
    assert json.loads([r for r in rows if r[0] == self_p][0][4])["side"] == "self"
    assert json.loads([r for r in rows if r[0] == user_p][0][4])["side"] == "user"


@pytest.mark.slow
def test_topic_merge_skips_other_day(a34_db):
    """不同日历日 ⇒ 不同提起档，不并（跨天各提各的，也不把今天的义务并进昨天那条）。"""
    a = _upsert(a34_db, "到了发定位", due_end=_YESTERDAY_EOD, src=821)
    b = _upsert(a34_db, "到了在门口等我", due_end=_DUE_TODAY, src=822)
    assert a != b and len(_rows(a34_db)) == 2


@pytest.mark.slow
def test_topic_merge_refuses_overflow_and_keeps_new_row(a34_db):
    """四条并满后再来一条 ⇒ 装不下 ⇒ 新承诺原文自成一行的保守边界。"""
    for i, text in enumerate(_EVENT_PROMISES):
        _upsert(a34_db, text, src=831 + i)
    assert len(_rows(a34_db)) == 1
    overflow = _upsert(a34_db, _OVERFLOW_PROMISE, src=839)
    rows = _rows(a34_db)
    assert len(rows) == 2
    assert rows[1][0] == overflow
    assert _OVERFLOW_PROMISE in rows[1][1]            # 原文完整，未被截进已并满的那条


# ───────────────────────── ⑧ 双发抑制 ─────────────────────────

@pytest.mark.slow
def test_dual_send_defers_before_claim(a34_db, monkeypatch):
    """同会话 20s 内已有 AI 消息 ⇒ 本轮不抢发：不认领、不调 LLM、不发送、不写日志，状态留 pending。"""
    from app.scheduling.prospective_intent import run_prospective_due
    pid = _upsert(a34_db, _CLOCK_PROMISES[0], src=841)
    _add_ai_message(a34_db, "嗯睡吧")

    prompts = _spy_llm(monkeypatch, "火锅我订好啦，这周末去？")
    sends = _spy_send(monkeypatch)
    logs0 = _log_count(a34_db)

    assert asyncio.run(run_prospective_due(_candidate(pid, _CLOCK_PROMISES[0]))) is False
    assert prompts == [] and sends == []
    assert _rows(a34_db)[0][3] == "pending"            # 未认领 → 下轮仍可重试
    assert _log_count(a34_db) == logs0                 # 不写「已发送」留痕


@pytest.mark.slow
def test_dual_send_window_boundary_sends_normally(a34_db, monkeypatch):
    """窗口外（>20s）的 AI 消息不算双发 ⇒ 照常发送一条并 discharged。"""
    from app.scheduling.prospective_intent import (
        PROACTIVE_DUAL_SEND_SUPPRESS_SEC, run_prospective_due,
    )
    pid = _upsert(a34_db, _CLOCK_PROMISES[0], src=851)
    _add_ai_message(a34_db, "嗯睡吧",
                    at=_FIXED_UTC - timedelta(seconds=PROACTIVE_DUAL_SEND_SUPPRESS_SEC + 25))

    _spy_llm(monkeypatch, "火锅我订好啦，这周末去？")
    sends = _spy_send(monkeypatch)
    assert asyncio.run(run_prospective_due(_candidate(pid, _CLOCK_PROMISES[0]))) is True
    assert len(sends) == 1
    assert _rows(a34_db)[0][3] == "discharged"


@pytest.mark.slow
def test_dual_send_terminal_check_reverts_claim(a34_db, monkeypatch):
    """终局闸：认领**之后**、发送**之前**才出现的 AI 消息 ⇒ 回滚 pending（不算已提）。

    用桩复现这个时序：第一次查（认领前）干净，第二次查（发送前）命中。
    """
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import run_prospective_due
    pid = _upsert(a34_db, _CLOCK_PROMISES[0], src=861)
    calls = []

    async def _fake_recent(session_id, *, within_sec=20):
        calls.append((session_id, within_sec))
        return 9999 if len(calls) >= 2 else None
    monkeypatch.setattr(pi, "recent_ai_message_id", _fake_recent)

    prompts = _spy_llm(monkeypatch, "火锅我订好啦，这周末去？")
    sends = _spy_send(monkeypatch)
    assert asyncio.run(run_prospective_due(_candidate(pid, _CLOCK_PROMISES[0]))) is False
    assert len(prompts) == 1 and len(calls) == 2        # 生成完才被终局闸拦下
    assert sends == []
    assert _rows(a34_db)[0][3] == "pending"


@pytest.mark.slow
def test_dual_send_helper_is_fail_open(a34_db, monkeypatch):
    """助手 fail-open：查不到＝不抑制（宁可漏抑制一次双发，也不丢一条承诺）。"""
    import app.scheduling.prospective_intent as pi

    def _boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(pi, "async_session_factory", _boom)
    assert asyncio.run(pi.recent_ai_message_id(7)) is None


def test_dual_send_not_wired_into_immediate_reply():
    """⑧ 只接在承诺发送路径（认领前 + 发送前，共 2 处）；即时回复路径绝不经过这道闸。"""
    import inspect
    from app.scheduling import prospective_intent as pi
    body = inspect.getsource(pi.run_prospective_due)
    assert body.count("recent_ai_message_id(int(session_id))") == 2
    for mod in ("app.agent.loop", "app.scheduling.message_generator"):
        source = inspect.getsource(__import__(mod, fromlist=["_"]))
        assert "recent_ai_message_id" not in source, mod


# ───────────────────────── ⑨ 回忆式开场分家 ─────────────────────────

@pytest.mark.slow
def test_recall_opener_blocked_and_rolled_back(a34_db, monkeypatch):
    """句首回忆式开场 ⇒ 无条件拦回（与一小时冷却无关）：带约束重生成一次，仍违规则不发送。"""
    from app.scheduling.prospective_intent import run_prospective_due
    pid = _upsert(a34_db, _CLOCK_PROMISES[0], src=871)
    prompts = _spy_llm(monkeypatch, "我记得你之前说过明早叫你，得起床了。")
    sends = _spy_send(monkeypatch)

    assert asyncio.run(run_prospective_due(_candidate(pid, _CLOCK_PROMISES[0]))) is False
    assert len(prompts) == 2
    assert "回忆式开场只留给回忆通道" in prompts[1]
    assert sends == []
    assert _rows(a34_db)[0][3] == "pending"


@pytest.mark.slow
def test_recall_marker_mid_sentence_still_sends(a34_db, monkeypatch):
    """句中引用（不是开场）不误杀：回忆式判定只看句首。"""
    from app.scheduling.prospective_intent import run_prospective_due
    pid = _upsert(a34_db, _CLOCK_PROMISES[0], src=881)
    prompts = _spy_llm(monkeypatch, "火锅位订好了，汤点的清汤，我记得你之前说过想吃清汤。")
    sends = _spy_send(monkeypatch)

    assert asyncio.run(run_prospective_due(_candidate(pid, _CLOCK_PROMISES[0]))) is True
    assert len(prompts) == 1 and len(sends) == 1


def test_prompt_no_longer_invites_recall_opener():
    """提示词分家：self / user 两个分支都不再邀请回忆式开场，且都带禁令。"""
    from app.scheduling.prospective_intent import _build_prospective_hint
    for side in ("self", "user"):
        hint = _build_prospective_hint("sam", "用户答应喂团子", side)
        assert "可以说'我记得你之前说过…'" not in hint
        assert "回忆式开场" in hint and "留给回忆" in hint
