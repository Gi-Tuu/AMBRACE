# -*- coding: utf-8 -*-
"""A4 批 3 / T1「M2a 两档释放」接线测试（2026-10-01；P5 拆两把键）。

口径（设计 §3.1 / §3.2 / §5；用户 2026-10-01 拍板 P1 可配、P2 归属窗 24h、P3 对称释放、P5 拆键）：
  1. 关闸＝**逐字节旧行为**，且**一次 SELECT 都不发**（本文件用「碰库就炸」的替身钉死）；
  2. 开口档：主动消息发送确认（seq==0 且有 intent）后按比例**部分释放**；
  3. 全额档：用户发言后取「本会话最近一条已发送的主动消息」，24h 窗内 ＋ 幂等（last_released_at）；
  4. 生效闸＝**对应 v1 键 ∧ shadow**；只开 v1 未开 shadow ⇒ 不生效并打 WARNING。
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from app.application import relational_drive_service as svc
from app.domain.relational import drives
from app.flags.agent_flags import AGENT_FLAGS

_OPEN = svc.V1_OPEN_FLAG_KEY
_FULL = svc.V1_FULL_FLAG_KEY
_SHADOW = svc.FLAG_KEY
_KEYS = (_OPEN, _FULL, _SHADOW)


@pytest.fixture(autouse=True)
def _restore_flags():
    saved = {k: AGENT_FLAGS.get(k) for k in _KEYS}
    for k in _KEYS:
        AGENT_FLAGS[k] = False
    yield
    for k, v in saved.items():
        if v is None:
            AGENT_FLAGS.pop(k, None)
        else:
            AGENT_FLAGS[k] = v


class _BoomDB:
    """闸关时若被碰一下就炸（钉死「零 SQL」）。"""

    def __getattr__(self, name):
        raise AssertionError("闸关时不得访问 db." + name)


def test_两把键默认关且已登记():
    assert AGENT_FLAGS[_OPEN] is False
    assert AGENT_FLAGS[_FULL] is False


@pytest.mark.parametrize("kind,key", [("open", _OPEN), ("full", _FULL)])
def test_生效闸必须_v1_与_shadow_同时开(kind, key):
    AGENT_FLAGS[key] = True
    AGENT_FLAGS[_SHADOW] = False
    assert svc.release_enabled(kind) is False
    AGENT_FLAGS[_SHADOW] = True
    assert svc.release_enabled(kind, 13) is True
    AGENT_FLAGS[key] = False
    assert svc.release_enabled(kind, 13) is False


def test_灰度白名单外的角色fails_closed():
    AGENT_FLAGS[_OPEN] = True
    AGENT_FLAGS[_SHADOW] = True
    assert svc.release_enabled("open", 13) is True          # 白名单内（char13）
    assert svc.release_enabled("open", 2) is False          # 白名单外
    assert svc.release_enabled("open", None) is False       # 缺角色 ⇒ fail-closed


def test_只开v1未开shadow_不生效并告警(caplog):
    AGENT_FLAGS[_OPEN] = True
    with caplog.at_level("WARNING"):
        assert svc.release_enabled("open", 13) is False
    assert any("relational_drive_shadow is OFF" in r.message for r in caplog.records)


def test_比例可配_覆盖与回落(monkeypatch):
    from app.config import settings

    baseline = svc._open_ratio("longing")
    assert baseline == pytest.approx(drives.DRIVE_OPEN_RELEASE_RATIO["longing"])
    monkeypatch.setattr(settings, "relational_drive_open_release_ratios",
                        json.dumps({"longing": 0.5}), raising=False)
    assert svc._open_ratio("longing") == pytest.approx(0.5)
    assert svc._open_ratio("concern") == pytest.approx(drives.DRIVE_OPEN_RELEASE_RATIO["concern"])
    monkeypatch.setattr(settings, "relational_drive_open_release_ratios", "{bad json", raising=False)
    assert svc._open_ratio("longing") == pytest.approx(baseline)


def test_release_open_显式比例并按边界夹取():
    assert drives.release_open(100.0, "longing", ratio=0.5) == pytest.approx(50.0)
    assert drives.release_open(100.0, "longing", ratio=-1) == pytest.approx(100.0)
    assert drives.release_open(100.0, "longing", ratio=2) == pytest.approx(0.0)
    assert drives.release_open(100.0, "longing") == pytest.approx(
        100.0 * (1.0 - drives.DRIVE_OPEN_RELEASE_RATIO["longing"]))


def test_闸关时_apply_open_release_一次库都不碰():
    intent = drives.DRIVE_TO_INTENT["longing"]
    assert asyncio.run(svc.apply_open_release(_BoomDB(), 13, 1, intent)) is None


def test_闸关时_apply_reply_release_一次库都不碰():
    assert asyncio.run(svc.apply_reply_release(_BoomDB(), 13, 1, 99)) is None


def test_intent脏值一律None():
    class _Row:
        extra_meta = "{bad json"

    class _Row2:
        extra_meta = None

    assert svc._intent_of(_Row()) is None
    assert svc._intent_of(_Row2()) is None


@pytest.fixture()
def db_env(tmp_path):
    from _dbclone import clone_engine, make_session_factory

    engine = clone_engine(tmp_path / "m2a.db")
    factory = make_session_factory(engine)
    yield factory, engine
    asyncio.run(engine.dispose())


def _seed(factory, *, level=10.0, sent_minutes_ago=5.0, drive_key="longing"):
    from app.models.character import AICharacter, ProactiveMessageLog, RelationalDrive
    from app.utils.timeutil import now_naive_utc

    now = now_naive_utc()
    intent = drives.DRIVE_TO_INTENT[drive_key]

    async def _run():
        from app.models.chat import ChatSession
        from app.models.user import User

        async with factory() as db:
            # 父行先落（ORM 默认值负责 NOT NULL 列），再补本次要用的两组行
            db.add(User(id=1, username="m2a_u1", nickname="M2A"))
            db.add(AICharacter(id=13, user_id=1, name="M2A", is_partner=False))
            db.add(ChatSession(id=99, user_id=1, character_id=13, title="m2a", is_active=True))
            await db.flush()
            db.add(RelationalDrive(character_id=13, user_id=1, drive_key=drive_key,
                                   level=level, last_settled_at=now, last_released_ratio=0.0))
            db.add(ProactiveMessageLog(character_id=13, session_id=99, message_type="proactive",
                                       content="x", extra_meta=json.dumps({"intent": intent}),
                                       created_at=now - timedelta(minutes=sent_minutes_ago)))
            await db.commit()

    asyncio.run(_run())
    return now, intent


def _read_level(factory, drive_key="longing"):
    from app.models.character import RelationalDrive
    from sqlalchemy import select

    async def _run():
        async with factory() as db:
            row = (await db.execute(select(RelationalDrive).where(
                RelationalDrive.character_id == 13, RelationalDrive.user_id == 1,
                RelationalDrive.drive_key == drive_key))).scalar_one_or_none()
            return None if row is None else (row.level, row.last_released_ratio, row.last_released_at)

    return asyncio.run(_run())


def test_开口释放写入水位与比例(db_env):
    factory, _engine = db_env
    _seed(factory, level=10.0)
    AGENT_FLAGS[_OPEN] = True
    AGENT_FLAGS[_SHADOW] = True

    async def _run():
        async with factory() as db:
            out = await svc.apply_open_release(db, 13, 1, drives.DRIVE_TO_INTENT["longing"])
            await db.commit()
            return out

    out = asyncio.run(_run())
    assert out is not None and out["drive"] == "longing"
    ratio = drives.DRIVE_OPEN_RELEASE_RATIO["longing"]
    level, ratio_written, _rel = _read_level(factory)
    # settle 会按游标补一点点自然增量（毫秒级），故容差取 1e-2
    assert level == pytest.approx(10.0 * (1.0 - ratio), abs=1e-2)
    assert ratio_written == pytest.approx(ratio)


def test_全额释放清零且二次调用幂等(db_env):
    factory, _engine = db_env
    _seed(factory, level=8.0)
    AGENT_FLAGS[_FULL] = True
    AGENT_FLAGS[_SHADOW] = True

    async def _run():
        async with factory() as db:
            first = await svc.apply_reply_release(db, 13, 1, 99)
            await db.commit()
        async with factory() as db:
            second = await svc.apply_reply_release(db, 13, 1, 99)
            await db.commit()
        return first, second

    first, second = asyncio.run(_run())
    assert first is not None and first["drive"] == "longing"
    assert second is None, "同一条主动消息不得被重复释放（last_released_at 幂等闸）"
    level, ratio_written, released_at = _read_level(factory)
    assert level == pytest.approx(0.0, abs=1e-9)
    assert ratio_written == pytest.approx(1.0)
    assert released_at is not None


def test_超出归属窗不释放(db_env):
    factory, _engine = db_env
    _seed(factory, level=8.0, sent_minutes_ago=25 * 60)  # 25 小时前 > 24h 窗
    AGENT_FLAGS[_FULL] = True
    AGENT_FLAGS[_SHADOW] = True

    async def _run():
        async with factory() as db:
            out = await svc.apply_reply_release(db, 13, 1, 99)
            await db.commit()
            return out

    assert asyncio.run(_run()) is None
    level, _ratio, released_at = _read_level(factory)
    assert level > 0.0 and released_at is None
