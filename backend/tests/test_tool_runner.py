# -*- coding: utf-8 -*-
"""Phase C 测试：插件 action 自动登记 + 统一执行入口（权限三档 + 工具生命周期钩子）"""
import asyncio

from app.agent import tools
from app.agent import tool_runner
from app.plugins import registry


def test_内置工具带scope():
    assert tools.get_tool("search").scope == "browser"
    assert tools.get_tool("image_gen").scope == "image_gen"
    assert tools.get_tool("note_calendar").scope is None  # 本地能力无权限门禁
    assert tools.get_tool("timer").scope is None


def test_插件action自动登记(monkeypatch):
    # 加载 douyin_mcp 插件（含 handle_mention / handle_social_event actions）
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp")
    count = tools.sync_plugin_tools()
    assert count >= 2
    t = tools.get_tool("douyin_mcp.handle_social_event")
    assert t is not None
    assert t.plugin == "douyin_mcp"
    assert t.plugin_action == "handle_social_event"
    assert t.scope == "douyin"  # 插件映射权限 scope
    assert t.risk_level == "high"
    t2 = tools.get_tool("douyin_mcp.handle_mention")
    assert t2 is not None and t2.plugin_action == "handle_mention"


def test_execute_tool_allow执行(monkeypatch):
    calls = []

    def fake_execute(payload):
        calls.append(payload)
        return {"ok": True, "echo": payload}

    spec = tools.ToolSpec(name="fake_tool", description="测试", execute=fake_execute)
    out = asyncio.run(tool_runner.execute_tool(spec, {"x": 1}, user_id=1))
    assert out["status"] == "ok"
    assert out["result"]["echo"] == {"x": 1}
    assert calls == [{"x": 1}]
    assert out["latency_ms"] >= 0


def test_execute_tool_异步execute():
    async def fake_execute(payload):
        return {"ok": True}

    spec = tools.ToolSpec(name="fake_async", description="测试", execute=fake_execute)
    out = asyncio.run(tool_runner.execute_tool(spec, {}))
    assert out["status"] == "ok"


def test_execute_tool_禁用不执行(monkeypatch):
    def fake_execute(payload):
        raise AssertionError("不应执行")

    spec = tools.ToolSpec(name="fake_disabled", description="测试", execute=fake_execute, enabled=False)
    out = asyncio.run(tool_runner.execute_tool(spec, {}))
    assert out["status"] == "blocked"
    assert out["error"] == "tool disabled"


def test_execute_tool_forbid拦截(monkeypatch):
    async def _forbid(spec, uid):
        return "forbid"

    monkeypatch.setattr(tool_runner, "check_tool_permission", _forbid)

    def fake_execute(payload):
        raise AssertionError("forbid 不应执行")

    spec = tools.ToolSpec(name="fake_forbid", description="测试", execute=fake_execute)
    out = asyncio.run(tool_runner.execute_tool(spec, {}, user_id=1))
    assert out["status"] == "blocked"
    assert out["error"] == "forbid"


def test_execute_tool_ask挂起(monkeypatch):
    async def _ask(spec, uid):
        return "ask"

    monkeypatch.setattr(tool_runner, "check_tool_permission", _ask)

    class FakeRow:
        id = 777

    async def _create_pending(user_id, session_id, character_id, scope, action):
        return FakeRow()

    import app.application.permission_service as ps
    monkeypatch.setattr(ps, "create_pending_action", _create_pending)

    spec = tools.ToolSpec(name="fake_ask", description="测试", scope="image_gen")
    out = asyncio.run(tool_runner.execute_tool(
        spec, {"prompt": "图"}, user_id=1, character_id=2, session_id=3,
    ))
    assert out["status"] == "pending"
    assert out["action_id"] == 777


def test_execute_tool_ask_auto_allow放行(monkeypatch):
    async def _ask(spec, uid):
        return "ask"

    monkeypatch.setattr(tool_runner, "check_tool_permission", _ask)

    def fake_execute(payload):
        return {"ok": True}

    # 只读低风险工具（如 search）：ask 不挂起直接放行
    spec = tools.ToolSpec(name="search", description="测试", scope="browser", ask_auto_allow=True, execute=fake_execute)
    out = asyncio.run(tool_runner.execute_tool(spec, {}, user_id=1))
    assert out["status"] == "ok"


def test_execute_tool_ask缺会话上下文():
    async def _ask(spec, uid):
        return "ask"

    spec = tools.ToolSpec(name="fake_ask2", description="测试", scope="image_gen")
    orig = tool_runner.check_tool_permission
    tool_runner.check_tool_permission = _ask
    try:
        out = asyncio.run(tool_runner.execute_tool(spec, {}, user_id=1))
    finally:
        tool_runner.check_tool_permission = orig
    assert out["status"] == "blocked"


def test_execute_tool_插件action走run_plugin_action(monkeypatch):
    async def _fake_run(plugin, action, payload, user_id=None):
        return True

    monkeypatch.setattr(registry, "run_plugin_action", _fake_run)
    spec = tools.ToolSpec(
        name="douyin_mcp.handle_mention", description="测试", plugin="douyin_mcp",
        plugin_action="handle_mention", scope="douyin",
    )
    out = asyncio.run(tool_runner.execute_tool(
        spec, {"social_event": {}}, user_id=1,
    ))
    assert out["status"] == "ok"
    assert out["result"]["ok"] is True


def test_execute_tool_幂等失败重试一次(monkeypatch):
    calls = []

    def fake_execute(payload):
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("第一次失败")
        return {"ok": True}

    spec = tools.ToolSpec(name="fake_retry", description="测试", execute=fake_execute, idempotent=True)
    out = asyncio.run(tool_runner.execute_tool(spec, {}))
    assert out["status"] == "ok"
    assert len(calls) == 2  # 失败自动重试 1 次


def test_execute_tool_错误隔离(monkeypatch):
    def fake_execute(payload):
        raise RuntimeError("boom")

    spec = tools.ToolSpec(name="fake_err", description="测试", execute=fake_execute)
    out = asyncio.run(tool_runner.execute_tool(spec, {}))
    assert out["status"] == "error"
    assert "boom" in out["error"]


def test_工具生命周期钩子顺序(monkeypatch):
    events = []
    hooks = {stage: [] for stage in tool_runner.TOOL_HOOK_STAGES}

    def _make(stage):
        async def _h(ctx):
            events.append(stage)
        return _h

    for stage in tool_runner.TOOL_HOOK_STAGES:
        hooks[stage] = [_make(stage)]

    monkeypatch.setattr(registry, "_loaded", {
        "_lifecycle_test": {"info": {"name": "_lifecycle_test"}, "hooks": hooks, "actions": {}, "router": None},
    })
    monkeypatch.setattr(registry, "_enabled", {"_lifecycle_test": True})

    spec = tools.ToolSpec(name="fake_life", description="测试", execute=lambda p: {"ok": True})
    out = asyncio.run(tool_runner.execute_tool(spec, {}))
    assert out["status"] == "ok"
    # 生命周期顺序：requested → permission_checked → started → result → finished
    assert events == [
        "tool_call_requested",
        "tool_permission_checked",
        "tool_execution_started",
        "tool_result",
        "tool_finished",
    ]


def test_内置工具_execute_tool_搜索生图observation(monkeypatch):
    """AMBRACE 步骤 8：search / image_gen 经 tool_runner.execute_tool 执行成功且 observation 注入。

    user_id=None 时权限放行（scope 三档由 check_tool_permission 负责）；底层服务函数 monkeypatch，
    验证内置工具的执行入口（execute）真实接通并产出 observation。
    """
    from app.application.chat import tools as chat_tools

    async def _fake_search(query, timeout=20.0):
        return "- 搜索结果A：这是摘要内容"

    async def _fake_gen_flow(uid, cid, sid, prompt, img_text=None):
        seen["gen"] = (uid, cid, sid, prompt, img_text)

    seen = {"gen": None}
    monkeypatch.setattr(chat_tools, "_run_web_search", _fake_search)
    monkeypatch.setattr(chat_tools, "_gen_image_flow", _fake_gen_flow)

    search_spec = tools.get_tool("search")
    assert search_spec is not None and search_spec.execute is not None
    out_search = asyncio.run(tool_runner.execute_tool(search_spec, {"query": "AI"}, user_id=None))
    assert out_search["status"] == "ok"
    assert out_search["observation"]["provenance"] == "web_search"
    assert out_search["observation"]["epistemic_status"] == "UNVERIFIED"
    assert out_search["observation"]["summary"]

    image_spec = tools.get_tool("image_gen")
    assert image_spec is not None and image_spec.execute is not None
    out_image = asyncio.run(tool_runner.execute_tool(
        image_spec, {"prompt": "海边日落", "character_id": 2}, user_id=None,
    ))
    assert out_image["status"] == "ok"
    assert out_image["observation"]["provenance"] == "image_gen"
    assert out_image["observation"]["summary"]
    assert seen["gen"] is not None
