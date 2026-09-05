# -*- coding: utf-8 -*-
"""G 前瞻 INTENT 输出观测（2026-09-06 三查点派工）：extractor 便车分支 obs_event 断言。

轻量单测：mock llm_call / upsert_intent / obs_event，驱动 extract_single，
断言观测事件参数（written/kind/content）与主链路不被观测异常阻断（fail-open）。
不连 DB（各类目全「无」+ source_id=None 无落库路径）。
"""
import asyncio

from app.agent import loop as agent_loop
from app.memory import extractor


def _drive(monkeypatch, raw: str):
    events = []

    async def _fake_llm(**kw):
        return raw

    ups = []

    async def _fake_upsert(**kw):
        ups.append(kw)
        return 1

    def _fake_obs(cid, metric, detail, kind=None):
        events.append((cid, metric, detail))

    monkeypatch.setattr(extractor, "llm_call", _fake_llm)
    monkeypatch.setattr("app.scheduling.prospective_intent.upsert_intent", _fake_upsert)
    monkeypatch.setattr("app.memory.observability.obs_event", _fake_obs)
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "prospective_intent_enabled", True)
    asyncio.run(extractor.extract_single(1, 11, 4, "u", "a", source_id=None))
    return events, ups


def test_obs_written_true_on_intent_line(monkeypatch):
    raw = ("USER_INFO: 无\nEVENTS: 无\nPREFERENCES: 无\nBIO: 无\nSTATUS: 无\nRELATIONSHIP: 无\n"
           "STAGE: 无\nCURATED: 无\n"
           "INTENT: 下周末带用户去吃火锅 | promise | 2026-09-12~2026-09-13 | 火锅,周末")
    events, ups = _drive(monkeypatch, raw)
    hit = [e for e in events if e[1] == "prospective_intent_extract"]
    assert len(hit) == 1
    cid, metric, detail = hit[0]
    assert cid == 11 and metric == "prospective_intent_extract"
    assert detail["written"] is True and detail["kind"] == "promise"
    assert "火锅" in detail["content"]
    assert len(ups) == 1  # 主链路照常落表


def test_obs_written_false_on_none(monkeypatch):
    raw = ("USER_INFO: 无\nEVENTS: 无\nPREFERENCES: 无\nBIO: 无\nSTATUS: 无\nRELATIONSHIP: 无\n"
           "STAGE: 无\nCURATED: 无\nINTENT: 无")
    events, ups = _drive(monkeypatch, raw)
    hit = [e for e in events if e[1] == "prospective_intent_extract"]
    assert len(hit) == 1 and hit[0][2]["written"] is False
    assert ups == []


def test_obs_failure_does_not_break_upsert(monkeypatch):
    """fail-open：观测抛异常不影响主链路（upsert 仍执行）。"""
    raw = ("USER_INFO: 无\nEVENTS: 无\nPREFERENCES: 无\nBIO: 无\nSTATUS: 无\nRELATIONSHIP: 无\n"
           "STAGE: 无\nCURATED: 无\n"
           "INTENT: 樱花开了提醒用户拍照 | cue | 无 | 樱花,拍照")

    async def _fake_llm(**kw):
        return raw

    ups = []

    async def _fake_upsert(**kw):
        ups.append(kw)
        return 1

    def _boom(*a, **k):
        raise RuntimeError("obs down")

    monkeypatch.setattr(extractor, "llm_call", _fake_llm)
    monkeypatch.setattr("app.scheduling.prospective_intent.upsert_intent", _fake_upsert)
    monkeypatch.setattr("app.memory.observability.obs_event", _boom)
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "prospective_intent_enabled", True)
    asyncio.run(extractor.extract_single(1, 11, 4, "u", "a", source_id=None))
    assert len(ups) == 1  # 主链路不受观测异常影响
