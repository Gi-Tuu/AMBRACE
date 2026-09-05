# -*- coding: utf-8 -*-
"""一机多主 / 渠道绑定 per-账号化测试（S0 内核 + S1 微信桥路由配额 + S2 抖音写死清零）。

- S0：ChannelBindingService 多租户隔离 / family_single 占用 / 跨家庭 / 子账号 / bot_single
  多 bot / 并发双绑唯一约束兜底 / reader 双读（flag 关回落旧全局串）；
- S1：bridge_relay 按 (bot_account_id, ilink_user_id) 路由不串台 / 配额 per-bot 独立 /
  回执稳定键（out 行 ilink_msg_id="gw:{in_msg_id}" + delivery 稳定键回退定位）；
- S2：抖音五表 user_id→tenant_id 正名 + 代码写死清零（grep 兜底）。
"""
import asyncio
import importlib
import pathlib
import re

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.application import channel_binding_service as svc
from app.application.tenant_scope import assert_standalone_owner, resolve_tenant
from app.models.channel import ChannelBinding
from app.models.character import AICharacter
from app.models.user import User
from app.plugins import registry
from app.providers import registry as prov_reg
from app.providers.channel_binding_reader import (
    all_bound_characters,
    bound_characters_for_runtime,
    channel_binding_v2_enabled,
)
from app.agent import loop as agent_loop

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "wechat_ilink"
_SECRET = "binding-v2-test-secret"
RELAY_URL = "/api/v1/plugins/bridge/wechat-relay"
DELIVERY_URL = "/api/v1/plugins/bridge/wechat-delivery"


# ================================================================= 共用夹具


@pytest.fixture()
def v2_db(tmp_path):
    """独立临时库（不动生产/全局库），供 S0 service 与 S1 桥测试共用建库逻辑。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


async def _seed_family(factory):
    """两个独立主账号家庭：user1(char101/103)、user2(char102)；user3 是 user1 的子账号。"""
    async with factory() as db:
        db.add(User(id=1, username="main1", nickname="m1", is_admin=True))
        db.add(User(id=2, username="main2", nickname="m2", is_admin=True))
        db.add(User(id=3, username="sub1", nickname="s1", parent_id=1))
        db.add(AICharacter(id=101, user_id=1, name="小慧"))
        db.add(AICharacter(id=103, user_id=1, name="小橙"))
        db.add(AICharacter(id=102, user_id=2, name="小蓝"))
        await db.commit()


def _flag_on(monkeypatch):
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)


# ================================================================= S0：service / tenant_scope / reader


def test_s0_tenant_isolation_no_overwrite(v2_db):
    """核心回归（§15-1）：租户 A/B 各绑各的角色，B 的 upsert 不覆盖 A（后绑覆盖先绑已根除）。"""
    f = v2_db
    asyncio.run(_seed_family(f))

    async def _run():
        async with f() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with f() as db:
            await svc.upsert_binding(db, 2, "wechat", 102)
            await db.commit()

    asyncio.run(_run())
    rows = asyncio.run(_list_all(f))
    assert len(rows) == 2
    by_tenant = {r.tenant_id: r.character_id for r in rows}
    assert by_tenant == {1: 101, 2: 102}  # A 的绑定未被冲掉


async def _list_all(f):
    async with f() as db:
        return (await db.execute(select(ChannelBinding).order_by(ChannelBinding.id))).scalars().all()


def test_s0_family_single_occupied_then_unbind(v2_db):
    """§15-2：同租户同渠道第二角色 → ChannelOccupied；解绑后可换绑。"""
    f = v2_db
    asyncio.run(_seed_family(f))

    async def _run():
        async with f() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with f() as db:
            with pytest.raises(svc.ChannelOccupied):
                await svc.upsert_binding(db, 1, "wechat", 103)
        async with f() as db:
            assert await svc.remove_binding(db, 1, "wechat") is True
            await db.commit()
        async with f() as db:
            await svc.upsert_binding(db, 1, "wechat", 103)
            await db.commit()

    asyncio.run(_run())
    rows = asyncio.run(_list_all(f))
    assert [r.character_id for r in rows] == [103]


def test_s0_cross_family_character(v2_db):
    """§15-4：把别家庭角色传入 → CrossFamilyCharacter（403 语义）。"""
    f = v2_db
    asyncio.run(_seed_family(f))

    async def _run():
        async with f() as db:
            with pytest.raises(svc.CrossFamilyCharacter):
                await svc.upsert_binding(db, 1, "wechat", 102)

    asyncio.run(_run())


def test_s0_sub_account_forbidden(v2_db):
    """§15-5：子账号不可写绑定（403 语义）；resolve_tenant 跟随其 root。"""
    f = v2_db
    asyncio.run(_seed_family(f))

    async def _run():
        async with f() as db:
            with pytest.raises(svc.SubAccountForbidden):
                await assert_standalone_owner(db, 3)
            assert await resolve_tenant(db, 3) == 1

    asyncio.run(_run())


def test_s0_bot_single_multi_bot_and_missing_bot(v2_db):
    """§15-3：bot_single 渠道同租户多 bot 并存各绑一角色；缺 bot_account_id → 400 语义。"""

    class _Port:
        pass

    prov_reg.register_provider("channel", "testch", lambda: _Port(), source="test")
    prov_reg._ENTRIES[("channel", "testch")]["meta"] = {"binding": {"mode": "bot_single"}}
    try:
        f = v2_db
        asyncio.run(_seed_family(f))

        async def _run():
            async with f() as db:
                await svc.upsert_binding(db, 1, "testch", 101, bot_account_id="bot_alpha")
                await svc.upsert_binding(db, 1, "testch", 103, bot_account_id="bot_beta")
                await db.commit()
            async with f() as db:
                with pytest.raises(svc.BotAccountRequired):
                    await svc.upsert_binding(db, 1, "testch", 101, bot_account_id="  ")

        asyncio.run(_run())
        rows = asyncio.run(_list_all(f))
        assert {(r.bot_account_id, r.character_id) for r in rows} == {("bot_alpha", 101), ("bot_beta", 103)}
    finally:
        prov_reg._ENTRIES.pop(("channel", "testch"), None)


def test_s0_upsert_replaces_same_triple(v2_db):
    """upsert 语义：bot_single 渠道同 (channel,tenant,bot) 重复绑定=改绑（唯一行），不产生第二行。
    （family_single 换绑须先解绑——与内核旧「占用 400」语义一致，见 occupied 用例。）"""

    class _Port:
        pass

    prov_reg.register_provider("channel", "testch2", lambda: _Port(), source="test")
    prov_reg._ENTRIES[("channel", "testch2")]["meta"] = {"binding": {"mode": "bot_single"}}
    try:
        f = v2_db
        asyncio.run(_seed_family(f))

        async def _run():
            async with f() as db:
                await svc.upsert_binding(db, 1, "testch2", 101, bot_account_id="bot_x")
                await db.commit()
            async with f() as db:
                await svc.upsert_binding(db, 1, "testch2", 103, bot_account_id="bot_x")
                await db.commit()

        asyncio.run(_run())
        rows = asyncio.run(_list_all(f))
        assert len(rows) == 1 and rows[0].character_id == 103 and rows[0].bot_account_id == "bot_x"
    finally:
        prov_reg._ENTRIES.pop(("channel", "testch2"), None)


def test_s0_reader_dual_read(v2_db, monkeypatch):
    """§15-8/9：flag 关回落旧全局 config 串；flag 开读新表；flag 开空表也回落（灰度期双读）。"""
    f = v2_db
    asyncio.run(_seed_family(f))
    monkeypatch.delitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", raising=False)
    assert channel_binding_v2_enabled() is False

    async def _run():
        # 旧全局 config 串（fallback 数据源）
        from app.models.plugin import Plugin

        async with f() as db:
            db.add(Plugin(name="wechat_ilink", version="1.0.0", config_json='{"allowed_character_ids":"101"}'))
            await db.commit()
            assert await bound_characters_for_runtime(db, "wechat", 1) == [101]
            # flag 开 + 新表（租户 1 有行）
            _flag_on(monkeypatch)
            async with f() as db2:
                await svc.upsert_binding(db2, 1, "wechat", 103)
                await db2.commit()
            assert await bound_characters_for_runtime(db, "wechat", 1) == [103]

    asyncio.run(_run())


def test_s0_reader_all_bound_characters_tenants(v2_db, monkeypatch):
    """flag 开：all_bound_characters 返回 (tenant, char) 对，供插件侧无租户上下文遍历。"""
    f = v2_db
    asyncio.run(_seed_family(f))
    _flag_on(monkeypatch)

    async def _run():
        async with f() as db:
            await svc.upsert_binding(db, 2, "wechat", 102)
            await db.commit()
        async with f() as db:
            assert await all_bound_characters(db, "wechat") == [(2, 102)]

    asyncio.run(_run())


def test_s0_concurrent_double_bind_unique(v2_db):
    """§15-10：并发双绑同 (channel,tenant,bot) → DB 唯一约束兜底（一行成功，另一行 IntegrityError）。"""
    f = v2_db
    asyncio.run(_seed_family(f))

    async def _run():
        async with f() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with f() as db:
            db.add(ChannelBinding(channel="wechat", tenant_id=1, owner_user_id=1,
                                  bot_account_id="default", character_id=103))
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

    asyncio.run(_run())


# ================================================================= S1：微信桥路由 / 配额 / 回执稳定键


@pytest.fixture()
def wc_plugin():
    if not registry.load_plugin_dir(_PLUGIN_DIR):
        raise RuntimeError("wechat_ilink plugin failed to load")
    yield
    prov_reg.unregister_providers_for_source("wechat_ilink")
    registry._loaded.pop("wechat_ilink", None)
    registry._db_config.pop("wechat_ilink", None)
    registry._enabled.pop("wechat_ilink", None)


@pytest.fixture()
def wc_db(wc_plugin, v2_db, monkeypatch):
    """wc_plugin 须先于 v2_db：插件先加载（自有 ORM 注册进 Base.metadata），建表才含 wechat_ilink_*。"""
    monkeypatch.setenv("AMBRACE_SECRET_KEY", "wechat-ilink-test-secret-000000000000000000000001")
    monkeypatch.setenv("WECHAT_ILINK_BRIDGE_SECRET", _SECRET)
    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", v2_db)
    return v2_db


def _client() -> TestClient:
    from app.api import plugins as plugins_api

    app = FastAPI()
    app.include_router(plugins_api.router)
    return TestClient(app)


async def _seed_two_bots(factory):
    """同一微信用户 wx1 对两个 bot 的会话（§15-6 前置）：bot default→char101、botB→char102。"""
    M = registry._loaded["wechat_ilink"]["module"].models
    async with factory() as db:
        db.add(M.WeChatILinkBinding(
            user_id=1, tenant_id=1, bot_account_id="default", character_id=101,
            ilink_user_id="wx1", bot_token_enc="x", baseurl="https://ilinkai.weixin.qq.com", enabled=True))
        db.add(M.WeChatILinkBinding(
            user_id=1, tenant_id=1, bot_account_id="botB", character_id=102,
            ilink_user_id="wx1", bot_token_enc="x", baseurl="https://ilinkai.weixin.qq.com", enabled=True))
        await db.commit()


def _patch_reply_capture(monkeypatch, text="回复文本"):
    mod = importlib.import_module("inbound")
    seen = []

    async def _fake(user_id, character_id, content):
        seen.append((user_id, character_id))
        return text

    monkeypatch.setattr(mod, "_run_companion_reply", _fake)
    return seen


def test_s1_relay_routes_by_bot_not_wxuser_only(wc_db, monkeypatch):
    """§15-6：同 ilink_user_id 不同 bot_account_id 两条绑定 → 各自命中角色，不 .first() 串台。"""
    asyncio.run(_seed_two_bots(wc_db))
    seen = _patch_reply_capture(monkeypatch)
    client = _client()

    r = client.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m-b",
                                     "bot_account_id": "botB"},
                    headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.status_code == 200 and r.json()["ok"] is True
    r2 = client.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m-a"},
                     headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r2.status_code == 200 and r2.json()["ok"] is True
    assert seen == [(1, 102), (1, 101)]  # botB→char102，default→char101
    assert r.json()["bot_account_id"] == "botB"


def test_s1_quota_per_bot_independent(wc_db, monkeypatch):
    """§15-7：入站窗口重置只重置本行（per-bot/per-tenant 独立，不跨 bot 累加）。

    语义口径（设计 §9）：relay 收到入站 → QuotaGate.on_inbound 只重置目标 bot 的绑定行；
    另一 bot 的窗口/计数保持不动。
    """
    import datetime as _dt

    M = registry._loaded["wechat_ilink"]["module"].models

    async def _seed():
        old = _dt.datetime.now() - _dt.timedelta(hours=30)
        async with wc_db() as db:
            db.add(M.WeChatILinkBinding(
                user_id=1, tenant_id=1, bot_account_id="botA", character_id=101,
                ilink_user_id="wx1", bot_token_enc="x",
                baseurl="https://ilinkai.weixin.qq.com", enabled=True,
                out_count_in_window=10, window_started_at=old))
            db.add(M.WeChatILinkBinding(
                user_id=1, tenant_id=1, bot_account_id="botB", character_id=102,
                ilink_user_id="wx1", bot_token_enc="x",
                baseurl="https://ilinkai.weixin.qq.com", enabled=True,
                out_count_in_window=7, window_started_at=old))
            await db.commit()

    asyncio.run(_seed())
    _patch_reply_capture(monkeypatch)
    client = _client()
    r = client.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "q1",
                                     "bot_account_id": "botA"},
                    headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r.json()["sendable"] is True  # botA 入站重置本行窗口 → 重新计 10 条
    M2 = registry._loaded["wechat_ilink"]["module"].models

    async def _rows():
        async with wc_db() as db:
            return {b.bot_account_id: b for b in (await db.execute(
                select(M2.WeChatILinkBinding))).scalars().all()}

    rows = asyncio.run(_rows())
    assert rows["botA"].out_count_in_window == 1 and rows["botA"].window_started_at > rows["botB"].window_started_at
    assert rows["botB"].out_count_in_window == 7  # botB 行未被重置（不跨 bot 累加/联动）


def test_s1_delivery_stable_key_fallback(wc_db, monkeypatch):
    """回执稳定键：out 行 ilink_msg_id="gw:{in_msg_id}"；delivery 缺 out_row_id 时按
    (bot_account_id, in_msg_id) 稳定键定位（消除 lastOutRowId 单值竞态）。"""
    asyncio.run(_seed_two_bots(wc_db))
    _patch_reply_capture(monkeypatch)
    client = _client()
    r = client.post(RELAY_URL, json={"ilink_user_id": "wx1", "text": "hi", "msg_id": "m-key",
                                     "bot_account_id": "botB"},
                    headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    data = r.json()
    assert data["sendable"] is True and data["in_msg_id"] == "m-key"

    M = registry._loaded["wechat_ilink"]["module"].models

    async def _out_rows():
        async with wc_db() as db:
            return list((await db.execute(
                select(M.WeChatILinkMessage).where(M.WeChatILinkMessage.direction == "out")
            )).scalars().all())

    out_rows = asyncio.run(_out_rows())
    assert [m.ilink_msg_id for m in out_rows] == ["gw:m-key"]

    # 不带 out_row_id，仅稳定键（botB 上的 in_msg_id）→ 定位成功并回标 failed
    r2 = client.post(DELIVERY_URL, json={"bot_account_id": "botB", "in_msg_id": "m-key", "ok": False},
                     headers={"X-AMBRACE-Bridge-Secret": _SECRET})
    assert r2.status_code == 200 and r2.json()["status"] == "failed"
    rows = asyncio.run(_out_rows())
    assert rows[0].status == "failed"


def test_s1_partial_unique_bot_wxuser(wc_db):
    """uq_wechat_bot_wxuser（partial：ilink_user_id != ''）：同 (bot,wxuser) 唯一；空 wxuser 多行可存。"""
    M = registry._loaded["wechat_ilink"]["module"].models

    async def _run():
        async with wc_db() as db:
            db.add(M.WeChatILinkBinding(user_id=1, character_id=101, ilink_user_id="wx1", enabled=True))
            await db.commit()
        async with wc_db() as db:
            db.add(M.WeChatILinkBinding(user_id=2, character_id=102, ilink_user_id="wx1", enabled=True))
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()
        async with wc_db() as db:  # 空 ilink_user_id（未扫码落行）不撞约束
            db.add(M.WeChatILinkBinding(user_id=2, character_id=102, ilink_user_id="", enabled=True))
            await db.commit()

    asyncio.run(_run())


# ================================================================= S2：抖音正名 + 写死清零


@pytest.fixture()
def douyin_plugin():
    """经内核加载器装配 douyin_mcp（与其他 douyin 测试同法，保持 sys.modules/metadata 单份注册）。"""
    ddir = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "douyin_mcp"
    if not registry.load_plugin_dir(ddir):
        raise RuntimeError("douyin_mcp plugin failed to load")
    yield
    prov_reg.unregister_providers_for_source("douyin_mcp")
    registry._loaded.pop("douyin_mcp", None)
    registry._db_config.pop("douyin_mcp", None)
    registry._enabled.pop("douyin_mcp", None)


def test_s2_douyin_models_tenant_rename(douyin_plugin):
    """模型层：五表 user_id→tenant_id（无 default），DouyinAccount 带 bot 维度。

    注意：经 registry 装载后检查（不许独立 exec douyin_models.py——会向全局 Base.metadata
    重复注册同名表，污染后续插件加载，2026-09-05 全量回归教训）。
    """
    import douyin_models  # noqa: PLC0415 - 插件目录已入 sys.path（registry 加载 main.py 时）

    for name in ("DouyinAccount", "DouyinPost", "DouyinComment", "DouyinPending", "DouyinViewedNote"):
        cols = douyin_models.__dict__[name].__table__.columns
        assert "tenant_id" in cols, name
        assert "user_id" not in cols, name
        assert cols["tenant_id"].default is None, f"{name}.tenant_id 不应有默认值"
    acc = douyin_models.DouyinAccount.__table__.columns
    assert "bot_account_id" in acc and "bot_label" in acc


def test_s2_douyin_no_hardcoded_user_id_1():
    """§15 写死清零（回归兜底）：douyin 插件源码不得残留 user_id=1 / default=1（注释/docstring 除外）。"""
    base = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "examples" / "douyin_mcp"
    banned = re.compile(r"user_id\s*=\s*1\b|user_id\s*==\s*1\b|default\s*=\s*1\b|\"user_id\"\s*:\s*1\b|DEFAULT\s+1\b")

    def _code_lines(text: str):
        # 去掉 docstring/注释，只扫代码行
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        text = re.sub(r"'''[\s\S]*?'''", "", text)
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "不再写死" in line:
                continue
            yield i, line

    for py in base.glob("*.py"):
        for i, line in _code_lines(py.read_text(encoding="utf-8")):
            assert not banned.search(line), f"{py.name}:{i} 残留写死: {line.strip()}"


def test_s2_flag_off_reader_falls_back_to_global_config(v2_db, monkeypatch):
    """flag 关（默认）：douyin 白名单读取回落旧全局 config 串，与 3.4.4 行为一致。"""
    monkeypatch.delitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", raising=False)
    f = v2_db
    asyncio.run(_seed_family(f))

    async def _run():
        from app.models.plugin import Plugin

        async with f() as db:
            db.add(Plugin(name="douyin_mcp", version="1.0.0", config_json='{"allowed_character_ids":"101,102"}'))
            await db.commit()
            # 全局串两角色 → 解析各自归属租户（user1→1、user2→2）
            assert await all_bound_characters(db, "douyin") == [(1, 101), (2, 102)]
            assert await bound_characters_for_runtime(db, "douyin", 1) == [101, 102]

    asyncio.run(_run())


# ================================================================= 落地审查修复（2026-09-05 Batch2：C2/C3）


def test_c2_reader_takeover_isolation(v2_db, monkeypatch):
    """C2：flag 开时渠道已被 v2 接管 → 无行租户读 []（不跨租户回落）；解绑幽灵防回归。"""
    f = v2_db
    asyncio.run(_seed_family(f))
    _flag_on(monkeypatch)

    async def _run():
        # 全局串残留（含 A/B 两租户角色）
        from app.models.plugin import Plugin

        async with f() as db:
            db.add(Plugin(name="wechat_ilink", version="1.0.0", config_json='{"allowed_character_ids":"101,102"}'))
            await db.commit()
        # A 绑定（接管渠道）→ A 读 [101]；B 无行 → 读 []（全局串 102 是 B 的角色也不回落给 B）
        async with f() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with f() as db:
            assert await bound_characters_for_runtime(db, "wechat", 1) == [101]
            assert await bound_characters_for_runtime(db, "wechat", 2) == []
        # A 删行（渠道仍有行吗？删的是 A 的唯一行 → 渠道表全空 → 回落但按归属过滤）
        async with f() as db:
            await svc.remove_binding(db, 1, "wechat")
            await db.commit()
        async with f() as db:
            assert await bound_characters_for_runtime(db, "wechat", 1) == [101]  # 全空回落，过滤后仅 A 的 101
            assert await bound_characters_for_runtime(db, "wechat", 2) == [102]  # 过滤后仅 B 的 102
        # 防幽灵：B 绑定后 A 解绑 → A 读 []（渠道仍被接管）
        async with f() as db:
            await svc.upsert_binding(db, 2, "wechat", 102)
            await db.commit()
        async with f() as db:
            await svc.remove_binding(db, 1, "wechat")
            await db.commit()
        async with f() as db:
            assert await bound_characters_for_runtime(db, "wechat", 1) == []

    asyncio.run(_run())


def test_c3_physical_singleton_second_tenant_rejected(v2_db, monkeypatch):
    """C3 路线 A：物理单实例渠道第二主账号绑定 → PhysicalSingletonTaken（API 409）。"""
    from app.providers import registry as prov_reg

    class _Port:
        pass

    prov_reg.register_provider("channel", "singlech", lambda: _Port(), source="test")
    prov_reg._ENTRIES[("channel", "singlech")]["meta"] = {
        "binding": {"mode": "family_single", "physical_singleton": True}}
    try:
        f = v2_db
        asyncio.run(_seed_family(f))
        _flag_on(monkeypatch)

        async def _run():
            async with f() as db:
                await svc.upsert_binding(db, 1, "singlech", 101)
                await db.commit()
            async with f() as db:
                with pytest.raises(svc.PhysicalSingletonTaken):
                    await svc.upsert_binding(db, 2, "singlech", 102)
            # A 解绑后 B 可绑
            async with f() as db:
                await svc.remove_binding(db, 1, "singlech")
                await db.commit()
            async with f() as db:
                await svc.upsert_binding(db, 2, "singlech", 102)
                await db.commit()

        asyncio.run(_run())
    finally:
        prov_reg._ENTRIES.pop(("channel", "singlech"), None)


def test_c3_viewed_note_tenant_composite_unique(douyin_plugin, v2_db, monkeypatch):
    """C3/C9：两租户各写同 aweme_id 的 ViewedNote 不撞键（复合唯一）。"""
    import douyin_models

    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", v2_db)

    async def _run():
        async with v2_db() as db:
            db.add(douyin_models.DouyinViewedNote(tenant_id=1, aweme_id="same", author="a", desc="d"))
            db.add(douyin_models.DouyinViewedNote(tenant_id=2, aweme_id="same", author="b", desc="d"))
            await db.commit()
        async with v2_db() as db:
            with pytest.raises(IntegrityError):
                db.add(douyin_models.DouyinViewedNote(tenant_id=1, aweme_id="same", author="c", desc="d"))
                await db.commit()
            await db.rollback()

    asyncio.run(_run())
