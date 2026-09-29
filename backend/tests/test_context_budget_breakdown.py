# -*- coding: utf-8 -*-
"""Y2（2026-09-29，S2 尾巴）上下文预算读数端：每层体量聚合 ＋ 一轮输入侧费用区间。

守的口径（对应派单第 4 条要求的八类）：
1. **无样本** → section_breakdown.status='no_sample'、items=[]、samples=0（App 据此显示
   「暂无样本」，绝不能把 0 当成「这段真的没内容」）；
2. **单样本** → 按 key 出 avg/max/empty/samples，条数与埋点段数一致，最大段 share=1.0；
3. **多样本聚合** → 均值取整、最大值取峰值、空次数只数真空段；某 key 缺席的那轮不进它的分母；
4. **只看自己的行**（user_id 维度）+ 坏 JSON / 非 sections 形状的留痕逐行跳过；
5. **越界 N 保护** → 0 / 负数 / 超大 / 脏值一律夹到 [1, 50]，不报错；夹小的 N 真的少取行；
6. **缺价目表 → unavailable**（reason=no_price_table），金额字段全 None，绝不编数字；
   有表但查不到该 model → reason=model_unpriced；
7. **档位切换后估算变化** → 区间随 effective_budget_tokens 等比变化（standard vs 加长档）；
8. **纯读** → 调用前后各表行数不变、会话里没有待写对象（零写库）。

临时库统一走 tests/_dbclone 模板克隆（不触 backend/data 生产库）；价目表只在用例内
monkeypatch 注入，仓库里那张表刻意留空（见 application/system.py 的 _TOKEN_PRICE_RANGES）。
"""
import asyncio
import json
import os
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as agent_loop
from app.api import system as system_api
from app.application import system as system_svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.agent import AgentTaskLog, LlmUsage
from app.models.user import User

# 快测档：本文件每例起一次临时库（_dbclone 克隆），属重量级/集成型用例
pytestmark = pytest.mark.slow

USER = 1
OTHER = 2
_ROUTE = "section_budget"
_FLAG_KEY = "context_budget_reserve"


@pytest.fixture()
def budget_db(tmp_path, monkeypatch):
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)
    # 逐例锁定默认口径（关），防上一例把开关留在开态
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, _FLAG_KEY, False)
    yield factory
    asyncio.run(engine.dispose())


def _run(factory, coro_fn):
    async def _main():
        async with factory() as db:
            return await coro_fn(db)

    return asyncio.run(_main())


def _make_client(factory, user_id: int = USER, query: str = "") -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _get(factory, query: str = "", user_id: int = USER) -> dict:
    path = "/api/v1/system/context-budget" + query
    r = _make_client(factory, user_id=user_id).get(path)
    assert r.status_code == 200, r.text
    return r.json()


def _sec(key: str, chars: int) -> dict:
    """按 agent/context/__init__.py 的埋点形态造一段（empty 与 chars 同源）。"""
    return {"key": key, "chars": chars, "empty": chars <= 0}


def _turn(sections: list[dict]) -> dict:
    return {
        "sections": sections,
        "total": len(sections),
        "n_empty": sum(1 for s in sections if s.get("empty")),
        "chars_total": sum(s["chars"] for s in sections),
    }


def _seed(factory, steps: Any, *, user_id: int = USER, route: str = _ROUTE,
          trigger: str = "memory_obs", character_id: int = 13) -> None:
    """写一条留痕（steps 传 dict 走 json.dumps，传 str 原样入库＝坏 JSON 用例）。"""
    payload = steps if isinstance(steps, str) else json.dumps(steps, ensure_ascii=False)

    async def _coro(db):
        db.add(AgentTaskLog(user_id=user_id, character_id=character_id, trigger=trigger,
                           route=route, steps_json=payload, status="ok"))
        await db.commit()

    _run(factory, _coro)


def _seed_usage(factory, *, user_id: int = USER, model: str = "deepseek-v4-flash",
                provider: str = "deepseek") -> None:
    async def _coro(db):
        db.add(LlmUsage(user_id=user_id, provider=provider, model=model, prompt_tokens=100,
                        completion_tokens=20, total_tokens=120, reasoning_tokens=0, task="chat"))
        await db.commit()

    _run(factory, _coro)


def _set_tier(factory, tier: str | None, *, user_id: int = USER) -> None:
    async def _coro(db):
        row = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if row is None:
            db.add(User(id=user_id, username="u%d" % user_id, nickname="号%d" % user_id,
                        context_budget_tier=tier))
        else:
            row.context_budget_tier = tier
        await db.commit()

    _run(factory, _coro)


def _counts(factory) -> dict:
    async def _coro(db):
        out = {}
        for name, model in (("agent_task_logs", AgentTaskLog), ("llm_usage", LlmUsage),
                           ("users", User)):
            out[name] = int((await db.execute(select(func.count()).select_from(model))).scalar() or 0)
        return out

    return _run(factory, _coro)


# ── ① 每层体量：无样本 / 单样本 / 多样本 ─────────────────────────────────

def test_no_sample_reports_no_sample_not_zero(budget_db):
    body = _get(budget_db)
    bd = body["section_breakdown"]
    assert bd["status"] == "no_sample"
    assert bd["items"] == [] and bd["samples"] == 0 and bd["keys_total"] == 0
    assert body["error"] == ""


def test_single_sample_aggregates_per_key(budget_db):
    _seed(budget_db, _turn([_sec("panorama", 1200), _sec("phone", 300), _sec("pets", 0)]))
    bd = _get(budget_db)["section_breakdown"]
    assert bd["status"] == "ok" and bd["samples"] == 1
    items = {it["key"]: it for it in bd["items"]}
    assert items["panorama"] == {"key": "panorama", "samples": 1, "avg_chars": 1200,
                                 "max_chars": 1200, "empty_count": 0, "share": 1.0}
    assert items["phone"]["share"] == 0.25          # 300 / 峰值 1200
    assert items["pets"]["empty_count"] == 1 and items["pets"]["avg_chars"] == 0
    assert bd["items"][0]["key"] == "panorama"       # 按均值降序（条形从上往下就是「谁最占地方」）
    assert bd["sections_scope"] == "top16_per_turn"  # 埋点侧每轮只带最大的 16 段，口径写进响应


def test_multi_sample_aggregation_avg_max_empty(budget_db):
    _seed(budget_db, _turn([_sec("panorama", 1000), _sec("diary", 0)]))
    _seed(budget_db, _turn([_sec("panorama", 2000)]))            # diary 这轮缺席 ⇒ 不进它的分母
    _seed(budget_db, _turn([_sec("panorama", 1500), _sec("diary", 400)]))
    bd = _get(budget_db, "?breakdown_samples=3")["section_breakdown"]
    assert bd["samples"] == 3 and bd["samples_limit"] == 3
    items = {it["key"]: it for it in bd["items"]}
    assert items["panorama"] == {"key": "panorama", "samples": 3, "avg_chars": 1500,
                                 "max_chars": 2000, "empty_count": 0, "share": 1.0}
    assert items["diary"]["samples"] == 2 and items["diary"]["avg_chars"] == 200
    assert items["diary"]["max_chars"] == 400 and items["diary"]["empty_count"] == 1
    assert items["diary"]["share"] == round(200 / 1500, 3)


def test_scoped_to_own_rows_and_tolerates_bad_json(budget_db):
    _seed(budget_db, _turn([_sec("mine", 900)]), user_id=USER)
    _seed(budget_db, _turn([_sec("theirs", 5000)]), user_id=OTHER)
    _seed(budget_db, "{坏 json", user_id=USER)
    _seed(budget_db, {"sections": "不是数组"}, user_id=USER)
    body = _get(budget_db)
    assert body["error"] == "", "单行坏留痕跳过，不该把整段拖成 fail-open"
    bd = body["section_breakdown"]
    assert bd["samples"] == 1 and [it["key"] for it in bd["items"]] == ["mine"]


# ── ② 越界 N 保护 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (0, 1), (-7, 1), (10 ** 6, system_svc.BREAKDOWN_MAX_SAMPLES),
    ("abc", system_svc.BREAKDOWN_DEFAULT_SAMPLES), (None, system_svc.BREAKDOWN_DEFAULT_SAMPLES),
    (5, 5),
])
def test_samples_clamped_not_rejected(raw, expected):
    assert system_svc._clamp_breakdown_samples(raw) == expected


def test_clamped_limit_actually_limits_rows(budget_db):
    for chars in (100, 200, 300):
        _seed(budget_db, _turn([_sec("panorama", chars)]))
    bd = _get(budget_db, "?breakdown_samples=1")["section_breakdown"]
    assert bd["samples"] == 1 and bd["samples_limit"] == 1
    assert bd["items"][0]["avg_chars"] == 300, "只取最近一条（id desc）"
    big = _get(budget_db, "?breakdown_samples=99999")["section_breakdown"]
    assert big["samples_limit"] == system_svc.BREAKDOWN_MAX_SAMPLES
    assert big["samples"] == 3 and big["items"][0]["avg_chars"] == 200


# ── ③ 费用估算 ───────────────────────────────────────────────────────────

def test_cost_estimate_unavailable_without_price_table(budget_db):
    """仓库里没有价目来源 ⇒ 如实标 unavailable，金额一律 None（不编数字）。"""
    assert system_svc._TOKEN_PRICE_RANGES == {}, "价目表默认必须为空（有价目就该走另一批口径）"
    _seed_usage(budget_db, model="deepseek-v4-flash")
    body = _get(budget_db)
    cost = body["cost_estimate"]
    assert cost["status"] == "unavailable" and cost["reason"] == "no_price_table"
    for field in ("per_million_low", "per_million_high", "per_turn_low", "per_turn_high"):
        assert cost[field] is None, field
    assert cost["basis"] == "full_effective_budget_input_only"
    assert cost["budget_tokens"] == body["effective_budget_tokens"]
    assert cost["currency"] == system_svc._PRICE_CURRENCY


def test_cost_estimate_range_from_price_table(budget_db, monkeypatch):
    monkeypatch.setattr(system_svc, "_TOKEN_PRICE_RANGES", {"deepseek-v4-flash": (1.0, 2.0)})
    _seed_usage(budget_db, model="deepseek-v4-flash")
    body = _get(budget_db)
    cost = body["cost_estimate"]
    budget = body["effective_budget_tokens"]
    assert cost["status"] == "ok" and cost["price_source"] == "deepseek-v4-flash"
    assert cost["per_million_low"] == 1.0 and cost["per_million_high"] == 2.0
    assert cost["per_turn_low"] == round(budget / 1_000_000 * 1.0, 6)
    assert cost["per_turn_high"] == round(budget / 1_000_000 * 2.0, 6)
    assert cost["per_turn_high"] > cost["per_turn_low"], "报的是区间，不是单点承诺"


def test_cost_estimate_unpriced_model(budget_db, monkeypatch):
    """有价目表但查不到这个 model（也无 default）→ model_unpriced，仍然不编数字。"""
    monkeypatch.setattr(system_svc, "_TOKEN_PRICE_RANGES", {"other-model": (1.0, 2.0)})
    _seed_usage(budget_db, model="deepseek-v4-flash", provider="zzz")
    cost = _get(budget_db)["cost_estimate"]
    assert cost["status"] == "unavailable" and cost["reason"] == "model_unpriced"
    assert cost["model"] == "deepseek-v4-flash"
    assert cost["per_turn_low"] is None


def test_cost_estimate_follows_tier_switch(budget_db, monkeypatch, flag_on):
    """切档 ⇒ 生效预算变 ⇒ 估算区间跟着变（比值 == 预算比值，不是拍脑袋的另一套数）。

    顺带钉住「预留开关开着时也一样」：估算基数就是**生效**预算，不是硬顶常量。
    """
    monkeypatch.setattr(system_svc, "_TOKEN_PRICE_RANGES", {"default": (0.5, 1.0)})
    std = _get(budget_db)["cost_estimate"]
    _set_tier(budget_db, "extended")
    ext = _get(budget_db, "?breakdown_samples=5")["cost_estimate"]
    assert ext["status"] == "ok" and ext["price_source"] == "default"
    assert std["budget_tokens"] < ext["budget_tokens"]
    assert std["budget_tokens"] == 9000 - 1300 and ext["budget_tokens"] == 13000 - 1300
    assert ext["per_turn_low"] == round(
        std["per_turn_low"] * ext["budget_tokens"] / std["budget_tokens"], 6)
    assert ext["per_turn_high"] > std["per_turn_high"]


@pytest.fixture()
def flag_on(monkeypatch):
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, _FLAG_KEY, True)


# ── ④ 纯读断言 ───────────────────────────────────────────────────────────

def test_endpoint_is_read_only(budget_db):
    """整条读数链路零写库：前后各表行数不变，会话里没有待写对象。"""
    _seed(budget_db, _turn([_sec("panorama", 1000)]))
    _seed_usage(budget_db)
    _set_tier(budget_db, "extended")
    before = _counts(budget_db)

    captured: dict = {}

    async def _coro(db):
        body = await system_svc.get_context_budget(USER, db, breakdown_samples=4)
        captured["new"] = list(db.new)
        captured["dirty"] = list(db.dirty)
        captured["deleted"] = list(db.deleted)
        return body

    body = _run(budget_db, _coro)
    assert body["status"] == "ok" and body["error"] == ""
    assert captured["new"] == [] and captured["dirty"] == [] and captured["deleted"] == []
    assert _counts(budget_db) == before


def test_db_failure_fails_open_keeps_both_sections_shape(budget_db):
    """查库炸（坏会话）→ 不 500，两段仍是「无样本 / unavailable」形状，前端不用判空指针。"""
    class _BrokenDb:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    app = FastAPI()
    app.include_router(system_api.router)

    async def _db():
        yield _BrokenDb()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user_id] = lambda: USER
    r = TestClient(app).get("/api/v1/system/context-budget")
    assert r.status_code == 200
    body = r.json()
    assert body["error"].startswith("clip_query_failed")
    assert body["section_breakdown"]["status"] == "no_sample"
    assert body["section_breakdown"]["items"] == []
    assert body["cost_estimate"]["status"] == "unavailable"
