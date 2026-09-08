# -*- coding: utf-8 -*-
"""工具轨迹失真治理 R3 / R6 专项回归测试（2026-09-09，方案 §4.3 / §4.6）。

- R3：MCP 未注册 / 已禁用不再静默跳过（steps 带 reason + 回注「工具不可用」防 AI 谎称已执行）；
      声明注入的连接态守卫；流式声明注入走 flag ``mcp_stream_declarations``（默认关=旧行为零变化）。
- R6：「调用能力」只列本轮真实用到的能力——ability_labels 归一（中文 / 去重 / 上限 / 隐藏内部工具），
      nodes 不再用「启用插件全集」填充，执行成功点回填，组装处统一 normalize。

均为纯函数 / 内存态测试（不触碰 backend/data）；项目未装 pytest-asyncio，统一 asyncio.run。
"""
import asyncio

from app.agent import loop


# ────────────────────────── R3②：未注册 / 禁用不再静默 ──────────────────────────

def test_R3_未注册工具带reason并回注(monkeypatch):
    """get_tool 返回 None → steps 带 reason=tool_not_registered + 回注「工具不可用」系统消息。"""
    from app.agent import mcp_tools

    def _no_tool(name):
        return None

    monkeypatch.setattr("app.agent.tools.get_tool", _no_tool)
    state: dict = {"ai_response": "[mcp.srv.echo]{}\n[/mcp.srv.echo]"}
    steps: list[dict] = []
    executed, results = asyncio.run(mcp_tools.run_stream_mcp_tool_stage(
        state, steps, user_id=1, character_id=2, session_id=3,
    ))
    assert executed is False
    assert steps and steps[0]["ok"] is False
    assert steps[0]["reason"] == "tool_not_registered"
    assert results and results[0]["ok"] is False
    assert "未注册" in results[0]["error"]
    injected = " ".join(m["content"] for m in state.get("context_messages") or [])
    assert "【工具不可用】" in injected          # 回注防 AI 谎称已执行
    assert "不要声称已调用" in injected


def test_R3_禁用工具带reason(monkeypatch):
    """spec.enabled=False → reason=tool_disabled（与未注册区分）。"""
    from app.agent import mcp_tools
    from app.agent.tools import ToolSpec

    def _disabled(name):
        return ToolSpec(name=name, description="d", enabled=False)

    monkeypatch.setattr("app.agent.tools.get_tool", _disabled)
    steps: list[dict] = []
    asyncio.run(mcp_tools.run_stream_mcp_tool_stage(
        {"ai_response": "[mcp.srv.echo]{}\n[/mcp.srv.echo]"}, steps,
        user_id=1, character_id=2, session_id=3,
    ))
    assert steps[0]["reason"] == "tool_disabled"


def test_R3_无mcp标记零行为():
    """无 mcp.* 标记 → (False, [])（零行为变化，回归保护）。"""
    from app.agent import mcp_tools
    state = {"ai_response": "你好呀"}
    executed, results = asyncio.run(mcp_tools.run_stream_mcp_tool_stage(
        state, [], user_id=1, character_id=2, session_id=3,
    ))
    assert executed is False and results == []
    assert not state.get("context_messages")


# ────────────────────────── R3①：流式声明注入 flag ──────────────────────────

def test_R3_流式默认不注入声明(monkeypatch):
    """flag 默认关 → stream=True 仍返回 []（与旧行为逐字节一致，一键回退）。"""
    from app.agent.context import section_mcp

    async def _owned(uid):
        return {1}

    monkeypatch.setattr("app.mcp.ownership.owned_server_ids", _owned)
    monkeypatch.setattr("app.agent.tools.list_tools", lambda: [])
    assert asyncio.run(section_mcp._build_mcp_tool_declarations(1, stream=True)) == []


def test_R3_流式flag开才注入(monkeypatch):
    """flag mcp_stream_declarations 开 → 流式也注入声明（灰度路径）。"""
    from app.agent.context import section_mcp
    from app.agent.tools import ToolSpec

    async def _owned(uid):
        return {7}

    monkeypatch.setattr("app.mcp.ownership.owned_server_ids", _owned)
    monkeypatch.setattr("app.agent.tools.list_tools", lambda: [
        ToolSpec(name="mcp.srv.read", description="d", server_id=7),
    ])
    loop.AGENT_FLAGS["mcp_stream_declarations"] = True
    try:
        decls = asyncio.run(section_mcp._build_mcp_tool_declarations(1, stream=True))
    finally:
        loop.AGENT_FLAGS["mcp_stream_declarations"] = False
    assert [d["name"] for d in decls] == ["mcp.srv.read"]


def test_R3_声明文本含不可用时如实告知约束():
    """声明格式化文本新增约束：工具不可用时必须如实告知，不得声称已执行。"""
    from app.agent.context.section_mcp import _format_mcp_declarations
    text = _format_mcp_declarations([{"name": "mcp.srv.echo", "description": "d", "parameters": {}}])
    assert "[mcp.<server>.<tool>]" in text          # 旧约束保留
    assert "不要声称已执行" in text                    # R3 新增


# ────────────────────────── R3③：连接态守卫 ──────────────────────────

def test_R3_已断连的工具不注入(monkeypatch):
    """连接记录存在但已断连（未注销的瞬时窗口）→ 不注入，防 AI 调用打不通的工具。"""
    from app.agent.context import section_mcp
    from app.agent.tools import ToolSpec

    class _Conn:
        is_connected = False

    class _Mgr:
        def get_connection(self, sid):
            return _Conn()

    async def _owned(uid):
        return {7}

    monkeypatch.setattr("app.mcp.ownership.owned_server_ids", _owned)
    monkeypatch.setattr("app.agent.tools.list_tools", lambda: [
        ToolSpec(name="mcp.srv.read", description="d", server_id=7),
    ])
    import app.mcp.manager as _mm
    monkeypatch.setattr(_mm, "mcp_manager", _Mgr())
    assert asyncio.run(section_mcp._build_mcp_tool_declarations(1)) == []


def test_R3_已连接或从未连接的工具仍注入(monkeypatch):
    """连接正常 / 无连接记录（未纳入管理）→ 正常注入（守卫只拦「记录存在但断连」）。"""
    from app.agent.context import section_mcp
    from app.agent.tools import ToolSpec

    class _Mgr:
        def get_connection(self, sid):
            return None          # 无记录（未纳入管理）→ 不拦

    async def _owned(uid):
        return {7}

    monkeypatch.setattr("app.mcp.ownership.owned_server_ids", _owned)
    monkeypatch.setattr("app.agent.tools.list_tools", lambda: [
        ToolSpec(name="mcp.srv.read", description="d", server_id=7),
    ])
    import app.mcp.manager as _mm
    monkeypatch.setattr(_mm, "mcp_manager", _Mgr())
    decls = asyncio.run(section_mcp._build_mcp_tool_declarations(1))
    assert [d["name"] for d in decls] == ["mcp.srv.read"]


# ────────────────────────── R6：能力标签归一 ──────────────────────────

def test_R6_normalize_去重保序上限():
    from app.agent.ability_labels import normalize_tool_list, _MAX_ITEMS
    out = normalize_tool_list(["生图", "识图", "生图", "", None, "语音回复"])
    assert out == ["生图", "识图", "语音回复"]
    many = [f"能力{i}" for i in range(20)]
    assert len(normalize_tool_list(many)) == _MAX_ITEMS
    assert normalize_tool_list(many)[0] == "能力0"      # 保序
    assert normalize_tool_list([]) == []


def test_R6_is_hidden_内部工具():
    from app.agent.ability_labels import is_hidden
    assert is_hidden("memory_search") is True
    assert is_hidden("memory_obs") is True
    assert is_hidden("state_trigger") is True
    assert is_hidden("memory_extract") is True
    assert is_hidden("联网搜索") is False


def test_R6_plugin_label_display_name优先():
    from app.agent.ability_labels import plugin_label
    assert plugin_label({"display_name": "抖音助手", "name": "douyin_mcp"}) == "抖音助手"
    # 无 display_name → 美化英文 id（不出现空白）
    assert plugin_label({"name": "douyin_mcp"}) == "Douyin Mcp"
    assert plugin_label({}) == "Plugin"


def test_R6_mcp_label_只到服务器粒度():
    from app.agent.ability_labels import mcp_label
    assert mcp_label("srv", "mcp.srv.echo") == "MCP·srv"
    assert mcp_label("", "") == "MCP·MCP"


def test_R6_record_内置插件MCP分类():
    from app.agent.ability_labels import record_ability_used
    from app.agent.tools import ToolSpec

    st: dict = {}
    record_ability_used(st, spec=ToolSpec(name="search", description="d"))
    record_ability_used(st, spec=ToolSpec(name="note_memo", description="d"))
    record_ability_used(st, spec=ToolSpec(name="mcp.srv.echo", description="d", server_id=7))
    # 插件友好名：有 display_name 用之，否则美化英文 id（此处显式给 info，避免依赖插件加载顺序）
    record_ability_used(st, spec=ToolSpec(name="douyin_mcp.handle_mention", description="d", plugin="douyin_mcp"),
                        plugin_info={"name": "douyin_mcp"})
    record_ability_used(st, spec=ToolSpec(name="douyin_mcp.handle_mention", description="d", plugin="douyin_mcp"),
                        plugin_info={"name": "douyin_mcp", "display_name": "抖音助手"})
    assert st["tools_used"] == ["联网搜索", "记备忘", "MCP·srv", "Douyin Mcp", "抖音助手"]


def test_R6_record_隐藏内部工具与去重上限():
    from app.agent.ability_labels import record_ability_used, _MAX_ITEMS
    from app.agent.tools import ToolSpec

    st: dict = {}
    record_ability_used(st, spec=ToolSpec(name="memory_search", description="d"))
    record_ability_used(st, spec=ToolSpec(name="memory_obs", description="d"))
    assert (st.get("tools_used") or []) == []          # 内部工具不出现

    record_ability_used(st, spec=ToolSpec(name="search", description="d"))
    record_ability_used(st, spec=ToolSpec(name="search", description="d"))
    assert st["tools_used"] == ["联网搜索"]             # 同一工具一轮多次只记一次

    st2: dict = {}
    for i in range(20):
        record_ability_used(st2, label=f"能力{i}")
    assert len(st2["tools_used"]) == _MAX_ITEMS


def test_R6_record_异常不影响主链路():
    from app.agent.ability_labels import record_ability_used
    st: dict = {}
    record_ability_used(st)                            # 无 spec 无 label → 直接返回
    assert st == {}
    record_ability_used(st, label="生图")
    assert st["tools_used"] == ["生图"]


def test_R6_启用插件全集不再填充(monkeypatch):
    """flag 开（默认）：启用多个插件但本轮不调用 → tools_used 不含「扩展：…」全集。"""
    import app.agent.nodes as nodes
    from app.plugins import registry

    registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp")
    registry._enabled["douyin_mcp"] = True

    async def _cfg(uid):
        return None

    async def _chat(**kw):
        return "你好呀"

    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _cfg)
    monkeypatch.setattr(nodes, "chat_completion", _chat)
    state = {
        "user_message": "hi", "character_id": 1, "user_id": 1, "session_id": 1,
        "context_messages": [], "ai_response": "", "new_memories": [],
        "tools_used": [], "skip_memory_save": True, "lang": "zh",
    }
    loop.AGENT_FLAGS["chat_tools_list_real_only"] = True
    try:
        out = asyncio.run(nodes.generate_response(state))
    finally:
        loop.AGENT_FLAGS["chat_tools_list_real_only"] = True
    tools = out.get("tools_used") or []
    assert not any(str(t).startswith("扩展：") for t in tools)


def test_R6_flag关回退启用插件全集(monkeypatch):
    """flag 关 → 回退旧行为（「扩展：启用插件 id 列表」），保证可一键回退。"""
    import app.agent.nodes as nodes
    from app.plugins import registry

    registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp")
    registry._enabled["douyin_mcp"] = True

    async def _cfg(uid):
        return None

    async def _chat(**kw):
        return "你好呀"

    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _cfg)
    monkeypatch.setattr(nodes, "chat_completion", _chat)
    state = {
        "user_message": "hi", "character_id": 1, "user_id": 1, "session_id": 1,
        "context_messages": [], "ai_response": "", "new_memories": [],
        "tools_used": [], "skip_memory_save": True, "lang": "zh",
    }
    loop.AGENT_FLAGS["chat_tools_list_real_only"] = False
    try:
        out = asyncio.run(nodes.generate_response(state))
    finally:
        loop.AGENT_FLAGS["chat_tools_list_real_only"] = True
    tools = out.get("tools_used") or []
    assert any(str(t).startswith("扩展：") and "douyin_mcp" in str(t) for t in tools)


def test_R6_工具真实执行成功才回填(monkeypatch):
    """_run_tool_stage：工具执行成功 → tools_used 出现中文能力标签；失败不回填。"""
    from app.agent import runtime
    from app.agent.tools import ToolSpec, register_tool

    async def _ok_execute(spec, payload, **kw):
        return {"status": "ok", "result": {"ok": True},
                "observation": {"summary": "已记下"}}

    async def _fail_execute(spec, payload, **kw):
        return {"status": "error", "error": "boom"}

    spec = ToolSpec(name="note_memo", description="d", action_type="MEMO",
                    execute=lambda p: {"ok": True})
    register_tool(spec)
    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _ok_execute)

    state = {"ai_response": "[MEMO]买牛奶[/MEMO]", "tools_used": []}
    asyncio.run(runtime._run_tool_stage(
        state, [], character_id=1, user_id=1, session_id=1,
    ))
    assert state["tools_used"] == ["记备忘"]

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fail_execute)
    state2 = {"ai_response": "[MEMO]买牛奶[/MEMO]", "tools_used": []}
    asyncio.run(runtime._run_tool_stage(
        state2, [], character_id=1, user_id=1, session_id=1,
    ))
    assert state2["tools_used"] == []                  # 失败不冒充成功


def test_R6_MCP成功记MCP服务器标签(monkeypatch):
    """MCP 工具真实执行成功 → 记「MCP·服务器名」一项（不堆远端工具名）。"""
    from app.agent import mcp_tools
    from app.agent.tools import ToolSpec

    spec = ToolSpec(name="mcp.srv.echo", description="d", server_id=7)

    def _get(name):
        return spec

    async def _call(spec, payload, **kw):
        return {"status": "ok", "result": {"ok": True},
                "observation": {"summary": "echo ok"}}

    monkeypatch.setattr("app.agent.tools.get_tool", _get)
    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _call)
    state: dict = {"ai_response": "[mcp.srv.echo]{}\n[/mcp.srv.echo]"}
    executed, _ = asyncio.run(mcp_tools.run_stream_mcp_tool_stage(
        state, [], user_id=1, character_id=2, session_id=3,
    ))
    assert executed is True
    assert state.get("tools_used") == ["MCP·srv"]
