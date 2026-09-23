# -*- coding: utf-8 -*-
"""X7-M3 能力级权限 + 强授权安装（判定侧）测试（派单 P11/P12 的 B 段，2026-09-22）。

覆盖（与派单 B 段自检一一对应）：
① 权限名并入：``manifest.VALID_PERMISSIONS`` 含全部 8 条 ``device:<capability>:read``（M3）与
   3 条 ``device:<capability>:write``（M4a 行动能力），且与
   ``app.device.capabilities.CAPABILITIES`` **一一对应**（不多不少、格式固定、原有权限一个不少）；
② 逐条同意：只同意 1 条能力 → ``has_capability_permission`` 对那条 True、对其余各条（含 M4a
   的 3 条行动能力）False；
③ 前置缺失一律 False（fail-closed）：插件已停用 / 本租户无同意行（别的租户有也不算）/
   插件未安装（无 ``plugins`` 行）/ 拿不到租户（``tenant_id=None`` 或非整数）/ 未知能力 id；
④ 审计：放行与拒绝各记一条 INFO，字段固定 ``plugin= tenant= capability= allowed=``。

隔离：私有临时库（``_dbclone`` 克隆模板 → pytest ``tmp_path``）+ 把
``app.db.database.async_session_factory`` 换成私有工厂。registry 的读点都是**函数内局部
import** 该名字（非模块级早绑定），故只 patch 源头模块即可覆盖 registry / 同意落库链路。
**不连生产库、不写 backend/data、不重启服务。**
"""
import asyncio
import logging

import pytest

from _dbclone import clone_engine, make_session_factory

from app.db import database
from app.device import capabilities as caps
from app.models.plugin import Plugin
from app.plugins import manifest, registry

# 两个「家庭根」租户 id（plugin_consents.tenant_id 口径；无外键，故无需建 users 行）
TID_A, TID_B = 7, 8

# 建临时库属重量级/集成型用例（docs/engineering-protocol.md 十八）
pytestmark = pytest.mark.slow


@pytest.fixture()
def perm_db(monkeypatch, tmp_path):
    """私有临时 SQLite（含 plugins / plugin_consents）：把 registry 的读点指到它。"""
    engine = clone_engine(tmp_path / "cap_perm.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(database, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


async def _add_plugin(factory, name, *, enabled=True, owner_tenant_id=None):
    """造一条插件安装行（``plugins`` 有行 = 已安装；``owner_tenant_id`` 非空 = 已归某家庭）。"""
    async with factory() as db:
        db.add(Plugin(name=name, version="1.0.0", description="", author="",
                      enabled=enabled, owner_tenant_id=owner_tenant_id,
                      consented_permissions="[]"))
        await db.commit()


# ── ① 权限名并入 + 与 CAPABILITIES 一一对应 ──
def test_能力级权限并入VALID_PERMISSIONS且与能力一一对应():
    perms = caps.capability_permissions()
    read = {cid: spec for cid, spec in caps.CAPABILITIES.items() if spec.kind == "read"}
    act = {cid: spec for cid, spec in caps.CAPABILITIES.items() if spec.kind == "act"}

    # M0 只读 8 条 + M4a 行动 3 条（读写是两条独立权限，各按 kind 校验后缀）
    assert len(read) == 8 and len(act) == 3
    assert len(perms) == len(caps.CAPABILITIES) == 11
    assert len(set(perms)) == 11, "能力级权限名不得重名（重名即破坏一一对应）"
    for cid, spec in read.items():
        assert spec.permission == f"device:{cid}:read"
        assert spec.permission in manifest.VALID_PERMISSIONS, f"{spec.permission} 未并入白名单"
    for cid, spec in act.items():
        assert spec.permission == f"device:{cid}:write"
        assert spec.permission in manifest.VALID_PERMISSIONS, f"{spec.permission} 未并入白名单"

    # 反查：白名单里的 device: 权限 = 能力注册表全部权限，一条不多一条不少
    assert {p for p in manifest.VALID_PERMISSIONS if p.startswith("device:")} == set(perms)
    # 并入是加法：原有权限名一个不少
    for p in ("write_memory", "send_message", "persona:read", "memory:read",
              "life:read", "relationship:read", "proactive:read"):
        assert p in manifest.VALID_PERMISSIONS


def test_能力级权限名格式与manifest校验口径():
    """三段式按 kind 分流：只读 ``device:<id>:read``、行动 ``device:<id>:write``；后缀不可互换。"""
    for cid, spec in caps.CAPABILITIES.items():
        p = spec.permission
        suffix = ":read" if spec.kind == "read" else ":write"
        assert p.startswith("device:") and p.endswith(suffix), p
        assert p.count(":") == 2, f"{p} 不是 device:<capability>:{suffix[1:]} 三段式"
        assert p == f"device:{cid}{suffix}", f"{spec.kind} 能力的权限名后缀漂移"

    base = {"name": "cap_probe", "version": "1.0.0", "description": "能力级权限探针"}
    # 并入生效：声明能力级权限的 manifest 通过校验（安装期据此逐条同意）
    assert manifest.validate_manifest({**base, "permissions": ["device:battery:read"]}) is None
    # M4a：行动能力声明 :write 同样通过（读写分离，同意读不放开写）
    assert manifest.validate_manifest(
        {**base, "permissions": ["device:action_tap:write"]}
    ) is None
    # 后缀不得互换：行动能力没有 :read 名，只读能力也没有 :write 名
    assert manifest.validate_manifest(
        {**base, "permissions": ["device:action_tap:read"]}
    ) == "未知权限: device:action_tap:read"
    assert manifest.validate_manifest(
        {**base, "permissions": ["device:battery:write"]}
    ) == "未知权限: device:battery:write"
    # 不存在的能力 id 仍然拒绝（白名单没有放宽成「device: 前缀随便写」）
    assert manifest.validate_manifest(
        {**base, "permissions": ["device:nope:read"]}
    ) == "未知权限: device:nope:read"


def test_声明能力级权限会触发安装期同意闸():
    """强授权安装：能力级权限走的是**同一份**权限白名单，声明了就必须装时同意（纯函数，无 DB）。

    （安装期同意是「清单级」判定——:func:`registry.consent_state` 对声明集整体判 required/auto；
    逐条粒度体现在调用期 :func:`registry.has_capability_permission`，见本文件 ②③。）
    """
    state, needed = registry.consent_state(
        ["device:battery:read", "device:network:read"], []
    )
    assert state == "required"
    assert needed == ["device:battery:read", "device:network:read"]

    assert registry.consent_state(["device:battery:read"], ["device:battery:read"]) == ("auto", [])


# ── ② 只同意一条 → 只放行那一条 ──
def test_只同意一条则只放行那一条(perm_db):
    asyncio.run(_add_plugin(perm_db, "cap_one", owner_tenant_id=TID_A))
    asyncio.run(registry.grant_plugin_consent(
        "cap_one", ["device:notifications:read"], tenant_id=TID_A
    ))
    # 正向前提：该租户确实只同意了这一条
    assert asyncio.run(
        registry.get_tenant_consented_permissions("cap_one", TID_A)
    ) == ["device:notifications:read"]

    assert asyncio.run(
        registry.has_capability_permission("cap_one", TID_A, "notifications")
    ) is True
    for cid in caps.CAPABILITY_IDS:
        if cid == "notifications":
            continue
        assert asyncio.run(
            registry.has_capability_permission("cap_one", TID_A, cid)
        ) is False, f"{cid} 未同意却被放行"


# ── ③ 前置缺失一律 False（fail-closed）──
def test_插件停用一律False(perm_db):
    asyncio.run(_add_plugin(perm_db, "cap_off", enabled=False, owner_tenant_id=TID_A))
    asyncio.run(registry.grant_plugin_consent(
        "cap_off", ["device:battery:read"], tenant_id=TID_A
    ))
    # 正向前提：同意行确实在（拒绝来自「已停用」，不是来自「没同意」）
    assert "device:battery:read" in asyncio.run(
        registry.get_tenant_consented_permissions("cap_off", TID_A)
    )
    assert asyncio.run(
        registry.has_capability_permission("cap_off", TID_A, "battery")
    ) is False


def test_本租户无同意行一律False(perm_db):
    """归户插件：别的租户同意了不算（跨租户读取 fail-closed）。"""
    asyncio.run(_add_plugin(perm_db, "cap_family", owner_tenant_id=TID_A))
    asyncio.run(registry.grant_plugin_consent(
        "cap_family", ["device:battery:read"], tenant_id=TID_B
    ))
    assert asyncio.run(
        registry.has_capability_permission("cap_family", TID_B, "battery")
    ) is True, "正向前提：B 租户已同意"
    assert asyncio.run(
        registry.has_capability_permission("cap_family", TID_A, "battery")
    ) is False, "A 租户无同意行，不得借 B 租户的同意放行"


def test_未安装与拿不到租户与未知能力一律False(perm_db):
    asyncio.run(_add_plugin(perm_db, "cap_family", owner_tenant_id=TID_A))
    asyncio.run(registry.grant_plugin_consent(
        "cap_family", ["device:battery:read"], tenant_id=TID_A
    ))
    assert asyncio.run(
        registry.has_capability_permission("cap_family", TID_A, "battery")
    ) is True, "正向前提：A 租户已同意"

    # 插件未安装（plugins 无该行）
    assert asyncio.run(
        registry.has_capability_permission("cap_absent", TID_A, "battery")
    ) is False
    # 拿不到具体租户：None / 非整数 → 拒绝（不走服务级回落）
    assert asyncio.run(
        registry.has_capability_permission("cap_family", None, "battery")
    ) is False
    assert asyncio.run(
        registry.has_capability_permission("cap_family", "not-a-tenant", "battery")
    ) is False
    # 未知能力 id
    assert asyncio.run(
        registry.has_capability_permission("cap_family", TID_A, "no_such_cap")
    ) is False


# ── ④ 审计日志（结构化字段固定）──
@pytest.fixture()
def plugins_log_records():
    """直连收集 ``plugins`` 日志器的记录（不依赖 pytest ``caplog``）。

    背景（2026-09-22 实测）：pytest 的 ``log_disable_existing_loggers``（默认开）在 collection
    之后对**已存在**的日志器跑 dictConfig，会把 ``plugins`` 这类「import 期创建」的应用日志器
    置成 ``disabled=True``；项目的应用日志器都是这种，故 caplog 收不到它们（本项目此前没有
    caplog 用例，一直没暴露）。这里显式临时复位 ``disabled`` 并挂一个直连 handler，
    只动本用例窗口内的日志器状态，退出时原样还回。
    """
    logger = logging.getLogger("plugins")
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


def test_放行与拒绝各记一条审计日志(perm_db, plugins_log_records):
    asyncio.run(_add_plugin(perm_db, "cap_audit", owner_tenant_id=TID_A))
    asyncio.run(registry.grant_plugin_consent(
        "cap_audit", ["device:battery:read"], tenant_id=TID_A
    ))
    assert asyncio.run(
        registry.has_capability_permission("cap_audit", TID_A, "battery")
    ) is True
    assert asyncio.run(
        registry.has_capability_permission("cap_audit", TID_A, "network")
    ) is False

    msgs = [r.getMessage() for r in plugins_log_records]
    assert all(r.levelno == logging.INFO for r in plugins_log_records)  # 审计固定 INFO
    assert "plugin=cap_audit tenant=7 capability=battery allowed=true" in msgs
    assert "plugin=cap_audit tenant=7 capability=network allowed=false" in msgs
