# -*- coding: utf-8 -*-
"""P2-2（2026-09-20）：插件自定义 REST（``sdk.router()``）依赖闸测试。

四条口径（对应派单 ①②③④）：

① ``plugin_disabled_route_gate`` 开 + 插件 ``enabled=0`` → 自定义 REST 404（复用既有
   ``plugin_not_found`` 文案，与桥 / chat / 页面托管同口径）；改回 ``enabled=1`` 恢复 200；
   flag 关时停用插件仍按旧行为放行；
② 两个闸 flag 全关（默认态）→ 与「改动前只挂 ``get_current_user_id``」的参考路由
   **逐字节一致**（登录分支 200 + 未登录分支 401，状态码与响应体字节相同），
   且整条请求**零查库**（会话工厂被替换为「一调就炸」，任何取数即失败）；
③ 插件 HTTP handler 内 ``registry.current_plugin_name()`` 不再为 None（改前恒 None），
   ``sdk.get_config()`` 在 REST 路径恢复可用，上下文带调用者 user_id；
④ 免登录 S2S 端点 ``/api/v1/plugins/bridge/wechat-relay`` 不受影响：三 flag 全开、无 token
   仍直达内核 handler（未安装实现 → 既有 503 语义），既不 401 也不 404；
   同一应用里被停用的插件自己的 REST 仍然 404（证明闸确实生效、只是不越界到内核端点）。

隔离：全程内存假插件（直接写 registry 四张缓存表 + 走真实 ``sdk.router()`` 造路由），
不 import 真实插件、不读磁盘插件目录；需要库的用例走 pytest ``tmp_path`` 私有库并
monkeypatch ``async_session_factory``——不连生产库、不写 backend/data。
项目未装 pytest-asyncio，HTTP 一律用 TestClient 同步驱动。
"""
import asyncio

import pytest
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.agent.loop import AGENT_FLAGS
from app.api import plugins as plugins_api
from app.auth.deps import get_current_user_id
from app.plugins import registry, sdk

pytestmark = pytest.mark.slow

# 闸相关三 flag（本批次只前两个参与判定；plugin_user_scope 属列表可见性，一并归零防串扰）
GATE_FLAGS = ("plugin_disabled_route_gate", "plugin_user_scope", "plugin_runtime_scope")
ROOT_UID, OTHER_UID = 1, 3
PLUGIN_PATH = "/api/v1/plugins"
RELAY_URL = "/api/v1/plugins/bridge/wechat-relay"


# ---------------------------------------------------------------- 内存假插件

def _info(name: str, *, config: dict | None = None) -> dict:
    """与 load_plugin_dir 产出的 info 同形状（list_plugins / sdk.get_config 读这几项）。"""
    return {
        "name": name, "version": "0.0.1", "description": "", "author": "",
        "category": "plugin", "type": "http", "icon": "", "page": "",
        "has_page": False, "hooks": [], "permissions": [],
        "config": dict(config or {}), "usage": "", "display_name": "",
        "hook_timeout": None, "context_keys": [], "content": {}, "path": "",
    }


@pytest.fixture(autouse=True)
def _flags_off_and_clean_caches(monkeypatch):
    """每例：三 flag 归 False（默认态）+ 四张缓存表换成空 dict + 清运行面/归属缓存。"""
    for key in GATE_FLAGS:
        monkeypatch.setitem(AGENT_FLAGS, key, False)
    for attr in ("_loaded", "_enabled", "_db_config", "_db_prov"):
        monkeypatch.setattr(registry, attr, {})
    registry.clear_runtime_scope_caches()
    sdk.clear_sdk_owner_cache()
    yield
    registry.clear_runtime_scope_caches()
    sdk.clear_sdk_owner_cache()


def _add_plugin(name: str, *, enabled: bool = True, source: str = "local",
                owner_user_id=None, owner_tenant_id=None, config: dict | None = None) -> APIRouter:
    """造一个带自定义 REST 的内存假插件（路由经真实 ``sdk.router()`` 创建）。

    两个端点：``/ping`` 只回常量（供逐字节对照）、``/who`` 回读插件上下文（供身份断言）。
    """
    registry._loaded[name] = {"info": _info(name, config=config), "module": None,
                              "hooks": {}, "actions": {}, "router": None}
    registry._enabled[name] = bool(enabled)
    registry._db_config[name] = {}
    registry._db_prov[name] = {"source": source, "owner_user_id": owner_user_id,
                               "owner_tenant_id": owner_tenant_id}
    with registry.sdk_context(name):
        router = sdk.router()
    assert registry._loaded[name]["router"] is router, "sdk.router 应把路由登记回 _loaded"

    @router.get("/ping")
    async def ping():
        return {"echo": "pong"}

    @router.get("/who")
    async def who():
        return {"plugin": registry.current_plugin_name(),
                "ctx": registry.current_sdk_context(),
                "cfg": sdk.get_config()}

    return router


def _reference_router(name: str) -> APIRouter:
    """改动前 ``sdk.router()`` 的等价物：同样的 prefix/tags，依赖只有登录态。"""
    return APIRouter(prefix=f"{PLUGIN_PATH}/{name}", tags=[name],
                     dependencies=[Depends(get_current_user_id)])


def _client(*routers: APIRouter, caller: int | None = ROOT_UID, raise_exc: bool = False) -> TestClient:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    if caller is not None:
        app.dependency_overrides[get_current_user_id] = lambda: caller
    return TestClient(app, raise_server_exceptions=raise_exc)


# ---------------------------------------------------------------- 私有临时库

@pytest.fixture()
def family_db(monkeypatch, tmp_path):
    """两个互不相干的家庭根（1 / 3），供 runtime_scope 可见性判定取数。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'gate.db').as_posix()}",
                                 poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册主 metadata
        from app.models.base import Base
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                User(id=ROOT_UID, username="root", nickname="家庭根", is_admin=True),
                User(id=OTHER_UID, username="other", nickname="别家根", is_admin=True),
            ])
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _forbid_db(monkeypatch) -> None:
    """把会话工厂换成「一调就炸」：证明被断言的分支确实零查库。"""
    import app.db.database as db_mod
    import app.db.session as session_mod

    def _boom(*_a, **_kw):
        raise AssertionError("flag 关闭时插件闸不得查库")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)
    monkeypatch.setattr(session_mod, "async_session_factory", _boom, raising=False)


# ================================================================ ① 运行时禁用闸

def test_停用插件自定义REST_flag开时404_flag关时旧行为(monkeypatch):
    router = _add_plugin("off_plugin", enabled=False, source="builtin")
    c = _client(router)
    url = f"{PLUGIN_PATH}/off_plugin/ping"

    # flag 关（默认）：只判存在、不判 enabled —— 逐字节旧行为
    assert c.get(url).status_code == 200

    monkeypatch.setitem(AGENT_FLAGS, "plugin_disabled_route_gate", True)
    r = c.get(url)
    assert r.status_code == 404
    assert r.json()["detail"] == "插件不存在"  # 复用既有文案，不泄漏「停用」与「不存在」的区别

    # 启用后恢复（判定读的是内存缓存 _enabled，与 set_plugin_state 写入口一致）
    registry._enabled["off_plugin"] = True
    assert c.get(url).status_code == 200
    # 缓存里没有这个插件（启动挂载后被卸载）→ flag 开时按停用处理
    registry._enabled.pop("off_plugin")
    assert c.get(url).status_code == 404


def test_禁用闸不受语言头影响仍走既有文案(monkeypatch):
    router = _add_plugin("off_en", enabled=False, source="builtin")
    monkeypatch.setitem(AGENT_FLAGS, "plugin_disabled_route_gate", True)
    r = _client(router).get(f"{PLUGIN_PATH}/off_en/ping", headers={"lang": "en"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Plugin not found"


# ================================================================ ② flag 全关=逐字节旧行为

def test_两flag全关_与改动前逐字节一致且零查库(monkeypatch):
    _forbid_db(monkeypatch)
    gated = _add_plugin("parity", source="builtin")
    ref = _reference_router("parity")

    @ref.get("/ping")
    async def ref_ping():
        return {"echo": "pong"}

    url = f"{PLUGIN_PATH}/parity/ping"
    # 登录分支：闸放行且一次库都不碰（禁用闸读 _enabled 内存、可见性闸 flag 内短路）
    c_new = _client(gated, raise_exc=True)
    c_old = _client(ref, raise_exc=True)
    a, b = c_new.get(url, params={"text": "x"}), c_old.get(url, params={"text": "x"})
    assert a.status_code == b.status_code == 200
    assert a.content == b.content  # 响应体逐字节相同

    # 未登录分支：401 由 get_current_user_id 先拒，闸不参与（文案亦逐字节一致）
    an = _client(gated, caller=None).get(url)
    ao = _client(ref, caller=None).get(url)
    assert an.status_code == ao.status_code == 401
    assert an.content == ao.content

    # 停用插件在 flag 全关时同样不受影响（禁用判定与可见性判定各自门控）
    registry._enabled["parity"] = False
    assert _client(gated, raise_exc=True).get(url).status_code == 200


def test_runtime_scope跨租户闸_关时放行开时404(family_db, monkeypatch):
    mine = _add_plugin("mine_local", source="local", owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID)
    other = _add_plugin("other_local", source="local", owner_user_id=OTHER_UID, owner_tenant_id=OTHER_UID)
    c = _client(mine, other, caller=ROOT_UID)
    assert c.get(f"{PLUGIN_PATH}/other_local/ping").status_code == 200  # 不判归属

    monkeypatch.setitem(AGENT_FLAGS, "plugin_runtime_scope", True)
    registry.clear_runtime_scope_caches()
    assert c.get(f"{PLUGIN_PATH}/other_local/ping").status_code == 404  # 别家安装的 → 404
    assert c.get(f"{PLUGIN_PATH}/mine_local/ping").status_code == 200   # 本家庭 → 放行


# ================================================================ ③ 插件身份上下文

def test_handler内可解析自身身份_改动前恒为None(monkeypatch):
    gated = _add_plugin("id_plugin", source="builtin", config={"k": "v"})
    ref = _reference_router("id_plugin")

    @ref.get("/who")
    async def ref_who():
        return {"plugin": registry.current_plugin_name(),
                "ctx": registry.current_sdk_context(),
                "cfg": sdk.get_config()}

    body = _client(gated).get(f"{PLUGIN_PATH}/id_plugin/who").json()
    assert body["plugin"] == "id_plugin"          # 改前恒 None
    assert body["cfg"] == {"k": "v"}              # sdk.get_config 在 REST 路径恢复可用
    assert body["ctx"]["user_id"] == ROOT_UID     # 调用者已写进上下文（供 M4 归属断言）
    # push/reset 成对：第二次调用仍拿得到自己（不会被上一轮的 reset 打成 None）
    assert _client(gated).get(f"{PLUGIN_PATH}/id_plugin/who").json()["plugin"] == "id_plugin"
    # 请求结束后测试线程自身不残留插件身份（上下文不外泄）
    assert registry.current_plugin_name() is None

    old = _client(ref).get(f"{PLUGIN_PATH}/id_plugin/who").json()
    assert old["plugin"] is None and old["cfg"] == {}  # 改动前的真实形态（对照留证）


def test_runtime_scope开时上下文带caller身份可判定归属(family_db, monkeypatch):
    """闸把 caller 推入上下文 → sdk 的 M4 归属断言在 REST 路径上真正可判定。"""
    mine = _add_plugin("scoped", source="local", owner_user_id=ROOT_UID, owner_tenant_id=ROOT_UID)
    monkeypatch.setitem(AGENT_FLAGS, "plugin_runtime_scope", True)
    body = _client(mine, caller=ROOT_UID).get(f"{PLUGIN_PATH}/scoped/who").json()
    assert body["plugin"] == "scoped"
    assert body["ctx"]["user_id"] == ROOT_UID
    # 上下文里 tenant 由 sdk 侧按需懒解析（闸只带 user_id，与 run_hook 传 viewer 的口径互补）
    assert asyncio.run(sdk._family_root_of(ROOT_UID)) == ROOT_UID


# ================================================================ ④ 免登录内核桥端点

def test_免登录桥端点不受闸影响(monkeypatch):
    """三 flag 全开：S2S 桥出口照旧直达 handler，插件自定义 REST 照旧被管住。"""
    for key in GATE_FLAGS:
        monkeypatch.setitem(AGENT_FLAGS, key, True)
    router = _add_plugin("wechat_ilink", enabled=False, source="builtin")
    app = FastAPI()
    app.include_router(plugins_api.router)   # 内核免登录桥端点（/bridge/wechat-relay）
    app.include_router(router)               # 插件自定义 REST（同前缀，必须被闸管住）
    app.dependency_overrides[get_current_user_id] = lambda: ROOT_UID
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post(RELAY_URL, json={"hello": "world"})
    # 无 token 也不 401/404：503 是「wechat_ilink 实现未安装」的既有语义，说明闸未插手
    assert r.status_code == 503
    assert r.json()["detail"] == "wechat bridge not installed"
    # 同前缀下的插件自定义 REST 仍按禁用闸 404（闸只作用于 sdk.router() 生成的路由）
    assert c.get(f"{PLUGIN_PATH}/wechat_ilink/ping").status_code == 404
