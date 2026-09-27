# -*- coding: utf-8 -*-
"""A4 批3 M1b2（2026-09-27）：三处 settle 钩子 + 影子改判留痕 + 按驱力切分取数 的回归测试。

覆盖派单 §2.4 六项：
① flag 关 ⇒ 三处钩子一次库都不多查、relational_drives 零变化、extra_meta 与改动前逐字节一致、
   trigger_reason 里不出现 ``[drive=``；
② flag 开 ⇒ arbiter 钩子把结算落库（水位增长 + 游标前进）、影子文本两种形态（有驱力 / 无驱力）、
   extra_meta 多出 shadow_drive / shadow_intent / level_at_send 三键且 M0 三键原样；
③ 发送行为不变 ⇒ 同一夹具在开 / 关下 candidate 字段、发送内容、发送条数三者一致（快照对比）；
④ 异常静默 ⇒ settle 抛错时 annotate / 回合末 / 周期兜底与发送流程照常返回、不冒泡；
⑤ 端点 shadow_agreement 与 by_shadow_drive 数值正确（含缺键老数据归空串组、59 / 61 分钟窗口边界）；
⑥ commit 口径 ⇒ 钩子自开 session 时确实提交（换一个连接读回）。

纪律：不调模型、不走网络；临时库一律 pytest ``tmp_path``（不连生产库）；项目未装 pytest-asyncio，
统一 asyncio.run。两档释放（release_open / release_full）属 M2，本单一次都不该调用 —— 故相关用例
顺带钉住 last_released_at / last_released_ratio 两列原样。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import scheduler as scheduler_api
from app.application import character_state_service as css
from app.application import chat_service
from app.application import relational_drive_service as svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.domain.proactivity import outreach as oc
from app.domain.relational import drives as d
from app.scheduling import arbiter
from app.utils.timeutil import now_naive_utc

OWNER = 1
CHAR = 1
SESSION = 1
# 固定「现在」：UTC 04:00 = 北京 12:00（白天 ⇒ 无夜间倍率；且避开「凌晨 0-7 点不发切片」夜间闸门）
FAKE_NOW = datetime(2026, 9, 27, 4, 0, 0)
GROW = d.DRIVE_GROWTH_PER_HOUR[d.DRIVE_LONGING]
NIGHT = d.DRIVE_NIGHT_MULTIPLIER[d.DRIVE_LONGING]


# ────────────────────── 公共夹具：临时 SQLite（每例独立，tmp_path） ──────────────────────


def _engine_factory(tmp_path, name="m1b2.db"):
    db_path = os.path.join(str(tmp_path), name).replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all():
        import app.models  # noqa: F401  确保全部 ORM 进入 metadata（含 relational_drives）
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    return engine, factory, db_path


def _seed_base(factory):
    async def _go():
        from app.models.character import AICharacter
        from app.models.chat import ChatSession
        from app.models.user import User
        async with factory() as db:
            db.add_all([
                User(id=OWNER, username="drv_u1", nickname="u1"),
                AICharacter(id=CHAR, user_id=OWNER, name="小暖", is_active=True),
                ChatSession(id=SESSION, user_id=OWNER, character_id=CHAR),
            ])
            await db.commit()

    asyncio.run(_go())


def _flag(monkeypatch, on: bool) -> None:
    """翻影子总闸（三处钩子与仓储层读的都是这一项）。"""
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, svc.FLAG_KEY, bool(on))


def _stmt_counter(engine) -> dict:
    """SQL 计数：「flag 关 ⇒ 一次库都不多查」以此为准（开 session 本身不产生语句）。"""
    counter = {"n": 0}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _tally(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    return counter


def _raw_drive_rows(db_path) -> list[tuple]:
    """绕过 ORM、换一条连接读全表（⑥「确实提交了」的判据：另一个连接看得见）。"""
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT drive_key, level, last_settled_at, last_released_at, last_released_ratio "
            "FROM relational_drives WHERE character_id=? ORDER BY drive_key",
            (CHAR,),
        ).fetchall()


def _seed_rows(factory, rows) -> None:
    """预置水位：rows = [(drive_key, level, last_settled_at)]。"""
    from app.models.character import RelationalDrive

    async def _go():
        async with factory() as db:
            for key, level, cursor in rows:
                db.add(RelationalDrive(
                    character_id=CHAR, user_id=OWNER, drive_key=key, level=level,
                    last_settled_at=cursor,
                ))
            await db.commit()

    asyncio.run(_go())


def _patch_all_sessions(monkeypatch, factory) -> None:
    """三处钩子各自的 session 出口全部指向临时库（绝不误连生产库）。"""
    monkeypatch.setattr(arbiter, "async_session_factory", factory)
    monkeypatch.setattr(chat_service, "async_session_factory", factory)
    monkeypatch.setattr(css, "async_session_factory", factory)


class _FakeSend:
    def __init__(self):
        self.calls = []

    async def __call__(self, session_id, character_id, user_id, content, message_type, **kw):
        self.calls.append({"message_type": message_type, "content": content, **kw})


def _patch_clock_and_sender(monkeypatch, at_utc_naive) -> _FakeSend:
    """把 arbiter 的 datetime.now 固定到白天时刻（夜间闸门不触发），发送出口换成替身。"""
    base = at_utc_naive.replace(tzinfo=timezone.utc)

    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

    fake = _FakeSend()
    monkeypatch.setattr(arbiter, "datetime", _FixedDT)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", fake)
    return fake


def _seed_item(factory, now, reasoning=None, seq=0):
    from app.models.character import ProactiveStorylineItem

    async def _ins():
        async with factory() as db:
            db.add(ProactiveStorylineItem(
                character_id=CHAR, session_id=SESSION, user_id=OWNER,
                group_id="g-m1b2-1", seq=seq, content="在吗", reasoning=reasoning,
                send_at=now - timedelta(seconds=5), status="pending",
            ))
            await db.commit()

    asyncio.run(_ins())


def _annotate(factory, monkeypatch, *, on: bool, mats=None, idle_minutes=60):
    """跑一次真实的意图选择（素材 / 最近意图预置 ⇒ 除本单钩子外零 IO），返回 (item, candidate)。"""
    cand = {
        "character_id": CHAR,
        "user_id": OWNER,
        "session_id": SESSION,
        "idle_minutes": idle_minutes,  # ≤2h → tier=continue
        "trigger_reason": "节律采样",
        "last_context": "用户：我回来了",
    }
    item = {"type": "motivation", "candidate": cand, "priority": 1}
    _patch_all_sessions(monkeypatch, factory)
    _flag(monkeypatch, on)
    asyncio.run(arbiter._annotate_outreach_plan(
        item, CHAR,
        {CHAR: mats or oc.OutreachMaterials(has_open_loop=True, has_life_now=True)},
        {CHAR: []},
    ))
    return item, cand


_REAL_SELECT_OUTREACH = oc.select_outreach  # 必须在任何打补丁之前抓住原函数


def _pin_outreach_rng(monkeypatch, seed: int = 7) -> None:
    """把意图选择的随机钉死（select_outreach 每次自带 random.Random() ⇒ 不注入就是掷骰子）。

    只影响测试内的可复现性：真实实现照跑（权重 / 素材前提 / 避开最近意图都不变），只是骰子有底。
    """
    def _fixed(tier, materials, recent_intents=None):
        return _REAL_SELECT_OUTREACH(tier, materials, recent_intents, rng=random.Random(seed))

    monkeypatch.setattr(oc, "select_outreach", _fixed)


def _pipeline(tmp_path, monkeypatch, *, on: bool, rows=None, reasoning="先想想怎么开口", tag="a"):
    """「意图选择 → 真正发送（seq=0 落留痕）」整条链路跑一遍，返回可对比的快照。

    tag 决定库文件名：同一个用例里跑两遍（开 / 关对照）必须各用一份临时库，否则会重复播种。
    """
    _pin_outreach_rng(monkeypatch)
    engine, factory, path = _engine_factory(tmp_path, f"m1b2_{tag}.db")
    _seed_base(factory)
    if rows:
        _seed_rows(factory, rows)
    item, cand = _annotate(factory, monkeypatch, on=on)
    trace = dict(arbiter._OUTREACH_SEND_TRACE.get(CHAR) or {})  # 发送时会被取走，先留一份
    _seed_item(factory, FAKE_NOW, reasoning=reasoning)
    fake = _patch_clock_and_sender(monkeypatch, FAKE_NOW)
    sent = asyncio.run(arbiter.flush_storyline_items())
    return SimpleNamespace(
        engine=engine, factory=factory, path=path, item=item, cand=cand, trace=trace,
        meta=fake.calls[0]["extra_meta"] if fake.calls else None,
        contents=[c["content"] for c in fake.calls], sent=sent,
    )


@pytest.fixture(autouse=True)
def _clean_process_state():
    """M0 观测暂存与人格基线缓存都是进程内的，逐例清，防止用例间串味。"""
    arbiter._OUTREACH_SEND_TRACE.clear()
    css._persona_baseline.clear()
    yield
    arbiter._OUTREACH_SEND_TRACE.clear()
    css._persona_baseline.clear()


# ─────────────────────────── ① flag 关 ⇒ 逐字节旧行为 ───────────────────────────


def test_flag关_三处钩子零查询且留痕逐字节旧行为(tmp_path, monkeypatch):
    engine, factory, path = _engine_factory(tmp_path)
    _seed_base(factory)
    _seed_rows(factory, [(d.DRIVE_LONGING, 40.0, FAKE_NOW - timedelta(hours=6))])  # 预置行也不许被碰
    before = _raw_drive_rows(path)
    counter = _stmt_counter(engine)
    base_n = counter["n"]

    item, cand = _annotate(factory, monkeypatch, on=False)                      # 钩子 1
    asyncio.run(chat_service._settle_relational_drive(CHAR, OWNER))             # 钩子 2
    assert asyncio.run(css.settle_recent_relational_drives([CHAR], now_naive_utc())) == 0  # 钩子 3
    assert counter["n"] == base_n, "flag 关时三处钩子一次库都不许多查"

    # 周期任务整体照跑（它自身既有的 SELECT 不属本单新增），水位仍一字未动
    assert asyncio.run(css.drift_all_character_states()) is None
    assert _raw_drive_rows(path) == before
    assert item.get("_drive_note") is None

    # 发送留痕：与改动前逐字节一致（reasoning + M0 三键，键序也不变）
    _seed_item(factory, FAKE_NOW, reasoning="先想想怎么开口")
    fake = _patch_clock_and_sender(monkeypatch, FAKE_NOW)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1
    expected = json.dumps(
        {"reasoning": "先想想怎么开口", "intent": cand["outreach_intent"],
         "tier": cand["outreach_plan"]["tier"], "materials": ["open_loop", "life"]},
        ensure_ascii=False,
    )
    assert fake.calls[0]["extra_meta"] == expected

    # trigger_reason：不出现 [drive=，既有 [ctx=] / [outreach=] 拼串照旧
    async def _log_and_read():
        await arbiter.log_trigger_candidate(item, True)
        async with factory() as db:
            return (await db.execute(select(arbiter.ProactiveTriggerLog.trigger_reason))).scalars().all()

    reasons = asyncio.run(_log_and_read())
    assert reasons and all("[drive=" not in (r or "") for r in reasons)
    assert any("[outreach=" in (r or "") for r in reasons), "M0/B1-③ 既有拼串不受影响"


def test_flag关_总闸缺省为关():
    """钩子的「关＝零成本」前提：默认值就是关（真值取自 AGENT_FLAGS，不是 runtime_flags）。"""
    from app.agent.loop import AGENT_FLAGS

    assert AGENT_FLAGS[svc.FLAG_KEY] is False
    assert svc.shadow_enabled() is False


# ──────────────── ② flag 开 ⇒ 结算落库 + 影子文本 + 三键留痕 ────────────────


def test_flag开_arbiter钩子结算落库并追加影子三键(tmp_path, monkeypatch):
    cursor = now_naive_utc() - timedelta(hours=2)
    out = _pipeline(
        tmp_path, monkeypatch, on=True,
        rows=[(d.DRIVE_LONGING, 60.0, cursor), (d.DRIVE_CONCERN, 20.0, cursor)],
    )
    # ① 结算落库：行在、水位按 settle 增长（跨夜间也至少有 0.6 当量）、游标前进
    rows = {r[0]: r for r in _raw_drive_rows(out.path)}
    level = rows[d.DRIVE_LONGING][1]
    assert 60.0 + 2 * GROW * NIGHT <= level <= 60.0 + 2.01 * GROW  # 上限留 µs 级余量
    assert rows[d.DRIVE_LONGING][2] > str(cursor)[:19], "游标必须推进（懒结算的唯一事实）"
    assert d.DRIVE_INTIMACY not in rows, "影子期只建最小集合，intimacy 不落行"
    # 两档释放属 M2：本单一次都不许调用
    assert all(r[3] is None and r[4] == 0.0 for r in rows.values())

    # ② 三键进同一个 dict，且 M0 三键一字未动
    trace = out.trace
    assert trace["intent"] == out.cand["outreach_intent"]
    assert trace["tier"] == out.cand["outreach_plan"]["tier"]
    assert trace["materials"] == ["open_loop", "life"]
    assert trace["shadow_drive"] == d.DRIVE_LONGING
    assert trace["shadow_intent"] == d.DRIVE_TO_INTENT[d.DRIVE_LONGING] == oc.CHECK_IN
    assert trace["level_at_send"] == pytest.approx(level)
    assert set(trace) == {"intent", "tier", "materials",
                          "shadow_drive", "shadow_intent", "level_at_send"}

    # ③ 长期留痕（proactive_message_logs.extra_meta）
    meta = json.loads(out.meta)
    assert meta["reasoning"] == "先想想怎么开口"
    assert meta["shadow_drive"] == d.DRIVE_LONGING and meta["shadow_intent"] == oc.CHECK_IN
    assert meta["level_at_send"] == pytest.approx(level)
    # ⑥ 钩子自开 session ⇒ 自己 commit（上面换连接读得到即已证明）
    assert rows


def test_影子文本_有驱力形态格式正确(tmp_path, monkeypatch):
    """[drive=驱力:水位→would=驱力会选的|did=实际选的]，水位一位小数。"""
    _, factory, _ = _engine_factory(tmp_path)
    _seed_base(factory)
    _patch_all_sessions(monkeypatch, factory)
    _flag(monkeypatch, True)
    _seed_rows(factory, [(d.DRIVE_LONGING, 63.2, now_naive_utc())])  # 游标＝now ⇒ 增量 0

    item: dict = {}
    asyncio.run(arbiter._shadow_drive_note(item, CHAR, OWNER, SimpleNamespace(intent=oc.RECALL_SHARED)))
    assert item["_drive_note"] == "[drive=longing:63.2→would=check_in|did=recall_shared]"


def test_影子文本_无驱力时空串与零水位(tmp_path, monkeypatch):
    """首次结算只定游标（增量 0）⇒ 无驱力：文本 [drive=none]、三键为空串 / 0.0。"""
    _, factory, _ = _engine_factory(tmp_path)
    _seed_base(factory)
    _patch_all_sessions(monkeypatch, factory)
    _flag(monkeypatch, True)
    arbiter._OUTREACH_SEND_TRACE[CHAR] = {"intent": oc.CHECK_IN, "tier": "recent", "materials": ["life"]}

    item: dict = {}
    asyncio.run(arbiter._shadow_drive_note(item, CHAR, OWNER, SimpleNamespace(intent=oc.CHECK_IN)))
    assert item["_drive_note"] == "[drive=none]"
    trace = arbiter._OUTREACH_SEND_TRACE[CHAR]
    assert trace["shadow_drive"] == "" and trace["shadow_intent"] == ""
    assert trace["level_at_send"] == 0.0
    assert trace["intent"] == oc.CHECK_IN and trace["materials"] == ["life"], "M0 三键不许动"

    # 无暂存 ⇒ 不新增键（对齐 M0「有暂存才写」）
    arbiter._OUTREACH_SEND_TRACE.clear()
    asyncio.run(arbiter._shadow_drive_note({}, CHAR, OWNER, SimpleNamespace(intent=oc.CHECK_IN)))
    assert arbiter._OUTREACH_SEND_TRACE == {}


def test_影子文本走_intimacy_不参与定调(tmp_path, monkeypatch):
    """设计 §⑧ 第 4 条：intimacy 再高也不进候选 ⇒ 视为无驱力。"""
    _, factory, _ = _engine_factory(tmp_path)
    _seed_base(factory)
    _patch_all_sessions(monkeypatch, factory)
    _flag(monkeypatch, True)
    _seed_rows(factory, [(d.DRIVE_LONGING, 5.0, now_naive_utc())])
    # 直接把 intimacy 抬到最高（绕开「影子期不建 intimacy 行」，模拟历史脏行）
    async def _bump():
        from app.models.character import RelationalDrive
        async with factory() as db:
            db.add(RelationalDrive(
                character_id=CHAR, user_id=OWNER, drive_key=d.DRIVE_INTIMACY,
                level=99.0, last_settled_at=now_naive_utc(),
            ))
            await db.commit()

    asyncio.run(_bump())
    item: dict = {}
    asyncio.run(arbiter._shadow_drive_note(item, CHAR, OWNER, SimpleNamespace(intent=oc.SHARE_SELF)))
    assert item["_drive_note"] == "[drive=longing:5.0→would=check_in|did=share_self]"


# ─────────────────────────── ③ 发送行为不变（快照对比） ───────────────────────────


def test_发送行为在开关两侧一致(tmp_path, monkeypatch):
    """同一夹具跑两遍（唯一变量＝relational_drive_shadow）：候选字段 / 发送内容 / 条数三者一致。

    prompt 入参口径：进 message_generator 的只有 candidate 的 outreach_intent / outreach_plan 等字段
    （两侧逐键相等即证），本单不新增 section、不改任何文案。
    """
    off = _pipeline(tmp_path, monkeypatch, on=False, tag="off")
    on = _pipeline(
        tmp_path, monkeypatch, on=True, tag="on",
        rows=[(d.DRIVE_LONGING, 60.0, now_naive_utc() - timedelta(hours=2))],
    )
    assert json.dumps(on.cand, sort_keys=True, ensure_ascii=False) == json.dumps(
        off.cand, sort_keys=True, ensure_ascii=False), "candidate 字段（含 outreach_*）逐键一致"
    assert on.contents == off.contents, "发送内容不变"
    assert on.sent == off.sent == 1, "发送条数不变"
    on_meta, off_meta = json.loads(on.meta), json.loads(off.meta)
    assert {k: v for k, v in on_meta.items() if k in off_meta} == off_meta, "开闸只追加键，既有键值不动"


# ─────────────────────────── ④ 异常静默（绝不冒泡） ───────────────────────────


def test_settle抛异常时三处钩子与发送照常(tmp_path, monkeypatch):
    _, factory, path = _engine_factory(tmp_path)
    _seed_base(factory)
    _flag(monkeypatch, True)

    async def _boom(*a, **kw):
        raise RuntimeError("settle 炸了")

    monkeypatch.setattr(svc, "settle", _boom)
    item, cand = _annotate(factory, monkeypatch, on=True)
    assert cand["outreach_intent"], "意图选择照常完成"
    assert item.get("_drive_note") is None
    assert set(arbiter._OUTREACH_SEND_TRACE[CHAR]) == {"intent", "tier", "materials"}, \
        "钩子失败 ⇒ 影子键一个都不写"

    # 发送照常（异常不冒泡、留痕仍带 M0 三键）
    _seed_item(factory, FAKE_NOW, reasoning="照发")
    fake = _patch_clock_and_sender(monkeypatch, FAKE_NOW)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1
    assert json.loads(fake.calls[0]["extra_meta"])["intent"] == cand["outreach_intent"]

    # 回合末 / 周期兜底：吞掉异常、正常返回
    assert asyncio.run(chat_service._settle_relational_drive(CHAR, OWNER)) is None
    assert asyncio.run(css.settle_recent_relational_drives([CHAR], now_naive_utc())) == 0
    assert _raw_drive_rows(path) == []


def test_周期兜底取数异常与空输入不抛(tmp_path, monkeypatch):
    """角色不存在（取不到 user_id）/ 空清单 ⇒ 0 条、不抛（漂移任务不受影响）。"""
    _, factory, _ = _engine_factory(tmp_path)
    _seed_base(factory)
    _patch_all_sessions(monkeypatch, factory)
    _flag(monkeypatch, True)
    assert asyncio.run(css.settle_recent_relational_drives([999], now_naive_utc())) == 0
    assert asyncio.run(css.settle_recent_relational_drives([], now_naive_utc())) == 0


# ─────────────────── ⑤ 端点：影子一致率 + 按驱力切分回复率 ───────────────────


def _client(tmp_path, name="m1b2_api.db"):
    _engine, factory, _ = _engine_factory(tmp_path, name)
    _seed_base(factory)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app = FastAPI()
    app.include_router(scheduler_api.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: OWNER
    return TestClient(app), factory


async def _add_logs(factory, logs):
    from app.models.character import ProactiveMessageLog
    async with factory() as db:
        db.add_all([ProactiveMessageLog(**kw) for kw in logs])
        await db.commit()


async def _add_user_replies(factory, times):
    from app.models.chat import ChatMessage
    async with factory() as db:
        db.add_all([
            ChatMessage(session_id=SESSION, sender_type="user", content="收到了", created_at=t)
            for t in times
        ])
        await db.commit()


def _log(created_at, extra_meta=None):
    return {
        "character_id": CHAR, "session_id": SESSION, "message_type": "storyline",
        "content": "在吗", "created_at": created_at, "extra_meta": extra_meta,
    }


def _shadow_meta(intent, drive=None, shadow_intent=None):
    payload = {"intent": intent, "tier": "recent", "materials": ["life"]}
    if drive is not None:
        payload["shadow_drive"] = drive
        payload["shadow_intent"] = shadow_intent
        payload["level_at_send"] = 50.0
    return json.dumps(payload, ensure_ascii=False)


def _drive_group(body, drive):
    for g in body["by_shadow_drive"]:
        if g["drive"] == drive:
            return g
    raise AssertionError(f"驱力分组 {drive!r} 不存在：{body['by_shadow_drive']}")


def test_端点_shadow_agreement与by_shadow_drive数值(tmp_path):
    client, factory = _client(tmp_path)
    t0 = (now_naive_utc() - timedelta(days=3)).replace(microsecond=0)
    # (i, intent, shadow_drive, shadow_intent, 相对本条发送时刻的用户回复)
    cases = [
        (0, oc.CHECK_IN, d.DRIVE_LONGING, oc.CHECK_IN, timedelta(minutes=59)),    # 一致 + 窗口内
        (1, oc.RECALL_SHARED, d.DRIVE_AFFECTION, oc.RECALL_SHARED, None),         # 一致 + 无回复
        (2, oc.CHECK_IN, d.DRIVE_SHARING, oc.SHARE_SELF, timedelta(minutes=61)),  # 不一致 + 61 分钟
        (3, oc.CHECK_IN, None, None, None),                                       # 缺键老数据 → 空串组
        (4, oc.CHECK_IN, "", "", timedelta(minutes=45)),                          # 无驱力 → 空串组
    ]
    logs, replies = [], []
    for i, intent, drive, shadow_intent, gap in cases:
        sent_at = t0 + timedelta(hours=i * 2)
        logs.append(_log(sent_at, _shadow_meta(intent, drive, shadow_intent)))
        if gap is not None:
            replies.append(sent_at + gap)
    asyncio.run(_add_logs(factory, logs))
    asyncio.run(_add_user_replies(factory, replies))

    body = client.get("/api/v1/scheduler/stats/outreach").json()
    assert body["shadow_agreement"] == {
        "total": 5, "agree": 2, "disagree": 1, "missing": 2,
        "agreement_rate": pytest.approx(round(2 / 3, 4)),
    }
    assert {g["drive"] for g in body["by_shadow_drive"]} == {
        d.DRIVE_LONGING, d.DRIVE_AFFECTION, d.DRIVE_SHARING, ""
    }
    empty = _drive_group(body, "")
    assert empty == {"drive": "", "sent": 2, "scorable": 2,
                     "replied_within_window": 1, "reply_rate_60min": 0.5}
    assert _drive_group(body, d.DRIVE_LONGING)["replied_within_window"] == 1
    assert _drive_group(body, d.DRIVE_LONGING)["reply_rate_60min"] == 1.0
    assert _drive_group(body, d.DRIVE_AFFECTION)["replied_within_window"] == 0
    assert _drive_group(body, d.DRIVE_SHARING)["replied_within_window"] == 0, "61 分钟不算接住"
    assert body["by_shadow_drive"][0]["drive"] == "", "排序与 groups 同口径（发送多者在前）"
    # M0 既有段不受影响
    assert body["total_sent"] == 5
    assert [g for g in body["groups"] if g["intent"] == oc.CHECK_IN][0]["sent"] == 4


def test_端点_老数据全缺键时影子段仍稳定输出(tmp_path):
    """整批老数据（无影子键）⇒ agree/disagree 全 0、一致率 0.0，全部落空串组，不抛。"""
    client, factory = _client(tmp_path, "m1b2_api2.db")
    t0 = (now_naive_utc() - timedelta(days=2)).replace(microsecond=0)
    asyncio.run(_add_logs(factory, [_log(t0 + timedelta(minutes=i), None) for i in range(3)]))
    body = client.get("/api/v1/scheduler/stats/outreach").json()
    assert body["shadow_agreement"] == {
        "total": 3, "agree": 0, "disagree": 0, "missing": 3, "agreement_rate": 0.0,
    }
    assert body["by_shadow_drive"] == [
        {"drive": "", "sent": 3, "scorable": 3, "replied_within_window": 0, "reply_rate_60min": 0.0}
    ]
    assert body["total_sent"] == 3 and body["window_minutes"] == 60


# ─────────────────────── ⑥ commit 口径 / 周期兜底取数 ───────────────────────


def test_回合末钩子确实提交水位(tmp_path, monkeypatch):
    cursor = now_naive_utc() - timedelta(hours=3)
    _, factory, path = _engine_factory(tmp_path)
    _seed_base(factory)
    _seed_rows(factory, [(d.DRIVE_LONGING, 10.0, cursor)])
    _patch_all_sessions(monkeypatch, factory)
    _flag(monkeypatch, True)

    assert asyncio.run(chat_service._settle_relational_drive(CHAR, OWNER)) is None
    rows = {r[0]: r for r in _raw_drive_rows(path)}  # 换连接读回 ⇒ 已提交
    assert rows[d.DRIVE_LONGING][1] > 10.0
    assert rows[d.DRIVE_LONGING][2] > str(cursor)[:19]


def test_周期兜底只结算近期有互动的角色(tmp_path, monkeypatch):
    from app.models.character import CharacterState

    recent = now_naive_utc() - timedelta(hours=5)
    stale = now_naive_utc() - timedelta(days=30)
    cursor = now_naive_utc() - timedelta(hours=1)
    engine, factory, path = _engine_factory(tmp_path)
    _seed_base(factory)
    _seed_rows(factory, [(d.DRIVE_LONGING, 10.0, cursor)])

    async def _seed_state():
        async with factory() as db:
            db.add(CharacterState(character_id=CHAR, mood=70, last_activity_at=recent))
            await db.commit()

    asyncio.run(_seed_state())
    _patch_all_sessions(monkeypatch, factory)
    _flag(monkeypatch, True)

    states = [SimpleNamespace(character_id=CHAR, last_activity_at=recent, updated_at=recent),
              SimpleNamespace(character_id=555, last_activity_at=stale, updated_at=stale)]
    assert css._drive_settle_candidates(states, now_naive_utc()) == [CHAR], "近 N 小时之外不进兜底"
    assert css._DRIVE_SETTLE_HOURS == 24

    assert asyncio.run(css.settle_recent_relational_drives([CHAR], now_naive_utc())) == 1
    before = _raw_drive_rows(path)[0]
    assert before[1] > 10.0 and before[2] > str(cursor)[:19], "兜底自开 session ⇒ 自己提交"

    # 串起真实漂移任务：跑一次 drift，游标继续被推进（挂点生效）
    assert asyncio.run(css.drift_all_character_states()) is None
    assert _raw_drive_rows(path)[0][2] > before[2]
