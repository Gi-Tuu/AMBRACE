# -*- coding: utf-8 -*-
"""S4：租户隔离矩阵回归（一机多主 / SaaS S0 同源键，2026-09-05）。

统一约定：任何用户私有数据以 tenant_id = 家庭 root 为隔离键（app/application/tenant_scope）。
本文件与既有角色/会话隔离测试并列，覆盖渠道面（channel_bindings）：
- A/B 两独立主账号：互相不可见、互相不可影响（service 层 + reader 层）；
- 子账号 = 其 root（GET 跟随、写 403）；
- resolve_tenant 全员一致（主=自己，子=父）。
"""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import loop as agent_loop
from app.application import channel_binding_service as svc
from app.application.tenant_scope import resolve_tenant
from app.models.channel import ChannelBinding
from app.models.character import AICharacter
from app.models.user import User


# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

@pytest.fixture()
def iso_db(tmp_path):
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


async def _seed(factory):
    async with factory() as db:
        db.add(User(id=1, username="A", nickname="A", is_admin=True))
        db.add(User(id=2, username="B", nickname="B", is_admin=True))
        db.add(User(id=3, username="A_sub", nickname="s", parent_id=1, is_admin=False))
        db.add(AICharacter(id=101, user_id=1, name="甲"))
        db.add(AICharacter(id=102, user_id=2, name="乙"))
        await db.commit()


async def _rows(factory):
    async with factory() as db:
        return (await db.execute(select(ChannelBinding).order_by(ChannelBinding.id))).scalars().all()


def test_matrix_tenant_resolution(iso_db):
    """租户键：主账号=自己；子账号=其主账号；与 SaaS S0 同源。"""
    asyncio.run(_seed(iso_db))

    async def _run():
        async with iso_db() as db:
            return {
                1: await resolve_tenant(db, 1),
                2: await resolve_tenant(db, 2),
                3: await resolve_tenant(db, 3),
            }

    assert asyncio.run(_run()) == {1: 1, 2: 2, 3: 1}


def test_matrix_bindings_mutually_invisible(iso_db, monkeypatch):
    """A/B 各绑各渠道角色：list_bindings 互不可见；DB 行互不覆盖。"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    asyncio.run(_seed(iso_db))

    async def _run():
        async with iso_db() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with iso_db() as db:
            await svc.upsert_binding(db, 2, "wechat", 102)
            await db.commit()
        async with iso_db() as db:
            a = await svc.list_bindings(db, 1, "wechat")
            b = await svc.list_bindings(db, 2, "wechat")
            all_rows = await svc.list_bindings(db, 1)  # 跨渠道列 A 名下全部
            return a, b, all_rows

    a, b, all_rows = asyncio.run(_run())
    assert [r.character_id for r in a] == [101]
    assert [r.character_id for r in b] == [102]
    assert len(asyncio.run(_rows(iso_db))) == 2
    assert all(r.tenant_id == 1 for r in all_rows)


def test_matrix_sub_account_get_follows_root_write_forbidden(iso_db, monkeypatch):
    """子账号读=其 root 家庭视图；写=403 语义（SubAccountForbidden）。"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    asyncio.run(_seed(iso_db))

    async def _seed_bind():
        async with iso_db() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()

    asyncio.run(_seed_bind())

    async def _run():
        async with iso_db() as db:
            sub_view = await svc.list_bindings(db, await resolve_tenant(db, 3), "wechat")
            with pytest.raises(svc.SubAccountForbidden):
                await svc.upsert_binding(db, 3, "wechat", 101)
            with pytest.raises(svc.SubAccountForbidden):
                await svc.remove_binding(db, 3, "wechat")
            return sub_view

    sub_view = asyncio.run(_run())
    assert [r.character_id for r in sub_view] == [101]


def test_matrix_b_rebind_does_not_touch_a(iso_db, monkeypatch):
    """后绑覆盖先绑回归（服务层）：B 换绑后 A 的 character_id 保持。"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    asyncio.run(_seed(iso_db))

    async def _run():
        async with iso_db() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with iso_db() as db:
            await svc.upsert_binding(db, 2, "wechat", 102)
            await db.commit()
        async with iso_db() as db:
            await svc.upsert_binding(db, 2, "wechat", 102, bot_label="乙的bot")  # B 再次 PUT
            await db.commit()

    asyncio.run(_run())
    rows = asyncio.run(_rows(iso_db))
    by_tenant = {r.tenant_id: r.character_id for r in rows}
    assert by_tenant == {1: 101, 2: 102}


# ═══════════════════════════════════════════════════════════════════════════════
# 账号独立 P1（2026-09-19）：租户隔离矩阵（口径 A = 家庭根）
#
# 统一约定：tenant_key = 家庭根账号 user_id（app/application/tenant_service）。
# 本段覆盖用户维度资源的**逐类跨家庭拒绝 + 家庭内放行**正向用例，外加：
#   - 四模态配置回落链（用户默认 → 家庭默认 → 服务器默认 → 明确报错）；
#   - tenant_key_mode 切口径（family → user）只改一处的可验证性；
#   - /uploads 闸门（带身份按租户 / 匿名按开关）；
#   - users.server_admin 存量迁移（Alembic c4d5e6f7a8b9）与启动期幂等回填。
#
# 临时库一律 pytest tmp_path（私有 SQLite 文件），不落 backend/data、跑完即随 tmp 清理。
# ═══════════════════════════════════════════════════════════════════════════════

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api import admin as admin_api
from app.api import characters as characters_api
from app.api import chat_groups as chat_groups_api
from app.api import diary as diary_api
from app.api import memories as memories_api
from app.api import moments as moments_api
from app.api import scheduler as scheduler_api
from app.api import user_content as user_content_api
from app.auth.config import create_token
from app.application import permission_service as perm
from app.application.tenant_service import get_tenant_key_mode
from app.models.character import ProactiveSettings
from app.models.chat import (
    ChatGroup,
    ChatGroupMember,
    ChatGroupMessage,
    ChatSession,
)
from app.models.config import VlmConfig
from app.models.life import (
    AIDiary,
    AIMoment,
    ScheduledEvent,
    UserDiary,
    UserMemo,
)
from app.models.memory import Memory

_FAMILY_A_ROOT, _FAMILY_A_SUB = 1, 3
_FAMILY_B_ROOT, _FAMILY_B_SUB = 2, 4
_FAMILY_C_ROOT = 5  # 无自有配置的独立主账号（回落服务器默认用）


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上。

    两类接缝都覆盖：
    - ``app.db.database.async_session_factory``：``get_db`` / ``unit_of_work`` 在定义模块
      命名空间里查找（既有 patch 接缝），函数体内的 ``from app.db.database import ...``
      晚绑定也命中；
    - 模块级 ``from app.db.database import async_session_factory`` 的早绑定引用：
      扫描已加载的 app.* 模块，把仍指向原工厂的属性换成私有工厂。
    """
    import app.db.database as db_mod
    import app.db.session as session_mod

    original = db_mod.async_session_factory
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(session_mod, "async_session_factory", factory, raising=False)
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")):
            continue
        try:
            if getattr(mod, "async_session_factory", None) is original:
                monkeypatch.setattr(mod, "async_session_factory", factory)
        except Exception:
            continue


@pytest.fixture()
def matrix_db(monkeypatch, tmp_path):
    """两个家庭 + 一个无配置家庭 的私有临时 SQLite 库（含各资源种子）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/matrix.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                # 家庭 A：root=1 + 子账号 3；家庭 B：root=2 + 子账号 4；家庭 C：独立 root=5（无配置）
                User(id=1, username="A_root", nickname="A主", is_admin=True, server_admin=True),
                User(id=2, username="B_root", nickname="B主", is_admin=True, server_admin=False),
                User(id=3, username="A_sub", nickname="A子", is_admin=False, parent_id=1),
                User(id=4, username="B_sub", nickname="B子", is_admin=False, parent_id=2),
                User(id=5, username="C_root", nickname="C主", is_admin=True, server_admin=False),
                # 角色
                AICharacter(id=101, user_id=1, name="A主角色"),
                AICharacter(id=103, user_id=3, name="A子角色"),
                AICharacter(id=201, user_id=2, name="B主角色"),
                # 记忆
                Memory(id=1001, user_id=1, character_id=101, memory_type="event", content="A主记忆"),
                Memory(id=1002, user_id=3, character_id=103, memory_type="event", content="A子记忆"),
                Memory(id=2001, user_id=2, character_id=201, memory_type="event", content="B主记忆"),
                # AI 日记（无 user_id 列，归属由角色承载）
                AIDiary(id=3001, character_id=101, diary_date="2026-09-19", content="A主日记"),
                AIDiary(id=3002, character_id=201, diary_date="2026-09-19", content="B主日记"),
                # 朋友圈
                AIMoment(id=4001, character_id=None, user_id=1, sender_type="user", content="A用户动态"),
                AIMoment(id=4002, character_id=101, user_id=1, sender_type="ai", content="A主角色动态"),
                AIMoment(id=4003, character_id=201, user_id=2, sender_type="ai", content="B主角色动态"),
                # 会话 + 定时器
                ChatSession(id=5001, user_id=1, character_id=101),
                ChatSession(id=6001, user_id=2, character_id=201),
                ScheduledEvent(
                    id=7001, user_id=1, character_id=101, session_id=5001, status="pending",
                    trigger_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3),
                ),
                ScheduledEvent(
                    id=7002, user_id=2, character_id=201, session_id=6001, status="pending",
                    trigger_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3),
                ),
                # 群聊
                ChatGroup(id=8001, user_id=1, name="A家群"),
                ChatGroup(id=8002, user_id=2, name="B家群"),
                ChatGroupMember(group_id=8001, character_id=101),
                ChatGroupMember(group_id=8002, character_id=201),
                ChatGroupMessage(id=9001, group_id=8001, sender_type="user", content="A群消息"),
                ChatGroupMessage(id=9002, group_id=8002, sender_type="user", content="B群消息"),
                # 备忘录 / 用户日记（含家庭 A 子账号自有行，用于验证家庭内共享）
                UserMemo(id=11001, user_id=1, content="A备忘"),
                UserMemo(id=11002, user_id=2, content="B备忘"),
                UserMemo(id=11003, user_id=3, content="A子备忘"),
                UserDiary(id=11051, user_id=1, diary_date="2026-09-19", content="A用户日记"),
                UserDiary(id=11052, user_id=2, diary_date="2026-09-19", content="B用户日记"),
                UserDiary(id=11053, user_id=3, diary_date="2026-09-19", content="A子用户日记"),
                # 四模态配置：A/B 各自用户级识图配置 + 服务器级哨兵行（家庭 C 无自有配置）
                VlmConfig(id=12001, user_id=1, enabled=True, base_url="http://a.example", api_key="sk-a", model="vlm-a"),
                VlmConfig(id=12002, user_id=2, enabled=True, base_url="http://b.example", api_key="sk-b", model="vlm-b"),
                VlmConfig(id=12000, user_id=0, enabled=True, base_url="http://server.example", api_key="sk-server", model="vlm-server"),
                # 主动设置（scheduler settings 路由）
                ProactiveSettings(id=13001, character_id=101),
                ProactiveSettings(id=13002, character_id=201),
            ])
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_admin_caches():
    """清主账号/服务器管理员判定缓存（跨用例污染护栏）。"""
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    yield
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()


def _auth(uid: int) -> dict:
    """真实 JWT（conftest 固定 AUTH_SECRET_KEY）——走真实鉴权路径，不做依赖覆盖。"""
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _matrix_client() -> TestClient:
    app = FastAPI()
    for r in (
        characters_api.router, memories_api.router, diary_api.router, moments_api.router,
        scheduler_api.router, scheduler_api.proactive_router, chat_groups_api.router,
        user_content_api.router, admin_api.router,
    ):
        app.include_router(r)
    return TestClient(app, raise_server_exceptions=False)


# ── 1. 租户键与口径开关 ────────────────────────────────────────────────────────

def test_p1_tenant_key_and_scope_ids(matrix_db):
    """A: 1+3 同租户；B: 2+4；C: 5 独立；scope_ids 给出家庭白名单。"""
    from app.application.tenant_service import tenant_key, tenant_scope_ids

    async def _run():
        async with matrix_db() as db:
            return {
                "keys": [await tenant_key(db, i) for i in (1, 3, 2, 4, 5)],
                "a": sorted(await tenant_scope_ids(db, 1)),
                "a_sub": sorted(await tenant_scope_ids(db, 3)),
                "b": sorted(await tenant_scope_ids(db, 2)),
                "c": sorted(await tenant_scope_ids(db, 5)),
            }

    out = asyncio.run(_run())
    assert out["keys"] == [1, 1, 2, 2, 5]
    assert out["a"] == [1, 3] and out["a_sub"] == [1, 3]
    assert out["b"] == [2, 4] and out["c"] == [5]


def test_p1_mode_switch_user_isolates_every_account(matrix_db):
    """口径开关：tenant_key_mode=user → 每账号独立（只改 helper 一处即生效）。"""
    from app.application.tenant_service import set_tenant_key_mode, tenant_scope_ids

    assert get_tenant_key_mode() == "family"
    try:
        assert set_tenant_key_mode("user") == "user"
        async def _run():
            async with matrix_db() as db:
                return sorted(await tenant_scope_ids(db, 1))
        assert asyncio.run(_run()) == [1]
        # 子账号不再看到家庭主账号的角色
        client = _matrix_client()
        r = client.get("/api/v1/characters", headers=_auth(3))
        assert r.status_code == 200, r.text
        assert {c["id"] for c in r.json()["characters"]} == {103}
    finally:
        set_tenant_key_mode(None)
    assert get_tenant_key_mode() == "family"


# ── 2. 角色 ───────────────────────────────────────────────────────────────────

def test_p1_characters_cross_family_denied_same_family_allowed(matrix_db):
    client = _matrix_client()
    # 家庭内放行：A 主账号看到 1+3 的角色；子账号 3 也看到家庭角色
    for uid in (1, 3):
        r = client.get("/api/v1/characters", headers=_auth(uid))
        assert r.status_code == 200, r.text
        assert {c["id"] for c in r.json()["characters"]} == {101, 103}, r.text
    r = client.get("/api/v1/characters", headers=_auth(2))
    assert {c["id"] for c in r.json()["characters"]} == {201}
    # 跨家庭：读 / 改 / 删 一律 404
    assert client.get("/api/v1/characters/201", headers=_auth(1)).status_code == 404
    assert client.get("/api/v1/characters/101", headers=_auth(2)).status_code == 404
    assert client.get("/api/v1/characters/101", headers=_auth(4)).status_code == 404
    assert client.put("/api/v1/characters/201", headers=_auth(1), json={"name": "改"}).status_code == 404
    assert client.delete("/api/v1/characters/201", headers=_auth(1)).status_code == 404
    # 跨家庭 lorebook / world-facts 子资源同样 404
    assert client.get("/api/v1/characters/201/lorebook", headers=_auth(1)).status_code == 404
    assert client.post(
        "/api/v1/characters/201/world-facts", headers=_auth(1),
        json={"content": "x", "predicate": "setting"},
    ).status_code == 404
    # 自家子资源放行
    assert client.get("/api/v1/characters/101/lorebook", headers=_auth(3)).status_code == 200


# ── 3. 记忆 / 日记 / 备忘录 / 用户日记 ────────────────────────────────────────

def test_p1_memories_cross_family_denied_same_family_allowed(matrix_db):
    client = _matrix_client()
    r = client.get("/api/v1/memories", headers=_auth(1))
    assert r.status_code == 200, r.text
    assert {m["id"] for m in r.json()["memories"]} == {1001, 1002}
    r = client.get("/api/v1/memories", headers=_auth(2))
    assert {m["id"] for m in r.json()["memories"]} == {2001}
    # 单条：跨家庭 404，家庭内 200 / 可写
    assert client.get("/api/v1/memories/2001", headers=_auth(1)).status_code == 404
    assert client.get("/api/v1/memories/1001", headers=_auth(2)).status_code == 404
    assert client.get("/api/v1/memories/1002", headers=_auth(1)).status_code == 200  # 家庭内放行
    assert client.patch("/api/v1/memories/2001", headers=_auth(1), json={"importance": 3}).status_code == 404
    assert client.delete("/api/v1/memories/2001", headers=_auth(1)).status_code == 404
    assert client.patch("/api/v1/memories/1001", headers=_auth(3), json={"importance": 3}).status_code == 200


def test_p1_diary_and_user_content(matrix_db):
    client = _matrix_client()
    # AI 日记（按角色归属）
    assert client.get("/api/v1/diary/101", headers=_auth(1)).status_code == 200
    assert client.get("/api/v1/diary/101", headers=_auth(3)).status_code == 200   # 家庭内放行
    assert client.get("/api/v1/diary/201", headers=_auth(1)).status_code == 404
    assert client.get("/api/v1/diary/201", headers=_auth(3)).status_code == 404
    assert client.get("/api/v1/diary/201/date/2026-09-19", headers=_auth(1)).status_code == 404
    assert client.post("/api/v1/diary/generate/201", headers=_auth(1)).status_code == 404
    # 备忘录 / 用户日记（按 user_id 归属）
    r = client.get("/api/v1/user/memos", headers=_auth(1))
    assert {m["id"] for m in r.json()["memos"]} == {11001, 11003}   # 家庭 A 内共享
    assert {m["id"] for m in client.get("/api/v1/user/memos", headers=_auth(3)).json()["memos"]} == {11001, 11003}
    r = client.get("/api/v1/user/memos", headers=_auth(2))
    assert {m["id"] for m in r.json()["memos"]} == {11002}
    assert client.put("/api/v1/user/memos/11002", headers=_auth(1), json={"content": "改"}).status_code == 404
    assert client.delete("/api/v1/user/memos/11002", headers=_auth(1)).status_code == 404
    assert client.put("/api/v1/user/memos/11001", headers=_auth(3), json={"content": "改"}).status_code == 200
    r = client.get("/api/v1/user/diaries", headers=_auth(1))
    assert {d["id"] for d in r.json()["diaries"]} == {11051, 11053}  # 家庭 A 内共享
    assert client.get("/api/v1/user/diaries/2026-09-19", headers=_auth(4)).json()["id"] == 11052
    assert client.delete("/api/v1/user/diaries/11052", headers=_auth(1)).status_code == 404


# ── 4. 朋友圈 ─────────────────────────────────────────────────────────────────

def test_p1_moments_cross_family_denied_same_family_allowed(matrix_db):
    client = _matrix_client()
    r = client.get("/api/v1/moments", headers=_auth(1))
    assert r.status_code == 200, r.text
    assert {m["id"] for m in r.json()["moments"]} == {4001, 4002}
    assert {m["id"] for m in client.get("/api/v1/moments", headers=_auth(3)).json()["moments"]} == {4001, 4002}
    assert {m["id"] for m in client.get("/api/v1/moments", headers=_auth(2)).json()["moments"]} == {4003}
    # 跨家庭：点赞 / 评论 / 删除 一律拒绝（404/403）
    assert client.post("/api/v1/moments/4003/like", headers=_auth(1)).status_code == 404
    assert client.get("/api/v1/moments/4003/comments", headers=_auth(1)).status_code == 404
    assert client.post("/api/v1/moments/4003/comments", headers=_auth(1), json={"content": "x"}).status_code == 404
    assert client.delete("/api/v1/moments/4003", headers=_auth(1)).status_code == 403
    # 家庭内放行
    assert client.post("/api/v1/moments/4002/like", headers=_auth(3)).status_code == 200
    assert client.get("/api/v1/moments/4001/comments", headers=_auth(3)).status_code == 200


# ── 5. 定时器 / 主动设置（scheduler）──────────────────────────────────────────

def test_p1_scheduler_cross_family_denied_same_family_allowed(matrix_db):
    client = _matrix_client()
    assert client.get("/api/v1/scheduler/settings/101", headers=_auth(1)).status_code == 200
    assert client.get("/api/v1/scheduler/settings/101", headers=_auth(3)).status_code == 200  # 家庭内放行
    assert client.get("/api/v1/scheduler/settings/201", headers=_auth(1)).status_code == 404
    assert client.get("/api/v1/scheduler/timers/101", headers=_auth(3)).status_code == 200
    assert client.get("/api/v1/scheduler/timers/201", headers=_auth(1)).status_code == 404
    assert client.delete("/api/v1/scheduler/timers/201/7002", headers=_auth(1)).status_code == 404
    # stats 带别家 character_id：原先无归属校验（跨家庭读），现 404
    assert client.get("/api/v1/scheduler/stats?character_id=201", headers=_auth(1)).status_code == 404
    assert client.get("/api/v1/scheduler/stats?character_id=101", headers=_auth(1)).status_code == 200


# ── 6. 群聊 ───────────────────────────────────────────────────────────────────

def test_p1_chat_groups_cross_family_denied_same_family_allowed(matrix_db):
    client = _matrix_client()
    r = client.get("/api/v1/chat-groups", headers=_auth(1))
    assert r.status_code == 200, r.text
    assert {g["id"] for g in r.json()["items"]} == {8001}
    assert client.get("/api/v1/chat-groups/8001/messages", headers=_auth(1)).status_code == 200
    assert client.get("/api/v1/chat-groups/8001/messages", headers=_auth(3)).status_code == 200  # 家庭内放行
    # 跨家庭：A 家两个账号读 B 家群 → 404；B 家（主 + 子）读到自己群 → 200（家庭内放行）
    for uid in (1, 3):
        assert client.get("/api/v1/chat-groups/8002/messages", headers=_auth(uid)).status_code == 404
    assert client.delete("/api/v1/chat-groups/8002", headers=_auth(1)).status_code == 404
    for uid in (2, 4):
        assert client.get("/api/v1/chat-groups/8002/messages", headers=_auth(uid)).status_code == 200


# ── 7. /uploads 闸门（App 裸 URL 兼容 + 带身份强制）──────────────────────────

def test_p1_uploads_scope_resolution():
    from app.uploads_gate import resolve_upload_scope

    assert resolve_upload_scope("avatars/1/a.png") == ("user", 1)
    assert resolve_upload_scope("moments/2/b.jpg") == ("user", 2)
    assert resolve_upload_scope("phone/1/album/c.png") == ("user", 1)
    assert resolve_upload_scope("emojis/user/3/x.png") == ("user", 3)
    assert resolve_upload_scope("12/e.png") == ("session", 12)
    assert resolve_upload_scope("files/12/c.pdf") == ("session", 12)
    assert resolve_upload_scope("voice/12/d.mp3") == ("session", 12)
    for shared in ("pets_assets/cat/idle.png", "pets/cat.png", "emojis/market/pack/y.png",
                   "tts/1/a.mp3", "douyin/t/v.mp4"):
        assert resolve_upload_scope(shared) == ("shared", None)


def test_p1_uploads_gate_cross_family_denied_same_family_allowed(matrix_db, tmp_path):
    """带身份请求：跨家庭 404、自家/家庭内放行、共享资源恒放行（本轮无前端改动）。"""
    from app.uploads_gate import TenantStaticFiles

    up = tmp_path / "uploads"
    (up / "avatars" / "1").mkdir(parents=True)
    (up / "avatars" / "1" / "a.png").write_bytes(b"pngA")
    (up / "avatars" / "2").mkdir(parents=True)
    (up / "avatars" / "2" / "b.png").write_bytes(b"pngB")
    (up / "pets_assets").mkdir(parents=True)
    (up / "pets_assets" / "shared.png").write_bytes(b"sharedPNG")

    app = FastAPI()
    app.mount("/uploads", TenantStaticFiles(directory=str(up)))
    client = TestClient(app)

    # 自家放行
    assert client.get("/uploads/avatars/1/a.png", headers=_auth(1)).status_code == 200
    # 家庭内放行（子账号读家庭主账号目录）
    assert client.get("/uploads/avatars/1/a.png", headers=_auth(3)).status_code == 200
    # 跨家庭 404（两个方向）
    assert client.get("/uploads/avatars/1/a.png", headers=_auth(2)).status_code == 404
    assert client.get("/uploads/avatars/2/b.png", headers=_auth(1)).status_code == 404
    assert client.get("/uploads/avatars/2/b.png", headers=_auth(3)).status_code == 404
    # 共享资源：任何身份放行
    assert client.get("/uploads/pets_assets/shared.png", headers=_auth(2)).status_code == 200
    # 匿名（App 现状）：兼容模式放行，严格模式 404
    assert client.get("/uploads/avatars/1/a.png").status_code == 200
    from app.config import settings as _settings
    _settings.uploads_require_auth = True
    try:
        assert client.get("/uploads/avatars/1/a.png").status_code == 404
        assert client.get("/uploads/pets_assets/shared.png").status_code == 200
        assert client.get("/uploads/avatars/1/a.png", headers=_auth(1)).status_code == 200
        assert client.get("/uploads/avatars/1/a.png", headers=_auth(2)).status_code == 404
    finally:
        _settings.uploads_require_auth = False


# ── 8. 四模态配置回落链（角色绑定 → 用户默认 → 家庭默认 → 服务器默认 → 报错）──

def test_p1_modality_fallback_chain_and_errors(matrix_db):
    """A/B 有用户级配置；子账号回落家庭；家庭 C 无自有配置回落服务器默认；全空则明确报错。"""
    from fastapi import HTTPException

    from app.application.llm_config_service import resolve_modality_config

    async def _run():
        async with matrix_db() as db:
            return {
                "a_root": await resolve_modality_config("vlm", _FAMILY_A_ROOT, None, db),
                "a_sub": await resolve_modality_config("vlm", _FAMILY_A_SUB, None, db),
                "b_root": await resolve_modality_config("vlm", _FAMILY_B_ROOT, None, db),
                "b_sub": await resolve_modality_config("vlm", _FAMILY_B_SUB, None, db),
                "c_root": await resolve_modality_config("vlm", _FAMILY_C_ROOT, None, db),
                "speech_none": await resolve_modality_config("speech", _FAMILY_C_ROOT, None, db),
                "image_alias": await resolve_modality_config("image_gen", _FAMILY_C_ROOT, None, db),
            }

    out = asyncio.run(_run())
    # 用户默认
    assert out["a_root"]["scope"] == "user" and out["a_root"]["config_id"] == 12001
    assert out["b_root"]["scope"] == "user" and out["b_root"]["config_id"] == 12002
    # 家庭默认：子账号回落家庭根（跨家庭不串：A 子拿 A 根，B 子拿 B 根）
    assert out["a_sub"]["scope"] == "family" and out["a_sub"]["config_id"] == 12001
    assert out["b_sub"]["scope"] == "family" and out["b_sub"]["config_id"] == 12002
    # 无自有配置 → 服务器默认（SERVER_CONFIG_UID=0）
    assert out["c_root"]["scope"] == "server" and out["c_root"]["config_id"] == 12000
    # 未配置的模态：返回 None（required=False），required=True 抛明确可读 400
    assert out["speech_none"] is None
    # 别名归一（image_gen → image，落到 image_gen_configs 哨兵表，此处无行 → None）
    assert out["image_alias"] is None

    async def _required():
        async with matrix_db() as db:
            with pytest.raises(HTTPException) as ei:
                await resolve_modality_config("speech", _FAMILY_C_ROOT, None, db, required=True)
            return ei.value

    err = asyncio.run(_required())
    assert err.status_code == 400
    assert "语音" in str(err.detail)


def test_p1_server_modality_outlet_is_single_source(matrix_db):
    """四模态（含语音 speech_configs、生图、视觉理解）查表都走同一出口，且写入口可新建哨兵行。"""
    from app.application.llm_config_service import (
        get_or_create_server_modality_row,
        get_server_modality_row,
        normalize_modality,
    )

    async def _run():
        async with matrix_db() as db:
            # 视觉理解：统一出口读到 12000 哨兵行
            vlm = await get_server_modality_row(db, "vlm")
            # 语音：无行 → 写入口新建（幂等）
            sp1 = await get_or_create_server_modality_row(db, "speech")
            sp2 = await get_or_create_server_modality_row(db, "speech")
            await db.commit()
            return vlm, sp1, sp2

    vlm, sp1, sp2 = asyncio.run(_run())
    assert vlm is not None and vlm.id == 12000
    assert sp1.user_id == 0 and sp2.id == sp1.id
    assert normalize_modality("image_gen") == "image"
    assert normalize_modality("") == "llm"


# ── 9. server_admin：存量迁移 + 启动期回填 + 依赖/端点 ────────────────────────

def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "c4d5e6f7a8b9_add_users_server_admin.py"
    spec = importlib.util.spec_from_file_location("mig_server_admin", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_server_admin_migration_add_column_and_backfill(tmp_path, monkeypatch):
    """Alembic c4d5e6f7a8b9：只加列 + **只授予 ADMIN_USER_IDS 列出的账号**，可重复执行（幂等）。

    09-19 复核修正（Qwen 只读审计发现）：本产品「独立账号」默认都是家庭主账号
    （`parent_id IS NULL → is_admin=1`），按 is_admin=1 回填会把每个新注册账号都变成
    控制台管理员、`require_server_admin` 闸门形同虚设。故权威来源改为 `.env ADMIN_USER_IDS`。
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_migration()
    assert mig.down_revision == "e8f9a0b1c2d3"

    monkeypatch.setenv("ADMIN_USER_IDS", "1")
    engine = sa.create_engine(f"sqlite:///{tmp_path}/mig.db")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "username VARCHAR(50), is_admin BOOLEAN DEFAULT 0)"
        ))
        conn.execute(sa.text(
            "INSERT INTO users (id, username, is_admin) VALUES (1,'a',1),(2,'b',0),(3,'c',1)"
        ))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()
            mig.upgrade()  # 幂等：重复执行不报错、不重复写
        cols = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(users)"))}
        assert "server_admin" in cols
        rows = dict(conn.execute(sa.text("SELECT id, server_admin FROM users")).fetchall())
        # 只有 ADMIN_USER_IDS 里的 1 被授予；同为 is_admin=1 的 3 **不**授予（回归 Qwen 发现）
        assert rows == {1: 1, 2: 0, 3: 0}
    engine.dispose()


def test_server_admin_migration_bootstraps_oldest_account(tmp_path, monkeypatch):
    """ADMIN_USER_IDS 指向不存在的账号 → 引导兜底授予最早账号，避免控制台彻底进不去。"""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_migration()
    monkeypatch.setenv("ADMIN_USER_IDS", "999")
    engine = sa.create_engine(f"sqlite:///{tmp_path}/mig_boot.db")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "username VARCHAR(50), is_admin BOOLEAN DEFAULT 0)"
        ))
        conn.execute(sa.text(
            "INSERT INTO users (id, username, is_admin) VALUES (1,'a',1),(2,'b',0),(3,'c',1)"
        ))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()
        rows = dict(conn.execute(sa.text("SELECT id, server_admin FROM users")).fetchall())
        assert rows == {1: 1, 2: 0, 3: 0}
    engine.dispose()


def test_server_admin_init_db_backfill_idempotent(tmp_path):
    """启动期幂等回填（与迁移同口径：只认 ADMIN_USER_IDS，不按 is_admin）：反复执行结果不变。"""
    from sqlalchemy import text
    from app.db.init_db import _backfill_server_admin

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/init.db", poolclass=NullPool)

    async def _run():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text(
                "INSERT INTO users (id, username, nickname, is_admin, server_admin) VALUES "
                "(11,'x','X',1,0),(12,'y','Y',0,0),(13,'z','Z',1,1)"
            ))
            await _backfill_server_admin(conn)
            await _backfill_server_admin(conn)
            return (await conn.execute(text(
                "SELECT id, server_admin FROM users WHERE id IN (11,12,13) ORDER BY id"
            ))).fetchall()

    rows = asyncio.run(_run())
    engine.sync_engine.dispose()
    # settings.admin_user_ids 默认 [1]：11/12/13 都不在名单里 → 只有预置的 13 保持 1；
    # is_admin=1 的 11 不得被授予（回归 Qwen 09-19 发现的「人人都是控制台管理员」）
    assert [tuple(r) for r in rows] == [(11, 0), (12, 0), (13, 1)]


def test_server_admin_dependency_and_console_endpoints(matrix_db):
    """require_server_admin：非 server_admin 403；server_admin 放行；不可取消最后一个。"""
    client = _matrix_client()
    assert client.get("/api/v1/admin/server/accounts", headers=_auth(2)).status_code == 403
    assert client.get("/api/v1/admin/server/accounts", headers=_auth(3)).status_code == 403
    r = client.get("/api/v1/admin/server/accounts", headers=_auth(1))
    assert r.status_code == 200, r.text
    assert {a["id"] for a in r.json()["accounts"]} == {1, 2, 3, 4, 5}
    assert r.json()["accounts"][0]["server_admin"] is True
    # 授予 B 主账号 → 之后 B 可用
    assert client.put(
        "/api/v1/admin/server/accounts/2/server-admin", headers=_auth(1), json={"enabled": True}
    ).status_code == 200
    assert client.get("/api/v1/admin/server/accounts", headers=_auth(2)).status_code == 200
    # 取消自己 → 400（防误操作锁死），且最后一个 server_admin 不可取消
    assert client.put(
        "/api/v1/admin/server/accounts/1/server-admin", headers=_auth(1), json={"enabled": False}
    ).status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 10. 插件（A2 M3 可见性过滤 + M7 越权矩阵，2026-09-20）
#
# 统一约定：插件可见性以「调用者家庭根 = tenant 键」为隔离键（A2 M3，flag 门控）。
# 隔离：私有临时库（复用 matrix_db）+ 假插件注册缓存 + 临时 USER_DIR；**不加载真实插件**
# （douyin/browser 两条端点例外：只加载插件、表建在临时库，绝不写 backend/data / 生产库）。
# ═══════════════════════════════════════════════════════════════════════════════

import io
import json
import zipfile

from app.api import marketplace as marketplace_api
from app.api import plugin_bridge as plugin_bridge_api
from app.api import plugins as plugins_api
from app.plugins import registry as plugin_registry

_BUILTIN_PLUGIN = "builtin_echo"        # source=builtin，owner 两列 NULL
_FAM_A_PLUGIN = "fam_a_local"           # 家庭 A（uid=1）安装
_FAM_B_PLUGIN = "fam_b_local"           # 家庭 B（uid=2）安装
_SERVICE_PLUGIN = "service_local"       # 存量/服务级（owner 两列 NULL）
_ALL_PLUGINS = (_BUILTIN_PLUGIN, _FAM_A_PLUGIN, _FAM_B_PLUGIN, _SERVICE_PLUGIN)


def _fake_plugin_info(name: str) -> dict:
    return {
        "name": name, "version": "0.0.1", "description": "", "author": "",
        "category": "plugin", "type": "http", "icon": "", "page": "",
        "has_page": False, "hooks": [], "permissions": [], "config": {},
        "usage": "", "display_name": "", "hook_timeout": None,
        "context_keys": [], "content": {}, "path": "",
    }


def _fake_prov(source: str, owner_user_id, owner_tenant_id) -> dict:
    return {"source": source, "source_url": None, "sha256": None,
            "consented_permissions": [], "consented_at": None,
            "owner_user_id": owner_user_id, "owner_tenant_id": owner_tenant_id}


@pytest.fixture()
def plugin_scope_env(matrix_db, monkeypatch, tmp_path):
    """M3 可见性矩阵环境：4 个假插件（内置 / 家庭A / 家庭B / 服务级）+ 临时 USER_DIR。

    直接替换 registry 的内存缓存（不 import 任何真实插件）；``sync_plugins_db`` 打桩为 no-op，
    避免卸载/安装后的重扫执行真实插件代码或写 backend/data。
    """
    loaded = {
        name: {"info": _fake_plugin_info(name), "module": None, "hooks": {},
               "actions": {}, "router": None}
        for name in _ALL_PLUGINS
    }
    prov = {
        _BUILTIN_PLUGIN: _fake_prov("builtin", None, None),
        _FAM_A_PLUGIN: _fake_prov("local", 1, 1),
        _FAM_B_PLUGIN: _fake_prov("local", 2, 2),
        _SERVICE_PLUGIN: _fake_prov("local", None, None),
    }
    user_dir = tmp_path / "user_plugins"
    for name in (_FAM_A_PLUGIN, _FAM_B_PLUGIN):
        d = user_dir / name
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (d / "index.html").write_text(f"<html>{name}</html>", encoding="utf-8")
    (user_dir / _FAM_A_PLUGIN / "evil.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(plugin_registry, "_loaded", loaded)
    monkeypatch.setattr(plugin_registry, "_enabled", {n: True for n in loaded})
    monkeypatch.setattr(plugin_registry, "_db_config", {})
    monkeypatch.setattr(plugin_registry, "_db_prov", prov)
    monkeypatch.setattr(plugin_registry, "USER_DIR", user_dir)

    async def _noop_sync():
        return None

    monkeypatch.setattr(plugin_registry, "sync_plugins_db", _noop_sync)
    return user_dir


def _plugin_client(*extra_routers) -> TestClient:
    app = FastAPI()
    for r in (plugins_api.router, plugin_bridge_api.router, marketplace_api.router, *extra_routers):
        app.include_router(r)
    return TestClient(app, raise_server_exceptions=False)


def _plugin_names(resp) -> set:
    return {i["name"] for i in resp.json()["items"]}


def _set_scope_flag(monkeypatch, on: bool) -> None:
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "plugin_user_scope", on)


def _make_plugin_zip(name: str) -> bytes:
    manifest = {"name": name, "version": "1.0.0", "description": "m7 测试包",
                "type": "http", "permissions": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("main.py", "x = 1\n")
    return buf.getvalue()


# ── M3：GET /api/v1/plugins 可见性 ─────────────────────────────────────────────

def test_p2_plugin_list_flag_off_is_global_old_behavior(plugin_scope_env):
    """M3 flag 关（默认）：列表全量，A/B/子账号/无关家庭看到同一集合（逐字节旧行为）。"""
    client = _plugin_client()
    for uid in (1, 2, 3, 5):
        r = client.get("/api/v1/plugins", headers=_auth(uid))
        assert r.status_code == 200, r.text
        assert _plugin_names(r) == set(_ALL_PLUGINS), (uid, r.text)


def test_p2_plugin_list_flag_on_scoped_by_family(plugin_scope_env, monkeypatch):
    """M3 flag 开：只看到内置 + 本家庭安装 + 服务级；跨租户安装的插件不可见（子账号随家庭根）。"""
    _set_scope_flag(monkeypatch, True)
    client = _plugin_client()
    a = _plugin_names(client.get("/api/v1/plugins", headers=_auth(1)))
    assert a == {_BUILTIN_PLUGIN, _FAM_A_PLUGIN, _SERVICE_PLUGIN}
    assert _FAM_B_PLUGIN not in a                       # 跨租户装 → 不可见
    # 子账号 3（家庭根=1）与家庭主账号同视图
    assert _plugin_names(client.get("/api/v1/plugins", headers=_auth(3))) == a
    # 家庭 B 反向看不到家庭 A 的插件
    b = _plugin_names(client.get("/api/v1/plugins", headers=_auth(2)))
    assert b == {_BUILTIN_PLUGIN, _FAM_B_PLUGIN, _SERVICE_PLUGIN}
    assert _FAM_A_PLUGIN not in b
    # 无自有插件的家庭 C 只看到内置与服务级
    assert _plugin_names(client.get("/api/v1/plugins", headers=_auth(5))) == {
        _BUILTIN_PLUGIN, _SERVICE_PLUGIN}


def test_p2_plugin_list_flag_on_tenant_resolution_failure_fail_closed(plugin_scope_env, monkeypatch):
    """M3 fail-closed：调用者家庭根解析失败 → 只保留内置 ∪ 服务级（绝不全放）。"""
    _set_scope_flag(monkeypatch, True)

    async def _boom(db, uid):
        raise RuntimeError("family root 不可用")

    monkeypatch.setattr("app.application.family_service.get_family_root_id", _boom)
    # registry 层（未给 viewer_tenant_id = 解析失败口径）
    items = plugin_registry.list_plugins(viewer_user_id=1, viewer_tenant_id=None)
    assert {i["name"] for i in items} == {_BUILTIN_PLUGIN, _SERVICE_PLUGIN}
    # 端到端同口径（resolve_viewer_tenant 内部吞异常返回 None）
    client = _plugin_client()
    assert _plugin_names(client.get("/api/v1/plugins", headers=_auth(1))) == {
        _BUILTIN_PLUGIN, _SERVICE_PLUGIN}


# ── M3：市场 installed 标记随可见集重算 ────────────────────────────────────────

def test_p2_marketplace_installed_flag_scoped(plugin_scope_env, monkeypatch):
    """M3：市场 installed 标记随可见集重算（flag 关=全量已装；flag 开=别家装→未安装）。"""
    async def _no_remote():
        return None

    monkeypatch.setattr(marketplace_api, "get_remote_index", _no_remote)
    monkeypatch.setattr(marketplace_api, "_all_items", lambda: [
        {"name": _FAM_A_PLUGIN, "description": "", "category": "plugin"},
        {"name": _FAM_B_PLUGIN, "description": "", "category": "plugin"},
    ])
    client = _plugin_client()
    # flag 关：旧行为（全量），别家插件对 A 也显示已安装
    r = client.get("/api/v1/marketplace", headers=_auth(1))
    assert {i["name"]: i["installed"] for i in r.json()["items"]} == {
        _FAM_A_PLUGIN: True, _FAM_B_PLUGIN: True}
    # flag 开：installed 随可见集重算
    _set_scope_flag(monkeypatch, True)
    r = client.get("/api/v1/marketplace", headers=_auth(1))
    assert {i["name"]: i["installed"] for i in r.json()["items"]} == {
        _FAM_A_PLUGIN: True, _FAM_B_PLUGIN: False}
    r = client.get("/api/v1/marketplace", headers=_auth(2))
    assert {i["name"]: i["installed"] for i in r.json()["items"]} == {
        _FAM_A_PLUGIN: False, _FAM_B_PLUGIN: True}


# ── M7：插件管理端点越权矩阵 ──────────────────────────────────────────────────

def test_p2_plugin_management_cross_account_gate(plugin_scope_env):
    """M7：PUT /{name} 与 DELETE /{name} 是服务器级动作——非 server_admin 403、未登录 401。"""
    client = _plugin_client()
    # 未登录 → 401
    assert client.put(f"/api/v1/plugins/{_FAM_B_PLUGIN}",
                      json={"enabled": True}).status_code == 401
    # 家庭 B 主账号（非 server_admin）→ 403（即便插件是自己装的，管理权也已收口）
    assert client.put(f"/api/v1/plugins/{_FAM_B_PLUGIN}", headers=_auth(2),
                      json={"enabled": True}).status_code == 403
    assert client.delete(f"/api/v1/plugins/{_FAM_B_PLUGIN}", headers=_auth(2)).status_code == 403
    # 子账号 3（非 server_admin）→ 403
    assert client.put(f"/api/v1/plugins/{_FAM_A_PLUGIN}", headers=_auth(3),
                      json={"enabled": True}).status_code == 403
    # server_admin（家庭 A 根=1）可改任意插件（服务器级管理，不按家庭过滤）
    r = client.put(f"/api/v1/plugins/{_FAM_B_PLUGIN}", headers=_auth(1), json={"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    # 插件不存在 → 404（先于权限判定）
    assert client.put("/api/v1/plugins/no_such_plugin", headers=_auth(1),
                      json={"enabled": True}).status_code == 404


def test_p2_plugin_uninstall_builtin_forbidden_user_plugin_ok(plugin_scope_env):
    """M7：内置插件不可卸载（400）；临时 USER_DIR 内的插件由 server_admin 卸载成功。"""
    client = _plugin_client()
    # 内置示例（仅存在于 EXAMPLE_DIR）→ 400，且不删任何文件
    assert client.delete("/api/v1/plugins/ai_diary", headers=_auth(1)).status_code == 400
    r = client.delete(f"/api/v1/plugins/{_FAM_B_PLUGIN}", headers=_auth(1))
    assert r.status_code == 200, r.text
    assert r.json()["uninstalled"] is True
    assert not (plugin_scope_env / _FAM_B_PLUGIN).exists()  # 只删临时 USER_DIR
    # 已卸载插件再读 → 仍由 fake 缓存返回（本用例只验证删除语义，不涉重扫）
    assert (plugin_scope_env / _FAM_A_PLUGIN).is_dir()      # 未误删别家插件目录


def test_p2_plugin_install_owner_gate_and_temp_user_dir(plugin_scope_env, monkeypatch):
    """M7：本地 zip 安装仅 server_admin；写盘只落临时 USER_DIR（不碰 backend/data）。"""
    client = _plugin_client()
    data = _make_plugin_zip("m7_local_plugin")
    # 非 server_admin → 403（在读包/解压前就被拦）
    assert client.post("/api/v1/plugins/install", headers=_auth(2),
                       files={"file": ("p.zip", data, "application/zip")}).status_code == 403
    # 重扫打桩：只登记临时目录里已有的插件（不 import 真实插件、不执行插件代码）
    loaded = plugin_registry._loaded

    async def _fake_sync():
        for d in plugin_scope_env.iterdir():
            mf = d / "manifest.json"
            if mf.is_file():
                nm = json.loads(mf.read_text(encoding="utf-8")).get("name")
                loaded.setdefault(nm, {"info": _fake_plugin_info(nm), "module": None,
                                       "hooks": {}, "actions": {}, "router": None})

    monkeypatch.setattr(plugin_registry, "sync_plugins_db", _fake_sync)
    r = client.post("/api/v1/plugins/install", headers=_auth(1),
                    files={"file": ("p.zip", data, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "m7_local_plugin"
    assert (plugin_scope_env / "m7_local_plugin" / "manifest.json").is_file()


def test_p2_plugin_bridge_login_and_unknown(plugin_scope_env, monkeypatch):
    """M7：bridge 强制登录（401）；插件不存在 404；已知插件放行（现状无归属校验，服务器级）。"""
    async def _fake_dispatch(*a, **k):
        return {"ok": True}

    monkeypatch.setattr(plugin_bridge_api, "dispatch", _fake_dispatch)
    client = _plugin_client()
    body = {"api": "store.get", "params": {"key": "k"}}
    assert client.post(f"/api/v1/plugins/{_FAM_B_PLUGIN}/bridge", json=body).status_code == 401
    assert client.post("/api/v1/plugins/no_such_plugin/bridge", headers=_auth(1),
                       json=body).status_code == 404
    # 现状：bridge 无插件归属校验 → 别家装的插件同样放行（M3 只收敛列表可见性）
    assert client.post(f"/api/v1/plugins/{_FAM_B_PLUGIN}/bridge", headers=_auth(1),
                       json=body).status_code == 200


def test_p2_plugin_page_hosting(plugin_scope_env):
    """M7：页面托管——本家插件 200、未知 404、可执行扩展名 404；跨租户现状无归属校验。"""
    client = _plugin_client()
    assert client.get(f"/api/v1/plugins/{_FAM_A_PLUGIN}/page/index.html",
                      headers=_auth(1)).status_code == 200
    assert client.get("/api/v1/plugins/no_such_plugin/page/index.html",
                      headers=_auth(1)).status_code == 404
    assert client.get(f"/api/v1/plugins/{_FAM_A_PLUGIN}/page/evil.py",
                      headers=_auth(1)).status_code == 404
    assert client.get(f"/api/v1/plugins/{_FAM_A_PLUGIN}/page/index.html").status_code == 401
    # 现状：页面托管无插件归属校验 → 别家插件页面同样 200（本批未覆盖，报告登记为已知缺口）
    assert client.get(f"/api/v1/plugins/{_FAM_B_PLUGIN}/page/index.html",
                      headers=_auth(1)).status_code == 200


# ── M7：渠道插件端点（douyin / browser）跨租户 404 / 只见本账号 ────────────────

@pytest.fixture()
def douyin_matrix_env(matrix_db):
    """装载 douyin_mcp（真实插件）并在临时库建其自有表；返回 (client, module)。"""
    from app.plugins.plugin_base import plugin_metadata

    assert plugin_registry.load_plugin_dir(plugin_registry.EXAMPLE_DIR / "douyin_mcp") is not None
    mod = sys.modules.get("ai_plugin_douyin_mcp")
    assert mod is not None, "douyin_mcp 应可加载"

    async def _mk():
        async with matrix_db() as db:
            conn = await db.connection()
            await conn.run_sync(plugin_metadata.create_all)
            await db.commit()

    asyncio.run(_mk())
    router = plugin_registry._loaded["douyin_mcp"].get("router")
    client = _plugin_client(router)
    yield client, mod
    plugin_registry._loaded.pop("douyin_mcp", None)
    plugin_registry._enabled.pop("douyin_mcp", None)


def _seed_douyin_pending(factory, rows) -> None:
    import douyin_models

    async def _go():
        async with factory() as db:
            for tid, kind, status in rows:
                db.add(douyin_models.DouyinPending(
                    tenant_id=tid, kind=kind, status=status, title="t"))
            await db.commit()

    asyncio.run(_go())


def test_p2_douyin_pending_and_confirm_cross_tenant_404(douyin_matrix_env, matrix_db, monkeypatch):
    """M7：抖音 /pending 只列本租户；跨租户 /confirm/{id} → 404（不泄漏存在性），本租户 200。"""
    client, mod = douyin_matrix_env
    _seed_douyin_pending(matrix_db, [(1, "image_post", "pending"), (2, "image_post", "pending")])
    from datetime import datetime as _dt, timedelta as _td
    # _random_execute_at 非法分钟缺陷已修复（randint 上界 59 + 回归测试 test_douyin_quiet_hours.py）；
    # 这里固定执行时间只为消除随机性，与本批租户口径无关。
    monkeypatch.setattr(mod, "_random_execute_at",
                        lambda: _dt(2030, 1, 1, 12, 0, 0) + _td(minutes=30))

    a = client.get("/api/v1/plugins/douyin_mcp/pending", headers=_auth(1)).json()["items"]
    b = client.get("/api/v1/plugins/douyin_mcp/pending", headers=_auth(2)).json()["items"]
    assert len(a) == 1 and len(b) == 1
    assert a[0]["id"] != b[0]["id"]                     # 各自只看自己的行
    # 家庭 A 的账号去确认家庭 B 的任务 → 404
    assert client.post(f"/api/v1/plugins/douyin_mcp/confirm/{b[0]['id']}",
                       headers=_auth(1)).status_code == 404
    assert client.post(f"/api/v1/plugins/douyin_mcp/confirm/{a[0]['id']}",
                       headers=_auth(2)).status_code == 404
    # 本租户确认 → 200
    r = client.post(f"/api/v1/plugins/douyin_mcp/confirm/{a[0]['id']}", headers=_auth(1))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


@pytest.fixture()
def browser_matrix_env(matrix_db):
    """装载 browser_mcp（真实插件）；快照表在主 metadata（临时库已建），返回 client。"""
    assert plugin_registry.load_plugin_dir(plugin_registry.EXAMPLE_DIR / "browser_mcp") is not None
    mod = sys.modules.get("ai_plugin_browser_mcp")
    assert mod is not None, "browser_mcp 应可加载"
    mod._ensure_done = True   # 表已由临时库 create_all 建好，跳过插件内联 DDL
    router = plugin_registry._loaded["browser_mcp"].get("router")
    client = _plugin_client(router)
    yield client
    plugin_registry._loaded.pop("browser_mcp", None)
    plugin_registry._enabled.pop("browser_mcp", None)


def test_p2_browser_latest_only_own_account(browser_matrix_env, matrix_db):
    """M7：browser /latest 只见本账号快照（跨账号不可见）。"""
    from app.models.user import BrowserSnapshot

    async def _seed():
        async with matrix_db() as db:
            db.add(BrowserSnapshot(user_id=1, url="https://a.example/1", domain="a.example",
                                   title="A1", text="t"))
            db.add(BrowserSnapshot(user_id=2, url="https://b.example/1", domain="b.example",
                                   title="B1", text="t"))
            await db.commit()

    asyncio.run(_seed())
    r = browser_matrix_env.get("/api/v1/plugins/browser_mcp/latest", headers=_auth(1))
    assert r.status_code == 200, r.text
    urls = [s["url"] for s in r.json()["snapshots"]]
    assert urls == ["https://a.example/1"]
    r2 = browser_matrix_env.get("/api/v1/plugins/browser_mcp/latest", headers=_auth(2))
    assert [s["url"] for s in r2.json()["snapshots"]] == ["https://b.example/1"]

