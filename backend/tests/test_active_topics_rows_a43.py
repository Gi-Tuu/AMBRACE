# -*- coding: utf-8 -*-
"""A43 守卫：话题的结构化出口 ＋ 投影侧接线。

四条纪律（每条都配了变异可咬的断言）：
① 只取「该角色 × 该用户」（跨账号不串味），缺任一 id 一次查询都不发；
② 与两个文本出口**共用同一份排序常量**（防哪天一边加排序、另一边没加 ⇒ 投影拿到的前三和模型看到的前三不是同一批）；
③ 返回字段而不是渲染文本（「🎯」「（今天）」这类展示串不许出现在结构化出口里）；
④ 投影侧**只在影子闸开时**调它（闸关＝零额外查询），取数失败只是少投一格、绝不冒泡。
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest

import app.agent.topic_tracker as tt
from app.agent import workspace_projection as wp
from app.agent.workspace import create_workspace
from app.flags.agent_flags import AGENT_FLAGS


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """假会话：把执行过的 SQL 文本记进 sink，返回预置行集（不建库、不跑迁移）。"""

    def __init__(self, sink, rows):
        self.sink = sink
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        self.sink.append(str(stmt))
        return _FakeResult(self.rows)


def _patch_db(monkeypatch, rows, sink):
    monkeypatch.setattr(tt, "async_session_factory", lambda: _FakeSession(sink, rows))


def _row(topic="答辩 PPT", *, goal=False, follow=True, imp=0.8, touched=None):
    return SimpleNamespace(topic=topic, goal=goal, follow_up=follow, importance=imp,
                           last_touched_at=touched or datetime(2026, 10, 8, 1, 2, 3))


# ───────────────────────── ① 用户口径 ─────────────────────────

def test_出口SQL的WHERE段真的带user_id过滤(monkeypatch):
    """只看 **WHERE 段**——SELECT 列表里本来就带 `conversation_topics.user_id` 列名，
    拿整条语句做 `in` 判断会**永远为真**（10-08 变异自测当场演示：摘掉过滤条件仍 45 全绿＝没牙）。"""
    sink = []
    _patch_db(monkeypatch, [_row()], sink)
    asyncio.run(tt.load_active_topics_rows(13, 3))
    assert len(sink) == 1
    sql = sink[0]
    assert "WHERE" in sql, "语句里没有 WHERE 段（形状不认识，先别当通过）：" + sql[:200]
    where = sql.split("WHERE", 1)[1]
    for col in ("conversation_topics.user_id", "conversation_topics.character_id", "conversation_topics.status"):
        assert col in where, "WHERE 段缺 %s ⇒ 口径不完整：%s" % (col, where[:260])


@pytest.mark.parametrize("cid,uid", [(None, 3), (13, None), (0, 3), (13, 0)])
def test_缺任一id一次查询都不发(monkeypatch, cid, uid):
    """用**计数器**而不是抛异常的桩：本模块整体 fail-open，`raise AssertionError` 会被
    `except Exception` 吞掉 ⇒ 那种写法永远测不出"到底查没查"（10-08 变异自测实测没牙）。"""
    hits = []
    _patch_db(monkeypatch, [_row()], hits)
    assert asyncio.run(tt.load_active_topics_rows(cid, uid)) == []
    assert hits == [], "缺 id 却还是发出了 %d 条查询" % len(hits)


def test_查库失败只是返回空列表不抛(monkeypatch):
    def _bad():
        raise RuntimeError("库断了")

    monkeypatch.setattr(tt, "async_session_factory", _bad)
    assert asyncio.run(tt.load_active_topics_rows(13, 3)) == []


# ───────────────────────── ② 排序口径唯一 ─────────────────────────

def test_排序口径全仓只有一份且三处出口都共用():
    src = inspect.getsource(tt)
    pair = "ConversationTopic.importance.desc(), ConversationTopic.last_touched_at.desc()"
    # 口径要精确：单键排序（maybe_extract_topics 按重要度裁剪、update_topic_resolution 兜底最近一条）
    # 语义不同、**不该**被并进来，所以这里只钉"importance＋last_touched 这套两键口径"全仓唯一。
    assert src.count(pair) == 1, "又出现第二份两键排序字面量 ⇒ 各话题出口会各排各的"
    # 反向钉：定义之前不算使用（A23 口径＝变异点写在定义之后）
    body = src.split("_ACTIVE_TOPICS_ORDER = ")[1]
    assert body.count("_ACTIVE_TOPICS_ORDER") >= 4, \
        "共用没接上（goal／text／rows／fresh 四个出口至少要各自引用一次）"


# ───────────────────────── ③ 返回字段不返回渲染 ─────────────────────────

def test_返回的是字段不是渲染文本(monkeypatch):
    _patch_db(monkeypatch, [_row(goal=True, touched=datetime(2026, 10, 8, 9, 0, 0))], [])
    got = asyncio.run(tt.load_active_topics_rows(13, 3))
    assert got == [{
        "topic": "答辩 PPT", "goal": True, "follow_up": True, "importance": 0.8,
        "last_touched_at": "2026-10-08T09:00:00",
    }]
    blob = repr(got)
    for banned in ("🎯", "（", "之前聊到的"):
        assert banned not in blob, f"结构化出口里混进了渲染展示串：{banned}"


def test_带时区的行归一成naive再落字段(monkeypatch):
    tz_row = _row(touched=datetime(2026, 10, 8, 9, 0, 0, tzinfo=__import__("datetime").timezone.utc))
    _patch_db(monkeypatch, [tz_row], [])
    assert asyncio.run(tt.load_active_topics_rows(13, 3))[0]["last_touched_at"] == "2026-10-08T09:00:00"


# ───────────────────────── ④ 投影侧接线 ─────────────────────────

def _state_with_ws():
    ws = create_workspace(character_id=13, user_id=3, session_id=9)
    return ws, {"workspace": ws, "character_id": 13, "user_id": 3, "session_id": 9,
                "character_name": "萨姆", "user_name": "小美",
                "character_info": {"self_statement": "我叫萨姆。", "bio": "话少。"}}


def test_闸关时一次都不调话题出口(monkeypatch):
    """计数桩（同上道理：抛异常的桩会被 fail-open 吞掉，等于没测）。"""
    calls = []

    async def _spy(cid, uid, **k):
        calls.append((cid, uid))
        return []

    monkeypatch.setattr(tt, "load_active_topics_rows", _spy)
    monkeypatch.setitem(AGENT_FLAGS, wp.SHADOW_FLAG, False)
    ws, state = _state_with_ws()
    rep = asyncio.run(wp.project_into_workspace(state))
    assert rep["shadow_flag"] is False
    assert calls == [], "闸关着却取了话题（%s）⇒ 默认关必须是零额外查询" % calls
    assert ws.active_topics == [], "闸关着就不该有话题投进来"


def test_闸开时话题投上且不再列进留空清单(monkeypatch):
    seen = []

    async def _rows(cid, uid, **k):
        seen.append((cid, uid))
        return [{"topic": "答辩 PPT", "goal": True, "importance": 0.8}]

    monkeypatch.setattr(tt, "load_active_topics_rows", _rows)
    monkeypatch.setitem(AGENT_FLAGS, wp.SHADOW_FLAG, True)
    ws, state = _state_with_ws()
    rep = asyncio.run(wp.project_into_workspace(state))
    assert seen == [(13, 3)], "话题出口没被接线（或用了别的 id）"
    assert ws.active_topics and ws.active_topics[0]["topic"] == "答辩 PPT"
    assert "active_topics" in rep["filled"]
    assert "active_topics" not in [z["field"] for z in rep["zero_coverage"]]


def test_话题取数失败只是少投一格主链路照旧(monkeypatch):
    async def _bad(*a, **k):
        raise RuntimeError("库断了")

    monkeypatch.setattr(tt, "load_active_topics_rows", _bad)
    monkeypatch.setitem(AGENT_FLAGS, wp.SHADOW_FLAG, True)
    ws, state = _state_with_ws()
    rep = asyncio.run(wp.project_into_workspace(state))
    assert ws.active_topics == []
    assert rep["shadow_flag"] is True and "identity" in rep["filled"], "话题炸了不该连累别的字段"


def test_DEFER_REASONS不再列active_topics但字段清单仍是15格():
    assert "active_topics" not in wp.DEFER_REASONS
    assert len(wp.PROJECTED_FIELDS) == 15
    assert wp.PROJECTED_FIELDS.count("active_topics") == 1
