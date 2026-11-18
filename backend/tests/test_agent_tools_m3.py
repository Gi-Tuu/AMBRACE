# -*- coding: utf-8 -*-
"""YAI 接入 M3：记忆适配器 recall/remember（scope 映射、source 留在适配层、tag 映射）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import agent_tools as agent_tools_mod
from app.api.agent_tools import router
from app.auth.deps import get_current_user_id


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: 3
    return TestClient(app)


def test_memory_recall_maps_scope_and_kind(client, monkeypatch):
    seen = {}

    async def _search(character_id, query, limit=5, user_id=None):
        seen.update({"character_id": character_id, "query": query,
                     "limit": limit, "user_id": user_id})
        return [
            {"id": 11, "type": "daily", "content": "用户喜欢雨天", "importance": 80},
            {"id": 12, "type": "fact", "content": "生日 3 月", "importance": 50},
        ]

    monkeypatch.setattr(agent_tools_mod, "search_memories", _search)
    r = client.post("/api/agent/tools/memory_recall",
                    json={"character_id": 42, "query": "天气", "k": 3, "kind": "daily"})
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["items"]] == [11]
    assert body["items"][0]["text"] == "用户喜欢雨天"
    assert seen == {"character_id": 42, "query": "天气", "limit": 3, "user_id": 3}


def test_memory_remember_passes_scope_and_maps_tags(client, monkeypatch):
    seen = {}

    async def _save(**kwargs):
        seen.update(kwargs)
        return 77

    monkeypatch.setattr(agent_tools_mod, "save_memory", _save)
    r = client.post("/api/agent/tools/memory_remember",
                    json={"character_id": 42, "kind": "fact", "text": "用户养了一只猫",
                          "tags": ["pet", "cat"]})
    assert r.status_code == 200
    assert r.json() == {"id": 77, "applied": True}
    assert seen["user_id"] == 3
    assert seen["character_id"] == 42
    assert seen["memory_type"] == "fact"
    assert seen["content"] == "用户养了一只猫"
    assert seen["sub_type"] == "tags:pet,cat"
    assert seen["source"] == "agent_tools"  # 固定来源，不接受模型自报


def test_memory_remember_rejected_returns_not_applied(client, monkeypatch):
    async def _save(**kwargs):
        return None

    monkeypatch.setattr(agent_tools_mod, "save_memory", _save)
    r = client.post("/api/agent/tools/memory_remember",
                    json={"character_id": 42, "text": "AI 台词原文"})
    assert r.status_code == 200
    assert r.json()["applied"] is False


def test_memory_tools_in_registration_spec(client):
    spec = client.get("/api/agent/tools/openapi.json").json()
    assert "/api/agent/tools/memory_recall" in spec["paths"]
    assert "/api/agent/tools/memory_remember" in spec["paths"]
    schemas = spec["components"]["schemas"]
    assert {"RecallRequest", "RecallResponse", "RememberRequest",
            "RememberResponse"} <= set(schemas)
    for path in ("/api/agent/tools/memory_recall", "/api/agent/tools/memory_remember"):
        op = spec["paths"][path]["post"]
        assert op["operationId"].startswith("agentTools")
        assert op["security"] == [{"BearerAuth": []}]


def test_memory_remember_rejects_source_field(client):
    r = client.post("/api/agent/tools/memory_remember",
                    json={"character_id": 42, "text": "x", "source": "forged"})
    assert r.status_code == 422


def test_memory_roundtrip_real_db(client):
    """真实写库 → recall 命中 → 删除（测试库事务性，不碰生产库）。"""
    import asyncio

    from app.memory.maintain import delete_memory
    from app.memory.retrieve import search_memories
    from app.memory.write import save_memory

    marker = "M3roundtrip-9f2c1a"

    async def _seed():
        from app.db import database
        from app.models.user import User
        from app.models.character import AICharacter
        async with database.async_session_factory() as db:
            if await db.get(User, 3) is None:
                db.add(User(id=3, username="m3user", nickname="M3"))
                await db.flush()
            if await db.get(AICharacter, 13) is None:
                db.add(AICharacter(id=13, user_id=3, name="M3C"))
            await db.commit()

    asyncio.run(_seed())

    async def _save():
        obj = await save_memory(user_id=3, character_id=13, memory_type="daily",
                                content=f"{marker} 用户今天想喝热可可")
        return getattr(obj, "id", obj)

    mid = asyncio.run(_save())
    print("[M3] saved memory id =", mid)
    assert mid, "save_memory 应写入成功"

    r = client.post("/api/agent/tools/memory_recall",
                    json={"character_id": 13, "query": marker, "k": 5})
    ids = [i["id"] for i in r.json()["items"]]
    assert mid in ids, f"recall 未命中 {mid}: {ids}"

    assert asyncio.run(delete_memory(mid)) is True
    r2 = client.post("/api/agent/tools/memory_recall",
                     json={"character_id": 13, "query": marker, "k": 5})
    assert mid not in [i["id"] for i in r2.json()["items"]]
    print("[M3] recall hit -> delete ok -> recall miss")
