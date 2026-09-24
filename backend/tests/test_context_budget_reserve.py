# -*- coding: utf-8 -*-
"""P2a 上下文预算预留 + 超预算显式告知（2026-09-23，雷达 §3 后端部分）单测。

覆盖（对应派单要求 D）：
① flag 关 = 逐字旧行为：有效预算 = TOTAL_SYSTEM_QUOTA_TOKENS(9000)、埋点字段与旧版一致；
② flag 开 = 有效预算 = 9000 − 800(回复预留) − 500(工具声明预留)；
③ 边界：总量刚好不超 / 刚好超（开、关两条预算线各测一次）；
④ 埋点 detail 六字段齐全（budget/used/reserve_reply/reserve_tools/clipped_blocks/freed_chars）
   且 budget、used、freed_chars 关系自洽；
⑤ 下限保护：预留之和 ≥ 总硬顶（配置退化）→ 不抛异常、生效预算不为负、不写坏数据。
另测要求 C：flag 开着但没发生裁剪 → 一条都不写（不刷日志）。

纯函数级测试，全程不连任何 sqlite 数据库（不建库、不查库、不写库）。
"""
import json

import pytest

from app.agent import context_builder as cb
from app.agent.loop import AGENT_FLAGS

_TOTAL = 9000                  # 总硬顶（token）
_RESERVE_SUM = 800 + 500       # 两块预留（token）
_BUDGET_ON = _TOTAL - _RESERVE_SUM          # 7700
_BUDGET_ON_CHARS = _BUDGET_ON * 2           # 15400 字符
_BUDGET_OFF_CHARS = _TOTAL * 2              # 18000 字符
_OLD_DETAIL_KEYS = {"total_removed", "blocks"}
_NEW_DETAIL_KEYS = _OLD_DETAIL_KEYS | {
    "budget", "used", "reserve_reply", "reserve_tools", "clipped_blocks", "freed_chars",
}


# ────────────────────────── 夹具 ──────────────────────────

@pytest.fixture()
def detail_events(monkeypatch):
    """捕获 obs_event(… "quota_clipped_sections", detail)（patch 源头，模块内动态 import）"""
    events: list[tuple] = []
    monkeypatch.setattr(
        "app.memory.observability.obs_event",
        lambda cid, metric, detail, kind=None: events.append((cid, metric, detail)),
    )
    return events


@pytest.fixture()
def writes(monkeypatch):
    """捕获真正落库入口（enqueue_task_log），并放开观测总开关，验证「有没有多写一行」"""
    rows: list[dict] = []
    monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", True)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: rows.append(kw))
    return rows


def _block(chars: int, head: str = "") -> str:
    """恰好 chars 个字符的多行文本（行宽 50=49 字 + 换行；零头补一行），保证整行边界可裁"""
    if chars < len(head):
        raise ValueError("head 不得超过总长")
    body = ("填" * 49 + "\n") * ((chars - len(head)) // 50)
    rem = chars - len(head) - len(body)
    if rem >= 2:
        body += "余" * (rem - 1) + "\n"
    elif rem == 1:
        body += "余"
    return head + body


def _msgs(*sizes: int) -> list[dict]:
    """若干 system 块（合计 = sum(sizes) 字符）+ 一条不参与配额的 user 消息"""
    msgs = [{"role": "system", "content": _block(n)} for n in sizes]
    msgs.append({"role": "user", "content": "在忙吗"})
    return msgs


def _sys_chars(msgs: list[dict]) -> int:
    return sum(len(m["content"]) for m in msgs if m["role"] == "system")


# ────────────────────── 要求 A：常量与有效预算 ──────────────────────

def test_预留常量与flag默认值():
    assert cb.REPLY_RESERVE_TOKENS == 800
    assert cb.TOOL_DEFS_RESERVE_TOKENS == 500
    assert cb.TOTAL_SYSTEM_QUOTA_TOKENS == _TOTAL
    assert AGENT_FLAGS.get("context_budget_reserve") is False, "新 flag 必须默认关（零行为变化）"
    assert cb.context_budget_reserve_enabled() is False, "默认未生效"


def test_flag关_有效预算等于总硬顶(monkeypatch):
    assert cb._effective_system_budget_tokens(reserve_enabled=False) == _TOTAL
    assert cb._effective_system_budget_tokens() == _TOTAL, "未显式传参时必须真读 AGENT_FLAGS（默认关）"
    # 改桩口径不得漂移：既有测试用 monkeypatch TOTAL 构造小预算，关时必须原样取该值
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", 10)
    assert cb._effective_system_budget_tokens(reserve_enabled=False) == 10
    assert cb._effective_system_budget_tokens() == 10


def test_flag开_有效预算扣除两块预留(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)
    assert cb.context_budget_reserve_enabled() is True, "热切口径：不传参也要读到 AGENT_FLAGS"
    assert cb._effective_system_budget_tokens() == _BUDGET_ON
    assert cb._effective_system_budget_tokens(reserve_enabled=True) == _TOTAL - _RESERVE_SUM == 7700
    assert cb._effective_system_budget_tokens(reserve_enabled=False) == _TOTAL, "显式关=预留不生效"


@pytest.mark.parametrize("flags,expected", [
    ({"context_budget_reserve": True}, True),
    ({"context_budget_reserve": False}, False),
    ({}, False),
    (None, False),
])
def test_生效判定只认已登记的键(monkeypatch, flags, expected):
    if flags is None:
        monkeypatch.setattr("app.agent.loop.AGENT_FLAGS", {})
        assert cb.context_budget_reserve_enabled() is False
        return
    assert cb.context_budget_reserve_enabled(flags=flags) is expected


# ────────────────── 要求 D①：flag 关 = 旧行为（含埋点字段） ──────────────────

def test_flag关_超顶裁剪与埋点字段与旧版一致(detail_events):
    assert AGENT_FLAGS["context_budget_reserve"] is False
    msgs = _msgs(_TOTAL + 1, _TOTAL + 1)  # 18002 字符 > 18000（总硬顶）
    assert _sys_chars(msgs) == _BUDGET_OFF_CHARS + 2
    before = [m["content"] for m in msgs]

    cb._apply_system_total_quota(msgs, character_id=13)

    assert _sys_chars(msgs) <= _BUDGET_OFF_CHARS, "关=按总硬顶裁（旧语义）"
    assert msgs[0]["content"] == before[0], "同级靠前的块保留（旧顺序）"
    assert len(detail_events) == 1
    cid, metric, detail = detail_events[0]
    assert (cid, metric) == (13, "quota_clipped_sections")
    assert set(detail) == _OLD_DETAIL_KEYS, f"关时埋点字段不得新增：{set(detail)}"
    assert detail["total_removed"] >= 2
    assert all({"removed", "head"} == set(b) for b in detail["blocks"])
    assert all(len(b["head"]) <= 24 for b in detail["blocks"]), "head ≤24 字符（旧口径）"


def test_flag关_预留常量不影响旧裁剪线(detail_events):
    """15402 字符：开=超预算会裁；关=远小于 18000 一字不动（逐字旧行为）"""
    msgs = _msgs(_BUDGET_ON_CHARS + 2)
    before = [m["content"] for m in msgs]
    cb._apply_system_total_quota(msgs, character_id=13)
    assert [m["content"] for m in msgs] == before
    assert detail_events == []


# ────────────────── 要求 A/B + D③④：flag 开（边界 + 埋点六字段） ──────────────────

def test_flag开_刚好不超预留后预算_零裁剪零埋点(detail_events, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)
    msgs = _msgs(_BUDGET_ON_CHARS // 2, _BUDGET_ON_CHARS // 2)  # 恰好 15400
    assert _sys_chars(msgs) == _BUDGET_ON_CHARS
    before = [m["content"] for m in msgs]

    cb._apply_system_total_quota(msgs, character_id=13)

    assert [m["content"] for m in msgs] == before, "刚好不超界必须一字不动"
    assert detail_events == [], "刚好不超界不得留痕"


def test_flag开_刚好超预留后预算_裁剪并补齐六字段(detail_events, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)
    msgs = _msgs(_BUDGET_ON, _BUDGET_ON + 2)                    # 15402 > 15400
    before_chars = _sys_chars(msgs)
    assert before_chars == _BUDGET_ON_CHARS + 2

    cb._apply_system_total_quota(msgs, character_id=13)

    after_chars = _sys_chars(msgs)
    assert after_chars <= _BUDGET_ON_CHARS, f"必须裁到预留后预算以内，实际 {after_chars}"
    assert msgs[0]["content"], "靠前的块应保留（同级后块先牺牲）"
    assert len(detail_events) == 1
    _, metric, detail = detail_events[0]
    assert metric == "quota_clipped_sections"
    # 要求 B：六个新字段齐全，且既有字段名与语义不变
    assert set(detail) == _NEW_DETAIL_KEYS, f"detail 字段必须恰好这 8 个：{set(detail)}"
    assert detail["budget"] == _BUDGET_ON
    assert detail["reserve_reply"] == cb.REPLY_RESERVE_TOKENS == 800
    assert detail["reserve_tools"] == cb.TOOL_DEFS_RESERVE_TOKENS == 500
    assert detail["budget"] + detail["reserve_reply"] + detail["reserve_tools"] == _TOTAL
    # 自洽：used=裁剪前 token 用量（估算）、freed_chars=释放字符、裁后余量必不超预算
    assert detail["used"] == before_chars // cb._EST_CHARS_PER_TOKEN
    assert detail["used"] * cb._EST_CHARS_PER_TOKEN <= before_chars
    assert (detail["used"] + 1) * cb._EST_CHARS_PER_TOKEN > before_chars
    assert detail["used"] >= detail["budget"], "触发裁剪的前提：已用 ≥ 预算"
    assert detail["freed_chars"] == before_chars - after_chars
    assert detail["freed_chars"] == detail["total_removed"], "新字段与既有 total_removed 同值同义"
    assert detail["freed_chars"] >= before_chars - _BUDGET_ON_CHARS
    # 裁前字符量 = used 折算 + 零头，裁后 = 裁前 − freed：三值必须闭环
    remainder = before_chars - detail["used"] * cb._EST_CHARS_PER_TOKEN
    assert after_chars == detail["used"] * cb._EST_CHARS_PER_TOKEN - detail["freed_chars"] + remainder
    assert detail["clipped_blocks"] == len(detail["blocks"]) == 1
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == "在忙吗", "user 消息不参与配额"


def test_flag开_整行边界仍不切半句(detail_events, monkeypatch):
    """预留生效后裁剪仍走整行边界（M1-S4 语义不得回退）"""
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", 2000)  # 预算 2000-1300=700 → 1400 字符
    text = _block(2000)
    msgs = [{"role": "system", "content": text}, {"role": "user", "content": "x"}]
    cb._apply_system_total_quota(msgs, character_id=13)
    kept = msgs[0]["content"]
    assert kept == "" or text.startswith(kept + "\n"), "保留部分必须落在行边界（无半行残片）"
    assert _sys_chars(msgs) <= 1400
    assert detail_events[0][2]["budget"] == 700


# ────────────────── 要求 C：没裁剪就不写（不刷日志） ──────────────────

def test_flag开_未裁剪不写任何库(writes, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)
    msgs = _msgs(100, 100)
    cb._apply_system_total_quota(msgs, character_id=13)
    assert writes == [], f"没发生裁剪不得写库：{writes}"


def test_flag开_裁剪时确实留痕一条(writes, monkeypatch):
    """同一路径反向验证：真的超预算时必须落且只落一条（含六字段，可被读端 json 解析）"""
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)
    msgs = _msgs(_BUDGET_ON, _BUDGET_ON + 2)
    cb._apply_system_total_quota(msgs, character_id=13)
    assert len(writes) == 1, f"应恰好一条：{writes}"
    row = writes[0]
    assert row["route"] == "quota_clipped_sections" and row["character_id"] == 13
    detail = json.loads(row["steps_json"])
    assert set(detail) == _NEW_DETAIL_KEYS
    assert len(row["steps_json"]) <= 1600, "steps_json 不得被截断（截断会丢六字段）"


def test_留痕失败不影响裁剪(detail_events, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)

    def _boom(*_a, **_kw):
        raise RuntimeError("obs down")

    monkeypatch.setattr("app.memory.observability.obs_event", _boom)
    msgs = _msgs(_BUDGET_ON, _BUDGET_ON + 2)
    cb._apply_system_total_quota(msgs, character_id=13)  # 不应抛出
    assert _sys_chars(msgs) <= _BUDGET_ON_CHARS


# ────────────────── 要求 D⑤：下限保护（配置退化） ──────────────────

@pytest.mark.parametrize("total,expected", [
    (1000, cb.MIN_SYSTEM_BUDGET_TOKENS),   # 预留之和 1300 ≥ 1000 → 取保底 256（正数）
    (1300, cb.MIN_SYSTEM_BUDGET_TOKENS),   # 恰好相等（预留吃掉全部额度）→ 仍是保底
    (1556, cb.MIN_SYSTEM_BUDGET_TOKENS),   # 刚好只留得下 256（1556−1300）
    (1557, 257),                           # 越线一格 → 正常扣减
    (100, 100),                            # 总硬顶比保底还小 → 不得越过总硬顶（退化为旧预算）
    (_TOTAL, 7700),                        # 正常配置
])
def test_下限保护_预算恒为正且不越过总硬顶(monkeypatch, total, expected):
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", total)
    budget = cb._effective_system_budget_tokens(reserve_enabled=True)
    assert budget == expected
    assert 0 < budget <= total, "不出现负预算/零预算，也不得比总硬顶更宽松"


@pytest.mark.parametrize("total", [0, -5])
def test_总硬顶非正_沿用旧语义不做总量裁剪(detail_events, monkeypatch, total):
    """TOTAL<=0 是既有「关闭总量裁剪」哨兵：原样返回、不裁剪、不抛异常、不写库"""
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", total)
    msgs = _msgs(5000, 5000)
    before = [m["content"] for m in msgs]
    cb._apply_system_total_quota(msgs, character_id=13)
    assert [m["content"] for m in msgs] == before
    assert detail_events == []


def test_配置退化时裁剪不抛异常且埋点不写坏数据(detail_events, monkeypatch):
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", 1000)  # 预留 1300 > 1000
    monkeypatch.setitem(AGENT_FLAGS, "context_budget_reserve", True)
    msgs = _msgs(1500, 1502)                                    # 3002 字符 > 512
    cb._apply_system_total_quota(msgs, character_id=13)
    assert len(detail_events) == 1
    _, _, detail = detail_events[0]
    assert set(detail) == _NEW_DETAIL_KEYS
    assert detail["budget"] == cb.MIN_SYSTEM_BUDGET_TOKENS > 0
    assert detail["budget"] <= 1000
    assert detail["used"] >= detail["budget"] > 0
    assert detail["freed_chars"] > 0 and detail["freed_chars"] == detail["total_removed"]
    assert detail["clipped_blocks"] == len(detail["blocks"]) >= 1
    assert _sys_chars(msgs) <= detail["budget"] * cb._EST_CHARS_PER_TOKEN
    for b in detail["blocks"]:
        assert isinstance(b["removed"], int) and b["removed"] > 0


# ────────────────── 开关目录登记 ──────────────────

def test_flag已在开关目录登记():
    from app.application.flag_catalog import FLAG_CATALOG
    meta = FLAG_CATALOG["context_budget_reserve"]
    assert meta["group"] == "agent" and meta["visible"] is False
    assert meta["title_zh"] and meta["desc_zh"] and meta["title_en"] and meta["desc_en"]
    for field in ("title_zh", "desc_zh", "title_en", "desc_en"):
        text = meta[field].lower()
        for term in ("flag", "db", "prompt"):
            assert term not in text, f"用户向文案不得含实现术语 {term!r}：{field}"
