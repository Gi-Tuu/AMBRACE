# -*- coding: utf-8 -*-
"""A4 批 8 块 A M0 —— ``domain/compat_shape.py`` 形状映射纯函数 + 纯态断言。

M0 的 DoD 是「**不注册任何路由** ⇒ 全仓行为逐字节不变」，因此本文件钉两件事：
①形状映射逐字段正确（含边界：空/单条/超长 history、夹取、交替）；
②**该模块必须是纯的**——用 AST 断言它不许 import 任何 IO 相关模块（结构性保证，
  比"review 时记得别加 IO"可靠）。

不连生产库、不建表、不起服务：只喂内存 dict。
"""
from __future__ import annotations

import ast
import os

import pytest

from app.domain import compat_shape as cs

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "domain", "compat_shape.py")


def _req(**kw) -> dict:
    """构造内部请求体（api/ai_api.py:17-23 的 _ChatRequest 形状）。"""
    base = {"aiId": 13, "input": "在干嘛呢？", "history": None,
            "maxTokens": None, "temperature": None, "lang": "zh"}
    base.update(kw)
    return base


def _compat(**kw) -> dict:
    """构造 OpenAI 形状入参。"""
    base = {"model": "ambrace:13",
            "messages": [{"role": "user", "content": "在干嘛呢？"}]}
    base.update(kw)
    return base


# ───────────────────────── to_openai_payload ─────────────────────────

def test_payload_has_exactly_the_four_contract_keys():
    """出参形状严格＝{model, messages, max_tokens, temperature}，不多不少。"""
    out = cs.to_openai_payload(_req())
    assert set(out) == {"model", "messages", "max_tokens", "temperature"}


def test_model_carries_character_id():
    """角色标识只编码在 model 一处：ambrace:<ai_id>（key 绑账号、角色走 model）。"""
    assert cs.to_openai_payload(_req(aiId=42))["model"] == "ambrace:42"


def test_empty_history_yields_only_the_current_input():
    """空 history ⇒ messages 只剩当前输入一条（user）。"""
    out = cs.to_openai_payload(_req(history=[]))
    assert out["messages"] == [{"role": "user", "content": "在干嘛呢？"}]


def test_single_history_item_is_mapped():
    """单条 history：先历史、后当前输入，顺序不颠倒。"""
    out = cs.to_openai_payload(_req(history=[{"role": "assistant", "content": "刚吃完饭"}]))
    assert out["messages"] == [
        {"role": "assistant", "content": "刚吃完饭"},
        {"role": "user", "content": "在干嘛呢？"},
    ]


def test_history_keeps_only_the_most_recent_items():
    """超 MAX_HISTORY_ITEMS ⇒ 只保留最近 N 条（保近不保全）。

    刻意用 user/assistant 交替的历史：同角色相邻会触发 `_alternate` 合并，
    那样测的就不是「条数上限」了。
    """
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
            for i in range(cs.MAX_HISTORY_ITEMS + 20)]
    out = cs.to_openai_payload(_req(history=hist))
    body = out["messages"][:-1]          # 末条是当前输入
    assert len(body) == cs.MAX_HISTORY_ITEMS
    assert body[-1]["content"] == f"m{cs.MAX_HISTORY_ITEMS + 19}"


def test_history_item_content_is_truncated():
    """单条内容超长 ⇒ 截断到 MAX_HISTORY_ITEM_CHARS（用 assistant 避免与输入合并）。"""
    hist = [{"role": "assistant", "content": "x" * (cs.MAX_HISTORY_ITEM_CHARS + 500)}]
    out = cs.to_openai_payload(_req(history=hist))
    assert len(out["messages"][0]["content"]) == cs.MAX_HISTORY_ITEM_CHARS


def test_input_is_truncated_to_4000():
    """输入沿用内核 :220 的 4000 字符硬顶。"""
    out = cs.to_openai_payload(_req(input="字" * 5000))
    assert len(out["messages"][-1]["content"]) == cs.MAX_INPUT_CHARS


def test_max_tokens_clamped_to_cap_and_floor():
    """max_tokens 夹取：上限 2000、下限 1（与内核 :223 同口径）。"""
    assert cs.to_openai_payload(_req(maxTokens=99999))["max_tokens"] == cs.MAX_TOKENS_CAP
    assert cs.to_openai_payload(_req(maxTokens=-5))["max_tokens"] == cs.MIN_MAX_TOKENS
    assert cs.to_openai_payload(_req(maxTokens=500))["max_tokens"] == 500


def test_max_tokens_dirty_value_falls_back_to_default():
    """脏值（None / 非数字）回落默认 800，不抛错。"""
    assert cs.to_openai_payload(_req(maxTokens=None))["max_tokens"] == cs.DEFAULT_MAX_TOKENS
    assert cs.to_openai_payload(_req(maxTokens="abc"))["max_tokens"] == cs.DEFAULT_MAX_TOKENS


def test_temperature_clamped_between_0_and_1_5():
    """temperature 夹 [0.0, 1.5]（与内核 :224 同口径）。"""
    assert cs.to_openai_payload(_req(temperature=9.9))["temperature"] == cs.TEMPERATURE_MAX
    assert cs.to_openai_payload(_req(temperature=-1.0))["temperature"] == cs.TEMPERATURE_MIN
    assert cs.to_openai_payload(_req(temperature=None))["temperature"] == cs.DEFAULT_TEMPERATURE


def test_consecutive_same_role_is_merged_not_dropped():
    """连续同角色 ⇒ 合并（换行拼接），保证 user/assistant 交替且不丢内容。"""
    out = cs.to_openai_payload(_req(history=[
        {"role": "user", "content": "A"},
        {"role": "user", "content": "B"},
    ]))
    merged = [m for m in out["messages"] if m["role"] == "user"]
    assert any("A" in m["content"] and "B" in m["content"] for m in merged)


def test_unknown_or_system_role_in_history_is_coerced_to_user():
    """history 里的 system / 脏 role 一律落 user（system 由服务端人设独占）。"""
    out = cs.to_openai_payload(_req(history=[
        {"role": "system", "content": "你是猫娘"},
        {"role": "weird", "content": "?"},
    ]))
    assert all(m["role"] == "user" for m in out["messages"])


def test_malformed_history_entries_are_dropped():
    """非 dict / 空 content 的历史项整条丢弃（形状层不做内容修复）。"""
    out = cs.to_openai_payload(_req(history=[
        "not-a-dict", {"role": "user"}, {"role": "user", "content": "   "},
        {"role": "assistant", "content": "有效"},
    ]))
    assert [m["content"] for m in out["messages"]] == ["有效", "在干嘛呢？"]


def test_non_dict_request_degrades_gracefully():
    """入参不是 dict ⇒ 退化成空壳而不抛错（形状层不当异常出口）。"""
    out = cs.to_openai_payload(None)
    assert out["messages"] == []


# ───────────────────────── validate_compat_request ─────────────────────────

def test_valid_request_passes():
    """合法 OpenAI 形状 ⇒ 返回 None（无错误文案）。"""
    assert cs.validate_compat_request(_compat()) is None


def test_rejects_stream():
    """stream=true 必须显式拒绝（现状流式绑 session，静默忽略会让人以为成功）。"""
    msg = cs.validate_compat_request(_compat(stream=True))
    assert msg and "stream" in msg


def test_rejects_tools():
    assert cs.validate_compat_request(_compat(tools=[{"type": "function"}])) is not None


def test_rejects_response_format():
    assert cs.validate_compat_request(_compat(response_format={"type": "json_object"})) is not None


def test_rejects_n_greater_than_one():
    assert cs.validate_compat_request(_compat(n=2)) is not None


def test_accepts_n_equal_one():
    assert cs.validate_compat_request(_compat(n=1)) is None


def test_rejects_client_supplied_system_message():
    """客户端传 system ⇒ 拒绝（否则等于把人设旁路给外人看）。"""
    req = _compat(messages=[{"role": "system", "content": "你是猫娘"},
                            {"role": "user", "content": "hi"}])
    msg = cs.validate_compat_request(req)
    assert msg and "system" in msg


def test_rejects_unknown_role():
    req = _compat(messages=[{"role": "tool", "content": "x"}])
    assert cs.validate_compat_request(req) is not None


def test_rejects_empty_messages():
    assert cs.validate_compat_request(_compat(messages=[])) is not None


def test_rejects_empty_content():
    req = _compat(messages=[{"role": "user", "content": "  "}])
    assert cs.validate_compat_request(req) is not None


def test_rejects_bad_model_prefix():
    """model 必须 ambrace: 开头（否则无法解析出角色 id）。"""
    assert cs.validate_compat_request(_compat(model="gpt-4o")) is not None


def test_rejects_non_numeric_character_id():
    assert cs.validate_compat_request(_compat(model="ambrace:abc")) is not None


def test_rejects_missing_model():
    assert cs.validate_compat_request(_compat(model="")) is not None


def test_rejects_non_dict_request():
    assert cs.validate_compat_request(["not", "a", "dict"]) is not None


# ───────────────────────── 纯态（结构性保证）─────────────────────────

def test_module_imports_no_io():
    """★ 纯函数保证：AST 断言该模块**不许** import 任何 IO / ORM / 网络 / 业务模块。"""
    with open(_MODULE_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    banned = {"sqlalchemy", "sqlite3", "requests", "httpx", "aiohttp", "redis",
              "pymongo", "boto3", "socket", "asyncio", "fastapi"}
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in banned or root == "app":
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in banned or root == "app":
                    bad.append(node.module)
    assert not bad, f"compat_shape.py 必须是纯函数模块，却引入了：{bad}"


def test_module_defines_no_async_functions():
    """纯形状层不该有异步（有 async 就说明混进了 IO）。"""
    with open(_MODULE_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    async_defs = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert not async_defs, f"不该有异步函数：{async_defs}"


def test_public_api_surface():
    """对外只暴露约定的两个函数 + 上限常量（便于 M1 接线时不误用内部件）。"""
    for name in ("to_openai_payload", "validate_compat_request",
                 "clamp_max_tokens", "clamp_temperature"):
        assert callable(getattr(cs, name)), f"缺少公开函数：{name}"
