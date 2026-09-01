# -*- coding: utf-8 -*-
"""Phase E 测试：群聊/抖音统一 Runtime（Feature Flag 回退 + 关键路径 + 知识不串线）

覆盖：
- 两个新 Flag 已全量开启（2026-08-18 用户拍板；改回 False 即恢复旧链路）；
- 群聊 _generate_replies：flag 关走旧单次 JSON 链路 / 开走 _generate_replies_runtime；
- _generate_replies_runtime：逐角色独立上下文（知识不串线）+ 群公开上下文共享 + 顺序发言承接；
- app/agent/runtime.py run_social_reply：build_context/generate_response 复用、标记剥离、
  save_memory=False 透传 skip_memory_save、失败静默降级、allow_tools 经 execute_tool 执行并再决策；
- arbiter._plugin_proactive_runtime：走统一 Runtime 并 send_to_session。
"""
import asyncio
from types import SimpleNamespace

from app.agent import loop
from app.agent import runtime as runtime_mod
from app.application import chat_groups as cg  # F5-a：实现迁至 application/chat_groups，patch 须指向定义模块
from app.scheduler import arbiter


# ---------------- Flag 默认值 ----------------

def test_phase_e_flag已全量开启():
    assert loop.AGENT_FLAGS.get("agent_loop_group_chat") is True
    assert loop.AGENT_FLAGS.get("agent_loop_social") is True  # X5：原 agent_loop_douyin 改名


# ---------------- 群聊 _generate_replies：flag 回退 ----------------

class _FakeGroupDB:
    """伪造群聊查询：按 SQL 文本分发 members / chars / recent messages"""

    def __init__(self, members, chars, recent):
        self._members = members
        self._chars = chars
        self._recent = recent

    async def execute(self, stmt, *a, **kw):
        text = str(stmt)
        if "chat_group_members" in text:
            return _FakeScalar(self._members)
        if "ai_characters" in text:
            return _FakeScalar(self._chars)
        if "chat_group_messages" in text:
            return _FakeScalar(self._recent)
        return _FakeScalar([])


class _FakeScalar:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


def _char(cid, name):
    return SimpleNamespace(id=cid, name=name, personality="活泼", chat_style="元气")


def _group_fake_db():
    chars = [_char(11, "小阳"), _char(12, "小冰")]
    return _FakeGroupDB(members=[11, 12], chars=chars, recent=[]), chars


def test_group_flag关走旧链路(monkeypatch):
    """flag 关闭 = 旧单次 JSON 链路（零行为变化）"""
    db, _ = _group_fake_db()
    seen = {}

    async def _fake_chat_completion(messages, **kw):
        seen["prompt"] = messages[1]["content"] if len(messages) > 1 else ""
        return '{"replies": [{"character_id": 11, "content": "好呀！"}]}'

    async def _fake_state_line(char):
        return ""

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)
    monkeypatch.setattr(cg, "_state_line", _fake_state_line)
    loop.AGENT_FLAGS["agent_loop_group_chat"] = False
    try:
        out = asyncio.run(cg._generate_replies(db, 1, "明天一起做饭吗？", "用户", user_id=4))
    finally:
        loop.AGENT_FLAGS["agent_loop_group_chat"] = False
    assert out == [{"character_id": 11, "content": "好呀！"}]
    # 旧链路特征：JSON 单次调用 + 成员/知识边界规则都在 prompt 里
    assert "请从以上成员中选择 1-3 个最可能回应的角色" in seen["prompt"]
    assert "知识边界（必须遵守）" in seen["prompt"]
    assert "只输出 JSON" in seen["prompt"]


def test_group_flag开走runtime(monkeypatch):
    """flag 开启 = 走 _generate_replies_runtime（结果同构返回）"""
    db, _ = _group_fake_db()
    calls = {}

    async def _fake_runtime(_db, gid, content, uname, uid, **kw):
        calls["args"] = (gid, content, uname, uid)
        calls["kw"] = kw
        return [{"character_id": 11, "content": "runtime 回复"}]

    monkeypatch.setattr(cg, "_generate_replies_runtime", _fake_runtime)
    loop.AGENT_FLAGS["agent_loop_group_chat"] = True
    try:
        out = asyncio.run(cg._generate_replies(db, 1, "明天一起做饭吗？", "用户", user_id=4))
    finally:
        loop.AGENT_FLAGS["agent_loop_group_chat"] = False
    assert out == [{"character_id": 11, "content": "runtime 回复"}]
    assert calls["args"] == (1, "明天一起做饭吗？", "用户", 4)
    assert {c.id for c in calls["kw"]["speakers"]} == {11, 12}


def test_group_runtime_异常回退旧链路(monkeypatch):
    """runtime 抛异常 → 回退旧链路兜底（不破坏群聊）"""
    db, _ = _group_fake_db()
    seen = {}

    async def _boom(*a, **kw):
        raise RuntimeError("runtime boom")

    async def _fake_chat_completion(messages, **kw):
        seen["prompt"] = messages[1]["content"] if len(messages) > 1 else ""
        return '{"replies": [{"character_id": 12, "content": "旧链路兜底"}]}'

    async def _fake_state_line(char):
        return ""

    monkeypatch.setattr(cg, "_generate_replies_runtime", _boom)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)
    monkeypatch.setattr(cg, "_state_line", _fake_state_line)
    loop.AGENT_FLAGS["agent_loop_group_chat"] = True
    try:
        out = asyncio.run(cg._generate_replies(db, 1, "明天一起做饭吗？", "用户", user_id=4))
    finally:
        loop.AGENT_FLAGS["agent_loop_group_chat"] = False
    assert out == [{"character_id": 12, "content": "旧链路兜底"}]
    assert "只输出 JSON" in seen["prompt"]


# ---------------- _generate_replies_runtime：知识不串线 + 顺序发言 ----------------

def _runtime_fake_db():
    recent = [
        SimpleNamespace(sender_type="user", character_id=None, content="明天一起做饭吗？"),
        SimpleNamespace(sender_type="ai", character_id=11, content="好呀！我来打下手！"),
    ]
    return _FakeGroupDB(members=[11, 12], chars=[], recent=recent)


def test_group_runtime_逐角色独立上下文_知识不串线(monkeypatch):
    """每个回应角色独立构建上下文（character_id 各自传入），群公开上下文共享；
    后发言者可见先发言者的回应（真实对话顺序承接）"""
    db = _runtime_fake_db()
    calls = []
    char_map = {11: _char(11, "小阳"), 12: _char(12, "小冰")}
    speakers = [char_map[11], char_map[12]]

    async def _fake_runtime(**kw):
        calls.append({"character_id": kw["character_id"], "extra_system": kw["extra_system"]})
        return {"status": "ok", "text": f"回复{kw['character_id']}", "steps": []}

    async def _fake_state_line(char):
        return ""

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr(cg, "_state_line", _fake_state_line)
    out = asyncio.run(cg._generate_replies_runtime(
        db, 1, "明天一起做饭吗？", "用户", 4,
        chars=list(char_map.values()), char_map=char_map,
        speakers=speakers, at_chars=[],
    ))
    assert out == [
        {"character_id": 11, "content": "回复11"},
        {"character_id": 12, "content": "回复12"},
    ]
    # 逐角色独立调用（知识不串线的承载：各自 character_id → build_context 只注入各自记忆）
    assert [c["character_id"] for c in calls] == [11, 12]
    # 群公开上下文（成员列表 + 本轮发言 + 知识边界规则）每次共享注入
    first = calls[0]["extra_system"][0]["content"]
    assert "小阳" in first and "小冰" in first
    assert "用户用户在群里说：明天一起做饭吗？" in first
    assert "最近群聊记录" in first and "好呀！我来打下手！" in first
    assert "知识边界（必须遵守）" in first
    # 后发言者可见先发言者回应（顺序承接，不重复不矛盾）
    second = calls[1]["extra_system"][0]["content"]
    assert "已有人先说了" in second
    assert "[小阳] 回复11" in second


def test_group_runtime_失败角色静默跳过(monkeypatch):
    """某个角色生成失败 → 该角色不回应（静默降级），其余角色正常"""
    db = _runtime_fake_db()
    char_map = {11: _char(11, "小阳"), 12: _char(12, "小冰")}

    async def _fake_runtime(**kw):
        if kw["character_id"] == 11:
            return {"status": "error", "text": "", "steps": []}
        return {"status": "ok", "text": "回复12", "steps": []}

    async def _fake_state_line(char):
        return ""

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr(cg, "_state_line", _fake_state_line)
    out = asyncio.run(cg._generate_replies_runtime(
        db, 1, "明天一起做饭吗？", "用户", 4,
        chars=list(char_map.values()), char_map=char_map,
        speakers=[char_map[11], char_map[12]], at_chars=[],
    ))
    assert out == [{"character_id": 12, "content": "回复12"}]


# ---------------- runtime.run_social_reply：关键路径 ----------------

def _patch_runtime_build(monkeypatch, seen):
    """把 build_context 替换为最小契约假实现：世界认知 system + 末尾 user 消息"""

    async def _fake_reasoning_level(cid):
        return 0

    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _fake_reasoning_level)

    async def _fake_build(state):
        seen.setdefault("char_ids", []).append(state["character_id"])
        state["context_messages"] = [
            {"role": "system", "content": f"【世界认知】角色{state['character_id']}的记忆"},
        ]
        state["context_messages"].append({"role": "user", "content": state["user_message"]})
        return state

    monkeypatch.setattr("app.agent.context_builder.build_context", _fake_build)


def _patch_generate(monkeypatch, seen, texts):
    """generate_response 假实现：记录 state，依次返回 texts"""

    async def _fake_gen(state):
        seen["states"].append(state)
        text = texts.pop(0) if texts else "最终回复"
        state["ai_response"] = text
        state["new_memories"] = []
        return state

    monkeypatch.setattr("app.agent.nodes.generate_response", _fake_gen)


def _patch_session_resolve(monkeypatch, sid=99):
    async def _fake_resolve(uid, cid):
        return sid
    monkeypatch.setattr(runtime_mod, "_resolve_session_id", _fake_resolve)


def test_runtime_关键路径_复用build_context与generate_response(monkeypatch):
    seen = {"char_ids": [], "states": []}
    _patch_runtime_build(monkeypatch, seen)
    _patch_generate(monkeypatch, seen, ["正文回复"])
    _patch_session_resolve(monkeypatch)

    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=None,
        user_message="你好呀",
        extra_system=[{"role": "system", "content": "【群公开】大家在讨论做饭"}],
        max_text=200, save_memory=False,
    ))
    assert res["status"] == "ok"
    assert res["text"] == "正文回复"
    # 世界认知按角色注入
    assert seen["char_ids"] == [11]
    # save_memory=False → skip_memory_save 透传（防机器内容污染记忆）
    assert seen["states"][0].get("skip_memory_save") is True
    # 平台公开上下文插入到 user 消息之前（系统块在前）
    msgs = seen["states"][0]["context_messages"]
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == "你好呀"
    assert msgs[-2]["content"] == "【群公开】大家在讨论做饭"
    assert msgs[0]["content"] == "【世界认知】角色11的记忆"


def test_runtime_默认save_memory_不设skip(monkeypatch):
    seen = {"states": []}
    _patch_runtime_build(monkeypatch, seen)
    _patch_generate(monkeypatch, seen, ["正文"])
    _patch_session_resolve(monkeypatch)
    asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=99, user_message="hi", max_text=200,
    ))
    assert "skip_memory_save" not in seen["states"][0]


def test_runtime_动作标记剥离_不编造(monkeypatch):
    """社交短回复默认不执行工具：标记记录 trace 并剥离，正文不泄漏标记"""
    seen = {"states": []}
    _patch_runtime_build(monkeypatch, seen)
    _patch_generate(monkeypatch, seen, ["正文[SEARCH]查一下[/SEARCH]【状态更新：开心】"])
    _patch_session_resolve(monkeypatch)
    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=99,
        user_message="这个梗是什么意思", max_text=200,
    ))
    assert res["status"] == "ok"
    assert res["text"] == "正文"
    assert "[SEARCH]" not in res["text"]
    # 动作已记入 steps（trace），但未执行（不编造成功）
    assert any(s.get("action") == "SEARCH" for s in res["steps"])


def test_runtime_失败静默降级(monkeypatch):
    seen = {"states": []}
    _patch_runtime_build(monkeypatch, seen)

    async def _boom(state):
        raise RuntimeError("LLM down")

    monkeypatch.setattr("app.agent.nodes.generate_response", _boom)
    _patch_session_resolve(monkeypatch)
    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=99, user_message="hi", max_text=200,
    ))
    assert res["status"] == "error"
    assert res["text"] == ""


def test_runtime_allow_tools_经execute_tool执行并再决策(monkeypatch):
    """allow_tools=True：note 工具经 tool_runner.execute_tool 统一执行（执行入口由内置工具提供）
    → observation 注入 → 再决策 1 次"""
    seen = {"char_ids": [], "states": [], "tools": []}
    _patch_runtime_build(monkeypatch, seen)
    # 首轮输出 MEMO 标记 → 工具执行 → 第二轮输出最终正文
    _patch_generate(monkeypatch, seen, ["[MEMO]喂猫[/MEMO]正文第一轮", "最终正文"])

    async def _fake_exec_note(spec, payload, **kw):
        seen["tools"].append((spec.name, payload, kw.get("character_id")))
        return {"status": "ok", "result": {"ok": True}, "observation": {"summary": "已记录到小手机备忘录"}}

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake_exec_note)
    _patch_session_resolve(monkeypatch)
    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=99,
        user_message="帮我记一下", max_text=200, allow_tools=True,
    ))
    assert res["status"] == "ok"
    assert res["text"] == "最终正文"
    assert seen["tools"] == [("note_memo", {"text": "喂猫", "character_id": 11}, 11)]
    # 工具结果 observation 注入后再决策 1 次
    assert len(seen["states"]) == 2
    assert "已记录到小手机（note_memo）" in seen["states"][1]["context_messages"][-1]["content"]
    assert any(s.get("action") == "MEMO" and s.get("ok") is True for s in res["steps"])


def test_runtime_allow_tools_未注册工具不执行不编造(monkeypatch):
    """未接执行入口的工具（如 status_update 占位登记，execute=None）→ 不执行、剥离标记，不编造成功"""
    seen = {"states": [], "tools": []}
    _patch_runtime_build(monkeypatch, seen)
    _patch_generate(monkeypatch, seen, ["【状态更新：开心】正文"])

    async def _fake_execute(spec, payload, **kw):
        seen["tools"].append(spec.name)
        return {"status": "ok"}

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake_execute)
    _patch_session_resolve(monkeypatch)
    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=99,
        user_message="这个梗是什么", max_text=200, allow_tools=True,
    ))
    assert res["status"] == "ok"
    assert res["text"] == "正文"  # 标记剥离，无编造
    assert seen["tools"] == []  # 未执行
    assert any(s.get("action") == "STATUS_UPDATE" and s.get("ok") is False for s in res["steps"])


# ---------------- arbiter._plugin_proactive_runtime ----------------

def test_arbiter_plugin_runtime_发送成功(monkeypatch):
    sent = {}

    async def _fake_runtime(**kw):
        return {"status": "ok", "text": "刚看到你的新动态，真有意思！", "steps": []}

    async def _fake_send(session_id, char_id, user_id, content, message_type="", **kw):
        sent.update({"sid": session_id, "cid": char_id, "uid": user_id, "content": content, "mtype": message_type})

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr("app.scheduler.scheduler.send_to_session", _fake_send)
    ok = asyncio.run(arbiter._plugin_proactive_runtime(
        11, {"user_id": 4, "plugin": "douyin_mcp"}, 7, "抖音上有人评论了你的视频",
    ))
    assert ok is True
    assert sent == {"sid": 7, "cid": 11, "uid": 4,
                    "content": "刚看到你的新动态，真有意思！", "mtype": "plugin"}


def test_arbiter_plugin_runtime_失败不发送(monkeypatch):
    sent = []

    async def _fake_runtime(**kw):
        return {"status": "error", "text": "", "steps": []}

    async def _fake_send(*a, **kw):
        sent.append(a)

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr("app.scheduler.scheduler.send_to_session", _fake_send)
    ok = asyncio.run(arbiter._plugin_proactive_runtime(
        11, {"user_id": 4, "plugin": "douyin_mcp"}, 7, "hint",
    ))
    assert ok is False
    assert sent == []  # 失败不发送（与旧链路失败语义一致）


def test_arbiter_plugin_runtime_空内容不发送(monkeypatch):
    sent = []

    async def _fake_runtime(**kw):
        return {"status": "ok", "text": "短", "steps": []}

    async def _fake_send(*a, **kw):
        sent.append(a)

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr("app.scheduler.scheduler.send_to_session", _fake_send)
    ok = asyncio.run(arbiter._plugin_proactive_runtime(
        11, {"user_id": 4, "plugin": "douyin_mcp"}, 7, "hint",
    ))
    assert ok is False  # 内容 < 2 字视为失败
    assert sent == []
