# -*- coding: utf-8 -*-
"""X7-M0 设备能力契约骨架测试（派单 P5）。

覆盖派单 §1(4) 四类断言：
1) 能力注册表完整性（8 条齐全、权限命名、requires 合法、sources 非空）；
2) M0 刻意不把能力级权限并入 manifest.VALID_PERMISSIONS；
3) read_capability 三态（ok / empty / unknown 不抛）；
4) 消费方回归——迁移后 section_phone 手机感知产出与迁移前逐字一致。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from _dbclone import clone_engine, make_session_factory

from app.agent.context.section_phone import phone_perception_section
from app.application import phone_service
from app.device import capabilities as caps
from app.device import port
from app.models.device import PhoneSnapshot
from app.models.user import User
from app.plugins import manifest

# 建临时库（clone_engine）属重量级/集成型用例（docs/engineering-protocol.md 十八）
pytestmark = pytest.mark.slow


@pytest.fixture()
def device_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：端口链路与旧渲染链路都指到它（迁移前后同源可比）。"""
    engine = clone_engine(tmp_path / "cap.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(port, "async_session_factory", factory)             # read_capability
    monkeypatch.setattr(phone_service, "async_session_factory", factory)    # read_perception_context 委托
    yield factory
    engine.sync_engine.dispose()


async def _add_user(factory, uid: int):
    async with factory() as db:
        db.add(User(id=uid, username=f"u{uid}", nickname=f"n{uid}"))
        await db.commit()


async def _add_snap(factory, uid, source, content, *, minutes_ago=0):
    created = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    async with factory() as db:
        db.add(PhoneSnapshot(user_id=uid, source=source, content=content, created_at=created))
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
def test_能力级权限未并入_manifest():
    perms = caps.capability_permissions()
    assert len(perms) == 8
    for perm in perms:
        assert perm not in manifest.VALID_PERMISSIONS


# ── 3) read_capability 三态 ──
def test_read_capability_ok(device_db):
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(device_db, 1, "notification", "张三发来一条消息", minutes_ago=3))
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "ok"
    assert reading.retriable is False
    assert reading.value == {"raw_text": "张三发来一条消息"}
    assert reading.source == "notification"
    assert reading.observed_at                    # 有观测时间


def test_read_capability_empty(device_db):
    asyncio.run(_add_user(device_db, 1))
    # battery 在 M0 无采集来源 → 恒 empty、retriable、raw_text 空
    reading = asyncio.run(port.read_capability("battery", 1))
    assert reading.code == "empty"
    assert reading.retriable is True
    assert reading.value == {"raw_text": ""}
    assert reading.observed_at is None and reading.source is None


def test_read_capability_unknown_not_raise(device_db, monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(port, "async_session_factory", _boom)
    # 查询异常必须被吞掉并降级为 unknown，绝不抛出到上下文注入链路
    reading = asyncio.run(port.read_capability("notifications", 1))
    assert reading.code == "unknown"
    assert reading.retriable is True
    assert reading.value == {"raw_text": ""}


# ── 4) 消费方回归：迁移前后逐字一致 ──
def test_section_phone_perception_逐字不变(device_db):
    asyncio.run(_add_user(device_db, 1))
    asyncio.run(_add_snap(device_db, 1, "clipboard", "复制了一段验证码", minutes_ago=2))
    asyncio.run(_add_snap(device_db, 1, "notification", "妈妈发来消息", minutes_ago=1))

    text = asyncio.run(phone_perception_section({"user_id": 1}, {}))
    # 迁移前口径：每条「[来源标签 N分钟前] 正文」，最新在前，换行拼接（同 phone_service.get_recent_perception_text）
    assert text == "[通知 1分钟前] 妈妈发来消息\n[剪贴板 2分钟前] 复制了一段验证码"


def test_section_phone_perception_empty_falls_back_to_none_text(device_db):
    """无任何快照时，分区缺省「无」（退化分支与迁移前一致）。"""
    asyncio.run(_add_user(device_db, 7))
    text = asyncio.run(phone_perception_section({"user_id": 7}, {}))
    assert text == "无"
