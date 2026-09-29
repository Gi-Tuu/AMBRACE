# -*- coding: utf-8 -*-
"""B10 埋点小活（2026-09-29，Z1）：section_budget 补「空段 key 名单」——零行为、只改观测载荷。

守的口径（对应派单第 4 条要求的八类）：
1. **字段兼容**：旧四字段 sections/total/n_empty/chars_total 名字与取值逐字不变，sections 元素
   仍恰好 {key, chars, empty}（读端 Y2 `_aggregate_section_breakdown` 就是按这些取值）；
2. **空段 key 落库**：空段 key 进 empty_keys，非空段不进，n_empty 与名单长度对齐；
3. **截断标记**：空段数量超 _EMPTY_KEYS_MAX → 名单夹到阈值且 truncated=True；
4. **上限保护**：累计字符超 _EMPTY_KEYS_CHAR_BUDGET / 单 key 超 _EMPTY_KEY_MAX_CHARS → 截断，
   整条 detail 序列化后不顶穿 obs_event 的 1600 字符落库上限；
5. **异常隔离**：观测通道抛异常 / 载荷计算抛异常，_run_sections 返回值逐字不变；
6. **单条频率**：一轮装配只写一条 section_budget（沿用既有频率，不每段一条）；
7. **key 去重**：同 key 重复执行只出一条，且重复本身不算截断（truncated=False）；
8. **顺序稳定**：名单按注册声明序（(order, key)）落，同输入两次运行逐字节相同。

全用例只 monkeypatch 观测通道 + 假注册表，不建库、不连 backend/data 生产库。
"""
import asyncio
import json

import pytest

import app.agent.context as _ctx
from app.agent.context.sections import ContextSection, TARGET_APPEND, TARGET_TEMPLATE

_ROUTE = "section_budget"


# ────────────────────────────────────── 假 section（builder 只回文本，不碰库）


async def _tpl_text(state, ctx):
    return "hello"          # 5 字符，非空


async def _append_list(state, ctx):
    return ["a", "bb"]      # 3 字符，非空


async def _tpl_empty(state, ctx):
    return ""               # 空串


async def _append_empty(state, ctx):
    return []               # 空列表


async def _raise(state, ctx):
    raise ValueError("section 崩了")


@pytest.fixture()
def events(monkeypatch):
    """捕获 obs_event 入参（调用点在函数内 `from app.memory.observability import obs_event`，
    patch 模块属性即命中），并保证观测门控开着。"""
    from app.memory.observability import _flag_on

    assert _flag_on(), "memory_trace_debug 默认开，用例前提"
    captured: list = []
    monkeypatch.setattr(
        "app.memory.observability.obs_event",
        lambda cid, metric, detail, kind=None: captured.append((cid, metric, detail)),
    )
    return captured


def _run(monkeypatch, sections, state=None):
    monkeypatch.setattr(_ctx, "get_sections", lambda: list(sections))
    return asyncio.run(_ctx._run_sections(state or {"character_id": 13, "user_id": 1}, {}))


def _budget(events):
    hit = [e for e in events if e[1] == _ROUTE]
    assert len(hit) == 1, f"{_ROUTE} 聚合事件应恰好一条，实际 {len(hit)} 条"
    return hit[0]


def _three():
    """基线三非空段 + 二空段（template 空串 / append 空列表）。"""
    return [
        ContextSection(key="t1", builder=_tpl_text, target=TARGET_TEMPLATE, slot="t1", order=1),
        ContextSection(key="a1", builder=_append_list, target=TARGET_APPEND, order=2),
        ContextSection(key="e1", builder=_tpl_empty, target=TARGET_TEMPLATE, slot="e1", order=3),
        ContextSection(key="e2", builder=_append_empty, target=TARGET_APPEND, order=4),
    ]


# ────────────────────────────────────── ① 字段兼容：旧字段一字未动


def test_旧字段全在与取值不变(monkeypatch, events):
    """新增三项之后，sections/total/n_empty/chars_total 与改动前逐字一致，sections 元素形状不扩字段。"""
    values = _run(monkeypatch, _three())
    assert values == {"t1": "hello", "a1": ["a", "bb"], "e1": "", "e2": []}, values

    _, _, detail = _budget(events)
    assert {"sections", "total", "n_empty", "chars_total"} <= set(detail), detail
    assert detail["total"] == 4, detail
    assert detail["n_empty"] == 2, detail
    assert detail["chars_total"] == 8, detail
    # sections 元素仍恰好三字段（读端按 key/chars/empty 取值，不许改名/加字段）
    assert {i["key"] for i in detail["sections"]} == {"t1", "a1", "e1", "e2"}, detail["sections"]
    for item in detail["sections"]:
        assert set(item) == {"key", "chars", "empty"}, f"元素字段被改：{item}"
        assert isinstance(item["chars"], int) and isinstance(item["empty"], bool)
    assert detail["sections"][0]["key"] == "t1", "top-16 排序口径（体量大在前）不许变"
    # 新字段名按派单示例，且 truncated 是 bool 不是 None/字符串
    assert {"empty_keys", "declared_keys_n", "truncated"} <= set(detail), detail
    assert isinstance(detail["truncated"], bool)


def test_零行为_新载荷不影响values与旧实现逐字相同(monkeypatch, events):
    """只改观测载荷：把 obs 通道整体掐死，values 与开观测时完全一致（注入结果零变化）。"""
    sections = _three() + [
        ContextSection(key="off", builder=_tpl_text, target=TARGET_APPEND, order=5, enabled=False),
        ContextSection(key="boom", builder=_raise, target=TARGET_APPEND, order=6),
    ]
    values_with_obs = _run(monkeypatch, sections)
    events.clear()
    monkeypatch.setattr("app.memory.observability.obs_event", lambda *a, **k: None)
    values_without_obs = _run(monkeypatch, sections)
    assert values_with_obs == values_without_obs == {
        "t1": "hello", "a1": ["a", "bb"], "e1": "", "e2": []}, values_with_obs
    # enabled=False 的段不执行、崩掉的段不写入（legacy 内联兜底语义不动）
    assert "off" not in values_with_obs and "boom" not in values_with_obs


# ────────────────────────────────────── ② 空段 key 名单落库


def test_空段key名单落库且不含非空段(monkeypatch, events):
    """本单的唯一目的：空段 key 名字进库（此前只有 n_empty 计数）。"""
    _run(monkeypatch, _three())
    _, _, detail = _budget(events)
    assert detail["empty_keys"] == ["e1", "e2"], detail
    assert "t1" not in detail["empty_keys"] and "a1" not in detail["empty_keys"]
    assert len(detail["empty_keys"]) == detail["n_empty"], "名单长度应与空段计数对齐"
    assert detail["declared_keys_n"] == 4, "声明段数含空段（4=2 非空 + 2 空）"


def test_异常段与禁用段不进名单(monkeypatch, events):
    """崩掉/禁用的段本轮未执行 ⇒ 既不进 empty_keys 也不进 declared_keys_n（否则分母虚高）。"""
    _run(monkeypatch, _three() + [
        ContextSection(key="boom", builder=_raise, target=TARGET_APPEND, order=7),
        ContextSection(key="off", builder=_tpl_empty, target=TARGET_APPEND, order=8, enabled=False),
    ])
    _, _, detail = _budget(events)
    assert detail["empty_keys"] == ["e1", "e2"], detail
    assert "boom" not in detail["empty_keys"] and "off" not in detail["empty_keys"]
    assert detail["declared_keys_n"] == 4 == detail["total"], detail


def test_全空轮次名单齐全且truncated为假(monkeypatch, events):
    """整轮全空（冷角色首轮的现实形态）：名单收全部 key，truncated=False。"""
    secs = [ContextSection(key=f"z{i}", builder=_tpl_empty, target=TARGET_TEMPLATE,
                           slot=f"z{i}", order=i) for i in range(5)]
    _run(monkeypatch, secs)
    _, _, detail = _budget(events)
    assert detail["empty_keys"] == [f"z{i}" for i in range(5)], detail
    assert detail["n_empty"] == 5 and detail["chars_total"] == 0, detail
    assert detail["truncated"] is False, detail


# ────────────────────────────────────── ③④ 截断标记 + 上限保护


def test_数量超阈值截断并标truncated(monkeypatch, events):
    """空段数超 _EMPTY_KEYS_MAX：名单夹到阈值、truncated=True，但 n_empty/total 仍按全部段计。"""
    n = _ctx._EMPTY_KEYS_MAX + 12
    secs = [ContextSection(key=f"ek{i:03d}", builder=_tpl_empty, target=TARGET_TEMPLATE,
                           slot=f"ek{i:03d}", order=i) for i in range(n)]
    _run(monkeypatch, secs)
    _, _, detail = _budget(events)
    assert detail["truncated"] is True, detail
    assert len(detail["empty_keys"]) == _ctx._EMPTY_KEYS_MAX, detail
    assert detail["empty_keys"] == [f"ek{i:03d}" for i in range(_ctx._EMPTY_KEYS_MAX)], "截断保前缀"
    assert detail["n_empty"] == n, "计数口径不许被名单截断带跑"
    assert detail["declared_keys_n"] == n and detail["total"] == n, detail


def test_字符超阈值截断且不顶穿落库上限(monkeypatch, events):
    """单 key 夹到 _EMPTY_KEY_MAX_CHARS、累计字符超预算即截断（名单自身体量有硬上限）。"""
    n = (_ctx._EMPTY_KEYS_CHAR_BUDGET // _ctx._EMPTY_KEY_MAX_CHARS) + 8
    # 长名且前 60 字符内互不相同（否则会被去重、变成「重复」而非「字符预算」触发截断）
    secs = [ContextSection(key=f"k{i:03d}" + "x" * 117, builder=_tpl_empty,
                           target=TARGET_TEMPLATE, slot=f"s{i}", order=i) for i in range(n)]
    _run(monkeypatch, secs)
    _, _, detail = _budget(events)
    assert detail["truncated"] is True, detail
    assert all(len(k) == _ctx._EMPTY_KEY_MAX_CHARS for k in detail["empty_keys"]), detail
    assert sum(len(k) for k in detail["empty_keys"]) <= _ctx._EMPTY_KEYS_CHAR_BUDGET, detail
    assert len(detail["empty_keys"]) < n, detail


def test_真注册表最坏形态不顶穿落库上限(monkeypatch, events):
    """上限保护的判据用**真 key**：46 段全空时（现实最坏形态）整条 detail 序列化必须 ≤1600，
    否则 obs_event 会把它截成坏 JSON，读端 Y2 直接丢样本。"""
    from app.agent.context import get_sections

    real = [s for s in get_sections() if s.enabled]
    assert len(real) >= 40, f"真注册表段数异常：{len(real)}"
    secs = [ContextSection(key=s.key, builder=_tpl_empty, target=s.target,
                           slot=s.slot, order=s.order) for s in real]
    _run(monkeypatch, secs)
    _, _, detail = _budget(events)
    assert detail["declared_keys_n"] == len(real), detail
    assert len(detail["empty_keys"]) <= _ctx._EMPTY_KEYS_MAX, detail
    dumped = json.dumps(detail, ensure_ascii=False, default=str)
    assert len(dumped) <= 1600, f"观测载荷顶穿落库上限：{len(dumped)} 字符"
    assert json.loads(dumped[:1600]) == detail, "未触发 obs_event 截断 ⇒ 读端拿到完整 JSON"


def test_未超阈值时不标截断(monkeypatch, events):
    """阈值余量内（现实形态：46 段里约 7 段空）truncated 必须为 False，不许误报。"""
    secs = [ContextSection(key=f"sec{i}", builder=(
        _tpl_empty if i % 6 == 0 else _tpl_text), target=TARGET_TEMPLATE,
        slot=f"sec{i}", order=i) for i in range(46)]
    _run(monkeypatch, secs)
    _, _, detail = _budget(events)
    assert detail["declared_keys_n"] == 46 and detail["total"] == 46, detail
    assert detail["n_empty"] == 8 == len(detail["empty_keys"]), detail
    assert detail["truncated"] is False, "未超限不得误报截断"
    assert len(json.dumps(detail, ensure_ascii=False, default=str)) <= 1600, detail


# ────────────────────────────────────── ⑤⑥ 异常隔离 + 单条频率


def test_观测通道抛异常不影响装配(monkeypatch, events):
    """沿用既有「异常照旧吞掉不阻塞」：通道炸了，values 照旧完整返回。"""
    monkeypatch.setattr("app.memory.observability.obs_event",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("观测通道炸了")))
    assert _run(monkeypatch, _three()) == {
        "t1": "hello", "a1": ["a", "bb"], "e1": "", "e2": []}


def test_载荷计算抛异常不影响装配(monkeypatch, events):
    """新代码自己的异常也要被既有 try 吞掉（名单计算绝不能变成装配的故障点）。"""
    monkeypatch.setattr(_ctx, "_empty_keys_payload",
                        lambda loads: (_ for _ in ()).throw(RuntimeError("名单算崩了")))
    assert _run(monkeypatch, _three()) == {
        "t1": "hello", "a1": ["a", "bb"], "e1": "", "e2": []}
    assert not [e for e in events if e[1] == _ROUTE], "通道未被调用即无留痕，但不许外抛"


def test_一轮只写一条且既有埋点路由不串(monkeypatch, events):
    """频率不变：4 段（含 1 段崩）⇒ 一条 section_budget + 一条 context_section_failed。"""
    secs = _three() + [ContextSection(key="boom", builder=_raise, target=TARGET_APPEND, order=9)]
    _run(monkeypatch, secs)
    cid, metric, _ = _budget(events)
    assert metric == _ROUTE and cid == 13, (cid, metric)
    assert len([e for e in events if e[1] == _ROUTE]) == 1
    assert len([e for e in events if e[1] == "context_section_failed"]) == 1


# ────────────────────────────────────── ⑦⑧ 去重 + 顺序稳定


def test_key去重且重复不算截断(monkeypatch, events):
    """注册表重复 key（同 key 重注册后 _loads 仍可能出现两次）：名单一条，truncated=False。"""
    secs = [
        ContextSection(key="dup", builder=_tpl_empty, target=TARGET_TEMPLATE, slot="dup", order=1),
        ContextSection(key="dup", builder=_tpl_empty, target=TARGET_APPEND, order=2),
        ContextSection(key="dup", builder=_append_empty, target=TARGET_APPEND, order=3),
        ContextSection(key="x", builder=_tpl_empty, target=TARGET_TEMPLATE, slot="x", order=4),
    ]
    _run(monkeypatch, secs)
    _, _, detail = _budget(events)
    assert detail["empty_keys"] == ["dup", "x"], detail
    assert detail["truncated"] is False, "去重丢弃的重复项不该记成截断"
    assert detail["total"] == 4, "total 仍按执行次数计（旧口径不许改）"
    assert detail["declared_keys_n"] == 2, "去重后的声明段数才是分母"
    assert detail["n_empty"] == 4, detail


def test_空段名单顺序稳定且随注册序(monkeypatch, events):
    """同一注册表跑两轮：empty_keys 逐字节相同，且按 get_sections 的 (order, key) 声明序落。"""
    secs = sorted([
        ContextSection(key="late_empty", builder=_tpl_empty, target=TARGET_TEMPLATE,
                       slot="late_empty", order=300),
        ContextSection(key="big", builder=_tpl_text, target=TARGET_TEMPLATE, slot="big", order=10),
        ContextSection(key="early_empty", builder=_append_empty, target=TARGET_APPEND, order=5),
    ], key=lambda s: (s.order, s.key))  # 与真 get_sections 的排序契约一致
    _run(monkeypatch, secs)
    first = _budget(events)[2]["empty_keys"]
    events.clear()
    _run(monkeypatch, secs)
    second = _budget(events)[2]["empty_keys"]
    assert first == second == ["early_empty", "late_empty"], (first, second)
    assert json.dumps(first) == json.dumps(second), "同输入必须逐字节稳定"


def test_纯函数阈值边界():
    """_empty_keys_payload 直接判边界：空输入、全非空、恰好达预算、超一字符。"""
    assert _ctx._empty_keys_payload([]) == ([], False)
    assert _ctx._empty_keys_payload([{"key": "a", "chars": 3, "empty": False}]) == ([], False)
    budget = _ctx._EMPTY_KEYS_CHAR_BUDGET
    k = 60
    fits = [{"key": f"k{i:02d}" + "x" * (k - 3), "chars": 0, "empty": True}
            for i in range(budget // k)]
    assert _ctx._empty_keys_payload(fits)[1] is False, f"{len(fits)}×{k}={budget // k * k} 应恰在预算内"
    assert _ctx._empty_keys_payload(fits + [{"key": "y" * k, "chars": 0, "empty": True}])[1] is True
    # 坏形状（缺 key / 非 str）不进名单、也不炸
    assert _ctx._empty_keys_payload([{"chars": 0, "empty": True}, {"key": None, "empty": True}]) \
        == ([], False)
