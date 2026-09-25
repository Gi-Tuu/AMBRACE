# -*- coding: utf-8 -*-
"""X7-M4e-1（派单 P25）：行动通道的**后端管理端点**（挂 /api/v1/admin/server 前缀）。

覆盖派单 §1(3) ①〜⑫：非管理员 403；GET 缺行三开关缺省方向（global/plugin_enabled=false、
**force_dry_run=true**）与 rows_present 全 false；PUT 生效态随之翻转（含 force_dry_run 关掉＝
灰度期允许真实执行的唯一开关）；PUT 非法 key 400；两份名单增删幂等；非法包名/插件名；容量上限
（targets 20 / plugins 50）；跨租户隔离；开关写入落 ``runtime_flags`` 且**不污染 AGENT_FLAGS**；
删除后再 GET 确实少一条。

隔离：私有临时 SQLite（``_dbclone`` 克隆模板 → pytest ``tmp_path``）+ 把工厂接到所有早绑定的
``async_session_factory``（含 app.api.admin / app.device.actions 经 database 的读点）；鉴权走真实
JWT + DB 权威 server_admin 判定（不 fake 依赖）。**不连生产库、不写 backend/data、不重启服务。**
夹具自建，**不改动任何既有测试文件**。
"""
import asyncio

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import admin as admin_api
from app.auth.config import create_token
from app.device import actions

pytestmark = pytest.mark.slow

# server_admin 家庭主账号（独立根＝自己）；MGR_B 是**另一个**家庭的管理员（跨租户隔离用）；
# NON_ADMIN 是家庭主账号但非 server_admin（控制台一律 403）。
MGR_A, MGR_B, NON_ADMIN = 1, 2, 3
ROOT = "/api/v1/admin/server/device-actions"
SWITCHES_URL = ROOT + "/switches"
TARGETS_URL = ROOT + "/targets"
PLUGINS_URL = ROOT + "/plugins"

# 阈值取自被测模块，本文件不另立字面量（改了端点常量而测试仍绿＝假通过）
from app.api.device_actions import (  # noqa: E402
    MAX_PLUGINS_GRAYLISTED,
    MAX_TARGETS_PER_TENANT,
)


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库工厂接到所有 app.* 模块的 ``async_session_factory`` 名上（含早绑定引用）。

    与 test_admin_console_p2 同法：``app.db.database`` 是接缝（app.device.actions 经属性访问走它），
    但 app.api.admin / permission_service 等在 import 期就 ``from app.db.database import
    async_session_factory``（早绑定），需逐个换掉。
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


@pytest.fixture()
def adm_db(monkeypatch, tmp_path):
    """私有临时 SQLite：两个不同家庭的 server_admin + 一个非 server_admin 主账号。

    C1a 起 ``set_action_flag`` 写库后同步 ``AGENT_FLAGS``（进程级字典），故登记原值、用例结束
    由 monkeypatch 自动还原，防止一个用例把闸门锁死给后面的用例。
    """
    from app.agent.loop import AGENT_FLAGS

    engine = clone_engine(tmp_path / "adm_da.db")
    factory = make_session_factory(engine)

    async def _init():
        from app.models.user import User

        async with factory() as db:
            db.add_all([
                User(id=MGR_A, username="mgr_a", nickname="甲", is_admin=True, server_admin=True),
                User(id=MGR_B, username="mgr_b", nickname="乙", is_admin=True, server_admin=True),
                User(id=NON_ADMIN, username="plain", nickname="丙", is_admin=True, server_admin=False),
            ])
            await db.commit()

    asyncio.run(_init())
    _patch_session_factories(monkeypatch, factory)
    for key in (actions.KILL_SWITCH_KEY, actions.PLUGIN_KILL_SWITCH_KEY, actions.FORCE_DRY_RUN_KEY):
        monkeypatch.setitem(AGENT_FLAGS, key, AGENT_FLAGS[key])
    actions.reset_runtime_state()
    yield factory
    actions.reset_runtime_state()
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_perm_caches():
    """清 server_admin / 账号门禁进程内缓存（跨用例残留会凭空造 403/401）。"""
    from app.application import permission_service as perm

    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()
    yield
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _rows(factory, model) -> list:
    async def _go():
        async with factory() as db:
            return list((await db.execute(select(model))).scalars().all())
    return asyncio.run(_go())


# ── ① 非管理员三类端点一律 403；未登录 401 ──
def test_非管理员三类端点一律403未登录401(adm_db):
    c = _client()
    na = _auth(NON_ADMIN)
    assert c.put(SWITCHES_URL, headers=na, json={"key": "global", "enabled": True}).status_code == 403
    assert c.post(TARGETS_URL, headers=na, json={"target": "com.example.app"}).status_code == 403
    assert c.post(PLUGINS_URL, headers=na, json={"plugin": "some_plugin"}).status_code == 403
    assert c.get(ROOT, headers=na).status_code == 403
    # 三类端点各覆盖一个删除入口 + GET 主端点未登录 → 401
    assert c.get(ROOT).status_code == 401
    assert c.request("DELETE", TARGETS_URL, json={"target": "com.example.app"}).status_code == 401
    assert c.request("DELETE", PLUGINS_URL, json={"plugin": "some_plugin"}).status_code == 401


# ── ② GET 缺行：三开关按各自缺省方向，rows_present 全 false ──
def test_GET缺行时三开关走各自缺省方向且rows_present全false(adm_db):
    body = _client().get(ROOT, headers=_auth(MGR_A)).json()
    assert body["tenant_id"] == MGR_A
    sw = body["switches"]
    assert sw["global"] is False, "全局闸缺行＝关"
    assert sw["plugin_enabled"] is False, "插件总闸缺行＝关"
    assert sw["force_dry_run"] is True, "强制干跑缺行＝开（缺省更严）"
    assert sw["rows_present"] == {"global": False, "plugin_enabled": False, "force_dry_run": False}
    assert body["targets"] == [] and body["plugins"] == []
    assert body["limits"] == {"targets_max": MAX_TARGETS_PER_TENANT,
                              "plugins_max": MAX_PLUGINS_GRAYLISTED}


# ── ③ PUT 写 global=on 后 GET 生效值 true 且 rows_present.global true ──
def test_PUT写global_on后生效值翻true且rows_present翻true(adm_db):
    c = _client()
    r = c.put(SWITCHES_URL, headers=_auth(MGR_A), json={"key": "global", "enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["switches"]["global"] is True
    assert r.json()["switches"]["rows_present"]["global"] is True
    # GET 与 PUT 回显同源
    assert c.get(ROOT, headers=_auth(MGR_A)).json()["switches"]["global"] is True


# ── ④ PUT 写 force_dry_run=off：灰度期允许真实执行的唯一开关 ──
def test_PUT关force_dry_run后生效值false(adm_db):
    c = _client()
    assert c.get(ROOT, headers=_auth(MGR_A)).json()["switches"]["force_dry_run"] is True
    r = c.put(SWITCHES_URL, headers=_auth(MGR_A), json={"key": "force_dry_run", "enabled": False})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert r.json()["switches"]["force_dry_run"] is False
    assert r.json()["switches"]["rows_present"]["force_dry_run"] is True
    assert c.get(ROOT, headers=_auth(MGR_A)).json()["switches"]["force_dry_run"] is False


# ── ⑤ PUT 非法 key → 400；缺 enabled → 400 ──
def test_PUT非法key400且缺enabled也400(adm_db):
    c = _client()
    for bad_key in ("nope", "device_actions_enabled", ""):
        r = c.put(SWITCHES_URL, headers=_auth(MGR_A), json={"key": bad_key, "enabled": True})
        assert r.status_code == 400, bad_key
    assert c.put(SWITCHES_URL, headers=_auth(MGR_A), json={"key": "global"}).status_code == 400
    # 非法 key 不得留下任何开关行
    assert c.get(ROOT, headers=_auth(MGR_A)).json()["switches"]["rows_present"]["global"] is False


# ── ⑥ targets 增删幂等 ──
def test_targets增删幂等(adm_db):
    c = _client()
    h = _auth(MGR_A)
    for _ in range(3):
        assert c.post(TARGETS_URL, headers=h, json={"target": "com.example.shop"}).json()["ok"] is True
    assert c.get(ROOT, headers=h).json()["targets"] == ["com.example.shop"]
    # 删除命中 1 行，再删同一条 → removed=0（幂等）
    assert c.request("DELETE", TARGETS_URL, headers=h,
                     json={"target": "com.example.shop"}).json()["removed"] == 1
    assert c.request("DELETE", TARGETS_URL, headers=h,
                     json={"target": "com.example.shop"}).json()["removed"] == 0
    assert c.get(ROOT, headers=h).json()["targets"] == []


# ── ⑦ plugins 增删幂等 ──
def test_plugins增删幂等(adm_db):
    c = _client()
    h = _auth(MGR_A)
    for _ in range(2):
        assert c.post(PLUGINS_URL, headers=h, json={"plugin": "gray_one"}).json()["ok"] is True
    assert c.get(ROOT, headers=h).json()["plugins"] == ["gray_one"]
    assert c.request("DELETE", PLUGINS_URL, headers=h,
                     json={"plugin": "gray_one"}).json()["removed"] == 1
    assert c.request("DELETE", PLUGINS_URL, headers=h,
                     json={"plugin": "gray_one"}).json()["removed"] == 0
    assert c.get(ROOT, headers=h).json()["plugins"] == []


# ── ⑧ 非法包名 / 非法插件名 ──
def test_非法包名与非法插件名走200加reason(adm_db):
    c = _client()
    h = _auth(MGR_A)
    for bad in ("", "com.a b", "com/example", "singlesegment", "com.示例.app", "x" * 129):
        body = c.post(TARGETS_URL, headers=h, json={"target": bad}).json()
        assert body["ok"] is False and body["reason"] == "invalid_target", bad
        assert body["targets"] == [], bad
    assert c.post(TARGETS_URL, headers=h, json={}).json()["reason"] == "invalid_target"
    for bad in ("", "has space", "com/a", "plug;in", "p" * 65):
        body = c.post(PLUGINS_URL, headers=h, json={"plugin": bad}).json()
        assert body["ok"] is False and body["reason"] == "invalid_plugin", bad
        assert body["plugins"] == [], bad
    assert c.post(PLUGINS_URL, headers=h, json={}).json()["reason"] == "invalid_plugin"
    from app.models.device import DeviceActionPlugin, DeviceActionTarget
    assert _rows(adm_db, DeviceActionTarget) == [] and _rows(adm_db, DeviceActionPlugin) == []


# ── ⑨ 容量上限：targets 20 / plugins 50 ──
def test_容量上限超限即拒且不写入(adm_db):
    c = _client()
    h = _auth(MGR_A)
    targets = [f"com.a{i}.b" for i in range(MAX_TARGETS_PER_TENANT)]
    for t in targets:
        assert c.post(TARGETS_URL, headers=h, json={"target": t}).json()["ok"] is True, t
    over = c.post(TARGETS_URL, headers=h, json={"target": "com.over.b"}).json()
    assert over["ok"] is False and over["reason"] == "too_many_targets"
    assert sorted(over["targets"]) == sorted(targets), "超容量不得写入"
    # 重复添加已有目标不占新额度
    again = c.post(TARGETS_URL, headers=h, json={"target": targets[0]}).json()
    assert again["ok"] is True and sorted(again["targets"]) == sorted(targets)

    names = [f"gray_plugin_{i}" for i in range(MAX_PLUGINS_GRAYLISTED)]
    for n in names:
        assert c.post(PLUGINS_URL, headers=h, json={"plugin": n}).json()["ok"] is True, n
    overp = c.post(PLUGINS_URL, headers=h, json={"plugin": "gray_plugin_over"}).json()
    assert overp["ok"] is False and overp["reason"] == "too_many_plugins"
    assert "gray_plugin_over" not in asyncio.run(actions.configured_plugins()), "超容量不得写入"


# ── ⑩ 跨租户隔离：管理员 A 加的目标在管理员 B 的 GET 里看不到 ──
def test_跨租户目标白名单彼此隔离(adm_db):
    c = _client()
    assert c.post(TARGETS_URL, headers=_auth(MGR_A),
                  json={"target": "com.alpha.app"}).json()["ok"] is True
    a = c.get(ROOT, headers=_auth(MGR_A)).json()
    b = c.get(ROOT, headers=_auth(MGR_B)).json()
    assert "com.alpha.app" in a["targets"]
    assert b["targets"] == [], "别的家庭看不到"
    assert a["tenant_id"] != b["tenant_id"]
    # 插件灰度名单是全局的：A 加后 B 也看得见（对比目标白名单的按租户隔离）
    assert c.post(PLUGINS_URL, headers=_auth(MGR_A), json={"plugin": "shared_p"}).json()["ok"] is True
    assert "shared_p" in c.get(ROOT, headers=_auth(MGR_B)).json()["plugins"]


# ── ⑪ 开关写入落 runtime_flags 且同步 AGENT_FLAGS（C1a 反转：三条键必须进常规 flag 体系）──
def test_开关落runtime_flags且同步AGENT_FLAGS(adm_db):
    from app.agent.loop import AGENT_FLAGS
    from app.models.config import RuntimeFlag

    three = (actions.KILL_SWITCH_KEY, actions.PLUGIN_KILL_SWITCH_KEY, actions.FORCE_DRY_RUN_KEY)
    assert set(three) <= set(AGENT_FLAGS.keys()), "三条开关必须进常规 flag 体系（App 开关页才看得见）"
    # 默认值严格保持原缺省方向：两条总闸关、强制干跑开
    assert AGENT_FLAGS[actions.KILL_SWITCH_KEY] is False
    assert AGENT_FLAGS[actions.PLUGIN_KILL_SWITCH_KEY] is False
    assert AGENT_FLAGS[actions.FORCE_DRY_RUN_KEY] is True

    c = _client()
    assert c.put(SWITCHES_URL, headers=_auth(MGR_A),
                 json={"key": "plugin_enabled", "enabled": True}).json()["ok"] is True

    keys = {r.key: r.enabled for r in _rows(adm_db, RuntimeFlag)}
    assert keys.get(actions.PLUGIN_KILL_SWITCH_KEY) is True, "必须落在 runtime_flags"
    assert AGENT_FLAGS[actions.PLUGIN_KILL_SWITCH_KEY] is True, "库写成功后内存随之生效（闸门读它）"


# ── ⑫ 删除后再 GET 确实少了那一条 ──
def test_删除后GET确实少了那一条(adm_db):
    c = _client()
    h = _auth(MGR_A)
    c.post(TARGETS_URL, headers=h, json={"target": "com.keep.a"})
    c.post(TARGETS_URL, headers=h, json={"target": "com.keep.b"})
    before = c.get(ROOT, headers=h).json()["targets"]
    assert sorted(before) == ["com.keep.a", "com.keep.b"]
    r = c.request("DELETE", TARGETS_URL, headers=h,
                  json={"target": "com.keep.a"}).json()
    assert r["ok"] is True and r["removed"] == 1 and r["targets"] == ["com.keep.b"]
    assert c.get(ROOT, headers=h).json()["targets"] == ["com.keep.b"]
