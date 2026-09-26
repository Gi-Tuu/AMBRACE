# -*- coding: utf-8 -*-
"""两遍重读「判定点三态留痕 + 入口计数」（2026-09-26 派单 Part B）。

要解决的问题：注入留痕（``route=two_pass_trace``）只在「命中且有 trace」时才写 ⇒
「开关没开/白名单没命中所以没跑」与「跑了但 trace 拼空」在数据上完全同形（都＝没有留痕）。
新增 ``route=two_pass_gate``：**每次 generate_proactive_event 恰好一条**，state 三值区分
not_allowed / empty_trace / injected，直接给出分母（这一拍被调用了几次）。

守的底线（禁止为绿放宽）：
1. 复用同一观测通道（obs_event → agent_task_logs，trigger=memory_obs），只换 route，不另起一套；
2. **548 行既有留痕逐字不动**：字段仍是 {enabled, trace_len, trace_sha8, prompt_len, elapsed_ms}，
   且仍只在「有 trace」时写（不因为新留痕而多写/少写）；
3. 生成结果逐字不变：返回的 segments 与送 LLM 的 messages 在「留痕正常 / 留痕炸掉」两态下全等；
4. 不新增 LLM 调用；未命中时仍不构造 trace（「不多一次查询」的语义保持）；
5. fail-open：留痕写失败只记 WARNING，绝不冒泡。

链路打桩直接复用 test_two_pass_trace（同一套 patch 口径，禁止复制第二套）。
"""
import json

import pytest

from app.scheduling import message_generator as mg
from test_two_pass_trace import (  # 复用既有打桩与常量，不复制第二套
    _CHAR, _MARK, _OTHER_CHAR, _USER, _patch_pipeline, _resp, _run, _trace_ok)

pytestmark = pytest.mark.slow

_GATE = "two_pass_gate"
_INJ = "two_pass_trace"


class _Loud:
    """捕获 WARNING，其余属性透传给真实 logger（不改日志行为）。"""

    def __init__(self, inner):
        self._inner = inner
        self.lines: list[str] = []

    def warning(self, msg, *a):
        self.lines.append(str(msg) % a if a else str(msg))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _trace_text():
    return f"【当前现状速读】\n- 状态：在加班{_MARK}"


@pytest.fixture()
def obs_events(monkeypatch):
    """捕获 obs_event → enqueue_task_log 的入参（与既有注入留痕同一通道），并保证观测开关开着。"""
    from app.agent.loop import AGENT_FLAGS
    assert AGENT_FLAGS.get("memory_trace_debug") is True, "该通道默认开（现状）"
    monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", True)
    got: list[dict] = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: got.append(kw))
    return got


def _rows(got, route):
    return [json.loads(e["steps_json"]) for e in got if e.get("route") == route]


def _gates(got):
    return _rows(got, _GATE)


def _flags(monkeypatch, on: bool):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", on)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)


async def _empty_loader(_cid, _uid):
    return "", 3.0, False    # 批次 B：第三位 False＝「真·拼空」（非异常）


async def _error_loader(_cid, _uid):
    return "", 0.0, True     # 批次 B：第三位 True＝查库/构造异常被 fail-open 吞掉


# ────────────────────────── ① 三态各自可辨 ──────────────────────────

def test_开关关记not_allowed_且仍不构造trace(monkeypatch, obs_events):
    _flags(monkeypatch, False)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()])
    assert _run(_CHAR)

    gates = _gates(obs_events)
    assert len(gates) == 1, "每次调用恰好一条 gate（入口计数）"
    assert gates[0]["state"] == "not_allowed" and gates[0]["allowed"] is False
    assert gates[0]["trace_len"] == 0
    assert _rows(obs_events, _INJ) == [], "未命中不得有注入留痕（原语义不变）"
    assert captured["loader"] == [], "未命中仍然不构造 trace（不多一次查询）"


def test_白名单外角色记not_allowed(monkeypatch, obs_events):
    _flags(monkeypatch, True)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()])
    assert _run(_OTHER_CHAR)

    gates = _gates(obs_events)
    assert len(gates) == 1 and gates[0]["state"] == "not_allowed"
    assert gates[0]["allowed"] is False and captured["loader"] == []
    assert [e["character_id"] for e in obs_events if e.get("route") == _GATE] == [_OTHER_CHAR]


def test_命中但trace拼空记empty_trace_异常情形一眼可见(monkeypatch, obs_events):
    _flags(monkeypatch, True)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_empty_loader)
    assert _run(_CHAR)

    gates = _gates(obs_events)
    assert len(gates) == 1 and gates[0]["state"] == "empty_trace"
    assert gates[0]["allowed"] is True and gates[0]["trace_len"] == 0, "跑到了但拼空＝这一型"
    assert _rows(obs_events, _INJ) == [], "拼空时仍不写注入留痕（548 行语义不动）"
    assert [m["role"] for m in captured["messages"][0]] == ["system", "user"], "不注入＝旧行为"


def test_查库失败记trace_error_与拼空可区分(monkeypatch, obs_events):
    """批次 B（低-5）：fail-open 吞掉的构造异常必须与「真·拼空」在留痕上分开。"""
    _flags(monkeypatch, True)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_error_loader)
    assert _run(_CHAR)

    gates = _gates(obs_events)
    assert len(gates) == 1 and gates[0]["state"] == "trace_error"
    assert gates[0]["allowed"] is True and gates[0]["trace_len"] == 0
    assert _rows(obs_events, _INJ) == [], "异常时空串，照旧不注入"
    assert [m["role"] for m in captured["messages"][0]] == ["system", "user"], "不注入＝旧行为"


def test_命中且有trace记injected_且既有留痕字段逐字不变(monkeypatch, obs_events):
    _flags(monkeypatch, True)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_trace_ok)
    assert _run(_CHAR)

    gates = _gates(obs_events)
    assert len(gates) == 1 and gates[0]["state"] == "injected" and gates[0]["allowed"] is True
    assert gates[0]["trace_len"] == len(_trace_text()) and gates[0]["elapsed_ms"] == 12.0

    injs = _rows(obs_events, _INJ)
    assert len(injs) == 1, "注入留痕仍恰好一条（不因新留痕而多写）"
    assert set(injs[0]) == {"enabled", "trace_len", "trace_sha8", "prompt_len", "elapsed_ms"}, \
        f"既有留痕字段集合必须逐字不变：{set(injs[0])}"
    assert injs[0]["enabled"] is True and injs[0]["trace_len"] == len(_trace_text())
    assert _MARK not in json.dumps(injs[0], ensure_ascii=False), "trace 正文不得落库"
    assert captured["messages"][0][0] == {"role": "system", "content": _trace_text()}, "注入位置不变"


# ────────────────────────── ② 入口计数（分母/分子可比） ──────────────────────────

def test_三次调用三条gate_其中两次拼空仍可直接对比注入率(monkeypatch, obs_events):
    """gate 计数＝调用次数（与 trace 是否为空无关）；注入留痕＝真注入次数 ⇒ 两者相除即注入率。"""
    _flags(monkeypatch, True)
    for _ in range(2):
        _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_empty_loader)
        assert _run(_CHAR)
    _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_trace_ok)
    assert _run(_CHAR)

    gates = _gates(obs_events)
    assert [g["state"] for g in gates] == ["empty_trace", "empty_trace", "injected"], f"{gates}"
    assert len(_rows(obs_events, _INJ)) == 1, "分子仍只有真注入那一次"


def test_gate轻量字段固定四个且不落trace正文(monkeypatch, obs_events):
    _flags(monkeypatch, True)
    _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_trace_ok)
    _run(_CHAR)

    ev = [e for e in obs_events if e.get("route") == _GATE][0]
    assert ev["trigger"] == "memory_obs" and ev["character_id"] == _CHAR
    assert ev["task_id"], "沿用 obs_event 的 task_id（同一写入通道，不另起一套）"
    detail = json.loads(ev["steps_json"])
    assert set(detail) == {"state", "allowed", "trace_len", "elapsed_ms"}, f"轻量留痕字段：{set(detail)}"
    assert len(ev["steps_json"]) <= 1600 and _MARK not in ev["steps_json"]


# ────────────────────────── ③ fail-open：留痕炸了不影响生成 ──────────────────────────

def _run_pair(monkeypatch, *, trace_loader):
    """跑一次生成，返回 (segments, 送 LLM 的 messages)。"""
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=trace_loader)
    return _run(_CHAR), captured["messages"][0]


def test_留痕通道抛异常_生成结果与messages逐字不变(monkeypatch, obs_events):
    from app.memory import observability as obs_mod
    _flags(monkeypatch, True)

    base_segs, base_msgs = _run_pair(monkeypatch, trace_loader=_trace_ok)
    assert _gates(obs_events) == [{"state": "injected", "allowed": True,
                                   "trace_len": len(_trace_text()), "elapsed_ms": 12.0}]

    # ① 写入通道炸（obs_event 内部 catch ⇒ WARNING，不打扰生成链路）
    loud_obs = _Loud(obs_mod._logger)
    monkeypatch.setattr(obs_mod, "_logger", loud_obs)

    def _boom(**_kw):
        raise RuntimeError("trace down")
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _boom)
    segs, msgs = _run_pair(monkeypatch, trace_loader=_trace_ok)
    assert repr(segs) == repr(base_segs) and msgs == base_msgs, "留痕炸掉不得改变生成"
    assert any("trace down" in x for x in loud_obs.lines), f"必须记 WARNING：{loud_obs.lines}"

    # ② 观测函数本身炸（message_generator 侧 catch ⇒ WARNING）
    def _explode(*_a, **_k):
        raise RuntimeError("obs down")
    monkeypatch.setattr("app.memory.observability.obs_event", _explode)
    loud_mg = _Loud(mg._logger)
    monkeypatch.setattr(mg, "_logger", loud_mg)
    segs2, msgs2 = _run_pair(monkeypatch, trace_loader=_trace_ok)
    assert repr(segs2) == repr(base_segs) and msgs2 == base_msgs, "obs_event 炸了也照常生成"
    assert any("obs down" in x for x in loud_mg.lines), f"gate 侧须记 WARNING：{loud_mg.lines}"
    assert len(_gates(obs_events)) == 1, "通道炸掉后一条留痕也没写出（分母只剩基线那条）"


def test_未命中态留痕炸了也不影响生成(monkeypatch, obs_events):
    """flag 关（当前生产最常见形态）时 gate 留痕同样不得把主链路带下水。"""
    from app.memory import observability as obs_mod
    _flags(monkeypatch, False)
    _patch_pipeline(monkeypatch, gen_responses=[_resp()])   # 前置查询/LLM 一律打桩，不碰真实链路

    def _boom(**_kw):
        raise RuntimeError("trace down")
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _boom)
    loud_obs = _Loud(obs_mod._logger)
    monkeypatch.setattr(obs_mod, "_logger", loud_obs)

    base = _run(_CHAR)
    assert base, "留痕炸掉时仍必须正常生成"
    assert any("trace down" in x for x in loud_obs.lines)


def test_gate留痕不新增LLM调用与trace构造(monkeypatch, obs_events):
    """gate 只是多写一条观测：LLM 调用次数与 trace 构造次数都不变（各 1 次）。

    注意必须先 _patch_pipeline 把前置查询/LLM/DB 会话全部打桩（否则会用真实链路与生产库）。
    """
    _flags(monkeypatch, True)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_trace_ok)
    gen_calls: list[int] = []
    stubbed_gen = mg._gen_with_reasoning          # 已是打桩后的假生成器

    async def _count(messages, *a, **k):
        gen_calls.append(1)
        return await stubbed_gen(messages, *a, **k)
    monkeypatch.setattr(mg, "_gen_with_reasoning", _count)
    assert _run(_CHAR)

    assert len(gen_calls) == 1, f"LLM 调用次数不得增加：{len(gen_calls)}"
    assert captured["loader"] == [(_CHAR, _USER)], f"trace 构造次数不得增加：{captured['loader']}"
    assert len(_gates(obs_events)) == 1


# ────────────── ④ C12b（2026-09-26）：硬编码白名单 → 「白名单 + 运行期总开关」 ──────────────

_ALL = "two_pass_trace_all_chars"


def _flags2(monkeypatch, *, main_on: bool, all_on: bool):
    """同时拨主开关与 C12b 新开关（自然度评分与本批无关，固定关）。返回被热改的 AGENT_FLAGS。"""
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", main_on)
    monkeypatch.setitem(AGENT_FLAGS, _ALL, all_on)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    return AGENT_FLAGS


def test_C12b新开关默认关():
    from app.agent.loop import AGENT_FLAGS
    assert AGENT_FLAGS.get(_ALL) is False, "新 flag 必须默认关（否则本批就不是零行为变化）"
    assert mg.TWO_PASS_TRACE_ALL_FLAG == _ALL, "常量与 AGENT_FLAGS 键名必须一致"


def test_C12b主开关关_新开关怎么拨都不允许且gate记not_allowed(monkeypatch, obs_events):
    """新开关单独开不产生任何行为：主开关仍是第一道闸。"""
    flags = _flags2(monkeypatch, main_on=False, all_on=True)
    assert mg.two_pass_trace_allowed(_CHAR, flags=flags) is False
    assert mg.two_pass_trace_allowed(_OTHER_CHAR, flags=flags) is False

    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()])
    assert _run(_OTHER_CHAR)
    gates = _gates(obs_events)
    assert len(gates) == 1 and gates[0]["state"] == "not_allowed", f"主开关关必须记 not_allowed：{gates}"
    assert gates[0]["allowed"] is False and gates[0]["trace_len"] == 0
    assert captured["loader"] == [], "主开关关时仍不得构造 trace（不多一次查询）"


@pytest.mark.parametrize("char,expected", [
    (_CHAR, True),          # 白名单内：与本批前逐字一致
    (_OTHER_CHAR, False),   # 白名单外：本批前只有 char13 会用
    ("13", True),           # 字符串 id 同 int 口径
    (None, False),          # 无角色
    ("abc", False),         # 脏值不炸、按不生效处理
])
def test_C12b主开关开且新开关关_逐字维持白名单现状(monkeypatch, char, expected):
    flags = _flags2(monkeypatch, main_on=True, all_on=False)
    assert mg.two_pass_trace_allowed(char, flags=flags) is expected
    assert mg.two_pass_trace_allowed(char) is expected, "热切口径：默认读 AGENT_FLAGS，与主开关同一份来源"


def test_C12b新开关开_白名单外角色也允许且真注入trace(monkeypatch, obs_events):
    """放开全量＝白名单不再参与判定，且必须走到真注入（gate 记 injected、messages 首位是 trace）。"""
    flags = _flags2(monkeypatch, main_on=True, all_on=True)
    assert mg.two_pass_trace_allowed(_OTHER_CHAR, flags=flags) is True
    assert mg.two_pass_trace_allowed("1", flags=flags) is True, "字符串 id 同 int 口径"
    assert mg.two_pass_trace_allowed(_CHAR, flags=flags) is True, "白名单内照常允许"
    assert mg.two_pass_trace_allowed(None, flags=flags) is False, "无角色仍不生效"
    assert mg.two_pass_trace_allowed("abc", flags=flags) is False, "脏值不炸、按不生效处理"

    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_trace_ok)
    assert _run(_OTHER_CHAR)
    assert captured["loader"] == [(_OTHER_CHAR, _USER)], "放开后白名单外角色也要构造 trace"

    gates = _gates(obs_events)
    assert len(gates) == 1 and gates[0]["state"] == "injected" and gates[0]["allowed"] is True
    assert gates[0]["trace_len"] == len(_trace_text())
    msgs = captured["messages"][0]
    assert msgs[0] == {"role": "system", "content": _trace_text()}, f"trace 必须真注入到首位：{msgs[0]}"


def test_C12b新键在开关目录里有登记条目():
    """目录条目（键名一致、默认关、非直显）——漏登记会被 test_flag_catalog_metadata 兜底拦下，这里额外钉住形态。"""
    from app.agent.loop import AGENT_FLAGS
    from app.application.flag_catalog import FLAG_CATALOG, meta_for
    assert _ALL in AGENT_FLAGS, "新键必须登记进 AGENT_FLAGS（否则 runtime_flags 里开了也不生效）"
    assert AGENT_FLAGS[_ALL] is False
    assert _ALL in FLAG_CATALOG, "新键必须在目录里有自己的条目（不能落通用兜底文案）"
    zh = meta_for(_ALL, 'zh')
    assert zh['group'] == 'proactive' and zh['visible'] is False
    assert zh['title'].strip() and '白名单' in zh['desc'], f"中文说明需点明白名单口径：{zh}"
    assert meta_for(_ALL, 'en')['desc'].strip(), "en 说明不得为空"
