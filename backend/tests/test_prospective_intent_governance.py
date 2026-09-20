# -*- coding: utf-8 -*-
"""前瞻约定治理（2026-09-13）单测：主体口径 / 时效收窄 / 幂等去重 / 开场冷却 / stale。

覆盖：
- ① side=self|user 判定（我承诺… / 用户答应… / cue 三类）+ 渲染分流 + flag 关逐字节回退；
- ② 窗口 N 小时内可提起、超窗置 stale 不可提起、无 due 不误伤；
- ③ 同一 intent 幂等只提一次；同款开场一小时冷却拦截；
- ⑤ stale 标记不影响其它状态（discharged/cancelled/matched/cue 原样）。

纯函数用例为快测档；DB 用例走临时库（pytest 全局沙箱 + 本文件临时库），不碰生产库。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from _dbclone import clone_engine, make_session_factory

from app.models.memory import ProspectiveIntent

pytestmark = pytest.mark.slow


# ───────────────────────── 纯函数（快测档，零 DB）─────────────────────────

def test_side_classification_three_kinds():
    from app.scheduling.prospective_intent import classify_intent_side
    # 我承诺… → self（AI 自己许的）
    assert classify_intent_side("我承诺晚饭由我来做", "promise") == "self"
    assert classify_intent_side("我答应明天画芒芒的图", "promise") == "self"
    # 用户…开头 → user
    assert classify_intent_side("用户答应等一下去喂团子", "promise") == "user"
    assert classify_intent_side("用户洗完澡出来要听 sam 对设计的评价", "promise") == "user"
    # cue 一律 user
    assert classify_intent_side("樱花开了提醒我拍照", "cue") == "user"
    assert classify_intent_side("我承诺的那件事", "cue") == "user"
    # 无信号兜底 user（与既有渲染口径一致）
    assert classify_intent_side("改天一起吃火锅", "promise") == "user"
    assert classify_intent_side("", "promise") == "user"


def test_get_intent_side_reads_metadata_and_backcompat():
    from app.scheduling.prospective_intent import get_intent_side
    # 新格式：元数据落库值优先
    row = SimpleNamespace(
        content="随便写点什么", kind="promise",
        cue_terms_json='{"confidence": "medium", "terms": [], "side": "self"}',
    )
    assert get_intent_side(row) == "self"
    # 旧格式（list）：回退按 content/kind 现算
    row_old = SimpleNamespace(content="我承诺晚饭我来做", kind="promise", cue_terms_json='["火锅"]')
    assert get_intent_side(row_old) == "self"
    row_bad = SimpleNamespace(content="用户答应喂团子", kind="promise", cue_terms_json="not-json")
    assert get_intent_side(row_bad) == "user"


def test_hint_side_split_and_legacy_equivalence():
    from app.scheduling.prospective_intent import (
        _build_prospective_hint, _build_prospective_hint_legacy,
    )
    self_hint = _build_prospective_hint("sam", "我承诺晚饭由我来做", "self")
    assert "你自己" in self_hint
    assert "禁止" in self_hint and "不要替用户做决定" in self_hint
    user_hint = _build_prospective_hint("sam", "用户答应喂团子", "user")
    assert "用户之前提过/答应过" in user_hint
    assert "我记得你之前说过" in user_hint
    # flag 关 → 旧话术逐字节等价（可回退）
    assert _build_prospective_hint_legacy("sam", "X") == (
        "你是sam。你和用户之前有过一个约定/用户曾提到过：「X」。"
        "现在到了合适的时间，请用自己的语气自然提起这件事（可以说'我记得你之前说过…'，"
        "但不要生硬念稿、不要提'AI'、不要加引号标注），并顺势把话题抛给用户，不要替用户做决定。"
    )


def test_hint_cross_day_anchor_and_present_tense_ban():
    from app.scheduling.prospective_intent import _build_prospective_hint, _is_cross_day
    due = datetime(2026, 9, 12, 23, 59)
    now = datetime(2026, 9, 13, 3, 37)
    assert _is_cross_day(due, now) is True
    assert _is_cross_day(datetime(2026, 9, 13, 1, 0), now) is False
    assert _is_cross_day(None, now) is False
    hint = _build_prospective_hint("sam", "用户答应喂团子", "user", due, now_naive=now)
    assert "已经跨天" in hint and "2026-09-12" in hint
    assert "现在时间也差不多了" in hint  # 作为"严禁使用"的负例出现在约束里
    same_day = _build_prospective_hint("sam", "用户答应喂团子", "user", datetime(2026, 9, 13, 1, 0), now_naive=now)
    assert "已经跨天" not in same_day


def test_opening_and_present_tense_helpers():
    from app.scheduling.prospective_intent import (
        has_forbidden_present_tense, has_proactive_opening,
    )
    assert has_proactive_opening("嘿，我记得你之前说过晚饭你来做的") is True
    assert has_proactive_opening("哎，你之前不是说晚上回来说一声嘛") is True
    assert has_proactive_opening("粥好了，快来吃") is False
    assert has_forbidden_present_tense("现在时间也差不多了，你打算做点什么？") is True
    assert has_forbidden_present_tense("昨天你说过要做晚饭的") is False


def test_similar_intent_text_threshold():
    from app.scheduling.prospective_intent import (
        SIMILAR_INTENT_THRESHOLD, normalize_intent_text, similar_intent_text,
    )
    assert normalize_intent_text("我承诺，晚饭 我来做！") == "我承诺晚饭我来做"
    assert similar_intent_text("我承诺晚饭由我来做", "我承诺晚饭由我来做。") is True
    # 不同粒度不合并（既有测试依赖：尾号 2 视为不同条）
    assert similar_intent_text("下周带你去吃火锅", "下周带你去吃火锅2") is False
    assert similar_intent_text("晚上回来吱一声", "明天要画芒芒的图") is False
    assert SIMILAR_INTENT_THRESHOLD >= 0.9  # 写入期保守阈值


def test_idempotency_key_format():
    from app.scheduling.prospective_intent import fired_idempotency_key
    assert fired_idempotency_key(19) == "prospective_intent_fired:19"


def test_cue_is_stale_rules():
    from types import SimpleNamespace
    from app.scheduling.prospective_intent import _cue_is_stale
    # 固定 UTC 2026-09-15 02:00 = 北京 2026-09-15 10:00
    now_utc = datetime(2026, 9, 15, 2, 0)
    now_local = now_utc + timedelta(hours=8)

    def _row(status="pending", due_end=None, created=None, kind="cue"):
        return SimpleNamespace(kind=kind, status=status, due_end=due_end, created_at=created)

    # ① 日期型 cue：昨天 23:59 → 跨天 stale；今天 23:59 → 仍有效
    assert _cue_is_stale(_row(due_end=datetime(2026, 9, 14, 23, 59)),
                         now_utc=now_utc, now_local=now_local) is True
    assert _cue_is_stale(_row(due_end=datetime(2026, 9, 15, 23, 59)),
                         now_utc=now_utc, now_local=now_local) is False
    # ② 非日期型 cue（时分粒度，防御分支）：超 24h stale，24h 内有效
    assert _cue_is_stale(_row(due_end=now_local - timedelta(hours=25)),
                         now_utc=now_utc, now_local=now_local) is True
    assert _cue_is_stale(_row(due_end=now_local - timedelta(hours=1)),
                         now_utc=now_utc, now_local=now_local) is False
    # ③ 无 due 纯线索：created_at(UTC) 31 天前 stale，29 天前有效
    assert _cue_is_stale(_row(created=now_utc - timedelta(days=31)),
                         now_utc=now_utc, now_local=now_local) is True
    assert _cue_is_stale(_row(created=now_utc - timedelta(days=29)),
                         now_utc=now_utc, now_local=now_local) is False
    # ④ 终态 / promise 一律不动
    assert _cue_is_stale(_row(status="stale", due_end=datetime(2026, 9, 1, 23, 59)),
                         now_utc=now_utc, now_local=now_local) is False
    assert _cue_is_stale(_row(status="discharged", created=now_utc - timedelta(days=99)),
                         now_utc=now_utc, now_local=now_local) is False
    assert _cue_is_stale(_row(kind="promise", due_end=datetime(2026, 9, 1, 23, 59)),
                         now_utc=now_utc, now_local=now_local) is False


# ───────────────────────── DB 用例（slow，临时库）─────────────────────────

@pytest.fixture()
def pi_db(monkeypatch, tmp_path):
    """临时库（模板库克隆，见 tests/_dbclone.py）+ 把 database / prospective_intent 的
    工厂指向临时工厂；种子（账号 1 / 角色 11）原样保留。"""
    tmp = str(tmp_path)
    engine = clone_engine(os.path.join(tmp, "t.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=11, user_id=1, name="sam", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_seed())
    import app.db.database as db_mod
    import app.scheduling.prospective_intent as pi
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(pi, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _rows_of(factory):
    async def _run():
        async with factory() as db:
            rows = (await db.execute(select(ProspectiveIntent).order_by(ProspectiveIntent.id))).scalars().all()
            return {r.id: (r.content, r.kind, r.status, r.cue_terms_json) for r in rows}
    return asyncio.run(_run())


@pytest.mark.slow
def test_upsert_stores_side_and_merges_near_duplicate(pi_db):
    from app.scheduling.prospective_intent import upsert_intent
    due = datetime(2026, 9, 20, 23, 59)

    self_id = asyncio.run(upsert_intent(user_id=1, character_id=11, content="我承诺晚饭由我来做",
                                        kind="promise", due_end=due, source_message_id=901))
    row = _rows_of(pi_db)[self_id]
    assert '"side": "self"' in row[3]

    user_id2 = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户答应等一下去喂团子",
                                         kind="promise", due_end=due, source_message_id=902))
    assert '"side": "user"' in _rows_of(pi_db)[user_id2][3]

    # ③ 同角色 + 同 due 窗口 + 近逐字重复 → 复用既有行（不新增）
    dup_id = asyncio.run(upsert_intent(user_id=1, character_id=11, content="我承诺晚饭由我来做。",
                                       kind="promise", due_end=due, source_message_id=903))
    assert dup_id == self_id
    assert len(_rows_of(pi_db)) == 2


@pytest.mark.slow
def test_near_duplicate_upgrades_side_to_self(pi_db):
    """① 近重复合并时，若新内容明确 self 侧 → 既有行的 side 元数据升级为 self（口径纠正）。"""
    from app.scheduling.prospective_intent import upsert_intent
    due = datetime(2026, 9, 21, 23, 59)
    a = asyncio.run(upsert_intent(user_id=1, character_id=11, content="明天晚饭我来做",
                                  kind="promise", due_end=due, source_message_id=951))
    assert '"side": "user"' in _rows_of(pi_db)[a][3]
    b = asyncio.run(upsert_intent(user_id=1, character_id=11, content="明天晚饭我来做。",
                                  kind="promise", due_end=due, source_message_id=952, side="self"))
    assert b == a
    assert '"side": "self"' in _rows_of(pi_db)[a][3]


@pytest.mark.slow
def test_window_narrowing_and_stale(pi_db, monkeypatch):
    """② 窗口收窄（2026-09-14：12h → 2h，且到期判定走北京口径）。

    时间固定为 UTC 2026-09-14 02:00 = 北京 10:00，避免用例结果随跑测时刻漂移。
    """
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import (
        collect_due_promises, mark_stale_overdue, upsert_intent,
    )
    monkeypatch.setattr(pi, "_now_naive", lambda: datetime(2026, 9, 14, 2, 0))
    now_local = pi._now_local_naive()          # 北京 2026-09-14 10:00
    in_win = asyncio.run(upsert_intent(user_id=1, character_id=11, content="窗口内的一小时内承诺",
                                       kind="promise", due_end=now_local - timedelta(hours=1), source_message_id=911))
    late = asyncio.run(upsert_intent(user_id=1, character_id=11, content="迟到一天多的承诺超窗",
                                     kind="promise", due_end=now_local - timedelta(hours=13), source_message_id=912))
    ancient = asyncio.run(upsert_intent(user_id=1, character_id=11, content="很久以前的承诺八天",
                                        kind="promise", due_end=now_local - timedelta(days=8), source_message_id=913))

    due = asyncio.run(collect_due_promises())
    ids = {c["pis_id"] for c in due}
    assert in_win in ids
    assert late not in ids and ancient not in ids
    cand = [c for c in due if c["pis_id"] == in_win][0]
    assert cand["side"] in ("self", "user")

    rows = {i: v[2] for i, v in _rows_of(pi_db).items()}
    assert rows[in_win] == "pending"
    assert rows[late] == "stale"       # ② 超窗 13h > 2h → stale
    assert rows[ancient] == "expired"  # 既有 7 天宽限语义保持

    # mark_stale_overdue 幂等
    assert asyncio.run(mark_stale_overdue()) == 0


@pytest.mark.slow
def test_cross_day_date_scoped_promise_not_raised(pi_db, monkeypatch):
    """2026-09-14 真机反馈：日期型约定跨天一律不再主动提起（不再「使劲提昨天的事」）。

    现场：约定 09-13 的豆腐/收游戏，在 09-14 18:25~19:29（北京）被成对提起。
    """
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import collect_due_promises, upsert_intent, run_prospective_due

    monkeypatch.setattr(pi, "_now_naive", lambda: datetime(2026, 9, 14, 10, 0))   # 北京 18:00
    yesterday_eod = datetime(2026, 9, 13, 23, 59)                                # 昨天（北京日历日）到期
    pid = asyncio.run(upsert_intent(user_id=1, character_id=13, content="用户答应十一点前收游戏",
                                    kind="promise", due_end=yesterday_eod, source_message_id=941))

    due = asyncio.run(collect_due_promises())
    assert pid not in {c["pis_id"] for c in due}                                  # 不被采集
    assert {i: v[2] for i, v in _rows_of(pi_db).items()}[pid] == "stale"          # 直接置 stale（留痕）

    # 防御性硬闸：即使绕过采集直接调用，也不发送、且置 stale
    ok = asyncio.run(run_prospective_due({"pis_id": pid, "character_id": 13, "user_id": 1,
                                          "content": "用户答应十一点前收游戏", "side": "user",
                                          "due_end": yesterday_eod, "session_id": 7}))
    assert ok is False
    assert {i: v[2] for i, v in _rows_of(pi_db).items()}[pid] == "stale"


@pytest.mark.slow
def test_stale_marking_only_touches_pending_promises(pi_db):
    from app.scheduling.prospective_intent import (
        mark_stale_overdue, match_cue_intents, upsert_intent, _now_naive,
    )
    now = _now_naive()
    p = asyncio.run(upsert_intent(user_id=1, character_id=11, content="超窗的普通承诺一条",
                                  kind="promise", due_end=now - timedelta(hours=20), source_message_id=921))
    d = asyncio.run(upsert_intent(user_id=1, character_id=11, content="已经兑现掉的承诺一条",
                                  kind="promise", due_end=now - timedelta(hours=20), source_message_id=922))
    c = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户取消掉的承诺一条",
                                  kind="promise", due_end=now - timedelta(hours=20), source_message_id=923))
    cue = asyncio.run(upsert_intent(user_id=1, character_id=11, content="樱花开了提醒我拍照",
                                    kind="cue", cue_terms=["樱花"], source_message_id=924))

    # 手工置终态（绕过 run 链路）
    from app.scheduling.prospective_intent import _set_status
    asyncio.run(_set_status([d], "discharged", discharge=True))
    asyncio.run(_set_status([c], "cancelled"))
    asyncio.run(match_cue_intents(11, "樱花开了"))

    n = asyncio.run(mark_stale_overdue())
    assert n == 1  # 只动 pending promise
    rows = {i: v[2] for i, v in _rows_of(pi_db).items()}
    assert rows[p] == "stale"
    assert rows[d] == "discharged"
    assert rows[c] == "cancelled"
    assert rows[cue] == "matched"
    # stale 仍可被读/回忆（行仍在，只是状态标记）
    assert len(rows) == 4


def _candidate(pis_id, content, side="user", due_end=None):
    return {"pis_id": pis_id, "character_id": 11, "user_id": 1, "content": content,
            "side": side, "due_end": due_end, "session_id": 7}


@pytest.mark.slow
def test_same_intent_fire_once_idempotent(pi_db, monkeypatch):
    from app.scheduling.prospective_intent import upsert_intent, run_prospective_due, _now_naive
    pis_id = asyncio.run(upsert_intent(user_id=1, character_id=11, content="我承诺晚饭由我来做",
                                       kind="promise", due_end=_now_naive() - timedelta(minutes=5),
                                       source_message_id=931))
    sends = []

    async def _fake_llm(**kw):
        return "昨天的晚饭我来弄，这就去做。"

    async def _fake_send(session_id, character_id, user_id, content, message_type="prospective_intent", **kw):
        sends.append(content)

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "promise_self_side_split", True)

    ok1 = asyncio.run(run_prospective_due(_candidate(pis_id, "我承诺晚饭由我来做", "self")))
    ok2 = asyncio.run(run_prospective_due(_candidate(pis_id, "我承诺晚饭由我来做", "self")))
    assert ok1 is True
    assert ok2 is False            # ③ 第二次直接跳过，不重复落地
    assert len(sends) == 1
    assert _rows_of(pi_db)[pis_id][2] == "discharged"


@pytest.mark.slow
def test_opening_cooldown_blocks_templated_opener(pi_db, monkeypatch):
    from app.models.character import ProactiveMessageLog
    from app.scheduling.prospective_intent import (
        recent_opener_exists, run_prospective_due, upsert_intent, _now_naive,
    )
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "promise_self_side_split", True)
    now = _now_naive()

    async def _seed(content, age_hours):
        async with pi_db() as db:
            db.add(ProactiveMessageLog(character_id=11, session_id=None,
                                       message_type="prospective_intent", content=content,
                                       created_at=now - timedelta(hours=age_hours)))
            await db.commit()

    # 一小时冷却：10 分钟前已发过同款开场 → 命中
    asyncio.run(_seed("嘿，我记得你之前说过晚饭你来做的", 1 / 6))
    assert asyncio.run(recent_opener_exists(11)) is True

    pis_id = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户答应喂团子这件事",
                                       kind="promise", due_end=now - timedelta(minutes=5),
                                       source_message_id=941))
    sends = []

    async def _llm_opener(**kw):
        return "嘿，我记得你之前说过喂团子的事，现在时间也差不多了？"

    async def _fake_send(session_id, character_id, user_id, content, message_type="prospective_intent", **kw):
        sends.append(content)

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm_opener)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)
    assert asyncio.run(run_prospective_due(_candidate(pis_id, "用户答应喂团子这件事"))) is False
    assert sends == []
    assert _rows_of(pi_db)[pis_id][2] == "pending"  # 被拦→回滚可重试

    # 冷却窗口外（2 小时前）：同款开场放行一次
    async def _clear_logs():
        async with pi_db() as db:
            await db.execute(ProactiveMessageLog.__table__.delete())
            await db.commit()
    asyncio.run(_clear_logs())
    asyncio.run(_seed("嘿，我记得你之前说过喂团子", 2))
    assert asyncio.run(recent_opener_exists(11)) is False

    async def _llm_clean(**kw):
        return "喂团子的事，你去弄好了吗？"

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm_clean)
    assert asyncio.run(run_prospective_due(_candidate(pis_id, "用户答应喂团子这件事"))) is True
    assert len(sends) == 1


@pytest.mark.slow
def test_opening_cooldown_works_with_side_flag_off(pi_db, monkeypatch):
    """③ 开场冷却独立于 ① 的 side 开关：promise_self_side_split 关时仍拦同款开场。"""
    from app.models.character import ProactiveMessageLog
    from app.scheduling.prospective_intent import (
        recent_opener_exists, run_prospective_due, upsert_intent, _now_naive,
    )
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "promise_self_side_split", False)
    now = _now_naive()

    async def _seed():
        async with pi_db() as db:
            db.add(ProactiveMessageLog(character_id=11, session_id=None,
                                       message_type="prospective_intent",
                                       content="嘿，我记得你之前说过晚饭你来做的",
                                       created_at=now - timedelta(minutes=10)))
            await db.commit()

    asyncio.run(_seed())
    assert asyncio.run(recent_opener_exists(11)) is True
    pis_id = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户答应喂团子这件事",
                                       kind="promise", due_end=now - timedelta(minutes=5),
                                       source_message_id=961))
    sends = []

    async def _llm_opener(**kw):
        return "嘿，我记得你之前说过喂团子的事。"

    async def _fake_send(session_id, character_id, user_id, content, message_type="prospective_intent", **kw):
        sends.append(content)

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm_opener)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)
    assert asyncio.run(run_prospective_due(_candidate(pis_id, "用户答应喂团子这件事"))) is False
    assert sends == []
    assert _rows_of(pi_db)[pis_id][2] == "pending"


async def _age_created_at(factory, pis_id: int, created: datetime) -> None:
    """把某行 created_at 回拨（server_default 是 UTC，回拨值也按 UTC 给）。"""
    async with factory() as db:
        await db.execute(
            update(ProspectiveIntent).where(ProspectiveIntent.id == pis_id).values(created_at=created)
        )
        await db.commit()


@pytest.mark.slow
def test_cue_cross_day_excluded_from_match(pi_db, monkeypatch):
    """带日期窗的 cue 跨天后不再被 match 命中，并在线置 stale（lazy sweep）。"""
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import upsert_intent, match_cue_intents
    monkeypatch.setattr(pi, "_now_naive", lambda: datetime(2026, 9, 15, 2, 0))  # 北京 09-15 10:00
    yesterday_eod = datetime(2026, 9, 14, 23, 59)
    cid = asyncio.run(upsert_intent(user_id=1, character_id=11, content="樱花开了提醒我拍照",
                                    kind="cue", cue_terms=["樱花"], due_end=yesterday_eod,
                                    source_message_id=1001))
    hits = asyncio.run(match_cue_intents(11, "樱花开了好漂亮"))
    assert hits == []
    assert _rows_of(pi_db)[cid][2] == "stale"


@pytest.mark.slow
def test_cue_nodue_ages_out_after_30_days(pi_db, monkeypatch):
    """无 due 纯线索：创建满 30 天退场；新鲜线索正常命中且 pending→matched。"""
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import upsert_intent, match_cue_intents
    fixed_utc = datetime(2026, 9, 15, 2, 0)
    monkeypatch.setattr(pi, "_now_naive", lambda: fixed_utc)

    old = asyncio.run(upsert_intent(user_id=1, character_id=11, content="出门记得带伞",
                                    kind="cue", cue_terms=["出门"], source_message_id=1002))
    fresh = asyncio.run(upsert_intent(user_id=1, character_id=11, content="提到火锅想起要去重庆",
                                      kind="cue", cue_terms=["火锅"], source_message_id=1003))
    # 两条无 due 纯线索都显式回拨 created_at 为固定值，避免结果依赖真实时钟（Codex 复核要求 B）
    asyncio.run(_age_created_at(pi_db, old, fixed_utc - timedelta(days=31)))
    asyncio.run(_age_created_at(pi_db, fresh, fixed_utc))

    hits = asyncio.run(match_cue_intents(11, "等下出门一趟，晚上吃火锅"))
    hit_ids = {r.id for r in hits}
    assert old not in hit_ids and fresh in hit_ids          # 陈旧退场、新鲜命中
    rows = {i: v[2] for i, v in _rows_of(pi_db).items()}
    assert rows[old] == "stale"
    assert rows[fresh] == "matched"                         # 命中后 pending→matched


@pytest.mark.slow
def test_mark_stale_cues_sweep_and_idempotent(pi_db, monkeypatch):
    """周期清扫：只把该 stale 的 cue 置 stale；promise / 终态 / 新鲜 cue 不动；幂等。"""
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import (
        upsert_intent, mark_stale_cues, _set_status,
    )
    fixed_utc = datetime(2026, 9, 15, 2, 0)
    monkeypatch.setattr(pi, "_now_naive", lambda: fixed_utc)

    old_nodue = asyncio.run(upsert_intent(user_id=1, character_id=11, content="陈旧纯线索",
                                          kind="cue", cue_terms=["AAA"], source_message_id=1011))
    cross_day = asyncio.run(upsert_intent(user_id=1, character_id=11, content="跨天带窗线索",
                                          kind="cue", cue_terms=["BBB"],
                                          due_end=datetime(2026, 9, 14, 23, 59), source_message_id=1012))
    fresh = asyncio.run(upsert_intent(user_id=1, character_id=11, content="新鲜线索",
                                      kind="cue", cue_terms=["CCC"], source_message_id=1013))
    discharged = asyncio.run(upsert_intent(user_id=1, character_id=11, content="已兑现线索",
                                           kind="cue", cue_terms=["DDD"], source_message_id=1014))
    promise = asyncio.run(upsert_intent(user_id=1, character_id=11, content="迟到超窗的承诺",
                                        kind="promise",
                                        due_end=fixed_utc + timedelta(hours=8) - timedelta(hours=20),
                                        source_message_id=1015))
    # 无 due 的两条纯线索都显式回拨 created_at 为固定值（Codex 复核要求 B）
    asyncio.run(_age_created_at(pi_db, old_nodue, fixed_utc - timedelta(days=31)))
    asyncio.run(_age_created_at(pi_db, fresh, fixed_utc))
    asyncio.run(_set_status([discharged], "discharged", discharge=True))

    n = asyncio.run(mark_stale_cues())
    assert n == 2                                            # 只 stale 两条 cue
    rows = {i: v[2] for i, v in _rows_of(pi_db).items()}
    assert rows[old_nodue] == "stale"
    assert rows[cross_day] == "stale"
    assert rows[fresh] == "pending"
    assert rows[discharged] == "discharged"
    assert rows[promise] == "pending"                        # cue 清扫不碰 promise
    assert asyncio.run(mark_stale_cues()) == 0               # 幂等


# ───────── 批次一（2026-09-16）任务1/任务2：日期型当天可提 + 无 due promise 陈旧闸 ─────────

def test_intent_is_stale_nodue_promise_and_cue():
    """任务2 纯函数口径：无 due 的 promise 与 cue 同闸（31 天 stale / 29 天有效）；带 due 的 promise 不归本函数。"""
    from app.scheduling.prospective_intent import _intent_is_stale
    now_utc = datetime(2026, 9, 16, 2, 0)        # 北京 10:00
    now_local = now_utc + timedelta(hours=8)

    def _row(kind="promise", status="pending", due_end=None, created=None):
        return SimpleNamespace(kind=kind, status=status, due_end=due_end, created_at=created)

    # ① 无 due promise：31 天 → stale，29 天 → 仍在
    assert _intent_is_stale(_row(created=now_utc - timedelta(days=31)),
                            now_utc=now_utc, now_local=now_local) is True
    assert _intent_is_stale(_row(created=now_utc - timedelta(days=29)),
                            now_utc=now_utc, now_local=now_local) is False
    # ② matched 属半激活态，同样纳入
    assert _intent_is_stale(_row(status="matched", created=now_utc - timedelta(days=31)),
                            now_utc=now_utc, now_local=now_local) is True
    # ③ 带 due 的 promise 不归本函数（交给 mark_stale_overdue / collect_due_promises）
    assert _intent_is_stale(_row(due_end=now_local - timedelta(hours=20)),
                            now_utc=now_utc, now_local=now_local) is False
    # ④ 终态一律不动
    assert _intent_is_stale(_row(status="discharged", created=now_utc - timedelta(days=99)),
                            now_utc=now_utc, now_local=now_local) is False
    # ⑤ cue 原有口径不变（日期型跨天 / 无 due 超龄）
    assert _intent_is_stale(_row(kind="cue", due_end=datetime(2026, 9, 15, 23, 59)),
                            now_utc=now_utc, now_local=now_local) is True
    assert _intent_is_stale(_row(kind="cue", due_end=datetime(2026, 9, 16, 23, 59)),
                            now_utc=now_utc, now_local=now_local) is False
    assert _intent_is_stale(_row(kind="cue", created=now_utc - timedelta(days=31)),
                            now_utc=now_utc, now_local=now_local) is True


@pytest.mark.slow
def test_date_scoped_promise_raiseable_all_day_then_stale_next_day(pi_db, monkeypatch):
    """任务1 ①②：日期型约定到期日当天任一时刻（北京 10:00）可被捞到；跨天不再捞且置 stale。

    同时也验证 mark_stale_overdue 豁免日期型（否则当天上午就被 2 小时窗清掉）。
    """
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import (
        collect_due_promises, mark_stale_overdue, upsert_intent,
    )
    monkeypatch.setattr(pi, "_now_naive", lambda: datetime(2026, 9, 15, 2, 0))   # 北京 09-15 10:00
    today_eod = datetime(2026, 9, 15, 23, 59)                                    # 日期型（23:59）
    pid = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户答应今天把豆腐煎了",
                                    kind="promise", due_end=today_eod, source_message_id=2011))

    # 当天上午：2 小时窗清理必须豁免日期型
    assert asyncio.run(mark_stale_overdue()) == 0
    due = asyncio.run(collect_due_promises())
    assert pid in {c["pis_id"] for c in due}
    assert {i: v[2] for i, v in _rows_of(pi_db).items()}[pid] == "pending"

    # 次日同一时刻：跨天 → 不再捞，且被置 stale（留痕不删）
    monkeypatch.setattr(pi, "_now_naive", lambda: datetime(2026, 9, 16, 2, 0))
    due_next = asyncio.run(collect_due_promises())
    assert pid not in {c["pis_id"] for c in due_next}
    assert {i: v[2] for i, v in _rows_of(pi_db).items()}[pid] == "stale"


@pytest.mark.slow
def test_exact_time_promise_window_unchanged_and_nodue_untouched(pi_db, monkeypatch):
    """任务1 ③④：精确时刻型维持 [now-2h, now]（超窗 stale）；无 due promise 不受本条影响。"""
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import collect_due_promises, upsert_intent
    fixed_utc = datetime(2026, 9, 15, 2, 0)                                       # 北京 10:00
    monkeypatch.setattr(pi, "_now_naive", lambda: fixed_utc)
    now_local = pi._now_local_naive()

    in_win = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户答应18:30收游戏",
                                       kind="promise", due_end=now_local - timedelta(hours=1),
                                       source_message_id=2021))
    late = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户答应昨晚十点收游戏",
                                     kind="promise", due_end=now_local - timedelta(hours=20),
                                     source_message_id=2022))
    nodue = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户吃完饭后要报备一声",
                                      kind="promise", cue_terms=["报备"], source_message_id=2023))

    due = asyncio.run(collect_due_promises())
    ids = {c["pis_id"] for c in due}
    assert in_win in ids and late not in ids and nodue not in ids
    rows = {i: v[2] for i, v in _rows_of(pi_db).items()}
    assert rows[in_win] == "pending"
    assert rows[late] == "stale"        # 精确时刻型超 2h → stale（现状不变）
    assert rows[nodue] == "pending"     # 无 due 且新鲜 → 不受任务1影响


@pytest.mark.slow
def test_nodue_promise_swept_by_periodic_and_online(pi_db, monkeypatch):
    """任务2：无 due promise 创建超 30 天 → 周期清扫 + 在线采集两条路径都置 stale；新鲜的不动。"""
    import app.scheduling.prospective_intent as pi
    from app.scheduling.prospective_intent import (
        collect_due_promises, mark_stale_cues, upsert_intent,
    )
    fixed_utc = datetime(2026, 9, 16, 2, 0)
    monkeypatch.setattr(pi, "_now_naive", lambda: fixed_utc)

    old = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户吃完饭后要告诉我一声",
                                    kind="promise", cue_terms=["吃完饭"], source_message_id=2031))
    fresh = asyncio.run(upsert_intent(user_id=1, character_id=11, content="下次聚餐先跟用户报备",
                                      kind="promise", cue_terms=["聚餐"], source_message_id=2032))
    asyncio.run(_age_created_at(pi_db, old, fixed_utc - timedelta(days=31)))
    asyncio.run(_age_created_at(pi_db, fresh, fixed_utc))

    # 周期清扫（每小时）：只 stale 超龄那条
    assert asyncio.run(mark_stale_cues()) == 1
    rows = {i: v[2] for i, v in _rows_of(pi_db).items()}
    assert rows[old] == "stale" and rows[fresh] == "pending"

    # 在线路径（collect_due_promises 内 lazy sweep）：超龄的再置一条，仍新鲜的不动
    stale2 = asyncio.run(upsert_intent(user_id=1, character_id=11, content="用户洗完澡要说一声",
                                       kind="promise", cue_terms=["洗澡"], source_message_id=2033))
    asyncio.run(_age_created_at(pi_db, stale2, fixed_utc - timedelta(days=45)))
    asyncio.run(collect_due_promises())
    rows = {i: v[2] for i, v in _rows_of(pi_db).items()}
    assert rows[stale2] == "stale" and rows[fresh] == "pending"
    assert asyncio.run(mark_stale_cues()) == 0               # 幂等
