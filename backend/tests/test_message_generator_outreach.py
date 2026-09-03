# -*- coding: utf-8 -*-
"""B1-③ message_generator 意图分支测试（方案 §5.3）：flag 关逐字节等价 + 意图分支素材开关 + RECALL_SHARED 捞链。"""
import asyncio

from app.scheduling import message_generator as mg


class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult()


def _fake_session_factory():
    def _factory():
        return _FakeSession()
    return _factory


def _patch_mg(monkeypatch, persona=None, llm=None, recall_chain=None, search_mems=None):
    """mock 掉 generate_proactive_event 的全部前置查询与 LLM，捕获 messages。返回 captured。"""
    if persona is None:
        persona = {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}
    if llm is None:
        llm = "你好呀！\n今天天气不错。"

    async def _noop(*_a, **_k):
        return ""
    async def _noop_list(*_a, **_k):
        return search_mems or []
    async def _persona(*_a, **_k):
        return persona

    captured = {}

    async def _fake_llm(**_kw):
        captured["messages"] = _kw.get("messages")
        return llm

    async def _fake_level(_cid):
        return 0

    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _noop)
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop)
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_session_factory)
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
    monkeypatch.setattr(mg, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg, "load_character_reasoning_level", _fake_level)
    if recall_chain is not None:
        async def _fake_pick(cid):
            return recall_chain
        monkeypatch.setattr("app.memory.chain_builder.pick_recall_chain", _fake_pick)
    return captured


def _run(monkeypatch, **kw):
    gen_kw = dict(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家",
        last_context="用户: 今天天气不错\n你: 是啊，很适合出去走走",
    )
    gen_kw.update(kw)
    return asyncio.run(mg.generate_proactive_event(**gen_kw))


# ═══════════════════ flag 关 = 旧链路逐字节等价（锁行为） ═══════════════════

def test_mg_flag_off_uses_old_forced_block(monkeypatch):
    """ouorth_intent=None（flag 关）：prompt 仍用旧「先看最近聊了什么」强制承接块。"""
    captured = _patch_mg(monkeypatch)
    segments = _run(monkeypatch)
    assert segments == ["你好呀！", "今天天气不错。"]
    prompt = captured["messages"][1]["content"]
    assert "先看最近聊了什么：" in prompt
    assert "新消息必须承接最近正在聊的或与当前语境一致" in prompt
    assert "只是背景，不是必须续写的剧本" not in prompt


# ═══════════════════ outreach 新分支 prompt ═══════════════════

def test_mg_outreach_new_branch_prompt(monkeypatch):
    """outreach_intent 非空：用「分级+意图+转场句库」块替换强制承接块。"""
    captured = _patch_mg(monkeypatch, llm="我刚泡了杯咖啡。\n你最近工作还顺心吗？")
    _run(monkeypatch, outreach_intent="share_self", outreach_plan={
        "tier": "recent", "allow_active_topics": False, "allow_storyline": False,
        "allow_recall": False, "memory_query": "", "must_return_question": True,
    })
    prompt = captured["messages"][1]["content"]
    assert "只是背景，不是必须续写的剧本" in prompt
    assert "硬约束：" in prompt
    assert "只问一个，不连环问、不审问" in prompt
    assert "可参考的自然开头" in prompt          # 转场句库注入
    assert "先看最近聊了什么：" not in prompt    # 旧强制承接块被替换
    assert "新消息必须承接最近正在聊的" not in prompt


def test_mg_outreach_stale_share_self_no_storyline(monkeypatch):
    """stale + SHARE_SELF（allow_storyline=False）：不注入 AI 剧情。"""
    captured = _patch_mg(monkeypatch, persona={
        "cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "正在冷战",
    }, llm="跟你说个小事。\n你最近还好吗？")
    _run(monkeypatch, outreach_intent="share_self", outreach_plan={
        "tier": "stale", "allow_active_topics": False, "allow_storyline": False,
        "allow_recall": False, "memory_query": "", "must_return_question": True,
    })
    prompt = captured["messages"][1]["content"]
    assert "正在冷战" not in prompt            # 不背剧情


def test_mg_outreach_continue_share_self_injects_storyline(monkeypatch):
    """continue + SHARE_SELF（allow_storyline=True）：仅此时可轻点 AI 剧情。"""
    captured = _patch_mg(monkeypatch, persona={
        "cognitive": True, "relationship_state": "关系温度（自然体现，别念数据）：信任80",
        "active_topics": "", "storyline_status": "正在冷战",
    }, llm="跟你说个小事。\n你最近还好吗？")
    _run(monkeypatch, outreach_intent="share_self", outreach_plan={
        "tier": "continue", "allow_active_topics": False, "allow_storyline": True,
        "allow_recall": False, "memory_query": "", "must_return_question": True,
    })
    prompt = captured["messages"][1]["content"]
    assert "正在冷战" in prompt                # continue+share_self 允许剧情
    assert "信任80" in prompt                  # 关系温度仍注入


# ═══════════════════ RECALL_SHARED 捞链衔接 ═══════════════════

def test_mg_recall_shared_uses_chain_when_present(monkeypatch):
    """RECALL_SHARED：链存在时用链（时间锚点清晰）。"""
    from app.agent import loop as loop_mod
    loop_mod.AGENT_FLAGS["memory_chain_builder"] = True
    try:
        chain_text = ("[2026-08-01] 你们一起去看海\n[2026-08-03] 你说想再去一次")
        captured = _patch_mg(monkeypatch, recall_chain=chain_text, llm="突然想起我们去看海那次。\n你后来还有再去吗？")
        _run(monkeypatch, outreach_intent="recall_shared", outreach_plan={
            "tier": "recent", "allow_active_topics": False, "allow_storyline": False,
            "allow_recall": True, "memory_query": "和用户一起经历的事", "must_return_question": True,
        })
        prompt = captured["messages"][1]["content"]
        assert "一起去看海" in prompt
        assert "你记得的近期事情" in prompt
    finally:
        loop_mod.AGENT_FLAGS["memory_chain_builder"] = False


def test_mg_recall_shared_fallback_to_semantic(monkeypatch):
    """RECALL_SHARED：无链时回退语义检索（search_memories 结果注入）。"""
    from app.agent import loop as loop_mod
    loop_mod.AGENT_FLAGS["memory_chain_builder"] = True
    try:
        mems = [{"content": "用户说他下个月要搬家", "created_at": "2026-08-05", "epistemic_status": None}]
        captured = _patch_mg(monkeypatch, recall_chain=None, search_mems=mems, llm="我记得你提过搬家的事。\n准备得怎么样啦？")
        _run(monkeypatch, outreach_intent="recall_shared", outreach_plan={
            "tier": "recent", "allow_active_topics": False, "allow_storyline": False,
            "allow_recall": True, "memory_query": "和用户一起经历的事", "must_return_question": True,
        })
        prompt = captured["messages"][1]["content"]
        assert "下个月要搬家" in prompt
        assert "[记录于 2026-08-05]" in prompt    # format_memory_line 带真实日期
    finally:
        loop_mod.AGENT_FLAGS["memory_chain_builder"] = False


# ═══════════════════ 必须抛回问题 → 追加修正重试一次 ═══════════════════

def test_mg_outreach_missing_question_triggers_retry(monkeypatch):
    """must_return_question=True 但生成结果无邀请 → 与自然度低分相同，追加修正重试一次。"""
    calls = {"n": 0}

    # 首轮：纯陈述句（无邀请）；次轮：带邀请
    llm_responses = ["我刚才泡了杯咖啡，挺香的。", "我刚泡了杯咖啡。\n你最近睡得好吗？"]

    async def _fake_llm(**_kw):
        calls["messages"] = _kw.get("messages")
        resp = llm_responses[calls["n"]]
        calls["n"] += 1
        return resp

    async def _noop(*_a, **_k):
        return ""
    async def _persona(*_a, **_k):
        return {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}
    async def _noop_list(*_a, **_k):
        return []
    async def _fake_level(_cid):
        return 0

    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _noop)
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop)
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_session_factory)
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
    monkeypatch.setattr(mg, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg, "load_character_reasoning_level", _fake_level)

    _run(monkeypatch, outreach_intent="share_self", outreach_plan={
        "tier": "recent", "allow_active_topics": False, "allow_storyline": False,
        "allow_recall": False, "memory_query": "", "must_return_question": True,
    })
    assert calls["n"] == 2                      # 触发了一次追加修正重试
    # 修正提示注入
    hint = calls["messages"][-1]["content"]
    assert "轻松问题" in hint and "只问一个" in hint
