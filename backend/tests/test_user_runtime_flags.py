# -*- coding: utf-8 -*-
"""A5 用户级开关覆盖（user_runtime_flags）测试（2026-09-19）。

覆盖：
- ``flag_service.resolve_flag`` 解析优先级：server_locked → 全局；该账号有覆盖 → 用户值；
  无覆盖 → 全局值；表缺失/读失败 → fail-open 回全局（不打挂业务链）；
- ``set_user_flag``：写覆盖不改进程级 AGENT_FLAGS；未知键 / 非 bool 键 / 空 user_id 拒绝；upsert 幂等；
- API 口径：GET 带 scope + user_enabled（无覆盖 = null）；PUT 用户语义键写覆盖（全局不变、返回
  scope='user'）；PUT 服务器级键写全局（scope='server'）；锁定 / 自助关 → 403（两条路径都不放松）；
- 接线：user_facts 细槽开关按账号取值（有覆盖 / 无覆盖两种账号），含 user_id 透传读路径；
- 迁移自检：单头 + 哨兵 + 临时库 upgrade head 建表 / downgrade 可逆 / 再 upgrade。

全程临时 SQLite（create_all 或真实 alembic 临时库），**不连/不写生产库**（生产库只读）。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import system as system_api
from app.auth.deps import get_current_user_id

USER_A = 11
USER_B = 22

# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库），打 slow 标记。
pytestmark = pytest.mark.slow


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上（含早绑定引用）。

    与 test_admin_console_p2._patch_session_factories 同法：``app.db.database`` 是接缝，但
    ``admin_audit_service`` / ``user_facts`` 等模块在 import 期就 from-import 早绑定，需逐个换掉
    ——否则审计/记忆写会落到真实库（本用例硬红线：不写生产数据）。
    """
    import sys

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


@pytest.fixture(autouse=True)
def _admin_for_test_users(monkeypatch):
    """放开本文件测试账号的 _require_admin 判定（不依赖库里 is_admin，避免连真实库）。"""
    from app.application import system as system_svc

    async def _is_admin(uid: int) -> bool:
        return int(uid) in (USER_A, USER_B)

    monkeypatch.setattr(system_svc, "is_admin_user", _is_admin)


@pytest.fixture()
def flag_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库（不触碰 backend/data）：create_all 全模型 + 替换全部异步工厂。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(str(tmp_path), 'flags.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    yield factory
    asyncio.run(engine.dispose())


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _flags_of(r) -> dict:
    assert r.status_code == 200, r.text
    return {f["key"]: f for f in r.json()["flags"]}


def _seed_user(factory, user_id: int):
    async def _run():
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=user_id, username=f"u{user_id}", nickname="测"))
            await db.commit()
    asyncio.run(_run())


def _put(c, key: str, enabled: bool):
    return c.put(f"/api/v1/system/feature-flags/{key}", json={"enabled": enabled})


# ══════════════════════════ 1. resolve_flag 解析链 ══════════════════════════

def test_resolve_flag_priority(flag_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service

    monkeypatch.setitem(AGENT_FLAGS, "user_fact_health", False)
    # ① 无覆盖、未锁定 → 全局值
    assert asyncio.run(flag_service.resolve_flag("user_fact_health", USER_A)) is False
    monkeypatch.setitem(AGENT_FLAGS, "user_fact_health", True)
    assert asyncio.run(flag_service.resolve_flag("user_fact_health", USER_A)) is True
    # ② 该账号有覆盖 → 用户值；其它账号仍走全局（多账号不串扰）
    assert asyncio.run(flag_service.set_user_flag("user_fact_health", USER_A, False)) is True
    assert asyncio.run(flag_service.resolve_flag("user_fact_health", USER_A)) is False
    assert asyncio.run(flag_service.resolve_flag("user_fact_health", USER_B)) is True
    # ③ server_locked → 忽略用户覆盖，直接回全局值
    asyncio.run(flag_service.set_flag_policy("user_fact_health", server_locked=True))
    assert asyncio.run(flag_service.resolve_flag("user_fact_health", USER_A)) is True
    # 解锁 → 用户覆盖重新生效
    asyncio.run(flag_service.set_flag_policy("user_fact_health", server_locked=False))
    assert asyncio.run(flag_service.resolve_flag("user_fact_health", USER_A)) is False
    # 缺 user_id → 全局值
    assert asyncio.run(flag_service.resolve_flag("user_fact_health")) is True


def test_resolve_flag_fail_open_on_db_error(monkeypatch):
    """表缺失/读失败 → fail-open 回全局值，绝不抛错打挂业务链。"""
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service

    monkeypatch.setitem(AGENT_FLAGS, "global_user_facts", True)

    class _BrokenFactory:
        def __call__(self):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr("app.db.database.async_session_factory", _BrokenFactory())
    assert asyncio.run(flag_service.resolve_flag("global_user_facts", USER_A)) is True
    assert asyncio.run(flag_service.get_user_flags(USER_A)) == {}
    assert asyncio.run(flag_service.get_user_flags(None)) == {}
    assert asyncio.run(flag_service.set_user_flag("global_user_facts", USER_A, False)) is False


def test_get_user_flags_reads_only_that_account(flag_db):
    from app.application import flag_service

    asyncio.run(flag_service.set_user_flag("user_fact_location", USER_A, True))
    asyncio.run(flag_service.set_user_flag("global_user_facts", USER_B, True))
    assert asyncio.run(flag_service.get_user_flags(USER_A)) == {"user_fact_location": True}
    assert asyncio.run(flag_service.get_user_flags(USER_B)) == {"global_user_facts": True}


# ══════════════════════════ 2. set_user_flag 语义 ══════════════════════════

def test_set_user_flag_semantics(flag_db):
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service
    from app.models.config import UserRuntimeFlag

    saved = AGENT_FLAGS["user_fact_location"]
    # 写覆盖：成功且**不改进程级 AGENT_FLAGS**
    assert asyncio.run(flag_service.set_user_flag("user_fact_location", USER_A, True)) is True
    assert AGENT_FLAGS["user_fact_location"] == saved
    # 未知键 / 非 bool 键 / 空 user_id → 拒绝
    assert asyncio.run(flag_service.set_user_flag("not_a_flag", USER_A, True)) is False
    assert asyncio.run(flag_service.set_user_flag("domain_event_retention_days", USER_A, True)) is False
    assert asyncio.run(flag_service.set_user_flag("user_fact_location", 0, True)) is False
    assert asyncio.run(flag_service.set_user_flag("user_fact_location", None, True)) is False

    async def _rows():
        async with flag_db() as db:
            return (await db.execute(select(UserRuntimeFlag))).scalars().all()

    rows = asyncio.run(_rows())
    assert [(r.user_id, r.key, r.enabled) for r in rows] == [(USER_A, "user_fact_location", True)]
    # upsert：同账号同键再写 → 仍一行，值更新（不新增行）
    assert asyncio.run(flag_service.set_user_flag("user_fact_location", USER_A, False)) is True
    rows = asyncio.run(_rows())
    assert len(rows) == 1 and rows[0].enabled is False


# ══════════════════════════ 3. API 口径 ══════════════════════════

def test_api_get_flags_scope_and_user_enabled(flag_db):
    from app.application import flag_service

    asyncio.run(flag_service.set_user_flag("user_fact_health", USER_A, True))
    flags = _flags_of(_client(USER_A).get("/api/v1/system/feature-flags"))
    # 用户语义键 + 有覆盖 → scope=user / user_enabled=覆盖值
    assert flags["user_fact_health"]["scope"] == "user"
    assert flags["user_fact_health"]["user_enabled"] is True
    # 用户语义键 + 无覆盖 → user_enabled=null（不是 False）
    assert flags["user_fact_location"]["scope"] == "user"
    assert flags["user_fact_location"]["user_enabled"] is None
    # 服务器级键 → scope=server / user_enabled=null
    assert flags["agent_loop_chat"]["scope"] == "server"
    assert flags["agent_loop_chat"]["user_enabled"] is None
    # 既有字段语义不变
    for k in ("key", "enabled", "value", "type", "source"):
        assert k in flags["agent_loop_chat"]
    # 另一账号读不到 A 的覆盖（多账号隔离）
    flags_b = _flags_of(_client(USER_B).get("/api/v1/system/feature-flags"))
    assert flags_b["user_fact_health"]["scope"] == "user"
    assert flags_b["user_fact_health"]["user_enabled"] is None


def test_api_put_user_scoped_writes_override(flag_db):
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service
    from app.models.config import RuntimeFlag

    saved = AGENT_FLAGS["user_fact_relationship"]
    r = _put(_client(USER_A), "user_fact_relationship", True)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "key": "user_fact_relationship",
                        "enabled": True, "scope": "user"}
    # 全局进程级值不变；只写该账号覆盖行
    assert AGENT_FLAGS["user_fact_relationship"] == saved
    assert asyncio.run(flag_service.resolve_flag("user_fact_relationship", USER_A)) is True
    assert asyncio.run(flag_service.resolve_flag("user_fact_relationship", USER_B)) == saved
    assert asyncio.run(flag_service.get_user_flags(USER_B)) == {}

    async def _global_keys():
        async with flag_db() as db:
            return set((await db.execute(select(RuntimeFlag.key))).scalars().all())

    assert "user_fact_relationship" not in asyncio.run(_global_keys())  # 没写全局表


def test_api_put_server_scoped_writes_global(flag_db):
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service
    from app.models.config import RuntimeFlag

    saved = AGENT_FLAGS["agent_loop_chat"]
    try:
        r = _put(_client(USER_A), "agent_loop_chat", not saved)
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "server"
        assert r.json()["enabled"] is (not saved)
        assert AGENT_FLAGS["agent_loop_chat"] is (not saved)  # 保持现状写全局

        async def _global_keys():
            async with flag_db() as db:
                return set((await db.execute(select(RuntimeFlag.key))).scalars().all())

        assert "agent_loop_chat" in asyncio.run(_global_keys())
        assert asyncio.run(flag_service.get_user_flags(USER_A)) == {}
    finally:
        AGENT_FLAGS["agent_loop_chat"] = saved


def test_api_put_policy_403_unchanged(flag_db):
    """锁定 / self_service=0 → 403（两条路径都不得放松）；且不落覆盖、不改全局。"""
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service

    c = _client(USER_A)
    # 用户语义键：server_locked → 403
    asyncio.run(flag_service.set_flag_policy("user_fact_health", server_locked=True))
    assert _put(c, "user_fact_health", True).status_code == 403
    # 用户语义键：self_service=0 → 403
    asyncio.run(flag_service.set_flag_policy("user_fact_health", server_locked=False, self_service=False))
    assert _put(c, "user_fact_health", True).status_code == 403
    assert asyncio.run(flag_service.get_user_flags(USER_A)) == {}
    # 服务器级键：策略判定同样生效（不因分流放松）
    asyncio.run(flag_service.set_flag_policy("agent_loop_chat", server_locked=True))
    saved = AGENT_FLAGS["agent_loop_chat"]
    assert _put(c, "agent_loop_chat", not saved).status_code == 403
    assert AGENT_FLAGS["agent_loop_chat"] == saved
    # 未知键 / 非 bool 键仍 404（类型防护不被绕过）
    asyncio.run(flag_service.set_flag_policy("agent_loop_chat", server_locked=False))
    assert _put(c, "not_a_flag", True).status_code == 404
    assert _put(c, "domain_event_retention_days", True).status_code == 404


# ══════════════════════════ 4. 接线：按账号取值（含 user_id 透传）══════════════════════════

def test_user_facts_slot_gate_per_account(flag_db, monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service
    from app.memory import user_facts as uf

    for k in ("global_user_facts", "user_fact_location", "user_fact_relationship",
              "user_fact_health", "user_fact_job", "user_fact_living",
              "user_fact_goal_state", "user_current_location_share"):
        monkeypatch.setitem(AGENT_FLAGS, k, False)
    asyncio.run(flag_service.set_user_flag("user_fact_health", USER_A, True))
    asyncio.run(flag_service.set_user_flag("user_current_location_share", USER_A, False))
    # A 覆盖 health 开 → A True / B False（同一进程、同一全局值）
    assert asyncio.run(uf.user_fact_slot_enabled_for("health", USER_A)) is True
    assert asyncio.run(uf.user_fact_slot_enabled_for("health", USER_B)) is False
    assert asyncio.run(uf.enabled_user_fact_slots_for(USER_A)) == ["health"]
    assert asyncio.run(uf.enabled_user_fact_slots_for(USER_B)) == []
    # 非用户级细槽键（job/living/goal_state）本批仍服务器级：全局开 → 两账号都开
    monkeypatch.setitem(AGENT_FLAGS, "user_fact_job", True)
    assert asyncio.run(uf.user_fact_slot_enabled_for("job", USER_A)) is True
    assert asyncio.run(uf.user_fact_slot_enabled_for("job", USER_B)) is True
    # 红线：敏感槽不吃总闸旁路（总闸开也只显式开才生效）
    monkeypatch.setitem(AGENT_FLAGS, "global_user_facts", True)
    assert asyncio.run(uf.user_fact_slot_enabled_for("relationship", USER_B)) is False
    assert asyncio.run(uf.user_fact_slot_enabled_for("health", USER_B)) is False
    # 默认口径：无覆盖账号 = 全局值（行为与改动前一致）
    monkeypatch.setitem(AGENT_FLAGS, "user_fact_location", True)
    assert asyncio.run(uf.user_fact_slot_enabled_for("location", USER_B)) is True


def test_user_facts_read_paths_per_account(flag_db, monkeypatch):
    """get_shared_user_facts / get_authoritative_user_location 必须按 user_id 取槽（不冒充 per-user）。"""
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service
    from app.memory import user_facts as uf

    for k in ("global_user_facts", "user_fact_location", "user_fact_relationship",
              "user_fact_health", "user_fact_job", "user_fact_living",
              "user_fact_goal_state", "user_current_location_share"):
        monkeypatch.setitem(AGENT_FLAGS, k, False)
    _seed_user(flag_db, USER_A)
    _seed_user(flag_db, USER_B)
    asyncio.run(uf.upsert_user_fact(USER_A, "location", "湛江市", source="manual"))
    asyncio.run(uf.upsert_user_fact(USER_B, "location", "长沙市", source="manual"))
    # A：location 写侧开 + 共享开；B：共享显式关（覆盖全局默认）
    asyncio.run(flag_service.set_user_flag("user_fact_location", USER_A, True))
    asyncio.run(flag_service.set_user_flag("user_current_location_share", USER_A, True))
    asyncio.run(flag_service.set_user_flag("user_current_location_share", USER_B, False))

    shared_a = asyncio.run(uf.get_shared_user_facts(USER_A))
    assert shared_a.get("location") == "湛江市"
    assert asyncio.run(uf.get_authoritative_user_location(USER_A)) == "湛江市"
    assert "location" not in asyncio.run(uf.get_shared_user_facts(USER_B))
    assert asyncio.run(uf.get_authoritative_user_location(USER_B)) is None


# ══════════════════════════ 5. 迁移自检 ══════════════════════════

def test_migration_single_head_sentinel_and_reversible(monkeypatch, tmp_path):
    """单头 + 哨兵在位；临时库 upgrade head 建表 / downgrade 可逆 / 再 upgrade。"""
    import app.config as app_config
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    from app.db import migrate

    cfg = migrate._alembic_config()
    script = ScriptDirectory.from_config(cfg)
    # A8（2026-09-20）新增迁移 b5c6d7e8f9a0 后 head 前移：不再硬编码 head 值
    # （硬编码会让每个新迁移都误伤本用例），改为「单头 + 本修订在 head 的祖先链上」，
    # 与 tests/test_admin_console_p2.py 的跟随单头写法一致。
    heads = script.get_heads()
    assert len(heads) == 1, heads
    assert script.get_current_head() == heads[0]
    revs = [r.revision for r in script.walk_revisions(base="base", head=heads[0])]
    assert "a5b6c7d8e9f0" in revs
    assert "user_runtime_flags" in migrate._migration_chain_tables(cfg)
    assert ("user_runtime_flags", "user_id") in migrate._CURRENT_SCHEMA_SENTINELS

    db_path = os.path.join(str(tmp_path), "mig.db")
    monkeypatch.setattr(app_config.settings, "database_url", "sqlite+aiosqlite:///" + db_path)

    command.upgrade(cfg, "head")
    eng = create_engine("sqlite:///" + db_path)
    insp = inspect(eng)
    assert insp.has_table("user_runtime_flags")
    cols = {c["name"] for c in insp.get_columns("user_runtime_flags")}
    assert {"user_id", "key", "enabled", "updated_at"} <= cols
    pk = set(insp.get_pk_constraint("user_runtime_flags")["constrained_columns"])
    assert pk == {"user_id", "key"}
    eng.dispose()

    command.downgrade(cfg, "f7a8b9c0d1e2")  # 只回退本 revision
    eng2 = create_engine("sqlite:///" + db_path)
    assert not inspect(eng2).has_table("user_runtime_flags")
    eng2.dispose()

    command.upgrade(cfg, "head")
    eng3 = create_engine("sqlite:///" + db_path)
    assert inspect(eng3).has_table("user_runtime_flags")
    eng3.dispose()
