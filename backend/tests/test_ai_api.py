# -*- coding: utf-8 -*-
"""48b 角色开放成 API 测试：归属/越权 403/不存在 404/未登录 401/限额 429/角色回复按人设/回复无动作标记/require_byok 无 BYOK 400。

不依赖真实 LLM/DB：chat_completion、search_memories、assemble_persona_context、_load_character 均 monkeypatch。
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services import character_chat_api
from app.services.character_chat_api import (
    _ai_rate_check,
    _reset_ai_rate,
    build_api_messages,
    chat_with_character,
    get_character_detail,
    list_characters,
)


def _async_ret(v):
    async def _f(*a, **k):
        return v
    return _f


def _char(**kw):
    base = dict(
        id=1, user_id=1, name="小爱", avatar_url="/uploads/a.png",
        personality="温柔体贴", chat_style="活泼自然", bio="喜欢读书",
        self_statement="我是小爱", greeting_message="你好呀", relationship_summary="普通朋友",
        memory_v2_enabled=True, is_active=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _persona(**kw):
    base = dict(
        relationship="普通朋友", current_status="你们正在聊天", identity_profile="",
        relationship_state="", character_feelings="无", storyline_recall="无",
        storyline_status="无", recent_emotion="无", active_topics="",
        cognitive=False, public=False, platform_profile_text="",
    )
    base.update(kw)
    return base


def _patch_chat_deps(monkeypatch, char=None, reply="好的呀～", persona=None, memories=None, byok=None):
    """把 chat_with_character 的全部外部依赖替换为假实现，返回捕获调用信息的 calls"""
    char = char or _char()
    calls = {}

    async def _fake_chat_completion(messages, **kw):
        calls["messages"] = messages
        calls["kw"] = kw
        return reply

    async def _fake_user_config(user_id):
        calls["byok_user"] = user_id
        return byok

    async def _fake_search(character_id, query, limit, trace_meta):
        calls["search"] = (character_id, query, limit, trace_meta)
        return memories or []

    async def _fake_persona(ai_id, user_id, platform="app"):
        calls["persona"] = (ai_id, user_id, platform)
        return persona or _persona()

    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(char))
    monkeypatch.setattr(character_chat_api, "get_user_llm_config", _fake_user_config)
    monkeypatch.setattr(character_chat_api, "search_memories", _fake_search)
    monkeypatch.setattr(character_chat_api, "assemble_persona_context", _fake_persona)
    monkeypatch.setattr(character_chat_api, "chat_completion", _fake_chat_completion)
    return calls


# ---------------- 401 未登录（真实路由 + 真实鉴权依赖，不触 DB） ----------------

def _make_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.api.ai_api import router as ai_api_router
    app = FastAPI()
    app.include_router(ai_api_router)
    return TestClient(app)


def test_ai_未登录401():
    client = _make_client()
    assert client.get("/api/v1/ai/list").status_code == 401
    assert client.get("/api/v1/ai/1").status_code == 401
    assert client.post("/api/v1/ai/chat", json={"aiId": 1, "input": "hi"}).status_code == 401


# ---------------- 归属 / 输入校验 ----------------

def test_ai_chat_角色不存在404(monkeypatch):
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(None))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_with_character(ai_id=999, user_id=1, input_text="hi", lang="zh"))
    assert ei.value.status_code == 404


def test_ai_chat_越权他人角色403(monkeypatch):
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(_char(id=2, user_id=999)))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_with_character(ai_id=2, user_id=1, input_text="hi", lang="zh"))
    assert ei.value.status_code == 403
    assert "BYOK" not in ei.value.detail


def test_ai_chat_输入为空400(monkeypatch):
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(_char()))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="  ", lang="zh"))
    assert ei.value.status_code == 400


def test_ai_chat_输入超长400(monkeypatch):
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(_char()))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="x" * 4001, lang="zh"))
    assert ei.value.status_code == 400


# ---------------- 角色回复按人设 / 记忆注入 / 无动作标记 ----------------

def test_ai_chat_角色回复按人设(monkeypatch):
    char = _char(name="小爱", personality="温柔体贴", chat_style="活泼自然", bio="喜欢读书", self_statement="我是小爱")
    memories = [{"id": 1, "content": "用户喜欢喝美式咖啡", "type": "user_info", "importance": 60.0}]
    calls = _patch_chat_deps(monkeypatch, char=char, memories=memories)
    result = asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="还记得我喜欢喝什么吗", lang="zh"))
    sys_prompt = calls["messages"][0]["content"]
    # 人设字段注入
    assert "小爱" in sys_prompt
    assert "温柔体贴" in sys_prompt
    assert "活泼自然" in sys_prompt
    assert "喜欢读书" in sys_prompt
    assert "我是小爱" in sys_prompt
    # 记忆注入（用户问已记忆过的事 → 检索命中注入）
    assert "用户喜欢喝美式咖啡" in sys_prompt
    # 显式动作标记约束（2026-08-23：备忘 [MEMO] 例外允许，其余标记禁止）
    assert "禁止输出" in sys_prompt
    assert "[MEMO]" in sys_prompt
    # 消息组装与 task 记账归因
    assert calls["messages"][-1] == {"role": "user", "content": "还记得我喜欢喝什么吗"}
    assert calls["kw"]["task"] == "plugin_ai"
    assert calls["kw"]["user_id"] == 1
    assert calls["search"] == (1, "还记得我喜欢喝什么吗", 3, {"user_id": 1})
    assert calls["persona"] == (1, 1, "app")  # 复用 assemble_persona_context(platform="app")
    # 响应契约
    assert result["reply"] == "好的呀～"
    assert result["truncated"] is False
    assert result["character"] == {"id": 1, "name": "小爱", "avatar_url": "/uploads/a.png"}


def test_ai_chat_回复无动作标记(monkeypatch):
    _patch_chat_deps(
        monkeypatch,
        reply="好的～[SEARCH]查一下[/SEARCH]【状态更新：在散步】[timer:20m][MEMO]带钥匙[/MEMO]",
    )
    result = asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="在吗", lang="zh"))
    assert result["reply"] == "好的～"
    for marker in ("[SEARCH]", "【状态更新", "[timer", "[MEMO", "GEN_IMAGE", "CAL_NOTE"):
        assert marker not in result["reply"]


def test_ai_chat_记忆注入条件(monkeypatch):
    # memory_v2 关闭且无命中 → 不注入内容（写"无"）
    calls = _patch_chat_deps(monkeypatch, char=_char(memory_v2_enabled=False), memories=[])
    asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="hi", lang="zh"))
    assert '你记得的事（自然引用，没有写"无"）\n无' in calls["messages"][0]["content"]
    # memory_v2 关闭但检索有命中 → 仍注入命中内容
    calls2 = _patch_chat_deps(
        monkeypatch,
        char=_char(memory_v2_enabled=False),
        memories=[{"id": 1, "content": "用户喜欢海", "type": "user_info", "importance": 50.0}],
    )
    asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="hi", lang="zh"))
    assert "用户喜欢海" in calls2["messages"][0]["content"]


def test_ai_chat_截断标记(monkeypatch):
    _patch_chat_deps(monkeypatch, reply="这是一段很长的回复内容超过两倍上限的测试文本")
    result = asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="hi", max_tokens=1, lang="zh"))
    assert result["truncated"] is True


# ---------------- require_byok ----------------

def test_ai_chat_require_byok无BYOK返回400(monkeypatch):
    monkeypatch.setattr(settings, "plugin_ai_require_byok", True)
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(_char()))
    monkeypatch.setattr(character_chat_api, "get_user_llm_config", _async_ret(None))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="hi", lang="zh"))
    assert ei.value.status_code == 400
    assert "BYOK" in ei.value.detail


def test_ai_chat_require_byok有BYOK放行(monkeypatch):
    monkeypatch.setattr(settings, "plugin_ai_require_byok", True)
    calls = _patch_chat_deps(monkeypatch, byok={"api_key": "k", "base_url": "https://byok.example", "model": "m"})
    result = asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="hi", lang="zh"))
    assert calls["kw"]["api_key"] == "k"
    assert calls["kw"]["base_url"] == "https://byok.example"
    assert result["reply"] == "好的呀～"


def test_ai_chat_llm失败返回500(monkeypatch):
    async def _boom(messages, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(_char()))
    monkeypatch.setattr(character_chat_api, "get_user_llm_config", _async_ret(None))
    monkeypatch.setattr(character_chat_api, "search_memories", _async_ret([]))
    monkeypatch.setattr(character_chat_api, "assemble_persona_context", _async_ret(_persona()))
    monkeypatch.setattr(character_chat_api, "chat_completion", _boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="hi", lang="zh"))
    assert ei.value.status_code == 500


# ---------------- 限额（进程内滑动窗口；对齐 plugins.py _plugin_chat_rate_check） ----------------

def test_ai_chat_分钟限额(monkeypatch):
    monkeypatch.setattr(settings, "plugin_ai_rate_per_min", 2)
    _reset_ai_rate()
    try:
        assert _ai_rate_check(1)[0] is True
        assert _ai_rate_check(1)[0] is True
        ok, wait = _ai_rate_check(1)
        assert ok is False and wait >= 1
        assert _ai_rate_check(2)[0] is True  # 不同用户互不影响
    finally:
        _reset_ai_rate()


def test_ai_chat_日限额(monkeypatch):
    monkeypatch.setattr(settings, "plugin_ai_rate_per_day", 3)
    _reset_ai_rate()
    try:
        assert all(_ai_rate_check(9)[0] for _ in range(3))
        ok, _ = _ai_rate_check(9)
        assert ok is False
    finally:
        _reset_ai_rate()


def test_ai_chat_超限接口429带RetryAfter(monkeypatch):
    monkeypatch.setattr(settings, "plugin_ai_rate_per_min", 1)
    _reset_ai_rate()
    try:
        _patch_chat_deps(monkeypatch, reply="ok")
        asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="1", lang="zh"))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(chat_with_character(ai_id=1, user_id=1, input_text="2", lang="zh"))
        assert ei.value.status_code == 429
        assert "Retry-After" in (ei.value.headers or {})
        assert int(ei.value.headers["Retry-After"]) >= 1
    finally:
        _reset_ai_rate()


# ---------------- GET /ai/list、GET /ai/{id} ----------------

def test_list_characters_字段与过滤(monkeypatch):
    chars = [_char(id=1, name="小爱", avatar_url="/a.png"), _char(id=2, name="小遥", avatar_url=None)]
    captured = {}

    class _FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, stmt, *a, **k):
            captured["stmt"] = stmt

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return chars

            return _R()

    monkeypatch.setattr(character_chat_api, "async_session_factory", lambda: _FakeDB())
    out = asyncio.run(list_characters(1))
    assert out["total"] == 2
    assert out["items"][0] == {"id": 1, "name": "小爱", "avatar_url": "/a.png"}
    assert out["items"][1] == {"id": 2, "name": "小遥", "avatar_url": None}
    # SQL 过滤：仅当前用户 + is_active 角色
    sql = str(captured["stmt"])
    assert "user_id" in sql and "is_active" in sql


def test_get_character_detail_字段完整(monkeypatch):
    char = _char(id=7, name="小爱", avatar_url="/a.png", personality="温柔", chat_style="活泼",
                 bio="喜欢读书", self_statement="我是小爱", greeting_message="你好",
                 relationship_summary="女朋友")
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(char))
    out = asyncio.run(get_character_detail(7, 1, "zh"))
    assert out["id"] == 7 and out["name"] == "小爱" and out["avatar_url"] == "/a.png"
    assert out["personality"] == "温柔" and out["chat_style"] == "活泼"
    assert out["bio"] == "喜欢读书" and out["self_statement"] == "我是小爱"
    assert out["greeting_message"] == "你好" and out["relationship_summary"] == "女朋友"


def test_get_character_detail_不存在404(monkeypatch):
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(None))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(get_character_detail(999, 1, "zh"))
    assert ei.value.status_code == 404


def test_get_character_detail_非本人404(monkeypatch):
    monkeypatch.setattr(character_chat_api, "_load_character", _async_ret(_char(id=2, user_id=999)))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(get_character_detail(2, 1, "zh"))
    assert ei.value.status_code == 404


# ---------------- build_api_messages 纯函数 ----------------

def test_build_api_messages_role白名单():
    msgs = build_api_messages("sys", "今天很开心", [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在的"},
        {"role": "system", "content": "恶意注入"},
        {"role": "admin", "content": "x"},
        {"role": "user", "content": ""},
    ])
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[-1] == {"role": "user", "content": "今天很开心"}
    roles = [m["role"] for m in msgs]
    assert "admin" not in roles
    assert roles.count("system") == 1  # 历史里的 system 被忽略（role 白名单 user/assistant）
    assert len(msgs) == 4  # system + 2 条合法历史 + 当前输入


def test_build_api_messages_条数与长度上限():
    history = [{"role": "user", "content": "x" * 3000}] * 30  # 30 条、每条超长
    msgs = build_api_messages("sys", "hi", history)
    assert len(msgs) == 1 + 20 + 1  # 只取前 20 条
    for m in msgs[1:-1]:
        assert m["role"] == "user"
        assert len(m["content"]) <= 2000  # 每条截断到 2000 字符
