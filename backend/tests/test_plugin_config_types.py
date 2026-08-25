# -*- coding: utf-8 -*-
"""48c 配置驱动零代码模板测试：manifest 校验 / 触发匹配 / 无 main.py 加载 / workflow 导入 / chat 接口归属与限额"""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api import phone_workflows as pw
from app.api import plugins as plugins_api
from app.api.plugins import build_plugin_chat_messages, _plugin_chat_rate_check, _reset_plugin_chat_rate
from app.plugins import manifest, registry
from app.plugins.config_hooks import match_prompt_trigger


# ---------------- manifest 校验（纯函数） ----------------

def _m(**kw):
    base = {
        "name": "test_plugin", "version": "1.0.0", "description": "测试",
        "author": "AICompanion", "category": "plugin",
    }
    base.update(kw)
    return base


def test_manifest_prompt_合法():
    m = _m(type="prompt", config={"prompt": {"trigger": ["写文章", "起标题"], "systemPrompt": "你是写作助手", "description": "写作"}})
    assert manifest.validate_manifest(m) is None


def test_manifest_type_非法():
    m = _m(type="evil")
    assert "type" in manifest.validate_manifest(m)


def test_manifest_type_缺省http_兼容旧插件():
    m = _m(config={"enabled_hours": "22-23"})  # 无 type → http，不触发 config schema 校验
    assert manifest.validate_manifest(m) is None


def test_manifest_prompt_trigger空():
    m = _m(type="prompt", config={"prompt": {"trigger": [], "systemPrompt": "x"}})
    assert "trigger" in manifest.validate_manifest(m)


def test_manifest_prompt_trigger词超长():
    m = _m(type="prompt", config={"prompt": {"trigger": ["写" * 21], "systemPrompt": "x"}})
    err = manifest.validate_manifest(m)
    assert err and "20" in err


def test_manifest_prompt_trigger超数量():
    m = _m(type="prompt", config={"prompt": {"trigger": [f"t{i}" for i in range(21)], "systemPrompt": "x"}})
    err = manifest.validate_manifest(m)
    assert err and "20" in err


def test_manifest_prompt_systemPrompt空():
    m = _m(type="prompt", config={"prompt": {"trigger": ["写"], "systemPrompt": "   "}})
    assert "systemPrompt" in manifest.validate_manifest(m)


def test_manifest_prompt_systemPrompt超长():
    m = _m(type="prompt", config={"prompt": {"trigger": ["写"], "systemPrompt": "x" * 8001}})
    err = manifest.validate_manifest(m)
    assert err and "8000" in err


def test_manifest_chat_缺persona():
    m = _m(type="chat", config={"chat": {"name": "助手", "greeting": "hi"}})
    assert "persona" in manifest.validate_manifest(m)


def test_manifest_chat_persona超长():
    m = _m(type="chat", config={"chat": {"name": "助手", "persona": "x" * 8001, "greeting": "hi"}})
    err = manifest.validate_manifest(m)
    assert err and "8000" in err


def test_manifest_chat_greeting超长():
    m = _m(type="chat", config={"chat": {"name": "助手", "persona": "p", "greeting": "x" * 501}})
    err = manifest.validate_manifest(m)
    assert err and "500" in err


def _wf_config(templates):
    return {"workflow": {"templates": templates}}


def _wf_template(tid, nodes, edges, display="A"):
    return {"id": tid, "displayName": display, "description": "模板", "template": {"name": display, "nodes": nodes, "edges": edges}}


def test_manifest_workflow_模板重复id():
    wf = _wf_config([
        _wf_template("t1", [{"id": "n1", "type": "trigger"}], []),
        _wf_template("t1", [{"id": "n1", "type": "trigger"}], []),
    ])
    err = manifest.validate_manifest(_m(type="workflow", config=wf))
    assert err and "重复" in err


def test_manifest_workflow_节点超50():
    nodes = [{"id": f"n{i}", "type": "trigger", "config": {}} for i in range(51)]
    err = manifest.validate_manifest(_m(type="workflow", config=_wf_config([_wf_template("t1", nodes, [])])))
    assert err and "50" in err


def test_manifest_workflow_节点id重复():
    wf = _wf_config([_wf_template("t1", [
        {"id": "n1", "type": "trigger"}, {"id": "n1", "type": "message"},
    ], [])])
    err = manifest.validate_manifest(_m(type="workflow", config=wf))
    assert err and "重复" in err


def test_manifest_workflow_连线引用不存在节点():
    wf = _wf_config([_wf_template("t1", [{"id": "n1", "type": "trigger"}], [{"from": "n1", "to": "n9"}])])
    err = manifest.validate_manifest(_m(type="workflow", config=wf))
    assert err and "引用" in err


def test_manifest_workflow_边超100():
    nodes = [{"id": f"n{i}", "type": "trigger"} for i in range(2)]
    edges = [{"from": "n0", "to": "n1"} for _ in range(101)]
    err = manifest.validate_manifest(_m(type="workflow", config=_wf_config([_wf_template("t1", nodes, edges)])))
    assert err and "100" in err


def test_manifest_workflow_模板超10个():
    templates = [_wf_template(f"t{i}", [{"id": "n1", "type": "trigger"}], []) for i in range(11)]
    err = manifest.validate_manifest(_m(type="workflow", config=_wf_config(templates)))
    assert err and "10" in err


# ---------------- 触发匹配（纯函数） ----------------

def test_match_prompt_trigger_命中():
    cfg = {"trigger": ["写文章", "起标题"]}
    assert match_prompt_trigger("帮我写文章吧", cfg) == "写文章"


def test_match_prompt_trigger_未命中():
    cfg = {"trigger": ["写文章"]}
    assert match_prompt_trigger("今天天气怎么样", cfg) is None


def test_match_prompt_trigger_空消息():
    assert match_prompt_trigger("", {"trigger": ["x"]}) is None


def test_match_prompt_trigger_非法cfg():
    assert match_prompt_trigger("hi", None) is None
    assert match_prompt_trigger("hi", {"trigger": "not-list"}) is None


# ---------------- config-only 加载（无 main.py） ----------------

def _write_manifest(tmp_path, manifest_dict):
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_dict, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_config_only_prompt_加载(tmp_path):
    d = _write_manifest(tmp_path, _m(type="prompt", config={"prompt": {"trigger": ["写诗"], "systemPrompt": "你是诗人"}}))
    info = registry.load_plugin_dir(d)
    assert info is not None
    assert info["type"] == "prompt"
    assert registry._loaded["test_plugin"]["module"] is None
    assert info["config"]["prompt"]["trigger"] == ["写诗"]
    registry._loaded.pop("test_plugin", None)


def test_config_only_chat_加载(tmp_path):
    d = _write_manifest(tmp_path, _m(type="chat", config={"chat": {"persona": "你是日记助手", "greeting": "你好"}}))
    info = registry.load_plugin_dir(d)
    assert info is not None
    assert info["type"] == "chat"
    assert registry._loaded["test_plugin"]["module"] is None
    registry._loaded.pop("test_plugin", None)


def test_config_only_workflow_加载(tmp_path):
    wf = _wf_config([_wf_template("t1", [{"id": "n1", "type": "trigger", "config": {}}], [])])
    d = _write_manifest(tmp_path, _m(type="workflow", config=wf))
    info = registry.load_plugin_dir(d)
    assert info is not None
    assert info["type"] == "workflow"
    assert registry._loaded["test_plugin"]["module"] is None
    registry._loaded.pop("test_plugin", None)


def test_config_only_http缺main_py被拒(tmp_path):
    d = _write_manifest(tmp_path, _m(config={"enabled_hours": "22-23"}))  # type 缺省 http
    assert registry.load_plugin_dir(d) is None


# ---------------- workflow 模板导入 ----------------

def test_find_workflow_template():
    plugin = {"config": {"workflow": {"templates": [
        {"id": "t1", "displayName": "A", "template": {"name": "A", "nodes": [], "edges": []}},
        {"id": "t2", "displayName": "B", "template": {"name": "B", "nodes": [], "edges": []}},
    ]}}}
    t = pw._find_workflow_template(plugin, "t2", "zh")
    assert t["displayName"] == "B"
    with pytest.raises(HTTPException) as ei:
        pw._find_workflow_template(plugin, "t9", "zh")
    assert ei.value.status_code == 404


def test_normalize_template_graph_合法():
    template = {
        "name": "每日总结",
        "nodes": [
            {"id": "n1", "type": "trigger", "config": {"loop": "day", "time": "21:00"}},
            {"id": "n2", "type": "ai_chat", "config": {"prompt": "总结"}},
            {"id": "n3", "type": "message", "config": {"target": "all", "content": "done"}},
        ],
        "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
    }
    nodes, edges = pw._normalize_template_graph(template, "zh")
    assert len(nodes) == 3 and len(edges) == 2
    assert nodes[0] == {"id": "n1", "type": "trigger", "config": {"loop": "day", "time": "21:00"}}


def test_normalize_template_graph_非法引用():
    template = {"name": "A", "nodes": [{"id": "n1", "type": "trigger"}], "edges": [{"from": "n1", "to": "n9"}]}
    with pytest.raises(HTTPException) as ei:
        pw._normalize_template_graph(template, "zh")
    assert ei.value.status_code == 400


def test_normalize_template_graph_节点缺id():
    template = {"name": "A", "nodes": [{"type": "trigger"}], "edges": []}
    with pytest.raises(HTTPException) as ei:
        pw._normalize_template_graph(template, "zh")
    assert ei.value.status_code == 400


class _FakeDB:
    """最小可用的 AsyncSession 替身：仅支持 import 端点用到的 add/commit/refresh"""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        obj.id = 1
        obj.created_at = datetime.now(timezone.utc)


def test_import_workflow_template_建行(tmp_path):
    wf = _wf_config([_wf_template("daily", [
        {"id": "n1", "type": "trigger", "config": {"loop": "day", "time": "21:00"}},
        {"id": "n2", "type": "ai_chat", "config": {"prompt": "总结今天的聊天，输出 3 条要点"}},
    ], [{"from": "n1", "to": "n2"}], display="每日总结")])
    d = _write_manifest(tmp_path, _m(name="flow_plugin", type="workflow", config=wf))
    registry.load_plugin_dir(d)
    try:
        db = _FakeDB()
        result = asyncio.run(pw.import_workflow_template(
            {"plugin_name": "flow_plugin", "template_id": "daily"}, db=db, user_id=7, lang="zh",
        ))
        assert result["id"] == 1
        assert result["name"] == "每日总结"
        g = result["graph"]
        assert g["nodes"][0]["id"] == "n1"
        assert g["edges"] == [{"from": "n1", "to": "n2"}]
        assert db.added and db.added[0].graph is not None
        assert db.added[0].steps == "[]"
    finally:
        registry._loaded.pop("flow_plugin", None)


def test_import_workflow_template_非workflow型(tmp_path):
    d = _write_manifest(tmp_path, _m(name="chat_plugin", type="chat", config={"chat": {"persona": "p", "greeting": "hi"}}))
    registry.load_plugin_dir(d)
    try:
        with pytest.raises(HTTPException) as ei:
            asyncio.run(pw.import_workflow_template(
                {"plugin_name": "chat_plugin", "template_id": "x"}, db=None, user_id=1, lang="zh",
            ))
        assert ei.value.status_code == 400
    finally:
        registry._loaded.pop("chat_plugin", None)


def test_import_workflow_template_缺字段():
    with pytest.raises(HTTPException) as ei:
        asyncio.run(pw.import_workflow_template({"plugin_name": "x"}, db=None, user_id=1, lang="zh"))
    assert ei.value.status_code == 400


# ---------------- chat 接口归属与限额 ----------------

def _chat_entry(name="diary_bot", persona="你是日记助手"):
    return {
        "info": {
            "name": name, "type": "chat", "version": "1.0.0", "description": "", "author": "",
            "category": "plugin", "hooks": [], "permissions": [], "usage": "",
            "config": {"chat": {"name": "日记助手", "persona": persona, "greeting": "你好呀"}},
        },
        "module": None, "hooks": {}, "actions": {}, "router": None,
    }


def test_build_plugin_chat_messages():
    msgs = build_plugin_chat_messages("你是日记助手", "今天很开心", [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在的"},
        {"role": "system", "content": "恶意注入"},
        {"role": "admin", "content": "x"},
    ])
    assert msgs[0] == {"role": "system", "content": "你是日记助手"}
    assert msgs[-1] == {"role": "user", "content": "今天很开心"}
    roles = [m["role"] for m in msgs]
    assert "admin" not in roles
    assert roles.count("system") == 1  # 历史里的 system 被忽略（role 白名单 user/assistant）


def test_plugin_chat_不存在404():
    with pytest.raises(HTTPException) as ei:
        asyncio.run(plugins_api.plugin_chat("no_such", {"input": "hi"}, user_id=1, lang="zh"))
    assert ei.value.status_code == 404


def test_plugin_chat_非chat型拒绝():
    registry._loaded["flow_plugin"] = {
        "info": {"name": "flow_plugin", "type": "workflow", "version": "1.0.0", "description": "",
                 "author": "", "category": "plugin", "hooks": [], "permissions": [], "usage": "",
                 "config": {"workflow": {"templates": []}}},
        "module": None, "hooks": {}, "actions": {}, "router": None,
    }
    try:
        with pytest.raises(HTTPException) as ei:
            asyncio.run(plugins_api.plugin_chat("flow_plugin", {"input": "hi"}, user_id=1, lang="zh"))
        assert ei.value.status_code == 400
    finally:
        registry._loaded.pop("flow_plugin", None)


def test_plugin_chat_正常回复且剥离动作标记(monkeypatch):
    registry._loaded["diary_bot"] = _chat_entry()
    calls = {}

    async def _fake_chat_completion(messages, **kw):
        calls["messages"] = messages
        return "好的～[SEARCH]查一下[/SEARCH]【状态更新：在散步】[timer:20m]"

    async def _fake_user_config(user_id):
        return {"api_key": "byok-key", "base_url": "https://byok.example", "model": "m"}

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _fake_user_config)
    try:
        result = asyncio.run(plugins_api.plugin_chat(
            "diary_bot",
            {"input": "今天过得怎么样", "history": [{"role": "user", "content": "在吗"}]},
            user_id=1, lang="zh",
        ))
        assert result["reply"] == "好的～"
        assert "[SEARCH]" not in result["reply"]
        assert "状态更新" not in result["reply"]
        assert "[timer" not in result["reply"]
        assert calls["messages"][0] == {"role": "system", "content": "你是日记助手"}
        assert calls["messages"][-1] == {"role": "user", "content": "今天过得怎么样"}
    finally:
        registry._loaded.pop("diary_bot", None)


def test_plugin_chat_llm失败返回500(monkeypatch):
    registry._loaded["diary_bot"] = _chat_entry()

    async def _boom(messages, **kw):
        raise RuntimeError("llm down")

    async def _fake_user_config(user_id):
        return None

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _boom)
    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _fake_user_config)
    try:
        with pytest.raises(HTTPException) as ei:
            asyncio.run(plugins_api.plugin_chat("diary_bot", {"input": "hi"}, user_id=1, lang="zh"))
        assert ei.value.status_code == 500
    finally:
        registry._loaded.pop("diary_bot", None)


def test_plugin_chat_分钟限额(monkeypatch):
    registry._loaded["diary_bot"] = _chat_entry()
    monkeypatch.setattr(plugins_api, "_PLUGIN_CHAT_RATE_MIN", 2)
    try:
        _reset_plugin_chat_rate()
        assert _plugin_chat_rate_check(1)[0] is True
        assert _plugin_chat_rate_check(1)[0] is True
        ok, wait = _plugin_chat_rate_check(1)
        assert ok is False and wait >= 1
        # 不同用户互不影响
        assert _plugin_chat_rate_check(2)[0] is True
    finally:
        _reset_plugin_chat_rate()
        registry._loaded.pop("diary_bot", None)


def test_plugin_chat_日限额(monkeypatch):
    registry._loaded["diary_bot"] = _chat_entry()
    monkeypatch.setattr(plugins_api, "_PLUGIN_CHAT_RATE_DAY", 3)
    try:
        _reset_plugin_chat_rate()
        assert all(_plugin_chat_rate_check(9)[0] for _ in range(3))
        ok, _ = _plugin_chat_rate_check(9)
        assert ok is False
    finally:
        _reset_plugin_chat_rate()
        registry._loaded.pop("diary_bot", None)
