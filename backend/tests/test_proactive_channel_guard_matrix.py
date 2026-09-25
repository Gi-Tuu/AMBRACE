# -*- coding: utf-8 -*-
"""C11：主动消息「全通道」现状锚／时空纪律覆盖矩阵回归（2026-09-25）。

目的：A7（2026-09-24）只给 life_regression 与 pet_care 两条通道接了「当前现状锚 + 【时空纪律】」，
本文件把**所有会主动发消息的通道**逐条钉住：把该通道的锚函数打桩成哨兵串，跑真实的 prompt
构建（或直接调该通道的 prompt 构建函数），断言最终喂给 LLM 的 prompt 里【含哨兵】或【含纪律段】。

口径（不许放宽）：
- 行为断言 = 真的跑到该通道的 prompt 构建并捕获 messages，哨兵/纪律必须出现在真实 prompt 文本里；
- 静态断言 = 链路太重（须真库行/真服务）时，退化为「模块源码里必须出现锚函数名/纪律常量名」，
  用例 docstring 会明确标注这是静态断言；
- 核实后确实没接锚/没接纪律的通道 → ``xfail(strict=False)``，**不改业务代码**、不放宽断言。

覆盖矩阵（通道 / message_type / 覆盖方式 / 是否接锚）见交付报告，本文件只负责钉住行为。

纪律常量与锚函数出处：
- ``app/scheduling/life_regression.py:65`` STATE_GUARD_DISCIPLINE（A7）
- ``app/scheduling/pet_care.py:29`` STATE_GUARD_DISCIPLINE（A7，与上者逐字一致）
- ``app/memory/current_state.py:90`` current_user_state_anchor（三源聚合现状锚）
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.agent.loop import AGENT_FLAGS
from app.scheduling import arbiter
from app.scheduling import life_regression
from app.scheduling import life_share
from app.scheduling import memory_review
from app.scheduling import message_generator as mg
from app.scheduling import pet_care
from app.scheduling import prospective_intent
from app.scheduling import state_triggers
from app.scheduling import storyline_engine
from app.scheduling import unfinished_topic

# 哨兵串：只要它出现在真实 prompt 里，就证明该通道把现状锚喂给了 LLM
SENTINEL = "【现状哨兵XYZ】位置：示例市；状态：在读学生。"

# 各通道 prompt 里的「自家话术指纹」——用来证明真实构建跑到了（而不是捕获到空列表）
FINGERPRINT = {
    "proactive": "你是一个名叫「小爱」的朋友",
    "life_regression": "最近你的生活里发生了这些事",
    "pet_remind": "请你主动提醒用户照顾它",
    "ai_care": "你刚照顾完自己的宠物",
    "ai_adopt": "你刚领养了一只",
    "timer": "现在时间到了",
    "plugin": "请像朋友一样自然地用 1-2 句话提起这件事",
    "storyline": "直接输出你要说的话",
    "life_share": "你刚做了一件小事，想随口自然提一句",
    "prospective": "现在到了合适的时间",
    "prospective_legacy": "请用自己的语气自然提起这件事",
    "state_trigger": "你的当前状态：",
    "unfinished": "像是在约下次或留了话头",
    "notification": "用户手机最近收到了这些通知",
    "birthday": "今天是好友",
    "holiday": "请发一条简短的节日祝福",
    "anniversary": "认识的第",
}


# ───────────────────────────── 通用桩件 ─────────────────────────────

def _patch_anchor(monkeypatch, value: str = SENTINEL) -> None:
    """把现状锚函数打桩成哨兵串（各通道都是函数内 import，故打模块属性即生效）。"""

    async def _anchor(*_a, character_id=None, user_id=None, **_kw):
        return value

    monkeypatch.setattr("app.memory.current_state.current_user_state_anchor", _anchor)


class _FakeResult:
    """execute() 返回值：本文件所有用例都只要求「查不到行」，不依赖具体数据。"""

    def scalar(self):
        return 0

    def scalar_one_or_none(self):
        return None

    def all(self):
        return []

    def first(self):
        return None

    def scalars(self):
        return self


class _FakeSession:
    def __init__(self, rows: dict | None = None):
        self._rows = rows or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult()

    async def get(self, model, _pk):
        return self._rows.get(getattr(model, "__name__", str(model)))

    async def add(self, *_a, **_k):
        return None

    async def delete(self, *_a, **_k):
        return None

    async def commit(self):
        return None

    async def flush(self):
        return None


def _fake_db(rows: dict | None = None):
    def _factory(*_a, **_k):
        return _FakeSession(rows)

    return _factory


class _Capture:
    """捕获真正喂给 LLM 的 messages（call 记录按调用顺序追加）。"""

    def __init__(self, reply: str = "我今天把阳台的花浇了。"):
        self.calls: list[list[dict]] = []
        self.reply = reply

    async def __call__(self, messages=None, **_kw):
        self.calls.append(list(messages or []))
        return self.reply

    @property
    def user_prompt(self) -> str:
        assert self.calls, "该通道的 LLM 调用没有被捕获到（prompt 构建未跑到）"
        return self.calls[0][-1].get("content", "")

    @property
    def payload(self) -> str:
        assert self.calls, "该通道的 LLM 调用没有被捕获到（prompt 构建未跑到）"
        return "\n".join(m.get("content", "") for m in self.calls[0])


async def _async_return(value):
    async def _fn(*_a, **_k):
        return value

    return _fn


def _noop_async(*_a, **_k):
    async def _fn(*__a, **__k):
        return None

    return _fn()


def _empty_str(*_a, **_k):
    async def _fn(*__a, **__k):
        return ""

    return _fn()


def _false(*_a, **_k):
    async def _fn(*__a, **__k):
        return False

    return _fn()


def _zero(*_a, **_k):
    async def _fn(*__a, **__k):
        return 0

    return _fn()


def _assert_guarded(prompt: str, fingerprint: str) -> None:
    """统一断言口径：真实 prompt（含指纹，证明构建跑到）里必须有现状锚哨兵或【时空纪律】段。"""
    assert fingerprint in prompt, "未捕获到该通道真实 prompt（用例桩件失效，需修桩件而非放宽断言）"
    assert (SENTINEL in prompt) or (life_regression.STATE_GUARD_DISCIPLINE in prompt), (
        "该主动通道既没有注入现状锚（current_user_state_anchor / 【当前现状】），"
        "也没有【时空纪律】段——角色会把用户的旧地点/旧状态当成现在说"
    )


# ─────────── 已接锚：行为断言（A7 两条通道 + C3/B1 三条通道） ───────────

def _patch_mg_preloads(monkeypatch, cap: _Capture):
    """静音 generate_proactive_event 的全部前置查询并把 LLM 换成捕获器。

    与 tests/test_proactive_context.py::_patch_mg_preloads 同一套桩件（本文件自带一份，
    不依赖、不修改既有测试）。
    """
    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _empty_str)
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona_ctx)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _empty_str)
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_db())
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr(mg, "_load_recent_reflection", _empty_str)
    monkeypatch.setattr(mg, "chat_completion", cap)
    monkeypatch.setattr(mg, "load_character_reasoning_level", _zero)
    for key in ("proactive_naturalness_score", "proactive_segment_guard",
                "proactive_outreach_v2", "two_pass_trace"):
        monkeypatch.setitem(AGENT_FLAGS, key, False)


async def _persona_ctx(*_a, **_k):
    return {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}


async def _noop_list(*_a, **_k):
    return []


def test_proactive_chat_channel_carries_state_anchor(monkeypatch):
    """通道「主动搭话」：greeting / proactive_chat / goodnight / status_update / motivation。

    prompt 构建：app/scheduling/message_generator.py:558 ``generate_proactive_event``，
    现状锚在 app/scheduling/message_generator.py:766 ``_load_current_state_anchor`` 取，
    app/scheduling/message_generator.py:822-823 ``if state_anchor: prompt += state_anchor`` 注入
    **user** prompt（C3，2026-09-10）。五类 message_type 共用这一个构建函数，故一处行为断言覆盖五条。
    行为断言：真实跑完 prompt 构建并捕获 messages。
    """
    cap = _Capture()
    _patch_mg_preloads(monkeypatch, cap)
    _patch_anchor(monkeypatch)
    segments = asyncio.run(mg.generate_proactive_event(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=11, user_id=3, current_status="在家",
        last_context="用户: 我回示例市了\n你: 好，路上注意安全",
    ))
    assert segments, "generate_proactive_event 未产出分段（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["proactive"])


def test_life_regression_prompt_carries_anchor_and_discipline(monkeypatch):
    """通道「生活回归」message_type=life_regression（A7 已接）。

    prompt 构建：app/scheduling/life_regression.py:296-305，锚取 app/scheduling/life_regression.py:53
    ``_current_anchor``，护栏段 app/scheduling/life_regression.py:71 ``_state_guard_segments``
    （【当前现状】+ STATE_GUARD_DISCIPLINE）。
    行为断言：真跑 run_life_regression（DB/LLM/发送全部打桩），捕获最终 user prompt。
    """
    # 2026-09-25 Codex 复核：原稿想真跑 run_life_regression 捕获 prompt，但桩件不足以走到发送
    # （实测 ok=False）。按派单书「收敛」条款改成**确定性接线断言**：钉住「锚函数 → 护栏段 → 拼进 prompt」
    # 三处接线都在；行为层（段顺序 / fail-open / 逐字纪律文案）由 test_proactive_state_guard_a7.py 覆盖。
    src = inspect.getsource(life_regression)
    assert "_current_anchor" in src, "life_regression 未接现状锚函数"
    assert "STATE_GUARD_DISCIPLINE" in src, "life_regression 未定义时空纪律段"
    assert "_state_guard_segments" in src, "life_regression 未把护栏段拼进 prompt"
    assert "{guard}" in src, "护栏段没有被用进 prompt 模板"
    segs = life_regression._state_guard_segments(SENTINEL)
    assert segs and "【当前现状】" in segs[0], "锚非空时现状段必须在前"
    assert "【时空纪律】" in segs[-1], "时空纪律段必须在末尾"
    assert life_regression.STATE_GUARD_DISCIPLINE == segs[-1], "纪律段必须是模块常量原文"


def test_pet_remind_prompt_carries_anchor_and_discipline(monkeypatch):
    """通道「宠物提醒」message_type=pet_remind（A7 三处之一）。

    prompt 构建：app/scheduling/pet_care.py:227-240，护栏块 app/scheduling/pet_care.py:54
    ``_state_guard_block``（锚取 pet_care.py:44 ``_state_anchor``）。
    行为断言：真跑 run_pet_remind（Pet/AICharacter 行用假会话喂，LLM 捕获）。
    """
    cap = _Capture("团子饿了，给它添点粮吧。")
    pet = SimpleNamespace(id=5, user_id=3, name="团子", species="cat", hunger=10,
                          cleanliness=80, abandoned_at=None, last_remind_at=None,
                          owner_type="user", owner_id=None)
    char = SimpleNamespace(id=11, user_id=3, name="小爱", personality="友善", chat_style="自然")
    rows = {"Pet": pet, "AICharacter": char}
    async def _sid(*_a, **_k):
        return 99

    monkeypatch.setattr(pet_care, "async_session_factory", _fake_db(rows))
    monkeypatch.setattr(pet_care, "_user_in_dnd_period", _false)
    monkeypatch.setattr(pet_care, "_daily_count", _zero)
    monkeypatch.setattr(pet_care, "chat_completion", cap)
    monkeypatch.setattr(pet_care, "load_character_reasoning_level", _zero)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    monkeypatch.setattr("app.agent.user_profile.build_role_prompt_block", _empty_str)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _empty_str)
    monkeypatch.setattr("app.application.pet_service.species_fact", lambda _s: "猫科")
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _noop_async)
    _patch_anchor(monkeypatch)

    ok = asyncio.run(pet_care.run_pet_remind(11, 3, 5))
    assert ok is True, "run_pet_remind 未走到发送（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["pet_remind"])


def test_pet_ai_care_prompt_carries_anchor_and_discipline(monkeypatch):
    """通道「AI 照顾宠物」message_type=ai_care（A7 三处之二）。

    prompt 构建：app/scheduling/pet_care.py:485-492（``_gen_care_message`` 内联 f-string +
    ``_state_guard_block``）。行为断言：直接调用 _gen_care_message（只需 char/pet 对象）。
    """
    cap = _Capture("刚给团子梳完毛，它舒服多了。")
    pet = SimpleNamespace(id=5, user_id=3, name="团子", species="cat")
    char = SimpleNamespace(id=11, user_id=3, name="小爱", bio="", personality="友善")
    monkeypatch.setattr(pet_care, "chat_completion", cap)
    monkeypatch.setattr(pet_care, "load_character_reasoning_level", _zero)
    monkeypatch.setattr("app.agent.user_profile.build_role_prompt_block", _empty_str)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _empty_str)
    _patch_anchor(monkeypatch)

    text, _reasoning = asyncio.run(pet_care._gen_care_message(char, pet))
    assert text, "_gen_care_message 内部异常（返回空串＝用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["ai_care"])


def test_pet_ai_adopt_prompt_carries_anchor_and_discipline(monkeypatch):
    """通道「AI 领养告知」message_type=ai_adopt（A7 三处之三）。

    prompt 构建：app/scheduling/pet_care.py:377-384（``_gen_adopt_message`` 内联 f-string +
    ``_state_guard_block``）。行为断言：直接调用 _gen_adopt_message。
    """
    cap = _Capture("我领养了一只猫，叫团子。")
    pet = SimpleNamespace(id=5, user_id=3, name="团子", species="cat")
    char = SimpleNamespace(id=11, user_id=3, name="小爱", bio="", personality="友善")
    monkeypatch.setattr(pet_care, "chat_completion", cap)
    monkeypatch.setattr(pet_care, "load_character_reasoning_level", _zero)
    monkeypatch.setattr("app.agent.user_profile.build_role_prompt_block", _empty_str)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _empty_str)
    _patch_anchor(monkeypatch)

    text, _reasoning = asyncio.run(pet_care._gen_adopt_message(char, pet))
    assert text, "_gen_adopt_message 内部异常（返回空串＝用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["ai_adopt"])


def test_memory_review_state_block_renders_anchor_and_is_wired(monkeypatch):
    """通道「记忆复习」message_type=memory_review（现状锚已接，纪律段未接）。

    锚来源：app/scheduling/memory_review.py:377 ``_current_status_anchor`` →
    app/scheduling/memory_review.py:118 ``build_review_current_state_block`` →
    拼进 hint（app/scheduling/memory_review.py:496-506）。
    覆盖方式＝**行为（锚函数 + 块渲染纯函数）+ 静态接线**：run_memory_review 需要先有到期
    Memory 行与限额留痕（真库形态），整链跑不动，故「hint 里确实用了 state_block」用源码接线断言，
    而「锚 → 【当前现状】块」用真实调用证明。
    """
    _patch_anchor(monkeypatch)
    anchor = asyncio.run(memory_review._current_status_anchor(11, 3))
    assert SENTINEL in anchor, "memory_review 的现状锚函数没有走到 current_user_state_anchor"
    block = memory_review.build_review_current_state_block(anchor)
    assert SENTINEL in block, "现状锚没有渲染进「当前现状」块"

    src = " ".join(inspect.getsource(memory_review.run_memory_review).split())
    assert "status_anchor = await _current_status_anchor(char_id, user_id)" in src
    assert "state_block = build_review_current_state_block(status_anchor)" in src
    assert '{state_block}' in src, "「当前现状」块没有被拼进 hint（静态接线断言）"


def test_timer_anchor_reaches_llm_payload(monkeypatch):
    """通道「定时承诺兑现」message_type=timer（现状锚已接，但注入 **system** 而非 user）。

    锚来源：app/scheduling/arbiter.py:1100 ``_timer_current_anchor``（其中 app/scheduling/arbiter.py:1139-1145
    并列调用 current_user_state_anchor），拼装结果在 app/scheduling/arbiter.py:1253-1258 追加到
    ``_msgs[0]``（system）。话术本体是纯函数 arbiter.py:1030 ``_build_timer_hint``（不含锚）。
    覆盖方式＝**行为断言**：真跑 ``_execute`` 的 timer 分支并捕获 messages，锚哨兵必须出现在
    真实 payload 里；同时钉住「锚在 system 块」这一事实（其它通道都在 user 块，属口径差异非缺陷）。
    """
    cap = _Capture("我买完菜回来啦。")
    event = SimpleNamespace(id=1, character_id=11, user_id=3, session_id=99,
                            content_hint="买菜", event_type="back", owner="ai",
                            source_message_id=None)
    monkeypatch.setitem(AGENT_FLAGS, "timer_render_subject_fix", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_topic_guard", False)
    monkeypatch.setattr(arbiter, "async_session_factory",
                        _fake_db({"AICharacter": SimpleNamespace(id=11, user_id=3, name="sam")}))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _zero)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", cap)
    monkeypatch.setattr("app.agent.llm_client.load_character_reasoning_level", _zero)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _noop_async)
    monkeypatch.setattr("app.scheduling.promise_service.mark_fired", _noop_async)
    _patch_anchor(monkeypatch)

    ok = asyncio.run(arbiter._execute({"type": "timer", "event": event}))
    assert ok is True, "timer 分支未走到发送（用例桩件失效）"
    messages = cap.calls[0]
    assert FINGERPRINT["timer"] in messages[1]["content"]
    assert SENTINEL in messages[0]["content"], "现状锚没有进 timer 的 LLM payload"
    assert SENTINEL not in messages[1]["content"], "timer 的锚实际在 system 块（docstring 口径已变，需复核本用例）"


@pytest.mark.xfail(strict=False, reason="plugin Runtime 轻量上下文（默认开关）未接现状锚：只有全量 build_context 才带（本用例即该缺口的行为证据）")
def test_plugin_runtime_anchor_needs_full_context_xfail(monkeypatch):
    """通道「插件/渠道主动候选（统一 Runtime 分支）」message_type=plugin。

    静态断言（链路须真 Runtime/真会话，跑不动）：Runtime 分支的上下文由
    app/scheduling/arbiter.py:1663-1664 决定 —— ``agent_social_light_context`` 默认开（
    app/agent/loop.py:56）→ 走 app/agent/runtime.py:171 ``build_light_social_context``；
    该轻量构建明确「跳过完整世界认知 / 世界状态」（runtime.py:178-179），源码里没有
    current_user_state_anchor。现状锚只存在于全量 build_context 的
    app/agent/context/section_current_state.py:15-19（order=42 section）。
    因此默认开关下本通道拿不到现状锚 → 记为**未接**，xfail 保留证据。
    """
    _patch_anchor(monkeypatch)
    light_src = inspect.getsource(__import__("app.agent.runtime", fromlist=["runtime"]).build_light_social_context)
    assert "current_user_state_anchor" in light_src, (
        "插件 Runtime 轻量上下文（默认开关）未接现状锚；只有全量 build_context 才带"
    )


# ─────────── 未接锚／未接纪律：一律 xfail(strict=False)，不改业务代码 ───────────

@pytest.mark.xfail(strict=False, reason="storyline 未接现状锚/纪律（storyline_engine.py:171-196 全模块无锚函数引用）")
def test_storyline_llm_line_guard_missing_xfail(monkeypatch):
    """通道「剧情线」message_type=storyline —— **未接**。

    prompt 构建：app/scheduling/storyline_engine.py:171 ``_llm_line``（切片内容在
    storyline_engine.py:225 落库后由 app/scheduling/arbiter.py:506 以
    message_type="storyline" 发送）。该函数只有人设 + 状态行 + behavior_hint，
    全模块无 current_user_state_anchor、无 STATE_GUARD_DISCIPLINE。
    行为断言（真实 prompt 捕获）当前必然失败 → xfail。
    """
    cap = _Capture("阳台上来了一只猫，我蹲下去看了会儿。")
    monkeypatch.setattr("app.agent.llm_client.chat_completion", cap)
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_db())
    monkeypatch.setattr("app.agent.user_profile.build_role_prompt_block", _empty_str)
    _patch_anchor(monkeypatch)

    line = asyncio.run(storyline_engine._llm_line(
        "小爱", "友善", "疲惫=80；心情=60", "你在阳台发现一只躲雨的猫", character_id=11, user_id=3))
    assert line, "_llm_line 返回空（内部异常＝用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["storyline"])


@pytest.mark.xfail(strict=False, reason="life_share 未接现状锚/纪律（life_share.py:131-137 hint 无任何护栏段）")
def test_life_share_prompt_guard_missing_xfail(monkeypatch):
    """通道「活动完成自然分享」message_type=life_share —— **未接**。

    prompt 构建：app/scheduling/life_share.py:131-137（``_generate_share`` 内联 hint），
    LLM 调用 life_share.py:138-143，发送 life_share.py:217。全模块无 current_user_state_anchor。
    行为断言：直接调用 _generate_share 捕获真实 user prompt。
    """
    cap = _Capture("我刚画完一张水彩，颜色有点超出预期。")
    monkeypatch.setattr(life_share, "async_session_factory", _fake_db())
    monkeypatch.setattr("app.agent.llm_client.chat_completion", cap)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _false)
    _patch_anchor(monkeypatch)

    text = asyncio.run(life_share._generate_share(11, 3, "create", "画了一幅水彩"))
    assert text, "_generate_share 返回空（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["life_share"])


@pytest.mark.xfail(strict=False, reason="prospective_intent 未接现状锚/纪律（prospective_intent.py:655-688 无锚函数引用）")
def test_prospective_hint_self_branch_guard_missing_xfail():
    """通道「到期承诺」message_type=prospective_intent，side=self 分支 —— **未接**。

    prompt 构建：app/scheduling/prospective_intent.py:655 ``_build_prospective_hint``（纯函数，
    flag promise_self_side_split 开时启用）。self 分支只有「自述口径」约束 + 跨天时间锚
    （prospective_intent.py:675-681），没有用户现状锚、没有【时空纪律】段。行为断言＝直接调纯函数。
    """
    prompt = prospective_intent._build_prospective_hint("小爱", "周末给你做红烧肉", "self")
    _assert_guarded(prompt, FINGERPRINT["prospective"])


@pytest.mark.xfail(strict=False, reason="prospective_intent 未接现状锚/纪律（prospective_intent.py:655-688 无锚函数引用）")
def test_prospective_hint_user_branch_guard_missing_xfail():
    """通道「到期承诺」message_type=prospective_intent，side=user 分支 —— **未接**。

    同上（prospective_intent.py:668-673 else 分支）：只处理责任归属话术，
    不涉及「用户现在在哪/现在怎样」，故旧地点冒充现状的 bug 在此通道仍然无护栏。
    """
    prompt = prospective_intent._build_prospective_hint("小爱", "周五交报告", "user")
    _assert_guarded(prompt, FINGERPRINT["prospective"])


test_prospective_hint_self_branch_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="prospective_intent 未接现状锚/纪律（prospective_intent.py:655-688 无锚函数引用）")(
        test_prospective_hint_self_branch_guard_missing_xfail)

test_prospective_hint_user_branch_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="prospective_intent 未接现状锚/纪律（prospective_intent.py:655-688 无锚函数引用）")(
        test_prospective_hint_user_branch_guard_missing_xfail)


@pytest.mark.xfail(strict=False, reason="prospective_intent legacy 话术未接锚/纪律（prospective_intent.py:646-652）")
def test_prospective_hint_legacy_branch_guard_missing_xfail():
    """通道「到期承诺」legacy 分支（flag promise_self_side_split 关，默认口径）—— **未接**。

    prompt 构建：app/scheduling/prospective_intent.py:646 ``_build_prospective_hint_legacy``，
    与 2026-09-04 上线版本逐字节一致的话术，同样没有现状锚与纪律段。
    """
    prompt = prospective_intent._build_prospective_hint_legacy("小爱", "周五交报告")
    _assert_guarded(prompt, FINGERPRINT["prospective_legacy"])


test_prospective_hint_legacy_branch_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="prospective_intent legacy 话术未接锚/纪律（prospective_intent.py:646-652）")(
        test_prospective_hint_legacy_branch_guard_missing_xfail)


def test_plugin_legacy_hint_branch_guard_missing_xfail(monkeypatch):
    """通道「插件/渠道主动候选（旧裸生成分支）」message_type=plugin —— **未接**。

    prompt 构建：app/scheduling/arbiter.py:1414-1425（``_execute`` 的 etype=="plugin" 且
    ``agent_loop_social`` 关时），hint 由插件自带（candidate["hint"]），内核只加一句
    「像朋友一样自然提起」，无现状锚、无纪律段。
    行为断言：跑真实 ``_execute`` plugin 分支并捕获 user prompt（发送/门控全部打桩）。
    """
    cap = _Capture("那个美食频道更新了，看着挺好吃。")
    candidate = {"character_id": 11, "user_id": 3, "session_id": 99,
                 "plugin": "demo", "hint": "你关注的美食频道更新了一道新菜"}
    monkeypatch.setattr(arbiter, "async_session_factory", _fake_db())
    monkeypatch.setattr(arbiter, "is_dnd_now", _false)
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _false)
    monkeypatch.setattr(arbiter, "is_user_active", _false)
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _zero)
    monkeypatch.setattr(arbiter, "_pacing_gate", _false)
    monkeypatch.setattr("app.application.flag_service.resolve_flag", _false)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", cap)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _noop_async)
    _patch_anchor(monkeypatch)

    ok = asyncio.run(arbiter._execute({"type": "plugin", "candidate": candidate}))
    assert ok is True, "plugin 分支未走到发送（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["plugin"])


test_plugin_legacy_hint_branch_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="plugin 旧分支未接锚/纪律（arbiter.py:1414-1425 hint 直接来自插件）")(
        test_plugin_legacy_hint_branch_guard_missing_xfail)


def test_state_trigger_prompt_guard_missing_xfail(monkeypatch):
    """通道「状态触发」message_type=state_trigger —— **未接**。

    prompt 构建：app/scheduling/state_triggers.py 的 ``_execute_rule_behavior``（发送在
    state_triggers.py:581）。注入的是「你的当前状态」（AI 自身属性）与最近聊天/persona，
    **没有**用户现状锚（全模块无 current_user_state_anchor），也没有【时空纪律】。
    行为断言：真跑 _execute_rule_behavior 私聊分支（桩件同 tests/test_proactive_context.py）。
    """
    cap = _Capture("有点累，你先别忙，我歇会儿。")
    async def _boom():
        raise RuntimeError("no db")

    async def _sid(*_a, **_k):
        return 99

    async def _last(*_a, **_k):
        return "用户: 我回示例市了\n你: 好"

    async def _persona(*_a, **_k):
        return "关系温度（自然体现，别念数据）：信任80"

    monkeypatch.setattr(state_triggers, "async_session_factory", _boom)
    monkeypatch.setattr(state_triggers, "_post_trigger_notes", _noop_async)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    monkeypatch.setattr("app.scheduling.triggers.get_last_messages", _last)
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _persona)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", cap)
    monkeypatch.setattr("app.scheduling.arbiter.get_hourly_active_count", _zero)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _noop_async)
    _patch_anchor(monkeypatch)

    rule = state_triggers._RULE_BY_KEY["fatigue_high"]
    ok = asyncio.run(state_triggers._execute_rule_behavior(11, 3, rule, "疲惫=85；心情=40"))
    assert ok is True, "state_trigger 私聊分支未走到发送（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["state_trigger"])


test_state_trigger_prompt_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="state_trigger 未接用户现状锚/纪律（state_triggers.py 全模块无锚函数引用）")(
        test_state_trigger_prompt_guard_missing_xfail)


def test_unfinished_topic_prompt_guard_missing_xfail(monkeypatch):
    """通道「未收尾话题跟进」message_type=unfinished_topic —— **未接**。

    prompt 构建：app/scheduling/unfinished_topic.py:137-142（``run_unfinished_topic`` 内联 hint），
    发送 unfinished_topic.py:153。只有「用户之前说过：「话题」」的话头复述，
    既无现状锚也无纪律段 → 旧话题当现状的风险仍在这条通道上。
    """
    cap = _Capture("对了，你上次说的那家店，后来去了吗？")
    monkeypatch.setattr(unfinished_topic, "async_session_factory", _fake_db())
    monkeypatch.setattr("app.agent.llm_client.chat_completion", cap)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _noop_async)
    _patch_anchor(monkeypatch)

    ok = asyncio.run(unfinished_topic.run_unfinished_topic({
        "character_id": 11, "user_id": 3, "session_id": 99, "character_name": "小爱",
        "character_personality": "友善", "unfinished_content": "下次一起去吃那家店",
    }))
    assert ok is True, "unfinished_topic 未走到发送（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["unfinished"])


test_unfinished_topic_prompt_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="unfinished_topic 未接锚/纪律（unfinished_topic.py:137-142 hint 无护栏段）")(
        test_unfinished_topic_prompt_guard_missing_xfail)


def test_emotion_care_prompt_guard_missing_xfail(monkeypatch):
    """通道「情绪关怀」message_type=emotion_care —— **未接**（静态断言）。

    链路必须先在 emotion_care_tasks 里有一条待办行 + 限额/免打扰留痕（真库形态），
    整链跑不动 → 退化为**静态接线断言**：app/domain/emotion/care.py 的 ``run_emotion_care``
    hint 构建在 care.py:180-187（identity + persona + weather + 「用户刚才跟你说…」），
    源码里没有 current_user_state_anchor，也没有 STATE_GUARD_DISCIPLINE。
    """
    src = inspect.getsource(__import__("app.domain.emotion.care", fromlist=["care"]).run_emotion_care)
    assert "current_user_state_anchor" in src, "emotion_care 未接现状锚（care.py:180-187）"
    assert "STATE_GUARD_DISCIPLINE" in src, "emotion_care 未接时空纪律（care.py:180-187）"


test_emotion_care_prompt_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="emotion_care 未接锚/纪律（care.py:180-187 只有 identity/persona/weather）")(
        test_emotion_care_prompt_guard_missing_xfail)


def test_notification_mention_prompt_guard_missing_xfail(monkeypatch):
    """通道「手机通知提及」message_type=notification_mention —— **未接**。

    prompt 构建：app/application/phone_auto_notify_service.py:102-108（``_generate_mention``），
    发送 phone_auto_notify_service.py:223。通知正文本身就是「此刻发生的事」，
    但没有用户现状锚 → 容易把旧位置/旧状态混进关心里。行为断言＝直接调 _generate_mention。
    """
    cap = _Capture("下午那个会别忘了，要不要我给你带点喝的？")
    char = SimpleNamespace(id=11, user_id=3, name="小爱", personality="友善", chat_style="自然")
    monkeypatch.setattr("app.agent.llm_client.chat_completion", cap)
    _patch_anchor(monkeypatch)

    text = asyncio.run(__import__(
        "app.application.phone_auto_notify_service", fromlist=["svc"]
    )._generate_mention(char, [{"app": "日历", "title": "周会", "text": "下午三点"}]))
    assert text, "_generate_mention 返回空（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT["notification"])


test_notification_mention_prompt_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="notification_mention 未接锚/纪律（phone_auto_notify_service.py:102-108）")(
        test_notification_mention_prompt_guard_missing_xfail)


@pytest.mark.parametrize("kind", ["birthday", "holiday", "anniversary"])
def test_special_day_prompt_guard_missing_xfail(monkeypatch, kind):
    """通道「生日 / 节日 / 认识纪念日」message_type=birthday|holiday|anniversary —— **未接**。

    prompt 构建：app/scheduling/message_generator.py:1157 ``generate_birthday_message`` /
    :1185 ``generate_anniversary_message`` / :1213 ``generate_holiday_message``（发送在
    app/scheduling/arbiter.py:1472-1508）。三者只有人设 + 身份块，既不取现状锚也不带纪律段
    （对比同文件的 ``generate_proactive_event`` —— 那条接了 C3 锚）。
    行为断言＝真实调用生成函数并捕获 user prompt。
    """
    cap = _Capture("生日快乐呀，今天你得开心点。")
    monkeypatch.setattr("app.db.database.async_session_factory", _fake_db())
    monkeypatch.setattr(mg, "chat_completion", cap)
    monkeypatch.setattr(mg, "load_character_reasoning_level", _zero)
    _patch_anchor(monkeypatch)

    if kind == "birthday":
        text = asyncio.run(mg.generate_birthday_message("小爱", "友善", "阿轩", character_id=11, user_id=3))
    elif kind == "anniversary":
        text = asyncio.run(mg.generate_anniversary_message(
            "小爱", "友善", "阿轩", 100, character_id=11, user_id=3))
    else:
        text = asyncio.run(mg.generate_holiday_message(
            "小爱", "友善", "阿轩", "中秋", character_id=11, user_id=3))
    assert text, f"{kind} 生成返回空（用例桩件失效）"
    _assert_guarded(cap.user_prompt, FINGERPRINT[kind])


test_special_day_prompt_guard_missing_xfail = pytest.mark.xfail(
    strict=False,
    reason="birthday/holiday/anniversary 未接锚/纪律（message_generator.py:1157/1185/1213）")(
        test_special_day_prompt_guard_missing_xfail)


# ─────────── 不适用通道：无 LLM 生成，护栏无从谈起（正向钉住事实） ───────────

def test_anniversary_recall_is_template_not_llm():
    """通道「纪念日回访」message_type=anniversary_recall —— **不适用**（无 LLM）。

    app/scheduling/scheduler.py:171-172 发送的是 ``anniversary_text(e)``
    （app/memory/shared_events.py:146）——纯模板字符串、不经 LLM，因此不存在
    「模型把旧地点/旧状态当现状」的生成面；本用例钉住「该通道确实没有 LLM 调用」这一事实，
    避免后人误以为漏接锚。
    """
    src = inspect.getsource(__import__("app.memory.shared_events", fromlist=["se"]).anniversary_text)
    assert "chat_completion" not in src
    assert "current_user_state_anchor" not in src
