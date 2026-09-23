# -*- coding: utf-8 -*-
"""X7-M4c-4 插件行动通道端到端验收（派单 P21：示例插件 + 真实桥端点 + 真闸门 + 真落库名单）。

把 M4c 的验收口径钉成可执行规格：「**示例插件在「已授权 1 个能力 + 白名单 1 个应用」下只对该
应用成功；其余一律拒绝并可解释**」。与 :mod:`tests.test_device_actions` 的分工：那个文件测
**判定函数**（可直接调 ``decide_action``），本文件**一律走真实 HTTP 桥端点**
``POST /api/v1/plugins/{name}/bridge``（``api=device_action``）——插件视角＝身份来自路径名、
租户来自库里解析、名单来自 M4c-3 的两张新表、结论来自桥响应体的 ``data``。

被测插件 ``device_action_demo`` **只同意一个能力** ``device:action_open_app:write``（故本文件
提交的意图恒为 ``open_app``；``tap``/``set_text`` 未同意，属 ③b 该拒的口径，见用例 ⑤b）；
目标白名单**只放一个应用** ``com.demo.target``。

场景 ↔ 用例（派单 §1(2) ①〜⑨）：
① 总闸缺行 → ``plugin_actions_disabled``；总闸开但该插件未灰度 → ``plugin_not_graylisted``（两例）；
② 灰度 + 授权 + 白名单齐备、``device_actions_force_dry_run`` 未插行（缺省即干跑）
   → ``allowed=true`` 且 ``status=="dry_run"``，且响应体**没有** ``action_token``；
③ 显式把该键置 0 → 同一请求拿到 ``action_token``，并由内置 pending 端点取走、回报闭环；
④ 同一插件提交另一个目标 → ``target_not_allowed``（「只对该应用成功」的字面验收）；
⑤ 只同意过只读权限 / 库里没有安装行 → ``plugin_unauthorized``（读写分离 + fail-closed，两例）；
⑥ 总闸未开时即使已灰度且能力授权齐备 → 仍拒（闸门顺序：③a 在 ③b 之前）；
⑦ ``params`` 里混入 ``plugin`` 自称 → ``invalid_intent:plugin_not_allowed``；
⑧ 内置通道（``POST /api/v1/device/actions``）不受这三条插件闸影响；
⑨ 审计里 ``plugin=device_action_demo`` 且**带 user_id**（放行与被拒各一条，拒绝可解释）；
＋ 两份名单**真落库**（``device_action_targets`` / ``device_action_plugins`` 确有行，换一条新
  连接后桥提交仍过闸）。

复用而不复制（派单 §1(3)）：夹具与造数原语全部 ``import`` 自 :mod:`tests.test_device_actions`
（pytest prepend 导入模式下 ``backend/tests`` 在 ``sys.path`` 上，同目录 import 拿到的就是它自己
那份模块对象，不是第二份拷贝）。**唯一**的本地 helper :func:`_open_demo_gates` 是因为
``_open_plugin_gates`` 把「放进白名单的那个应用」写死成它自己的 ``TARGET``，而本批口径要求白名单
里只有一个（示例用）应用——它只把 ``allow_target`` 的目标名参数化，闸门摆法逐条沿用那个函数
（未新增、未放宽任何判定）。**未改动 tests/test_device_actions.py 一字。**

隔离：私有临时库（``_dbclone`` 克隆模板 → pytest ``tmp_path``）+ patch
``app.db.database.async_session_factory``（桥侧租户解析与闸门读点同源）。**不连生产库、
不写 backend/data、不重启服务、不碰生产库里那条真白名单与真灰度插件。**
"""
import asyncio
import logging

import pytest
from test_device_actions import (  # noqa: F401  # 夹具由 pytest 按名字从本模块命名空间取用
    REASON_SELF_IDENTITY,
    USER,
    _audit_lines,
    _bridge_client,
    _client,
    _install_consented,
    _model_rows,
    _plugins_model,
    _reopen,
    _register_plugin_in_runtime,
    _set_account,
    _set_flag,
    _set_global,
    _targets_model,
    actions_log_records,
    act_db,
)

from app.db import database
from app.device import actions
from app.plugins import registry

# ── 被测插件与它眼中的「那一个应用」（派单 §1(1)）──
DEMO = "device_action_demo"        # 示例插件：只同意 open_app 这一条能力
GHOST = "device_action_ghost"      # 只进注册表 + 灰度名单、库里**没有安装行**的插件名
DEMO_TARGET = "com.demo.target"    # 目标白名单里唯一的那个应用
OTHER_TARGET = "com.demo.other"    # 白名单外的另一个应用（④ 的字面验收）
OPEN_APP_WRITE = "device:action_open_app:write"
OPEN_APP_READ = "device:action_open_app:read"
OPEN_APP_PARAMS = {"capability": "action_open_app", "target_app": DEMO_TARGET}

# 建临时库属重量级/集成型用例（docs/engineering-protocol.md 十八），与 test_device_actions 同档
pytestmark = pytest.mark.slow


def _open_demo_gates(factory, *, tenant=USER, name=DEMO, perms=(OPEN_APP_WRITE,),
                      plugin_kill_switch=True, graylist=True, force_dry_run=None,
                      target=DEMO_TARGET):
    """摆出**插件通道**的各道闸门（逐条沿用 ``_open_plugin_gates``，只把白名单里的目标名参数化）。

    - ``plugin_kill_switch``：③a 总闸 ``device_actions_plugin_enabled``（``None``＝不插行＝缺省即关）；
    - ``graylist``：是否把 ``name`` 放进行动灰度名单（M4c-3 起＝写 ``device_action_plugins``）；
    - ``force_dry_run``：``None``＝不插行（缺省即「强制干跑」），True/False＝插对应值的行；
    - ``target``：**本批白名单里唯一的那一个应用**（``tenant`` 与桥侧解析出的家庭根一致）。
    """
    asyncio.run(_set_global(factory))              # ① 全局 kill switch
    asyncio.run(_set_account(factory, USER))      # ② 账号级（提交账号）
    if plugin_kill_switch is not None:             # ③a-总闸
        asyncio.run(_set_flag(factory, actions.PLUGIN_KILL_SWITCH_KEY, plugin_kill_switch))
    if perms:                                      # ③b 能力授权（逐条同意）
        asyncio.run(_install_consented(factory, tenant=tenant, name=name, perms=list(perms)))
    if graylist:                                   # ③a-逐插件灰度（落库入口）
        asyncio.run(actions.allow_plugin_actions(name))
    if force_dry_run is not None:                  # 灰度期强制干跑闸
        asyncio.run(_set_flag(factory, actions.FORCE_DRY_RUN_KEY, force_dry_run))
    asyncio.run(actions.allow_target(tenant, target))   # ④ 目标白名单（落库入口）


def _bridge_url(name: str = DEMO) -> str:
    return f"/api/v1/plugins/{name}/bridge"


def _submit(client, params: dict, *, name: str = DEMO) -> dict:
    """以插件视角提交一次行动意图（真实桥端点）→ 返回桥响应里的 ``data``。

    ``ok is True`` 是桥侧契约：**被哪层闸门挡住是业务结论**（``data.allowed=false`` + ``reason``），
    不是桥的错误；故被拒同样是 HTTP 200 + ok=true（与内置端点同一份机器可读口径）。
    """
    r = client.post(_bridge_url(name), json={"api": "device_action", "params": dict(params)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    return body["data"]


# ── ① 总闸缺行（读不到即关）→ plugin_actions_disabled ──
def test_桥提交在插件总闸缺行时拒(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, plugin_kill_switch=None)   # 灰度、授权、白名单都已就位，只缺总闸行
    data = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
    assert data["allowed"] is False
    assert data["reason"] == actions.REASON_PLUGIN_ACTIONS_DISABLED
    assert data["status"] == actions.STATUS_DENIED
    assert "action_token" not in data, "被拒不发任何可执行凭据"
    assert actions.take_pending(USER) == [], "被拒不得留下待执行项"

    # 正向前提：补上总闸那一行，同一条意图立刻过闸（证明上一条的拒因是这一行，不是顺序或授权）
    asyncio.run(_set_flag(act_db, actions.PLUGIN_KILL_SWITCH_KEY, True))
    assert _submit(_bridge_client(USER), OPEN_APP_PARAMS)["allowed"] is True


# ── ① 总闸开但该插件未灰度 → plugin_not_graylisted ──
def test_总闸已开但该插件未灰度时拒(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, graylist=False)
    assert asyncio.run(actions.plugin_graylisted(DEMO)) is False
    data = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
    assert data["allowed"] is False and data["reason"] == actions.REASON_PLUGIN_NOT_GRAYLISTED
    assert data["status"] == actions.STATUS_DENIED and "action_token" not in data

    # 正向前提：灰度放开（走 M4c-3 的落库入口）后同一条意图过闸 —— 拒因确实来自灰度名单
    asyncio.run(actions.allow_plugin_actions(DEMO))
    assert _submit(_bridge_client(USER), OPEN_APP_PARAMS)["allowed"] is True


# ── ② 全开 + 强制干跑未插行（缺省即开）→ allowed/dry_run，且不发 token ──
def test_闸门齐备但未插干跑行时只回dry_run不发token(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db)                             # force_dry_run=None＝库里没有那一行
    assert asyncio.run(actions._force_dry_run()) is True, "读不到即开（缺省更严）"
    data = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
    assert data["allowed"] is True and data["reason"] == "", "闸门全过，只是不下发可执行凭据"
    assert data["status"] == actions.STATUS_DRY_RUN
    assert "action_token" not in data, "干跑不得下发 token（无动作可执行）"
    assert actions.take_pending(USER) == [], "干跑不得入队"
    # App 侧走真实 pending 端点同样取不到任何东西（桥侧与 App 侧口径一致）
    assert _client(USER).get("/api/v1/device/actions/pending").json() == []


# ── ③ 显式关掉强制干跑 → 同一请求拿到 token，且 App 取走/回报闭环 ──
def test_显式关闭强制干跑后同一意图拿到token并由app取走回报(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, force_dry_run=False)
    data = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
    assert data["allowed"] is True and data["status"] == actions.STATUS_NOT_IMPLEMENTED
    token = data.get("action_token")
    assert token, "灰度过闸的真实路径要能拿到可执行凭据"

    # 插件提交的待办由**内置 pending 端点**取走（同账号；身份仍是提交它的插件）
    pending = _client(USER).get("/api/v1/device/actions/pending").json()
    assert [i["action_token"] for i in pending] == [token]
    assert pending[0]["plugin"] == DEMO and pending[0]["capability"] == "action_open_app"
    assert pending[0]["target_app"] == DEMO_TARGET
    # 回报闭环（本批执行体在 App 端；回报只打通链路 + 驱动熔断计数）
    r = _client(USER).post(f"/api/v1/device/actions/{token}/result",
                           json={"ok": True, "detail": "opened"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert _client(USER).get("/api/v1/device/actions/pending").json() == []


# ── ④ 同一插件提交另一个目标 → target_not_allowed（「只对该应用成功」的字面验收）──
def test_同一插件提交白名单外目标被拒而白名单内目标照常成功(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, force_dry_run=False)
    assert asyncio.run(actions.configured_targets(USER)) == frozenset({DEMO_TARGET}), \
        "白名单里就只有那一个应用"

    data = _submit(_bridge_client(USER), {**OPEN_APP_PARAMS, "target_app": OTHER_TARGET})
    assert data["allowed"] is False and data["reason"] == actions.REASON_TARGET_NOT_ALLOWED
    assert data["status"] == actions.STATUS_DENIED and "action_token" not in data
    assert actions.take_pending(USER) == []

    # 同一条意图换成白名单里那个应用即成功（差别只在 target）
    ok = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
    assert ok["allowed"] is True and ok.get("action_token")


# ── ⑤a 只同意过只读权限 → plugin_unauthorized（读写分离）──
def test_只同意只读权限的插件桥提交被拒(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, perms=(OPEN_APP_READ,))    # 灰度与白名单都放开，只缺 :write
    data = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
    assert data["allowed"] is False and data["reason"] == actions.REASON_PLUGIN_UNAUTHORIZED
    assert data["status"] == actions.STATUS_DENIED and "action_token" not in data
    assert actions.take_pending(USER) == []

    # 正向前提：补上 :write 同意即过闸（且读权限那条仍在，不构成放宽）
    assert asyncio.run(registry.has_capability_permission(DEMO, USER, "action_open_app")) is False
    asyncio.run(_install_consented(act_db, tenant=USER, name=DEMO, perms=[OPEN_APP_WRITE]))
    assert asyncio.run(registry.has_capability_permission(DEMO, USER, "action_open_app")) is True
    assert _submit(_bridge_client(USER), OPEN_APP_PARAMS)["allowed"] is True
    assert sorted(asyncio.run(registry.get_tenant_consented_permissions(DEMO, USER))) == \
        [OPEN_APP_READ, OPEN_APP_WRITE]


# ── ⑤b 未安装（库里没有 plugins 行）→ plugin_unauthorized（fail-closed）──
def test_未安装的插件名走桥通道提交被拒(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _register_plugin_in_runtime(monkeypatch, GHOST)     # 桥认它存在（注册表视图），但库里没安装行
    _open_demo_gates(act_db, force_dry_run=False)         # DEMO 灰度 + 已授权（对照面）
    asyncio.run(actions.allow_plugin_actions(GHOST))     # 灰度名单只是名字，不代表已安装
    assert asyncio.run(actions.plugin_graylisted(GHOST)) is True

    data = _submit(_bridge_client(USER), OPEN_APP_PARAMS, name=GHOST)
    assert data["allowed"] is False and data["reason"] == actions.REASON_PLUGIN_UNAUTHORIZED
    assert data["status"] == actions.STATUS_DENIED and "action_token" not in data
    # 同状态下已安装的 DEMO 照常成功 → 拒因确实是「没有安装/同意记录」，不是闸门顺序或租户解析
    assert _submit(_bridge_client(USER), OPEN_APP_PARAMS)["allowed"] is True
    assert [i["plugin"] for i in actions.take_pending(USER)] == [DEMO]


# ── ⑥ 闸门顺序：总闸未开时，灰度 + 能力授权齐备也救不回来 ──
def test_总闸未开时灰度与授权齐备仍拒(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, plugin_kill_switch=None)
    assert asyncio.run(actions.plugin_graylisted(DEMO)) is True
    assert asyncio.run(registry.has_capability_permission(DEMO, USER, "action_open_app")) is True
    assert DEMO_TARGET in asyncio.run(actions.configured_targets(USER))
    data = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
    assert data["allowed"] is False and data["reason"] == actions.REASON_PLUGIN_ACTIONS_DISABLED
    assert actions.take_pending(USER) == []


# ── ⑦ params 里混入 plugin 自称 → invalid_intent:plugin_not_allowed ──
def test_桥params自称plugin一律拒(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, force_dry_run=False)       # 全开＝若身份仍可伪造，这条必然拿到 token
    client = _bridge_client(USER)
    for claimed in (actions.BUILTIN_CALLER, DEMO, "other_plugin", "  builtin  "):
        data = _submit(client, {**OPEN_APP_PARAMS, "plugin": claimed})
        assert data["allowed"] is False, claimed
        assert data["reason"] == REASON_SELF_IDENTITY, claimed
        assert data["status"] == actions.STATUS_DENIED and "action_token" not in data, claimed
    assert actions.take_pending(USER) == [], "自称身份不得留下任何待执行项"
    # 正向前提：同一个插件不带自称、走同一条路径名即成功（拒因来自 params，不来自路径）
    assert _submit(client, OPEN_APP_PARAMS)["allowed"] is True


# ── ⑧ 内置通道不受三条插件闸影响（含强制干跑缺省即开的情形）──
def test_内置通道不受插件三闸影响而同目标照常allowed(act_db, monkeypatch):  # noqa: F811
    # 三把插件闸一行都没有（总闸缺省＝关、灰度名单空、force_dry_run 缺省＝开）+ 不装任何插件；
    # 插件只挂进注册表视图——否则对比那一次会先撞上桥自己的「插件不存在 404」，到不了闸门
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, perms=(), graylist=False, plugin_kill_switch=None)
    client = _client(USER)

    r = client.post("/api/v1/device/actions", json={**OPEN_APP_PARAMS, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allowed"] is True and body["status"] == actions.STATUS_DRY_RUN

    r = client.post("/api/v1/device/actions", json=dict(OPEN_APP_PARAMS))
    real = r.json()
    assert real["allowed"] is True and real["status"] == actions.STATUS_NOT_IMPLEMENTED
    assert real.get("action_token"), "内置不受 force_dry_run 改写"
    assert [i["plugin"] for i in actions.take_pending(USER)] == [actions.BUILTIN_CALLER]

    # 同一条意图由插件提交则被总闸挡住 → 三条插件闸只作用于插件通道
    assert _submit(_bridge_client(USER), OPEN_APP_PARAMS)[
        "reason"] == actions.REASON_PLUGIN_ACTIONS_DISABLED


# ── ⑨ 审计：plugin=device_action_demo 且带 user_id，放行与被拒都可解释 ──
def test_审计带插件名与user_id且拒绝结论可解释(act_db, monkeypatch, actions_log_records):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, force_dry_run=False)
    assert _submit(_bridge_client(USER), OPEN_APP_PARAMS).get("action_token")
    _submit(_bridge_client(USER), {**OPEN_APP_PARAMS, "target_app": OTHER_TARGET})

    lines = _audit_lines(actions_log_records)
    approved = next(row for row in lines if row["result"] == "approved")
    assert approved["plugin"] == DEMO, "审计要一眼看出是这条插件链发起的"
    assert approved["user_id"] == str(USER), "M4a 审计必须带 user_id"
    assert approved["tenant_id"] == str(USER), "桥侧租户由提交账号解析（独立主账号＝它自己）"
    assert approved["capability"] == "action_open_app" and approved["action"] == "open_app"
    assert approved["target_app"] == DEMO_TARGET and approved["dry_run"] == "false"
    denied = next(row for row in lines if row["result"] == "denied")
    assert denied["plugin"] == DEMO and denied["user_id"] == str(USER)
    assert denied["target_app"] == OTHER_TARGET
    assert denied["reason"] == actions.REASON_TARGET_NOT_ALLOWED, "拒绝要机器可读、可解释"
    # 行动审计恒为 INFO（device.actions 这个日志器只发审计与失败 WARNING）
    assert all(rec.levelno == logging.INFO for rec in actions_log_records)


# ── ＋ 两份名单真落库（M4c-3 的表），换一条新连接后桥提交仍过闸 ──
def test_两份名单真落库且换新连接后桥提交仍过闸(act_db, monkeypatch):  # noqa: F811
    _register_plugin_in_runtime(monkeypatch, DEMO)
    _open_demo_gates(act_db, force_dry_run=False)
    assert [r.plugin_name for r in asyncio.run(_model_rows(act_db, _plugins_model()))] == [DEMO]
    assert [(r.tenant_id, r.target)
            for r in asyncio.run(_model_rows(act_db, _targets_model()))] == [(USER, DEMO_TARGET)]

    fresh, engine = _reopen(act_db)                     # 同一个库文件、全新的 engine/连接
    try:
        monkeypatch.setattr(database, "async_session_factory", fresh)
        actions.reset_runtime_state()                   # 只清内存态：名单不得被它清掉
        data = _submit(_bridge_client(USER), OPEN_APP_PARAMS)
        assert data["allowed"] is True and data.get("action_token"), "名单在库里，不在进程里"
    finally:
        engine.sync_engine.dispose()
