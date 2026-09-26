# -*- coding: utf-8 -*-
"""召回门影子留痕测试（A4 批 2 / T4 P1，2026-09-27）。

守的口径：
1. 开关 recall_gate_shadow 默认关，且登记进目录（「每个开关都有目录条目」由
   test_flag_catalog_metadata 钉住，本文件只钉默认值）；
2. 关：调用点首行即返回 —— 不算判定、不建记录、不碰写库通道（零行为零开销）；
3. 开：一次调用＝一行 agent_task_logs（route=recall_gate_shadow），字段齐全、JSON 可解析；
4. 影子只留痕：把「门若生效会漏多少」量化成 would_lose / wasted，但**不参与是否检索**；
5. fail-open：留痕链路任何异常都不得向主链路抛出。
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


def test_检索节点已接影子挂点():
    """接线口径：挂点在 search_memories 之后（hit_count 才是真实命中数），且不据此改检索。"""
    from app.agent import nodes
    src = inspect.getsource(nodes)
    assert "observe_retrieval_decision(" in src
    assert src.index("observe_retrieval_decision(") > src.index("memories = await search_memories(")