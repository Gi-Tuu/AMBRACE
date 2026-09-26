# -*- coding: utf-8 -*-
"""A4 批 5 / T6 M1 项 1：分通道 token 用量报表读端（2026-09-27）。

被测：GET /api/v1/admin/server/usage/report?days=N
     服务层 app.application.system.usage_report（纯读端聚合，不碰 schema）

覆盖派单四项：
1. by_task / by_day / by_model 分组、合计与排序正确（含 task 为空 → (untagged)、
   provider/model 为空 → (unknown)），自然日按**应用本地日界**归桶（不是 UTC 日界）；
2. days 口径：越界 → 400 拒绝（声明为「拒绝而非静默归一」），非整数由 FastAPI 先行 422；
3. 空库 / 读库异常 → 200 + 空结构，不 500（fail-open）；
4. 鉴权沿用控制台口径 require_server_admin（非 server_admin → 403、未登录 → 401）；
   另钉一条「报表调用前后表行数不变」= 只读性。

口径：临时库一律 pytest tmp_path 私有 SQLite 文件（_dbclone 页级克隆，不碰 backend/data）；
用例前后清权限进程内缓存，避免跨用例残留凭空造 403。
"""
import asyncio
import logging
import sys
from datetime import timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import admin as admin_api
from app.application import permission_service as perm
from app.auth.config import create_token
from app.utils.timeutil import app_local_now, app_tz_offset_hours

pytestmark = pytest.mark.slow

ROOT_UID, NON_ADMIN_UID = 1, 3  # 1=server_admin；3=主账号但非 server_admin（控制台应拒）
URL = "/api/v1/admin/server/usage/report"


# ---------------------------------------------------------------- 造数时间 helper

def _at_local(days_ago: int, hour: int, minute: int = 0):
    """days_ago 天前（**应用本地日历**）的某时刻 → 库内口径（UTC naive）。

    种子行一律取 days_ago>=1：昨天任意时刻必然已过去，用例不会因「跑在本地正午前」把
    窗口内的行甩到右界之外（created_at <= now）。
    """
    dt = (app_local_now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _local_date(days_ago: int) -> str:
    """days_ago 天前的应用本地自然日（报表 by_day 的期望键）。"""
    return (app_local_now() - timedelta(days=days_ago)).date().isoformat()


# ---------------------------------------------------------------- 临时库 fixture

def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库接到所有 app.* 模块的 async_session_factory 名上（含 import 期早绑定）。

    与 test_admin_console_p2 同法：permission_service 等在 import 期就 ``from ... import``
    绑定了引用，只换 app.db.database 这一个接缝不够。
    """
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
def report_db(monkeypatch, tmp_path):
    """私有临时库（建全表 + 两个管理员账号），不预灌用量行。"""
    engine = clone_engine(tmp_path / "t6_report.db")
    factory = make_session_factory(engine)
    asyncio.run(_seed_users(factory))
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_perm_cache():
    """清权限进程内缓存（跨用例残留会凭空造 403 / 放行非管理员）。"""
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()
    yield
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()


async def _seed_users(factory) -> None:
    from app.models.user import User

    async with factory() as db:
        db.add_all([
            User(id=ROOT_UID, username="root", nickname="根", is_admin=True, server_admin=True),
            User(id=NON_ADMIN_UID, username="other", nickname="别的根", is_admin=True,
                 server_admin=False),
        ])
        await db.commit()


async def _seed_usage(factory) -> None:
    """五条用量行 + 四条留痕行：四用/三留痕在窗口内（跨两个本地日），其余在窗口外。"""
    from app.models.agent import AgentTaskLog, LlmUsage

    def _u(task, provider, model, p, c, total, reason, created):
        return LlmUsage(user_id=ROOT_UID, provider=provider, model=model, prompt_tokens=p,
                        completion_tokens=c, total_tokens=total, reasoning_tokens=reason,
                        task=task, created_at=created)

    async with factory() as db:
        db.add_all([
            # D-2 本地 00:30：其 UTC 时间是 D-3 16:30 —— 专门用来钉「按本地日界归桶」
            _u("chat", "deepseek", "v4-flash", 100, 20, 120, 5, _at_local(2, 0, 30)),
            _u("chat", "deepseek", "v4-flash", 60, 10, 70, 0, _at_local(1, 12)),
            _u("memory", "bailian", "qwen-turbo", 30, 5, 35, 0, _at_local(1, 12)),
            _u(None, None, None, 7, 0, 7, 0, _at_local(1, 12)),                     # 无归因行
            _u("chat", "deepseek", "v4-flash", 999, 999, 1998, 0, _at_local(30, 12)),  # 窗口外
        ])
        db.add_all([
            AgentTaskLog(route="usage_estimated", created_at=_at_local(1, 12)),      # 计入
            AgentTaskLog(route="usage_estimated", created_at=_at_local(2, 12)),      # 计入
            AgentTaskLog(route="chat", created_at=_at_local(1, 12)),                 # 非估算留痕
            AgentTaskLog(route="usage_estimated", created_at=_at_local(30, 12)),     # 窗口外
        ])
        await db.commit()


async def _row_counts(factory) -> tuple[int, int]:
    from app.models.agent import AgentTaskLog, LlmUsage

    async with factory() as db:
        usage = len((await db.execute(select(LlmUsage.id))).scalars().all())
        logs = len((await db.execute(select(AgentTaskLog.id))).scalars().all())
    return usage, logs


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_token(uid)}"}


def _report(days=None) -> tuple[int, dict]:
    r = _get(days)
    return r.status_code, (r.json() if r.status_code == 200 else {})


def _get(days=None):
    url = URL + (f"?days={days}" if days is not None else "")
    return _client().get(url, headers=_auth(ROOT_UID))


# ---------------------------------------------------------------- 1. 分组 / 合计 / 排序

def test_报表分组合计与排序正确(report_db):
    asyncio.run(_seed_usage(report_db))
    before = asyncio.run(_row_counts(report_db))
    code, data = _report(7)
    assert code == 200, data

    assert data["total"] == {"calls": 4, "prompt_tokens": 197, "completion_tokens": 35,
                             "total_tokens": 232, "reasoning_tokens": 5}

    by_task = data["by_task"]
    assert [b["task"] for b in by_task] == ["chat", "memory", "(untagged)"]  # 用量降序
    assert by_task[0] == {"task": "chat", "calls": 2, "prompt_tokens": 160, "completion_tokens": 30,
                          "total_tokens": 190, "reasoning_tokens": 5}
    # task 为空的行自成 (untagged) 桶，不混进真实用途（与 M0 项 3 同哨兵）
    assert by_task[2] == {"task": "(untagged)", "calls": 1, "prompt_tokens": 7,
                          "completion_tokens": 0, "total_tokens": 7, "reasoning_tokens": 0}

    # provider+model 组合成桶；缺失列归 (unknown)（与 (untagged) 同一哨兵思路）
    assert [(b["provider"], b["model"], b["total_tokens"]) for b in data["by_model"]] == [
        ("deepseek", "v4-flash", 190), ("bailian", "qwen-turbo", 35),
        ("(unknown)", "(unknown)", 7)]

    # by_day：按应用本地日界、**日期升序**（时间序列读法，与其余三桶的用量降序刻意不同）
    assert [b["date"] for b in data["by_day"]] == [_local_date(2), _local_date(1)]
    d2, d1 = data["by_day"]
    assert (d2["calls"], d2["total_tokens"]) == (1, 120)   # 该行 UTC 日属 D-3：证明没按 UTC 归桶
    assert (d1["calls"], d1["total_tokens"]) == (3, 112)

    for bucket in ("by_task", "by_day", "by_model"):
        assert sum(b["calls"] for b in data[bucket]) == 4, bucket        # 三桶各自与 total 闭合
        assert sum(b["total_tokens"] for b in data[bucket]) == 232, bucket
    vals = [b["total_tokens"] for b in data["by_task"]] + [b["total_tokens"] for b in data["by_model"]]
    assert vals[:3] == sorted(vals[:3], reverse=True) and vals[3:] == sorted(vals[3:], reverse=True)

    # estimated_calls：只数窗口内 route=usage_estimated 的留痕（M0 项 2(b) 写入点）
    assert data["estimated_calls"] == 2

    # 只读性：取报表不产生任何写入
    assert asyncio.run(_row_counts(report_db)) == before


def test_默认days为7且窗口字段按本地日界给出(report_db):
    asyncio.run(_seed_usage(report_db))
    code, data = _report()            # 不带 days
    assert code == 200
    w = data["window"]
    assert w["days"] == 7 and w["tz_offset_hours"] == app_tz_offset_hours()
    assert w["start_local"].startswith(_local_date(6))              # days=N 含今天 → 前推 N-1 个本地日
    exp_start_utc = (app_local_now() - timedelta(days=6)).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    assert w["start_utc"] == exp_start_utc.isoformat(timespec="seconds")
    assert data["total"]["calls"] == 4


def test_收窄days时窗口外行被剔除(report_db):
    asyncio.run(_seed_usage(report_db))
    code, data = _report(2)           # 只剩 D-1 一天
    assert code == 200
    assert [b["date"] for b in data["by_day"]] == [_local_date(1)]
    assert data["total"] == {"calls": 3, "prompt_tokens": 97, "completion_tokens": 15,
                             "total_tokens": 112, "reasoning_tokens": 0}
    assert data["estimated_calls"] == 1


# ---------------------------------------------------------------- 2. days 校验口径

def test_days越界返回400而非静默归一(report_db):
    for bad in (0, -1, 91, 365):
        r = _client().get(f"{URL}?days={bad}", headers=_auth(ROOT_UID))
        assert r.status_code == 400, (bad, r.text)
        assert "days 1-90" in r.json()["detail"], r.text
    # 端点里没有「越界改成 7」这类归一：窗口值必须与请求值一致
    for ok in (1, 90):
        code, data = _report(ok)
        assert code == 200, ok
        assert data["window"]["days"] == ok


def test_days非整数由fastapi先行拦截为422(report_db):
    r = _client().get(f"{URL}?days=abc", headers=_auth(ROOT_UID))
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------- 3. 空库 / 异常 fail-open

def test_空库返回空结构不抛错(report_db):
    code, data = _report(7)
    assert code == 200
    assert data["total"] == {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                             "total_tokens": 0, "reasoning_tokens": 0}
    assert data["by_task"] == [] and data["by_day"] == [] and data["by_model"] == []
    assert data["estimated_calls"] == 0
    assert data["window"]["days"] == 7


def test_读库异常按空结构返回不500(report_db, monkeypatch, caplog):
    asyncio.run(_seed_usage(report_db))

    def _boom():
        raise RuntimeError("注入：用量表不可读")

    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)
    with caplog.at_level(logging.WARNING, logger="application.system"):
        r = _get(7)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"]["calls"] == 0 and data["by_task"] == [] and data["estimated_calls"] == 0
    assert "usage report read failed" in caplog.text           # 只记 WARNING，不让控制台 500


# ---------------------------------------------------------------- 4. 鉴权

def test_端点沿用server_admin门禁(report_db):
    c = _client()
    r = c.get(URL, headers=_auth(NON_ADMIN_UID))
    assert r.status_code == 403, r.text                        # 主账号但非控制台管理员 → 拒
    assert c.get(URL).status_code == 401                       # 未登录 → 拒
    assert c.get(URL, headers=_auth(ROOT_UID)).status_code == 200
