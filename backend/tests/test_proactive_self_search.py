# -*- coding: utf-8 -*-
"""S1 第二步：主动消息链「角色自主搜索」（proactive_self_search，默认关）。

口径（用户 2026-09-27 拍板）：
1. 开放 [SEARCH] 给主动链，但新开关控制、默认关（关＝逐字节旧行为）；
2. 搜索失败 / 被节流 ⇒ 仍发原候选（不引入新的静默路径）；
3. 「不说」只在确实搜过一次、再生成正文为空时放行 return []（走 segments 为空不发送那条路），
   message_generator 里 6 处 segments 占位兜底（内容为省略号）一律不动；
4. 本批不落小手机浏览记录。

接线复用 agent/loop.py run_search_loop 的 self 分支语义，唯一差异＝regen 走 message_generator 的
_gen_with_reasoning（task="message"、按角色思考挡位），不走 nodes.generate_response。

集成用例只 patch 前置查询与 LLM/搜索原语，不触生产库。
"""
import asyncio

import pytest

from app.scheduling import message_generator as mg
from app.agent.loop import AGENT_FLAGS
import app.application.chat.tools as chat_tools

pytestmark = pytest.mark.slow

# 首轮模型输出：正文 + 一个自主搜索标记
_R1 = "我刚想到个事[SEARCH]某明星今年多大[/SEARCH]"
_R1_CLEAN = "我刚想到个事"          # extract_search 剥离标记后的正文（＝原候选）
# 搜索成功后「说」的再生成
_REGEN_SAY = "查到了，他今年三十啦"
# 搜索成功后「什么都不说」的再生成（空正文）
_REGEN_SILENT = ""


class _FakeResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


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


# 间谍必须永远委托到「真身」（导入期抓取）：一个用例里连开两个 harness 时，
# 若第二个抓当时的 mg 属性，会抓到第一个装上去的间谍，计数串台。
_REAL_PSS = mg._proactive_self_search


class _Harness:
    """patch 前置查询 + LLM + 搜索原语 + _proactive_self_search 间谍；暴露各调用计数。"""

    def __init__(self, monkeypatch, responses, *, search_result="搜索结果若干", throttle=True, inject=True):
        self.gen_calls = []        # 每次 _gen_with_reasoning 收到的 messages
        self.search_calls = []     # 每次 _run_web_search 收到的 query
        self.pss_calls = {"n": 0}  # _proactive_self_search 被调用次数
        self._seq = list(responses)

        async def _noop(*_a, **_k):
            return ""

        async def _noop_list(*_a, **_k):
            return []

        async def _persona(*_a, **_k):
            return {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}

        async def _anchor(**_kw):
            return ""

        async def _fake_gen(messages, character_id, user_id, *, temperature, max_tokens, **_kw):
            self.gen_calls.append(list(messages))
            idx = min(len(self.gen_calls) - 1, len(self._seq) - 1)
            return self._seq[idx], ""

        async def _fake_search(query, *a, **k):
            self.search_calls.append(query)
            return search_result

        real_pss = _REAL_PSS

        async def _spy_pss(response, **kw):
            self.pss_calls["n"] += 1
            return await real_pss(response, **kw)

        # ── 前置查询（沿用 test_proactive_segment_guard 已验证的最小 patch 集，不触生产库）──
        monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _noop)
        monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
        monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop)
        monkeypatch.setattr("app.db.database.async_session_factory", _fake_session_factory)
        monkeypatch.setattr("app.memory.search_memories", _noop_list)
        monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
        monkeypatch.setattr("app.memory.current_state.current_user_state_anchor", _anchor)
        # ── LLM 与搜索原语 ──
        monkeypatch.setattr(mg, "_gen_with_reasoning", _fake_gen)
        monkeypatch.setattr(chat_tools, "_run_web_search", _fake_search)
        monkeypatch.setattr(chat_tools, "_search_throttle", lambda _uid, *a, **k: throttle)
        monkeypatch.setattr(chat_tools, "_search_inject_enabled", lambda: inject)
        # ── 自主搜索接线间谍（仍真实执行）──
        monkeypatch.setattr(mg, "_proactive_self_search", _spy_pss)
        # ── 稳定性：关掉自然度评分 / 分块护栏，避免额外重试干扰计数 ──
        monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
        monkeypatch.setitem(AGENT_FLAGS, "proactive_segment_guard", False)


def _run(**kw):
    gen_kw = dict(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=1, user_id=1, current_status="在家",
        last_context="用户: 今天好累\n你: 早点休息",
    )
    gen_kw.update(kw)
    return asyncio.run(mg.generate_proactive_event(**gen_kw))


# ────────────────────────── 1. 开关默认关 + 目录有条目 ──────────────────────────

def test_开关登记且默认关():
    assert "proactive_self_search" in AGENT_FLAGS
    assert AGENT_FLAGS["proactive_self_search"] is False


def test_开关目录有条目且文案非空():
    from app.application.flag_catalog import FLAG_CATALOG, meta_for
    assert "proactive_self_search" in FLAG_CATALOG
    m = FLAG_CATALOG["proactive_self_search"]
    assert m["group"] == "proactive" and m["order"] == 209 and m["visible"] is False
    for lang in ("zh", "en"):
        got = meta_for("proactive_self_search", lang)
        assert got["title"].strip() and got["desc"].strip(), f'{lang} 文案为空'


# ────────────────────────── 2. 开关关：只走旧路、绝不搜索 ──────────────────────────

def test_关_含SEARCH也不搜索且逐字节旧行为(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", False)
    h = _Harness(monkeypatch, [_R1])
    segs = _run()
    # 自主搜索接线整段不触发：不调 _proactive_self_search、不调 _run_web_search、只 1 次 LLM
    assert h.pss_calls["n"] == 0
    assert h.search_calls == []
    assert len(h.gen_calls) == 1
    # 旧行为：主动链从不识别 [SEARCH] ⇒ 标记原样留在正文里（未被搜索分支处理/剥离）
    joined = "".join(segs)
    assert "[SEARCH]" in joined and _R1_CLEAN in joined


def test_关_普通输出与开而无标记时逐字节一致(monkeypatch):
    # 无 [SEARCH] 时，开关开＝开关关（无标记 ⇒ 搜索分支惰性，输出不变）
    normal = "刚下课回来\n你那边忙完了吗？"
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", False)
    h_off = _Harness(monkeypatch, [normal])
    off = _run()
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    h_on = _Harness(monkeypatch, [normal])
    on = _run()
    assert off == on, '无标记时开关开/关输出必须一致'
    assert off, '两侧都空会让断言失效'
    # 关：整条链路不碰搜索原语、不多调 LLM
    assert h_off.pss_calls["n"] == 0 and h_off.search_calls == []
    assert len(h_off.gen_calls) == len(h_on.gen_calls) == 1


def test_开_但无SEARCH_搜索分支惰性不搜索(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    h = _Harness(monkeypatch, ["刚下课回来，你那边忙完了吗？"])
    segs = _run()
    # 无标记时 helper 内部 extract_search 查不到 query ⇒ 不发起搜索；仅 1 次 LLM
    assert h.search_calls == []
    assert len(h.gen_calls) == 1
    assert segs and "".join(segs) == "刚下课回来，你那边忙完了吗？"


# ────────────────────────── 3. 开关开：self 分支「说」→ 正常出消息 ──────────────────────────

def test_开_有结果且选择说_正常出消息(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    h = _Harness(monkeypatch, [_R1, _REGEN_SAY], search_result="他出生于1995年，今年30岁")
    segs = _run()
    # 首轮命中 [SEARCH] → 自主搜索接线被调用、搜索真实发起一次
    assert h.pss_calls["n"] == 1
    assert h.search_calls == ["某明星今年多大"]
    # regen 走 _gen_with_reasoning：注入的那轮 messages 末尾是 self 口径模板（只作参考/没人等回复）
    regen_msgs = h.gen_calls[1]
    assert "没有人在等你回复" in regen_msgs[-1]["content"] and "只作参考" in regen_msgs[-1]["content"]
    # 正常产出消息（基于结果的正文），且不残留标记
    assert "".join(segs) == _REGEN_SAY
    assert "[SEARCH]" not in "".join(segs)


# ────────────────────────── 4. 开关开：self 分支「不说」→ 不发送（走空 segments） ──────────────────────────

def test_开_选择不说_走空segments不发送(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    h = _Harness(monkeypatch, [_R1, _REGEN_SILENT], search_result="他出生于1995年，今年30岁")
    segs = _run()
    # 搜索确实发生、模型选择「什么都不说」⇒ 本轮不产出消息
    assert h.pss_calls["n"] == 1 and h.search_calls == ["某明星今年多大"]
    # 走「segments 为空则不发送」那条路：返回空列表，而不是空串/省略号消息
    assert segs == []
    assert segs != ["……"] and segs != [""]


def test_开_选择不说_return_reasoning形态也返回空段(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    _Harness(monkeypatch, [_R1, _REGEN_SILENT], search_result="结果")
    segs, reasoning = _run(return_reasoning=True)
    assert segs == []


# ────────────────────────── 5. 开关开：搜索失败 / 被节流 ⇒ 仍发原候选 ──────────────────────────

def test_开_搜索失败_仍发原候选(monkeypatch):
    from app.agent.loop import SEARCH_RETRY
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    h = _Harness(monkeypatch, [_R1, _REGEN_SAY], search_result="")  # 搜索始终返回空
    segs = _run()
    # 发起过搜索但没拿到结果 ⇒ 不套用「不说」，回落到原候选（剥离标记后的首轮正文）
    assert len(h.search_calls) == SEARCH_RETRY + 1 == 2      # 首轮 + 重试 1 次（与 self/user 分支同策略）
    assert set(h.search_calls) == {"某明星今年多大"}
    assert segs == [_R1_CLEAN]
    # 搜索失败不触发 regen（不会消费第二条 LLM 输出）
    assert len(h.gen_calls) == 1


def test_开_被节流_仍发原候选且不搜索(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    h = _Harness(monkeypatch, [_R1, _REGEN_SAY], throttle=False)
    segs = _run()
    # 节流不过 ⇒ 连搜索都不发起，但仍把原候选发出去（不静默）
    assert h.pss_calls["n"] == 1
    assert h.search_calls == []
    assert segs == [_R1_CLEAN]


def test_开_注入开关关_仍发原候选(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "proactive_self_search", True)
    h = _Harness(monkeypatch, [_R1, _REGEN_SAY], inject=False)
    segs = _run()
    assert h.search_calls == []
    assert segs == [_R1_CLEAN]


# ────────────────────────── 6. 6 处 segments 占位兜底未被改动 ──────────────────────────
# 选择「源码级断言」：占位兜底是「内容为省略号」的固定字面量，源码计数最稳、
# 不依赖具体 LLM 输入路径（行为断言只能覆盖其中一条路径，无法钉住「6 处一律不动」）。

def test_六处省略号占位兜底未被改动():
    import io
    import os
    path = os.path.join(os.path.dirname(mg.__file__), "message_generator.py")
    src = io.open(path, encoding="utf-8").read()
    # 6 处「内容为省略号」的 segments 占位兜底（5 处直接赋值 + 1 处 or 兜底）
    assert src.count('["……"]') == 6, '省略号占位兜底处数变了（应恒为 6，本批不得增删/改写）'
    # 本批新增的自主搜索接线里绝不出现省略号占位，也不得返回空串消息
    # 2026-10-02 A22 ③b 锚点迁移：_proactive_self_search 已搬入 message_llm.py ⇒ 该切片改读新模块；
    # 断言原意不变（仍是「这个函数体内不得出现省略号占位」）；上面 mg 侧 6 处计数断言未动。
    import app.scheduling.message_llm as _llm_mod
    llm_src = io.open(_llm_mod.__file__, encoding="utf-8").read()
    _seg_at = llm_src.index("async def _proactive_self_search")
    import ast as _ast
    _fn = next(n for n in _ast.parse(llm_src).body
               if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
               and n.name == "_proactive_self_search")
    seg = chr(10).join(llm_src.splitlines()[_fn.lineno - 1:_fn.end_lineno])
    assert "……" not in seg
    wiring = src[src.index("if _self_search_on and attempt == 0:"):]
    wiring = wiring[:wiring.index("if _guard_on:")]
    assert "……" not in wiring and '[""]' not in wiring
