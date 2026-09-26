# -*- coding: utf-8 -*-
"""A4 批 5 / T6「成本与缓存护栏」M0 自检（2026-09-27）。

覆盖派单三个小项：
- 项 1：tools.list_tools 稳定排序（工具声明字节序与注册顺序解耦，system 前缀可复用上游 prompt 缓存）
- 项 2：llm_client usage 观测——① 上游已返回的缓存字段进日志；② 流式无 usage 时 prompt 记**估算值**
  而非 0，并带 estimated 标记（估算不冒充实测）
- 项 3：system.get_llm_usage 读端 by_task 聚合桶（复用 llm_usage.task 既有列，既有返回字段不减少）
"""
import asyncio
import logging
import types
from datetime import datetime

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.agent import llm_client
from app.agent import tools as tool_mod
from app.api import system as system_api
from app.auth.deps import get_current_user_id
from app.application import permission_service as perm


# ------------------------------------------------------------------ 项 1：list_tools 稳定排序

def test_list_tools_连续调用顺序一致且按名排序():
    first = tool_mod.list_tools()
    second = tool_mod.list_tools()
    names = [t.name for t in first]
    assert names == [t.name for t in second]          # 连续两次结果一致（稳定）
    assert names == sorted(names)                     # 与「按名排序」一致
    assert set(names) == set(tool_mod._REGISTRY.keys())  # 只排序，不增删工具


def test_list_tools_注册顺序不再决定返回顺序():
    """乱序登记两个临时工具：返回顺序必须按名排序（改前跟随注册顺序，声明字节序不稳定）。"""
    tool_mod.register_tool(tool_mod.ToolSpec(name="zz_tmp_probe", description="d"))
    tool_mod.register_tool(tool_mod.ToolSpec(name="aa_tmp_probe", description="d"))
    try:
        names = [t.name for t in tool_mod.list_tools()]
        assert names.index("aa_tmp_probe") < names.index("zz_tmp_probe")
        assert names == sorted(names)
    finally:
        tool_mod.unregister_tool("zz_tmp_probe")
        tool_mod.unregister_tool("aa_tmp_probe")
    assert "aa_tmp_probe" not in tool_mod._REGISTRY   # 临时登记不残留，不影响其他用例


# ------------------------------------------------------------------ 项 2(a)：usage 缓存字段解析

def test_usage_cache_fields_只取上游实有字段():
    u = types.SimpleNamespace(
        prompt_cache_hit_tokens=80, prompt_cache_miss_tokens=20,
        prompt_tokens_details=None, model_extra=None,
    )
    assert llm_client._usage_cache_fields(u) == {
        "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20,
    }
    assert llm_client._usage_cache_text(u) == "prompt_cache_hit_tokens=80,prompt_cache_miss_tokens=20"

    # 百炼/OpenAI 口径：prompt_tokens_details.cached_tokens（SDK 可能给对象，也可能给 dict）
    d_obj = types.SimpleNamespace(prompt_cache_hit_tokens=None, prompt_cache_miss_tokens=None,
                                  prompt_tokens_details=types.SimpleNamespace(cached_tokens=33),
                                  model_extra=None)
    assert llm_client._usage_cache_fields(d_obj) == {"cached_tokens": 33}
    d_dict = types.SimpleNamespace(prompt_tokens_details={"cached_tokens": 7}, model_extra=None)
    assert llm_client._usage_cache_fields(d_dict) == {"cached_tokens": 7}


def test_usage_cache_fields_上游未返回不造值且fail_open():
    assert llm_client._usage_cache_text(None) == "-"     # 无此维度 → '-'，不凭空记 0
    assert llm_client._usage_cache_fields(types.SimpleNamespace(model_extra=None)) == {}

    class _Boom:
        @property
        def prompt_cache_hit_tokens(self):
            raise RuntimeError("上游字段形态异常")

    assert llm_client._usage_cache_fields(_Boom()) == {}  # 观测异常一律吞掉（fail-open）


def test_usage_cache_fields_真实openai_sdk透传厂商字段():
    """证明解析位对齐上游客户端真实返回形态（openai SDK extra=allow 透传），不是自造字段名。"""
    from openai.types.completion_usage import CompletionUsage

    u = CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                        prompt_cache_hit_tokens=8, prompt_cache_miss_tokens=2)
    assert llm_client._usage_cache_text(u) == "prompt_cache_hit_tokens=8,prompt_cache_miss_tokens=2"


# ------------------------------------------------------------------ 项 2(b)：流式无 usage → 估算 + 标记

def _fake_cfg() -> dict:
    return {"api_key": "sk-test", "base_url": "https://example.invalid/v1",
            "model": "m-test", "provider": "p", "config_id": None}


class _FakeClient:
    """替身客户端：create 返回预置的流（或非流 response），不发真实请求。"""

    def __init__(self, ret):
        self._ret = ret
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        return self._ret


def _chunk(text, usage=None):
    return types.SimpleNamespace(
        usage=usage,
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=text))],
    )


def _fake_stream(chunks):
    async def _gen():
        for c in chunks:
            yield c
    return _gen()


def _patch_llm_entry(monkeypatch, ret, captured, obs_sink):
    """把 LLM 出入口（配置解析 / 客户端 / 用量落库 / obs 埋点）换成替身，只验证观测分支。"""
    async def _resolve(**kwargs):
        return _fake_cfg()

    async def _owner(uid):
        return uid

    def _record(provider, model, prompt_tokens, completion_tokens, reasoning_tokens, **kw):
        captured.append({"provider": provider, "model": model, "prompt": prompt_tokens,
                         "completion": completion_tokens, "reasoning": reasoning_tokens, **kw})

    monkeypatch.setattr(llm_client, "_resolve_llm_config", _resolve)
    monkeypatch.setattr(llm_client, "_resolve_group_owner_id", _owner)
    monkeypatch.setattr(llm_client, "_client_via_registry", lambda cfg, key: _FakeClient(ret))
    monkeypatch.setattr(llm_client, "_record_usage_async", _record)
    monkeypatch.setattr("app.memory.observability.obs_event",
                        lambda cid, metric, detail, kind=None: obs_sink.append((metric, dict(detail))))


def test_stream无usage时prompt记估算值并标estimated(monkeypatch, caplog):
    """派单项 2(b)：流式拿不到 usage 时 prompt 侧不再记 0，改记估算值且带估算标记。"""
    messages = [
        {"role": "system", "content": "s" * 40},
        {"role": "user", "content": [{"type": "text", "text": "u" * 20},
                                     {"type": "image_url", "image_url": {"url": "x"}}]},
    ]  # 文本合计 60 字符 → 估算 prompt = 60 / 2 = 30（图片段不参与折算）
    captured, obs = [], []
    _patch_llm_entry(monkeypatch, _fake_stream([_chunk("你好世界"), _chunk("!")]), captured, obs)

    async def _drain():
        return "".join([p async for p in llm_client.chat_completion_stream(
            messages=messages, task="chat", user_id=7)])

    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        out = asyncio.run(_drain())

    assert out == "你好世界!"                                    # 返回值一字未改
    assert len(captured) == 1
    row = captured[0]
    assert row["prompt"] == 30 and row["prompt"] != 0           # 估算值，不再是 0
    assert row["completion"] == 2                               # "你好世界!"=5 字符 // 2（下取整）
    assert row["estimated"] is True                             # 显式标估算
    assert "estimated=true" in caplog.text and "prompt=30" in caplog.text


def test_stream有usage时保持实测值不标估算(monkeypatch, caplog):
    usage = types.SimpleNamespace(
        prompt_tokens=111, completion_tokens=22, total_tokens=133,
        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=5),
        prompt_cache_hit_tokens=100, prompt_cache_miss_tokens=11,
        prompt_tokens_details=None, model_extra=None,
    )
    captured, obs = [], []
    _patch_llm_entry(monkeypatch, _fake_stream([_chunk("x", usage=usage)]), captured, obs)

    async def _drain():
        return [p async for p in llm_client.chat_completion_stream(
            messages=[{"role": "user", "content": "hi"}], task="chat", user_id=7)]

    with caplog.at_level(logging.INFO, logger="agent.llm"):
        pieces = asyncio.run(_drain())

    assert pieces == ["x"]
    row = captured[0]
    assert (row["prompt"], row["completion"], row["reasoning"]) == (111, 22, 5)  # 实测值原样
    assert "estimated" not in row                                               # 实测行不带估算标记
    assert "cache=prompt_cache_hit_tokens=100,prompt_cache_miss_tokens=11" in caplog.text


def test_chat_completion_usage日志带缓存维度(monkeypatch, caplog):
    """派单项 2(a)：非流式 usage 日志行补出上游已返回、原先没采的缓存字段。"""
    usage = types.SimpleNamespace(
        prompt_tokens=50, completion_tokens=7, total_tokens=57,
        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=3),
        prompt_cache_hit_tokens=40, prompt_cache_miss_tokens=10,
        prompt_tokens_details=None, model_extra=None,
    )
    response = types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="回复", reasoning_content=None),
            finish_reason="stop")],
        usage=usage,
    )
    captured, obs = [], []
    _patch_llm_entry(monkeypatch, response, captured, obs)

    with caplog.at_level(logging.INFO, logger="agent.llm"):
        out = asyncio.run(llm_client.chat_completion(
            messages=[{"role": "user", "content": "hi"}], task="chat", user_id=7))

    assert out == "回复"                                         # 返回值一字未改
    assert captured[0]["prompt"] == 50
    assert "cache=prompt_cache_hit_tokens=40,prompt_cache_miss_tokens=10" in caplog.text


def test_record_usage_async估算行走obs明细留痕(monkeypatch):
    """估算标记落点：llm_usage 无标记列（本单禁止改表），故 estimated=true 写进既有 obs 明细。"""
    captured, obs = {}, []

    class _FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def add(self, obj):
            captured["obj"] = obj

        async def commit(self):
            pass

    monkeypatch.setattr("app.db.database.async_session_factory", lambda: _FakeDB())
    monkeypatch.setattr("app.memory.observability.obs_event",
                        lambda cid, metric, detail, kind=None: obs.append((metric, dict(detail))))

    async def _run():
        llm_client._record_usage_async("p", "m", 30, 12, 0, task="chat", estimated=True)
        await asyncio.sleep(0.05)  # 等后台落库任务

    asyncio.run(_run())
    obj = captured["obj"]
    assert obj.prompt_tokens == 30 and obj.completion_tokens == 12
    assert obj.total_tokens == 42                                  # 记账口径不变（prompt+completion）
    assert obs and obs[0][0] == "usage_estimated"
    assert obs[0][1]["estimated"] is True and obs[0][1]["prompt_tokens_est"] == 30

    obs.clear()
    captured.clear()

    async def _run_measured():
        llm_client._record_usage_async("p", "m", 99, 1, 0, task="chat")  # 默认非估算
        await asyncio.sleep(0.05)

    asyncio.run(_run_measured())
    assert obs == [] and captured["obj"].prompt_tokens == 99       # 实测行不留痕


# ------------------------------------------------------------------ 项 3：by_task 读端聚合

@pytest.fixture()
def usage_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库（_dbclone 页级克隆）：不触碰 backend/data 生产库。"""
    engine = clone_engine(tmp_path / "t6.db")
    factory = make_session_factory(engine)
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(perm, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    perm._admin_cache.clear()
    yield
    perm._admin_cache.clear()


async def _seed(factory):
    from app.models.agent import LlmUsage
    from app.models.user import User
    now = datetime.now()
    async with factory() as db:
        db.add(User(id=1, username="main", nickname="n1", is_admin=True))
        for task, total in (("chat", 100), ("chat", 200), ("memory", 50), (None, 5)):
            db.add(LlmUsage(user_id=1, provider="p", model="m1", prompt_tokens=total,
                            completion_tokens=0, total_tokens=total, reasoning_tokens=0,
                            task=task, created_at=now))
        await db.commit()


def _client_for(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


@pytest.mark.slow
def test_llm_usage汇总新增by_task桶且既有字段不减少(usage_db):
    asyncio.run(_seed(usage_db))
    r = _client_for(1).get("/api/v1/system/llm-usage")
    assert r.status_code == 200, r.text
    data = r.json()

    buckets = {b["task"]: b for b in data["by_task"]}
    assert buckets["chat"] == {"task": "chat", "calls": 2, "total": 300, "prompt": 300, "completion": 0}
    assert buckets["memory"]["total"] == 50 and buckets["memory"]["calls"] == 1
    assert buckets["(untagged)"]["total"] == 5                    # 无归因行单独成桶，不混进真实任务
    assert [b["task"] for b in data["by_task"]] == ["chat", "memory", "(untagged)"]  # 用量降序
    assert sum(b["total"] for b in data["by_task"]) == data["used_total"]  # 与累计口径一致

    # 既有返回字段原样在位（只增不减）
    for key in ("total_limit", "limit_source", "used_total", "remaining",
                "today", "week", "month", "by_model", "by_user", "can_edit_limit"):
        assert key in data, key
    assert data["used_total"] == 355
    assert {m["model"]: m["total"] for m in data["by_model"]} == {"m1": 355}
    assert {u["user_id"]: u["total"] for u in data["by_user"]} == {1: 355}


@pytest.mark.slow
def test_llm_usage汇总by_task聚合失败按空处理不拖垮接口(usage_db, monkeypatch, caplog):
    """项 3 约束：聚合异常 → by_task 按空 + 只记 WARNING，接口照常 200、既有统计不受影响。"""
    asyncio.run(_seed(usage_db))
    real_factory = usage_db

    class _BadTaskRow:
        """代理用量行：只有 task 读取抛异常（模拟列值异常），其余字段照常透传。"""

        def __init__(self, inner):
            self._inner = inner

        @property
        def task(self):
            raise RuntimeError("注入：task 不可读")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _WrappedSession:
        def __init__(self, db):
            self._db = db

        async def __aenter__(self):
            await self._db.__aenter__()
            return self

        async def __aexit__(self, *a):
            return await self._db.__aexit__(*a)

        async def execute(self, stmt, *a, **kw):
            res = await self._db.execute(stmt, *a, **kw)
            if "FROM llm_usage" in str(stmt):   # 只替换用量查询结果，其余查询原样透传
                return _FakeResult([_BadTaskRow(r) for r in res.scalars().all()])
            return res

    monkeypatch.setattr("app.db.database.async_session_factory",
                        lambda: _WrappedSession(real_factory()))

    with caplog.at_level(logging.WARNING, logger="application.system"):
        r = _client_for(1).get("/api/v1/system/llm-usage")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["by_task"] == []                    # 聚合失败按空处理
    assert data["used_total"] == 355                # 主口径不受观测块影响
    assert "by_task aggregate failed" in caplog.text
