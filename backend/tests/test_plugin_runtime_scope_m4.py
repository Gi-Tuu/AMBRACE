# -*- coding: utf-8 -*-
"""A2 插件归户 · 批次 C-2（M4 运行面按账号过滤）测试（2026-09-20）。

覆盖（与派单 §四 一一对应）：

- **flag 关（对照）**：hook 分发 / 工具登记 / prompt 注入 / 桥 / 页面 / sdk 断言 /
  proactive 配对 / 未登记类别——逐条与改前一致（不判归属）；
- **flag 开**：①跨租户装的插件不进本租户 hook（内置仍进）；②未带 caller 的调用点 →
  非内置插件不分发（fail-closed）；③``sync_plugin_tools`` 过滤「可见 + enabled」；
  ④桥 / 页面跨租户 404；⑤sdk 越权（自报他人 user_id / 取他人角色）→ PermissionError；
  ⑥proactive 自报 user_id 与角色归属不符 → 候选被丢弃；⑦未登记类别不再直通（走通用闸）；
- **_sdk_ctx 并发回归**（不受 flag 门控）：两个协程同时跑不同插件的 hook，身份不串；
- **同步 hook 线程内身份**（always-on）：同步 hook 丢线程池执行时仍能看到自己的插件身份。

隔离：全程私有临时库（pytest ``tmp_path``）+ 把 app.* 各模块的 ``async_session_factory``
换成私有工厂（同 ``test_plugin_admin_scope_a._patch_session_factories`` 口径）；
``registry.USER_DIR`` 指向 ``tmp_path``；插件一律用内存假件，**不连生产库、不写 backend/data**。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import json
import sys

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.agent.loop import AGENT_FLAGS
from app.api import plugin_bridge as bridge_api
from app.api import plugins as plugins_api
from app.auth.config import create_token
from app.plugins import registry, sdk

pytestmark = pytest.mark.slow

# 账号：1=家庭根；2=1 的子账号（家庭根同为 1）；3=另一个家庭根。
ROOT_UID, SUB_UID, OTHER_UID = 1, 2, 3
# 角色：11 属账号 1；21 属账号 3。
CHAR_MINE, CHAR_OTHER = 11, 21
M4_FLAG = "plugin_runtime_scope"

BUILTIN, MINE, OTHER, SERVICE = "builtin_x", "mine_local", "other_local", "service_local"
ALL4 = (BUILTIN, MINE, OTHER, SERVICE)


# ---------------------------------------------------------------- 私有临时库

def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上（含早绑定引用）。"""
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
def m4_db(monkeypatch, tmp_path):
    """私有临时库：家庭根 1（子账号 2）+ 家庭根 3 + 分属两家的两个角色。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm4.db'}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册主 metadata
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                User(id=ROOT_UID, username="root", nickname="家庭根", is_admin=True),
                User(id=SUB_UID, username="sub", nickname="子账号", parent_id=ROOT_UID, is_admin=False),
                User(id=OTHER_UID, username="other", nickname="别家根", is_admin=True),
            ])
            db.add_all([
                AICharacter(id=CHAR_MINE, user_id=ROOT_UID, name="mine-char"),
                AICharacter(id=CHAR_OTHER, user_id=OTHER_UID, name="other-char"),
            ])
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    yield factory
    asyncio.run(engine.dispose())


# ---------------------------------------------------------------- 假插件装配

def _info(name: str, *, type_: str = "http", hooks=(), permissions=(), config=None) -> dict:
    return {
        "name": name, "version": "0.0.1", "description": "", "author": "",
        "category": "plugin", "type": type_, "icon": "", "page": "",
        "has_page": False, "hooks": list(hooks), "permissions": list(permissions),
        "config": dict(config or {}), "usage": "", "display_name": "",
        "hook_timeout": None, "context_keys": [], "content": {}, "path": "",
    }


def _spec(source: str = "local", owner_user_id=None, owner_tenant_id=None, enabled: bool = True,
          **kw) -> dict:
    return {"source": source, "owner_user_id": owner_user_id,
            "owner_tenant_id": owner_tenant_id, "enabled": enabled, **kw}


def _install(monkeypatch, specs: dict) -> dict:
    """用内存假件替换 registry 的四张缓存表（不 import 真实插件、不碰磁盘）。"""
    loaded, enabled, cfg, prov = {}, {}, {}, {}
    for name, s in specs.items():
        hooks = s.get("hooks") or {}
        loaded[name] = {
            "info": _info(name, type_=s.get("type_", "http"), hooks=list(hooks.keys()),
                          permissions=s.get("permissions") or [], config=s.get("config") or {}),
            "module": None, "hooks": hooks, "actions": s.get("actions") or {}, "router": None,
        }
        enabled[name] = bool(s.get("enabled", True))
        cfg[name] = dict(s.get("db_config") or {})
        prov[name] = {"source": s.get("source", "local"),
                      "owner_user_id": s.get("owner_user_id"),
                      "owner_tenant_id": s.get("owner_tenant_id")}
    monkeypatch.setattr(registry, "_loaded", loaded)
    monkeypatch.setattr(registry, "_enabled", enabled)
    monkeypatch.setattr(registry, "_db_config", cfg)
    monkeypatch.setattr(registry, "_db_prov", prov)
    return loaded


def _four(monkeypatch, **over) -> dict:
    """四个标准假插件：内置 / 家庭 1 装 / 家庭 3 装 / 服务级（owner 为空）。"""
    specs = {
        BUILTIN: _spec(source="builtin"),
        MINE: _spec(owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID),
        OTHER: _spec(owner_user_id=OTHER_UID, owner_tenant_id=OTHER_UID),
        SERVICE: _spec(),
    }
    specs.update(over)
    return _install(monkeypatch, specs)


def _set_flag(monkeypatch, on: bool) -> None:
    monkeypatch.setitem(AGENT_FLAGS, M4_FLAG, on)


@pytest.fixture(autouse=True)
def _clean_m4_state():
    """每个用例前后清 M4 运行面缓存 / sdk 归属缓存，并复原全局工具注册表。"""
    from app.agent import tools as tools_mod

    snapshot = dict(tools_mod._REGISTRY)
    registry.clear_runtime_scope_caches()
    sdk.clear_sdk_owner_cache()
    yield
    registry.clear_runtime_scope_caches()
    sdk.clear_sdk_owner_cache()
    tools_mod._REGISTRY.clear()
    tools_mod._REGISTRY.update(snapshot)


def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(plugins_api.router)
    app.include_router(bridge_api.router)
    return TestClient(app, raise_server_exceptions=False)


# ================================================================ 1) hook 分发

@pytest.fixture()
def hook_env(monkeypatch, m4_db):
    """四个假插件都注册 context_inject；记录每次回调的插件身份与 caller 上下文。"""
    calls: list[dict] = []

    def _make(name):
        async def _hook(ctx):
            calls.append({"plugin": name, "ctx": registry.current_sdk_context()})
            return name  # collect 路径据此收集（run_hook 忽略返回值）
        return _hook

    specs = {}
    for name, spec in {
        BUILTIN: _spec(source="builtin"),
        MINE: _spec(owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID),
        OTHER: _spec(owner_user_id=OTHER_UID, owner_tenant_id=OTHER_UID),
        SERVICE: _spec(),
    }.items():
        spec["hooks"] = {"context_inject": [_make(name)]}
        specs[name] = spec
    _install(monkeypatch, specs)
    return calls


def test_flag关_hook全量分发逐字节旧行为(hook_env, monkeypatch):
    _set_flag(monkeypatch, False)
    asyncio.run(registry.run_hook("context_inject", {}, user_id=ROOT_UID))
    assert [c["plugin"] for c in hook_env] == list(ALL4)


def test_flag开_hook只分发给可见插件(hook_env, monkeypatch):
    _set_flag(monkeypatch, True)
    asyncio.run(registry.run_hook("context_inject", {}, user_id=ROOT_UID))
    assert [c["plugin"] for c in hook_env] == [BUILTIN, MINE, SERVICE]  # 别家装的不分发
    # caller 已写进插件上下文（供 sdk 归属断言）：tenant_id = 调用者家庭根
    assert all(c["ctx"]["user_id"] == ROOT_UID for c in hook_env)
    assert all(c["ctx"]["tenant_id"] == ROOT_UID for c in hook_env)

    hook_env.clear()
    asyncio.run(registry.run_hook("context_inject", {}, user_id=OTHER_UID))
    assert [c["plugin"] for c in hook_env] == [BUILTIN, OTHER, SERVICE]

    hook_env.clear()
    # 子账号 2 随家庭根 1（不是自己的 uid）
    asyncio.run(registry.run_hook("context_inject", {}, user_id=SUB_UID))
    assert [c["plugin"] for c in hook_env] == [BUILTIN, MINE, SERVICE]
    assert [c["ctx"]["tenant_id"] for c in hook_env] == [ROOT_UID] * 3

    hook_env.clear()
    # 只给 tenant_id（异步入口已解析家庭根）同样生效
    asyncio.run(registry.run_hook("context_inject", {}, tenant_id=OTHER_UID))
    assert [c["plugin"] for c in hook_env] == [BUILTIN, OTHER, SERVICE]


def test_flag开_无caller时非内置不分发_fail_closed(hook_env, monkeypatch):
    _set_flag(monkeypatch, True)
    registry.clear_runtime_scope_caches()
    asyncio.run(registry.run_hook("context_inject", {}))
    assert [c["plugin"] for c in hook_env] == [BUILTIN]  # 内置继续放行
    assert registry._warned_no_caller, "拿不到 caller 应告警一次（带调用点标识）"


def test_flag开_collect同样按可见性过滤(hook_env, monkeypatch):
    _set_flag(monkeypatch, True)
    out = asyncio.run(registry.run_hook_collect("context_inject", {}, user_id=ROOT_UID))
    assert [r["plugin"] for r in out] == [BUILTIN, MINE, SERVICE]


# ================================================================ 2) 工具登记

def test_flag关_工具全量登记含未启用(monkeypatch):
    from app.agent import tools as tools_mod

    _install(monkeypatch, {
        BUILTIN: _spec(source="builtin", actions={"a": 1}),
        MINE: _spec(owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID, actions={"a": 1}),
        OTHER: _spec(owner_user_id=OTHER_UID, owner_tenant_id=OTHER_UID, actions={"a": 1}),
        SERVICE: _spec(actions={"a": 1}, enabled=False),
    })
    _set_flag(monkeypatch, False)
    assert tools_mod.sync_plugin_tools() == 4  # 旧行为：不判 enabled、不判可见
    assert {f"{n}.a" for n in ALL4} <= set(tools_mod._REGISTRY)


def test_flag开_工具只登记可见且enabled(monkeypatch):
    from app.agent import tools as tools_mod

    _install(monkeypatch, {
        BUILTIN: _spec(source="builtin", actions={"a": 1}),
        MINE: _spec(owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID, actions={"a": 1}),
        OTHER: _spec(owner_user_id=OTHER_UID, owner_tenant_id=OTHER_UID, actions={"a": 1}),
        SERVICE: _spec(actions={"a": 1}, enabled=False),
    })
    _set_flag(monkeypatch, True)
    # 启动期调用点拿不到 caller → fail-closed：只留「内置 ∪ 服务级」∩ enabled = builtin_x
    assert tools_mod.sync_plugin_tools() == 1
    assert f"{BUILTIN}.a" in tools_mod._REGISTRY
    assert f"{MINE}.a" not in tools_mod._REGISTRY
    assert f"{OTHER}.a" not in tools_mod._REGISTRY
    assert f"{SERVICE}.a" not in tools_mod._REGISTRY  # enabled=False 被过滤

    tools_mod._REGISTRY.pop(f"{BUILTIN}.a", None)
    # 异步入口已解析出家庭根时显式传入 → 内置 + 本家庭（服务级仍受 enabled 限制）
    assert tools_mod.sync_plugin_tools(viewer_user_id=ROOT_UID, viewer_tenant_id=ROOT_UID) == 2


# ================================================================ 3) prompt 注入

@pytest.fixture()
def prompt_env(monkeypatch, m4_db):
    _install(monkeypatch, {
        "builtin_prompt": _spec(source="builtin", type_="prompt",
                                config={"prompt": {"trigger": ["天气"], "systemPrompt": "内置技能"}}),
        "other_prompt": _spec(owner_user_id=OTHER_UID, owner_tenant_id=OTHER_UID, type_="prompt",
                              config={"prompt": {"trigger": ["天气"], "systemPrompt": "别家技能"}}),
    })
    return None


def test_flag关_prompt注入不看归属(prompt_env, monkeypatch):
    from app.plugins.config_hooks import inject_prompt_skill

    _set_flag(monkeypatch, False)
    ctx = {"user_message": "今天天气怎么样", "context_messages": [], "user_id": ROOT_UID}
    asyncio.run(inject_prompt_skill(ctx))
    assert len(ctx["context_messages"]) == 2


def test_flag开_prompt只注入可见(prompt_env, monkeypatch):
    from app.plugins.config_hooks import inject_prompt_skill

    _set_flag(monkeypatch, True)
    ctx = {"user_message": "今天天气怎么样", "context_messages": [], "user_id": ROOT_UID}
    asyncio.run(inject_prompt_skill(ctx))
    assert len(ctx["context_messages"]) == 1
    assert "内置技能" in ctx["context_messages"][0]["content"]
    # 拿不到 caller → fail-closed 只注入内置
    ctx2 = {"user_message": "今天天气怎么样", "context_messages": []}
    asyncio.run(inject_prompt_skill(ctx2))
    assert len(ctx2["context_messages"]) == 1
    assert "内置技能" in ctx2["context_messages"][0]["content"]


# ================================================================ 4) 桥 / 页面

@pytest.fixture()
def page_dirs(monkeypatch, tmp_path):
    user_dir = tmp_path / "user_plugins"
    for name in (MINE, OTHER):
        d = user_dir / name
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (d / "index.html").write_text(f"<html>{name}</html>", encoding="utf-8")
    monkeypatch.setattr(registry, "USER_DIR", user_dir)
    return user_dir


def _bridge_ok(monkeypatch) -> None:
    async def _fake_dispatch(*a, **k):
        return {"ok": True}

    monkeypatch.setattr(bridge_api, "dispatch", _fake_dispatch)


def test_flag关_桥与页面跨租户仍放行(m4_db, page_dirs, monkeypatch):
    _four(monkeypatch)
    _bridge_ok(monkeypatch)
    _set_flag(monkeypatch, False)
    client = _client()
    body = {"api": "store.get", "params": {"key": "k"}}
    assert client.post(f"/api/v1/plugins/{OTHER}/bridge", headers=_auth(ROOT_UID), json=body).status_code == 200
    assert client.get(f"/api/v1/plugins/{OTHER}/page/index.html", headers=_auth(ROOT_UID)).status_code == 200


def test_flag开_桥与页面跨租户404(m4_db, page_dirs, monkeypatch):
    _four(monkeypatch)
    _bridge_ok(monkeypatch)
    _set_flag(monkeypatch, True)
    client = _client()
    body = {"api": "store.get", "params": {"key": "k"}}
    # 家庭 1 调家庭 3 装的插件 → 404（复用既有文案，不新增 i18n key）
    r = client.post(f"/api/v1/plugins/{OTHER}/bridge", headers=_auth(ROOT_UID), json=body)
    assert r.status_code == 404
    assert client.get(f"/api/v1/plugins/{OTHER}/page/index.html", headers=_auth(ROOT_UID)).status_code == 404
    # 本家庭插件 / 内置插件照常
    assert client.post(f"/api/v1/plugins/{MINE}/bridge", headers=_auth(ROOT_UID), json=body).status_code == 200
    assert client.post(f"/api/v1/plugins/{BUILTIN}/bridge", headers=_auth(ROOT_UID), json=body).status_code == 200
    assert client.get(f"/api/v1/plugins/{MINE}/page/index.html", headers=_auth(ROOT_UID)).status_code == 200
    # 反向：家庭 3 调家庭 1 的插件 → 404；自己的 200
    assert client.get(f"/api/v1/plugins/{MINE}/page/index.html", headers=_auth(OTHER_UID)).status_code == 404
    assert client.get(f"/api/v1/plugins/{OTHER}/page/index.html", headers=_auth(OTHER_UID)).status_code == 200


# ================================================================ 5) sdk 归属断言

def _sdk_plugins(monkeypatch) -> None:
    _install(monkeypatch, {
        BUILTIN: _spec(source="builtin"),
        MINE: _spec(owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID,
                    permissions=["persona:read", "memory:read", "relationship:read",
                                 "life:read", "write_memory", "send_message"]),
        OTHER: _spec(owner_user_id=OTHER_UID, owner_tenant_id=OTHER_UID),
    })


def test_flag关_sdk归属断言不生效(m4_db, monkeypatch):
    _sdk_plugins(monkeypatch)
    _set_flag(monkeypatch, False)

    async def _run():
        with registry.sdk_context(MINE, user_id=ROOT_UID):
            await sdk._assert_caller_family(user_id=OTHER_UID)       # 不抛（旧行为）
            await sdk._assert_caller_family(character_id=CHAR_OTHER)
            assert (await sdk.get_persona(CHAR_OTHER))["name"] == "other-char"

    asyncio.run(_run())


def test_flag开_sdk越权抛PermissionError(m4_db, monkeypatch):
    _sdk_plugins(monkeypatch)
    _set_flag(monkeypatch, True)

    async def _run():
        with registry.sdk_context(MINE, user_id=ROOT_UID, tenant_id=ROOT_UID):
            assert (await sdk.get_persona(CHAR_MINE))["name"] == "mine-char"  # 本家庭 → 放行
            with pytest.raises(PermissionError):
                await sdk.get_persona(CHAR_OTHER)                  # 取别人角色
            with pytest.raises(PermissionError):
                await sdk.search_memory(CHAR_OTHER, "q")
            with pytest.raises(PermissionError):
                await sdk.get_relationship(CHAR_OTHER)
            with pytest.raises(PermissionError):
                await sdk.get_life_state(CHAR_OTHER)
            with pytest.raises(PermissionError):
                await sdk._assert_caller_family(user_id=OTHER_UID)  # 自报别人 user_id
            await sdk._assert_caller_family(user_id=SUB_UID)        # 子账号属同一家庭根 → 放行
        # 无 caller（插件上下文没带 user_id/tenant_id）→ fail-closed
        with registry.sdk_context(MINE):
            with pytest.raises(PermissionError):
                await sdk._assert_caller_family(user_id=ROOT_UID)

    asyncio.run(_run())


def test_run_plugin_action_把caller写进_sdk_ctx(monkeypatch):
    from app.application import permission_service

    seen = {}

    async def _action(payload):
        seen.update(registry.current_sdk_context())
        return True

    async def _allow(uid, scope):
        return "allow"

    monkeypatch.setattr(permission_service, "check_mode", _allow)
    _install(monkeypatch, {MINE: _spec(owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID,
                                       actions={"do": _action})})
    assert asyncio.run(registry.run_plugin_action(MINE, "do", {}, user_id=SUB_UID)) is True
    assert seen["current"] == MINE
    assert seen["user_id"] == SUB_UID


# ================================================================ 6) proactive 配对校验

def _probe_cand(user_id: int, category: str = "brand_new") -> dict:
    return {"character_id": CHAR_MINE, "user_id": user_id, "hint": "hi",
            "strategy": category, "message_type": category}


def test_flag关_proactive不校验配对(m4_db, monkeypatch):
    from app.scheduling.sources import strategy as strategy_mod
    from app.scheduling.sources.plugin import PluginSource

    async def _prep(cand):
        return cand

    monkeypatch.setattr(strategy_mod, "prepare_candidate", _prep)
    _set_flag(monkeypatch, False)
    item = asyncio.run(PluginSource()._build(_probe_cand(OTHER_UID), set(), MINE))
    assert item is not None  # 旧行为：自报别人 user_id 也放行


def test_flag开_proactive配对不符丢弃候选(m4_db, monkeypatch):
    from app.scheduling.sources import strategy as strategy_mod
    from app.scheduling.sources.plugin import PluginSource

    async def _owner(cid):
        return ROOT_UID

    async def _prep(cand):
        return cand

    monkeypatch.setattr(strategy_mod, "_user_id_of", _owner)
    monkeypatch.setattr(strategy_mod, "prepare_candidate", _prep)
    _set_flag(monkeypatch, True)
    src = PluginSource()
    assert asyncio.run(src._build(_probe_cand(OTHER_UID), set(), MINE)) is None   # 不符 → 丢弃
    assert asyncio.run(src._build(_probe_cand(ROOT_UID), set(), MINE)) is not None  # 相符 → 保留


def test_flag开_proactive配对走真实角色归属查询(m4_db, monkeypatch):
    """不打桩 _user_id_of：用私有库里的 chat_sessions 反查角色归属。"""
    from app.models.chat import ChatSession
    from app.scheduling.sources import strategy as strategy_mod
    from app.scheduling.sources.plugin import PluginSource

    async def _prep(cand):
        return cand

    async def _seed():
        async with m4_db() as db:
            db.add(ChatSession(user_id=ROOT_UID, character_id=CHAR_MINE, title="t"))
            await db.commit()

    monkeypatch.setattr(strategy_mod, "prepare_candidate", _prep)
    _set_flag(monkeypatch, True)
    asyncio.run(_seed())
    src = PluginSource()
    assert asyncio.run(src._build(_probe_cand(OTHER_UID), set(), MINE)) is None
    assert asyncio.run(src._build(_probe_cand(ROOT_UID), set(), MINE)) is not None


# ================================================================ 7) 未登记类别通用闸

def _unregistered_cand() -> dict:
    return {"character_id": CHAR_MINE, "user_id": ROOT_UID,
            "strategy": "brand_new", "message_type": "brand_new"}


def test_flag关_未登记类别原样直通(monkeypatch):
    from app.scheduling.sources import strategy as strategy_mod

    _set_flag(monkeypatch, False)
    cand = _unregistered_cand()
    assert asyncio.run(strategy_mod.prepare_candidate(cand)) is cand


def test_flag开_未登记类别走通用闸(monkeypatch):
    from app.scheduling.sources import strategy as strategy_mod

    _set_flag(monkeypatch, True)
    hit = {}

    async def _spy(cand):
        hit["cat"] = strategy_mod.category_of(cand)
        return None

    monkeypatch.setattr(strategy_mod, "_generic_kernel_prep", _spy)
    assert asyncio.run(strategy_mod.prepare_candidate(_unregistered_cand())) is None
    assert hit["cat"] == "brand_new"
    # 未登记类别不再直通；已登记类别仍走各自内核 prep（不被通用闸接管）
    hit.clear()
    from app.scheduling.sources import motivation as motivation_src

    async def _mspy(cand):
        hit["motivation"] = True
        return cand

    monkeypatch.setattr(motivation_src, "prepare_strategy_candidate", _mspy)
    out = asyncio.run(strategy_mod.prepare_candidate({"strategy": "motivation", "message_type": "motivation"}))
    assert out is not None and hit.get("motivation") is True
    # 未声明类别的普通插件候选不在本条口径内（逐字节旧行为）
    plain = {"character_id": CHAR_MINE, "user_id": ROOT_UID, "hint": "hi"}
    assert asyncio.run(strategy_mod.prepare_candidate(plain)) is plain


def test_flag开_通用闸_资格免打扰配额(monkeypatch):
    from app.scheduling import arbiter
    from app.scheduling.sources import strategy as strategy_mod

    _set_flag(monkeypatch, True)
    cand = _unregistered_cand()

    async def _no_active():
        return []

    monkeypatch.setattr(arbiter, "get_active_characters", _no_active)
    assert asyncio.run(strategy_mod.prepare_candidate(cand)) is None  # 资格不符

    async def _active():
        return [{"character_id": CHAR_MINE, "user_id": ROOT_UID}]

    async def _dnd(cid, now):
        return True

    monkeypatch.setattr(arbiter, "get_active_characters", _active)
    monkeypatch.setattr(arbiter, "is_dnd_now", _dnd)
    assert asyncio.run(strategy_mod.prepare_candidate(cand)) is None  # 免打扰

    async def _no_dnd(cid, now):
        return False

    async def _no_cd(cid, uid):
        return False

    async def _zero(cid):
        return 0

    async def _none(cid):
        return None

    async def _zero_used(cid, ttype, since):
        return 0

    monkeypatch.setattr(arbiter, "is_dnd_now", _no_dnd)
    monkeypatch.setattr(arbiter, "unreplied_cooldown_active", _no_cd)
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _zero)
    monkeypatch.setattr(arbiter, "get_last_proactive_time", _none)
    monkeypatch.setattr(strategy_mod, "_count_trigger_log", _zero_used)
    assert asyncio.run(strategy_mod.prepare_candidate(cand)) is cand  # 全闸通过

    async def _used(cid, ttype, since):
        return strategy_mod.GENERIC_MAX_PER_DAY

    monkeypatch.setattr(strategy_mod, "_count_trigger_log", _used)
    assert asyncio.run(strategy_mod.prepare_candidate(cand)) is None  # 日配额用尽


# ================================================================ 8) _sdk_ctx 并发 / 同步 hook

def test_sdk_ctx_并发不串身份(monkeypatch):
    """两个协程同时跑不同插件的 hook：各自 current_plugin_name() 一直是自己（改前会串）。"""
    _install(monkeypatch, {
        "pkg_a": _spec(source="builtin", hooks={}),
        "pkg_b": _spec(source="builtin", hooks={}),
    })

    async def _run():
        seen: dict = {}
        entered, release = asyncio.Event(), asyncio.Event()

        async def hook_a(ctx):
            seen["a_before"] = registry.current_plugin_name()
            entered.set()
            await release.wait()
            seen["a_after"] = registry.current_plugin_name()

        async def hook_b(ctx):
            seen["b"] = registry.current_plugin_name()

        registry._loaded["pkg_a"]["hooks"] = {"probe_a": [hook_a]}
        registry._loaded["pkg_b"]["hooks"] = {"probe_b": [hook_b]}

        async def _task_a():
            await registry.run_hook_collect("probe_a", {})

        async def _task_b():
            await entered.wait()
            await registry.run_hook_collect("probe_b", {})
            release.set()

        await asyncio.gather(_task_a(), _task_b())
        return seen

    seen = asyncio.run(_run())
    assert seen["a_before"] == "pkg_a"
    assert seen["a_after"] == "pkg_a"  # 旧进程级 dict：此处会读到 pkg_b / None
    assert seen["b"] == "pkg_b"


def test_同步hook线程池内仍可见插件身份(monkeypatch):
    """always-on：同步 hook 丢线程池执行时，进程级 dict→ContextVar 的搬家不得让它丢身份。"""
    seen: list = []

    def _sync_hook(ctx):
        seen.append(registry.current_plugin_name())
        return None

    _install(monkeypatch, {BUILTIN: _spec(source="builtin", hooks={"context_inject": [_sync_hook]})})
    asyncio.run(registry.run_hook("context_inject", {}))
    assert seen == [BUILTIN]
