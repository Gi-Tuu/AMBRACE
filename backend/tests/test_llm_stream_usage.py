# -*- coding: utf-8 -*-
"""SSE 真流式 usage 用量记账单测（AMBRACE #57）。

采用与 test_chat_stream 相同的「独立 mock」策略：mock 掉 llm_client 的配置解析 /
密钥轮换 / 客户端构造，注入带/不带 usage 的两种流式响应，验证 _record_usage_async
分别走「末尾 chunk usage」与「按累计文本估算」两条路径。
"""
import asyncio
import types

from app.agent import llm_client


# ── 纯函数：completion token 估算 ────────────────────────────────

def test_estimate_completion_tokens_basic():
    """2 字符 ≈ 1 token；空文本 0；非空至少 1。"""
    assert llm_client._estimate_completion_tokens("") == 0
    assert llm_client._estimate_completion_tokens("你好") == 1
    assert llm_client._estimate_completion_tokens("你好世界") == 2
    assert llm_client._estimate_completion_tokens("a") == 1


# ── 流式 usage：末尾 chunk 带 usage ──────────────────────────────

def _content_chunk(text: str):
    delta = types.SimpleNamespace(content=text)
    choice = types.SimpleNamespace(delta=delta)
    return types.SimpleNamespace(usage=None, choices=[choice])


def _usage_chunk(prompt, completion, total, reasoning):
    usage = types.SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=total,
        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=reasoning),
    )
    return types.SimpleNamespace(usage=usage, choices=[])


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def create(self, **kw):
        async def _gen():
            for c in self._chunks:
                yield c
        return _gen()


def _install_fakes(monkeypatch, chunks, calls):
    async def _fake_resolve(**kw):
        return {"api_key": "k", "base_url": "https://x", "model": "m", "provider": "p"}

    monkeypatch.setattr(llm_client, "_resolve_llm_config", _fake_resolve)
    monkeypatch.setattr(llm_client, "_pick_api_key", lambda raw: "k")
    monkeypatch.setattr(
        llm_client, "_record_usage_async",
        lambda provider, model, prompt, completion, reasoning, task=None,
               user_id=None, config_id=None, group_owner_id=None: calls.append(
            (provider, model, prompt, completion, reasoning, task, user_id, config_id, group_owner_id)),
    )
    fake_client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_FakeCompletions(chunks)),
    )
    monkeypatch.setattr(llm_client, "get_llm_client", lambda api_key=None, base_url=None: fake_client)


def test_chat_completion_stream_records_usage_from_last_chunk(monkeypatch):
    """流式末尾 chunk 带 usage：优先取 usage 字段记账（含 reasoning）。"""
    chunks = [
        _content_chunk("你好"),
        _content_chunk("世界"),
        _usage_chunk(prompt=10, completion=20, total=30, reasoning=5),
    ]
    calls: list[tuple] = []
    _install_fakes(monkeypatch, chunks, calls)

    out: list[str] = []

    async def _run():
        async for piece in llm_client.chat_completion_stream(
            [{"role": "user", "content": "hi"}], model="m", provider="p", task="chat",
        ):
            out.append(piece)

    asyncio.run(_run())

    assert "".join(out) == "你好世界"
    assert calls == [("p", "m", 10, 20, 5, "chat", None, None, None)]


def test_chat_completion_stream_estimates_when_no_usage(monkeypatch):
    """流式无 usage：按累计文本估算 completion tokens（prompt/reasoning=0）并注明。"""
    chunks = [
        _content_chunk("你好"),
        _content_chunk("世界"),
        _content_chunk("。"),
    ]
    calls: list[tuple] = []
    _install_fakes(monkeypatch, chunks, calls)

    out: list[str] = []

    async def _run():
        async for piece in llm_client.chat_completion_stream(
            [{"role": "user", "content": "hi"}], model="m", provider="p", task="chat",
        ):
            out.append(piece)

    asyncio.run(_run())

    # 累计文本 "你好世界。" = 5 字符 → 5 // 2 = 2 token（估算）
    assert "".join(out) == "你好世界。"
    assert calls == [("p", "m", 0, 2, 0, "chat", None, None, None)]
