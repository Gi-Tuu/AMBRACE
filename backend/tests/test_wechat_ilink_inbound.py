# -*- coding: utf-8 -*-
"""wechat_ilink PR3 入站收发闭环测试（§11.1 / 交接测试清单）。

覆盖：
1. 配额闸门（§8.4）：窗口初始化=0 / 入站重置 / 连续 N 次放行第 N+1 次拒绝 / 超 24h 拒绝主动 /
   回复占 1 条 / 入站后满额恢复（§11.1#1）。
2. 幂等/游标（§8.3 + P0-3）：同 ilink_msg_id 只处理一次；游标推进/续拉不丢不重（§11.1#2）。
3. 入站解析健壮性（PR1 已测，此处补消费层边界）。
4. 降级（§8.4）：配额耗尽入站回复 status=deferred 且不发微信（§11.1#6）。
5. 整段发送（P1-1）：长/流式文本被合并为恰好 1 次 send_text（§11.1#9）。
6. 异常隔离（P0-5）：ILinkClient 抛错 schedule_tick 不抛、其它正常；重入锁防并发（§11.1#7）。
7. 主链路调用点核对：create_session + send_and_receive 真实签名接线（只读，不改内核）。
8. live 用例默认跳过（NEEDS_RUNTIME_VERIFICATION，ILINK_RUN_LIVE=1 才跑）。
"""
import asyncio
import importlib.util
import os
import pathlib
import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.plugins import registry
from app.providers import registry as prov_reg

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_SECRET_KEY = "wechat-ilink-test-secret-000000000000000000000001"
_RUN_LIVE = os.environ.get("ILINK_RUN_LIVE") == "1"


def _plugin_mod(name: str):
    """取插件模块（models/inbound/quota/port）。

    插件 main.py 不逐个 import 全部子模块（如 quota 只在 poll_once 内惰性 import），
    故先经 sys.modules 取，未加载时回退到「插件目录在 sys.path / 按路径装载」。
    """
    if name in sys.modules:
        return sys.modules[name]
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    try:
        __import__(name)
    except ModuleNotFoundError:
        spec = importlib.util.spec_from_file_location("dsh_wechat_" + name, _PLUGIN_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return sys.modules[name]


def _load_plugin():
    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")


@pytest.fixture()
def wc_plugin():
    _load_plugin()
    yield
    prov_reg.unregister_providers_for_source("wechat_ilink")
    registry._loaded.pop("wechat_ilink", None)
    registry._db_config.pop("wechat_ilink", None)
    registry._enabled.pop("wechat_ilink", None)


@pytest.fixture()
def wc_db(monkeypatch, tmp_path, wc_plugin):
    """临时 SQLite：建全表 + patch 轻状态文件路径 + 清理重入锁/节流时间戳。"""
    monkeypatch.setenv("AMBRACE_SECRET_KEY", _SECRET_KEY)
    monkeypatch.setattr(sys, "path", sys.path)  # noqa: B009 - 保持现状（插件 path 由 main.py 插入）
    db_path = os.path.join(tmp_path, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    inbound = _plugin_mod("inbound")
    monkeypatch.setattr(inbound, "_STATE_FILE", pathlib.Path(tmp_path) / "wechat_ilink_state.json")
    monkeypatch.setattr(inbound, "_STATE_DIR", pathlib.Path(tmp_path))
    inbound._reset_locks()
    # _run_companion_reply(P3-5) 读 User.lang 走全局 async_session_factory，须让其落到临时库（勿碰生产库）
    import app.db.database as db_mod  # noqa: PLC0415
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


# ------------------------------------------------------------------ 轻量 binding 替身（配额单测）
class _Binding:
    """duck-typed binding：只暴露 QuotaGate 需要的字段。"""

    def __init__(self):
        self.window_started_at = None
        self.out_count_in_window = 0
        self.last_inbound_at = None


def _quota():
    return _plugin_mod("quota")


def _inbound():
    return _plugin_mod("inbound")


# ------------------------------------------------------------------ mock 客户端
class FakeClient:
    """具备 get_updates / send_text 的可录制客户端。"""

    def __init__(self):
        self.get_updates_calls: list[str | None] = []
        self.send_text_calls: list[tuple[str, str | None]] = []
        self.updates_responses: list[dict] = []
        self.send_result: dict = {"ok": True}

    async def get_updates(self, buf):
        self.get_updates_calls.append(buf)
        if self.updates_responses:
            return self.updates_responses.pop(0)
        return {"ok": True, "messages": [], "buf": buf}

    async def send_text(self, text, context_token=None):
        self.send_text_calls.append((text, context_token))
        return self.send_result


def _client_factory(*, updates: list[dict] | None = None, send_result: dict | None = None,
                    boom: bool = False):
    """返回 (factory, clients)；factory(*args) 每调用产出一个 FakeClient 并登记。

    boom=True 时首个产出的客户端 get_updates 立即抛错（模拟 iLink 宕机）。
    """
    clients: list[FakeClient] = []

    def factory(*_a, **_k):
        if boom and not clients:
            c = _BoomClient()
        else:
            c = FakeClient()
            if updates is not None:
                c.updates_responses = list(updates)
            if send_result is not None:
                c.send_result = send_result
        clients.append(c)
        return c

    return factory, clients


class _BoomClient(FakeClient):
    async def get_updates(self, buf):
        raise RuntimeError("ilink down")


def _fake_reply(text):
    async def _f(*_a, **_k):
        return text
    return _f


# 在临时库上原地改写 binding 配额状态（供降级/发送用例构造边界）
def _overwrite_binding(factory, binding_id, *, used, now_active=True):
    models = _plugin_mod("models")
    from datetime import datetime

    async def _do():
        async with factory() as db:
            row = await db.get(models.WeChatILinkBinding, binding_id)
            if now_active:
                row.window_started_at = datetime.now()
            row.out_count_in_window = used
            await db.commit()
    asyncio.run(_do())


async def _add_binding(factory, *, user_id=1, character_id=101, ilink_user_id="u1",
                       bot_token_enc="enc", baseurl="https://x.weixin.qq.com", enabled=True, poll_buf=""):
    models = _plugin_mod("models")
    async with factory() as db:
        b = models.WeChatILinkBinding(
            user_id=user_id, character_id=character_id, ilink_user_id=ilink_user_id,
            bot_token_enc=bot_token_enc, baseurl=baseurl, enabled=enabled, poll_buf=poll_buf,
        )
        db.add(b)
        await db.commit()
        await db.refresh(b)
        return b.id


async def _messages(factory, binding_id, direction):
    models = _plugin_mod("models")
    async with factory() as db:
        q = select(models.WeChatILinkMessage).where(models.WeChatILinkMessage.binding_id == binding_id)
        if direction:
            q = q.where(models.WeChatILinkMessage.direction == direction)
        return (await db.execute(q)).scalars().all()


async def _binding_row(factory, bid):
    models = _plugin_mod("models")
    async with factory() as db:
        return await db.get(models.WeChatILinkBinding, bid)


# ================================================================== 1. 配额闸门（§8.4，纯逻辑）
def test_quota_no_window_rejects_by_default():
    d = _quota().QuotaGate(10).can_acquire(_Binding())
    assert d.allowed is False and d.reason == "no_active_window" and d.remaining == 0


def test_quota_inbound_resets_window():
    b, g = _Binding(), _quota().QuotaGate(10)
    g.on_inbound(b)
    assert b.window_started_at is not None
    assert b.out_count_in_window == 0
    assert g.can_acquire(b).allowed is True


def test_quota_allows_up_to_n_then_rejects():
    b, g = _Binding(), _quota().QuotaGate(10)
    g.on_inbound(b)
    assert all(g.acquire(b).allowed for _ in range(10))
    d = g.acquire(b)
    assert d.allowed is False and d.reason == "exhausted" and b.out_count_in_window == 10


def test_quota_reply_counts_as_one():
    b, g = _Binding(), _quota().QuotaGate(10)
    g.on_inbound(b)
    d = g.acquire(b)  # 回复本身占 1 条
    assert d.allowed is True and d.remaining == 9 and b.out_count_in_window == 1


def test_quota_expired_window_rejects_proactive():
    from datetime import datetime, timedelta
    b, g = _Binding(), _quota().QuotaGate(10)
    g.on_inbound(b)
    d = g.can_acquire(b, now=datetime.now() + _quota().WINDOW + timedelta(seconds=1))
    assert d.allowed is False and d.reason == "no_active_window"


def test_quota_new_inbound_after_expiry_restores_full():
    from datetime import datetime, timedelta
    b, g = _Binding(), _quota().QuotaGate(10)
    g.on_inbound(b)
    assert g.can_acquire(b, now=datetime.now() + _quota().WINDOW + timedelta(seconds=1)).allowed is False
    g.on_inbound(b)
    assert g.can_acquire(b).allowed is True and g.can_acquire(b).remaining == 10


def test_quota_min_quota_is_one():
    g = _quota().QuotaGate(0)
    assert g.n == 1
    b = _Binding()
    g.on_inbound(b)
    assert g.acquire(b).allowed is True and g.acquire(b).allowed is False


def test_quota_remaining_reported_after_acquire():
    b, g = _Binding(), _quota().QuotaGate(5)
    g.on_inbound(b)
    assert g.acquire(b).remaining == 4 and g.acquire(b).remaining == 3


# ================================================================== 2/3. 消费层：幂等 + 解析边界
def test_process_inbound_resets_window_and_records_message(wc_db, monkeypatch):
    inbound = _inbound()
    gate = _quota().QuotaGate(10)
    bid = asyncio.run(_add_binding(wc_db))
    monkeypatch.setattr(inbound, "_run_companion_reply", _fake_reply("hi"))
    factory, clients = _client_factory()

    ok = asyncio.run(inbound._process_inbound(
        bid, inbound._std_inbound({"msg_id": "m1", "content": "你好", "context_token": "c1"}),
        gate, factory, wc_db))
    assert ok is True
    ins = asyncio.run(_messages(wc_db, bid, "in"))
    assert len(ins) == 1 and ins[0].content == "你好"
    outs = asyncio.run(_messages(wc_db, bid, "out"))
    assert len(outs) == 1 and outs[0].status == "ok" and outs[0].quota_charged is True
    assert len(clients) == 1 and clients[0].send_text_calls == [("hi", "c1")]
    row = asyncio.run(_binding_row(wc_db, bid))
    assert row.out_count_in_window == 1


def test_process_inbound_idempotent_same_msg_id(wc_db, monkeypatch):
    inbound = _inbound()
    gate = _quota().QuotaGate(10)
    bid = asyncio.run(_add_binding(wc_db))
    monkeypatch.setattr(inbound, "_run_companion_reply", _fake_reply("hi"))
    factory, clients = _client_factory()
    in1 = inbound._std_inbound({"msg_id": "m1", "content": "你好"})

    async def _run():
        await inbound._process_inbound(bid, in1, gate, factory, wc_db)
        return await inbound._process_inbound(bid, in1, gate, factory, wc_db)

    assert asyncio.run(_run()) is False
    assert len(asyncio.run(_messages(wc_db, bid, "in"))) == 1
    assert len(asyncio.run(_messages(wc_db, bid, "out"))) == 1
    assert len(clients) == 1


def test_process_inbound_non_text_skipped(wc_db):
    inbound = _inbound()
    gate = _quota().QuotaGate(10)
    bid = asyncio.run(_add_binding(wc_db))
    factory, clients = _client_factory()
    inb = inbound._std_inbound({"msg_id": "m2", "msg_type": "image", "content": "https://x/1.png"})

    assert asyncio.run(inbound._process_inbound(bid, inb, gate, factory, wc_db)) is False
    assert asyncio.run(_messages(wc_db, bid, None)) == []
    assert clients == []


def test_process_inbound_empty_text_skipped(wc_db):
    inbound = _inbound()
    gate = _quota().QuotaGate(10)
    bid = asyncio.run(_add_binding(wc_db))
    factory, clients = _client_factory()
    inb = inbound._std_inbound({"msg_id": "m3", "content": "   ", "msg_type": "text"})

    assert asyncio.run(inbound._process_inbound(bid, inb, gate, factory, wc_db)) is False
    assert asyncio.run(_messages(wc_db, bid, None)) == []


# ================================================================== 4. 降级 / 5. 整段发送
def test_process_reply_deferred_when_quota_exhausted(wc_db):
    """§11.1#6：配额耗尽 → 回复仍落 App（status=deferred），不发微信。"""
    inbound = _inbound()
    gate = _quota().QuotaGate(10)
    bid = asyncio.run(_add_binding(wc_db))
    _overwrite_binding(wc_db, bid, used=10, now_active=True)
    factory, clients = _client_factory()
    inb = inbound._std_inbound({"msg_id": "m4", "content": "在吗", "context_token": "c4"})

    asyncio.run(inbound._process_reply(bid, inb, "这里是回复", gate, factory, wc_db))
    outs = asyncio.run(_messages(wc_db, bid, "out"))
    assert len(outs) == 1 and outs[0].status == "deferred" and outs[0].quota_charged is False
    assert clients == []


def test_process_reply_sends_exactly_once_for_long_text(wc_db):
    """§11.1#9：长/流式文本被合并为恰好 1 次 send_text（P1-1 严禁逐token/气泡）。"""
    inbound = _inbound()
    gate = _quota().QuotaGate(10)
    bid = asyncio.run(_add_binding(wc_db))
    _overwrite_binding(wc_db, bid, used=0, now_active=True)
    factory, clients = _client_factory()
    long_reply = "这是一段很长很长的回复。" * 200
    inb = inbound._std_inbound({"msg_id": "m5", "content": "讲个故事"})

    asyncio.run(inbound._process_reply(bid, inb, long_reply, gate, factory, wc_db))
    outs = asyncio.run(_messages(wc_db, bid, "out"))
    assert len(outs) == 1 and outs[0].status == "ok" and outs[0].quota_charged is True
    assert len(clients) == 1
    assert clients[0].send_text_calls == [(long_reply, None)]  # 恰好 1 次、整段、无拆分


def test_process_reply_failed_send_not_charged(wc_db):
    """发送失败（返回非 ok）→ out status=failed、quota_charged=False（不浪费额度）。"""
    inbound = _inbound()
    gate = _quota().QuotaGate(10)
    bid = asyncio.run(_add_binding(wc_db))
    _overwrite_binding(wc_db, bid, used=0, now_active=True)
    factory, clients = _client_factory(send_result={"ok": False, "kind": "http"})
    inb = inbound._std_inbound({"msg_id": "m6", "content": "嗨"})

    asyncio.run(inbound._process_reply(bid, inb, "回复哦", gate, factory, wc_db))
    outs = asyncio.run(_messages(wc_db, bid, "out"))
    assert len(outs) == 1 and outs[0].status == "failed" and outs[0].quota_charged is False
    row = asyncio.run(_binding_row(wc_db, bid))
    assert row.out_count_in_window == 0


# ================================================================== 游标 / poll_once
def test_poll_once_resumes_and_persists_cursor(wc_db, monkeypatch):
    """§11.1#2：游标推进 + 续拉不丢不重；DB poll_buf 与轻状态文件都持久化。"""
    inbound = _inbound()
    bid = asyncio.run(_add_binding(wc_db))
    monkeypatch.setattr(inbound, "_run_companion_reply", _fake_reply("hi"))
    factory, clients = _client_factory(updates=[
        {"ok": True, "messages": [{"msg_id": "p1", "content": "a"}, {"msg_id": "p2", "content": "b"}], "buf": "cur-2"},
        {"ok": True, "messages": [], "buf": "cur-2"},
    ])

    asyncio.run(inbound.poll_once(client_factory=factory, interval=30, long_poll=25, quota=10,
                                  session_factory=wc_db))
    ins = asyncio.run(_messages(wc_db, bid, "in"))
    assert len(ins) == 2
    row = asyncio.run(_binding_row(wc_db, bid))
    assert row.poll_buf == "cur-2"
    assert inbound._read_cursor(bid) == "cur-2"


def test_poll_once_resumes_from_state_file_when_db_empty(wc_db):
    """DB poll_buf 为空但轻状态文件有游标 → 从 state 文件续拉（兜底）。"""
    inbound = _inbound()
    bid = asyncio.run(_add_binding(wc_db, poll_buf=""))
    inbound._save_cursor(bid, "state-cur")
    factory, clients = _client_factory()

    asyncio.run(inbound.poll_once(client_factory=factory, interval=30, long_poll=25, quota=10,
                                  session_factory=wc_db))
    assert clients[0].get_updates_calls == ["state-cur"]


def test_poll_once_skips_locked_binding(wc_db):
    """§11.1#7：重入锁——上一轮未跑完的 binding 本轮跳过（不重入并发）。"""
    inbound = _inbound()
    bid = asyncio.run(_add_binding(wc_db))
    factory, clients = _client_factory(updates=[
        {"ok": True, "messages": [{"msg_id": "x", "content": "hello"}], "buf": "c1"},
    ])

    async def _hold_lock():
        lock = asyncio.Lock()
        await lock.acquire()
        inbound._locks[bid] = lock
        return lock

    lock = asyncio.run(_hold_lock())
    try:
        asyncio.run(inbound.poll_once(client_factory=factory, interval=30, long_poll=25,
                                      quota=10, session_factory=wc_db))
        assert clients == []
    finally:
        lock.release()


def test_poll_once_exception_isolated_per_binding(wc_db, monkeypatch):
    """§11.1#7：ILinkClient 抛错时 poll_once 不抛；其它 binding 仍正常处理。"""
    inbound = _inbound()
    bid_a = asyncio.run(_add_binding(wc_db, character_id=101, ilink_user_id="ua"))
    bid_b = asyncio.run(_add_binding(wc_db, character_id=102, ilink_user_id="ub"))
    monkeypatch.setattr(inbound, "_run_companion_reply", _fake_reply("hi"))

    created = []
    def factory(*_a, **_k):
        if not created:
            c = _BoomClient()  # 第一个 binding：get_updates 抛错
        else:
            c = FakeClient()
            c.updates_responses = [{"ok": True, "messages": [{"msg_id": "b1", "content": "bb"}], "buf": "b-cur"}]
        created.append(c)
        return c

    asyncio.run(inbound.poll_once(client_factory=factory, interval=30, long_poll=25, quota=10,
                                  session_factory=wc_db))
    # 抛错的 binding 被隔离，另一个 binding 的消息仍被处理（合计恰好 1 条，顺序无关）
    total = (len(asyncio.run(_messages(wc_db, bid_a, "in")))
             + len(asyncio.run(_messages(wc_db, bid_b, "in"))))
    assert total == 1


# ================================================================== 7. 主链路调用点核对（只读不改内核）
def test_run_companion_reply_calls_send_and_receive(wc_db, monkeypatch):
    """真实签名接线核对：create_session → send_and_receive(session_id,user_id,character_id,content)。"""
    inbound = _inbound()
    calls = {}

    async def _fake_create_session(uid, cid):
        calls["create"] = (uid, cid)
        return {"id": 777, "character_id": cid, "greeting": None}

    async def _fake_send_and_receive(**kw):
        calls["send_and_receive"] = kw
        return {"ai_message": {"content": "从主链路来的整段回复"}, "memories_updated": True}

    import app.application.chat_service as cs
    monkeypatch.setattr(cs, "create_session", _fake_create_session)
    monkeypatch.setattr(cs, "send_and_receive", _fake_send_and_receive)

    out = asyncio.run(inbound._run_companion_reply(9, 101, "测试入站"))
    assert calls["create"] == (9, 101)
    assert calls["send_and_receive"]["session_id"] == 777
    assert calls["send_and_receive"]["user_id"] == 9
    assert calls["send_and_receive"]["character_id"] == 101
    assert calls["send_and_receive"]["content"] == "测试入站"
    assert calls["send_and_receive"]["channel"] == "wechat_ilink"  # 任务 A：渠道来源标记
    assert out == "从主链路来的整段回复"


def test_run_companion_reply_isolates_main_loop_error(wc_db, monkeypatch):
    """P0-5：主链路抛错 → _run_companion_reply 记日志返回空回复，绝不上抛。"""
    inbound = _inbound()

    async def _boom(*_a, **_k):
        raise RuntimeError("llm down")

    import app.application.chat_service as cs
    monkeypatch.setattr(cs, "create_session", _boom)
    out = asyncio.run(inbound._run_companion_reply(1, 1, "x"))
    assert out == ""


def test_run_companion_reply_lang_from_user_en(wc_db, monkeypatch):
    """P3-5：回复语言来自绑定所属 User.lang=en（读库接线后传入 send_and_receive）。"""
    from app.models.user import User  # noqa: PLC0415
    inbound = _inbound()
    calls = {}

    async def _seed():
        async with wc_db() as db:
            db.add(User(id=55, username="u55", nickname="n55", lang="en"))
            await db.commit()
    asyncio.run(_seed())

    async def _fake_create_session(uid, cid):
        return {"id": 1}

    async def _fake_send_and_receive(**kw):
        calls["lang"] = kw.get("lang")
        return {"ai_message": {"content": "ok"}}

    import app.application.chat_service as cs
    monkeypatch.setattr(cs, "create_session", _fake_create_session)
    monkeypatch.setattr(cs, "send_and_receive", _fake_send_and_receive)
    out = asyncio.run(inbound._run_companion_reply(55, 101, "hi"))
    assert calls["lang"] == "en"
    assert out == "ok"


def test_run_companion_reply_lang_fallback_zh_when_no_user(wc_db, monkeypatch):
    """P3-5：User 行缺失 → 回落 zh（不再写死 zh，但无配置时 zh 兜底）。"""
    inbound = _inbound()
    calls = {}

    async def _fake_create_session(uid, cid):
        return {"id": 1}

    async def _fake_send_and_receive(**kw):
        calls["lang"] = kw.get("lang")
        return {"ai_message": {"content": "ok"}}

    import app.application.chat_service as cs
    monkeypatch.setattr(cs, "create_session", _fake_create_session)
    monkeypatch.setattr(cs, "send_and_receive", _fake_send_and_receive)
    asyncio.run(inbound._run_companion_reply(999, 101, "hi"))  # 无 User 行
    assert calls["lang"] == "zh"


# ================================================================== schedule_tick hook 边界
def _on_tick_fn():
    """取插件 main 模块已注册的 schedule_tick hook 函数。"""
    return registry._loaded["wechat_ilink"]["module"]._on_tick


def _cfg(enabled, **extra):
    base = {"poll_interval_seconds": 30, "long_poll_timeout_seconds": 25,
            "quota_per_24h": 10, "enabled": enabled}
    base.update(extra)
    return base


def test_schedule_tick_disabled_when_cfg_off(wc_db, monkeypatch):
    """cfg.enabled=False → hook 直接返回（不轮询）。"""
    from app.plugins import sdk
    called = {}
    monkeypatch.setattr(sdk, "get_config", lambda: {"enabled": False})
    monkeypatch.setattr(_inbound(), "LAST_TICK_AT", {"t": 0.0})

    async def _boom_poll(**kw):
        called["polled"] = True
        raise AssertionError("enabled 关闭时不应轮询")
    monkeypatch.setattr(_inbound(), "poll_once", _boom_poll)

    asyncio.run(_on_tick_fn()({}))
    assert "polled" not in called


def test_schedule_tick_enabled_polls(wc_db, monkeypatch):
    """cfg.enabled=True → hook 调用 poll_once（并把节流时间戳前移）。"""
    from app.plugins import sdk
    called = {}
    monkeypatch.setattr(sdk, "get_config", lambda: _cfg(True))
    monkeypatch.setattr(_inbound(), "LAST_TICK_AT", {"t": 0.0})

    async def _fake_poll(**kw):
        called["kw"] = kw
    monkeypatch.setattr(_inbound(), "poll_once", _fake_poll)

    asyncio.run(_on_tick_fn()({}))
    assert "kw" in called
    assert called["kw"]["long_poll"] == 25 and called["kw"]["quota"] == 10


def test_schedule_tick_isolates_poll_error(wc_db, monkeypatch):
    """§11.1#7：ILinkClient/poll_once 抛错时 hook 不抛（P0-5 异常隔离）。"""
    from app.plugins import sdk
    monkeypatch.setattr(sdk, "get_config", lambda: _cfg(True))
    monkeypatch.setattr(_inbound(), "LAST_TICK_AT", {"t": 0.0})

    async def _boom_poll(**kw):
        raise RuntimeError("ilink down")
    monkeypatch.setattr(_inbound(), "poll_once", _boom_poll)

    # 不抛异常即通过
    asyncio.run(_on_tick_fn()({}))


# ================================================================== 8. live（默认跳过）
@pytest.mark.live
@pytest.mark.skipif(not _RUN_LIVE, reason="live 用例默认跳过，需真实微信扫码联调（PR3 阻塞项）")
def test_live_inbound_roundtrip_real_wechat():
    """真机收发闭环（NEEDS_RUNTIME_VERIFICATION）：默认不跑，ILINK_RUN_LIVE=1 才运行。"""
    raise AssertionError("live 用例默认跳过，需真实微信扫码联调")
