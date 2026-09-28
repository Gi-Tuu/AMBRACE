# -*- coding: utf-8 -*-
"""批 0-7（2026-09-28，雷达 08）：召回排序「显式 recency」＋ 命中记忆的「相邻块」。

覆盖（派单要求逐条对齐）：
- **recency 单调性**：纯函数按档位单调不增，跨档严格递减；边界（24h/7d/30d）落在「含」的一侧；
- **recency 量级**：档位取值与既有 +20/+15/+10 同量级，且不改条数/不剔除；
- **flag 关＝零行为**：两把闸关时排序逐字节旧、且**连邻居那条查询都不发**；
- **相邻块命中/未命中**：窗口内同角色邻居带出、窗口外/跨角色/跨群/工作记忆/已在结果里的条不带出；
- **不挤占**：邻居只补本轮不足 limit 的空缺槽位，本轮取满时邻居完全不进，已有结果逐条不变。

纪律：临时库走 tests/_dbclone（禁止连生产库）；嵌入/向量/BM25/插件 hook/trace 全打桩，
只观察排序与条数；项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.memory import retrieve as _retrieve

pytestmark = pytest.mark.slow

Q = "香菜"                       # 关键词兜底路的命中词（向量/BM25 已打桩为空）
HIT = "用户说不吃香菜"            # 直接命中（正文含 Q）
NBR_NEAR = "用户今天顺路去了菜市场"  # 邻居（正文不含 Q，只能靠时间邻域带出）
NBR_FAR = "用户三年前学过游泳"       # 窗口外/其他用途的对照条


@pytest.fixture(autouse=True)
def clean_flags():
    """本文件任何用例都不依赖本机 runtime_flags 现值：前后复位（含 peak/trace，防串扰）。"""
    def _reset():
        for k in ("recall_recency_bonus", "recall_neighbor_block", "memory_peak_cutoff",
                  "memory_trace_debug", "memory_temporal_recall", "memory_recall_second_hop",
                  "memory_chain_expand", "perception_isolate", "perception_source_tag"):
            _loop.AGENT_FLAGS[k] = False
    _reset()
    yield
    _reset()


def _recency(on: bool):
    _loop.AGENT_FLAGS["recall_recency_bonus"] = on


def _neighbor(on: bool):
    _loop.AGENT_FLAGS["recall_neighbor_block"] = on


# ─────────────────────── 登记面（不碰库） ───────────────────────

def test_两个新flag已登记且默认关():
    assert _loop.AGENT_FLAGS["recall_recency_bonus"] is False
    assert _loop.AGENT_FLAGS["recall_neighbor_block"] is False


def test_两个新flag已登记展示元数据且order接在感知键之后():
    from app.application.flag_catalog import FLAG_CATALOG

    base = FLAG_CATALOG["perception_isolate"]["order"]
    for key, expect in (("recall_recency_bonus", base + 1), ("recall_neighbor_block", base + 2)):
        meta = FLAG_CATALOG[key]
        assert meta["group"] == "memory"
        assert meta["visible"] is False
        assert meta["order"] == expect
        assert meta["title_zh"] and meta["desc_zh"] and meta["title_en"] and meta["desc_en"]
        assert "default" not in meta and "enabled" not in meta   # 默认值唯一事实源＝AGENT_FLAGS


def test_开关面不可用时两把闸一律按关处理():
    """读不到 AGENT_FLAGS ⇒ 两个 _on() 均 False ⇒ 逐字节旧行为（回退要退得干净）。"""
    saved = _loop.AGENT_FLAGS
    try:
        _loop.AGENT_FLAGS = None
        assert _retrieve._recency_bonus_on() is False
        assert _retrieve._neighbor_block_on() is False
    finally:
        _loop.AGENT_FLAGS = saved


# ─────────────────── 任务①：recency 纯函数（单调性 + 边界） ───────────────────

def test_recency档位与既有加分同量级():
    tiers = dict(_retrieve.RECENCY_TIERS)
    assert tiers == {24.0: 20.0, 24.0 * 7: 15.0, 24.0 * 30: 10.0}
    # 与既有 +20（意义记忆）/ +15（关系情绪近 7 天）/ +10（状态剧情近 3 天）同量级
    assert set(tiers.values()) <= {10.0, 15.0, 20.0}
    # 窗口边界必须是 24h / 7d / 30d
    assert list(tiers) == [24.0, 168.0, 720.0]


@pytest.mark.parametrize("age_h", [0, 1, 12, 23.9, 24, 24.1, 48, 167, 168, 169,
                                   500, 719, 720, 721, 1000, 24 * 90])
def test_recency单调性_越老不早于越新(age_h):
    """单调不增：年龄越大 ⇒ 加分越低（同档内相等也属单调）。"""
    f = _retrieve._recency_bonus_hours
    assert f(age_h) >= f(age_h + 0.5)
    for older in (age_h + 1, age_h + 24, age_h + 24 * 30):
        assert f(age_h) >= f(older)


@pytest.mark.parametrize("age_h,expect", [
    (0.0, 20.0),
    (23.9, 20.0),
    (24.0, 20.0),        # 边界含「满 24 小时」
    (24.0001, 15.0),    # 刚过 24h ⇒ 降一档
    (24 * 7, 15.0),     # 满 7 天
    (24 * 7 + 0.1, 10.0),
    (24 * 30, 10.0),    # 满 30 天
    (24 * 30 + 0.1, 0.0),
    (24 * 365, 0.0),    # 老记忆＝0（与旧行为一致）
])
def test_recency边界时间_逐档取值(age_h, expect):
    assert _retrieve._recency_bonus_hours(age_h) == expect


def test_recency跨档严格递减():
    f = _retrieve._recency_bonus_hours
    assert f(0) > f(25) > f(24 * 8) > f(24 * 31)


def test_recency未来时间戳按最新档处理():
    """时钟回拨/脏数据（created_at 在未来）⇒ 按最新档，不因负数落到 0。"""
    assert _retrieve._recency_bonus_hours(-3.0) == 20.0


# ─────────────────── 任务②：就近排序纯函数 ───────────────────

def test_邻居就近排序_距离相等按id稳定():
    from app.utils.timeutil import now_naive_utc

    anchor = now_naive_utc()
    rows = [
        {"id": 5, "created_at": anchor - timedelta(minutes=25)},
        {"id": 6, "created_at": anchor + timedelta(minutes=5)},
        {"id": 7, "created_at": anchor - timedelta(minutes=5)},
        {"id": 8, "created_at": None},           # 脏数据排最后
    ]
    got = [r["id"] for r in _retrieve._pick_nearest(rows, anchor, 3)]
    assert got == [6, 7, 5]                      # ±5 分钟并列 ⇒ id 升序，再 25 分钟
    assert _retrieve._pick_nearest(rows, None, 3) == []      # 锚点无时间 ⇒ 不猜
    assert _retrieve._pick_nearest(rows, anchor, 0) == []
    assert _retrieve._pick_nearest([], anchor, 2) == []


def test_邻居窗口与体积常量已定档():
    assert _retrieve.NEIGHBOR_WINDOW_MINUTES == 30
    assert _retrieve.NEIGHBOR_ANCHOR_MAX == 2
    assert _retrieve.NEIGHBOR_PER_ANCHOR_MAX == 2
    assert _retrieve.NEIGHBOR_QUERY_LIMIT >= _retrieve.NEIGHBOR_PER_ANCHOR_MAX


# ─────────────────────────── 公共临时库夹具 ───────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 全副作用打桩（嵌入/向量/BM25/插件 hook/trace），只观察召回排序与条数。"""
    engine = clone_engine(tmp_path / "rec07.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="rec07_u1", nickname="召回用户"))
            db.add(AICharacter(id=101, user_id=1, name="召回角色101"))
            db.add(AICharacter(id=102, user_id=1, name="召回角色102"))
            await db.commit()

    asyncio.run(_seed())

    import app.memory.service as svc

    async def _embed(_text):
        return [0.0] * 8

    async def _no_hits(**_kw):
        return []

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _no_hits)
    monkeypatch.setattr(svc, "bm25_search", _no_hits)
    monkeypatch.setattr(_retrieve, "_logger", _noop)   # 邻居失败只写日志，用例里静音
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", _noop)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **_kw: None)

    yield {"factory": factory}
    asyncio.run(engine.dispose())


def _add(factory, content, *, minutes_ago=0, importance=50.0, char=101, mtype="event",
         source="chat", group_id=None, **kw):
    """直接落一条记忆（精确控制时间与属性）。"""
    from app.models.memory import Memory
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=char, memory_type=mtype, content=content,
                       source=source, importance=importance,
                       created_at=now_naive_utc() - timedelta(minutes=minutes_ago), **kw)
            if group_id is not None:
                m.group_id = group_id
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _search(query=Q, limit=5):
    return asyncio.run(_retrieve.search_memories(101, query, limit=limit))


def _ids(rows):
    return [r["id"] for r in rows]


# ─────────────────── 任务①：DB 侧 recency 行为 ───────────────────

def test_recency_flag关_排序逐字节旧(env):
    """关＝旧行为：只按 importance 竞争，25h 前的 60 分条仍排在 1h 前的 58 分条之前。"""
    old = _add(env["factory"], "用户说不吃香菜（旧）", minutes_ago=25 * 60, importance=60.0)
    new = _add(env["factory"], "用户说不吃香菜（新）", minutes_ago=60, importance=58.0)
    res = _search()
    assert _ids(res) == [old, new]


def test_recency_flag开_新条反超旧条(env):
    """开＝同一对条次序翻转（+20 vs +15），只改次序、条数不变。"""
    old = _add(env["factory"], "用户说不吃香菜（旧）", minutes_ago=25 * 60, importance=60.0)
    new = _add(env["factory"], "用户说不吃香菜（新）", minutes_ago=60, importance=58.0)
    _recency(True)
    res = _search()
    assert _ids(res) == [new, old]
    assert len(res) == 2          # 不剔除
    assert {r["id"] for r in res} == {old, new}


def test_recency_flag开_30天以上不受影响(env):
    """档位外（>30d）加分为 0 ⇒ 与关 flag 时的相对次序一致（只与同样在档外的条比较）。"""
    a = _add(env["factory"], "用户说不吃香菜（甲）", minutes_ago=45 * 24 * 60, importance=70.0)
    b = _add(env["factory"], "用户说不吃香菜（乙）", minutes_ago=40 * 24 * 60, importance=69.0)
    _recency(False)
    off = _ids(_search())
    _recency(True)
    assert _ids(_search()) == off == [a, b]


def test_recency_flag开_条数与上限不变不剔除(env):
    """候选多于 limit 时仍截到 limit；关/开两侧条数恒等（recency 只动次序）。"""
    ids = [_add(env["factory"], f"用户说不吃香菜{i}", minutes_ago=i * 60, importance=50.0)
           for i in range(6)]
    _recency(False)
    off = _search(limit=3)
    _recency(True)
    on = _search(limit=3)
    assert len(off) == len(on) == 3
    assert len({r["id"] for r in on}) == 3
    assert set(_ids(on)) <= set(ids)


def test_recency_flag开_置顶恒在前(env):
    """置顶 +500 与本档位（≤20）不同量级 ⇒ 老置顶条仍在最前（不被 recency 挤下）。"""
    pin = _add(env["factory"], "用户说不吃香菜（置顶）", minutes_ago=200 * 24 * 60,
               importance=60.0, is_pinned=True)
    fresh = _add(env["factory"], "用户说不吃香菜（新鲜）", minutes_ago=10, importance=55.0)
    _recency(True)
    assert _ids(_search()) == [pin, fresh]


def test_recency_flag开_与意义记忆加分同档不打架(env):
    """同样新鲜度下，why_it_matters(+20) 仍决定胜负（recency 对两条取值相等，不改变既有优势）。"""
    plain = _add(env["factory"], "用户说不吃香菜（无意义）", minutes_ago=30, importance=50.0)
    why = _add(env["factory"], "用户说不吃香菜（有意义）", minutes_ago=30, importance=50.0,
               why_it_matters="关系到饮食照顾")
    _recency(True)
    assert _ids(_search()) == [why, plain]


# ─────────────────── 任务②：相邻块命中 / 未命中 ───────────────────

def test_邻居_flag关_连查询都不发(env, monkeypatch):
    """关＝零行为：邻居扩充函数一次都不被调用，返回与旧路径逐条一致。"""
    hit = _add(env["factory"], HIT, minutes_ago=10)
    _add(env["factory"], NBR_NEAR, minutes_ago=15)
    _add(env["factory"], NBR_NEAR + "（另一条）", minutes_ago=20)

    def _boom(*_a, **_kw):
        raise AssertionError("flag 关时不应进入相邻块扩充")

    monkeypatch.setattr(_retrieve, "_expand_neighbor_blocks", _boom)
    res = _search(limit=3)
    assert _ids(res) == [hit]


def test_邻居_flag开_补空缺槽位且排在命中之后(env):
    hit = _add(env["factory"], HIT, minutes_ago=10)
    near = _add(env["factory"], NBR_NEAR, minutes_ago=15)          # 距锚点 5 分钟
    later = _add(env["factory"], NBR_NEAR + "（更晚）", minutes_ago=3)   # 距锚点 7 分钟
    res = _search(limit=3)
    assert _ids(res) == [hit]
    _neighbor(True)
    got = _search(limit=3)
    assert _ids(got) == [hit, near, later]   # 最近优先，且排在直接命中之后
    assert len(got) == 3


def test_邻居_flag开_本轮取满时完全不进(env):
    """槽位没有空缺 ⇒ 邻居不进；直接命中的逐条内容与次序与关 flag 时一致（不挤占）。"""
    hits = [_add(env["factory"], f"{HIT}{i}", minutes_ago=i * 60) for i in range(3)]
    _add(env["factory"], NBR_NEAR, minutes_ago=1)
    _neighbor(False)
    off = _search(limit=3)
    _neighbor(True)
    on = _search(limit=3)
    assert _ids(on) == _ids(off) == hits    # 一条未被挤掉、一条邻居未进
    assert len(on) == 3


def test_邻居_flag开_窗口外不带出边界含30分钟(env):
    """锚点＝10 分钟前：±30 分钟窗口＝[40 分钟前, 20 分钟后]；31 分钟外的邻居不得带出。"""
    hit = _add(env["factory"], HIT, minutes_ago=10)
    inside_early = _add(env["factory"], NBR_NEAR, minutes_ago=40)     # Δ=30 分钟（含边界）
    outside_early = _add(env["factory"], NBR_NEAR + "（外）", minutes_ago=41)  # Δ=31 分钟
    inside_late = _add(env["factory"], NBR_NEAR + "（后）", minutes_ago=0)     # Δ=10 分钟
    _neighbor(True)
    got = _ids(_search(limit=5))
    assert got[0] == hit
    assert set(got[1:]) == {inside_late, inside_early}   # 近者先：Δ10 再 Δ30
    assert outside_early not in got


def test_邻居_flag开_跨角色不带出(env):
    hit = _add(env["factory"], HIT, minutes_ago=10)
    _add(env["factory"], NBR_NEAR, minutes_ago=12, char=102)   # 别的角色的条
    _neighbor(True)
    assert _ids(_search(limit=3)) == [hit]


def test_邻居_flag开_群记忆只带同群邻居(env):
    anchor = _add(env["factory"], HIT, minutes_ago=10, group_id=7)
    same = _add(env["factory"], NBR_NEAR, minutes_ago=14, group_id=7)
    _add(env["factory"], NBR_NEAR + "（他群）", minutes_ago=16, group_id=8)
    _add(env["factory"], NBR_NEAR + "（无群）", minutes_ago=18, group_id=None)
    _neighbor(True)
    assert _ids(_search(limit=4)) == [anchor, same]


def test_邻居_flag开_工作记忆不带出(env):
    """M3-a 同口径：working_state 属注入专用分区，不得被邻居通道从侧门灌进召回。"""
    hit = _add(env["factory"], HIT, minutes_ago=10)
    _add(env["factory"], NBR_NEAR, minutes_ago=12, mtype="working_state")
    _neighbor(True)
    assert _ids(_search(limit=3)) == [hit]


def test_邻居_flag开_已在结果里的条不重复带出(env):
    """三条直接命中彼此相邻：邻居候选必须排除已返回的 id，不得重复出条。"""
    a = _add(env["factory"], f"{HIT}甲", minutes_ago=10)
    b = _add(env["factory"], f"{HIT}乙", minutes_ago=12)
    c = _add(env["factory"], f"{HIT}丙", minutes_ago=14)
    d = _add(env["factory"], NBR_NEAR, minutes_ago=11)     # 唯一的真邻居（正文不含查询词）
    _neighbor(True)
    got = _search(limit=5)
    assert len(got) == len(set(_ids(got))) == 4            # 无重复 id
    assert set(_ids(got)[:3]) == {a, b, c}                 # 直接命中一条不少、顺序不变
    assert got[3]["id"] == d                                # 空缺槽位只由真邻居填


def test_邻居_flag开_条数不超上限(env):
    _add(env["factory"], HIT, minutes_ago=10)
    for i in range(10):        # 窗口内邻居很多
        _add(env["factory"], f"{NBR_NEAR}{i}", minutes_ago=max(1, 10 - i))
    _neighbor(True)
    for lim in (1, 2, 3, 5):
        assert len(_search(limit=lim)) <= lim


def test_邻居_flag开_输出字段形状与普通条一致(env):
    """邻居同样过一次 _rerank 回填：_final 的键集合不得比直接命中多/少（不泄漏内部临时键）。"""
    hit = _add(env["factory"], HIT, minutes_ago=10)
    nbr = _add(env["factory"], NBR_NEAR, minutes_ago=13)
    _neighbor(True)
    got = _search(limit=3)
    assert len(got) == 2 and _ids(got) == [hit, nbr]
    assert set(got[0].keys()) == set(got[1].keys())
    assert "_score" not in got[1] and "_neighbor" not in got[1]
    assert got[1]["created_at"] is not None


# ─────────── 生产默认侧（memory_trace_debug 开）───────────

def test_recency_trace留痕开_同样生效(env):
    """`memory_trace_debug` 生产为开 ⇒ recency 走的是 _rerank(return_debug=True) 那条口，次序同样翻转。"""
    old = _add(env["factory"], "用户说不吃香菜（旧）", minutes_ago=25 * 60, importance=60.0)
    new = _add(env["factory"], "用户说不吃香菜（新）", minutes_ago=60, importance=58.0)
    _recency(True)
    _loop.AGENT_FLAGS["memory_trace_debug"] = True
    assert _ids(_search()) == [new, old]


def test_邻居_trace留痕开_结果一致且留痕可查(env, monkeypatch):
    """开 trace 只多写留痕：返回与关 trace 逐条一致，且 neighbor_block 记的是锚点与带出条数。"""
    import json

    hit = _add(env["factory"], HIT, minutes_ago=10)
    nbr = _add(env["factory"], NBR_NEAR, minutes_ago=13)
    _neighbor(True)
    _loop.AGENT_FLAGS["memory_trace_debug"] = False
    plain = _ids(_search(limit=3))

    captured: list[dict] = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: captured.append(kw))
    _loop.AGENT_FLAGS["memory_trace_debug"] = True
    traced = _search(limit=3)
    assert _ids(traced) == plain == [hit, nbr]
    steps = json.loads(captured[-1]["steps_json"])
    assert steps["neighbor_block"] == {"anchors": [hit], "added": 1}
