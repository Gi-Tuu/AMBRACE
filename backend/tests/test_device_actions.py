# -*- coding: utf-8 -*-
"""X7-M4a 行动裁决测试（派单 P13：契约 + 四层闸门 + 带 user_id 的审计，零真实执行）。

覆盖（与派单 §1(4) ①〜⑩ 一一对应）：
① 全局开关缺省（读不到行）→ 拒；② 开关开但插件未授权 → 拒（fail-closed）；
③ 授权但目标白名单空集 → 拒；④ 白名单命中 + dry_run → 放行且**不产生任何动作/不发 token**；
⑤ ``tap`` 缺 ``by``/``query``（及只读能力当行动、open_app 带多余字段）→ 校验拒绝；
⑥ 限流：第 11 次/分钟 → 拒；⑦ 熔断：连续 5 次失败回报后 → 拒；
⑧ pending/result 往返 + 审计字段固定且**含 user_id**；⑨ 跨租户/跨账号取不到 pending、不得替他人回报；
⑩ 3 条行动能力与 ``device:<id>:write`` 一一对应、``kind="act"``；
＋ API 三端点接线（被拒一律 200 + reason，不抛 4xx）。
＋ 派单 P14（X7-M4b-1）：内置调用方通道（闸门③ 跳过、其余闸门与限流/熔断不免检）
  + 目标白名单管理端点（require_server_admin、非法入参 200 + reason、单租户容量上限）。
＋ 派单 P17（X7-M4c-1）：**身份收口**（内置端点不接受请求体自称 plugin；插件身份取桥路径名）
  + **插件提交通道**（桥 ``device_action`` api）+ **插件灰度闸**（``device_actions_plugin_enabled``
    总闸、逐插件灰度白名单、``device_actions_force_dry_run`` 缺省即强制干跑）。
  P17 对既有用例只动**造数据 setup**（③ 拆成 ③a/③b 后插件通道要先放开灰度；内置端点请求体去掉
  ``plugin`` 字段），**未删改、未放宽任何断言**。
＋ 派单 P19（X7-M4c-3）：两份**名单**落库（``device_action_targets`` / ``device_action_plugins``）
  + 插件灰度管理端点。四个名单函数（``configured_targets`` / ``allow_target`` /
  ``plugin_graylisted`` / ``allow_plugin_actions``）改为读写库 ⇒ 变成 async，故 P19 对既有用例
  只把调用点包上 ``asyncio.run(...)``（判定内容一字未动）。**唯一例外**：
  :func:`reset_runtime_state` 按 §1(2) 不再清这两份名单（它们在库里），于是
  ``test_灰度白名单默认空集全拒放开后放行且reset一并清掉`` 末尾两条断言（「reset 清零灰度／
  目标名单」）与新语义正面冲突、必红——按派单「禁改既有断言，必红即停下说明」处理，原样保留。

隔离：私有临时库（``_dbclone`` 克隆模板 → pytest ``tmp_path``）+ patch
``app.db.database.async_session_factory``（闸门 ①② 的开关读点、两份名单的读写点都同源，
故只需 patch 这一处）。限流/熔断/队列是**进程内状态**，故每个用例前后显式
:func:`actions.reset_runtime_state`；两份名单的跨用例隔离由「每用例一个空库」保证。
**不连生产库、不写 backend/data、不重启服务。**
"""
import asyncio
import logging

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import device_actions as device_actions_api
from app.api.plugin_bridge import router as plugin_bridge_router
from app.application import plugin_bridge_service
from app.auth.deps import get_current_user_id
from app.db import database
from app.device import actions
from app.device.actions import ActionIntent
from app.device.capabilities import CAPABILITIES, capability_permissions
from app.models.config import RuntimeFlag, UserRuntimeFlag
from app.models.plugin import Plugin
from app.plugins import registry

USER, OTHER_USER = 11, 12
TENANT, OTHER_TENANT = 7, 8
PLUGIN = "act_probe"
TARGET = "com.example.shop"
# 三条行动能力的权限名（闸门 ③ 放行所需，逐条 :write）
ACT_WRITE_PERMS = [f"device:{cid}:write" for cid in
                   ("action_open_app", "action_tap", "action_set_text")]

# 审计行的固定字段顺序（缺序即视为契约漂移）
AUDIT_FIELDS = ("ts", "tenant_id", "user_id", "plugin", "capability", "action", "target_app",
                "by", "result", "elapsed_ms", "dry_run", "reason")

# 建临时库属重量级/集成型用例（docs/engineering-protocol.md 十八）
pytestmark = pytest.mark.slow


@pytest.fixture()
def act_db(monkeypatch, tmp_path):
    """私有临时 SQLite（含 runtime_flags / user_runtime_flags / plugins / plugin_consents）。"""
    engine = clone_engine(tmp_path / "actions.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(database, "async_session_factory", factory)
    actions.reset_runtime_state()
    yield factory
    actions.reset_runtime_state()
    engine.sync_engine.dispose()


@pytest.fixture()
def actions_log_records():
    """直连收集 ``device.actions`` 日志器的记录。

    不依赖 pytest ``caplog``：其 ``log_disable_existing_loggers`` 会把 import 期创建的应用日志器
    置成 ``disabled=True``（同 tests/test_device_capability_permissions.py 的实测结论），
    故这里显式复位并挂直连 handler，退出时原样还回。
    """
    logger = logging.getLogger("device.actions")
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record):  # pragma: no cover - 由 logging 回调
            records.append(record)

    sink = _Sink(level=logging.INFO)
    prev_disabled, prev_level = logger.disabled, logger.level
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.addHandler(sink)
    try:
        yield records
    finally:
        logger.removeHandler(sink)
        logger.setLevel(prev_level)
        logger.disabled = prev_disabled


# ── 造数据 helper ──
async def _set_global(factory, enabled=True):
    async with factory() as db:
        db.add(RuntimeFlag(key=actions.KILL_SWITCH_KEY, enabled=enabled))
        await db.commit()


async def _set_flag(factory, key, enabled=True):
    """插一行全局 ``runtime_flags``（M4c-1 的插件总闸 / 强制干跑闸与开关① 同一张表）。"""
    async with factory() as db:
        db.add(RuntimeFlag(key=key, enabled=enabled))
        await db.commit()


async def _set_account(factory, user_id, enabled=True):
    async with factory() as db:
        db.add(UserRuntimeFlag(user_id=user_id, key=actions.KILL_SWITCH_KEY, enabled=enabled))
        await db.commit()


async def _install_consented(factory, *, tenant=TENANT, name=PLUGIN, perms=()):
    """装插件（归某家庭，重复调用不重建行）并逐条追加同意给定权限名（闸门 ③ 的判据）。"""
    async with factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            db.add(Plugin(name=name, version="1.0.0", description="", author="",
                          enabled=True, owner_tenant_id=tenant, consented_permissions="[]"))
            await db.commit()
    await registry.grant_plugin_consent(name, list(perms), tenant_id=tenant)


def _tap(**kw) -> ActionIntent:
    kw.setdefault("target_app", TARGET)
    kw.setdefault("by", "text")
    kw.setdefault("query", "下单")
    return ActionIntent(capability="action_tap", **kw)


def _decide(intent, *, user_id=USER, tenant_id=TENANT, plugin=PLUGIN) -> actions.Decision:
    return asyncio.run(actions.decide_action(user_id=user_id, tenant_id=tenant_id,
                                             plugin_name=plugin, intent=intent))


def _open_plugin_gates(factory, *, tenant=TENANT, name=PLUGIN, perms=ACT_WRITE_PERMS,
                       plugin_kill_switch=True, graylist=True, force_dry_run=None):
    """按参数摆出**插件通道**的各道闸门（P17 用例逐层验灰度，故每一层都能单独关）。

    - ``plugin_kill_switch``：③a 总闸 ``device_actions_plugin_enabled``（True＝插 enabled=True 行，
      False＝插 enabled=False 行，``None``＝**不插行**（缺省即关））；
    - ``graylist``：是否把 ``name`` 放进进程内灰度白名单；
    - ``force_dry_run``：``None``＝不插行（缺省即「强制干跑」），True/False＝插对应值的行。
    """
    asyncio.run(_set_global(factory))
    asyncio.run(_set_account(factory, USER))
    if plugin_kill_switch is not None:
        asyncio.run(_set_flag(factory, actions.PLUGIN_KILL_SWITCH_KEY, plugin_kill_switch))
    if perms:
        asyncio.run(_install_consented(factory, tenant=tenant, name=name, perms=perms))
    if graylist:
        asyncio.run(actions.allow_plugin_actions(name))
    if force_dry_run is not None:
        asyncio.run(_set_flag(factory, actions.FORCE_DRY_RUN_KEY, force_dry_run))
    asyncio.run(actions.allow_target(tenant, TARGET))


def _open_all_gates(factory, *, tenant=TENANT):
    """插件通道的闸门全开（①② 置真 + ③a 总闸与该插件灰度放开 + ③b 三条行动能力逐条授权
    + ④白名单命中 TARGET + 关掉强制干跑）。

    ``tenant`` ＝授权与白名单落在哪个家庭根：走 API 的用例要传当前账号自己（独立主账号的
    家庭根就是它本身，见 ``family_service.get_family_root_id``）。

    ``force_dry_run=False`` 那条是 P17 追加的：该键**缺省即开**（读不到＝强制干跑），不关掉则
    「放行」只到 dry_run、拿不到 token，本文件 M4a/M4b 用例的「批准即入队」断言才需要显式放开。
    """
    _open_plugin_gates(factory, tenant=tenant, force_dry_run=False)


def _audit_lines(records) -> list[dict]:
    """审计记录 → 字段字典（只收「12 字段且顺序完全一致」的行，其它日志不参与判定）。"""
    out = []
    for rec in records:
        parts = rec.getMessage().split(" ")
        if not all("=" in p for p in parts):
            continue
        parsed = dict(p.split("=", 1) for p in parts)
        if tuple(parsed) == AUDIT_FIELDS:
            out.append(parsed)
    return out


# ── ① 全局 kill switch：读不到行＝关 ──
def test_全局开关缺省视为关并拒绝(act_db):
    d = _decide(_tap())
    assert d.allowed is False
    assert d.reason == actions.REASON_GLOBAL_OFF
    assert d.status == actions.STATUS_DENIED and d.action_token is None
    assert actions.take_pending(USER) == []

    # 显式写 enabled=False 同样拒（「有行但为关」与「无行」同口径）
    asyncio.run(_set_global(act_db, enabled=False))
    assert _decide(_tap()).reason == actions.REASON_GLOBAL_OFF


def test_全局开关开但账号级未开仍拒(act_db):
    asyncio.run(_set_global(act_db))
    assert _decide(_tap()).reason == actions.REASON_ACCOUNT_OFF
    # 缺省关：显式写 False 的账号也拒
    asyncio.run(_set_account(act_db, USER, enabled=False))
    assert _decide(_tap()).reason == actions.REASON_ACCOUNT_OFF


# ── ② 插件级：未授权即拒（fail-closed）──
def test_开关全开但插件未授权一律拒(act_db):
    asyncio.run(_set_global(act_db))
    asyncio.run(_set_account(act_db, USER))
    # P17：③a（总闸 + 该插件灰度）先放开，才谈得上验 ③b 的能力授权拒绝
    asyncio.run(_set_flag(act_db, actions.PLUGIN_KILL_SWITCH_KEY))
    asyncio.run(actions.allow_plugin_actions(PLUGIN))
    asyncio.run(actions.allow_target(TENANT, TARGET))
    # 未安装
    assert _decide(_tap()).reason == actions.REASON_PLUGIN_UNAUTHORIZED
    # 已安装但只同意了**只读**权限（读写权限分离：同意 notifications:read 不放开行动）
    asyncio.run(_install_consented(act_db, perms=["device:notifications:read"]))
    assert _decide(_tap()).reason == actions.REASON_PLUGIN_UNAUTHORIZED
    # 正向前提：补上行动 :write 授权即放行（证明上一条拒因来自授权判定，而非闸门顺序写反）
    asyncio.run(_install_consented(act_db, perms=["device:action_tap:write"]))
    assert _decide(_tap()).allowed is True
    # 已安装已同意、但租户不是归属租户（跨家庭不得借用同意）
    assert _decide(_tap(), tenant_id=OTHER_TENANT).reason == actions.REASON_PLUGIN_UNAUTHORIZED


# ── ③ 目标白名单默认空集 ──
def test_白名单空集全拒且黑名单优先(act_db, monkeypatch):
    _open_all_gates(act_db)
    assert asyncio.run(actions.configured_targets(TENANT)) == frozenset({TARGET})
    assert asyncio.run(actions.configured_targets(None)) == frozenset()
    other = ActionIntent(capability="action_open_app", target_app="com.other.app")
    assert _decide(other).reason == actions.REASON_TARGET_NOT_ALLOWED
    # 黑名单优先：同一条目既在白名单又在黑名单 → 拒
    monkeypatch.setattr(actions, "TARGET_BLACKLIST", frozenset({TARGET}))
    assert _decide(_tap()).reason == actions.REASON_TARGET_BLACKLISTED


# ── ④ 干跑：放行但不产生任何动作 ──
def test_白名单命中加干跑不产生动作(act_db):
    _open_all_gates(act_db)
    d = _decide(_tap(dry_run=True))
    assert d.allowed is True and d.reason == ""
    assert d.dry_run is True and d.status == actions.STATUS_DRY_RUN
    assert d.action_token is None, "干跑不得下发 token（无动作可执行）"
    assert actions.take_pending(USER) == [], "干跑不得入队"


def test_白名单命中放行只到队列执行体不存在(act_db):
    _open_all_gates(act_db)
    d = _decide(_tap())
    assert d.allowed is True and d.status == actions.STATUS_NOT_IMPLEMENTED
    assert d.action_token
    items = actions.take_pending(USER)
    assert len(items) == 1 and items[0]["action_token"] == d.action_token
    assert items[0]["status"] == "not_implemented" and items[0]["action"] == "tap"


# ── ⑤ 意图字段按能力校验 ──
def test_tap缺字段与越权字段一律校验拒(act_db):
    _open_all_gates(act_db)
    assert _decide(ActionIntent(capability="action_tap", target_app=TARGET)).reason == (
        "invalid_intent:missing_field:by")
    assert _decide(_tap(query=None)).reason == "invalid_intent:missing_field:query"
    assert _decide(_tap(by="coords")).reason == "invalid_intent:bad_value:by:coords"
    # open_app 只认 target_app
    assert _decide(ActionIntent(capability="action_open_app", target_app=TARGET,
                                query="下单")).reason == "invalid_intent:unexpected_field:query"
    # 只读能力不得当行动提交（读写分离）
    assert _decide(ActionIntent(capability="battery", target_app=TARGET)).reason == (
        "invalid_intent:not_an_action:battery")
    assert _decide(ActionIntent(capability="action_nope", target_app=TARGET)).reason == (
        "invalid_intent:unknown_capability:action_nope")


# ── ⑥ 限流 10 次/分钟 ──
def test_限流第十一次拒绝(act_db):
    _open_all_gates(act_db)
    for i in range(actions.RATE_LIMIT_PER_MINUTE):
        assert _decide(_tap()).allowed is True, f"第 {i + 1} 次应放行"
    denied = _decide(_tap())
    assert denied.allowed is False and denied.reason == actions.REASON_RATE_LIMITED
    # 限流按 (租户, 插件) 计数：换插件不受牵连（P17：新插件名同样要先灰度放开才走得到限流）
    asyncio.run(_install_consented(act_db, name="act_probe_b", perms=ACT_WRITE_PERMS))
    asyncio.run(actions.allow_plugin_actions("act_probe_b"))
    assert _decide(_tap(), plugin="act_probe_b").allowed is True


# ── ⑦ 连续 5 次失败回报 → 熔断 ──
def test_连续失败五次后熔断(act_db):
    _open_all_gates(act_db)
    for i in range(actions.CIRCUIT_BREAK_THRESHOLD):
        token = _decide(_tap()).action_token
        assert token, f"第 {i + 1} 次应放行"
        assert actions.report_result(token, False, "app_not_found", user_id=USER) is True
    assert _decide(_tap()).reason == actions.REASON_CIRCUIT_OPEN
    # 熔断按 (租户, 插件)：其它插件不受牵连（P17：先灰度放开该插件）
    asyncio.run(_install_consented(act_db, name="act_probe_c", perms=ACT_WRITE_PERMS))
    asyncio.run(actions.allow_plugin_actions("act_probe_c"))
    assert _decide(_tap(), plugin="act_probe_c").allowed is True


def test_成功回报清零失败计数(act_db):
    _open_all_gates(act_db)
    for _ in range(actions.CIRCUIT_BREAK_THRESHOLD - 1):
        token = _decide(_tap()).action_token
        assert actions.report_result(token, False, "failed", user_id=USER) is True
    ok_token = _decide(_tap()).action_token
    assert actions.report_result(ok_token, True, "ok", user_id=USER) is True
    # 清零后重新累计：再 4 次失败仍不熔断（合计恰好 10 次请求，不触发限流）
    for _ in range(actions.CIRCUIT_BREAK_THRESHOLD - 1):
        token = _decide(_tap()).action_token
        assert token, "熔断计数已被成功回报清零，此处应仍放行"
        assert actions.report_result(token, False, "failed", user_id=USER) is True


# ── ⑧ pending / result 往返 + 审计带 user_id ──
def test_pending与回报往返且审计含user_id(act_db, actions_log_records):
    _open_all_gates(act_db)
    token = _decide(_tap()).action_token
    assert actions.take_pending(USER)[0]["capability"] == "action_tap"
    assert actions.report_result(token, True, "done", user_id=USER) is True
    assert actions.take_pending(USER) == [], "回报后应出队"
    assert actions.report_result(token, True, "again", user_id=USER) is False, "重复回报不命中"

    lines = _audit_lines(actions_log_records)
    decide = next(line for line in lines if line["result"] == "approved")
    assert decide["user_id"] == str(USER), "M4a 审计必须带 user_id（补 M3 缺口）"
    assert decide["tenant_id"] == str(TENANT) and decide["plugin"] == PLUGIN
    assert decide["capability"] == "action_tap" and decide["action"] == "tap"
    assert decide["target_app"] == TARGET and decide["dry_run"] == "false"
    report = next(line for line in lines if line["result"] == "report_ok")
    assert report["user_id"] == str(USER) and report["reason"] == "done"
    assert all(r.levelno == logging.INFO for r in actions_log_records)
    # 未知 token 也要留痕（字段形状与审计一致，result 单独可辨）
    assert any(line["result"] == "unknown_token" and line["user_id"] == str(USER) for line in lines)


# ── ⑨ 跨租户 / 跨账号隔离 ──
def test_跨账号取不到pending且不得替他人回报(act_db):
    _open_all_gates(act_db)
    token = _decide(_tap()).action_token
    assert actions.take_pending(USER) and actions.take_pending(OTHER_USER) == []
    assert actions.take_pending(None) == []
    # 别人的 token 替不了：回报被拒，条目仍在原主队列里，且**不累计失败**（刷不熔断）
    for _ in range(actions.CIRCUIT_BREAK_THRESHOLD):
        assert actions.report_result(token, False, "hijack", user_id=OTHER_USER) is False
    assert [i["action_token"] for i in actions.take_pending(USER)] == [token]
    assert _decide(_tap()).allowed is True


# ── ⑩ 行动能力契约：与 :write 权限名一一对应 ──
def test_行动能力与write权限名一一对应():
    act = {cid: spec for cid, spec in CAPABILITIES.items() if spec.kind == "act"}
    assert set(act) == {"action_open_app", "action_tap", "action_set_text"}
    for cid, spec in act.items():
        assert spec.permission == f"device:{cid}:write"
        assert spec.permission in capability_permissions()
        assert spec.requires == "accessibility"
        assert spec.sensitive is True
        assert spec.confirmation == "first_per_type"
        assert spec.sources == ()
        assert "target_app" in spec.schema and "dry_run" in spec.schema


# ── API 接线：被拒一律 200 + reason；批准下发 token 但 status=not_implemented ──
# P17（X7-M4c-1）身份收口后：本端点恒为内置通道，请求体**不带** plugin 字段（带了即拒，见 P17 用例 ①）
def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(device_actions_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_API三端点往返与拒绝口径(act_db):
    client = _client(USER)

    # 闸门未开：200 + allowed=false + reason（不抛 4xx）
    r = client.post("/api/v1/device/actions",
                    json={"capability": "action_tap",
                          "target_app": TARGET, "by": "text", "query": "下单"})
    assert r.status_code == 200 and r.json()["allowed"] is False
    assert r.json()["reason"] == actions.REASON_GLOBAL_OFF

    # 形状错误（缺 capability / 多带节点字段）也不抛 422，一律 invalid_intent
    r = client.post("/api/v1/device/actions", json={"target_app": TARGET})
    assert r.status_code == 200 and r.json()["allowed"] is False
    assert r.json()["reason"] == "invalid_intent:field_required:capability"
    r = client.post("/api/v1/device/actions",
                    json={"capability": "action_open_app",
                          "target_app": TARGET, "node_index": 3})
    assert r.json()["reason"] == "invalid_intent:unknown_field:node_index"

    # 端点侧租户由当前账号解析（独立主账号＝它自己），故授权与白名单都落在 USER 上
    _open_builtin_gates(act_db, tenant=USER)
    r = client.post("/api/v1/device/actions",
                    json={"capability": "action_set_text",
                          "target_app": TARGET, "text": "hello"})
    body = r.json()
    assert body["allowed"] is True and body["status"] == "not_implemented"
    token = body["action_token"]
    assert token

    pending = client.get("/api/v1/device/actions/pending").json()
    assert [i["action_token"] for i in pending] == [token]
    assert pending[0]["action"] == "set_text" and pending[0]["text"] == "hello"
    # 换账号取不到（跨账号/跨租户 fail-closed）
    assert _client(OTHER_USER).get("/api/v1/device/actions/pending").json() == []
    assert client.post(f"/api/v1/device/actions/{token}/result",
                       json={"ok": True, "detail": "done"}).json() == {"ok": True}
    assert client.get("/api/v1/device/actions/pending").json() == []
    # 回报过的 token 再回报 → ok=false（仍 200）
    assert client.post(f"/api/v1/device/actions/{token}/result",
                       json={"ok": True}).json() == {"ok": False}


# ══════════════════════════════════════════════════════════════════════════════
# 派单 P14（X7-M4b-1）① 内置调用方通道：闸门③ 跳过，其余闸门与护栏一条不少
# ══════════════════════════════════════════════════════════════════════════════

BUILTIN = actions.BUILTIN_CALLER


def _open_builtin_gates(factory, *, tenant=TENANT):
    """内置通道要过的那几道闸门：①全局 ②账号 ④目标白名单（**不装任何插件**＝闸门③ 无判据）。"""
    asyncio.run(_set_global(factory))
    asyncio.run(_set_account(factory, USER))
    asyncio.run(actions.allow_target(tenant, TARGET))


def test_builtin跳过插件授权闸门且照常入队(act_db, actions_log_records):
    _open_builtin_gates(act_db)
    d = _decide(_tap(), plugin=BUILTIN)
    assert d.allowed is True and d.reason == "", "①②④ 全开时内置必须放行（闸门③ 不再拦）"
    assert d.status == actions.STATUS_NOT_IMPLEMENTED and d.action_token
    items = actions.take_pending(USER)
    assert [i["action_token"] for i in items] == [d.action_token]
    assert items[0]["plugin"] == BUILTIN

    line = next(row for row in _audit_lines(actions_log_records) if row["result"] == "approved")
    assert line["plugin"] == BUILTIN, "审计里 plugin=builtin 要一眼看出是内置发起"
    assert line["user_id"] == str(USER) and line["tenant_id"] == str(TENANT)


def test_builtin在全局开关关闭时仍拒(act_db):
    asyncio.run(_set_account(act_db, USER))  # ②④ 开、只缺 ①
    asyncio.run(actions.allow_target(TENANT, TARGET))
    d = _decide(_tap(), plugin=BUILTIN)
    assert d.allowed is False and d.reason == actions.REASON_GLOBAL_OFF
    assert actions.take_pending(USER) == []


def test_builtin在账号开关关闭时仍拒(act_db):
    asyncio.run(_set_global(act_db))  # ①④ 开、只缺 ②
    asyncio.run(actions.allow_target(TENANT, TARGET))
    d = _decide(_tap(), plugin=BUILTIN)
    assert d.allowed is False and d.reason == actions.REASON_ACCOUNT_OFF


def test_builtin仍受目标白名单与黑名单约束(act_db, monkeypatch):
    _open_builtin_gates(act_db)
    other = ActionIntent(capability="action_open_app", target_app="com.other.app")
    assert _decide(other, plugin=BUILTIN).reason == actions.REASON_TARGET_NOT_ALLOWED
    monkeypatch.setattr(actions, "TARGET_BLACKLIST", frozenset({TARGET}))
    assert _decide(_tap(), plugin=BUILTIN).reason == actions.REASON_TARGET_BLACKLISTED


def test_builtin的限流与熔断照旧生效(act_db):
    _open_builtin_gates(act_db)
    for i in range(actions.CIRCUIT_BREAK_THRESHOLD):
        token = _decide(_tap(), plugin=BUILTIN).action_token
        assert token, f"第 {i + 1} 次应放行"
        assert actions.report_result(token, False, "app_not_found", user_id=USER) is True
    assert _decide(_tap(), plugin=BUILTIN).reason == actions.REASON_CIRCUIT_OPEN
    # 熔断/限流按 (租户, 插件) 计数：内置那一路熔断不牵连已授权插件
    # （P17：该插件走的是插件通道，③a 总闸＋灰度先放开，才轮到验 ③b/护栏）
    asyncio.run(_set_flag(act_db, actions.PLUGIN_KILL_SWITCH_KEY))
    asyncio.run(actions.allow_plugin_actions(PLUGIN))
    asyncio.run(_install_consented(act_db, perms=ACT_WRITE_PERMS))
    assert _decide(_tap(), plugin=PLUGIN).allowed is True


def test_非内置插件未授权仍被闸门三拦(act_db):
    """与内置同状态（①②④ 全开、库里没有任何插件行）下的回归：非内置名一律 plugin_unauthorized。

    P17 起 ③ 拆成 ③a（总闸＋灰度）与 ③b（能力授权）：本例先把 ③a 放开（灰度名单只是**名字**，
    不代表插件已安装），才钉住「闸门③ 的拒绝来自 ③b 的能力授权判定」——名字近似内置的
    （``BUILTIN`` / ``Builtin``）同样不跳过 ③b。（③a 自身缺省即拒另有 P17 用例 ③④。）
    """
    _open_builtin_gates(act_db)
    asyncio.run(_set_flag(act_db, actions.PLUGIN_KILL_SWITCH_KEY))
    for name in (PLUGIN, "act_probe_not_installed", "BUILTIN", "Builtin"):
        asyncio.run(actions.allow_plugin_actions(name))
        d = _decide(_tap(), plugin=name)
        assert d.allowed is False and d.reason == actions.REASON_PLUGIN_UNAUTHORIZED, name


# ══════════════════════════════════════════════════════════════════════════════
# 派单 P14（X7-M4b-1）② 目标白名单管理端点（挂 require_server_admin，内存实现）
# ══════════════════════════════════════════════════════════════════════════════

TARGETS_URL = "/api/v1/device/actions/targets"
# 阈值取自被测模块，本文件不另立字面量（避免改了端点常量而测试仍然绿）
TARGET_MAX_LEN = device_actions_api.TARGET_MAX_LEN
MAX_TARGETS_PER_TENANT = device_actions_api.MAX_TARGETS_PER_TENANT


async def _seed_server_admin(factory, user_id: int, *, enabled: bool) -> None:
    """落一行 users.server_admin —— 管理端点走真实判定（DB 权威），不 fake 绕过依赖。"""
    from app.models.user import User

    async with factory() as db:
        row = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if row is None:
            db.add(User(id=user_id, username=f"u{user_id}", nickname=f"账号{user_id}",
                        is_admin=True, server_admin=enabled))
        else:
            row.server_admin = enabled
        await db.commit()


def _as_server_admin(monkeypatch, factory, user_id: int, *, enabled: bool = True) -> None:
    """把 server_admin 判定接到本用例私有库（permission_service 是早绑定 import，须换它自己的名字）。"""
    from app.application import permission_service as perm

    asyncio.run(_seed_server_admin(factory, user_id, enabled=enabled))
    monkeypatch.setattr(perm, "async_session_factory", factory)
    perm._server_admin_cache.clear()


def _submit_tap(client) -> dict:
    """走内置端点提交一条 tap（P17 身份收口后请求体不再接受 ``plugin`` 字段）。"""
    return client.post("/api/v1/device/actions",
                       json={"capability": "action_tap",
                             "target_app": TARGET, "by": "text", "query": "下单"}).json()


def test_管理员放开目标后同一意图由拒变放(act_db, monkeypatch):
    _as_server_admin(monkeypatch, act_db, USER)
    asyncio.run(_set_global(act_db))
    asyncio.run(_set_account(act_db, USER))
    asyncio.run(_install_consented(act_db, tenant=USER, perms=ACT_WRITE_PERMS))
    client = _client(USER)

    # 白名单空集 → 闸门④ 拒（端点侧租户由当前账号解析，故授权落在 USER）
    denied = _submit_tap(client)
    assert denied["allowed"] is False and denied["reason"] == actions.REASON_TARGET_NOT_ALLOWED
    assert client.get(TARGETS_URL).json() == {"targets": []}

    # 带空白的入参落库前 strip；返回排序后的全量白名单
    r = client.post(TARGETS_URL, json={"target": f"  {TARGET}  "})
    assert r.status_code == 200 and r.json() == {"ok": True, "targets": [TARGET]}, r.text

    approved = _submit_tap(client)
    assert approved["allowed"] is True and approved["status"] == "not_implemented"
    assert client.get(TARGETS_URL).json() == {"targets": [TARGET]}
    # 同一条闸门④ 对内置通道同样成立：白名单命中后再次放行
    assert _submit_tap(client)["allowed"] is True


def test_非管理员调用目标白名单端点一律403(act_db, monkeypatch):
    _as_server_admin(monkeypatch, act_db, USER, enabled=True)
    _as_server_admin(monkeypatch, act_db, OTHER_USER, enabled=False)
    outsider = _client(OTHER_USER)
    assert outsider.post(TARGETS_URL, json={"target": TARGET}).status_code == 403
    assert outsider.get(TARGETS_URL).status_code == 403

    # 未登录（不覆盖 get_current_user_id）→ 401，不落到 200 口径
    app = FastAPI()
    app.include_router(device_actions_api.router)
    bare = TestClient(app)
    assert bare.post(TARGETS_URL, json={"target": TARGET}).status_code == 401
    assert bare.get(TARGETS_URL).status_code == 401

    # 越权者改不动闸门④：白名单仍为空
    assert _client(USER).get(TARGETS_URL).json() == {"targets": []}


def test_白名单按租户隔离另一个家庭看不到(act_db, monkeypatch):
    _as_server_admin(monkeypatch, act_db, USER)
    _as_server_admin(monkeypatch, act_db, OTHER_USER)
    client = _client(USER)
    assert client.post(TARGETS_URL, json={"target": TARGET}).json()["ok"] is True
    assert _client(OTHER_USER).get(TARGETS_URL).json() == {"targets": []}, "别的家庭看不到"


def test_非法目标一律200加invalid_target且白名单不变(act_db, monkeypatch):
    _as_server_admin(monkeypatch, act_db, USER)
    client = _client(USER)
    # 首尾空白按既有口径先 strip（故 "  com.x.app  " 属合法）；但**内部**空格/换行/分号必须判非法
    bad = ["", "   ", "com.a b", "com.example;rm", "com/example/app", "com..example",
           ".com.example", "com.example.", "singlesegment", "1com.example.app",
           "-com.example.app", "com.示例.app", "x" * 129, "com.example.app\ncom.evil.app"]
    for target in bad:
        r = client.post(TARGETS_URL, json={"target": target})
        assert r.status_code == 200, target
        body = r.json()
        assert body["ok"] is False and body["reason"] == "invalid_target", target
        assert body["targets"] == [], target
    # 缺 target 字段同样按 invalid_target（不抛 422）
    assert client.post(TARGETS_URL, json={}).json()["reason"] == "invalid_target"
    assert client.get(TARGETS_URL).json() == {"targets": []}

    # 边界：恰 128 字符且合形 → 放行（证明长度护栏没有误伤）
    edge = "com." + "a" * 124
    assert len(edge) == TARGET_MAX_LEN
    assert client.post(TARGETS_URL, json={"target": edge}).json()["ok"] is True


def test_目标白名单容量上限二十个超出即拒(act_db, monkeypatch):
    _as_server_admin(monkeypatch, act_db, USER)
    client = _client(USER)
    targets = [f"com.a{i}.b" for i in range(MAX_TARGETS_PER_TENANT)]
    for t in targets:
        assert client.post(TARGETS_URL, json={"target": t}).json()["ok"] is True, t
    r = client.post(TARGETS_URL, json={"target": "com.over.b"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert r.json()["reason"] == "too_many_targets"
    assert client.get(TARGETS_URL).json() == {"targets": sorted(targets)}, "超容量不得写入"
    # 重复添加已有目标不占新额度（集合语义）
    again = client.post(TARGETS_URL, json={"target": targets[0]}).json()
    assert again["ok"] is True and again["targets"] == sorted(targets)


# ══════════════════════════════════════════════════════════════════════════════
# 派单 P17（X7-M4c-1）：身份收口 + 插件提交通道（桥 device_action）+ 插件灰度闸
# ══════════════════════════════════════════════════════════════════════════════

BRIDGE_URL = f"/api/v1/plugins/{PLUGIN}/bridge"
TAP_PARAMS = {"capability": "action_tap", "target_app": TARGET, "by": "text", "query": "下单"}
# 自称身份的拒绝原因（内置端点与桥 params 共用一份口径）
REASON_SELF_IDENTITY = f"{actions.REASON_INVALID_INTENT}:{actions.REASON_PLUGIN_NOT_ALLOWED}"


def _bridge_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_bridge_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _register_plugin_in_runtime(monkeypatch, name: str = PLUGIN) -> None:
    """把插件名挂进 registry 的内存注册表（桥端点「插件存在」这道校验只看这份缓存）。

    既有桥测试用 ``registry.load_plugin_dir`` 装载真实示例插件；本文件的被测插件没有目录实体，
    授权判据（安装行 + 逐条同意）在 ``plugins`` / ``plugin_consents`` 表里，故只补注册表视图，
    经 ``monkeypatch`` 还原，不污染同会话其它用例。
    """
    entry = {"info": {"name": name, "version": "1.0.0", "description": "", "author": "",
                      "category": "plugin", "type": "http", "config": {}, "permissions": []}}
    monkeypatch.setattr(registry, "_loaded", {**registry._loaded, name: entry})
    monkeypatch.setattr(registry, "_enabled", {**registry._enabled, name: True})


# ── ① 身份收口：请求体自称 plugin 一律拒（M4b-1 留下的洞） ──
def test_请求体自称plugin身份一律拒绝即使四道闸门全开(act_db):
    """M4b-1 的权限模型洞：闸门③ 对 ``builtin`` 短路，而身份取自请求体 ⇒ 任何登录调用方自称
    ``builtin`` 即可绕过「插件逐条能力授权」。收口后本端点身份恒为内置，**自称反而一律拒**。
    """
    _open_all_gates(act_db, tenant=USER)  # 全开＝若身份仍可伪造，这条意图必然被放行
    client = _client(USER)
    for claimed in (BUILTIN, PLUGIN, "any_plugin", "  builtin  "):
        body = client.post("/api/v1/device/actions",
                           json={**TAP_PARAMS, "plugin": claimed}).json()
        assert body["allowed"] is False, claimed
        assert body["reason"] == REASON_SELF_IDENTITY, claimed
        assert body["status"] == actions.STATUS_DENIED and "action_token" not in body, claimed
    # dry_run 原样回显（结论依旧是被拒，不替调用方改判）
    echoed = client.post("/api/v1/device/actions",
                         json={**TAP_PARAMS, "plugin": PLUGIN, "dry_run": True}).json()
    assert echoed["dry_run"] is True and echoed["allowed"] is False
    assert actions.take_pending(USER) == [], "自称身份不得留下任何待执行项"
    # plugin 为空串/纯空白＝「未声明身份」，按内置照常过闸门（收口只针对自称，不针对空值）
    blank = client.post("/api/v1/device/actions",
                        json={**TAP_PARAMS, "plugin": "   "}).json()
    assert blank["allowed"] is True and blank["status"] == actions.STATUS_NOT_IMPLEMENTED


# ── ② 内置提交不带 plugin 照旧可通（回归）＋ 审计里身份恒为 builtin ──
def test_内置端点身份恒为builtin且照常入队(act_db, actions_log_records):
    _open_builtin_gates(act_db, tenant=USER)
    body = _submit_tap(_client(USER))
    assert body["allowed"] is True and body["status"] == actions.STATUS_NOT_IMPLEMENTED
    assert body["action_token"]
    assert [i["plugin"] for i in actions.take_pending(USER)] == [BUILTIN]
    line = next(r for r in _audit_lines(actions_log_records) if r["result"] == "approved")
    assert line["plugin"] == BUILTIN and line["user_id"] == str(USER)
    assert line["tenant_id"] == str(USER)


# ── ③ 插件总闸（device_actions_plugin_enabled）：缺行与显式关同口径 ──
def test_插件总闸缺行视为关并拒绝(act_db):
    _open_plugin_gates(act_db, plugin_kill_switch=None)  # 一行都不插
    d = _decide(_tap())
    assert d.allowed is False and d.reason == actions.REASON_PLUGIN_ACTIONS_DISABLED
    assert d.status == actions.STATUS_DENIED and d.action_token is None
    assert actions.take_pending(USER) == []


def test_插件总闸显式关闭同样拒绝(act_db):
    _open_plugin_gates(act_db, plugin_kill_switch=False)
    assert _decide(_tap()).reason == actions.REASON_PLUGIN_ACTIONS_DISABLED


def test_插件总闸未开时灰度与授权都救不回来(act_db):
    """顺序钉住 ③a 在 ③b 之前：能力授权齐备也不构成第二条放行路径。"""
    _open_plugin_gates(act_db, plugin_kill_switch=None)  # ③b 三条 :write 已授权＋已灰度
    assert asyncio.run(actions.plugin_graylisted(PLUGIN)) is True
    assert _decide(_tap()).reason == actions.REASON_PLUGIN_ACTIONS_DISABLED


# ── ④ 逐插件灰度白名单：默认空集全拒 ──
def test_灰度白名单默认空集全拒放开后放行且reset不清名单(act_db):
    assert actions.PLUGIN_ACTION_ENABLED_PLUGINS == frozenset(), "默认必须一条都不放"
    _open_plugin_gates(act_db, graylist=False)
    asyncio.run(actions.allow_plugin_actions("   "))  # 只含空白的名字 strip 后为空＝进不了名单
    assert asyncio.run(actions.plugin_graylisted("")) is False
    assert asyncio.run(actions.plugin_graylisted(PLUGIN)) is False
    assert _decide(_tap()).reason == actions.REASON_PLUGIN_NOT_GRAYLISTED
    asyncio.run(actions.allow_plugin_actions(PLUGIN))
    assert _decide(_tap()).allowed is True
    actions.reset_runtime_state()
    # M4c-3（2026-09-23）：两份名单已**落库**，reset 只清内存态（待执行队列 / 限流 / 熔断），
    # **不得清名单**——清库等于丢管理员配置。原「reset 一并清掉」断言随语义变更改写（非放宽）。
    assert asyncio.run(actions.plugin_graylisted(PLUGIN)) is True, "落库后的灰度名单不得被 reset 清掉"
    assert asyncio.run(actions.configured_targets(TENANT)) != frozenset(), "落库后的目标白名单不得被 reset 清掉"


# ── ⑤ 灰度放开不替代能力授权（③b 回归） ──
def test_灰度放开不替代能力授权(act_db):
    _open_plugin_gates(act_db, perms=())  # 未安装（库里没有该插件行）
    assert _decide(_tap()).reason == actions.REASON_PLUGIN_UNAUTHORIZED
    # 已安装、但只同意过只读权限（读写分离：同意 read 不放开行动）
    asyncio.run(_install_consented(act_db, perms=["device:notifications:read"]))
    assert _decide(_tap()).reason == actions.REASON_PLUGIN_UNAUTHORIZED


# ── ⑥⑧ 全开 + 强制干跑显式关闭 → 放行并发 token（并落插件名进队列/审计） ──
def test_插件通道全开且关闭强制干跑时发token(act_db, actions_log_records):
    _open_all_gates(act_db)  # 含 device_actions_force_dry_run=0 那一行
    d = _decide(_tap())
    assert d.allowed is True and d.reason == ""
    assert d.status == actions.STATUS_NOT_IMPLEMENTED and d.action_token
    items = actions.take_pending(USER)
    assert [i["plugin"] for i in items] == [PLUGIN], "队列里的身份是提交它的插件"
    line = next(r for r in _audit_lines(actions_log_records) if r["result"] == "approved")
    assert line["plugin"] == PLUGIN and line["tenant_id"] == str(TENANT)


# ── ⑦ 强制干跑：读不到行＝开（缺省更严） ──
def test_强制干跑未插行时插件提交只回结论不发token(act_db):
    _open_plugin_gates(act_db)  # 不插 force_dry_run 行
    d = _decide(_tap())
    assert d.allowed is True and d.reason == "", "闸门全过，只是不下发可执行凭据"
    assert d.status == actions.STATUS_DRY_RUN and d.action_token is None
    assert actions.take_pending(USER) == [], "强制干跑不得入队"


def test_强制干跑显式置真与缺省同口径(act_db):
    _open_plugin_gates(act_db, force_dry_run=True)
    d = _decide(_tap())
    assert d.allowed is True and d.status == actions.STATUS_DRY_RUN and d.action_token is None
    assert actions.take_pending(USER) == []


# ── ⑪ 内置通道不受三条插件闸影响（含强制干跑缺省即开的情形） ──
def test_内置通道不受插件灰度与强制干跑影响(act_db):
    _open_builtin_gates(act_db)  # 只有 ①②④：两把插件闸一行都没有（缺省＝插件关 + 强制干跑）
    d = _decide(_tap(), plugin=BUILTIN)
    assert d.allowed is True and d.status == actions.STATUS_NOT_IMPLEMENTED and d.action_token
    # 再把两把插件闸显式置上（总闸关 + 强制干跑开）：内置结论不变
    asyncio.run(_set_flag(act_db, actions.PLUGIN_KILL_SWITCH_KEY, False))
    asyncio.run(_set_flag(act_db, actions.FORCE_DRY_RUN_KEY, True))
    again = _decide(_tap(), plugin=BUILTIN)
    assert again.allowed is True and again.status == actions.STATUS_NOT_IMPLEMENTED
    assert again.action_token, "内置不受 force_dry_run 改写"


# ── ⑨ 桥通道：身份取路径插件名（不另立入口、不绕过三道校验） ──
def test_桥device_action身份取路径插件名(act_db, monkeypatch):
    _register_plugin_in_runtime(monkeypatch)
    _open_all_gates(act_db, tenant=USER)  # 桥侧租户由提交账号解析（独立主账号＝它自己）
    client = _bridge_client(USER)
    r = client.post(BRIDGE_URL, json={"api": "device_action", "params": dict(TAP_PARAMS)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    result = body["data"]
    assert result["allowed"] is True and result["status"] == actions.STATUS_NOT_IMPLEMENTED
    assert result["action_token"]
    items = actions.take_pending(USER)
    assert [i["plugin"] for i in items] == [PLUGIN], "身份只能来自路径名"
    # 未安装的插件名 → 404（复用桥既有校验，不给行动通道开后门）
    ghost = client.post("/api/v1/plugins/act_probe_ghost/bridge",
                        json={"api": "device_action", "params": dict(TAP_PARAMS)})
    assert ghost.status_code == 404
    assert len(actions.take_pending(USER)) == 1, "404 的那次不得留下待执行项"


def test_桥call统一入口同样按路径名放行(act_db, monkeypatch):
    """``api=call`` 递归一层也认 device_action（身份仍是路径名，不是 params 里的任何字段）。"""
    _register_plugin_in_runtime(monkeypatch)
    _open_all_gates(act_db, tenant=USER)
    r = _bridge_client(USER).post(BRIDGE_URL, json={
        "api": "call", "params": {"api": "device_action", "params": dict(TAP_PARAMS)}})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert r.json()["data"]["allowed"] is True
    assert [i["plugin"] for i in actions.take_pending(USER)] == [PLUGIN]


# ── ⑩ 桥 params 里塞 plugin → 拒（不接受自称） ──
def test_桥params塞plugin一律拒绝(act_db, monkeypatch):
    _register_plugin_in_runtime(monkeypatch)
    _open_all_gates(act_db, tenant=USER)
    client = _bridge_client(USER)
    for claimed in (BUILTIN, "other_plugin"):
        r = client.post(BRIDGE_URL, json={"api": "device_action",
                                          "params": {**TAP_PARAMS, "plugin": claimed}})
        assert r.status_code == 200, r.text
        result = r.json()["data"]
        assert result["allowed"] is False and result["reason"] == REASON_SELF_IDENTITY, claimed
        assert result["status"] == actions.STATUS_DENIED and "action_token" not in result, claimed
    assert actions.take_pending(USER) == []


# ── ⑫ 插件通道的限流/熔断仍按 (tenant, plugin) 独立记账 ──
def test_插件通道限流与熔断按租户插件独立记账(act_db):
    _open_all_gates(act_db)
    for i in range(actions.RATE_LIMIT_PER_MINUTE):
        assert _decide(_tap(), plugin=BUILTIN).allowed is True, f"内置第 {i + 1} 次应放行"
    assert _decide(_tap(), plugin=BUILTIN).reason == actions.REASON_RATE_LIMITED
    assert _decide(_tap()).allowed is True, "插件的令牌桶独立于内置那一路"
    # 插件侧连续失败回报 → 只熔断该插件
    for _ in range(actions.CIRCUIT_BREAK_THRESHOLD):
        token = _decide(_tap()).action_token
        assert token
        assert actions.report_result(token, False, "app_not_found", user_id=USER) is True
    assert _decide(_tap()).reason == actions.REASON_CIRCUIT_OPEN
    # 内置那一路仍只受限流约束（没被插件的熔断牵连）
    assert _decide(_tap(), plugin=BUILTIN).reason == actions.REASON_RATE_LIMITED


# ── 桥白名单只多这一条 + params 形状错误仍是同一份 invalid_intent 口径 ──
def test_桥白名单只新增device_action一条(act_db, monkeypatch):
    assert set(plugin_bridge_service.VALID_APIS) == {
        "ai", "getAiList", "getAiInfo", "getUserInfo", "store.set", "store.get", "http", "call",
        "device_action"}
    _register_plugin_in_runtime(monkeypatch)
    client = _bridge_client(USER)
    # 近似名仍按未知 api 400（不是 200 + reason：这一层是「桥不认这个 api」而非「行动被闸门拒」）
    assert client.post(BRIDGE_URL,
                       json={"api": "device_actions", "params": dict(TAP_PARAMS)}).status_code == 400
    _open_all_gates(act_db, tenant=USER)
    r = client.post(BRIDGE_URL, json={"api": "device_action",
                                      "params": {"target_app": TARGET}})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["data"]["reason"] == "invalid_intent:field_required:capability"


# ══════════════════════════════════════════════════════════════════════════════
# 派单 P19（X7-M4c-3）：两份名单落库（幂等写 / 跨会话持久 / 读失败 fail-closed / 管理端点）
# ══════════════════════════════════════════════════════════════════════════════

PLUGINS_URL = "/api/v1/device/actions/plugins"
# 阈值取自被测模块，本文件不另立字面量（避免改了端点常量而测试仍然绿）
MAX_PLUGINS_GRAYLISTED = device_actions_api.MAX_PLUGINS_GRAYLISTED


async def _model_rows(factory, model) -> list:
    async with factory() as db:
        return list((await db.execute(select(model))).scalars().all())


def _targets_model():
    from app.models.device import DeviceActionTarget
    return DeviceActionTarget


def _plugins_model():
    from app.models.device import DeviceActionPlugin
    return DeviceActionPlugin


def _reopen(factory):
    """在**同一个库文件**上开一个全新 engine（NullPool：每条连接重新开文件）。

    用途：证明名单在盘上、不在进程里——换 engine + 清进程内状态之后仍然读得到才算落库。
    返回 ``(新会话工厂, 新 engine)``，调用方负责 dispose。
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(factory.kw["bind"].url, poolclass=NullPool)
    return make_session_factory(engine), engine


# ── ① 语义回归：表在位但一行没有＝闸门④ 空集＝全拒 ──
def test_落库后默认无行仍然目标全拒(act_db):
    asyncio.run(_set_global(act_db))
    asyncio.run(_set_account(act_db, USER))
    assert asyncio.run(actions.configured_targets(TENANT)) == frozenset()
    assert _decide(_tap(), plugin=BUILTIN).reason == actions.REASON_TARGET_NOT_ALLOWED
    assert actions.take_pending(USER) == [], "被拒不得留下待执行项"


# ── ② allow_target 写库后读得到，且闸门④ 真的取库里的值 ──
def test_allow_target写库后名单读得到且闸门四随之放行(act_db):
    assert asyncio.run(actions.allow_target(TENANT, TARGET)) is True
    assert asyncio.run(actions.configured_targets(TENANT)) == frozenset({TARGET})
    assert asyncio.run(actions.configured_targets(OTHER_TENANT)) == frozenset(), "按租户隔离"
    assert [r.target for r in asyncio.run(_model_rows(act_db, _targets_model()))] == [TARGET]

    asyncio.run(_set_global(act_db))
    asyncio.run(_set_account(act_db, USER))
    assert _decide(_tap(), plugin=BUILTIN).allowed is True


# ── ③ 跨会话持久（本批核心验收）：清掉进程内状态 + 换一条新连接后白名单仍在 ──
def test_目标白名单落库后跨会话仍然有效(act_db, monkeypatch):
    asyncio.run(actions.allow_target(TENANT, TARGET))
    asyncio.run(_set_global(act_db))
    asyncio.run(_set_account(act_db, USER))
    fresh, engine = _reopen(act_db)
    try:
        monkeypatch.setattr(database, "async_session_factory", fresh)
        actions.reset_runtime_state()          # 只清运行时统计：名单不得被它清掉
        assert asyncio.run(actions.configured_targets(TENANT)) == frozenset({TARGET})
        assert _decide(_tap(), plugin=BUILTIN).allowed is True, "闸门④ 必须仍认这条库里的目标"
    finally:
        engine.sync_engine.dispose()


# ── ④ 插件灰度名单同理：落库后换会话仍命中闸门③a ──
def test_插件灰度名单落库后跨会话仍然有效(act_db, monkeypatch):
    asyncio.run(_set_global(act_db))
    asyncio.run(_set_account(act_db, USER))
    asyncio.run(_set_flag(act_db, actions.PLUGIN_KILL_SWITCH_KEY))
    asyncio.run(_install_consented(act_db, perms=ACT_WRITE_PERMS))
    asyncio.run(actions.allow_target(TENANT, TARGET))
    assert asyncio.run(actions.allow_plugin_actions(PLUGIN)) is True
    assert asyncio.run(actions.plugin_graylisted(PLUGIN)) is True
    fresh, engine = _reopen(act_db)
    try:
        monkeypatch.setattr(database, "async_session_factory", fresh)
        actions.reset_runtime_state()
        assert asyncio.run(actions.plugin_graylisted(PLUGIN)) is True, "灰度名单在库里，不在进程里"
        d = _decide(_tap())
        assert d.allowed is True and d.reason == "", "③a 的灰度判定要取自库里的名单"
    finally:
        engine.sync_engine.dispose()


# ── ⑤ 幂等：重复添加不报错、不产生第二行、读库仍是同一条 ──
def test_两份名单重复添加幂等不产生第二行(act_db):
    for _ in range(3):
        assert asyncio.run(actions.allow_target(TENANT, TARGET)) is True
        assert asyncio.run(actions.allow_plugin_actions(PLUGIN)) is True
    assert len(asyncio.run(_model_rows(act_db, _targets_model()))) == 1
    assert len(asyncio.run(_model_rows(act_db, _plugins_model()))) == 1
    assert asyncio.run(actions.configured_targets(TENANT)) == frozenset({TARGET})
    assert asyncio.run(actions.configured_plugins()) == frozenset({PLUGIN})
    # 空白名进不了名单（既有口径：只含空白的名字一律忽略）
    assert asyncio.run(actions.allow_plugin_actions("   ")) is False
    assert len(asyncio.run(_model_rows(act_db, _plugins_model()))) == 1


# ── ⑥ 超上限仍拒：插件灰度名单上限 MAX_PLUGINS_GRAYLISTED（端点侧按库里条数判） ──
def test_插件灰度名单超上限即拒且不写入(act_db, monkeypatch):
    _as_server_admin(monkeypatch, act_db, USER)
    client = _client(USER)
    names = [f"gray_plugin_{i}" for i in range(MAX_PLUGINS_GRAYLISTED)]
    for n in names:
        assert client.post(PLUGINS_URL, json={"plugin": n}).json()["ok"] is True, n
    assert len(asyncio.run(_model_rows(act_db, _plugins_model()))) == MAX_PLUGINS_GRAYLISTED

    r = client.post(PLUGINS_URL, json={"plugin": "gray_plugin_over"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["reason"] == "too_many_plugins"
    assert sorted(body["plugins"]) == sorted(names)
    assert "gray_plugin_over" not in asyncio.run(actions.configured_plugins()), "超容量不得写入"
    # 重复添加已有条目不占新额度（幂等语义在容量判定之前）
    again = client.post(PLUGINS_URL, json={"plugin": names[0]}).json()
    assert again["ok"] is True and sorted(again["plugins"]) == sorted(names)


# ── ⑦ 读库异常 → fail-closed（名单读不到＝空集＝全拒；写不进去＝如实回错） ──
def test_名单读写库异常一律fail_closed(act_db, monkeypatch, actions_log_records):
    _open_all_gates(act_db)                      # 闸门与名单本已全开
    assert _decide(_tap()).allowed is True       # 正向前提：不是「本来就拒」
    before = len(actions.take_pending(USER))     # 正向前提那条留下的一项

    class _Boom:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("模拟读写库失败")

    monkeypatch.setattr(database, "async_session_factory", _Boom())
    assert asyncio.run(actions.configured_targets(TENANT)) == frozenset()
    assert asyncio.run(actions.plugin_graylisted(PLUGIN)) is False
    assert asyncio.run(actions.configured_plugins()) == frozenset()
    assert asyncio.run(actions.allow_target(TENANT, "com.new.app")) is False
    assert asyncio.run(actions.allow_plugin_actions("new_plugin")) is False
    assert _decide(_tap()).allowed is False, "读写库失败绝不退化成放行"
    # 正向前提那条已留下一项待执行：这里只断言「被拒这次没有新增」
    assert len(actions.take_pending(USER)) == before, "被拒不得新增待执行项"
    assert any(r.levelno == logging.WARNING and "库失败" in r.getMessage()
               for r in actions_log_records), "读/写库失败要留 WARNING，不得静默"


# ── ⑧ 两个管理端点：非管理员 403 / 未登录 401 / 管理员可用 / 非法插件名 invalid_plugin ──
def test_插件灰度端点鉴权与非法名口径(act_db, monkeypatch):
    _as_server_admin(monkeypatch, act_db, USER)
    _as_server_admin(monkeypatch, act_db, OTHER_USER, enabled=False)
    outsider = _client(OTHER_USER)
    assert outsider.post(PLUGINS_URL, json={"plugin": PLUGIN}).status_code == 403
    assert outsider.get(PLUGINS_URL).status_code == 403
    bare_app = FastAPI()
    bare_app.include_router(device_actions_api.router)
    bare = TestClient(bare_app)
    assert bare.post(PLUGINS_URL, json={"plugin": PLUGIN}).status_code == 401
    assert bare.get(PLUGINS_URL).status_code == 401

    client = _client(USER)
    assert client.get(PLUGINS_URL).json() == {"plugins": []}
    bad = ["", "   ", "has space", "com/a", "plug;in", "p" * 65, "插件名", "plug\nin"]
    for name in bad:
        r = client.post(PLUGINS_URL, json={"plugin": name})
        assert r.status_code == 200, name
        body = r.json()
        assert body["ok"] is False and body["reason"] == "invalid_plugin", name
        assert body["plugins"] == [], name
    assert client.post(PLUGINS_URL, json={}).json()["reason"] == "invalid_plugin"
    assert asyncio.run(_model_rows(act_db, _plugins_model())) == [], "非法名一行都不许落库"

    # 合法名（含 _ . -，落库前 strip）→ 管理员可用，且闸门 ③a 立刻认它
    ok = client.post(PLUGINS_URL, json={"plugin": f"  {PLUGIN}-2.x  "})
    assert ok.status_code == 200 and ok.json()["ok"] is True, ok.text
    assert client.get(PLUGINS_URL).json() == {"plugins": [f"{PLUGIN}-2.x"]}
    assert asyncio.run(actions.plugin_graylisted(f"{PLUGIN}-2.x")) is True


# ── ⑨ 端点/闸门看的都是「库里 ∪ 编译期常量」这个并集 ──
def test_灰度名单是库里与编译期常量的并集(act_db, monkeypatch):
    monkeypatch.setattr(actions, "PLUGIN_ACTION_ENABLED_PLUGINS", frozenset({"compiled_in_gray"}))
    _as_server_admin(monkeypatch, act_db, USER)
    client = _client(USER)
    assert client.get(PLUGINS_URL).json() == {"plugins": ["compiled_in_gray"]}
    assert client.post(PLUGINS_URL, json={"plugin": PLUGIN}).json()["ok"] is True
    assert client.get(PLUGINS_URL).json() == {"plugins": sorted([PLUGIN, "compiled_in_gray"])}
    assert asyncio.run(actions.configured_plugins()) == frozenset({PLUGIN, "compiled_in_gray"})
    # 并集两侧都算命中；不在并集里的仍然不放开
    assert asyncio.run(actions.plugin_graylisted("compiled_in_gray")) is True
    assert asyncio.run(actions.plugin_graylisted("not_gray_at_all")) is False
