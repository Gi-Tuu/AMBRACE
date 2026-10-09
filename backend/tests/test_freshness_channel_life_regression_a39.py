# -*- coding: utf-8 -*-
"""A39 批 2b 通道 2（life_regression）的闸②守卫。

这条通道的形态和通道 1 不同：候选带的是**记忆 id 列表**，所以"重取现状"＝按 id 回读同几条记忆。
四档行为必须分开钉住，混在一起就会把"影子"做成"实改"：
  ① 两闸全关 ⇒ 一条额外查询都不发、prompt 逐字节旧行为；
  ② 只开影子 ⇒ 读库＋留痕，但**正文一个字都不改**；
  ③ 开实闸且记忆**全部**消失 ⇒ cancel（不调模型、不发）；
  ④ 开实闸且只是部分消失 ⇒ 照发，但用回读到的新鲜正文（这条是本通道真正的价值）。
"""
import asyncio

import pytest

from app.scheduling import freshness as frs

ITEMS = [{"id": 501, "content": "用户上周去爬了山", "sub_type": "life_event"},
         {"id": 502, "content": "用户养了一只猫", "sub_type": "life_event"}]
CAND = {"character_id": 8001, "user_id": 8002, "life_items": ITEMS}


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    """计数桩：记录每次 execute，返回预设行 (id, content, delete_at)。"""

    def __init__(self, sink, rows):
        self.sink = sink
        self.rows = rows

    async def execute(self, stmt, *a, **k):
        self.sink.append(str(stmt).split("\n")[0][:60])
        return _Res(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def db(monkeypatch):
    """把 `refresh_life_items` 唯一依赖的会话工厂换成计数桩，并预置"库里现在还剩下什么"。"""
    from app.db import database as dbmod

    calls = []
    box = {"rows": [(501, "用户上周去爬了山", None), (502, "用户养了一只猫", None)]}

    def _boom(*a, **k):
        calls.append("session")
        return _Db(calls, box["rows"])

    monkeypatch.setattr(dbmod, "async_session_factory", _boom)
    from app.flags import agent_flags

    for k in ("proactive_freshness_shadow", "proactive_freshness_gate"):
        assert k in agent_flags.AGENT_FLAGS, f"闸没注册：{k}"
        monkeypatch.setitem(agent_flags.AGENT_FLAGS, k, False)
    return calls, box, monkeypatch


def _set(dbbox, shadow=False, gate=False):
    _calls, _box, monkeypatch = dbbox
    from app.flags import agent_flags

    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_shadow", shadow)
    monkeypatch.setitem(agent_flags.AGENT_FLAGS, "proactive_freshness_gate", gate)


def test_两闸全关时一条额外查询都不发(db):
    calls, _box, _mp = db
    assert asyncio.run(frs.pre_send_check("life_regression", dict(CAND))) == ""
    assert frs.items_for_prompt("life_regression", dict(CAND), ITEMS) == ITEMS
    assert calls == [], "闸关着却回读记忆了"


def test_影子档读库留痕但正文一个字都不改(db):
    calls, _box, _mp = db
    _set(db, shadow=True)
    cand = dict(CAND)
    mark = asyncio.run(frs.pre_send_check("life_regression", cand))
    assert mark is not None and mark.startswith("[fresh=keep|"), mark
    assert "session" in calls, "影子档没真去回读"
    assert frs.items_for_prompt("life_regression", cand, ITEMS) == ITEMS, "影子档改了正文"


def test_实闸开且记忆全没了指向cancel(db):
    calls, box, _mp = db
    _set(db, shadow=True, gate=True)
    box["rows"] = []                       # 两条记忆都被删了
    cand = dict(CAND)
    assert asyncio.run(frs.pre_send_check("life_regression", cand)) is None
    assert calls.count("session") == 1, "同一次判定查了两遍库"


def test_实闸开且只部分消失时用新鲜正文照放行(db):
    _calls, box, _mp = db
    _set(db, shadow=True, gate=True)
    box["rows"] = [(502, "用户养了一只猫，名字叫糯米", None)]   # 501 没了；502 正文变了
    cand = dict(CAND)
    mark = asyncio.run(frs.pre_send_check("life_regression", cand))
    assert mark is not None and mark.startswith("[fresh=keep|"), mark
    got = frs.items_for_prompt("life_regression", cand, ITEMS)
    assert [it["id"] for it in got] == [502], got
    assert got[0]["content"] == "用户养了一只猫，名字叫糯米", "实闸开了却还在用旧正文"


def test_截断口径与采集侧一致(db):
    """采集侧存的是 content[:200]，回读也要截同一刀，否则"正文变了"会永远为真。"""
    _calls, box, _mp = db
    _set(db, shadow=True, gate=True)
    long_text = "用户养了一只猫" + "喵" * 300
    box["rows"] = [(501, long_text, None), (502, "用户养了一只猫", None)]
    cand = dict(CAND, life_items=[{"id": 501, "content": long_text[:200]},
                                  {"id": 502, "content": "用户养了一只猫"}])
    asyncio.run(frs.pre_send_check("life_regression", cand))
    got = frs.items_for_prompt("life_regression", cand, cand["life_items"])
    assert len(got) == 2 and got[0]["content"] == long_text[:200], got


def test_读库抛异常必须原样返回而不是把消息判死(db):
    _calls, _box, monkeypatch = db
    from app.db import database as dbmod

    class _Bad:
        async def execute(self, stmt, *a, **k):
            raise RuntimeError("库断了")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(dbmod, "async_session_factory", lambda *a, **k: _Bad())
    _set(db, shadow=True, gate=True)
    cand = dict(CAND)
    mark = asyncio.run(frs.pre_send_check("life_regression", cand))
    assert mark is not None and not mark.startswith("[fresh=cancel"), \
        "读失败被当成了 cancel＝把「我读不到」变成「这条没了」"
    assert frs.items_for_prompt("life_regression", cand, ITEMS) == ITEMS


def test_缺id的候选形态整体不动(db):
    """有任一 item 没有数字 id ⇒ 不是本通道形态，一律不猜（宁可不修也不误删内容）。"""
    calls, box, _mp = db
    _set(db, shadow=True, gate=True)
    box["rows"] = []
    odd = [{"id": "abc", "content": "没有数字 id"}]
    cand = dict(CAND, life_items=odd)
    asyncio.run(frs.pre_send_check("life_regression", cand))
    assert frs.items_for_prompt("life_regression", cand, odd) == odd
    assert calls == [], "没有可用 id 时不该去查库"


def test_判定只来自纯函数层(db):
    """读数层不许自己下结论：把 decide 换成常量，行为必须跟着走。"""
    from app.domain.proactivity import freshness as dom

    _calls, _box, monkeypatch = db
    _set(db, shadow=True, gate=True)
    monkeypatch.setattr(frs, "decide", lambda ch, f: (dom.CANCEL, "桩"))
    assert asyncio.run(frs.pre_send_check("life_regression", dict(CAND))) is None
    monkeypatch.setattr(frs, "decide", lambda ch, f: (dom.KEEP, "桩"))
    assert asyncio.run(frs.pre_send_check("life_regression", dict(CAND))) is not None
