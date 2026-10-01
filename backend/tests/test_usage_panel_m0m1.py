# -*- coding: utf-8 -*-
"""A4 批 8 / 块 D「费用面板」M0＋M1 测试（2026-09-30，只读读端 + 索引）。

被测：
- 服务层 app.application.system.usage_panel（按账号 × 窗口的 by_task / by_channel + 服务端占比）
  与 _panel_estimated_segment / _panel_money_segment / _panel_days_or_raise；
- 既有读数 get_llm_usage / get_context_budget 新增 usage_panel 段（只增不减）；
- 迁移 c3d5e7f9a1b2：llm_usage 两条复合索引在位（M1 唯一 schema 动作）。

派单四条自证：
1. **口径一致**：面板与 usage_report 用同一套聚合内核（_read_usage_window + _aggregate_usage_rows
   + 排序），同数据下桶值必须逐项相等；
2. **不编数字**：estimated 段没有任何数值字段、money 段金额全 None 且不复用
   ``full_effective_budget_input_only`` 这个「单轮预算投影」basis；
3. **不沿 get_llm_usage 全表载入扩窗口**（源码棘轮）：面板侧不得出现 ``select(LlmUsage)`` 整行
   载入，get_llm_usage 的既有全表载入语句一字未增（`rows = (await db.execute(select(LlmUsage)`
   仍只 1 处），且 get_llm_usage 的调用点数量钉死；
4. **只读**：调用面板前后 llm_usage / agent_task_logs 行数不变。

口径：临时库一律 pytest tmp_path 私有 SQLite（_dbclone 页级克隆，不碰 backend/data）。
"""
import asyncio
import inspect
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import system as system_api
from app.auth.deps import get_current_user_id
from app.application import permission_service as perm
from app.application import system as sys_mod
from app.utils.timeutil import app_local_now

pytestmark = pytest.mark.slow

ROOT_UID, SUB_UID, OTHER_UID = 1, 2, 4

_BACKEND = Path(__file__).resolve().parents[1]
_SYSTEM_PY = _BACKEND / "app" / "application" / "system.py"


# ---------------------------------------------------------------- helpers

def _at_local(days_ago: int, hour: int = 10, minute: int = 0) -> datetime:
    """days_ago 天前的应用本地时刻 → 库内口径（UTC naive）。种子一律 days_ago>=1 避开右界。"""
    dt = (app_local_now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _patch_session_factories(monkeypatch, factory) -> None:
    """把私有临时库接到所有 app.* 模块 import 期绑定的 async_session_factory 引用上。"""
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
def panel_db(monkeypatch, tmp_path):
    """私有临时库：1=主账号、2=其子账号、4=另一独立主账号；不预灌用量行。"""
    engine = clone_engine(tmp_path / "c8d.db")
    factory = make_session_factory(engine)
    asyncio.run(_seed_users(factory))
    _patch_session_factories(monkeypatch, factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_perm_cache():
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
            User(id=SUB_UID, username="sub", nickname="子", parent_id=ROOT_UID),
            User(id=OTHER_UID, username="other", nickname="外人", is_admin=True),
        ])
        await db.commit()


async def _seed_usage(factory, rows: list[dict]) -> None:
    from app.models.agent import LlmUsage

    async with factory() as db:
        for r in rows:
            db.add(LlmUsage(
                user_id=r.get("user_id"), task=r.get("task"), channel=r.get("channel"),
                provider=r.get("provider", "deepseek"), model=r.get("model", "v4-flash"),
                prompt_tokens=r.get("prompt", 0), completion_tokens=r.get("completion", 0),
                total_tokens=r.get("total", 0), reasoning_tokens=r.get("reasoning", 0),
                created_at=r.get("created_at") or _at_local(1),
            ))
        await db.commit()


async def _row_counts(factory) -> dict:
    from sqlalchemy import func, select

    from app.models.agent import AgentTaskLog, LlmUsage

    async with factory() as db:
        return {
            "llm_usage": int((await db.execute(select(func.count()).select_from(LlmUsage))).scalar_one()),
            "agent_task_logs": int((await db.execute(
                select(func.count()).select_from(AgentTaskLog))).scalar_one()),
        }


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


# 典型四行：两用途、两渠道（含一条渠道 NULL → (unknown)）
_SEED = [
    {"user_id": ROOT_UID, "task": "chat", "channel": "app", "prompt": 100, "completion": 20, "total": 120},
    {"user_id": ROOT_UID, "task": "chat", "channel": "app", "prompt": 60, "completion": 10, "total": 70},
    {"user_id": ROOT_UID, "task": "memory", "channel": "server", "prompt": 30, "completion": 5, "total": 35},
    {"user_id": ROOT_UID, "task": None, "channel": None, "prompt": 5, "completion": 2, "total": 7},
]


# ---------------------------------------------------------------- 1. 口径与 usage_report 一致

def test_面板分桶与usage_report同口径同排序(panel_db):
    """同一批数据（全属本账号）下：面板桶与控制台报表桶逐项相等，键集合也相等。"""
    asyncio.run(_seed_usage(panel_db, _SEED))
    report = asyncio.run(sys_mod.usage_report(7))
    panel = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))

    assert [b["task"] for b in panel["by_task"]] == [b["task"] for b in report["by_task"]]
    for pb, rb in zip(panel["by_task"], report["by_task"]):
        # 面板只多两个键（key + share），其余四件套与报表一字不差
        assert {k: v for k, v in pb.items() if k not in ("key", "share")} == rb, pb["task"]
    assert [b["channel"] for b in panel["by_channel"]] == [b["channel"] for b in report["by_channel"]]
    for pb, rb in zip(panel["by_channel"], report["by_channel"]):
        assert {k: v for k, v in pb.items() if k not in ("key", "share")} == rb, pb["channel"]
    assert panel["total"] == report["total"]


def test_面板哨兵桶与报表同哨兵不混读(panel_db):
    """task 空 → (untagged)、channel 空 → (unknown)：无归因不与真实取值混进同一桶。"""
    asyncio.run(_seed_usage(panel_db, _SEED))
    panel = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    tasks = [b["task"] for b in panel["by_task"]]
    chans = [b["channel"] for b in panel["by_channel"]]
    assert tasks == ["chat", "memory", sys_mod._USAGE_UNTAGGED]
    assert chans == ["app", "server", sys_mod._USAGE_UNKNOWN]
    assert panel["by_task"][0]["total_tokens"] == 190  # 120+70，未混入 memory/无归因


def test_面板占比由服务端算且与窗口总额闭合(panel_db):
    """share＝桶 total_tokens ÷ 窗口 total_tokens（前端零本地计算）；三桶占比合计≈1。"""
    asyncio.run(_seed_usage(panel_db, _SEED))
    panel = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    denom = panel["total"]["total_tokens"]
    assert denom == 232
    for bucket_key in ("by_task", "by_channel"):
        buckets = panel[bucket_key]
        for b in buckets:
            assert b["share"] == round(b["total_tokens"] / denom, 3), b
        assert abs(sum(b["share"] for b in buckets) - 1.0) <= 0.005, bucket_key


# ---------------------------------------------------------------- 2. 空态

def test_无数据空态结构完整不报错(panel_db):
    """空库：窗口照出、桶为空、total 全 0，不抛异常（App 端据此显示明确空态）。"""
    panel = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    assert panel["by_task"] == [] and panel["by_channel"] == []
    assert panel["total"] == {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                              "total_tokens": 0, "reasoning_tokens": 0}
    assert panel["window"]["days"] == 7
    assert panel["error"] == ""


def test_空窗口占比不做除零兜底数字(panel_db):
    """总额为 0 时不给任何桶（因此不存在 share）；这条钉的是「不编一个 0 除 0 的假占比」。"""
    panel = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    assert panel["by_task"] == []
    assert panel["money"]["amount_low"] is None and panel["money"]["amount_high"] is None


# ---------------------------------------------------------------- 3. 不编数字：estimated / money

def test_估算段如实unavailable且无任何数值(panel_db):
    """D-1：llm_usage 无估算列 ⇒ 面板不给估算数，且段落里一个数字都没有（只有状态与说明）。"""
    est = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))["estimated"]
    assert est["status"] == "unavailable"
    assert est["reason"] == "no_estimated_column"
    assert est["per_row"] == "unavailable"
    assert est["join_key"] is None
    numbers = {k: v for k, v in est.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    assert numbers == {}, numbers


def test_金额段unavailable且不冒用单轮投影basis(panel_db):
    """本轮费用估算＝单轮预算投影，不是历史花费 ⇒ 面板金额段不出数、不复用那个 basis 字符串。"""
    money = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))["money"]
    assert money["status"] == "unavailable"
    assert money["reason"] == "no_price_table"
    assert money["currency"] == "CNY"
    assert money["basis"] is None and "full_effective_budget_input_only" not in str(money)
    assert money["is_historical_spend"] is False
    assert money["amount_low"] is None and money["amount_high"] is None


def test_价目表为空是面板unavailable的前提(panel_db):
    """钉住前提：_TOKEN_PRICE_RANGES 刻意留空 ⇒ 面板金额段必然 no_price_table（有人填表要显式改口径）。"""
    assert sys_mod._TOKEN_PRICE_RANGES == {}
    assert sys_mod._panel_money_segment()["reason"] == "no_price_table"


def test_有人填了价目表面板仍不出金额(panel_db, monkeypatch):
    """闸门（不是注释）：面板不调用 _cost_estimate/_price_range_for ⇒ 填表不会变成「历史花费」。

    填表后 reason 翻成 priced_history_basis_undefined＝「有价目、但历史口径未定义」，
    status 仍 unavailable、金额仍 None ⇒ 想在这张面板上出钱，必须显式新定义 basis，
    绝不能顺手复用单轮投影那个字符串（§2.4(1) 末条：两套语义混进一个字段名＝第二套真相）。
    """
    monkeypatch.setattr(sys_mod, "_TOKEN_PRICE_RANGES", {"some-model": (1.0, 2.0)})

    def _boom(*a, **k):
        raise AssertionError("面板路径不得调用单轮预算投影 _cost_estimate")

    monkeypatch.setattr(sys_mod, "_cost_estimate", _boom)
    money = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))["money"]
    assert money["status"] == "unavailable"
    assert money["reason"] == "priced_history_basis_undefined"
    assert money["amount_low"] is None and money["amount_high"] is None
    assert money["basis"] is None and "full_effective_budget_input_only" not in str(money)
    assert money["is_historical_spend"] is False


# ---------------------------------------------------------------- 4. 账号范围（多租户安全）

def test_主账号面板含子账号与服务器级行(panel_db):
    asyncio.run(_seed_usage(panel_db, [
        {"user_id": ROOT_UID, "task": "chat", "channel": "app", "total": 100, "prompt": 100},
        {"user_id": SUB_UID, "task": "chat", "channel": "app", "total": 200, "prompt": 200},
        {"user_id": None, "task": "chat", "channel": "server", "total": 50, "prompt": 50},
        {"user_id": OTHER_UID, "task": "chat", "channel": "app", "total": 999, "prompt": 999},
    ]))
    panel = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    assert panel["total"]["total_tokens"] == 350      # 不含外人 999
    assert panel["scope"] == {"account_only": False, "includes_server_rows": True}


def test_子账号面板只看自己(panel_db):
    asyncio.run(_seed_usage(panel_db, [
        {"user_id": ROOT_UID, "task": "chat", "channel": "app", "total": 100, "prompt": 100},
        {"user_id": SUB_UID, "task": "memory", "channel": "app", "total": 200, "prompt": 200},
        {"user_id": None, "task": "chat", "channel": "server", "total": 50, "prompt": 50},
    ]))
    panel = asyncio.run(sys_mod.usage_panel(SUB_UID, 7))
    assert panel["total"]["total_tokens"] == 200
    assert panel["scope"]["account_only"] is True
    assert [b["task"] for b in panel["by_task"]] == ["memory"]


def test_面板按窗口过滤不走全表累计(panel_db):
    """窗口外（40 天前）的行不进 7 天面板 —— 与 get_llm_usage 的累计口径分得开（面板=窗口）。"""
    asyncio.run(_seed_usage(panel_db, [
        {"user_id": ROOT_UID, "task": "chat", "channel": "app", "total": 120, "prompt": 100,
         "completion": 20, "created_at": _at_local(1)},
        {"user_id": ROOT_UID, "task": "chat", "channel": "app", "total": 5000, "prompt": 5000,
         "created_at": _at_local(40)},
    ]))
    week = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    month = asyncio.run(sys_mod.usage_panel(ROOT_UID, 90))
    assert week["total"]["total_tokens"] == 120 and week["total"]["calls"] == 1
    assert month["total"]["total_tokens"] == 5120
    assert week["window"]["days"] == 7 and month["window"]["days"] == 90


# ---------------------------------------------------------------- 5. fail-open 与 days 校验

def test_读库异常failopen返回空结构加warning(panel_db, monkeypatch, caplog):
    """观测不得让读数端 500：读库炸 → 空结构 + error 标记 + 只记 WARNING。"""
    async def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(sys_mod, "_read_usage_window", _boom)
    with caplog.at_level(logging.WARNING):
        panel = asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    assert panel["error"] == "usage_panel_read_failed"
    assert panel["by_task"] == [] and panel["total"]["calls"] == 0
    assert "usage panel read failed" in caplog.text


def test_days越界与脏输入报错不静默夹取():
    """照控制台口径：越界/脏输入 → 报错（API 层映射 400），不静默夹到 90 让人误读窗口。"""
    assert sys_mod._panel_days_or_raise("30") == 30
    assert sys_mod._panel_days_or_raise(1) == 1
    assert sys_mod._panel_days_or_raise(sys_mod._PANEL_MAX_DAYS) == sys_mod._PANEL_MAX_DAYS
    for bad in (0, -1, 91, 365, "abc", None, ""):
        with pytest.raises(ValueError):
            sys_mod._panel_days_or_raise(bad)


# ---------------------------------------------------------------- 6. 只读性

def test_面板调用前后表行数不变(panel_db):
    asyncio.run(_seed_usage(panel_db, _SEED))
    before = asyncio.run(_row_counts(panel_db))
    asyncio.run(sys_mod.usage_panel(ROOT_UID, 7))
    asyncio.run(sys_mod.usage_report(7))
    assert asyncio.run(_row_counts(panel_db)) == before


# ---------------------------------------------------------------- 7. 既有读数只增不减

def test_llm_usage接口新增usage_panel段且既有字段不减少(panel_db):
    asyncio.run(_seed_usage(panel_db, _SEED))
    data = _client(ROOT_UID).get("/api/v1/system/llm-usage").json()
    for key in ("total_limit", "limit_source", "used_total", "remaining", "today", "week",
                "month", "by_model", "by_user", "by_task", "by_channel", "can_edit_limit"):
        assert key in data, key
    assert data["used_total"] == 232
    panel = data["usage_panel"]
    assert panel["total"]["total_tokens"] == 232
    assert [b["key"] for b in panel["by_task"]] == ["chat", "memory", "(untagged)"]
    assert all(isinstance(b["share"], float) for b in panel["by_task"])


def test_context_budget接口新增usage_panel段(panel_db):
    resp = _client(ROOT_UID).get("/api/v1/system/context-budget")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "usage_panel" in data and "cost_estimate" in data
    assert data["cost_estimate"]["basis"] == "full_effective_budget_input_only"  # 既有段口径未动
    assert data["usage_panel"]["money"]["is_historical_spend"] is False


# ---------------------------------------------------------------- 8. 索引（M1 唯一 schema 动作）

def _sync_engine(factory):
    """另起一个**同步** engine 指向同一临时库文件（反射/EXPLAIN 用）。

    不能直接用 async engine 的 ``sync_engine``：它共用 aiosqlite 连接池，在同步上下文里
    反射会踩 MissingGreenlet。用完由调用方 dispose。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    url = str(factory.kw["bind"].url).replace("+aiosqlite", "", 1)
    return create_engine(url, poolclass=NullPool)


def test_llm_usage两条复合索引在位(panel_db):
    from sqlalchemy import inspect as sa_inspect

    eng = _sync_engine(panel_db)
    try:
        insp = sa_inspect(eng)
        names = {i["name"] for i in insp.get_indexes("llm_usage")}
        assert {"ix_llm_usage_user_created", "ix_llm_usage_task_created"} <= names
        cols = {i["name"]: [str(c) for c in i["column_names"]] for i in insp.get_indexes("llm_usage")}
        assert cols["ix_llm_usage_user_created"] == ["user_id", "created_at"]
        assert cols["ix_llm_usage_task_created"] == ["task", "created_at"]
    finally:
        eng.dispose()


def test_索引哨兵登记且只缺索引也判落后(panel_db):
    """索引哨兵的**判别效果**：老库（表与列齐、只缺索引）必须判「落后」→ 走 upgrade head。

    索引迁移不建表也不加列，若 _schema_is_current 只看表/列，老库会被 stamp 到 head 而永久
    缺索引——「加索引」这件事被静默吃掉。登记形式＝_CURRENT_SCHEMA_SENTINELS 里带 idx: 前缀的
    条目，与列哨兵同一张表、同一个接缝（test_admin_llm_quota 正是靠 patch 这张表隔离语义）。
    """
    from sqlalchemy import text

    from app.db.migrate import (
        _CURRENT_SCHEMA_SENTINELS,
        _INDEX_SENTINEL_PREFIX,
        _schema_is_current,
    )

    for name in ("ix_llm_usage_user_created", "ix_llm_usage_task_created"):
        assert ("llm_usage", _INDEX_SENTINEL_PREFIX + name) in _CURRENT_SCHEMA_SENTINELS
    assert len(_CURRENT_SCHEMA_SENTINELS) == len(set(_CURRENT_SCHEMA_SENTINELS)), "哨兵不得重复"

    url = str(panel_db.kw["bind"].url).replace("+aiosqlite", "", 1)
    assert _schema_is_current(url) is True          # create_all 按 __table_args__ 已建好索引
    eng = _sync_engine(panel_db)
    try:
        with eng.begin() as conn:
            conn.execute(text("DROP INDEX ix_llm_usage_task_created"))
        assert _schema_is_current(url) is False     # 只缺这一条索引也照样判落后
    finally:
        eng.dispose()


def test_账号与用途窗口查询命中新索引(panel_db):
    """EXPLAIN QUERY PLAN 实测（只读）：两条过滤形态都走 idx，不出现 SCAN llm_usage。

    如实区分：**本轮读端只跑「账号 × 窗口」那一条**（_read_usage_window 不按 task 过滤，
    用途是在内存里分桶的）。「用途 × 窗口」是把分桶下推成 SQL 条件后的形态，用来说明
    ix_llm_usage_task_created 的消费条件——当前没有查询走它（见迁移 c3d5e7f9a1b2 文档同段）。
    """
    from sqlalchemy import text

    queries = {
        "账号×窗口": ("SELECT task, channel, created_at, total_tokens FROM llm_usage "
                     "WHERE user_id = 1 AND created_at >= '2026-01-01' AND created_at <= '2099-01-01'"),
        "用途×窗口": ("SELECT created_at, total_tokens FROM llm_usage "
                     "WHERE task = 'chat' AND created_at >= '2026-01-01' AND created_at <= '2099-01-01'"),
    }
    eng = _sync_engine(panel_db)
    try:
        with eng.connect() as conn:
            for label, sql in queries.items():
                plan = " ".join(str(r[3]) for r in conn.execute(text("EXPLAIN QUERY PLAN " + sql)))
                assert "SCAN llm_usage" not in plan, (label, plan)
            idx_used = {}
            for label, sql in queries.items():
                idx_used[label] = " ".join(str(r[3]) for r in conn.execute(
                    text("EXPLAIN QUERY PLAN " + sql)))
    finally:
        eng.dispose()
    assert "ix_llm_usage_user_created" in idx_used["账号×窗口"], idx_used
    assert "ix_llm_usage_task_created" in idx_used["用途×窗口"], idx_used


# ---------------------------------------------------------------- 9. 源码棘轮（不编数字 / 不扩窗口）

def _ast_scan(fn) -> dict:
    """按 AST 扫函数**代码**（注释/docstring 里会写被禁的写法，棘轮只看真代码）。

    - ``full_row_loads``：``select(LlmUsage)`` 整行载入次数（全表扫的形态特征）；
    - ``usage_calls``：对 get_llm_usage 的调用次数。
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    full_row_loads = usage_calls = column_selects = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        cname = getattr(callee, "id", None) or getattr(callee, "attr", None)
        if cname == "select" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id == "LlmUsage":
                full_row_loads += 1
            elif isinstance(first, ast.Attribute) and getattr(first.value, "id", "") == "LlmUsage":
                column_selects += 1   # select(LlmUsage.task, …) = 指定列读法
        if cname == "get_llm_usage":
            usage_calls += 1
    return {"full_row_loads": full_row_loads, "usage_calls": usage_calls,
            "column_selects": column_selects}


def test_面板不得走get_llm_usage全表载入_源码棘轮():
    """禁读 D-2 的机器化：面板这条读法只允许「窗口 + 指定列」的 SELECT。

    - 面板 / 窗口读法 / 控制台报表三处：**零**整行载入、**零**对 get_llm_usage 的调用，
      且确实用了「指定列」读法；
    - get_llm_usage 自身：整行载入仍只 1 处，且没被就地加上窗口条件
      （要扩窗口请走 usage_panel，不许在这条热路径上动刀）。
    """
    for fn in (sys_mod.usage_panel, sys_mod._read_usage_window, sys_mod.usage_report):
        scan = _ast_scan(fn)
        assert scan["full_row_loads"] == 0, fn.__name__
        assert scan["usage_calls"] == 0, fn.__name__
    assert _ast_scan(sys_mod._read_usage_window)["column_selects"] == 1

    load_src = inspect.getsource(sys_mod.get_llm_usage)
    assert _ast_scan(sys_mod.get_llm_usage)["full_row_loads"] == 1
    assert "created_at >=" not in load_src          # 全表载入没被就地扩窗口

    usage_py = (_BACKEND / "app" / "api" / "system.py").read_text(encoding="utf-8")
    assert "usage_panel" not in usage_py            # 本批不开新端点（挂在既有两个接口上）


def test_get_llm_usage未被新增调用点_源码棘轮():
    """面板一律走 usage_panel/usage_report，不得新增对 get_llm_usage 的调用（只增读数、不改路径）。

    钉死既有调用面：app/ 下只有 api/system.py 的 GET /llm-usage 一处。
    """
    import ast

    call_sites = []
    for path in (_BACKEND / "app").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            cname = getattr(getattr(node, "func", None), "attr", None) or \
                getattr(getattr(node, "func", None), "id", None)
            if cname == "get_llm_usage":
                call_sites.append(str(path.relative_to(_BACKEND)))
    assert call_sites == [str(Path("app") / "api" / "system.py")], call_sites
