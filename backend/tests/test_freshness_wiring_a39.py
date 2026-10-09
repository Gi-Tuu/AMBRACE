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


# ─────────────── moment 互评后续两轮（第一/第二轮互评）：闸②必须挡在生成前 ───────────────
# 为什么单独测：通道 4 的读数层守卫测的是 `moment_pre_send` 本身，它全绿也证不了后续两轮
# **把返回值用上了**。这里钉两条：①cancel ⇒ 零模型调用、零写入；②实闸换回来的现状真的进了
# 去重判据（不是拿在手里丢掉）——同一条用例里跑"吃快照"与"吃现状"两种口径，差值就是牙。
class _CM:
    """假的 MomentComment 行，只带判据用到的字段。"""

    def __init__(self, cid, parent_id=None, sender_id=1, sender_type="ai", name="小爱", content="我也去"):
        self.id = cid
        self.parent_id = parent_id
        self.sender_id = sender_id
        self.sender_type = sender_type
        self.sender_name = name
        self.content = content


class _Char:
    def __init__(self, cid, name):
        self.id, self.name, self.personality, self.user_id = cid, name, "友善", 8102


class _MRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one(self):        # 每日上限那条 count 查询：恒 0＝没触顶
        return 0


class _MSess:
    def __init__(self, rows, sink, written):
        self.rows, self.sink, self.written = rows, sink, written

    async def execute(self, stmt, *a, **k):
        self.sink.append(str(stmt).split("\n")[0][:60])
        return _MRows(self.rows)

    def add(self, obj):
        self.written.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        obj.id = 900000 + len(self.written)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


TOPS = [_CM(1, sender_id=1, name="小爱"), _CM(2, sender_id=2, name="阿泽")]
CHARS = {1: _Char(1, "小爱"), 2: _Char(2, "阿泽")}


def _wire_moment(monkeypatch, rows, gate):
    from app.application import moment_service as ms

    calls = {"llm": [], "sess": 0, "written": []}

    async def _gen(char_name, personality, prompt, max_tok=400):
        calls["llm"].append(char_name)
        return f"{char_name}的回复"

    async def _pre(moment_id, snap):
        calls["sess"] += 1
        return gate(moment_id, snap)

    async def _ident(char):
        return ""

    async def _rec(*a, **k):
        return None

    monkeypatch.setattr(ms, "_generate_comment_text", _gen)
    monkeypatch.setattr(ms, "_identity_block", _ident)
    monkeypatch.setattr(ms, "_record_moment_comment_event", _rec)
    monkeypatch.setattr(ms, "async_session_factory",
                        lambda *a, **k: _MSess(rows, [], calls["written"]))
    monkeypatch.setattr(frs, "moment_pre_send", _pre)
    # 目标选择与 50% 概率都定下来：断言要钉"谁回了/谁没回"，不能靠随机数运气
    monkeypatch.setattr(ms.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(ms.random, "random", lambda: 0.1)
    return ms, calls


def test_moment互评第一轮_cancel时一次模型都不调(monkeypatch):
    ms, calls = _wire_moment(monkeypatch, TOPS, lambda mid, snap: (None, snap))
    asyncio.run(ms._first_round_ai_replies(7777, dict(CHARS), 5, owner_char_ids={1, 2}))
    assert calls["llm"] == [] and calls["written"] == [], "闸判 cancel 还在互评"
    assert calls["sess"] == 1, "已经停了还逐个角色问一遍闸"


def test_moment互评第一轮_闸放行时照常各回一句(monkeypatch):
    ms, calls = _wire_moment(monkeypatch, TOPS,
                             lambda mid, snap: ("[fresh=keep|现状未变|快照=2|现状=2]", snap))
    asyncio.run(ms._first_round_ai_replies(7777, dict(CHARS), 5, owner_char_ids={1, 2}))
    assert calls["llm"] == ["小爱", "阿泽"], calls["llm"]


def test_moment互评第一轮_实闸换回的现状要进得去去重判据(monkeypatch):
    """同一条动态跑两遍：吃快照⇒小爱把已经回过的那条再回一遍；吃现状⇒跳过。"""
    ms, stale = _wire_moment(monkeypatch, TOPS,
                             lambda mid, snap: ("[fresh=keep|现状未变|快照=2|现状=2]", snap))
    asyncio.run(ms._first_round_ai_replies(7777, dict(CHARS), 5, owner_char_ids={1, 2}))
    assert stale["llm"] == ["小爱", "阿泽"], stale["llm"]

    fresh = TOPS + [_CM(3, parent_id=2, sender_id=1, name="小爱")]   # 现状：小爱已回过阿泽那条
    ms2, got = _wire_moment(monkeypatch, TOPS,
                            lambda mid, snap: ("[fresh=keep|新增1条|快照=2|现状=3]", fresh))
    asyncio.run(ms2._first_round_ai_replies(7777, dict(CHARS), 5, owner_char_ids={1, 2}))
    assert got["llm"] == ["阿泽"], "换回来的现状没进判据＝同一条评论被回了两遍"


def test_moment互评第二轮_cancel不调模型且现状进得去判据(monkeypatch):
    tops = [_CM(1, sender_id=1, name="小爱"), _CM(2, parent_id=1, sender_id=2, name="阿泽")]

    ms, calls = _wire_moment(monkeypatch, tops, lambda mid, snap: (None, snap))
    asyncio.run(ms._second_round_ai_replies(7777, dict(CHARS), 5, owner_char_ids={1, 2}))
    assert calls["llm"] == [] and calls["written"] == [], "闸判 cancel 还在互评"

    ms2, keep = _wire_moment(monkeypatch, tops,
                             lambda mid, snap: ("[fresh=keep|现状未变|快照=2|现状=2]", snap))
    asyncio.run(ms2._second_round_ai_replies(7777, dict(CHARS), 5, owner_char_ids={1, 2}))
    assert keep["llm"] == ["小爱"], keep["llm"]

    fresh = tops + [_CM(3, parent_id=2, sender_id=1, name="小爱")]   # 现状：小爱已回过这一条
    ms3, got = _wire_moment(monkeypatch, tops,
                            lambda mid, snap: ("[fresh=keep|新增1条|快照=2|现状=3]", fresh))
    asyncio.run(ms3._second_round_ai_replies(7777, dict(CHARS), 5, owner_char_ids={1, 2}))
    assert got["llm"] == [], "第二轮没看见刚写下的那条＝同一层楼再回一遍"


def test_moment互评两轮_源码顺序闸在生成前():
    """位置判据（不看注释）：`moment_pre_send` 在 `_generate_comment_text` 之前，
    且 cancel 走 break 而不是 continue——continue 等于"这条跳过、下条照发"。"""
    from app.application import moment_service as ms

    src = Path(ms.__file__).read_text(encoding="utf-8")
    for name in ("_first_round_ai_replies", "_second_round_ai_replies"):
        start = src.index(f"async def {name}")
        nxt = src.find("async def ", start + 10)
        body = src[start:nxt if nxt > 0 else len(src)]
        assert "moment_pre_send" in body, f"{name} 没接闸"
        assert body.index("moment_pre_send") < body.index("_generate_comment_text("), f"{name} 闸在模型之后"
        assert "if _gate is None:" in body and "break" in body, f"{name} 的 cancel 没停这批"
