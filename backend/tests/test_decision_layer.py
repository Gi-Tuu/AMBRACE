# -*- coding: utf-8 -*-
"""决策层阶段 0 测试（2026-09-25）：薄抽象透传 + 影子留痕，零行为变化。

守的六条底线（禁止为绿放宽）：
1. ``decision_layer_shadow`` 默认关；关时三原语只做一次透传，不建记录、不碰写库通道、不缓冲；
2. 关/开两态下返回值与直调 ``legacy()`` 相同（同一对象，含 int/str 类型不变）；
3. 开时一条决策＝一行 ``agent_task_logs``，``route='decision_layer_shadow'``，字段齐
   （state/question/output/confidence/latency_ms/source='legacy'）；
4. ``legacy()`` 抛异常 ⇒ 照常向外抛（本层不吞业务异常），且两态都不留痕；
5. ``confidence`` 恒为 None（未校准），落库 JSON 里必须是 null，不得被替换成 0/1；
6. 留痕写库失败 ⇒ 决策返回值不受影响（fail-open，只记 WARNING）；挂点 B 走内存缓冲，
   调用点**绝不**出现阻塞式写库（无事件循环时整批留在缓冲）。

DB 用例统一走 tmp_path 临时库（_dbclone 模板克隆）+ patch async_session_factory，不触生产库。
"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent.loop import AGENT_FLAGS
from app.domain.decision import layer as dl

pytestmark = pytest.mark.slow

_FLAG = dl.FLAG_KEY
_ROUTE = dl.SHADOW_ROUTE


# ────────────────────────── 夹具与查询helper ──────────────────────────

@pytest.fixture()
def shadow_db(monkeypatch, tmp_path):
    """临时库（模板克隆）：patch async_session_factory，shadow 写库全部落在临时库上。"""
    engine = clone_engine(os.path.join(str(tmp_path), "dl.db"))
    factory = make_session_factory(engine)
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    dl.reset_shadow_state()
    yield factory
    dl.reset_shadow_state()
    engine.sync_engine.dispose()


async def _afetch(factory):
    from app.models.agent import AgentTaskLog
    async with factory() as db:
        r = await db.execute(select(AgentTaskLog).where(AgentTaskLog.route == _ROUTE)
                             .order_by(AgentTaskLog.id))
        return [{"trigger": x.trigger, "route": x.route, "status": x.status,
                 "latency_ms": x.latency_ms, "character_id": x.character_id, "user_id": x.user_id,
                 "steps": json.loads(x.steps_json or "{}")}
                for x in r.scalars().all()]


def _fetch(factory):
    return asyncio.run(_afetch(factory))


async def _drain(timeout: float = 10.0) -> None:
    """等所有在途 fire-and-forget 后台任务真正结束（不用固定 sleep 赌时序）。"""
    cur = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not cur and not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=timeout)


def _flag(monkeypatch, on: bool):
    monkeypatch.setitem(AGENT_FLAGS, _FLAG, on)


# ────────────────────────── ①② flag 关：零行为 ──────────────────────────

def test_新键默认关():
    assert AGENT_FLAGS.get(_FLAG) is False, "阶段 0 必须默认关（零行为变化）"


def test_flag关_不写库不缓冲且返回值与直调legacy相同(shadow_db, monkeypatch):
    import app.agent.trace as trace
    calls = []
    monkeypatch.setattr(trace, "enqueue_task_log", lambda **kw: calls.append(kw))
    _flag(monkeypatch, False)
    obj = SimpleNamespace(tag="原样交回")

    async def _go():
        assert dl.ask_noul("s", "q", legacy=lambda: True) == (True, None)
        assert dl.ask_choice("s", "q", ("a", "b"), legacy=lambda: "b") == ("b", None)
        assert dl.ask_score("s", "q", 1, 5, legacy=lambda: 4) == (4, None)
        got, conf = dl.ask_choice("s", "q", (), legacy=lambda: obj)
        assert got is obj and conf is None          # 透传同一对象，不复制不改类型
        dl.observe_tense_decision({"content": "明天去长沙"}, "plan")
        await _drain()
    asyncio.run(_go())

    assert calls == []                              # 连写库通道都没碰过
    assert dl.shadow_buffer_size() == 0             # 缓冲也没进东西
    assert _fetch(shadow_db) == []


# ────────────────────────── ③ 开：恰好一条、字段齐、路由正确 ──────────────────────────

def test_flag开_score恰写一条且字段齐(shadow_db, monkeypatch):
    _flag(monkeypatch, True)

    async def _go():
        out = dl.ask_score("用户说明天要去长沙", "这条记忆有多重要", 1, 5,
                           legacy=lambda: 4, hook="memory_star_rating", character_id=13)
        assert out == (4, None)
        await _drain()                              # 等 fire-and-forget 落库真正完成
    asyncio.run(_go())

    rows = _fetch(shadow_db)
    assert len(rows) == 1, "一条决策＝恰好一行"
    row = rows[0]
    assert row["route"] == _ROUTE and row["trigger"] == dl.SHADOW_TRIGGER
    assert row["status"] == "ok" and row["character_id"] == 13
    s = row["steps"]
    assert {"primitive", "hook", "state", "question", "output", "confidence",
            "latency_ms", "source"} <= set(s)
    assert s["primitive"] == "score" and s["hook"] == "memory_star_rating"
    assert s["state"] == "用户说明天要去长沙" and s["output"] == 4
    assert s["source"] == "legacy" and (s["lo"], s["hi"]) == (1, 5)
    assert s["output_in_range"] is True
    assert row["latency_ms"] == s["latency_ms"] >= 0


def test_flag开_choice与noul各写一条并带候选留痕(shadow_db, monkeypatch):
    _flag(monkeypatch, True)

    async def _go():
        assert dl.ask_choice("明天去长沙", "这是往事还是安排", dl.TENSE_OPTIONS,
                             legacy=lambda: "plan", hook="memory_tense") == ("plan", None)
        assert dl.ask_noul("在吗", "是否紧急", legacy=lambda: False) == (False, None)
        await _drain()
    asyncio.run(_go())

    rows = _fetch(shadow_db)
    assert len(rows) == 2
    # 2026-09-26：两条影子留痕各自 fire-and-forget 写入，xdist 重负载下入库先后不保证
    # （实测全量跑出现过 id 互换）⇒ 按 primitive 取行，不依赖下标顺序（本用例只验内容）。
    by_primitive = {r["steps"]["primitive"]: r["steps"] for r in rows}
    assert set(by_primitive) == {"choice", "noul"}
    choice, noul = by_primitive["choice"], by_primitive["noul"]
    assert choice["output"] == "plan"
    assert choice["options"] == list(dl.TENSE_OPTIONS) and choice["output_in_options"] is True
    assert noul["output"] is False


def test_confidence恒为None_不得被替换成0或1(shadow_db, monkeypatch):
    _flag(monkeypatch, True)

    async def _go():
        dl.ask_score("s", "q", 1, 5, legacy=lambda: 3)
        await _drain()
    asyncio.run(_go())

    rows = _fetch(shadow_db)
    assert len(rows) == 1
    assert rows[0]["steps"]["confidence"] is None
    assert '"confidence": null' in json.dumps(rows[0]["steps"], ensure_ascii=False)


# ────────────────────────── ④ 业务异常照常抛出 ──────────────────────────

def test_legacy抛异常照常向外抛且不留痕(shadow_db, monkeypatch):
    def _boom():
        raise ValueError("业务异常不得被观测层吞掉")

    for on in (False, True):
        _flag(monkeypatch, on)

        async def _go():
            with pytest.raises(ValueError):
                dl.ask_score("s", "q", 1, 5, legacy=_boom, hook="memory_star_rating")
            await _drain()
        asyncio.run(_go())
        assert _fetch(shadow_db) == [], f"flag={on}：legacy 抛异常时不得留痕"


# ────────────────────────── ⑤⑥ fail-open ──────────────────────────

def test_direct写库通道异常不影响返回值(shadow_db, monkeypatch):
    import app.agent.trace as trace

    def _explode(**kw):
        raise RuntimeError("trace 通道炸了")

    _flag(monkeypatch, True)
    monkeypatch.setattr(trace, "enqueue_task_log", _explode)
    assert dl.ask_score("s", "q", 1, 5, legacy=lambda: 5, hook="memory_star_rating") == (5, None)
    assert dl.shadow_buffer_size() == 0


def test_buffer批量写库失败只留WARNING不影响判定(shadow_db, monkeypatch):
    _flag(monkeypatch, True)
    import app.db.database as db_mod

    def _dead_factory():
        raise RuntimeError("db down")
    monkeypatch.setattr(db_mod, "async_session_factory", _dead_factory)

    async def _go():
        for i in range(dl._BUFFER_FLUSH_COUNT):
            dl.observe_tense_decision({"content": f"明天去长沙{i}"}, "plan")
        await _drain()                   # 攒满自动交出这一批
    asyncio.run(_go())

    assert dl.shadow_buffer_size() == 0             # 批次已被交出（写失败也不回灌死循环）
    assert _fetch(shadow_db) == []                  # 一条都没落，但没有任何异常冒到调用方


def test_挂点B_无事件循环时留在缓冲不做阻塞式写库(shadow_db, monkeypatch):
    _flag(monkeypatch, True)

    for i in range(3):                              # 纯同步调用点（无 running loop）
        dl.observe_tense_decision({"content": "明天去长沙"}, "plan", character_id=None)

    assert dl.shadow_buffer_size() == 3             # 整批留在缓冲
    assert _fetch(shadow_db) == []                  # 没有同步写库、也没抛
    assert dl.flush_shadow_buffer() == 0            # 无循环时明确交不出去


# ────────────────────────── ⑦ 攒满即批量落库（一决策一行） ──────────────────────────

def test_buffer攒满自动批量落库_调用点不阻塞(shadow_db, monkeypatch):
    _flag(monkeypatch, True)

    async def _go():
        sizes = []
        for i in range(dl._BUFFER_FLUSH_COUNT - 1):          # 差一条不触发攒批
            dl.observe_tense_decision({"content": f"下周出差{i}", "memory_type": "event"}, "plan")
            sizes.append(dl.shadow_buffer_size())
        written_during_call = await _afetch(shadow_db)       # 调用点全程没有写库
        dl.observe_tense_decision({"content": "下周出差最后一批", "memory_type": "event"}, "plan")
        auto = dl.shadow_buffer_size()                        # 第 N 条 ⇒ 自动整批交出
        await _drain()
        return sizes, written_during_call, auto
    sizes, written_during_call, auto = asyncio.run(_go())

    assert sizes == list(range(1, dl._BUFFER_FLUSH_COUNT))
    assert written_during_call == []
    assert auto == 0                                          # 缓冲已清空，落库交给后台任务
    rows = _fetch(shadow_db)
    assert len(rows) == dl._BUFFER_FLUSH_COUNT                # 一决策一行
    assert {r["steps"]["hook"] for r in rows} == {"memory_tense"}
    assert all(r["steps"]["output"] == "plan" and r["steps"]["confidence"] is None for r in rows)
    assert all(r["steps"]["context"]["via"] == "rule" for r in rows), "无 tense_hint 时留痕应标明来自规则"
    assert all(r["steps"]["context"]["input"]["plan_markers"] == ["下周", "出差"] for r in rows)


# ────────────────────────── ⑧ 挂点 A：真实调用点两态对照 ──────────────────────────

def _run_rate_batch(monkeypatch, flag_on: bool, llm_text: str):
    """跑一次真实挂点 A（LLM 打桩，不落记忆表）：返回 _rate_batch 的原始输出。"""
    import app.agent.llm_client as llm
    import app.memory.ai_rating as ai_rating
    _flag(monkeypatch, flag_on)

    async def _fake(**kwargs):
        return llm_text
    monkeypatch.setattr(llm, "chat_completion", _fake)
    items = [SimpleNamespace(id=11, memory_type="event", content="用户说明天要去长沙出差"),
             SimpleNamespace(id=12, memory_type="preference", content="用户喜欢吃香菜"),
             SimpleNamespace(id=13, memory_type="event", content="用户上周看完了那部剧")]
    char = SimpleNamespace(id=13, name="小暖", user_id=1)

    async def _go():
        out = await ai_rating._rate_batch(char, items)
        await _drain()
        return out
    return asyncio.run(_go())


def test_挂点A_评星开关两态返回值逐字相同(shadow_db, monkeypatch):
    llm_text = '[{"id": 11, "star": 4}, {"id": 12, "star": 9}, {"id": 13, "star": "3"}]'
    off = _run_rate_batch(monkeypatch, False, llm_text)
    rows_off = _fetch(shadow_db)
    on = _run_rate_batch(monkeypatch, True, llm_text)
    rows_on = _fetch(shadow_db)

    assert repr(off) == repr(on), "包装前后返回值必须逐字相同"
    assert repr(off) == "[{'id': 11, 'star': 4}, {'id': 12, 'star': 5}]"
    assert all(type(r["star"]) is int for r in on), "透传不得把 int 星分变成 float"
    assert rows_off == [] and len(rows_on) == 2
    # direct 形态是 fire-and-forget，两行落库先后不保证 ⇒ 按 memory_id 取
    by_mem = {r["steps"]["context"]["memory_id"]: r for r in rows_on}
    assert sorted(by_mem) == [11, 12]
    assert by_mem[11]["steps"]["output"] == 4 and by_mem[11]["steps"]["context"]["raw_star"] == 4
    assert by_mem[12]["steps"]["output"] == 5 and by_mem[12]["steps"]["context"]["raw_star"] == 9
    assert by_mem[11]["steps"]["state"] == "用户说明天要去长沙出差"
    assert all(r["steps"]["hook"] == "memory_star_rating" and r["character_id"] == 13
               for r in rows_on)


# ────────────────────────── ⑨ 挂点 B：判定结果两态对照（适配器层） ──────────────────────────

_CORPUS = [
    {"title": "行程", "content": "用户明天要去长沙出差", "memory_type": "event", "sub_type": ""},
    {"title": "回长沙", "content": "用户已经从长沙回来了", "memory_type": "event", "sub_type": ""},
    {"title": "常驻地", "content": "用户长期在湛江", "memory_type": "user_info", "sub_type": "location"},
    {"title": "口味", "content": "用户不吃香菜", "memory_type": "preference", "sub_type": ""},
    {"title": "群聊", "content": "群里说明天一起去旅游", "memory_type": "shared", "sub_type": "group"},
]


def test_挂点B_时态判定两态逐字相同且开时一决策一行(shadow_db, monkeypatch):
    from app.memory.tense import classify_tense

    def _labels(flag_on: bool):
        _flag(monkeypatch, flag_on)

        async def _go():
            got = []
            for m in _CORPUS:
                label = classify_tense(m)            # 规则判定（本体未改动）
                dl.observe_tense_decision(m, label)  # 调用方留痕：只观测，不参与判定
                got.append(label)
            assert dl.shadow_buffer_size() == (len(_CORPUS) if flag_on else 0)
            dl.flush_shadow_buffer()
            await _drain()
            return got
        return asyncio.run(_go())

    off = _labels(False)
    rows_off = _fetch(shadow_db)
    on = _labels(True)
    rows_on = _fetch(shadow_db)

    assert repr(off) == repr(on) == repr(["plan", "episodic", "enduring", "enduring", "episodic"])
    assert rows_off == [] and len(rows_on) == len(_CORPUS)
    assert [r["steps"]["output"] for r in rows_on] == on
    assert all(r["steps"]["primitive"] == "choice" and r["steps"]["source"] == "legacy"
               for r in rows_on)
    inputs = [r["steps"]["context"]["input"] for r in rows_on]
    assert inputs[0]["plan_markers"] == ["明天", "行程", "出差", "要去"]
    assert inputs[0]["memory_type"] == "event" and inputs[0]["sub_type"] == ""
    assert inputs[1]["done_markers"] == ["回来了"]
    assert inputs[2]["sub_type"] == "location"
    assert inputs[4]["happened_source"] is True
    assert all(r["steps"]["context"]["via"] == "rule" for r in rows_on)


def test_挂点B_tense_hint走留痕不冒充规则判定(shadow_db, monkeypatch):
    _flag(monkeypatch, True)

    async def _go():
        dl.observe_tense_decision({"content": "用户说明天要去长沙"}, "episodic", tense_hint="episodic")
        assert dl.shadow_buffer_size() == 1
        dl.flush_shadow_buffer()
        await _drain()
    asyncio.run(_go())

    rows = _fetch(shadow_db)
    assert len(rows) == 1
    assert rows[0]["steps"]["output"] == "episodic"
    assert rows[0]["steps"]["context"]["via"] == "tense_hint"
    assert rows[0]["steps"]["context"]["tense_hint"] == "episodic"


# ────────────────────────── ⑩ 挂点 B 生产接线：format_memory_line 生效值处留痕 ──────────────────────────

def test_format接线_按生效label调用observe且透传tense_hint(monkeypatch):
    """格式化路径必须把**生效的** ``_tcls``（含 tense_hint 覆盖 / is_happened_source 短路）与被覆盖前的
    ``tense_hint`` 一并交给 observe —— 打桩 recorder 直接核对入参，不依赖 flag。"""
    import app.domain.decision as decision_pkg
    from app.memory.format import format_memory_line

    calls = []
    monkeypatch.setattr(
        decision_pkg, "observe_tense_decision",
        lambda m, label, *, tense_hint=None: calls.append((label, tense_hint)))

    # 规则来源：event + 计划词 → classify_tense 生效值 = plan（tense_hint 未给）
    format_memory_line({"content": "用户明天要去长沙出差", "memory_type": "event"})
    assert calls[-1] == ("plan", None)

    # 已发生来源短路：sub_type=moment → 生效值恒 episodic（tense_hint 仍为 None）
    format_memory_line({"content": "昨天一起看了电影", "sub_type": "moment"})
    assert calls[-1] == ("episodic", None)

    # 显式 tense_hint 覆盖：生效值取 tense_hint（而非规则的 plan），且原样透传 tense_hint
    format_memory_line({"content": "明天去长沙", "memory_type": "event"}, tense_hint="episodic")
    assert calls[-1] == ("episodic", "episodic")

    assert len(calls) == 3, "每次格式化恰好一次留痕"


def test_format接线_开关两态经真实格式化路径且返回文本逐字相同(shadow_db, monkeypatch):
    """关：格式化路径零缓冲、零 ``decision_layer_shadow`` 行；开：调用点只入内存缓冲不同步写库，
    交后台任务后恰好一决策一行。两态 ``format_memory_line`` 返回文本必须逐字一致。"""
    from app.memory.format import format_memory_line
    m = {"content": "用户明天要去长沙出差", "memory_type": "event"}

    dl.reset_shadow_state()
    _flag(monkeypatch, False)
    text_off = format_memory_line(m)
    assert dl.shadow_buffer_size() == 0
    assert _fetch(shadow_db) == []                    # 关：零行为、零留痕

    dl.reset_shadow_state()
    _flag(monkeypatch, True)
    text_on = format_memory_line(m)
    assert text_on == text_off, "接线不得改变返回文本"
    assert dl.shadow_buffer_size() == 1               # 开：同步调用点只入缓冲
    assert _fetch(shadow_db) == []                    # 尚未落库（无阻塞式写库）

    async def _go():
        assert dl.flush_shadow_buffer() == 1
        await _drain()
    asyncio.run(_go())

    rows = _fetch(shadow_db)
    assert len(rows) == 1
    assert rows[0]["steps"]["hook"] == "memory_tense" and rows[0]["steps"]["output"] == "plan"
    assert rows[0]["steps"]["context"]["via"] == "rule"
