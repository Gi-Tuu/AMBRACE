# -*- coding: utf-8 -*-
"""D2（深度思考收敛）+ F1/F2（群聊/抖音轻量上下文模式）测试（2026-08-18，dsh 实施）

覆盖：
- D2-A~E：ai_social / timer（arbiter）/ emotion_care / memory_review / pet_care 在思考挡位 2 时
  不再走 include_reasoning（monkeypatch chat_completion 断言无 include_reasoning 参数）；挡位 1 的
  prompt 引导保留；extra_meta 不再带 reasoning（恒 None / 仅 memory_id）；
- F1：run_social_reply(light_context=True) 走轻量上下文（不调用全量 build_context，context_messages 精简：
  含人设/core/锚点/时间/语言/短回复约束 + extra_system，不含全量分区标记）；默认 False 与现状一致（走全量）；
  chat_groups 读 Flag 传 light_context；MAX_GROUP_SPEAKERS 边界（@>3 告警且不裁）；
- F2：arbiter._plugin_proactive_runtime 读同一 Flag 传 light_context。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时假会话不触碰 backend/data）
"""
import asyncio
import json
from types import SimpleNamespace

from app.agent import loop
from app.agent import runtime as runtime_mod
from app.application import chat_groups as cg  # F5-a：实现迁至 application/chat_groups，patch 须指向定义模块
from app.scheduling import arbiter as arbiter_mod
from app.scheduling import ai_social as ai_social_mod
from app.domain.emotion import care as emotion_mod  # F2-a：实现迁至 domain/emotion/care，patch 须指向定义模块
from app.scheduling import memory_review as review_mod
from app.scheduling import pet_care as pet_mod


# ---------------- 通用假 DB（按 pk 的 db.get + 空查询结果） ----------------

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return _FakeScalars([])

    def scalar(self):
        return 0

    def all(self):
        return []


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """最小假会话：db.get 按 (模型类, pk) 查 rows；execute 对 ai_characters 查询返回 rows 首个对象，
    其余一律返回空结果（None/[]）。"""

    def __init__(self, rows):
        self._rows = rows

    async def get(self, model, pk):
        return self._rows.get(int(pk))

    async def execute(self, stmt, *a, **kw):
        text = str(stmt)
        if "ai_characters" in text and self._rows:
            return _FakeResult(next(iter(self._rows.values())))
        return _FakeResult(None)

    async def commit(self):
        pass

    def add_all(self, rows):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeFactory:
    def __init__(self, rows):
        self._rows = rows

    def __call__(self):
        return _FakeSession(self._rows)


def _char(cid, name):
    return SimpleNamespace(
        id=cid, name=name, personality="活泼", chat_style="元气",
        relationship_summary="恋人", current_status="正在聊天",
        gender="male", relation_type="", is_active=True, user_id=4,
        cognitive_loop_enabled=False, memory_v2_enabled=False, self_statement="我的自述",
    )


async def _noop_str(*a, **kw):
    return ""


async def _noop_bool(*a, **kw):
    return False


async def _noop_none(*a, **kw):
    return None


async def _noop_int0(*a, **kw):
    return 0


async def _noop(*a, **kw):
    return None


async def _sid(uid, cid):
    return 99


async def _fake_rl2(cid):
    return 2


# ---------------- D2：挡位 2 不再走 include_reasoning ----------------

def test_ai_social_level2_不走include_reasoning(monkeypatch):
    """D2-A：ai_social 挡位 2 时统一走挡位 1/0 分支（无 include_reasoning，reasoning 本就丢弃）"""
    calls = []
    char_a = _char(11, "小阳")
    char_b = _char(12, "小冰")
    char_b.gender = "female"
    char_b.relationship_summary = "朋友"
    user = SimpleNamespace(id=4, nickname="阿明", username="aming")

    async def _fake_chat_completion(messages, **kw):
        calls.append(kw)
        return "你好呀！"

    async def _fake_pair_eligible(uid, a, b):
        return True

    async def _fake_news(uid, limit=3):
        return ""

    async def _fake_last_first(uid, a, b):
        return None

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _fake_rl2)
    monkeypatch.setattr("app.memory.core.get_core_memories", _noop_str)
    monkeypatch.setattr("app.events.facts.get_character_view", _noop_str)
    monkeypatch.setattr("app.agent.topic_tracker.load_active_topics_text", _noop_str)
    monkeypatch.setattr(ai_social_mod, "async_session_factory", _FakeFactory({11: char_a, 12: char_b, 4: user}))
    monkeypatch.setattr(ai_social_mod, "_pair_eligible", _fake_pair_eligible)
    monkeypatch.setattr(ai_social_mod, "_user_recent_news", _fake_news)
    monkeypatch.setattr(ai_social_mod, "_last_first_round", _fake_last_first)
    monkeypatch.setattr(ai_social_mod.random, "randint", lambda a, b: 2)
    monkeypatch.setattr(ai_social_mod.random, "random", lambda: 0.9)

    ok = asyncio.run(ai_social_mod.run_ai_social(11, 12, 4))
    assert ok is True
    assert calls, "AI social 应触发 LLM 调用"
    assert len(calls) == 2  # 2 轮（rounds=2）
    for kw in calls:
        assert "include_reasoning" not in kw
        assert kw.get("task") == "message"


def test_emotion_care_level2_不走include_reasoning(monkeypatch):
    """D2-C：emotion_care 挡位 2 时无 include_reasoning；extra_meta 不再带 reasoning（恒 None）"""
    calls = []
    sent = []
    task = SimpleNamespace(id=5, status="pending", trigger_msg="今天好累", finished_at=None)
    char = _char(3, "小阳")

    async def _fake_chat_completion(messages, **kw):
        calls.append(kw)
        return "别难过了，我一直都在。"

    async def _fake_identity(char_, uid):
        return "你是小阳，性格活泼。"

    async def _fake_send(session_id, character_id, user_id, content, message_type="", **kw):
        sent.append({"sid": session_id, "cid": character_id, "uid": user_id,
                     "content": content, "mtype": message_type, "extra": kw.get("extra_meta")})

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _fake_rl2)
    monkeypatch.setattr(emotion_mod, "async_session_factory", _FakeFactory({5: task, 3: char}))
    monkeypatch.setattr(emotion_mod, "_user_in_dnd_period", _noop_bool)
    monkeypatch.setattr(emotion_mod, "_daily_count", _noop_int0)
    monkeypatch.setattr(emotion_mod, "_last_care_at", _noop_none)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    monkeypatch.setattr("app.agent.user_profile.build_role_prompt_block", _fake_identity)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _noop_str)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop_str)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)

    ok = asyncio.run(emotion_mod.run_emotion_care(3, 4, 5))
    assert ok is True
    assert len(calls) == 1
    assert "include_reasoning" not in calls[0]
    assert calls[0].get("task") == "emotion"
    assert sent and sent[0]["extra"] is None  # extra_meta 不再带 reasoning
    assert task.status == "done"  # _finish_task 正常置位


def test_memory_review_level2_不走include_reasoning(monkeypatch):
    """D2-D：memory_review 挡位 2 时无 include_reasoning；_review_extra 仅保留 memory_id"""
    calls = []
    sent = []
    mem = SimpleNamespace(id=9, content="用户喜欢喝美式咖啡", memory_type="user_info",
                          is_archived=False, is_pinned=False, is_locked=False,
                          next_review_at=None, created_at=None)
    char = _char(3, "小阳")

    async def _fake_chat_completion(messages, **kw):
        calls.append(kw)
        return "上次说的那件事我记着呢。"

    async def _fake_identity(char_, uid):
        return "你是小阳，性格活泼。"

    async def _fake_send(session_id, character_id, user_id, content, message_type="", **kw):
        sent.append({"extra": kw.get("extra_meta"), "mtype": message_type})

    async def _enabled(cid):
        return True

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _fake_rl2)
    monkeypatch.setattr(review_mod, "async_session_factory", _FakeFactory({9: mem, 3: char}))
    monkeypatch.setattr(review_mod, "_daily_count", _noop_int0)
    monkeypatch.setattr(review_mod, "_last_review_at", _noop_none)
    monkeypatch.setattr(review_mod, "_user_in_dnd_period", _noop_bool)
    monkeypatch.setattr("app.scheduling.triggers.memory_review_enabled", _enabled)
    monkeypatch.setattr("app.scheduling.triggers.get_last_messages", _noop_str)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    monkeypatch.setattr("app.agent.user_profile.build_role_prompt_block", _fake_identity)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _noop_str)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)

    ok = asyncio.run(review_mod.run_memory_review(3, 4, 9))
    assert ok is True
    assert len(calls) == 1
    assert "include_reasoning" not in calls[0]
    assert calls[0].get("task") == "review"
    assert sent, "memory_review 应发送"
    assert json.loads(sent[0]["extra"]) == {"memory_id": 9}  # _review_extra 仅保留 memory_id


def test_pet_llm_level2_不走include_reasoning(monkeypatch):
    """D2-E：pet_care._pet_llm 挡位 2 时不再走 include_reasoning（reasoning 恒为空串）"""
    calls = []

    async def _fake_chat_completion(messages, **kw):
        calls.append(kw)
        return "该喂猫啦"

    monkeypatch.setattr(pet_mod, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(pet_mod, "load_character_reasoning_level", _fake_rl2)
    content, reasoning = asyncio.run(pet_mod._pet_llm([{"role": "user", "content": "hi"}], 3))
    assert content == "该喂猫啦"
    assert reasoning == ""  # reasoning 恒为空串
    assert len(calls) == 1
    assert "include_reasoning" not in calls[0]
    assert calls[0].get("task") == "message"


def test_pet_llm_level1_保留简短思考引导(monkeypatch):
    """D2-E：挡位 1 保留「先在心里简短想一下」prompt 引导（廉价替代深度思考）"""
    calls = []

    async def _fake_chat_completion(messages, **kw):
        calls.append(messages)
        return "别饿着啦"

    async def _fake_rl1(cid):
        return 1

    monkeypatch.setattr(pet_mod, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(pet_mod, "load_character_reasoning_level", _fake_rl1)
    content, reasoning = asyncio.run(pet_mod._pet_llm([{"role": "user", "content": "hi"}], 3))
    assert content == "别饿着啦"
    assert reasoning == ""
    assert "先在心里简短想一下" in calls[0][0]["content"]


def test_timer_level2_不走include_reasoning(monkeypatch):
    """D2-B：定时承诺（timer）挡位 2 时无 include_reasoning；extra_meta 不再带 reasoning（恒 None）"""
    calls = []
    sent = []
    char = _char(3, "小阳")
    event = SimpleNamespace(character_id=3, user_id=4, session_id=7, owner="ai",
                            content_hint="粥好了", event_type="ready", id=1)

    async def _fake_chat_completion(messages, **kw):
        calls.append(kw)
        return "粥好啦！"

    async def _fake_send(session_id, character_id, user_id, content, message_type="", **kw):
        sent.append({"sid": session_id, "cid": character_id, "uid": user_id,
                     "content": content, "mtype": message_type, "extra": kw.get("extra_meta")})

    async def _fake_hourly(cid):
        return 0

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_chat_completion)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _fake_rl2)
    monkeypatch.setattr(arbiter_mod, "async_session_factory", _FakeFactory({3: char}))
    monkeypatch.setattr(arbiter_mod, "get_hourly_active_count", _fake_hourly)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)
    monkeypatch.setattr("app.scheduling.promise_service.mark_fired", _noop)

    ok = asyncio.run(arbiter_mod._execute({"type": "timer", "event": event}))
    assert ok is True
    assert len(calls) == 1
    assert "include_reasoning" not in calls[0]
    assert calls[0].get("task") == "message"
    assert sent and sent[0]["extra"] is None  # extra_meta 不再带 reasoning
    assert sent[0]["mtype"] == "timer"


# ---------------- F1/F2：轻量上下文与 Flag 接线 ----------------

def test_flag_agent_social_light_context已全量开启():
    """F1：2026-08-18 用户拍板全量开启（回滚=改 False 重启恢复全量 build_context）；运行时开关 API 可切换"""
    assert loop.AGENT_FLAGS.get("agent_social_light_context") is True


def test_runtime_light_context_精简上下文_不走build_context(monkeypatch):
    """F1：light_context=True 走轻量上下文——不调用全量 build_context，context_messages 精简
    （含人设/core/锚点/时间/语言/短回复约束 + extra_system，不含全量分区标记）"""
    seen = {"build_called": False, "states": []}
    char = _char(11, "小阳")

    async def _fake_build(state):
        seen["build_called"] = True
        return state

    async def _fake_persona(cid, uid, platform="app"):
        return {
            "relationship": "恋人", "current_status": "正在聊天",
            "identity_profile": "用户喜欢喝美式咖啡，怕辣。",
            "relationship_state": "关系温度（自然体现，别念数据）：信任80、依恋70、好奇60",
            "character_feelings": "无", "storyline_recall": "无", "storyline_status": "无",
            "recent_emotion": "无", "active_topics": "", "cognitive": True,
            "public": False, "platform_profile_text": "",
        }

    async def _fake_cores(cid, limit=10):
        return [SimpleNamespace(created_at=None, content="用户下周要去北京出差")]

    async def _fake_anchors(cid, uid, limit=5):
        return [SimpleNamespace(created_at=None, content="一起去过杭州旅行")]

    async def _fake_gen(state):
        seen["states"].append(state)
        state["ai_response"] = "好呀，一起去！"
        state["new_memories"] = []
        return state

    monkeypatch.setattr("app.agent.context_builder.build_context", _fake_build)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _noop_int0)
    monkeypatch.setattr("app.db.database.async_session_factory", _FakeFactory({11: char}))
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _fake_persona)
    monkeypatch.setattr("app.memory.core.get_core_memories", _fake_cores)
    monkeypatch.setattr("app.memory.core.get_relationship_anchors", _fake_anchors)
    monkeypatch.setattr("app.agent.nodes.generate_response", _fake_gen)
    monkeypatch.setattr(runtime_mod, "_resolve_session_id", _sid)

    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=None, user_message="周末一起吃饭吗？",
        extra_system=[{"role": "system", "content": "【群公开】大家在讨论周末聚餐"}],
        max_text=200, light_context=True,
    ))
    assert res["status"] == "ok"
    assert res["text"] == "好呀，一起去！"
    assert seen["build_called"] is False  # 不走全量 build_context
    state = seen["states"][0]
    msgs = state["context_messages"]
    # extra_system 仍在 user 消息之前插入（与全量模式同语义）
    assert msgs[-1] == {"role": "user", "content": "周末一起吃饭吗？"}
    assert msgs[-2] == {"role": "system", "content": "【群公开】大家在讨论周末聚餐"}
    sys_text = "\n".join(m["content"] for m in msgs if m["role"] == "system")
    # 保留项：人设 / 身份画像 / 关系温度 / core / 锚点 / 时间 / 语言 / 短回复约束
    assert "小阳" in sys_text and "活泼" in sys_text
    assert "你对用户的长期印象" in sys_text and "关系温度" in sys_text
    assert "你记得的核心信息" in sys_text and "一起去过杭州旅行" in sys_text
    assert "【当前时间】" in sys_text
    assert "【语言】" in sys_text
    assert "20-40 字" in sys_text and "不要提及'AI/群聊'" in sys_text
    # 跳过项：全量 build_context 的系统模板标题/分区标记不应出现
    for marker in ("## 当前时间", "## 核心记忆", "## 最近的对话上下文", "世界状态", "朋友圈动态",
                   "织库", "手机感知", "生图", "认知规划"):
        assert marker not in sys_text, f"轻量上下文不应含 {marker!r}"
    # character_info 自述仍注入（response_parser 自述更新分支依赖）
    assert state.get("character_info", {}).get("self_statement") == "我的自述"


def test_runtime_light_context默认False走全量build_context(monkeypatch):
    """F1：light_context 默认 False=全量 build_context（现状零变化）"""
    seen = {"build_called": False, "states": []}

    async def _fake_build(state):
        seen["build_called"] = True
        state["context_messages"] = [{"role": "system", "content": "【世界认知】全量"}]
        state["context_messages"].append({"role": "user", "content": state["user_message"]})
        return state

    async def _fake_gen(state):
        seen["states"].append(state)
        state["ai_response"] = "全量回复"
        state["new_memories"] = []
        return state

    monkeypatch.setattr("app.agent.context_builder.build_context", _fake_build)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _noop_int0)
    monkeypatch.setattr("app.agent.nodes.generate_response", _fake_gen)
    monkeypatch.setattr(runtime_mod, "_resolve_session_id", _sid)

    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=None, user_message="hi", max_text=200,
    ))
    assert res["status"] == "ok"
    assert seen["build_called"] is True  # 默认 False 走全量 build_context


def test_runtime_light_context_挡位1注入推理指令(monkeypatch):
    """F1：light_context=True 且 reasoning_level==1 时注入【推理指令】（与全量 build_context 同语义）"""
    seen = {"states": []}
    char = _char(11, "小阳")

    async def _fake_persona(cid, uid, platform="app"):
        return {"identity_profile": "", "relationship_state": "", "cognitive": True, "public": False}

    async def _fake_gen(state):
        seen["states"].append(state)
        state["ai_response"] = "正文"
        state["new_memories"] = []
        return state

    async def _fake_rl1(cid):
        return 1

    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _fake_rl1)
    monkeypatch.setattr("app.db.database.async_session_factory", _FakeFactory({11: char}))
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _fake_persona)
    monkeypatch.setattr("app.memory.core.get_core_memories", _noop_str)
    monkeypatch.setattr("app.memory.core.get_relationship_anchors", _noop_str)
    monkeypatch.setattr("app.agent.nodes.generate_response", _fake_gen)
    monkeypatch.setattr(runtime_mod, "_resolve_session_id", _sid)

    res = asyncio.run(runtime_mod.run_social_reply(
        character_id=11, user_id=4, session_id=None, user_message="hi",
        max_text=200, light_context=True,
    ))
    assert res["status"] == "ok"
    sys_text = "\n".join(m["content"] for m in seen["states"][0]["context_messages"] if m["role"] == "system")
    assert "【推理指令】" in sys_text


# ---------------- F1：chat_groups 接线与 MAX_GROUP_SPEAKERS 边界 ----------------

class _FakeGroupDB:
    """伪造群聊查询：按 SQL 文本分发 members / chars / recent messages（与 test_phase_e 同风格）"""

    def __init__(self, members, chars, recent):
        self._members = members
        self._chars = chars
        self._recent = recent

    async def execute(self, stmt, *a, **kw):
        text = str(stmt)
        if "chat_group_members" in text:
            return _FakeGroupScalar(self._members)
        if "ai_characters" in text:
            return _FakeGroupScalar(self._chars)
        if "chat_group_messages" in text:
            return _FakeGroupScalar(self._recent)
        return _FakeGroupScalar([])


class _FakeGroupScalar:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


def test_chat_groups_runtime_light_context随Flag传参(monkeypatch):
    """F1：_generate_replies_runtime 读 AGENT_FLAGS 传 light_context（Flag 关=False / 开=True）"""
    db = _FakeGroupDB(members=[11, 12], chars=[], recent=[])
    calls = []
    char_map = {11: _char(11, "小阳"), 12: _char(12, "小冰")}

    async def _fake_runtime(**kw):
        calls.append({"light": kw.get("light_context"), "cid": kw["character_id"]})
        return {"status": "ok", "text": "回复", "steps": []}

    async def _fake_state_line(char_):
        return ""

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr(cg, "_state_line", _fake_state_line)
    # Flag 关 → light_context=False（现状全量 build_context）
    _orig = loop.AGENT_FLAGS.get("agent_social_light_context")
    loop.AGENT_FLAGS["agent_social_light_context"] = False
    try:
        asyncio.run(cg._generate_replies_runtime(
            db, 1, "明天一起做饭吗？", "用户", 4,
            chars=list(char_map.values()), char_map=char_map,
            speakers=list(char_map.values()), at_chars=[],
        ))
    finally:
        loop.AGENT_FLAGS["agent_social_light_context"] = _orig
    assert calls and all(c["light"] is False for c in calls)
    calls.clear()
    # Flag 开 → light_context=True
    loop.AGENT_FLAGS["agent_social_light_context"] = True
    try:
        asyncio.run(cg._generate_replies_runtime(
            db, 1, "明天一起做饭吗？", "用户", 4,
            chars=list(char_map.values()), char_map=char_map,
            speakers=list(char_map.values()), at_chars=[],
        ))
    finally:
        loop.AGENT_FLAGS["agent_social_light_context"] = _orig
    assert calls and all(c["light"] is True for c in calls)


def test_chat_groups_at超3告警且不裁(monkeypatch):
    """F1：用户 @ 角色数 > MAX_GROUP_SPEAKERS 时告警但不裁掉被 @ 角色（合规优先）"""
    chars = [_char(11, "小阳"), _char(12, "小冰"), _char(13, "小满"), _char(14, "阿澈"), _char(15, "小遥")]
    db = _FakeGroupDB(members=[11, 12, 13, 14, 15], chars=chars, recent=[])
    warnings = []
    runtime_seen = {}

    async def _fake_runtime(_db, gid, content, uname, uid, **kw):
        runtime_seen["speakers"] = [c.id for c in kw["speakers"]]
        runtime_seen["at_chars"] = [c.id for c in kw["at_chars"]]
        return [{"character_id": cid, "content": "好呀"} for cid in runtime_seen["speakers"]]

    def _fake_warn(msg, *a):
        warnings.append((str(msg), a))

    async def _fake_state_line(char_):
        return ""

    monkeypatch.setattr(cg, "_generate_replies_runtime", _fake_runtime)
    monkeypatch.setattr(cg._logger, "warning", _fake_warn)
    monkeypatch.setattr(cg, "_state_line", _fake_state_line)
    _orig = loop.AGENT_FLAGS.get("agent_loop_group_chat")
    loop.AGENT_FLAGS["agent_loop_group_chat"] = True
    try:
        out = asyncio.run(cg._generate_replies(
            db, 1, "@小阳 @小冰 @小满 @阿澈 一起去吃饭吧", "用户", user_id=4,
        ))
    finally:
        loop.AGENT_FLAGS["agent_loop_group_chat"] = _orig
    # 被 @ 的 4 个角色全部保留（不裁）
    assert sorted(runtime_seen["at_chars"]) == [11, 12, 13, 14]
    assert sorted(runtime_seen["speakers"]) == [11, 12, 13, 14]
    assert len(out) == 4
    # 告警已记录
    assert any("exceeds MAX_GROUP_SPEAKERS" in w for w, _ in warnings)


def test_chat_groups_at不超3不告警(monkeypatch):
    """F1：@ 角色数 ≤ MAX_GROUP_SPEAKERS 时无告警，speakers=@ 角色（现状）"""
    chars = [_char(11, "小阳"), _char(12, "小冰"), _char(13, "小满")]
    db = _FakeGroupDB(members=[11, 12, 13], chars=chars, recent=[])
    warnings = []
    runtime_seen = {}

    async def _fake_runtime(_db, gid, content, uname, uid, **kw):
        runtime_seen["speakers"] = [c.id for c in kw["speakers"]]
        return []

    def _fake_warn(msg, *a):
        warnings.append(str(msg))

    async def _fake_state_line(char_):
        return ""

    monkeypatch.setattr(cg, "_generate_replies_runtime", _fake_runtime)
    monkeypatch.setattr(cg._logger, "warning", _fake_warn)
    monkeypatch.setattr(cg, "_state_line", _fake_state_line)
    _orig = loop.AGENT_FLAGS.get("agent_loop_group_chat")
    loop.AGENT_FLAGS["agent_loop_group_chat"] = True
    try:
        asyncio.run(cg._generate_replies(db, 1, "@小阳 @小冰 @小满 一起去吃饭吧", "用户", user_id=4))
    finally:
        loop.AGENT_FLAGS["agent_loop_group_chat"] = _orig
    assert sorted(runtime_seen["speakers"]) == [11, 12, 13]
    assert not warnings


# ---------------- F2：arbiter 插件链路 Flag 接线 ----------------

def test_arbiter_plugin_runtime_light_context随Flag传参(monkeypatch):
    """F2：_plugin_proactive_runtime 读同一 Flag 传 light_context（默认关=全量 build_context 零变化）"""
    seen = {}

    async def _fake_runtime(**kw):
        seen["light"] = kw.get("light_context")
        return {"status": "ok", "text": "刚看到你的新动态，真有意思！", "steps": []}

    async def _fake_send(*a, **kw):
        pass

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)
    _orig = loop.AGENT_FLAGS.get("agent_social_light_context")
    loop.AGENT_FLAGS["agent_social_light_context"] = True
    try:
        ok = asyncio.run(arbiter_mod._plugin_proactive_runtime(
            11, {"user_id": 4, "plugin": "douyin_mcp"}, 7, "抖音上有人评论了你的视频",
        ))
    finally:
        loop.AGENT_FLAGS["agent_social_light_context"] = _orig
    assert ok is True
    assert seen["light"] is True


def test_arbiter_plugin_runtime_light_context默认False(monkeypatch):
    """F2：Flag 关时 light_context=False（现状全量 build_context）"""
    seen = {}

    async def _fake_runtime(**kw):
        seen["light"] = kw.get("light_context")
        return {"status": "ok", "text": "回复", "steps": []}

    async def _fake_send(*a, **kw):
        pass

    monkeypatch.setattr(runtime_mod, "run_social_reply", _fake_runtime)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)
    _orig = loop.AGENT_FLAGS.get("agent_social_light_context")
    loop.AGENT_FLAGS["agent_social_light_context"] = False
    try:
        ok = asyncio.run(arbiter_mod._plugin_proactive_runtime(
            11, {"user_id": 4, "plugin": "douyin_mcp"}, 7, "hint",
        ))
    finally:
        loop.AGENT_FLAGS["agent_social_light_context"] = _orig
    assert ok is True
    assert seen["light"] is False
