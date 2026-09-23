# -*- coding: utf-8 -*-
"""X7-M0 能力契约骨架 + X7-M1 结构化承载测试（派单 P5 / P7-A）。

覆盖：
1) 能力注册表完整性（8 条齐全、权限命名、requires 合法、sources 非空）；
2) M0 不并入、**M3 已并入** manifest.VALID_PERMISSIONS（断言反转为「必须并入」，逐条判定见 test_device_capability_permissions.py）；
2b) P9：M1 新列已登记进启动期 schema 哨兵（``db.migrate._CURRENT_SCHEMA_SENTINELS``）；
3) read_capability 三态（ok / empty / unknown 不抛）+ M1 的 ``value['structured']``；
4) 消费方回归——**无载荷时** section_phone 与迁移前逐字一致（含与旧渲染函数对照）；
5) M1 结构化渲染：有载荷按字段渲染、确定性、混排时未带载荷的行仍是旧文本行。

感知接口侧（payload_json 入参校验）与迁移「旧库/新库两种形态」在
tests/test_phone_perception_payload.py。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from _dbclone import clone_engine, make_session_factory

from app.agent.context.section_phone import phone_perception_section, render_perception_records
from app.application import phone_service
from app.db import database
from app.device import capabilities as caps
from app.device import port
from app.models.device import PhoneSnapshot
from app.models.user import User
from app.plugins import manifest

# 建临时库（clone_engine）属重量级/集成型用例（docs/engineering-protocol.md 十八）
pytestmark = pytest.mark.slow


@pytest.fixture()
def device_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：端口链路与旧渲染链路都指到它（新旧口径同源可比）。"""
    engine = clone_engine(tmp_path / "cap.db")
    factory = make_session_factory(engine)
    # port 刻意不 from-import 绑死名字（见其模块 docstring）→ 直接 patch 源头 app.db.database
    monkeypatch.setattr(database, "async_session_factory", factory)        # read_capability / read_perception_records
    monkeypatch.setattr(phone_service, "async_session_factory", factory)   # 旧文本渲染（对照基准）
    yield factory
    engine.sync_engine.dispose()


async def _add_user(factory, uid: int):
    async with factory() as db:
        db.add(User(id=uid, username=f"u{uid}", nickname=f"n{uid}"))
        await db.commit()


async def _add_snap(factory, uid, source, content, *, minutes_ago=0, payload=None):
    created = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    async with factory() as db:
        db.add(PhoneSnapshot(
            user_id=uid, source=source, content=content, created_at=created, payload_json=payload
        ))
        await db.commit()


# ── 1) 注册表完整性 ──
def test_能力注册表完整性():
    expected = {
        "foreground_app", "screen_state", "battery", "network",
        "dnd", "notifications", "usage_stats", "location",
    }
    assert set(caps.CAPABILITIES) == expected
    assert set(caps.CAPABILITY_IDS) == expected
    for cid, spec in caps.CAPABILITIES.items():
        assert spec.id == cid
        assert spec.kind == "read" and spec.kind in caps.VALID_KINDS
        assert spec.permission.startswith("device:") and spec.permission.endswith(":read")
        assert spec.permission == f"device:{cid}:read"
        assert spec.requires in caps.VALID_REQUIRES
        assert isinstance(spec.sensitive, bool)
        assert spec.sources                      # sources 非空
        assert "raw_text" in spec.schema


# ── 2) M0 刻意不把能力级权限并入 manifest ──
def test_能力级权限已并入_manifest_M3():
    perms = caps.capability_permissions()
    assert len(perms) == 8
    for perm in perms:
        assert perm in manifest.VALID_PERMISSIONS


# ── 2b) P9 第 1 条：M1 新列登记进启动期 schema 哨兵 ──
def test_schema哨兵覆盖payload_json():
    """``phone_snapshots.payload_json`` 只由迁移 f4a5b6c7d8e9 引入；缺该列的老库若不判「落后」，
    会被 stamp 到 head 却永久缺列（select PhoneSnapshot 直接报错）。"""
    from app.db.migrate import _CURRENT_SCHEMA_SENTINELS

    assert ("phone_snapshots", "payload_json") in _CURRENT_SCHEMA_SENTINELS
    # 哨兵表不得有重复条目（重复会让「缺列」判定看起来正常却难以核对）
    assert len(_CURRENT_SCHEMA_SENTINELS) == len(set(_CURRENT_SCHEMA_SENTINELS))


# ── 3) read_capability 三态 + M1 structured ──
def test_read_capability_ok(device_db):
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(device_db, 1, "notification", "张三发来一条消息", minutes_ago=3))
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "ok"
    assert reading.retriable is False
    # M1：无载荷时 structured 为 None，raw_text 语义与 M0 逐字一致
    assert reading.value == {"raw_text": "张三发来一条消息", "structured": None}
    assert reading.source == "notification"
    assert reading.observed_at                    # 有观测时间


def test_read_capability_ok_带结构化载荷(device_db):
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(
        device_db, 1, "notification", "张三发来一条消息",
        minutes_ago=3, payload='{"app":"微信","title":"张三","text":"在吗"}',
    ))
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "ok"
    assert reading.value == {
        "raw_text": "张三发来一条消息",
        "structured": {"app": "微信", "title": "张三", "text": "在吗"},
    }


def test_read_capability_载荷坏数据只丢结构化(device_db):
    """坏 JSON 落库（绕过入参校验直写）也不得抛：structured 退化为 None，raw_text 不受影响。"""
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(device_db, 1, "notification", "坏载荷", minutes_ago=2, payload="{不是 JSON"))
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "ok"
    assert reading.value == {"raw_text": "坏载荷", "structured": None}


def test_parse_payload_只认JSON对象():
    """非对象（数组/裸量/null）、空、坏 JSON 一律视为「无载荷」；空对象算对象。"""
    for raw in (None, "", "{不是 JSON", '["a","b"]', '"裸字符串"', "3", "null"):
        assert port._parse_payload(raw) is None, raw
    assert port._parse_payload("{}") == {}


# ── 3b) M2：CapabilityReading.confidence（加法，不改三态 / value 形状）──
def test_read_capability_取载荷confidence(device_db):
    """payload_json 含 confidence → 读回同一 float。"""
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(
        device_db, 1, "notification", "带置信度", minutes_ago=1,
        payload='{"app":"微信","confidence":0.6}',
    ))
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "ok"
    assert reading.confidence == 0.6
    assert reading.value == {
        "raw_text": "带置信度", "structured": {"app": "微信", "confidence": 0.6},
    }


def test_read_capability_无confidence或坏值为None(device_db):
    """缺 confidence / 非数值 / empty 态都归 None，且绝不抛。"""
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(device_db, 1, "notification", "无置信度", minutes_ago=5,
                          payload='{"app":"微信"}'))
    assert asyncio.run(port.read_capability("notifications", 1)).confidence is None
    # 更近一条写坏值（非数值）→ None（float 转换失败被吞）
    asyncio.run(_add_snap(device_db, 1, "notification", "坏置信度", minutes_ago=1,
                          payload='{"confidence":"不是数"}'))
    assert asyncio.run(port.read_capability("notifications", 1)).confidence is None
    # empty 态也带 confidence=None（字段形状一致）
    empty = asyncio.run(port.read_capability("battery", 1))
    assert empty.code == "empty" and empty.confidence is None


def test_read_capability_empty(device_db):
    asyncio.run(_add_user(device_db, 1))
    # battery 在 M0 无采集来源 → 恒 empty、retriable、raw_text 空
    reading = asyncio.run(port.read_capability("battery", 1))
    assert reading.code == "empty"
    assert reading.retriable is True
    assert reading.value == {"raw_text": "", "structured": None}
    assert reading.observed_at is None and reading.source is None


def test_read_capability_unknown_not_raise(device_db, monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(database, "async_session_factory", _boom)
    # 查询异常必须被吞掉并降级为 unknown，绝不抛出到上下文注入链路
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "unknown"
    assert reading.retriable is True
    assert reading.value == {"raw_text": "", "structured": None}


# ── 4) 消费方回归：无载荷时逐字不变 ──
def test_section_phone_perception_逐字不变(device_db):
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(device_db, 1, "clipboard", "复制了一段验证码", minutes_ago=2))
    asyncio.run(_add_snap(device_db, 1, "notification", "妈妈发来消息", minutes_ago=1))

    text = asyncio.run(phone_perception_section({"user_id": 1}, {}))
    # 迁移前口径：每条「[来源标签 N分钟前] 正文」，最新在前，换行拼接（同 phone_service.get_recent_perception_text）
    assert text == "[通知 1分钟前] 妈妈发来消息\n[剪贴板 2分钟前] 复制了一段验证码"
    # 与旧渲染函数同库对照（防止两处口径各自漂移）
    assert text == asyncio.run(phone_service.get_recent_perception_text(1))


def test_section_phone_perception_empty_falls_back_to_none_text(device_db):
    """无任何快照时，分区缺省「无」（退化分支与迁移前一致）。"""
    asyncio.run(_add_user(device_db, 7))
    text = asyncio.run(phone_perception_section({"user_id": 7}, {}))
    assert text == "无"


# ── 5) M1 结构化渲染（纯函数：固定 now，与真实时钟无关）──
_NOW = datetime(2026, 9, 22, 12, 0, 0)


def _rec(source, raw="", structured=None, minutes_ago=1, desc=""):
    return port.PerceptionRecord(
        source=source, raw_text=raw, image_desc=desc, structured=structured,
        observed_at=_NOW - timedelta(minutes=minutes_ago),
    )


def test_结构化渲染_字段名升序且与键序无关():
    """确定性：同一份字段换个 JSON 键序必得同一份文案（字段名升序渲染）。"""
    a = _rec("notification", structured={"app": "微信", "title": "张三", "text": "在吗"})
    b = _rec("notification", structured={"text": "在吗", "title": "张三", "app": "微信"})
    assert render_perception_records([a], _NOW) == render_perception_records([b], _NOW) == (
        "[通知 1分钟前] app=微信 | text=在吗 | title=张三"
    )


def test_结构化渲染_容器与数字走紧凑JSON():
    """未登记来源直接显示 source 原值（与旧渲染同口径）；空值字段跳过。"""
    rec = _rec("usage_stats", structured={"count": 3, "ok": True, "apps": ["微信", "B站"], "note": ""})
    assert render_perception_records([rec], _NOW) == (
        '[usage_stats 1分钟前] apps=["微信","B站"] | count=3 | ok=true'
    )


def test_结构化渲染_原文不重复但图片描述保留():
    """raw_text 已作为字段值出现时不再重复占配额；image_desc 始终补上。"""
    dup = _rec("accessibility", raw="在吗", structured={"text": "在吗"}, desc="一张图")
    assert render_perception_records([dup], _NOW) == (
        "[屏幕 1分钟前] text=在吗 | [图片] 一张图"
    )
    keep = _rec("accessibility", raw="屏幕 OCR 全文", structured={"app": "微信"}, minutes_ago=0)
    assert render_perception_records([keep], _NOW) == (
        "[屏幕 0分钟前] app=微信 | 原文=屏幕 OCR 全文"
    )


def test_结构化与无载荷混排(device_db):
    """带载荷的行按字段渲染，未带载荷的行仍是旧文本行（回落逐字不变）。"""
    asyncio.run(_add_user(device_db, 3))
    asyncio.run(_add_snap(device_db, 3, "notification", "妈妈发来消息", minutes_ago=1,
                          payload='{"title":"妈妈","text":"吃饭了吗"}'))
    asyncio.run(_add_snap(device_db, 3, "clipboard", "复制了验证码", minutes_ago=2))

    text = asyncio.run(phone_perception_section({"user_id": 3}, {}))
    assert text == (
        '[通知 1分钟前] text=吃饭了吗 | title=妈妈 | 原文=妈妈发来消息\n'
        "[剪贴板 2分钟前] 复制了验证码"
    )
    # 旧渲染（不看载荷）只出文本行——证明差异只在「带载荷的那些行」
    legacy = asyncio.run(phone_service.get_recent_perception_text(3))
    assert legacy == "[通知 1分钟前] 妈妈发来消息\n[剪贴板 2分钟前] 复制了验证码"
