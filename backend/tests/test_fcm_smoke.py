# -*- coding: utf-8 -*-
"""FCM 后端冒烟测试（2026-08-28，迁自副本 test_fcm_smoke.py）。

覆盖：表结构 / CRUD / 推送频控 / FCM provider 懒初始化 / device API 路由。
"""
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.device import UserDeviceToken


def test_schema():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[UserDeviceToken.__table__])

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("user_device_tokens")}
    expected = {"id", "user_id", "device_id", "platform", "push_provider",
                "push_token", "app_version", "last_seen_at", "created_at"}
    assert set(cols.keys()) == expected, f"Missing: {expected - set(cols.keys())}"

    uc = insp.get_unique_constraints("user_device_tokens")
    assert any("user_device_provider" in (u["name"] or "") for u in uc)


def test_crud():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[UserDeviceToken.__table__])

    with Session(engine) as s:
        t = UserDeviceToken(
            user_id=1, device_id="dev1", platform="android",
            push_provider="fcm", push_token="tok123",
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        assert t.id is not None

        # 同一设备/provider -> 更新 token
        existing = s.query(UserDeviceToken).filter_by(
            user_id=1, device_id="dev1", push_provider="fcm"
        ).one()
        existing.push_token = "tok456"
        s.commit()
        assert s.query(UserDeviceToken).count() == 1

        # 不同设备 -> 新行
        s.add(UserDeviceToken(
            user_id=1, device_id="dev2", platform="android",
            push_provider="fcm", push_token="tok789",
        ))
        s.commit()
        assert s.query(UserDeviceToken).count() == 2


def test_push_rate_limit():
    from app.application.push_service import _consume_rate_slot, _check_rate_limit, _rate_buckets

    _rate_buckets.clear()
    # 用 _consume_rate_slot 填充配额（模拟已发送的 FCM），替代原来 _check_rate_limit 计数
    for _ in range(5):
        _consume_rate_slot(999)
    # 第 6 次被频控
    assert not _check_rate_limit(999, "normal"), "6th request should be rate limited"
    # 高优先级豁免
    assert _check_rate_limit(999, "high"), "High priority should bypass rate limit"
    # 不同用户独立
    assert _check_rate_limit(888, "normal"), "Different user should have own bucket"


def test_fcm_provider_lazy(monkeypatch):
    """FCM provider 未配置时懒初始化安全返回 None，不崩溃。

    测试隔离（2026-09-13）：本机 `.env` 可能已配好 FCM 凭据（`PUSH_FCM_ENABLED=true`），
    那会让本用例在开发机上必然失败（CI 无凭据所以一直是绿的）——用例必须自造「未配置」
    环境，不依赖宿主配置。
    """
    from types import SimpleNamespace

    from app.application.push import fcm_provider as fp

    # 覆盖 provider 读的 settings 与懒初始化状态，保证「未启用」这条分支可复现
    monkeypatch.setattr(fp, "settings", SimpleNamespace(
        push_fcm_enabled=False, push_fcm_credentials_path="", push_fcm_project_id="",
    ), raising=False)
    monkeypatch.setattr(fp, "_app", None, raising=False)
    monkeypatch.setattr(fp, "_init_attempted", False, raising=False)
    monkeypatch.setattr(fp, "_init_fail_count", 0, raising=False)

    assert fp._ensure_app() is None


def test_device_api_routes():
    from app.api.device import router

    paths = [r.path for r in router.routes]
    assert "/api/v1/device/register" in paths
    assert "/api/v1/device/unregister" in paths
    assert "/api/v1/device/heartbeat" in paths
    assert "/api/v1/device/fcm-config" in paths
