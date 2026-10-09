# -*- coding: utf-8 -*-
"""A40（A37 批 3）行为回归：非 arbiter 发送口上的闸③「发送前最后一眼」＋出口收敛。

钉四件事（对应审计 §3.1/§3.2/§3.3 与 §4 批 3）：
① 两档开关双登记、默认关；**两档都关＝一次额外查询都不发**（逐字节旧行为，I10）；
② 出口 `send_to_session` 的闸③排在**写库之前**：命中即不写 ChatMessage、不推 WS，返回
   `SendResult(False, "gate3_…")` 把「没发」交回调用方（I3）；没带快照只留痕、永不拦；
   出口**只判真人侧**（同会话里的 AI 消息不算冲突——剧情切片是同一事件按 3 秒节奏连发的）；
③ 四套发送口各自的「跳过＝回滚」闭环：切片保持 pending、通知提及不烧 30 分钟节流、
   群冒泡一行都不写；后台型豁免只到速率为止，新事件冲突照判（I11）；
④ 每一处都 fail-open：读不到现状＝照发／照落，但必须把读失败写进 INFO（I5）。

（项目未装 pytest-asyncio，统一 asyncio.run；临时库一律 tmp_path，绝不连生产库。）
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.flags.agent_flags import AGENT_FLAGS
from app.scheduling.scheduler import SendResult

SHADOW = "proactive_gate3_shadow"
ENFORCE = "proactive_gate3_enforce"
UID, CID, SID = 201, 202, 203
BASE = datetime(2026, 10, 10, 4, 0, 0)          # naive UTC＝北京 12:00（避开深夜闸）


# ── 夹具 ──────────────────────────────────────────────────────────────────────

def _factory(tmp_path, name="a40.db"):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(str(tmp_path), name).replace(chr(92), '/')}",
        poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all():
        import app.models  # noqa: F401  确保全部 ORM 进入 metadata
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    return factory


class _Cap:
    """自记账的 logger 替身（不依赖 caplog：各模块 logger 的 propagate 口径不确定）。"""

    def __init__(self):
        self.lines = []

    def _rec(self, fmt, args):
        try:
            self.lines.append(str(fmt) % args if args else str(fmt))
        except Exception:
            self.lines.append(str(fmt))

    def info(self, fmt, *a):
        self._rec(fmt, a)

    warning = error = exception = info

    def debug(self, fmt, *a):
        pass

    def text(self):
        return "\n".join(self.lines)

    def has(self, needle):
        return any(needle in line for line in self.lines)


@pytest.fixture()
def cap(monkeypatch):
    """把五个被测模块的 _logger 换成替身（断言只认「留痕有没有、写了什么」）。"""
    caps = {}
    for rel in ("app.scheduling.scheduler", "app.scheduling.arbiter",
                "app.application.phone_auto_notify_service",
                "app.scheduling.group_active", "app.scheduling.ai_social"):
        import importlib
        mod = importlib.import_module(rel)
        c = _Cap()
        caps[rel.split(".")[-1]] = c
        monkeypatch.setattr(mod, "_logger", c)
    return caps


def _flags(monkeypatch, shadow=False, enforce=False):
    monkeypatch.setitem(AGENT_FLAGS, SHADOW, shadow)
    monkeypatch.setitem(AGENT_FLAGS, ENFORCE, enforce)


def _async(value):
    async def _f(*_a, **_k):
        return value
    return _f


def _boom(msg="不该被调用"):
    """同步抛错的替身：既能当「一次都不该被调」的哨兵，也能当「一调就报错」的读失败模拟。"""
    def _f(*_a, **_k):
        raise AssertionError(msg)
    return _f


# ── ① 双登记与默认关 ──────────────────────────────────────────────────────────

def test_两档开关双登记_默认关():
    from app.application.flag_catalog import FLAG_CATALOG
    for key in (SHADOW, ENFORCE):
        assert key in AGENT_FLAGS, f"{key} 没进唯一真源"
        assert AGENT_FLAGS[key] is False, f"{key} 默认必须是关（I10 影子先行）"
        assert key in FLAG_CATALOG, f"{key} 漏了展示目录（硬口径：新增 flag 双登记）"
        assert FLAG_CATALOG[key]["group"] == "proactive"
        assert FLAG_CATALOG[key]["title_zh"] and FLAG_CATALOG[key]["title_en"]


def test_两档都关_出口一次复查都不发(monkeypatch, out, cap):
    from app.scheduling import scheduler
    _factory_, _pushed = out
    monkeypatch.setattr(scheduler, "gate3_conflict_reason", _boom())
    _flags(monkeypatch)
    res = asyncio.run(scheduler.send_to_session(
        SID, CID, UID, "在吗", message_type="storyline", snapshot_at=BASE))
    assert res.ok is True, "两档关时出口不该改变结局"
    assert cap["scheduler"].has("闸③") is False, "关档却留了闸③痕迹＝行为不再逐字节旧"


def test_两档都关_两个后台通道也零复查(monkeypatch, cap):
    from app.scheduling import ai_social, group_active
    monkeypatch.setattr(group_active, "async_session_factory", _boom("关档仍去查库"))
    monkeypatch.setattr(ai_social, "async_session_factory", _boom("关档仍去查库"))
    _flags(monkeypatch)
    assert asyncio.run(group_active._pre_land_conflict(1, 1, 0)) == ""
    assert asyncio.run(ai_social._pre_land_conflict(UID, 0, (1, 2))) == ""
    assert not cap["group_active"].has("读失败"), "关档不该走到查询"
    assert not cap["ai_social"].has("读失败")


# ── ② 出口闸③：位置、拦与不拦的边界 ───────────────────────────────────────────

def _seed_msg(factory, sender, created_at, session_id=SID):
    from app.models.chat import ChatMessage

    async def _do():
        async with factory() as db:
            db.add(ChatMessage(session_id=session_id, sender_type=sender,
                               content="x", created_at=created_at))
            await db.commit()

    asyncio.run(_do())


def _ai_rows(factory):
    from app.models.chat import ChatMessage

    async def _q():
        async with factory() as db:
            return (await db.execute(
                select(ChatMessage).where(ChatMessage.sender_type == "ai"))).scalars().all()

    return asyncio.run(_q())


@pytest.fixture()
def out(monkeypatch, tmp_path):
    """真临时库 + 打桩 WS/FCM：直发出口，返回 (factory, 已推送次数)。"""
    from app.scheduling import scheduler
    factory = _factory(tmp_path)
    monkeypatch.setattr(scheduler, "async_session_factory", factory)
    pushed = []

    async def _push(session_id, payload):
        pushed.append(session_id)
        return True

    monkeypatch.setattr("app.ws.connection_manager.push_to_session", _push)
    monkeypatch.setattr("app.application.push_service.notify_user", _async(None))
    return factory, pushed


def test_出口enforce_真人又说了话_不写库不推送(monkeypatch, out, cap):
    from app.scheduling import scheduler
    factory, pushed = out
    _seed_msg(factory, "user", BASE + timedelta(seconds=5))
    _flags(monkeypatch, enforce=True)
    res = asyncio.run(scheduler.send_to_session(
        SID, CID, UID, "下午那个会别忘了", message_type="storyline",
        snapshot_at=BASE, log_proactive=False))
    assert res.ok is False and res.reason == "gate3_new_user_msg"
    assert _ai_rows(factory) == [], "命中闸③仍写库＝「日志说拦了、库里写成已发」"
    assert pushed == [], "命中闸③还推了 WS"


def test_出口影子_照常落库并留痕(monkeypatch, out, cap):
    from app.scheduling import scheduler
    factory, pushed = out
    _seed_msg(factory, "user", BASE + timedelta(seconds=5))
    _flags(monkeypatch, shadow=True)
    res = asyncio.run(scheduler.send_to_session(
        SID, CID, UID, "下午那个会别忘了", message_type="storyline", snapshot_at=BASE))
    assert res.ok is True, "影子档只攒读数，不许改变发不发"
    assert len(_ai_rows(factory)) == 1 and pushed == [SID]
    assert "影子·照发" in cap["scheduler"].text()


def test_出口enforce_没有新发言_照发(monkeypatch, out, cap):
    from app.scheduling import scheduler
    factory, _pushed = out
    _seed_msg(factory, "user", BASE - timedelta(seconds=5))
    _flags(monkeypatch, enforce=True)
    res = asyncio.run(scheduler.send_to_session(
        SID, CID, UID, "在吗", message_type="storyline", snapshot_at=BASE))
    assert res.ok is True
    assert len(_ai_rows(factory)) == 1


def test_出口只判真人侧_AI消息不算冲突(monkeypatch, out):
    """审计 §3.1 的 (b) 刻意不在出口实现：剧情切片同事件连发，后一片必然看见前一片那条 AI 消息。"""
    from app.scheduling import scheduler
    factory, _ = out
    _seed_msg(factory, "ai", BASE + timedelta(seconds=5))
    _flags(monkeypatch, enforce=True)
    res = asyncio.run(scheduler.send_to_session(
        SID, CID, UID, "接着说", message_type="storyline", snapshot_at=BASE))
    assert res.ok is True, "出口把 AI 消息判成冲突＝同事件后续切片被当成双发（隐性收紧）"


def test_出口enforce_没带快照_只留痕永不拦(monkeypatch, out, cap):
    """没接进闸③的通道（不传 snapshot_at）开 enforce 也不该被拦：unknown 档位永不拦。"""
    from app.scheduling import scheduler
    factory, _ = out
    _seed_msg(factory, "user", BASE - timedelta(days=1))
    _flags(monkeypatch, enforce=True)
    res = asyncio.run(scheduler.send_to_session(
        SID, CID, UID, "在吗", message_type="holiday", holiday_name="节"))
    assert res.ok is True, "没这一眼＝看不见，按 fail-open 照发，不许变成不发"
    assert "unknown_snapshot" in cap["scheduler"].text()


def test_出口复检读失败_failopen照发并留痕(monkeypatch, cap):
    from app.scheduling import scheduler
    monkeypatch.setattr(scheduler, "async_session_factory", _boom("库读不动"))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(scheduler.gate3_conflict_reason(SID, BASE)) == ""
    assert "读失败照发" in cap["scheduler"].text()


def test_闸三排在写库之前_源码顺序钉():
    src = (os.path.join(os.path.dirname(__file__), "..", "app", "scheduling",
                        "scheduler.py"))
    with open(src, encoding="utf-8") as f:
        body = f.read()
    i_gate = body.index("_g3_shadow, _g3_enforce = gate3_flags()")
    i_write = body.index("db.add(msg)", i_gate)
    assert i_gate < i_write, "闸③排到写库之后＝拦不住任何一条消息"
    assert 'snapshot_at: "datetime | None" = None' in body, "快照参数默认值被改＝既有调用方语义变了"


# ── ③a arbiter 切片 flush：复检命中保持 pending ───────────────────────────────

CHAR, OWNER, SESSION = 301, 302, 303


def _seed_item(factory, send_at):
    from app.models.character import ProactiveStorylineItem

    async def _do():
        async with factory() as db:
            db.add(ProactiveStorylineItem(
                character_id=CHAR, session_id=SESSION, user_id=OWNER,
                group_id="g-a40", seq=0, content="在吗", send_at=send_at, status="pending"))
            await db.commit()

    asyncio.run(_do())


def _statuses(factory):
    from app.models.character import ProactiveStorylineItem

    async def _q():
        async with factory() as db:
            return (await db.execute(select(ProactiveStorylineItem.status))).scalars().all()

    return asyncio.run(_q())


@pytest.fixture()
def flush_env(monkeypatch, tmp_path):
    """时钟钉在中午 + 一条到期切片 + 出口替身（记录 kwargs），返回 (factory, 调用记录)。"""
    from app.scheduling import arbiter

    base = datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc)

    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

    monkeypatch.setattr(arbiter, "datetime", _FixedDT)
    factory = _factory(tmp_path, "flush.db")
    monkeypatch.setattr(arbiter, "async_session_factory", factory)
    _seed_item(factory, base.replace(tzinfo=None))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _async(False))
    calls = []

    async def _send(*_a, **_k):
        calls.append(_k)
        return SendResult(True, "sent")

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
    return factory, calls


def test_flushenforce_用户在活跃_切片保持pending(monkeypatch, flush_env, cap):
    from app.scheduling import arbiter
    factory, calls = flush_env
    monkeypatch.setattr(arbiter, "is_user_active", _async(True))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(arbiter.flush_storyline_items()) == 0
    assert calls == [], "复检命中仍调出口"
    assert _statuses(factory) == ["pending"], "命中却标 sent＝跳过即烧"


def test_flushenforce_小时额度用满_保持pending(monkeypatch, flush_env):
    from app.scheduling import arbiter
    factory, calls = flush_env
    monkeypatch.setattr(arbiter, "is_user_active", _async(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _async(arbiter.MAX_PER_HOUR))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(arbiter.flush_storyline_items()) == 0
    assert calls == [] and _statuses(factory) == ["pending"]


def test_flush影子_命中也照发并留痕(monkeypatch, flush_env, cap):
    from app.scheduling import arbiter
    factory, calls = flush_env
    monkeypatch.setattr(arbiter, "is_user_active", _async(True))
    _flags(monkeypatch, shadow=True)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1
    assert len(calls) == 1 and _statuses(factory) == ["sent"]
    assert "（影子）" in cap["arbiter"].text() and "user_active" in cap["arbiter"].text()


def test_flush复检读失败_照发留痕(monkeypatch, flush_env, cap):
    from app.scheduling import arbiter
    _factory_, calls = flush_env
    monkeypatch.setattr(arbiter, "is_user_active", _boom("查不动"))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _async(0))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1, "读不到现状却拦下＝越界收紧"
    assert "复检读失败照发" in cap["arbiter"].text()


def test_flush_两档关_复检函数一次都不调(monkeypatch, flush_env):
    from app.scheduling import arbiter
    _factory_, calls = flush_env
    monkeypatch.setattr(arbiter, "_flush_recheck_reason", _boom("关档仍复检"))
    _flags(monkeypatch)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1
    assert len(calls) == 1


def test_flush_把send_at作为快照交给出口(monkeypatch, flush_env):
    """审计 §3.2：send_at 就是天然快照时刻（不加列）。锚丢了，出口那一半复检就成了摆设。"""
    from app.scheduling import arbiter
    _factory_, calls = flush_env
    monkeypatch.setattr(arbiter, "is_user_active", _async(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _async(0))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(arbiter.flush_storyline_items()) == 1
    assert calls[0]["snapshot_at"] == datetime(2026, 10, 10, 4, 0)


def test_flush_出口说没发_切片不烧(monkeypatch, flush_env):
    """复检放行但出口拦下（gate3_*／主题熔断）：终态仍必须回到 pending。"""
    from app.scheduling import arbiter
    factory, calls = flush_env
    monkeypatch.setattr(arbiter, "is_user_active", _async(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _async(0))

    async def _blocked(*_a, **_k):
        calls.append(_k)
        return SendResult(False, "gate3_new_user_msg")

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _blocked)
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(arbiter.flush_storyline_items()) == 0
    assert _statuses(factory) == ["pending"]


# ── ③b 通知提及（C39）：发送前重查会话/用户，命中不烧节流 ──────────────────────

def _session_factory(tmp_path):
    """建一个「会话还在」的临时库：让复检走到第②步（用户活跃）/第③步（出口判据）。"""
    from app.models.chat import ChatSession
    from app.models.user import User
    factory = _factory(tmp_path, "phone.db")

    async def _seed():
        async with factory() as db:
            db.add(User(id=UID, username="u40", nickname="用户40"))
            await db.flush()
            db.add(ChatSession(id=SID, user_id=UID, character_id=CID))
            await db.commit()

    asyncio.run(_seed())
    return factory


def _phone_env(monkeypatch, factory, cap):
    from app.application import phone_auto_notify_service as svc
    from app.scheduling import arbiter, scheduler
    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "_select_character",
                        _async((type("C", (), {"id": CID, "name": "小爱"})(),
                                type("S", (), {"id": SID})(), SID)))
    monkeypatch.setattr(svc, "_kernel_gate_reason", _async(None))
    monkeypatch.setattr(svc, "_generate_mention", _async("下午那个会别忘了"))
    rejected = []

    async def _rej(char_id, user_id, reason, gate=""):
        rejected.append({"reason": reason, "gate": gate})

    monkeypatch.setattr(svc, "_log_rejected", _rej)
    monkeypatch.setattr(arbiter, "is_user_active", _async(False))
    monkeypatch.setattr(scheduler, "gate3_conflict_reason", _async(""))
    sent = []

    async def _send(*_a, **_k):
        sent.append(_k)
        return SendResult(True, "sent")

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
    return SimpleNamespace(sent=sent, rejected=rejected, svc=svc)


def test_通知enforce_会话没了_不发并留痕(monkeypatch, tmp_path, cap):
    env = _phone_env(monkeypatch, _factory(tmp_path, "phone.db"), cap)
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(env.svc._trigger_mention(UID, [{"title": "t", "text": "x"}])) is False
    assert env.sent == []
    assert env.rejected and env.rejected[-1]["gate"] == "session_gone"
    assert "[gate3=session_gone]" in env.rejected[-1]["reason"]


def test_通知enforce_用户正在活跃_不发(monkeypatch, tmp_path, cap):
    from app.scheduling import arbiter
    env = _phone_env(monkeypatch, _session_factory(tmp_path), cap)
    monkeypatch.setattr(arbiter, "is_user_active", _async(True))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(env.svc._trigger_mention(UID, [{"title": "t", "text": "x"}])) is False
    assert env.sent == [] and env.rejected[-1]["gate"] == "user_active"


def test_通知影子_照发并留痕(monkeypatch, tmp_path, cap):
    from app.scheduling import arbiter
    env = _phone_env(monkeypatch, _session_factory(tmp_path), cap)
    monkeypatch.setattr(arbiter, "is_user_active", _async(True))
    _flags(monkeypatch, shadow=True)
    assert asyncio.run(env.svc._trigger_mention(UID, [{"title": "t", "text": "x"}])) is True
    assert len(env.sent) == 1 and env.rejected == []
    assert "（影子）" in cap["phone_auto_notify_service"].text()


def test_通知enforce_真人插话_出口拦下后不烧30分钟节流(monkeypatch, tmp_path, cap):
    """I3 端到端：被闸③拦下 ⇒ handle_auto_report 报 triggered=False，last_trigger_at 不回填。"""
    from app.models.chat import ChatMessage, ChatSession
    from app.models.device import PhoneAutoState
    from app.models.user import User
    factory = _factory(tmp_path, "phone.db")

    async def _seed():
        async with factory() as db:
            db.add(User(id=UID, username="u40", nickname="用户40"))
            await db.flush()
            db.add(ChatSession(id=SID, user_id=UID, character_id=CID))
            db.add(ChatMessage(session_id=SID, sender_type="user", content="我刚说完了",
                               created_at=BASE + timedelta(seconds=5)))
            db.add(PhoneAutoState(id=1, user_id=UID, fingerprints='["old-fp"]',
                                  last_trigger_at=None))
            await db.commit()

    asyncio.run(_seed())
    env = _phone_env(monkeypatch, factory, cap)
    from app.scheduling import scheduler
    monkeypatch.setattr(scheduler, "gate3_conflict_reason", _async("new_user_msg"))

    async def _send(*_a, **_k):
        env.sent.append(_k)
        return SendResult(True, "sent")

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
    _flags(monkeypatch, enforce=True)
    out = asyncio.run(env.svc.handle_auto_report(
        UID, [{"package": "p", "title": "新通知", "text": "body"}]))
    assert out["triggered"] is False, "没发出去却向客户端报「已打扰」"
    assert env.sent == [] and env.rejected[-1]["gate"] == "new_user_msg"

    async def _q():
        async with factory() as db:
            st = await db.get(PhoneAutoState, 1)
            return st.last_trigger_at

    assert asyncio.run(_q()) is None, "被拦仍回填 last_trigger_at＝白烧 30 分钟节流"


def test_通知_出口说没发_同样不烧节流(monkeypatch, tmp_path, cap):
    """复检放行、但出口自己拦下（返回 gate3_*）：上层一样不许写「已打扰」。"""
    from app.models.chat import ChatSession
    from app.models.device import PhoneAutoState
    from app.models.user import User
    factory = _factory(tmp_path, "phone.db")

    async def _seed():
        async with factory() as db:
            db.add(User(id=UID, username="u40", nickname="用户40"))
            await db.flush()
            db.add(ChatSession(id=SID, user_id=UID, character_id=CID))
            db.add(PhoneAutoState(id=1, user_id=UID, fingerprints='["old-fp"]',
                                  last_trigger_at=None))
            await db.commit()

    asyncio.run(_seed())
    env = _phone_env(monkeypatch, factory, cap)

    async def _send(*_a, **_k):
        env.sent.append(_k)
        return SendResult(False, "gate3_new_user_msg")

    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)
    _flags(monkeypatch, enforce=True)
    out = asyncio.run(env.svc.handle_auto_report(
        UID, [{"package": "p", "title": "新通知", "text": "body"}]))
    assert out["triggered"] is False

    async def _q():
        async with factory() as db:
            st = await db.get(PhoneAutoState, 1)
            return st.last_trigger_at

    assert asyncio.run(_q()) is None, "出口说没发，节流却又烧了一次"


def test_通知复检读失败_照发留痕(monkeypatch, tmp_path, cap):
    from app.scheduling import arbiter
    env = _phone_env(monkeypatch, _session_factory(tmp_path), cap)
    monkeypatch.setattr(arbiter, "is_user_active", _boom("查不动"))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(env.svc._trigger_mention(UID, [{"title": "t", "text": "x"}])) is True
    assert len(env.sent) == 1, "读不到现状却不发＝越界收紧"
    assert "读失败照发" in cap["phone_auto_notify_service"].text()


def test_通知快照在生成之前取_且出口结局被消费():
    src = _read("app/application/phone_auto_notify_service.py")
    i_snap = src.index("_snap_at = now_naive_utc()")
    i_gen = src.index("content = await _generate_mention(")
    assert i_snap < i_gen, "快照取在生成之后＝判不出「生成这段时间」里的新发言"
    i_send = src.index("_res = await engine.send_to_session(", i_gen)
    i_check = src.index('getattr(_res, "ok", None) is False', i_send)
    assert i_send < i_check, "出口返回值又没被消费（I3 复发）"


def _read(rel):
    with open(os.path.join(os.path.dirname(__file__), "..", *rel.split("/")),
              encoding="utf-8") as f:
        return f.read()


# ── ③c 后台型落库前：豁免只到速率为止（I11） ─────────────────────────────────

def _group_factory(tmp_path):
    from app.models.chat import ChatGroupMessage
    factory = _factory(tmp_path, "group.db")

    async def _seed():
        async with factory() as db:
            db.add(ChatGroupMessage(group_id=1, sender_type="ai", character_id=CID,
                                    content="上一条"))
            await db.commit()

    asyncio.run(_seed())
    return factory


def _group_last_id(factory):
    from app.models.chat import ChatGroupMessage

    async def _q():
        async with factory() as db:
            return (await db.execute(select(ChatGroupMessage.id).order_by(
                ChatGroupMessage.id.desc()).limit(1))).scalars().first()

    return asyncio.run(_q())


def test_群冒泡_期间有真人发言_enforce返回原因影子只留痕(monkeypatch, tmp_path, cap):
    from app.scheduling import group_active
    factory = _group_factory(tmp_path)
    monkeypatch.setattr(group_active, "async_session_factory", factory)
    anchor = _group_last_id(factory)

    async def _new_human():
        from app.models.chat import ChatGroupMessage
        async with factory() as db:
            db.add(ChatGroupMessage(group_id=1, sender_type="user", content="用户插话"))
            await db.commit()

    asyncio.run(_new_human())
    _flags(monkeypatch, shadow=True)
    assert asyncio.run(group_active._pre_land_conflict(1, CID, anchor)) == ""
    assert "（影子）" in cap["group_active"].text() and "human_spoke" in cap["group_active"].text()
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(group_active._pre_land_conflict(1, CID, anchor)) == "human_spoke"


def test_群冒泡enforce命中_一行都不写(monkeypatch, tmp_path, cap):
    """命中后的「消费标记」就是那两条 add：不落地＝不留痕（I3）。"""
    from app.models.character import AICharacter, ProactiveMessageLog
    from app.models.chat import ChatGroupMessage
    from app.scheduling import group_active
    factory = _factory(tmp_path, "group.db")

    async def _seed():
        async with factory() as db:
            db.add(AICharacter(id=CID, user_id=UID, name="小爱"))
            await db.commit()

    asyncio.run(_seed())
    monkeypatch.setattr(group_active, "async_session_factory", factory)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _async("你们最近怎么样"))
    monkeypatch.setattr(group_active, "_pre_land_conflict", _async("human_spoke"))
    assert asyncio.run(group_active.run_group_active(CID, 1, UID)) is False

    async def _q():
        async with factory() as db:
            return ((await db.execute(select(ChatGroupMessage))).scalars().all(),
                    (await db.execute(select(ProactiveMessageLog))).scalars().all())

    msgs, logs = asyncio.run(_q())
    assert msgs == [] and logs == [], "没落地却写了群消息/已发送留痕"


def test_群冒泡无冲突_照常落地_正向对照(monkeypatch, tmp_path):
    from app.models.character import AICharacter
    from app.models.chat import ChatGroupMessage
    from app.scheduling import group_active
    factory = _factory(tmp_path, "group.db")

    async def _seed():
        async with factory() as db:
            db.add(AICharacter(id=CID, user_id=UID, name="小爱"))
            await db.commit()

    asyncio.run(_seed())
    monkeypatch.setattr(group_active, "async_session_factory", factory)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _async("你们最近怎么样"))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(group_active.run_group_active(CID, 1, UID)) is True

    async def _q():
        async with factory() as db:
            return (await db.execute(select(ChatGroupMessage))).scalars().all()

    assert len(asyncio.run(_q())) == 1


def test_群冒泡复检读失败_照落留痕(monkeypatch, cap):
    from app.scheduling import group_active
    monkeypatch.setattr(group_active, "async_session_factory", _boom("库读不动"))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(group_active._pre_land_conflict(1, CID, 0)) == ""
    assert "读失败照落" in cap["group_active"].text()


def _social_factory(tmp_path):
    from app.models.chat import ChatMessage, ChatSession
    from app.models.user import User
    factory = _factory(tmp_path, "social.db")

    async def _seed():
        async with factory() as db:
            db.add(User(id=UID, username="u40", nickname="用户40"))
            await db.flush()
            db.add(ChatSession(id=SID, user_id=UID, character_id=CID))
            db.add(ChatMessage(session_id=SID, sender_type="user", content="早",
                               created_at=BASE))
            await db.commit()

    asyncio.run(_seed())
    return factory


def test_AI互聊_锚之后真人又说话_enforce命中影子留痕(monkeypatch, tmp_path, cap):
    from app.models.chat import ChatMessage
    from app.scheduling import ai_social
    factory = _social_factory(tmp_path)
    monkeypatch.setattr(ai_social, "async_session_factory", factory)
    anchor = _max_msg_id(factory)
    _flags(monkeypatch, shadow=True)
    assert asyncio.run(ai_social._pre_land_conflict(UID, anchor, (1, 2))) == ""
    assert not cap["ai_social"].has("闸③"), "无新发言时不该留痕"

    async def _new_human():
        async with factory() as db:
            db.add(ChatMessage(session_id=SID, sender_type="user", content="我又说了",
                               created_at=BASE + timedelta(seconds=9)))
            await db.commit()

    asyncio.run(_new_human())
    assert asyncio.run(ai_social._pre_land_conflict(UID, anchor, (1, 2))) == ""
    assert "（影子）" in cap["ai_social"].text()
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(ai_social._pre_land_conflict(UID, anchor, (1, 2))) == "human_spoke"


def _max_msg_id(factory):
    from app.models.chat import ChatMessage

    async def _q():
        async with factory() as db:
            return (await db.execute(select(ChatMessage.id).order_by(
                ChatMessage.id.desc()).limit(1))).scalars().first()

    return asyncio.run(_q())


def test_AI互聊_锚未知时照落_不许退化成假锚(monkeypatch, tmp_path, cap):
    """读锚失败＝None。若退化成 0/-1，会把「全部历史」当成新发言（越界收紧）。"""
    from app.scheduling import ai_social
    factory = _social_factory(tmp_path)
    monkeypatch.setattr(ai_social, "async_session_factory", factory)
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(ai_social._pre_land_conflict(UID, None, (1, 2))) == ""
    assert "现状锚未知" in cap["ai_social"].text()
    src = _read("app/scheduling/ai_social.py")
    assert "_anchor_id = None" in src, "锚读失败被改成假锚了？"


def test_AI互聊_复检读失败_照落留痕(monkeypatch, cap):
    from app.scheduling import ai_social
    monkeypatch.setattr(ai_social, "_human_spoke_since", _boom("库读不动"))
    _flags(monkeypatch, enforce=True)
    assert asyncio.run(ai_social._pre_land_conflict(UID, 5, (1, 2))) == ""
    assert "读失败照落" in cap["ai_social"].text()


def test_AI互聊_复检排在落库之前_源码顺序钉():
    src = _read("app/scheduling/ai_social.py")
    i_gate = src.index("if await _pre_land_conflict(")
    i_write = src.index("db.add_all(rows)", i_gate)
    assert src.index("_anchor_id = await _user_last_msg_id(") < i_gate < i_write, \
        "锚/复检排到写库之后＝这一眼永远看不见生成期间的变化"
    gsrc = _read("app/scheduling/group_active.py")
    assert gsrc.index("if await _pre_land_conflict(") < gsrc.index("db.add(ChatGroupMessage("), \
        "群冒泡复检跑到 add 之后了"
