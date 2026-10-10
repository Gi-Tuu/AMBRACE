# -*- coding: utf-8 -*-
"""A39 欠项（批 44）：timer 的 ready 事件进闸②，但**一条事件只发一条 SELECT**。

台账里那句「仍欠一条不算完成＝timer 在 ready 事件上不重复查库」拆成两件事：

1. **ready 也得进闸**——`timer_ready` 早在域内白名单 `CHANNEL_ALLOWED_FIELDS` 里注册了，可闸只在
   `event_kind != "ready"` 时被调用 ⇒ 这条通道**永远产不出读数**（测一条走不到的路）。
2. **进闸不许再查一遍库**——`executors/timer.py` 的 settled 判据读的是同一条查询（同 WHERE、同
   limit 5），闸里再写一遍＝一拍两条一模一样的 SELECT，而且谓词会跟着各自漂移。

所以判据分两组：接线组（ready 真被调用、owner=ai 的 ready 不许被扩进来、cancel 仍必须 mark_fired）
＋取料组（查询计数＝1、`texts` 传入时闸内零查询、两闸全关时还是一条都不发）。
"""
import asyncio

import pytest

from app.scheduling import freshness as frs

_USER_PRED = "chat_messages.sender_type = :"      # 编译后 WHERE 里的用户侧谓词片段


class _Ev:
    id = 9101
    character_id = 8101
    user_id = 8102
    session_id = 4242
    source_message_id = 77
    content_hint = "开会说一声"
    event_type = "ready"
    owner = "user"
    trigger_at = None

    def __init__(self, **kw):
        self.__dict__.update(kw)

    async def save(self, *a, **k):
        return None


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """计数桩：每次 execute 记下语句，闸/判据到底查了几遍库就是这么量的（不是打桩抛异常）。"""

    def __init__(self, sink, rows):
        self.sink = sink
        self.rows = rows

    async def execute(self, stmt, *a, **k):
        self.sink.append(str(stmt))
        return _Res(self.rows)

    async def get(self, *a, **k):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _factory(sink, rows):
    def _f():
        return _Db(sink, rows)
    return _f


def _user_query_count(sink):
    return sum(1 for s in sink if _USER_PRED in s)


@pytest.fixture
def stubs(monkeypatch):
    """run_timer 的外围依赖一律打断：模型／发送／mark_fired／flag。"""
    from app.scheduling.executors import context as ctx_mod
    from app.scheduling.executors import timer as tm

    calls = {"llm": 0, "send": 0, "fired": [], "gate": []}

    async def _hourly(char_id):
        return 0

    async def _noop(*a, **k):
        return None

    async def _llm(messages=None, **kw):
        calls["llm"] += 1
        return "开会弄好了吗"

    async def _mark(event_id):
        calls["fired"].append(event_id)

    async def _send(*a, **k):
        calls["send"] += 1
        return True

    import app.agent.llm_client as llm_mod
    import app.scheduling.promise_service as ps
    import app.scheduling.scheduler as engine

    monkeypatch.setattr(llm_mod, "chat_completion", _llm)
    monkeypatch.setattr(ps, "mark_fired", _mark)
    monkeypatch.setattr(engine, "send_to_session", _send)
    monkeypatch.setattr(tm, "agent_flag_on", lambda k: False)

    async def _gate(event, session_factory=None, texts=None):
        calls["gate"].append({"event": event, "texts": texts})
        return None if calls.get("cancel") else ""

    monkeypatch.setattr(frs, "check_timer_event", _gate)

    def bundle(sink, rows):
        return ctx_mod.GateBundle(
            is_dnd_now=lambda *a, **k: False, has_user_said_sleep=lambda *a, **k: False,
            is_user_active=lambda *a, **k: True, hourly_active=_hourly, pacing_gate=_noop,
            mark_gate=lambda *a, **k: None, session_factory=_factory(sink, rows),
            app_day_start=lambda *a, **k: None)

    return calls, bundle


# ──────────────── 接线组：ready 真进闸，且只在 settled 判据跑过的那批上 ────────────────
def test_ready事件也会进闸并且拿到的通道名就是timer_ready(stubs):
    calls, bundle = stubs
    from app.scheduling.executors import timer as tm

    sink, ev = [], _Ev()
    asyncio.run(tm.run_timer({"event": ev}, bundle(sink, [("今天挺顺利的",)])))
    assert len(calls["gate"]) == 1, "ready 事件没进闸＝timer_ready 这条通道永远产不出读数"
    assert frs.timer_channel(calls["gate"][0]["event"]) == "timer_ready"


def test_进闸时复用settled那一次取料_不再发第二条select(stubs):
    calls, bundle = stubs
    from app.scheduling.executors import timer as tm

    sink, ev = [], _Ev()
    asyncio.run(tm.run_timer({"event": ev}, bundle(sink, [("今天挺顺利的",)])))
    assert _user_query_count(sink) == 1, f"同一拍发了 {_user_query_count(sink)} 条用户正文查询（应为 1）"
    assert calls["gate"][0]["texts"] == ["今天挺顺利的"], "闸拿的不是上面那批正文＝两处各查各的"


def test_owner是ai的ready不进闸(stubs):
    """反向钉：不许把「用户说过结果」这条判据用到 AI 自述回来的消息上。"""
    calls, bundle = stubs
    from app.scheduling.executors import timer as tm

    class _AiReady(_Ev):
        owner = "ai"

    sink = []
    asyncio.run(tm.run_timer({"event": _AiReady()}, bundle(sink, [("随便说点什么",)])))
    assert calls["gate"] == []


def test_非ready事件照旧走闸且不预先取料(stubs):
    calls, bundle = stubs
    from app.scheduling.executors import timer as tm

    class _Back(_Ev):
        event_type = "back"

    sink = []
    asyncio.run(tm.run_timer({"event": _Back()}, bundle(sink, [("我已经到家了",)])))
    assert len(calls["gate"]) == 1
    assert calls["gate"][0]["texts"] is None, "非 ready 没有上面那一次取料，必须让闸自己查"
    assert _user_query_count(sink) == 0, "非 ready 也不许在调用点先查一遍（两闸全关时得是零查询）"


def test_闸判cancel时ready也必须mark_fired且不发不调模型(stubs):
    calls, bundle = stubs
    from app.scheduling.executors import timer as tm

    calls["cancel"] = True
    sink = []
    ok = asyncio.run(tm.run_timer({"event": _Ev()}, bundle(sink, [("今天挺顺利的",)])))
    assert ok is True
    assert len(calls["gate"]) == 1, "这一档没走到闸，cancel 的终态就永远没被测过"
    assert calls["fired"] == [9101], "cancel 不 mark_fired＝下个 tick 再问一遍（承诺被反复催）"
    assert calls["llm"] == 0 and calls["send"] == 0


def test_settled判据命中时仍旧早退_不进闸不发模型(stubs):
    calls, bundle = stubs
    from app.scheduling.executors import timer as tm

    sink = []
    ok = asyncio.run(tm.run_timer({"event": _Ev()}, bundle(sink, [("会开完了",)])))
    assert ok is True
    assert calls["fired"] == [9101] and calls["llm"] == 0
    assert calls["gate"] == [], "上面已经判过兑现了还进闸＝同一条批正文两处各判一遍"


# ──────────────── 取料组：texts 传入 ⇒ 闸内零查询；两闸全关 ⇒ 一条都不发 ────────────────
def test_texts传入时闸内一次查询都不发(monkeypatch):
    sink = []
    monkeypatch.setattr(frs, "_flags", lambda: (True, False))
    mark = asyncio.run(frs.check_timer_event(_Ev(), _factory(sink, [(" x ",)]),
                                             texts=["我已经到家了"]))
    assert _user_query_count(sink) == 0, "复用取料时还发查询＝ready 一拍两条 SELECT"
    assert mark.startswith("[fresh="), f"影子档应给出留痕串，实得 {mark!r}"


def test_两闸全关时ready与非ready都零查询(monkeypatch):
    sink = []
    monkeypatch.setattr(frs, "_flags", lambda: (False, False))
    assert asyncio.run(frs.check_timer_event(_Ev(), _factory(sink, [("",)]),
                                             texts=["我已经到家了"])) == ""
    assert asyncio.run(frs.check_timer_event(_Ev(event_type="back"), _factory(sink, [("",)]))) == ""
    assert sink == []


def test_闸自己取料时那批正文真的喂进了判据(monkeypatch):
    """反向钉「取料单点」不是把查询删掉：没传 texts 时闸必须自己查、并把结果交给判据。

    （第一版电池里 M4＝删掉 `texts = await recent_user_texts(...)` 这一句时守卫全绿——
    那条守卫只断了"复用查询"，没断"取料落到判据"，等于给删查询留了后门。）
    """
    sink = []
    f = asyncio.run(frs.read_timer_facts(_Ev(event_type="back", content_hint="回来说一声"),
                                        _factory(sink, [("我回来了",)])))
    assert _user_query_count(sink) == 1, "没传 texts 时闸自己不发查询＝非 ready 通道永远读成没变"
    assert f.result_ready is True, "取到的正文没喂进判据＝闸看着在跑、其实一直是 keep"


def test_取料口径唯一_timer里不许再出现第二份用户正文查询():
    import inspect

    from app.scheduling.executors import timer as tm

    src = inspect.getsource(tm)
    assert "recent_user_texts" in src, "timer 不再经统一取料＝自己又写了一条 SELECT"
    assert 'ChatMessage.sender_type == "user"' not in src, "用户正文谓词出现第二处口径（会各自漂移）"
