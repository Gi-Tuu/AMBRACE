# -*- coding: utf-8 -*-
"""A39 批 2b 接线位置守卫：闸必须挡在调模型**之前**，且 cancel 的善后语义各通道不同。

为什么单独立一个文件：前面每个通道的守卫测的都是 `pre_send_check` / 读数层本身，
它们全绿也只证明"判得对"，证不了"接在了对的位置"。上一批我就是用桩把 `pre_send_check`
整个盖掉，结果一条变异（gate 接不上判据）从缝里溜过去——这条就是把那种缝堵上。

两条善后语义必须分开钉：
  · 主动消息（life_regression）cancel ⇒ **不调模型、不发**，也不动任何状态；
  · 定时承诺（timer）cancel ⇒ 不调模型、不发，但**必须 mark_fired**——
    承诺的"已兑现"就是它该静默收口的终态，漏了 mark_fired 就会下个 tick 再问一遍。
"""
import asyncio
from pathlib import Path

import pytest

from app.scheduling import freshness as frs


# ───────────────────────── life_regression：cancel ⇒ 零模型调用 ─────────────────────────
class _Db:
    async def get(self, model, pk):
        return None

    async def execute(self, stmt, *a, **k):
        class _R:
            def scalars(_s):
                return _s

            def all(_s):
                return []

            def scalar_one_or_none(_s):
                return None

        return _R()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _wire_life(monkeypatch):
    from app.agent import llm_client
    from app.scheduling import life_regression as lr
    from app.scheduling import scheduler as engine, state_guard, triggers

    llm, sent = [], []

    async def _fake_llm(messages=None, **kw):
        llm.append((messages or [{}])[-1].get("content", ""))
        return "我上周去爬了山"

    async def _send(session_id, char_id, user_id, msg, **kw):
        sent.append(msg)
        return True

    async def _sess(*a, **k):
        return {"id": 4242}

    async def _anchor(*a, **k):
        return ""

    monkeypatch.setattr(llm_client, "chat_completion", _fake_llm)
    monkeypatch.setattr(engine, "send_to_session", _send)
    monkeypatch.setattr(triggers, "get_latest_session", _sess)
    monkeypatch.setattr(state_guard, "current_state_anchor", _anchor)
    monkeypatch.setattr(lr, "_current_anchor", _anchor)
    monkeypatch.setattr(lr, "_state_guard_segments", lambda *a, **k: [])
    monkeypatch.setattr(lr, "async_session_factory", lambda *a, **k: _Db())
    monkeypatch.setattr(lr, "_used_today", lambda *a, **k: _false_coro())
    return llm, sent, lr


async def _false_coro():
    return False


CAND_LIFE = {"character_id": 8101, "user_id": 8102,
             "life_items": [{"id": 501, "content": "用户上周去爬了山"}], "session_id": None}


def test_life_regression_闸开且cancel时一次模型都不调(monkeypatch):
    llm, sent, lr = _wire_life(monkeypatch)

    async def _cancel(channel, candidate):
        return None

    monkeypatch.setattr(frs, "pre_send_check", _cancel)
    assert asyncio.run(lr.run_life_regression(dict(CAND_LIFE))) is False
    assert llm == [] and sent == [], "闸判 cancel 却还是烧了这次调用／还是发了"


def test_life_regression_源码顺序闸在模型前且正文取自回读():
    """位置判据（内容级，不看注释）：`pre_send_check` 在 `chat_completion` 之前，
    且 `lines` 在 `items_for_prompt` **之后**才拼——顺序反了就等于闸只管发不发、正文永远旧。"""
    src = Path(lr_path()).read_text(encoding="utf-8")
    start = src.index("async def run_life_regression")
    nxt = src.find("async def ", start + 10)
    body = src[start:nxt if nxt > 0 else len(src)]
    assert body.index("pre_send_check") < body.index("chat_completion("), "闸没挡在调模型之前"
    assert body.index("items_for_prompt") < body.index("lines = "), "正文在回读之前就已拼好"


def lr_path():
    from app.scheduling import life_regression
    return life_regression.__file__


# ───────────────────────── timer：cancel ⇒ 不调模型但必须 mark_fired ─────────────────────────
def test_timer_cancel必须mark_fired且不调模型(monkeypatch):
    from app.scheduling.executors import context as ctx_mod
    from app.scheduling.executors import timer as tm

    calls = {"llm": 0, "fired": [], "send": 0}

    async def _hourly(char_id):
        return 0

    async def _noop_async(*a, **k):
        return None

    async def _llm(messages=None, **kw):
        calls["llm"] += 1
        return "到家了吗"

    async def _mark(event_id):
        calls["fired"].append(event_id)

    async def _stop(channel, candidate, **kw):
        return None

    class _Ev:
        id, character_id, user_id, session_id = 9101, 8101, 8102, 4242
        source_message_id, content_hint, event_type, owner, trigger_at = 77, "到家说一声", "back", "ai", None

        async def save(self, *a, **k):
            return None

    def _sess():          # session_factory 必须**同步**返回异步上下文管理器（async with g.session_factory()）
        return _Db()

    monkeypatch.setattr(tm, "agent_flag_on", lambda k: False)
    monkeypatch.setattr(ctx_mod, "get_hourly_active_count", _hourly, raising=False)
    monkeypatch.setattr(frs, "check_timer_event", _stop)
    import app.agent.llm_client as llm_mod
    import app.scheduling.promise_service as ps
    import app.scheduling.scheduler as engine

    async def _send(*a, **k):
        calls["send"] += 1
        return True

    monkeypatch.setattr(llm_mod, "chat_completion", _llm)
    monkeypatch.setattr(ps, "mark_fired", _mark)
    monkeypatch.setattr(engine, "send_to_session", _send)
    async def _no(*a, **k):
        return False

    g = ctx_mod.GateBundle(is_dnd_now=_no, has_user_said_sleep=_no, is_user_active=_no,
                           hourly_active=_hourly, pacing_gate=_noop_async, mark_gate=lambda *a, **k: None,
                           session_factory=_sess, app_day_start=lambda *a, **k: None)
    ok = asyncio.run(tm.run_timer({"event": _Ev()}, g))
    assert calls["llm"] == 0, "闸判 cancel 却还是调了模型"
    assert calls["send"] == 0
    assert calls["fired"] == [9101], "cancel 没 mark_fired＝下个 tick 会再问一遍（承诺被反复催）"
    assert ok is True
