# -*- coding: utf-8 -*-
"""A4 批3 M0（基线与留痕，只埋点、零语义）回归测试。

覆盖派单 §2.3 的四项：
① 发送留痕补 intent/tier/materials 三键，且既有键（reasoning）一个都不变；
   无暂存时 payload 与改动前逐字节一致（reasoning-only / None 两种旧形态都钉住）；
② 60 分钟回复窗口边界：59 分钟算接住、61 分钟不算（恰 60 分钟按「≤」算）；
③ 按 intent × tier 切分正确（含只有一种 intent、tier 为空串时分组不误合并）；
④ 默认 30 天窗口，且新聚合**不受「前 200 条」截断影响**（造 > 200 条数据验证）；
   附带钉住旧端点 GET /stats 的 reply_scan_limit 默认 200＝行为不变、传 0＝放开上限。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库一律 pytest tmp_path，不连生产库。）
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import scheduler as scheduler_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.domain.proactivity import outreach as oc
from app.scheduling import arbiter
from app.utils.timeutil import now_naive_utc

OWNER = 1
CHAR = 1
SESSION = 1
# flush_storyline_items 的固定「现在」：UTC 04:00 ＝ 北京 12:00（避开「凌晨 0-7 点不发切片」夜间闸门）
FAKE_NOW = datetime(2026, 9, 27, 4, 0, 0)


# ────────────────────── 公共夹具：临时 SQLite（每例独立，tmp_path） ──────────────────────


def _factory(tmp_path):
    db_path = os.path.join(str(tmp_path), "m0.db").replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all():
        import app.models  # noqa: F401  确保全部 ORM 进入 metadata
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    return factory


def _client(tmp_path):
    """建一套「临时库 + scheduler 路由」的 TestClient（租户口径与 GET /stats 同闸门）。"""
    factory = _factory(tmp_path)

    async def _seed_base():
        from app.models.character import AICharacter
        from app.models.chat import ChatSession
        from app.models.user import User
        async with factory() as db:
            db.add_all([
                User(id=OWNER, username="u1", nickname="用户1"),
                AICharacter(id=CHAR, user_id=OWNER, name="小爱"),
                ChatSession(id=SESSION, user_id=OWNER, character_id=CHAR),
            ])
            await db.commit()

    asyncio.run(_seed_base())

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


def _log(created_at, extra_meta=None, message_type="storyline"):
    return {
        "character_id": CHAR, "session_id": SESSION, "message_type": message_type,
        "content": "在吗", "created_at": created_at, "extra_meta": extra_meta,
    }


def _meta(intent=None, tier=None):
    payload = {}
    if intent is not None:
        payload["intent"] = intent
    if tier is not None:
        payload["tier"] = tier
    return json.dumps(payload, ensure_ascii=False)


def _group(body, intent, tier):
    for g in body["groups"]:
        if g["intent"] == intent and g["tier"] == tier:
            return g
    raise AssertionError(f"分组 ({intent}, {tier}) 不存在：{body['groups']}")


# ──────────────── ① 留痕补键：_annotate_outreach_plan → flush_storyline_items ────────────────


@pytest.fixture(autouse=True)
def _clean_trace_carrier():
    """M0 观测暂存是进程内字典，逐例清空，防止用例间串味。"""
    arbiter._OUTREACH_SEND_TRACE.clear()
    yield
    arbiter._OUTREACH_SEND_TRACE.clear()


def _annotate(mats=None):
    """跑一次真实的意图选择（素材/最近意图预先塞好 → 零 IO、零 DB），返回 candidate。"""
    cand = {
        "character_id": CHAR,
        "user_id": OWNER,
        "session_id": SESSION,
        "idle_minutes": 60,  # ≤2h → tier=continue
    }
    item = {"type": "motivation", "candidate": cand}
    asyncio.run(arbiter._annotate_outreach_plan(
        item, CHAR, {CHAR: mats or oc.OutreachMaterials(has_open_loop=True, has_life_now=True)},
        {CHAR: []},
    ))
    return cand


def test_annotate_stores_three_trace_keys():
    """意图选定后，暂存里就是「本次实际使用」的 intent/tier ＋ 素材短标识（只收 True 的）。"""
    cand = _annotate()
    trace = arbiter._OUTREACH_SEND_TRACE[CHAR]
    assert trace["intent"] == cand["outreach_intent"]
    assert trace["tier"] == cand["outreach_plan"]["tier"] == oc.TIER_CONTINUE
    assert trace["materials"] == ["open_loop", "life"]
    assert set(trace) == {"intent", "tier", "materials"}


def test_annotate_trace_materials_empty_when_no_materials():
    """无素材时 materials 是空列表（不是缺键、不编造）。"""
    _annotate(oc.OutreachMaterials())
    assert arbiter._OUTREACH_SEND_TRACE[CHAR]["materials"] == []


class _FakeSend:
    def __init__(self):
        self.calls = []

    async def __call__(self, session_id, character_id, user_id, content, message_type, **kw):
        self.calls.append({"message_type": message_type, "content": content, **kw})


def _patch_clock_and_sender(monkeypatch, at_utc_naive):
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
                group_id="g-m0-1", seq=seq, content="在吗", reasoning=reasoning,
                send_at=now - timedelta(seconds=5), status="pending",
            ))
            await db.commit()

    asyncio.run(_ins())


def test_send_trace_carries_three_keys_and_keeps_reasoning(tmp_path, monkeypatch):
    """①：真正发出那条留痕时 reasoning（既有键）逐字不变，且多出三个新键；暂存取走即清。"""
    factory = _factory(tmp_path)
    monkeypatch.setattr(arbiter, "async_session_factory", factory)

    _seed_item(factory, FAKE_NOW, reasoning="先想想怎么开口")
    cand = _annotate()
    fake = _patch_clock_and_sender(monkeypatch, FAKE_NOW)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1

    meta = json.loads(fake.calls[0]["extra_meta"])
    assert meta["reasoning"] == "先想想怎么开口"           # 既有键值不变
    assert meta["intent"] == cand["outreach_intent"]       # 三个新键
    assert meta["tier"] == cand["outreach_plan"]["tier"]
    assert meta["materials"] == ["open_loop", "life"]
    assert arbiter._OUTREACH_SEND_TRACE.get(CHAR) is None  # 不重复计到下一条


def test_send_payload_unchanged_without_trace(tmp_path, monkeypatch):
    """①回归：未走接触意图链路（无暂存）时 payload 与改动前逐字节一致（reasoning-only / None）。"""
    factory = _factory(tmp_path)
    monkeypatch.setattr(arbiter, "async_session_factory", factory)

    _seed_item(factory, FAKE_NOW, reasoning="只想说一句")
    fake = _patch_clock_and_sender(monkeypatch, FAKE_NOW)
    asyncio.run(arbiter.flush_storyline_items())
    assert json.loads(fake.calls[0]["extra_meta"]) == {"reasoning": "只想说一句"}

    later = FAKE_NOW + timedelta(minutes=1)
    _seed_item(factory, later, reasoning=None)
    fake2 = _patch_clock_and_sender(monkeypatch, later + timedelta(minutes=1))
    asyncio.run(arbiter.flush_storyline_items())
    assert fake2.calls[0]["extra_meta"] is None


# ─────────────────────── ② / ③ / ④ 只读聚合端点 ───────────────────────


def test_reply_window_60min_boundary(tmp_path):
    """②：59 分钟算接住、61 分钟不算（恰 60 分钟按「≤」算）；旧口径「之后任意时刻」作参考行。"""
    client, factory = _client(tmp_path)
    t0 = (now_naive_utc() - timedelta(days=3)).replace(microsecond=0)
    pairs = [
        (t0, t0 + timedelta(minutes=59)),                              # 窗口内
        (t0 + timedelta(hours=2), t0 + timedelta(hours=3, minutes=1)),  # 61 分钟：窗口外
        (t0 + timedelta(hours=5), t0 + timedelta(hours=6)),             # 恰 60 分钟
    ]
    asyncio.run(_add_logs(factory, [_log(s, _meta("check_in", "recent")) for s, _r in pairs]))
    asyncio.run(_add_user_replies(factory, [r for _s, r in pairs]))

    g = _group(client.get("/api/v1/scheduler/stats/outreach", params={"days": 30}).json(),
               "check_in", "recent")
    assert g["sent"] == 3 and g["scorable"] == 3
    assert g["replied_within_window"] == 2                # 59 分 + 恰 60 分
    assert g["reply_rate_60min"] == pytest.approx(round(2 / 3, 4))
    assert g["replied_any_time"] == 3                     # 参考行：三条之后都有用户消息
    assert g["reply_rate_any"] == 1.0


def test_group_by_intent_and_tier(tmp_path):
    """③：按 intent × tier 切分；只有一种 intent、tier 为空串时分组照样正确（不误合并）。"""
    client, factory = _client(tmp_path)
    t0 = (now_naive_utc() - timedelta(days=2)).replace(microsecond=0)
    metas = [
        _meta("check_in", "recent"), _meta("check_in", "recent"),
        _meta("check_in", "cold"),
        _meta("check_in", None),          # 只有 intent、无 tier → ("check_in", "")
        _meta("share_self", "recent"),
        None,                             # 老数据 / 其他发送通道 → ("", "")
    ]
    asyncio.run(_add_logs(factory, [
        _log(t0 + timedelta(minutes=i), m) for i, m in enumerate(metas)
    ]))

    body = client.get("/api/v1/scheduler/stats/outreach").json()
    assert body["window_minutes"] == 60 and body["days"] == 30
    assert {(g["intent"], g["tier"]): g["sent"] for g in body["groups"]} == {
        ("check_in", "recent"): 2, ("check_in", "cold"): 1, ("check_in", ""): 1,
        ("share_self", "recent"): 1, ("", ""): 1,
    }
    assert body["total_sent"] == 6
    # C1 每角色日均条数（窗口 30 天、共 6 条）
    per = body["per_character_daily"][0]
    assert per["character_id"] == CHAR and per["sent"] == 6
    assert per["avg_per_day"] == pytest.approx(round(6 / 30, 4))


def test_default_30_days_and_no_200_row_truncation(tmp_path):
    """④：默认 30 天窗口；250 条全部纳入（新聚合无「前 200 条」截断），窗口外不进表。"""
    client, factory = _client(tmp_path)
    base = (now_naive_utc() - timedelta(days=5)).replace(microsecond=0)
    logs = [
        _log(base + timedelta(minutes=i), _meta("check_in", "recent")) for i in range(250)
    ]
    logs.append(_log(base - timedelta(days=40), _meta("check_in", "recent")))  # 45 天前
    asyncio.run(_add_logs(factory, logs))
    asyncio.run(_add_user_replies(factory, [base + timedelta(minutes=i + 1) for i in range(250)]))

    body = client.get("/api/v1/scheduler/stats/outreach").json()
    assert body["days"] == 30
    assert body["total_sent"] == 250                       # 截断已不存在
    g = _group(body, "check_in", "recent")
    assert g["sent"] == 250 and g["scorable"] == 250
    assert g["replied_within_window"] == 250               # 每条都在 1 分钟后被接住
    assert g["reply_rate_60min"] == 1.0

    # days 可传参：放宽到 60 天才看见窗口外那条
    assert client.get(
        "/api/v1/scheduler/stats/outreach", params={"days": 60}
    ).json()["total_sent"] == 251


def test_old_stats_endpoint_reply_scan_limit(tmp_path):
    """④附带：旧端点默认仍只扫前 200 条（行为不变），传 reply_scan_limit=0 才放开。"""
    client, factory = _client(tmp_path)
    base = (now_naive_utc() - timedelta(days=2)).replace(microsecond=0)
    asyncio.run(_add_logs(factory, [
        _log(base + timedelta(minutes=i), message_type="proactive") for i in range(250)
    ]))
    asyncio.run(_add_user_replies(factory, [base + timedelta(minutes=i + 1) for i in range(250)]))

    default_body = client.get("/api/v1/scheduler/stats").json()
    assert default_body["total_sent"] == 250
    assert default_body["reply_rate"] == 0.8          # 200/250：旧截断原样保留

    full_body = client.get("/api/v1/scheduler/stats", params={"reply_scan_limit": 0}).json()
    assert full_body["reply_rate"] == 1.0             # 传 0＝不截断
