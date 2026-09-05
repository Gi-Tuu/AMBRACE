# -*- coding: utf-8 -*-
"""nodes.generate_response 微信渠道提示 + L1 输出规范（WECHAT_CHANNEL_HINT）注入测试。

只验证「渠道提示集中常量 + 注入逻辑」，不跑真实 LLM：
- channel_hint == "wechat_ilink" 且未注入 → 在最后一条 system 后插入 WECHAT_CHANNEL_HINT；
- channel_hint 非 wechat_ilink → 零注入；
- 已注入 → 不重复注入；
- WECHAT_CHANNEL_HINT 内容含 L1 关键约束。
"""
import pytest

from app.agent import nodes


def _state(**over):
    st = {
        "user_id": 1,
        "character_id": 101,
        "channel_hint": "wechat_ilink",
        "channel_hint_injected": False,
        "context_messages": [
            {"role": "system", "content": "你是小慧"},
            {"role": "user", "content": "你好"},
        ],
        "reasoning_level": 0,
        "temperature": 0.8,
        "user_message": "你好",
        "stream_sink": None,
        "new_memories": [],
    }
    st.update(over)
    return st


@pytest.fixture()
def patched(monkeypatch):
    async def _cfg(_uid):
        return None

    async def _chat(*a, **k):
        return "好的呀"

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _cfg)
    monkeypatch.setattr("app.agent.nodes.chat_completion", _chat)
    monkeypatch.setattr("app.agent.nodes.parse_response", lambda resp, state: state)
    monkeypatch.setattr("app.agent.nodes._has_after_generate_hook", lambda: False)
    monkeypatch.setattr("app.plugins.registry.run_hook", _noop)
    monkeypatch.setattr("app.plugins.registry.list_plugins", lambda: [])


def test_channel_hint_constant_contains_l1_constraints():
    hint = nodes.WECHAT_CHANNEL_HINT
    assert "网络平台" in hint          # 核心：让角色理解微信是网络平台、动作看不见
    assert "括号" in hint              # 括号保留策略
    assert "emoji" in hint            # emoji 适度
    assert "元信息" in hint            # 不提渠道/App/微信等元信息


def test_wechat_channel_hint_injected_after_last_system(patched):
    state = _state()
    import asyncio
    result = asyncio.run(nodes.generate_response(state))
    assert result is not None
    msgs = state["context_messages"]
    assert state["channel_hint_injected"] is True
    # hint 插在最后一条 system 之后（索引 1），内容 = 常量；原 user 消息仍在索引 2
    assert [m["role"] for m in msgs] == ["system", "system", "user"]
    assert msgs[1]["role"] == "system"
    assert msgs[1]["content"] == nodes.WECHAT_CHANNEL_HINT
    assert msgs[2]["content"] == "你好"


def test_non_wechat_hint_no_injection(patched):
    state = _state(channel_hint="app")
    import asyncio
    asyncio.run(nodes.generate_response(state))
    msgs = state["context_messages"]
    assert len(msgs) == 2
    assert state.get("channel_hint_injected") is False
    # 不追加渠道提示
    assert all(m.get("content") != nodes.WECHAT_CHANNEL_HINT for m in msgs)


def test_already_injected_no_duplicate(patched):
    state = _state(channel_hint_injected=True)
    import asyncio
    asyncio.run(nodes.generate_response(state))
    msgs = state["context_messages"]
    # 已注入标记 → 不重复插入
    assert len(msgs) == 2
