# -*- coding: utf-8 -*-
"""召回门影子留痕测试（A4 批 2 / T4 P1，2026-09-27）。

守的口径：
1. 开关 recall_gate_shadow 默认关，且登记进目录（「每个开关都有目录条目」由
   test_flag_catalog_metadata 钉住，本文件只钉默认值）；
2. 关：调用点首行即返回 —— 不算判定、不建记录、不碰写库通道（零行为零开销）；
3. 开：一次调用＝一行 agent_task_logs（route=recall_gate_shadow），字段齐全、JSON 可解析；
4. 影子只留痕：把「门若生效会漏多少」量化成 would_lose / wasted，但**不参与是否检索**；
5. fail-open：留痕链路任何异常都不得向主链路抛出；
6. 严格口径（*_strict）＝同一套判定去掉「感知派生查询」再算一次，只用于给影子增加区分度，
   **主判定与既有字段的值必须与本改动前逐字一致**（test_主判定一字不改_只增字段 钉住）。
"""
import inspect
import json

import pytest

from app.agent.loop import AGENT_FLAGS
from app.memory import recall_gate as rg


@pytest.fixture
def flag_on():
    """临时打开影子开关（用完还原原值，避免污染其他用例）。"""
    original = AGENT_FLAGS.get(rg.SHADOW_FLAG_KEY, False)
    AGENT_FLAGS[rg.SHADOW_FLAG_KEY] = True
    yield
    AGENT_FLAGS[rg.SHADOW_FLAG_KEY] = original


@pytest.fixture
def recorder(monkeypatch):
    """替身写库通道：只记录调用参数，绝不碰数据库。"""
    rows = []

    def _fake(**kwargs):
        rows.append(kwargs)

    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _fake, raising=True)
    return rows


def test_影子开关默认关():
    assert AGENT_FLAGS[rg.SHADOW_FLAG_KEY] is False


def test_关时零行为(recorder):
    rg.observe_retrieval_decision("我们上次说的那件事怎么样了", hit_count=3,
                                  character_id=13, user_id=1)
    assert recorder == []


def test_开时写一行且字段齐全(flag_on, recorder):
    rg.observe_retrieval_decision("嗯", hit_count=3, character_id=13, user_id=1, task_id="t1")
    assert len(recorder) == 1
    row = recorder[0]
    assert row["route"] == rg.SHADOW_ROUTE == "recall_gate_shadow"
    assert row["trigger"] == rg.SHADOW_TRIGGER
    assert row["status"] == "ok"
    assert row["character_id"] == 13 and row["user_id"] == 1
    assert row["task_id"] == "t1"
    assert isinstance(row["latency_ms"], int)
    rec = json.loads(row["steps_json"])
    assert rec["retrieve"] is False and rec["reason"] == rg.REASON_SMALL_TALK
    assert rec["confidence"] == "high"
    assert rec["hit_count"] == 3
    assert rec["would_lose"] is True        # 门说跳过、实际命中 3 条 ⇒ 门若生效会漏
    assert rec["wasted"] is False
    assert rec["msg"] == "嗯"


def test_留痕自带对照口径(flag_on, recorder):
    """门赞成检索但实际空手 ⇒ wasted；门说跳过且真的没东西 ⇒ 两边都 False。"""
    rg.observe_retrieval_decision("我们上次说的那件事怎么样了", hit_count=0)
    rg.observe_retrieval_decision("嗯", hit_count=0)
    recs = [json.loads(r["steps_json"]) for r in recorder]
    assert [r["retrieve"] for r in recs] == [True, False]
    assert recs[0]["wasted"] is True and recs[0]["would_lose"] is False
    assert recs[1]["wasted"] is False and recs[1]["would_lose"] is False


def test_输入留痕带上本轮的三个来源(flag_on, recorder):
    rg.observe_retrieval_decision("", has_time_phrase=True, has_extra_queries=True,
                                  is_continue=True, hit_count=0)
    rec = json.loads(recorder[0]["steps_json"])
    assert rec["has_time_phrase"] is True
    assert rec["has_extra_queries"] is True
    assert rec["is_continue"] is True
    assert rec["retrieve"] is True and rec["reason"] == rg.REASON_CONTINUE


@pytest.mark.parametrize("text", ["", "   ", "。。。", "🙂"])
def test_纯符号与空文本判为跳过且不留风险(text):
    rec = rg.plan_shadow_record(text, hit_count=0)
    assert rec["retrieve"] is False
    assert rec["would_lose"] is False


def test_长句与问句一律判为要检索():
    assert rg.plan_shadow_record("刚刚说到哪儿了你还记不记得")["retrieve"] is True
    assert rg.plan_shadow_record("我记得你之前提过一个想法")["retrieve"] is True


def test_fail_open_留痕失败不影响主链路(flag_on, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _boom, raising=True)
    rg.observe_retrieval_decision("我们上次说的那件事", hit_count=1)   # 不得抛出


# ────────────── 严格口径（*_strict，2026-09-27：让影子真值有区分度） ──────────────

_BASE_KEYS = {"retrieve", "reason", "confidence", "hit_count", "would_lose", "wasted",
              "msg", "has_time_phrase", "has_extra_queries", "is_continue"}
_STRICT_KEYS = {"retrieve_strict", "reason_strict", "confidence_strict", "would_skip_strict"}

# 覆盖主判定的每一条判据分支（含短路顺序），严格口径在这些用例上必须与既有值分毫不差
_STRICT_CASES = [
    dict(),
    dict(has_extra_queries=True),
    dict(has_time_phrase=True),
    dict(has_time_phrase=True, has_extra_queries=True),
    dict(is_continue=True),
    dict(is_continue=True, has_extra_queries=True, has_time_phrase=True),
]


@pytest.mark.parametrize("text", ["嗯", "哈哈", "。。。", "🙂", "", "在干嘛呢",
                                  "我们上次说的那件事怎么样了", "今天我去了一个新的地方感觉还挺不错的"])
@pytest.mark.parametrize("kw", _STRICT_CASES)
def test_严格口径就是同一套判定去掉派生查询(text, kw):
    """严格口径 ＝ decide_retrieval(同参数, has_extra_queries=False)，不引入任何新判据。"""
    rec = rg.plan_shadow_record(text, **kw)
    expect = rg.decide_retrieval(text, **{**kw, "has_extra_queries": False})
    assert (rec["retrieve_strict"], rec["reason_strict"], rec["confidence_strict"]) == (
        expect.retrieve, expect.reason, expect.confidence)
    assert rec["would_skip_strict"] is (not expect.retrieve)


@pytest.mark.parametrize("hit_count", [0, 2])
@pytest.mark.parametrize("text", ["嗯", "哈哈", "。。。", "🙂", "", "在干嘛呢",
                                  "我们上次说的那件事怎么样了", "今天我去了一个新的地方感觉还挺不错的"])
@pytest.mark.parametrize("kw", _STRICT_CASES)
def test_主判定一字不改_只增字段(text, kw, hit_count):
    """钉住零行为：既有 10 个字段的值＝改动前那套算法的逐字结果（新字段完全独立成一路）。"""
    d = rg.decide_retrieval(text, **kw)
    rec = rg.plan_shadow_record(text, hit_count=hit_count, **kw)
    assert {k: rec[k] for k in _BASE_KEYS} == {
        "retrieve": d.retrieve,
        "reason": d.reason,
        "confidence": d.confidence,
        "hit_count": hit_count,
        "would_lose": (not d.retrieve) and hit_count > 0,
        "wasted": d.retrieve and hit_count == 0,
        "msg": (text or "")[:rg._MSG_MAX],
        "has_time_phrase": bool(kw.get("has_time_phrase", False)),
        "has_extra_queries": bool(kw.get("has_extra_queries", False)),
        "is_continue": bool(kw.get("is_continue", False)),
    }
    assert set(rec) == _BASE_KEYS | _STRICT_KEYS   # 只增不减，也没顺手加别的


def test_严格口径才有区分度():
    """线上实测全部 reason=extra_queries ⇒ 主判定恒真；去掉派生查询后门能判「跳过」。"""
    main = rg.plan_shadow_record("嗯", has_extra_queries=True, hit_count=0)
    assert main["retrieve"] is True and main["reason"] == rg.REASON_EXTRA_QUERIES
    assert main["would_skip_strict"] is True
    assert main["retrieve_strict"] is False and main["reason_strict"] == rg.REASON_SMALL_TALK
    assert main["confidence_strict"] == "high"


def test_继续指令排在派生查询前_严格口径也短路():
    """is_continue 短路在 has_extra_queries 之前 ⇒ 严格口径与主判定一致（不误伤继续指令）。"""
    rec = rg.plan_shadow_record("嗯", is_continue=True, has_extra_queries=True)
    assert rec["reason"] == rg.REASON_CONTINUE == rec["reason_strict"]
    assert rec["retrieve"] is True and rec["retrieve_strict"] is True
    assert rec["would_skip_strict"] is False


def test_没有派生查询时严格口径与主判定完全相同():
    for text in ("嗯", "在干嘛呢", "我们上次说的那件事怎么样了"):
        rec = rg.plan_shadow_record(text, has_time_phrase=True)
        assert rec["retrieve_strict"] == rec["retrieve"]
        assert rec["reason_strict"] == rec["reason"]


def test_严格口径随影子写进留痕且未被截断(flag_on, recorder):
    """新增字段后 steps_json 仍是完整可解析 JSON（步长上限 1200 未被打穿）。"""
    rg.observe_retrieval_decision("嗯" * 200, has_extra_queries=True, hit_count=2)
    row = recorder[0]
    assert len(row["steps_json"]) < rg._STEPS_MAX
    rec = json.loads(row["steps_json"])
    assert rec["retrieve"] is True and rec["reason"] == rg.REASON_EXTRA_QUERIES
    assert rec["reason_strict"] == rg.REASON_SUBSTANTIVE   # 长句这一路本身就该检索
    assert rec["would_skip_strict"] is False
    assert len(rec["msg"]) == rg._MSG_MAX                  # 最长 msg 也没把 JSON 挤断


def test_检索节点已接影子挂点():
    """接线口径：挂点在 search_memories 之后（hit_count 才是真实命中数），且不据此改检索。"""
    from app.agent import nodes
    src = inspect.getsource(nodes)
    assert "observe_retrieval_decision(" in src
    assert src.index("observe_retrieval_decision(") > src.index("memories = await search_memories(")