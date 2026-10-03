# -*- coding: utf-8 -*-
"""S2 M0（2026-09-27）上下文注入加长：账号级预算档位 + 读数端。

守的口径（对应派单五条）：
1. **默认＝标准＝现状 9000，逐字节旧行为**：未配置档位（ContextVar 未设 / 列 NULL / 读库失败）
   时有效预算 == TOTAL_SYSTEM_QUOTA_TOKENS，裁剪线、埋点字段与改动前一致；
2. 三档映射（standard 9000 / extended 13000 / max 18000）+ 夹紧（上限 20000、下限 256）：
   越界与脏值一律夹回，**不抛错**（对话链路不能被配置打断）；
3. 档位只抬**总预算**：超限仍走 _apply_system_total_quota 既有裁剪，quota_clipped_sections
   留痕照写且字段不新增（分区配额也不随档位放大）；
4. 读数接口：有档位 / 有该档有效预算 / 最近一轮实际占用取自 system_total_chars 埋点，
   **无样本时 status=unknown 且数值为 None**（不拿预算值倒推占用）；坏库 fail-open 返回结构而非 500；
5. 迁移 c6d7e8f9a0b1（users.context_budget_tier 可空列）幂等 + 可逆 + 收尾回验，
   以及 migrate.py 当前 schema 哨兵登记。

纪律：纯函数级用例不连任何库；读数/迁移用例走 pytest tmp_path 私有 SQLite（_dbclone 模板克隆；
跑迁移那条不用克隆——克隆模板是 create_all 出的当前 schema，测不到迁移路径会假绿），
全程不触 backend/data 生产库。
"""
import asyncio
import inspect
import json
import os
import sqlite3

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.agent import context_builder as cb
from app.agent import loop as agent_loop
from app.api import system as system_api
from app.application import system as system_svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.agent import AgentTaskLog
from app.models.user import User

USER = 1
OTHER = 2
_STANDARD = 9000
_EXTENDED = 13000
_MAX = 18000
_CEILING = 20000
_RESERVE_SUM = 800 + 500
_PREV_REV = "b4c5d6e7f8a9"          # c6d7e8f9a0b1 的 down_revision（本迁移的上一节）
_NEW_REV = "c6d7e8f9a0b1"
_OLD_DETAIL_KEYS = {"total_removed", "blocks"}


# ────────────────────────── 夹具 / helper ──────────────────────────

@pytest.fixture(autouse=True)
def _no_tier_leak():
    """守卫：档位 set 只应发生在协程/用例内部 ⇒ 外层上下文跑完必须仍是「未设置」。"""
    before = cb.get_turn_context_budget_tier()
    yield
    assert cb.get_turn_context_budget_tier() == before, "跨用例/跨请求上下文污染"


@pytest.fixture()
def detail_events(monkeypatch):
    """捕获 obs_event(… "quota_clipped_sections", detail)（patch 源头，模块内动态 import）"""
    events: list[tuple] = []
    monkeypatch.setattr(
        "app.memory.observability.obs_event",
        lambda cid, metric, detail, kind=None: events.append((cid, metric, detail)),
    )
    return events


def _block(chars: int) -> str:
    """恰好 chars 个字符的多行文本（行宽 50=49 字 + 换行；零头补一行），保证整行边界可裁"""
    body = ("填" * 49 + "\n") * (chars // 50)
    rem = chars - len(body)
    if rem >= 2:
        body += "余" * (rem - 1) + "\n"
    elif rem == 1:
        body += "余"
    return body


def _msgs(*sizes: int) -> list[dict]:
    """若干 system 块（合计 = sum(sizes) 字符）+ 一条不参与配额的 user 消息"""
    msgs = [{"role": "system", "content": _block(n)} for n in sizes]
    msgs.append({"role": "user", "content": "在忙吗"})
    return msgs


def _sys_chars(msgs: list[dict]) -> int:
    return sum(len(m["content"]) for m in msgs if m["role"] == "system")


def _run(coro):
    """在独立 Task 里跑协程（Task 复制上下文快照 ⇒ 用例体内的 set 不会被外部看到）"""
    async def _main():
        return await coro
    return asyncio.run(_main())


@pytest.fixture()
def tier_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库（含 users 表）；逐例锁定预留开关为「关」。"""
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "context_budget_reserve", False)
    yield factory
    asyncio.run(engine.dispose())


def _make_client(factory, user_id: int = USER) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _seed_user(factory, user_id: int, *, tier: str | None = None) -> None:
    async def _main():
        async with factory() as db:
            db.add(User(id=user_id, username=f"u{user_id}", nickname=f"号{user_id}",
                        context_budget_tier=tier))
            await db.commit()
    _run(_main())


def _seed_obs(factory, *, route: str, steps: dict, user_id: int = USER, character_id: int = 13) -> None:
    async def _main():
        async with factory() as db:
            db.add(AgentTaskLog(user_id=user_id, character_id=character_id, trigger="memory_obs",
                                route=route, steps_json=json.dumps(steps, ensure_ascii=False),
                                status="ok"))
            await db.commit()
    _run(_main())


# ──────────────── 1. 默认＝标准＝9000：逐字节旧行为 ────────────────

def test_未设置档位时有效预算等于总硬顶(detail_events):
    assert cb.CONTEXT_BUDGET_TIER_DEFAULT == "standard"
    assert cb.get_turn_context_budget_tier() is None, "外层上下文默认未设置"
    assert cb._effective_system_budget_tokens() == cb.TOTAL_SYSTEM_QUOTA_TOKENS == _STANDARD
    # 显式标准档 == 未设置（同一条预算线，不存在「配了标准档反而变了」）
    assert cb._effective_system_budget_tokens(tier="standard") == _STANDARD
    assert cb._effective_system_budget_tokens(tier=None) == _STANDARD


def test_未设置时裁剪线与改动前一致(detail_events):
    """18000 字符恰好等于旧预算线：不超即零裁剪零埋点（与 P2a 的旧口径同一边界）"""
    msgs = _msgs(_STANDARD - 1, _STANDARD - 1)   # 合计 17998 < 18000
    assert _sys_chars(msgs) == 17998
    before = [m["content"] for m in msgs]
    cb._apply_system_total_quota(msgs, character_id=13)
    assert [m["content"] for m in msgs] == before
    assert detail_events == []

    over = _msgs(_STANDARD + 1, _STANDARD + 1)   # 合计 18002 > 18000 → 裁
    cb._apply_system_total_quota(over, character_id=13)
    assert _sys_chars(over) <= _STANDARD * 2
    assert len(detail_events) == 1


def test_标准档跟随总硬顶常量漂移(monkeypatch):
    """standard 不写死数值：改 TOTAL_SYSTEM_QUOTA_TOKENS 时未设置账号仍跟着（防第二个事实源）"""
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", 10)
    assert cb._effective_system_budget_tokens(reserve_enabled=False) == 10
    assert cb._effective_system_budget_tokens(reserve_enabled=False, tier="standard") == 10
    assert cb.context_budget_tier_tokens(None) == 10


# ──────────────── 2. 三档映射 + 越界夹紧 ────────────────

def test_三档映射与夹紧上限():
    assert cb.context_budget_tier_tokens("standard") == _STANDARD
    assert cb.context_budget_tier_tokens("extended") == _EXTENDED
    assert cb.context_budget_tier_tokens("max") == _MAX
    assert cb.CONTEXT_BUDGET_TIER_CEILING_TOKENS == _CEILING
    assert max(cb.CONTEXT_BUDGET_TIERS.values()) <= _CEILING, "任何一档都不得超过护栏"
    # 大小写 / 空格归一（客户端传 " MAX " 不该变成脏值）
    assert cb.context_budget_tier_tokens("  MAX  ") == _MAX


@pytest.mark.parametrize("raw,expect", [
    (25000, _CEILING),           # 数值超上限 → 夹回 20000
    ("25000", _CEILING),         # 数字串同样夹回
    (-100, cb.MIN_SYSTEM_BUDGET_TOKENS),   # 负值 → 夹到下限（绝不算出负预算）
    (0, cb.MIN_SYSTEM_BUDGET_TOKENS),      # 0 → 夹到下限（0 会禁掉一切裁剪，属越界）
    (13000, _EXTENDED),
])
def test_数值越界一律夹回而不抛错(raw, expect):
    assert cb.context_budget_tier_tokens(raw) == expect


@pytest.mark.parametrize("raw", [None, "", "   ", "unknown-tier", "加长", True, False, {}, []])
def test_认不出的档位退回标准档(raw):
    assert cb.context_budget_tier_tokens(raw) == _STANDARD


def test_档表值本身越界也被上限夹住(monkeypatch):
    """护栏优先于档表：有人把 max 调成 30000，生效值仍是 20000"""
    monkeypatch.setitem(cb.CONTEXT_BUDGET_TIERS, "max", 30000)
    assert cb.context_budget_tier_tokens("max") == _CEILING
    assert cb._effective_system_budget_tokens(reserve_enabled=False, tier="max") == _CEILING


def test_上下限被改坏也算得出合法区间(monkeypatch):
    """下限 > 上限（配置打架）：取上限为准，仍不抛错、结果落在合法区间"""
    monkeypatch.setattr(cb, "CONTEXT_BUDGET_TIER_FLOOR_TOKENS", 99999)
    monkeypatch.setattr(cb, "CONTEXT_BUDGET_TIER_CEILING_TOKENS", 500)
    assert cb.context_budget_tier_tokens(1) == 500


@pytest.mark.parametrize("raw,expect", [
    ("standard", "standard"), ("EXTENDED", "extended"), (" max ", "max"),
    ("", "standard"), ("default", "standard"), ("reset", "standard"), (None, "standard"),
    ("garbage", "standard"), (25000, "max"), ("25000", "max"), (18000, "max"),
    (13000, "extended"), (12999, "standard"), (-5, "standard"), (True, "standard"),
])
def test_写入侧归一单调且越界夹到最近合法档(raw, expect):
    got = cb.normalize_context_budget_tier(raw)
    assert got == expect
    # 归一结果必然是三个合法档名之一（写进库的值域封闭）
    assert got in ("standard", "extended", "max")


def test_档位与预留叠加只抬总预算():
    """加长不绕开预留：max + 预留开 = 18000 − 1300；且分区配额表不因档位变化"""
    assert cb._effective_system_budget_tokens(reserve_enabled=True, tier="max") == _MAX - _RESERVE_SUM
    assert cb._effective_system_budget_tokens(reserve_enabled=True, tier="extended") == _EXTENDED - _RESERVE_SUM
    assert cb._effective_system_budget_tokens(reserve_enabled=True) == _STANDARD - _RESERVE_SUM
    assert cb._SECTION_QUOTA_TOKENS["chat_history"] == 4000, "分区配额是另一层，不随档位放大"


def test_总硬顶为零时档位无权打开裁剪(monkeypatch):
    """旧语义的总开关（TOTAL<=0 = 不做总量裁剪）优先于档位，不得被配置翻出来"""
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", 0)
    assert cb._effective_system_budget_tokens(tier="max") == 0


# ──────────────── 3. 生效点：ContextVar → 裁剪仍生效 ────────────────

def test_contextvar_设置与复原往返():
    outer = cb.get_turn_context_budget_tier()
    token = cb.set_turn_context_budget_tier("extended")
    assert cb.get_turn_context_budget_tier() == "extended"
    assert cb._effective_system_budget_tokens(reserve_enabled=False) == _EXTENDED, "同步读端取到本回合档位"
    cb.reset_turn_context_budget_tier(token)
    assert cb.get_turn_context_budget_tier() == outer
    assert cb._effective_system_budget_tokens(reserve_enabled=False) == _STANDARD
    cb.reset_turn_context_budget_tier(None)   # token 为空：什么都不做、不抛错


def test_脏档位写入时被归一后生效():
    token = cb.set_turn_context_budget_tier(25000)
    try:
        assert cb.get_turn_context_budget_tier() == "max", "写入侧归一，读端永远只见合法档名"
        assert cb._effective_system_budget_tokens(reserve_enabled=False) == _MAX
    finally:
        cb.reset_turn_context_budget_tier(token)


def test_加长档下同样长度不再被裁(detail_events):
    """18002 字符：标准档会裁，max 档预算内 ⇒ 一字不动、零留痕（加长确实生效）"""
    msgs = _msgs(9001, 9001)
    assert _sys_chars(msgs) == 18002
    token = cb.set_turn_context_budget_tier("max")
    try:
        before = [m["content"] for m in msgs]
        cb._apply_system_total_quota(msgs, character_id=13)
        assert [m["content"] for m in msgs] == before
        assert detail_events == [], "预算内不得留痕"
    finally:
        cb.reset_turn_context_budget_tier(token)


def test_超档位预算仍走既有裁剪与留痕(detail_events):
    """36002 字符 > max 档 36000：照样裁，且留痕字段与旧版逐字一致（档位不得绕过裁剪/留痕）"""
    msgs = _msgs(18001, 18001)
    assert _sys_chars(msgs) == 36002
    token = cb.set_turn_context_budget_tier("max")
    try:
        cb._apply_system_total_quota(msgs, character_id=13)
    finally:
        cb.reset_turn_context_budget_tier(token)
    assert _sys_chars(msgs) <= _MAX * 2
    assert len(detail_events) == 1
    cid, metric, detail = detail_events[0]
    assert (cid, metric) == (13, "quota_clipped_sections")
    assert set(detail) == _OLD_DETAIL_KEYS, f"档位不得给埋点加字段：{set(detail)}"


def test_预留开着时超档位预算留痕八字段齐备(detail_events, monkeypatch):
    """档位 + 预留同时生效：埋点仍是 P2a 那 8 个键，budget 报的是档位减预留后的数"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "context_budget_reserve", True)
    msgs = _msgs(18001, 18001)   # 36002 > (18000-1300)*2 = 33400
    token = cb.set_turn_context_budget_tier("max")
    try:
        cb._apply_system_total_quota(msgs, character_id=13)
    finally:
        cb.reset_turn_context_budget_tier(token)
    assert len(detail_events) == 1
    _, _, detail = detail_events[0]
    assert set(detail) == _OLD_DETAIL_KEYS | {
        "budget", "used", "reserve_reply", "reserve_tools", "clipped_blocks", "freed_chars"}
    assert detail["budget"] == _MAX - _RESERVE_SUM == 16700


# ──────────────── 4. 装配入口接线（每轮读库 + fail-open） ────────────────

def test_build_context入口先解析档位并收尾复原():
    """源码级接线断言：set 必须早于装配分派，reset 必须在 finally（防跨回合残留）"""
    src = inspect.getsource(cb.build_context)
    set_at = src.index("set_turn_context_budget_tier(await _resolve_account_budget_tier")
    # R4 随迁（A22 ⑤-b，2026-10-03）：`agent_context_registry` 转正后 `if use_registry:` 这行已不存在，
    # 装配分派变成唯一一句 `_ctx.build_context(...)`。断言原意一字未变（解析档位要早于装配分派、
    # 复原要在 finally），只把「分派」的锚点跟着代码搬家换成注册表调用本身。
    dispatch_at = src.index("result = await _ctx.build_context(state, stream=stream)")
    reset_at = src.index("finally:")
    assert set_at < dispatch_at < reset_at, "解析档位要早于装配，复原要在 finally"
    assert "return result" in src[reset_at:], "复原之后才返回结果"


def test_按账号解析档位_异常与缺失一律标准档(monkeypatch):
    calls: list = []

    async def _fake(user_id, db=None):
        calls.append(user_id)
        if user_id == 99:
            raise RuntimeError("db down")
        return "extended" if user_id == 7 else None

    monkeypatch.setattr(system_svc, "read_account_context_budget_tier", _fake)
    assert _run(cb._resolve_account_budget_tier(7)) == "extended"
    assert _run(cb._resolve_account_budget_tier(99)) is None, "读库异常 → 标准档（fail-open）"
    assert _run(cb._resolve_account_budget_tier(None)) is None
    assert _run(cb._resolve_account_budget_tier(0)) is None
    assert _run(cb._resolve_account_budget_tier("abc")) is None
    assert calls == [7, 99], "无 user_id / 非法 user_id 不多查一次库"


def test_存储读端_未设置与缺失账号都返回None(tier_db):
    _seed_user(tier_db, USER, tier=None)
    _seed_user(tier_db, OTHER, tier="  ")      # 空串等价未设置

    async def _main():
        async with tier_db() as db:
            return (
                await system_svc.read_account_context_budget_tier(USER, db),
                await system_svc.read_account_context_budget_tier(OTHER, db),
                await system_svc.read_account_context_budget_tier(4242, db),
            )

    assert _run(_main()) == (None, None, None)


# ──────────────── 5. 读数端 GET /system/context-budget ────────────────

def test_读数_未配置账号报标准档且无样本为未知(tier_db):
    _seed_user(tier_db, USER, tier=None)
    body = _make_client(tier_db).get("/api/v1/system/context-budget").json()
    assert body["tier"] == "standard"
    assert body["tier_source"] == "default" and body["tier_stored"] is None
    assert body["tier_budget_tokens"] == _STANDARD
    assert body["effective_budget_tokens"] == _STANDARD
    assert body["total_quota_tokens"] == _STANDARD, "既有键语义不变：常量硬顶，档位另报"
    assert body["error"] == "" and body["tier_error"] == ""
    # 无样本：状态未知 + 数值 None，不是拿预算倒推的 4500/9000 之类
    usage = body["last_usage"]
    assert usage["status"] == "unknown" and usage["reason"] == "no_sample"
    assert usage["system_chars"] is None and usage["est_tokens"] is None
    assert [o["key"] for o in body["tier_options"]] == ["standard", "extended", "max"]
    assert [o["budget_tokens"] for o in body["tier_options"]] == [_STANDARD, _EXTENDED, _MAX]
    assert [o["is_current"] for o in body["tier_options"]] == [True, False, False]


def test_读数_配置加长档后档位与有效预算同步(tier_db, monkeypatch):
    _seed_user(tier_db, USER, tier="extended")
    body = _make_client(tier_db).get("/api/v1/system/context-budget").json()
    assert (body["tier"], body["tier_source"], body["tier_stored"]) == ("extended", "user", "extended")
    assert body["tier_budget_tokens"] == _EXTENDED
    assert body["effective_budget_tokens"] == _EXTENDED
    assert body["total_quota_tokens"] == _STANDARD
    assert [o["is_current"] for o in body["tier_options"]] == [False, True, False]
    # 预留叠在档位之上（同一把算式，读端不另算）
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "context_budget_reserve", True)
    body = _make_client(tier_db).get("/api/v1/system/context-budget").json()
    assert body["effective_budget_tokens"] == _EXTENDED - _RESERVE_SUM


def test_读数_库里的脏档位被夹回且如实报原值(tier_db):
    _seed_user(tier_db, USER, tier="ultra")
    body = _make_client(tier_db).get("/api/v1/system/context-budget").json()
    assert body["tier_stored"] == "ultra", "原值照实回显（排障要看得见库里到底写了什么）"
    assert body["tier"] == "standard" and body["effective_budget_tokens"] == _STANDARD


def test_读数_有占用样本时回报实际字符与估算token(tier_db):
    _seed_user(tier_db, USER, tier="max")
    # 先一条更早的（id 小、字符少），再一条最近的：断言取的是 id desc 的最近一条
    _seed_obs(tier_db, route="system_total_chars", character_id=1,
              steps={"system_chars": 500, "budget_tokens": _MAX, "reserve_on": False})
    _seed_obs(tier_db, route="system_total_chars",
              steps={"system_chars": 8133, "budget_tokens": _MAX, "reserve_on": False})
    body = _make_client(tier_db).get("/api/v1/system/context-budget").json()
    usage = body["last_usage"]
    assert usage["status"] == "ok"
    assert usage["system_chars"] == 8133, "取最近一条（id desc）"
    assert usage["est_tokens"] == 8133 // cb._EST_CHARS_PER_TOKEN == 4066
    assert usage["budget_tokens_at_turn"] == _MAX
    assert usage["character_id"] == 13


def test_读数_他人占用样本不串账号(tier_db):
    _seed_user(tier_db, USER, tier="max")
    _seed_user(tier_db, OTHER, tier="max")
    _seed_obs(tier_db, route="system_total_chars", user_id=OTHER,
              steps={"system_chars": 30000, "budget_tokens": _MAX, "reserve_on": False})
    body = _make_client(tier_db, user_id=USER).get("/api/v1/system/context-budget").json()
    assert body["last_usage"]["status"] == "unknown"
    assert body["last_usage"]["system_chars"] is None


def test_读数_占用与裁剪两条埋点互不影响(tier_db):
    """裁剪埋点（含 blocks 数组）照旧只回标量字段；占用埋点独立一条，二者不互相顶掉"""
    _seed_user(tier_db, USER, tier="extended")
    _seed_obs(tier_db, route="quota_clipped_sections",
              steps={"total_removed": 900, "blocks": [{"removed": 900, "head": "【全景记忆·织库】独家记忆ABC"}],
                     "budget": _EXTENDED, "used": 8200})
    _seed_obs(tier_db, route="system_total_chars",
              steps={"system_chars": 16400, "budget_tokens": _EXTENDED, "reserve_on": False})
    body = _make_client(tier_db).get("/api/v1/system/context-budget").json()
    assert body["last_clip"]["detail"]["budget"] == _EXTENDED
    assert "blocks" not in body["last_clip"]["detail"]
    assert "独家记忆ABC" not in json.dumps(body, ensure_ascii=False)
    assert body["clip_count_24h"] == 1
    assert body["last_usage"]["est_tokens"] == 8200


def test_读数_坏库fail_open返回结构而非500():
    class _BrokenDb:
        """鸭子类型的坏会话：只要碰到 execute 就炸（本端点只读，不触真实引擎）。"""

        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_db] = lambda: _BrokenDb()
    app.dependency_overrides[get_current_user_id] = lambda: USER
    r = TestClient(app).get("/api/v1/system/context-budget")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "standard" and body["tier_source"] == "unavailable"
    assert body["tier_error"].startswith("tier_query_failed")
    assert body["error"].startswith("clip_query_failed"), "既有 error 口径不变（占用/裁剪段失败另计）"
    assert body["effective_budget_tokens"] == _STANDARD, "预算段与库无关，照出"
    assert body["last_usage"]["status"] == "unknown" and body["last_usage"]["est_tokens"] is None


# ──────────────── 6. 设置端 PUT /system/context-budget/tier ────────────────

def test_设置_三档写库并即时可读(tier_db):
    _seed_user(tier_db, USER, tier=None)
    client = _make_client(tier_db)
    for key, tokens in (("extended", _EXTENDED), ("max", _MAX), ("standard", _STANDARD)):
        r = client.put("/api/v1/system/context-budget/tier", json={"tier": key})
        assert r.status_code == 200, r.text
        assert r.json()["tier"] == key and r.json()["tier_budget_tokens"] == tokens
        got = client.get("/api/v1/system/context-budget").json()
        assert got["tier"] == key and got["effective_budget_tokens"] == tokens


@pytest.mark.parametrize("sent,expect", [
    (25000, "max"), ("max", "max"), ("MAX", "max"), ("乱七八糟", "standard"),
    ("", "standard"), (None, "standard"), (0, "standard"), (-9999, "standard"),
])
def test_设置_越界与脏值夹回不报错(tier_db, sent, expect):
    _seed_user(tier_db, USER, tier="max")
    r = _make_client(tier_db).put("/api/v1/system/context-budget/tier", json={"tier": sent})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == expect
    assert r.json()["previous_tier"] == "max"
    assert _make_client(tier_db).get("/api/v1/system/context-budget").json()["tier_stored"] == expect


def test_设置_空请求体与缺tier键也走归一(tier_db):
    _seed_user(tier_db, USER, tier="extended")
    client = _make_client(tier_db)
    assert client.put("/api/v1/system/context-budget/tier", json={}).json()["tier"] == "standard"
    assert client.put("/api/v1/system/context-budget/tier", json={"tier": 13000}).json()["tier"] == "extended"


def test_设置_账号不存在404(tier_db):
    r = _make_client(tier_db, user_id=4242).put(
        "/api/v1/system/context-budget/tier", json={"tier": "max"})
    assert r.status_code == 404


def test_设置_只影响本账号(tier_db):
    _seed_user(tier_db, USER, tier=None)
    _seed_user(tier_db, OTHER, tier=None)
    _make_client(tier_db, user_id=USER).put("/api/v1/system/context-budget/tier", json={"tier": "max"})
    assert _make_client(tier_db, user_id=OTHER).get(
        "/api/v1/system/context-budget").json()["tier_stored"] is None


# ──────────────── 7. 存储落点：列定义 + 哨兵 + 迁移幂等可逆 ────────────────

def test_哨兵已登记且列定义一致():
    from app.db.migrate import _CURRENT_SCHEMA_SENTINELS

    assert ("users", "context_budget_tier") in _CURRENT_SCHEMA_SENTINELS, _CURRENT_SCHEMA_SENTINELS
    col = User.__table__.c.context_budget_tier
    assert col.nullable is True, "可空：NULL = 未设置 = 标准档（老行无需回填）"
    assert col.type.length == 20
    assert col.default is None or col.default.arg is None


def _alembic_cfg():
    from alembic.config import Config

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    return cfg


def _alembic_version(db_path) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


@pytest.mark.slow
def test_迁移幂等与可逆(tmp_path, monkeypatch):
    """方式：临时文件库上真跑 alembic 编程接口（不用 _dbclone 模板——那是 create_all 的当前
    schema，跑迁移会假绿）。含「版本号退回上一节但保留列」的重放，以及历史行不回填。"""
    from sqlalchemy import create_engine, inspect

    import app.config as app_cfg

    db_path = tmp_path / "s2_mig.db"
    url_path = str(db_path).replace("\\", "/")
    monkeypatch.setattr(app_cfg.settings, "database_url", "sqlite+aiosqlite:///" + url_path)
    cfg = _alembic_cfg()

    def _cols() -> set:
        eng = create_engine("sqlite:///" + url_path)
        try:
            insp = inspect(eng)
            assert insp.has_table("users"), "整链跑完 users 必在（baseline 建表）"
            return {c["name"] for c in insp.get_columns("users")}
        finally:
            eng.dispose()

    def _tiers() -> list:
        with sqlite3.connect(str(db_path)) as conn:
            return [r[0] for r in conn.execute("SELECT context_budget_tier FROM users ORDER BY id")]

    from alembic import command

    # ① 老库形态：跑到上一节 → users 有表无该列，且已有一条历史账号
    command.upgrade(cfg, _PREV_REV)
    assert "context_budget_tier" not in _cols()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO users (id, username, nickname) VALUES (1, 'old', '老账号')")
        conn.commit()

    # ② upgrade head → 补列；历史行不回填（NULL = 未设置 = 标准档 = 该账号行为不变）
    command.upgrade(cfg, _NEW_REV)
    assert _alembic_version(db_path) == _NEW_REV
    assert "context_budget_tier" in _cols()
    assert _tiers() == [None], _tiers()

    # ③ 幂等：版本号退回上一节但列还在 → 重放本迁移不报错、不动数据（收尾回验也不误报）
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num=?", (_PREV_REV,))
        conn.execute("UPDATE users SET context_budget_tier='extended' WHERE id=1")
        conn.execute("INSERT INTO users (id, username, nickname, context_budget_tier) "
                     "VALUES (2, 'new', '新账号', 'max')")
        conn.commit()
    command.upgrade(cfg, _NEW_REV)
    assert _alembic_version(db_path) == _NEW_REV
    assert "context_budget_tier" in _cols()
    assert _tiers() == ["extended", "max"], _tiers()

    # ④ 可逆：downgrade 只删本列，表与其余列/行都在
    command.downgrade(cfg, _PREV_REV)
    assert _alembic_version(db_path) == _PREV_REV
    back = _cols()
    assert "context_budget_tier" not in back
    assert {"id", "username", "nickname", "deleted_at", "purge_after", "llm_mode"} <= back
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2

    # ⑤ 再 upgrade：列补回（档位选择随列消失，按设计退回未设置）
    command.upgrade(cfg, _NEW_REV)
    assert "context_budget_tier" in _cols()
    assert _tiers() == [None, None], _tiers()
