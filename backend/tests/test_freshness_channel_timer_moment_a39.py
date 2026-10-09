# -*- coding: utf-8 -*-
"""A39 批 2b 通道 4（朋友圈评论）与通道 6（timer 非 ready）的闸②守卫。

两条通道各钉四件事，共同点是**影子一档绝不能改行为**：
  ① 两闸全关 ⇒ 一条额外查询都不发、返回原对象（用计数桩，不用异常桩）；
  ② 只开影子 ⇒ 读库＋留痕，但内容/发送一律照旧；
  ③ 开实闸 ⇒ cancel 才真的停，非 cancel 才允许换内容；
  ④ 读库失败 ⇒ fail-open 原样放行，"我读不到"不等于"这条没了"。
通道 6 另加一条形状判据：`_signal_seen` 只在 arrival／medication 两类上调用——
把它套到"回家"这类事件上等于拿用药字面表去判别的事，日志里看不出来但判据已经错了。
"""
import asyncio

import pytest

from app.scheduling import freshness as frs


class _Event:
    def __init__(self, **kw):
        self.id = 9001
        self.session_id = 9002
        self.source_message_id = 100
        self.content_hint = "到家说一声"
        self.event_type = "back"
        self.__dict__.update(kw)


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _Res(self._rows)


class _Sess:
    def __init__(self, sink, rows):
        self.sink = sink
        self.rows = rows

    async def execute(self, stmt, *a, **k):
        self.sink.append(str(stmt).split("\n")[0][:70])
        return _Res(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def flags(monkeypatch):
    from app.flags import agent_flags

    for k in ("proactive_freshness_shadow", "proactive_freshness_gate"):
        assert k in agent_flags.AGENT_FLAGS, f"闸没注册：{k}"
        monkeypatch.setitem(agent_flags.AGENT_FLAGS, k, False)
    return monkeypatch


def _on(mp, shadow=False, gate=False):
    from app.flags import agent_flags

    mp.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_shadow", shadow)
    mp.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_gate", gate)


# ───────────────────────── 通道 6：timer ─────────────────────────
def test_timer_两闸全关不查库(flags):
    calls = []

    def _factory():
        calls.append("session")
        return _Sess(calls, [])

    assert asyncio.run(frs.check_timer_event(_Event(), _factory)) == ""
    assert calls == []


def test_timer_影子档读库但不拦(flags, monkeypatch):
    calls = []
    mp = flags
    monkeypatch.setattr("app.scheduling.promise_parser.ready_result_seen", lambda t, h="": True)
    _on(mp, shadow=True)
    mark = asyncio.run(frs.check_timer_event(_Event(), (lambda: _Sess(calls, [("我已经到家了",)]))))
    assert mark is not None, "影子档把 cancel 变成了实拦"
    assert mark.startswith("[fresh=cancel|"), mark
    assert len(calls) == 1


def test_timer_实闸开且结果已兑现才停(flags, monkeypatch):
    mp = flags
    monkeypatch.setattr("app.scheduling.promise_parser.ready_result_seen", lambda t, h="": True)
    _on(mp, shadow=True, gate=True)
    calls = []
    assert asyncio.run(frs.check_timer_event(_Event(), (lambda: _Sess(calls, [("开完了",)])))) is None


def test_timer_没有反查锚时一律未知不杀(flags):
    mp = flags
    _on(mp, shadow=True, gate=True)
    calls = []
    ev = _Event(session_id=None, source_message_id=None)
    mark = asyncio.run(frs.check_timer_event(ev, (lambda: _Sess(calls, []))))
    assert mark is not None and mark.startswith("[fresh=keep|"), mark
    assert calls == [], "缺 session／锚点时不该查库"


def test_timer_信号表只用在到达与吃药两类(flags, monkeypatch):
    """反向钉：event_type=back 时不许走 `_signal_seen`（那会拿 ARRIVAL/MED 字面表判"回家"）。"""
    mp = flags
    _on(mp, shadow=True, gate=True)
    seen = []
    monkeypatch.setattr("app.scheduling.prospective_intent._signal_seen",
                        lambda kind, *t: seen.append(kind) or True)
    monkeypatch.setattr("app.scheduling.promise_parser.ready_result_seen", lambda t, h="": False)
    asyncio.run(frs.read_timer_facts(_Event(event_type="back"),
                                     (lambda: _Sess([], [("我到家了",)]))))
    assert seen == [], "back 事件不该调用 _signal_seen"
    asyncio.run(frs.read_timer_facts(_Event(event_type="arrival"),
                                     (lambda: _Sess([], [("我到家了",)]))))
    assert seen == ["arrival"], "arrival 该走既有信号表"


def test_timer_通道名按事件类型分档():
    assert frs.timer_channel(_Event(event_type="ready")) == "timer_ready"
    assert frs.timer_channel(_Event(event_type="back")) == "timer_general"


def test_timer_读库抛异常必须放行(flags):
    mp = flags
    _on(mp, shadow=True, gate=True)

    class _Bad(_Sess):
        async def execute(self, stmt, *a, **k):
            raise RuntimeError("库断了")

    mark = asyncio.run(frs.check_timer_event(_Event(), (lambda: _Bad([], []))))
    assert mark is not None and mark.startswith("[fresh=keep|"), "读失败被当成 cancel"


# ───────────────────────── 通道 4：朋友圈评论 ─────────────────────────
SNAP = ["评论A"]


def test_moment_两闸全关返回原列表且不查库(flags, monkeypatch):
    hit = []
    monkeypatch.setattr("app.db.database.async_session_factory", lambda *a, **k: hit.append("s"))
    mark, lst = asyncio.run(frs.moment_pre_send(7777, SNAP))
    assert mark == "" and lst is SNAP
    assert hit == []


def test_moment_影子档读库但列表原样退回(flags, monkeypatch):
    mp = flags
    _on(mp, shadow=True)
    rows = ["评论A", "前一个角色刚发的评论"]
    monkeypatch.setattr("app.db.database.async_session_factory",
                        lambda *a, **k: _MomentSess([], rows))
    mark, lst = asyncio.run(frs.moment_pre_send(7777, SNAP))
    assert mark is not None and not mark.startswith("[fresh=cancel"), mark
    assert lst is SNAP, "影子档把快照换成了新列表＝改了行为"


def test_moment_实闸开且动态还在才用重读到的列表(flags, monkeypatch):
    mp = flags
    _on(mp, shadow=True, gate=True)
    rows = ["评论A", "刚发的评论"]
    monkeypatch.setattr("app.db.database.async_session_factory",
                        lambda *a, **k: _MomentSess([], rows))
    mark, lst = asyncio.run(frs.moment_pre_send(7777, SNAP))
    assert mark is not None and lst is not SNAP and len(lst) == 2, (mark, lst)


def test_moment_动态被删时实闸必须停这批(flags, monkeypatch):
    mp = flags
    _on(mp, shadow=True, gate=True)
    monkeypatch.setattr("app.db.database.async_session_factory",
                        lambda *a, **k: _MomentSess([], [], alive=False))
    mark, lst = asyncio.run(frs.moment_pre_send(7777, SNAP))
    assert mark is None, "动态已经没了还在继续评论"
    assert lst is SNAP


def test_moment_读库抛异常原样放行(flags, monkeypatch):
    mp = flags
    _on(mp, shadow=True, gate=True)

    class _Bad:
        async def execute(self, *a, **k):
            raise RuntimeError("库断了")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("app.db.database.async_session_factory", lambda *a, **k: _Bad())
    mark, lst = asyncio.run(frs.moment_pre_send(7777, SNAP))
    assert mark == "" and lst is SNAP


class _MomentSess:
    """假会话：第一条 SELECT 问动态在不在（alive），第二条返回评论行。"""

    def __init__(self, sink, rows, alive=True):
        self.sink = sink
        self.rows = rows
        self.alive = alive
        self.n = 0

    async def execute(self, stmt, *a, **k):
        self.sink.append(str(stmt)[:60])
        self.n += 1
        if "moments" in str(stmt).lower() or self.n == 1:
            return _Res([(1,)] if self.alive else [])
        return _Res(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

# ───────────────────────── A48：留痕里的「话」与「数」必须同源 ─────────────────────────
def test_a48_评论新增时留痕不自相矛盾(flags, monkeypatch):
    # 批 35 现场 [fresh=keep|现状未变|快照=2|现状=3]：一句话与数字互相否定。
    # 新口径＝档位仍是 keep（不碰发不发），原因改成「评论列表新增 N 条…」，
    # 且这个 N 与方括号里的 快照=／现状= 是同一份 len。
    mp = flags
    _on(mp, shadow=True)
    monkeypatch.setattr('app.db.database.async_session_factory',
                        lambda *a, **k: _MomentSess([], ['评论A', '前一个角色刚发的评论']))
    mark, lst = asyncio.run(frs.moment_pre_send(7777, SNAP))
    assert mark.startswith('[fresh=keep|'), mark
    assert '现状未变' not in mark, '话与数还在互相否定：' + mark
    assert '新增 1 条' in mark and '快照=1' in mark and '现状=2' in mark, mark
    assert '不改发不发' in mark, '原因串没写明它不参与判定：' + mark
    assert lst is SNAP, '影子档把列表换了＝改了行为'


def test_a48_评论减少与不变的措辞(flags, monkeypatch):
    # 减少也要说清；真没变时才允许说「现状未变」。
    mp = flags
    _on(mp, shadow=True)
    monkeypatch.setattr('app.db.database.async_session_factory',
                        lambda *a, **k: _MomentSess([], ['评论A']))
    mark, _ = asyncio.run(frs.moment_pre_send(7777, ['评论A', '已被作者删掉的评论']))
    assert '减少 1 条' in mark and '快照=2' in mark and '现状=1' in mark, mark
    monkeypatch.setattr('app.db.database.async_session_factory',
                        lambda *a, **k: _MomentSess([], ['评论A']))
    mark2, _ = asyncio.run(frs.moment_pre_send(7777, SNAP))
    assert '现状未变' in mark2, mark2


def test_a48_条数差只在朋友圈评论通道生效():
    # 白名单守卫：别的通道塞进来 items_delta 也不许改判定/措辞（防跨通道串扰）。
    # 反向钉：条数差不得把档位从 keep 抬成 cancel／regenerate——它只影响取材。
    from app.domain.proactivity import freshness as dom
    v_other, why_other = dom.decide('life_regression', dom.FreshFacts(items_delta=5))
    assert v_other == dom.KEEP and why_other == '现状未变', (v_other, why_other)
    v, why = dom.decide('moment_comment', dom.FreshFacts(items_delta=5))
    assert v == dom.KEEP and '新增 5 条' in why, (v, why)
    v2, _ = dom.decide('moment_comment', dom.FreshFacts(items_delta=-3))
    assert v2 == dom.KEEP, '条数差把档位抬出了 keep＝违反了本单边界'

