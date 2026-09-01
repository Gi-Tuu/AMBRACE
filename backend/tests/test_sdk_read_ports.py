# -*- coding: utf-8 -*-
"""X4 SDK 只读端口测试：权限校验、脱敏返回、事件前缀约束"""
import asyncio

import pytest

from app.plugins import sdk


@pytest.fixture()
def plugin_ctx(monkeypatch):
    """伪造一个已启用、声明全量只读权限的插件上下文"""
    monkeypatch.setattr(sdk.registry, "current_plugin_name", lambda: "test_reader")
    monkeypatch.setattr(sdk.registry, "_loaded", {
        "test_reader": {"info": {"permissions": [
            "persona:read", "memory:read", "life:read", "relationship:read",
        ]}},
    }, raising=False)


def test_ports_require_permission(monkeypatch):
    monkeypatch.setattr(sdk.registry, "current_plugin_name", lambda: "no_perm")
    monkeypatch.setattr(sdk.registry, "_loaded", {"no_perm": {"info": {"permissions": []}}}, raising=False)
    with pytest.raises(PermissionError):
        asyncio.run(sdk.get_persona(1))
    with pytest.raises(PermissionError):
        asyncio.run(sdk.search_memory(1, "q"))
    with pytest.raises(PermissionError):
        asyncio.run(sdk.get_relationship(1))
    with pytest.raises(PermissionError):
        asyncio.run(sdk.get_life_state(1))


def test_get_persona_returns_public_fields(plugin_ctx, monkeypatch):
    class _Row:
        name = "小爱"
        personality = "温柔体贴" * 200  # 超长截断
        self_statement = None

    class _Res:
        def scalar_one_or_none(self):
            return _Row()

    async def _exec(db, q):
        return _Res()

    monkeypatch.setattr(sdk, "_db_execute", _exec)
    out = asyncio.run(sdk.get_persona(7))
    assert out["name"] == "小爱"
    assert len(out["personality"]) == 500
    assert out["self_statement"] == ""


def test_search_memory_truncates_and_filters(plugin_ctx, monkeypatch):
    captured = {}

    async def _fake_search(character_id, query, limit=5):
        captured.update(cid=character_id, q=query, limit=limit)
        return [
            {"id": 1, "type": "event", "content": "x" * 300, "importance": 60},
            {"id": 2, "type": "preference", "content": "喜欢咖啡", "importance": 40},
        ]

    monkeypatch.setattr("app.memory.service.search_memories", _fake_search)
    out = asyncio.run(sdk.search_memory(7, "最近聊了什么", limit=5, types=["preference"]))
    assert captured["cid"] == 7 and captured["limit"] == 5
    assert len(out) == 1 and out[0]["type"] == "preference"

    # 截断脱敏：超长正文被截到 200
    out2 = asyncio.run(sdk.search_memory(7, "最近聊了什么", limit=5))
    assert len(out2[0]["content"]) == 200


def test_get_relationship_and_life_state(plugin_ctx, monkeypatch):
    async def _fake_states(cid):
        return {"trust": 80, "attachment": 66, "curiosity": 40, "mood": 70,
                "body_temp": 50, "desire": 30, "possessiveness": 55,
                "fatigue": 20, "sensitivity": 60, "comfort": 75, "anger": 10}

    monkeypatch.setattr("app.services.character_state_service.get_character_states", _fake_states)
    rel = asyncio.run(sdk.get_relationship(7))
    assert rel == {"trust": 80, "attachment": 66, "curiosity": 40}
    life = asyncio.run(sdk.get_life_state(7))
    assert life["mood"] == 70 and life["anger"] == 10 and len(life) == 8


def test_emit_requires_plugin_prefix(plugin_ctx):
    with pytest.raises(ValueError):
        asyncio.run(sdk.emit("kernel.event", {"x": 1}))  # 伪造内核域事件被拒


def test_emit_publishes_prefixed_event(plugin_ctx, monkeypatch):
    seen = {}

    def _fake_publish_async(event_type, payload=None):
        seen["type"] = event_type
        seen["payload"] = payload

    monkeypatch.setattr("app.events.bus.event_bus.publish_async", _fake_publish_async)
    asyncio.run(sdk.emit("test_reader.thing_done", {"n": 1}))
    assert seen["type"] == "test_reader.thing_done"
    assert seen["payload"]["source"] == "test_reader" and seen["payload"]["n"] == 1


def test_permission_enum_registered():
    from app.plugins.manifest import VALID_PERMISSIONS
    for perm in ("persona:read", "memory:read", "life:read", "relationship:read"):
        assert perm in VALID_PERMISSIONS
