# -*- coding: utf-8 -*-
"""X-2 speaker 契约未闭环修复测试（2026-08-18，审查 X-2）：

- 公共判定函数 resolve_speaker_from_content：推断词优先 → 「我」前缀 → 用户指代前缀 → 批级回退，
  覆盖「我」开头/「用户」开头/「我们一起」/含推断词等输入（对照 extractor 与 nodes 原行为回归）；
- extractor._resolve_speaker 与 nodes.generate_response 改调公共函数（mock 验证调用 + 同源断言）；
- 注入端 format_memory_line include_speaker=True/False/无 speaker 字段（纯函数测试）；
- EXTRACT_PROMPT 含主语强化指令（字符串断言）；context_builder 主链路注入启用 include_speaker。
"""
import asyncio
import inspect

from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_INFERRED
from app.memory import extractor as extractor_mod
from app.memory import speaker
from app.memory.extractor import EXTRACT_PROMPT, _resolve_speaker
from app.memory.format import format_memory_line

UID, CID = 4, 11


# ---------------- 公共判定函数：规则回归（对照 extractor 与 nodes 原行为） ----------------

def test_resolve_我开头_角色表述():
    # 对照 extractor 原行为：内容以「我」开头 → character/INFERRED
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("我喜欢吃辣", "我今天想吃辣的", "好呀", UID, CID)
    assert (spk_type, spk_id, epi) == ("character", CID, EPISTEMIC_INFERRED)


def test_resolve_用户开头_用户陈述():
    # 对照 extractor 原行为：内容以「用户」开头 → user/FACT
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("用户喜欢喝美式咖啡", "用户说喜欢喝美式", "记住啦", UID, CID)
    assert (spk_type, spk_id, epi) == ("user", UID, EPISTEMIC_FACT)


def test_resolve_用户指代前缀():
    for head in ("对方", "他", "她"):
        spk_type, spk_id, epi = speaker.resolve_speaker_from_content(f"{head}说喜欢下雨天", "嗯", "我也是", UID, CID)
        assert (spk_type, spk_id) == ("user", UID)
        assert epi == EPISTEMIC_FACT


def test_resolve_我字头优先于前八字用户():
    # 对照 extractor 原行为：「我」字头先命中 → character/INFERRED（即使前 8 字含「用户」）
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("我注意到用户今天心情不好", "用户说了很多", "嗯", UID, CID)
    assert (spk_type, spk_id, epi) == ("character", CID, EPISTEMIC_INFERRED)


def test_resolve_前八字含用户且非我字头_用户陈述():
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("记得用户喜欢喝美式咖啡", "用户说过", "嗯", UID, CID)
    assert (spk_type, spk_id, epi) == ("user", UID, EPISTEMIC_FACT)


def test_resolve_含推断词_角色推测():
    # 对照 nodes 原行为：含推断词 → character/INFERRED（推断词优先于「用户」前缀）
    for content in ("用户可能喜欢海边", "用户好像养了猫", "用户也许明天来", "用户大概会去", "我觉得用户喜欢我", "用户猜测会下雨"):
        spk_type, spk_id, epi = speaker.resolve_speaker_from_content(content, "嗯", "好", UID, CID)
        assert (spk_type, spk_id, epi) == ("character", CID, EPISTEMIC_INFERRED), content


def test_resolve_我们一起_批级回退():
    # 「我们一起/我们」类共同事件：不因「我」字头误归角色，按批级回退规则处理
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("我们一起看了海", "走吧", "好呀", UID, CID)
    assert (spk_type, spk_id, epi) == ("user", UID, EPISTEMIC_FACT)          # 用户消息在场 → user/FACT
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("我们一起看了海", "", "我们去了海边", UID, CID)
    assert (spk_type, spk_id, epi) == ("character", CID, EPISTEMIC_INFERRED)  # 仅 AI 单方 → character/INFERRED


def test_resolve_无主语批级回退():
    # 对照 extractor 原行为：无主语 → 批级回退（用户消息在场=user，否则 character）
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("喜欢下雨天", "嗯", "我也是", UID, CID)
    assert (spk_type, spk_id, epi) == ("user", UID, EPISTEMIC_FACT)
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("喜欢下雨天", "   ", "我也是", UID, CID)
    assert (spk_type, spk_id, epi) == ("character", CID, EPISTEMIC_INFERRED)


def test_resolve_空内容_无归属():
    spk_type, spk_id, epi = speaker.resolve_speaker_from_content("", "嗯", "好", UID, CID)
    assert (spk_type, spk_id) == (None, None)
    assert epi == EPISTEMIC_FACT


# ---------------- extractor / nodes 调用公共函数 ----------------

def test_两条写入路径引用同一公共函数():
    from app.agent import nodes as nodes_mod
    assert nodes_mod.resolve_speaker_from_content is speaker.resolve_speaker_from_content
    assert extractor_mod.resolve_speaker_from_content is speaker.resolve_speaker_from_content


def test_extractor_resolve_speaker_委托公共函数(monkeypatch):
    called = []

    def _fake(content, user_msg, ai_msg, user_id, character_id):
        called.append((content, user_msg, ai_msg, user_id, character_id))
        return ("system", 0, "FACT")

    monkeypatch.setattr(extractor_mod, "resolve_speaker_from_content", _fake)
    out = _resolve_speaker("用户喜欢喝美式咖啡", "用户说喜欢", "记住啦", UID, CID)
    assert out == ("system", 0, "FACT")
    assert called == [("用户喜欢喝美式咖啡", "用户说喜欢", "记住啦", UID, CID)]


def test_extractor_resolve_speaker_结果与公共函数一致():
    for content, u, a in [
        ("我喜欢吃辣", "我今天想吃辣的", "好呀"),
        ("用户喜欢喝美式咖啡", "用户说喜欢喝美式", "记住啦"),
        ("我们一起看了海", "走吧", "好呀"),
        ("用户可能喜欢海边", "嗯", "好"),
        ("喜欢下雨天", "嗯", "我也是"),
    ]:
        assert _resolve_speaker(content, u, a, UID, CID) == speaker.resolve_speaker_from_content(content, u, a, UID, CID)


def test_nodes_generate_response_委托公共函数(monkeypatch):
    from app.agent import nodes as nodes_mod
    calls = []

    def _spy(content, user_msg, ai_msg, user_id, character_id):
        calls.append((content, user_msg, ai_msg, user_id, character_id))
        return ("character", 999, "INFERRED")

    monkeypatch.setattr(nodes_mod, "resolve_speaker_from_content", _spy)
    saved = []

    async def _fake_save(**kw):
        saved.append(kw)
        return None

    monkeypatch.setattr("app.memory.save_memory", _fake_save)

    async def _fake_llm(**kw):
        return "好呀～"

    monkeypatch.setattr(nodes_mod, "chat_completion", _fake_llm)

    def _fake_parse(response, state):
        state["ai_response"] = response
        state["new_memories"] = [{"type": "user_info", "title": "", "content": "用户喜欢喝美式咖啡", "importance": 3}]
        return state

    monkeypatch.setattr(nodes_mod, "parse_response", _fake_parse)

    async def _fake_cfg(user_id):
        return None

    monkeypatch.setattr("app.agent.llm_client.get_user_llm_config", _fake_cfg)

    state = {
        "user_id": UID, "character_id": CID,
        "user_message": "我喜欢喝美式咖啡", "context_messages": [],
        "temperature": 0.8, "reasoning_level": 0, "new_memories": [],
    }
    asyncio.run(nodes_mod.generate_response(state))
    assert calls, "标记路径应调用公共判定函数"
    content, user_msg, _, uid, cid = calls[0]
    assert content == "用户喜欢喝美式咖啡"
    assert user_msg == "我喜欢喝美式咖啡"
    assert (uid, cid) == (UID, CID)
    assert saved and saved[0]["speaker_type"] == "character"
    assert saved[0]["speaker_id"] == 999
    assert saved[0]["epistemic_status"] == "INFERRED"


# ---------------- 注入端 format_memory_line：include_speaker 纯函数 ----------------

def test_format_include_speaker_true_用户():
    line = format_memory_line({
        "content": "用户喜欢喝美式咖啡", "created_at": "2026-08-01",
        "epistemic_status": "FACT", "speaker_type": "user",
    }, include_speaker=True)
    assert line == "- [记录于 2026-08-01] [你说的] 用户喜欢喝美式咖啡"


def test_format_include_speaker_true_角色_认知前缀之后():
    # speaker 标注放内容前、认知前缀后
    line = format_memory_line({
        "content": "我喜欢吃辣", "created_at": "2026-08-01",
        "epistemic_status": "INFERRED", "speaker_type": "character",
    }, include_speaker=True)
    assert line == "- [记录于 2026-08-01] [INFERRED] [TA说的] 我喜欢吃辣"


def test_format_include_speaker_true_系统():
    line = format_memory_line({
        "content": "系统维护通知", "created_at": "2026-08-01", "speaker_type": "system",
    }, include_speaker=True)
    assert line == "- [记录于 2026-08-01] [系统说的] 系统维护通知"


def test_format_include_speaker_false_不带标注():
    m = {"content": "用户喜欢喝美式咖啡", "created_at": "2026-08-01", "speaker_type": "user"}
    assert format_memory_line(m) == "- [记录于 2026-08-01] 用户喜欢喝美式咖啡"
    assert format_memory_line(m, include_speaker=False) == "- [记录于 2026-08-01] 用户喜欢喝美式咖啡"


def test_format_include_speaker_true_无speaker字段不加():
    line = format_memory_line({
        "content": "用户喜欢喝美式咖啡", "created_at": "2026-08-01", "epistemic_status": "FACT",
    }, include_speaker=True)
    assert line == "- [记录于 2026-08-01] 用户喜欢喝美式咖啡"
    line2 = format_memory_line({
        "content": "用户喜欢喝美式咖啡", "created_at": "2026-08-01", "speaker_type": None,
    }, include_speaker=True)
    assert "[你说的]" not in line2 and "[TA说的]" not in line2 and "[系统说的]" not in line2


def test_format_叠加顺序_纠正后缀():
    line = format_memory_line({
        "content": "用户住在北京朝阳区", "created_at": "2026-08-01",
        "epistemic_status": "FACT", "reliability_score": 0.8,
        "contradiction_count": 1, "speaker_type": "user",
    }, include_speaker=True)
    assert line.startswith("- [记录于 2026-08-01] [你说的] 用户住在北京朝阳区")
    assert line.endswith("（你后来纠正过，以你最新说法为准）")


def test_format_叠加顺序_UNVERIFIED与speaker():
    line = format_memory_line({
        "content": "用户养了一只猫", "created_at": "2026-08-01",
        "epistemic_status": "UNVERIFIED", "reliability_score": 0.3, "speaker_type": "user",
    }, include_speaker=True)
    assert "[UNVERIFIED] [你说的] " in line


# ---------------- EXTRACT_PROMPT 主语强化 / context_builder 注入接线 ----------------

def test_extract_prompt_主语强化():
    assert "主语规则" in EXTRACT_PROMPT
    assert "禁止用裸「我」开头描述用户的事" in EXTRACT_PROMPT
    assert "「我们一起/我们」" in EXTRACT_PROMPT
    assert "「用户喜欢喝美式咖啡」" in EXTRACT_PROMPT
    assert "角色自己的设定与偏好用「我」开头" in EXTRACT_PROMPT
    assert "PREFERENCES 归属（2026-08-16 修复）" not in EXTRACT_PROMPT  # 旧段落已并入主语规则


def test_extract_prompt_描述视角_主语要求():
    assert "关于用户的信息必须用「用户」作主语" in EXTRACT_PROMPT
    assert "角色自己的内容用「我」第一人称" in EXTRACT_PROMPT


def test_context_builder_主链路注入启用include_speaker():
    from app.agent import context_builder as cb_mod
    # 步骤 4（注册表试水）：主组装逻辑保留在 build_context_legacy（公开入口 build_context 经
    # Feature Flag 路由到注册表或本回退函数），此处检查旧实现仍启用说话人标注。
    src = inspect.getsource(cb_mod.build_context_legacy)
    # X-4（2026-08-18）：检索区行构建抽至 _build_retrieved_memory_lines，仍启用说话人标注
    helper_src = inspect.getsource(cb_mod._build_retrieved_memory_lines)
    assert "format_memory_line(m, include_speaker=True)" in helper_src
    assert "memory_lines = _build_retrieved_memory_lines(" in src
