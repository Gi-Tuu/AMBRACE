# -*- coding: utf-8 -*-
"""A39 批 2b 通道 1（unfinished_topic）的闸②守卫。

四条口径，缺一条这闸就是摆设：
  ① 两个闸都关 ⇒ **一条额外查询都不发**（用计数桩，不用异常桩——这条链路多处 fail-open，
     抛异常的桩会被自己吞掉，同 B15/A42 教训）；
  ② shadow 开／gate 关 ⇒ 只读数＋打留痕，cancel 判定**不许改变发送行为**（影子不能变成实拦）；
  ③ gate 开 ⇒ 判到 cancel 才真的不调模型、不发（这才是省钱的那一半）；
  ④ 读数或判定出异常 ⇒ fail-open 放行，绝不把消息一起带走（「我读不到」不能等于「这条没了」）。
"""
import asyncio

import pytest

from app.scheduling import freshness as frs

CAND = {
    "character_id": 9001, "user_id": 9002, "session_id": 9003,
    "unfinished_content": "改天一起去爬山",
}


@pytest.fixture
def stub(monkeypatch):
    """把闸②唯一的 IO 出口（会话尾部最新用户消息）换成计数桩，用 box 控制它返回什么。"""
    import app.scheduling.prospective_intent as pi
    from app.flags import agent_flags

    calls = []
    box = {"value": None}

    async def _fake_latest(session_id):
        calls.append(session_id)
        return box["value"]

    monkeypatch.setattr(pi, "_latest_user_message", _fake_latest)
    for k in ("proactive_freshness_shadow", "proactive_freshness_gate"):
        assert k in agent_flags.AGENT_FLAGS, f"闸没注册：{k}"
        monkeypatch.setitem(agent_flags.AGENT_FLAGS, k, False)
    return calls, box, monkeypatch


def _open(stub, shadow=False, gate=False):
    _calls, _box, monkeypatch = stub
    from app.flags import agent_flags

    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_shadow", shadow)
    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_gate", gate)


def test_两个闸都关时一条额外查询都不发(stub):
    calls, _box, _mp = stub
    assert asyncio.run(frs.pre_send_check("unfinished_topic", CAND)) == ""
    assert calls == [], "闸关着却去读会话尾部消息了"


def test_影子闸只读数不拦(stub):
    calls, box, _mp = stub
    _open(stub, shadow=True)
    box["value"] = "别的那句话"              # 与话头不同 ⇒ 判据给 cancel
    mark = asyncio.run(frs.pre_send_check("unfinished_topic", CAND))
    assert mark is not None, "影子档把 cancel 变成了实拦"
    assert mark.startswith("[fresh=cancel|"), mark
    assert calls == [9003]


def test_实闸开时cancel才拦_keep仍放行(stub):
    _calls, box, _mp = stub
    _open(stub, shadow=True, gate=True)
    box["value"] = "别的那句话"
    assert asyncio.run(frs.pre_send_check("unfinished_topic", CAND)) is None
    box["value"] = "改天一起去爬山"          # 话头仍是最后一条 ⇒ keep
    assert asyncio.run(frs.pre_send_check("unfinished_topic", CAND)) is not None


def test_读数层抛异常必须放行而不是吞掉消息(stub):
    _calls, _box, monkeypatch = stub
    import app.scheduling.prospective_intent as pi

    async def _boom(session_id):
        raise RuntimeError("库断了")

    monkeypatch.setattr(pi, "_latest_user_message", _boom)
    _open(stub, shadow=True, gate=True)
    assert asyncio.run(frs.pre_send_check("unfinished_topic", CAND)) == "", \
        "闸自己坏了却把消息判死＝隐性收紧"


def test_未登记通道直接放行不读数(stub):
    calls, _box, _mp = stub
    _open(stub, shadow=True, gate=True)
    assert asyncio.run(frs.pre_send_check("moment_comment", CAND)) == ""
    assert calls == []


def test_话头比较要用与落库一致的截断口径(stub):
    """采集侧存的是话头的 [:120]，重读到的原文可能更长——不截同一刀就永远判「用户已接着说过」。"""
    _calls, box, _mp = stub
    _open(stub, shadow=True, gate=True)
    long_text = "改天一起去爬山" + "啊" * 200
    box["value"] = long_text
    cand = dict(CAND, unfinished_content=long_text[:120])
    got = asyncio.run(frs.pre_send_check("unfinished_topic", cand))
    assert got is not None, "同一条消息因截断口径不同被误判成「用户已接着说过」"
    # 反向钉：真换了别的话，必须仍然判 cancel（否则上一条断言只是"恒不拦"）
    box["value"] = "我改主意了不想去" + "哈" * 200
    assert asyncio.run(frs.pre_send_check("unfinished_topic", cand)) is None


def test_判定只来自纯函数层读数层不许自己下结论(stub):
    """把 domain 的 `decide` 换成桩，通道行为必须**完全跟着桩走**——证明「读」在 scheduling、「判」在 domain。

    反向钉两条：桩给 KEEP 却拦了＝读数层在越权判；桩给 CANCEL 却放行＝gate 根本没接上判据。
    """
    _calls, _box, monkeypatch = stub
    from app.domain.proactivity import freshness as dom

    _open(stub, shadow=True, gate=True)
    monkeypatch.setattr(frs, "decide", lambda channel, facts: (dom.KEEP, "桩"))
    assert asyncio.run(frs.pre_send_check("unfinished_topic", CAND)) is not None
    monkeypatch.setattr(frs, "decide", lambda channel, facts: (dom.CANCEL, "桩"))
    assert asyncio.run(frs.pre_send_check("unfinished_topic", CAND)) is None


class _FakeDb:
    async def get(self, model, pk):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _wire_run(monkeypatch):
    """把 `run_unfinished_topic` 的四个外部口（DB／LLM／发送／现状锚）全换成计数桩，返回记账本。"""
    from app.agent import llm_client
    from app.scheduling import scheduler as engine
    from app.scheduling import state_guard
    from app.scheduling import unfinished_topic as ut

    llm_calls, sent = [], []

    async def _fake_llm(messages=None, **kw):
        llm_calls.append((messages or [{}])[-1].get("content", ""))
        return "对了，你上次说的那个爬山的事"

    async def _fake_send(session_id, char_id, user_id, msg, **kw):
        sent.append(msg)
        return True

    async def _anchor(**kw):
        return "现状锚"

    monkeypatch.setattr(llm_client, "chat_completion", _fake_llm)
    monkeypatch.setattr(engine, "send_to_session", _fake_send)
    monkeypatch.setattr(state_guard, "current_state_anchor", _anchor)
    monkeypatch.setattr(ut, "async_session_factory", lambda *a, **k: _FakeDb())
    return llm_calls, sent, ut


def test_cancel时一次模型都不调_闸关时行为照旧(monkeypatch):
    """跑**整条 `run_unfinished_topic`**，只桩最外层读数出口——让 flag→decide→拦 这条链保持真的。

    为什么不桩 `pre_send_check`：那样只证明"它返回 None 就不发"，证不了"闸开且判到 cancel 时真的返回 None"
    （M2 那种变异——gate 接不上判据——当场就溜过去了，这是我自己先撞出来的一次无效覆盖）。
    """
    from app.domain.proactivity.freshness import FreshFacts
    from app.flags import agent_flags

    llm_calls, sent, ut = _wire_run(monkeypatch)
    reads = []

    async def _replied(candidate):
        reads.append(candidate["session_id"])
        return FreshFacts(user_replied=True)      # 判据表里的 cancel：用户已接着说过

    monkeypatch.setitem(frs._READERS, "unfinished_topic", _replied)
    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_shadow", True)
    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_gate", True)
    assert asyncio.run(ut.run_unfinished_topic(CAND)) is False
    assert reads == [9003], "闸开了却没读数"
    assert llm_calls == [] and sent == [], "闸判 cancel 却还是烧了这次调用／还是发了"

    # 反向①：两个闸都关 ⇒ 读数出口一次都不碰，且照旧生成照旧发
    llm_calls.clear(); sent.clear(); reads.clear()
    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_shadow", False)
    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_gate", False)
    assert asyncio.run(ut.run_unfinished_topic(CAND)) is True
    assert reads == [] and len(llm_calls) == 1 and len(sent) == 1, "关闸后行为被改掉了"

    # 反向②：只开影子不开实闸 ⇒ 读了数、留了痕，但**照发**（影子不能变成实拦）
    llm_calls.clear(); sent.clear(); reads.clear()
    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_shadow", True)
    assert asyncio.run(ut.run_unfinished_topic(CAND)) is True
    assert reads == [9003] and len(sent) == 1, "影子档把 cancel 变成了实拦"



