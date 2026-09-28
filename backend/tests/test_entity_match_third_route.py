# -*- coding: utf-8 -*-
"""批 0-11（2026-09-28，雷达 44）：专名确定性匹配「第三路」并入 RRF / 融合重排。

覆盖（派单要求逐条对齐）：
- **专名命中**：字面（surface）/ 别名组（alias）/ 昵称前后缀派生（nickname）三条途径各一例；
- **大小写与全半角**：`MIKE`↔`mike`、`ａｌｉｃｅ`↔`alice` 折叠后命中并记 `folded=True`；
- **未命中不误召**：抽不出词面 / 库中无对应字面 / 跨角色 / 工作记忆 ⇒ 一条都不返回；
- **flag 关＝零行为**：连那条 LIKE 查询都不发，返回与 trace 逐字节旧；
- **并入既有融合**：与向量路重叠即多一路（`_rerank` 每多一路 +5）⇒ 次序翻转，条数与字段集合不变；
- **与批 0-7 的相互作用**：recency 只改次序（新鲜专名条反超旧高分条、条数不变）；邻居块仍只补空缺槽（恒 ≤ limit）。

纪律：临时库走 tests/_dbclone（禁止连生产库）；嵌入/向量/BM25/插件 hook/trace 全打桩，
只观察排序与条数；项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.memory import entity_match as _em
from app.memory import retrieve as _retrieve

pytestmark = pytest.mark.slow

Q_NICK = "阿明喜欢吃什么"          # 抽出的词面＝「阿明」，库里写的是「小明」⇒ 只能靠昵称派生命中
HIT_NICK = "小明只吃辣的"           # 直接对应「阿明」的等价写法
NOISE = "用户说不吃香菜"           # 与查询毫无字面关系的对照条（绝不应被专名路带出）
Q_ALIAS = "我妈最近怎么样"          # 词面＝「我妈」，库里写「母亲」⇒ 别名组命中
HIT_ALIAS = "母亲住院了"


@pytest.fixture(autouse=True)
def clean_flags():
    """本文件任何用例都不依赖本机 runtime_flags 现值：前后复位（含 trace / 批 0-7 两键，防串扰）。"""
    def _reset():
        for k in ("recall_entity_match", "recall_recency_bonus", "recall_neighbor_block",
                  "memory_peak_cutoff", "memory_trace_debug", "perception_source_tag",
                  "perception_isolate"):
            _loop.AGENT_FLAGS[k] = False
    _reset()
    yield
    _reset()


def _entity(on: bool):
    _loop.AGENT_FLAGS["recall_entity_match"] = on


def _recency(on: bool):
    _loop.AGENT_FLAGS["recall_recency_bonus"] = on


# ─────────────────────── 登记面（不碰库） ───────────────────────

def test_新flag已登记且默认关():
    assert _loop.AGENT_FLAGS["recall_entity_match"] is False


def test_新flag已登记展示元数据且order接在批0_7两键之后():
    from app.application.flag_catalog import FLAG_CATALOG

    meta = FLAG_CATALOG["recall_entity_match"]
    assert meta["group"] == "memory"
    assert meta["visible"] is False
    assert meta["order"] == FLAG_CATALOG["recall_neighbor_block"]["order"] + 1
    assert meta["title_zh"] and meta["desc_zh"] and meta["title_en"] and meta["desc_en"]
    assert "default" not in meta and "enabled" not in meta   # 默认值唯一事实源＝AGENT_FLAGS


def test_第三处登记_开关总表已写入本键():
    """登记三处之第三处（docs/feature-flags.md）：漏这里＝热切能改但清单看不到，重演 memory_admission_gate 事故。

    **CI 例外（2026-09-29 修）**：公开仓 CI 跑的是**脱敏快照**（docs/ 只留 changelog.md），
    该文件在 CI checkout 里根本不存在 ⇒ 原先直接 read_text 会 FileNotFoundError 把 CI 打红
    （第 69 棒实测：py3.12/py3.14 全量绿、只有本条挂）。故：文件不在＝跳过（该断言只对内部全仓有意义），
    在则照旧强断言（内部回归不漏）。
    """
    doc_path = Path(__file__).resolve().parents[2] / "docs" / "feature-flags.md"
    if not doc_path.is_file():
        pytest.skip("脱敏快照无 docs/feature-flags.md（CI）；该断言只在内部全仓生效")
    doc = doc_path.read_text(encoding="utf-8")
    assert "recall_entity_match" in doc
    assert "## 十三、批 0-11" in doc


def test_开关面不可用时按关处理():
    """读不到 AGENT_FLAGS ⇒ 第三路按关处理（回退要退得干净）。"""
    saved = _loop.AGENT_FLAGS
    try:
        _loop.AGENT_FLAGS = None
        assert _retrieve._entity_match_on() is False
    finally:
        _loop.AGENT_FLAGS = saved


def test_字典词与常量已定档():
    assert _em.ENTITY_TERMS_MAX == 4
    assert _em.ENTITY_TERM_MIN_LEN == 2          # 单字「妈/猫」噪音大，一律不抽
    assert _em.ENTITY_TERM_MAX_LEN == 12
    assert _em.ENTITY_LITERALS_MAX >= _em.ENTITY_TERMS_MAX
    assert {v for _, v in _em.expand_terms(["阿明"]).values()} <= {"surface", "alias", "nickname"}


# ─────────────────── 归一化：大小写与全半角 ───────────────────

def test_normalize折叠大小写全半角与空白():
    assert _em.normalize("ＭＩＫＥ  只吃  辣的") == _em.normalize("mike 只吃 辣的")
    assert _em.normalize("Mike") == _em.normalize("MIKE") == "mike"
    assert _em.normalize(None) == ""


def test_to_fullwidth是NFKC的逆映射():
    fw = _em.to_fullwidth("mike")
    assert fw == "ｍｉｋｅ"
    assert _em.normalize(fw) == "mike"          # 全角形状折回来与半角等价


def test_pull_literals备三种形状且钳制总数():
    lits = _em.pull_literals(["mike"])
    assert "mike" in lits and "ｍｉｋｅ" in lits          # 原形 / 归一 / 全角
    assert len(_em.pull_literals(["阿明", "小红", "明哥", "张哥"])) <= _em.ENTITY_LITERALS_MAX


# ─────────────────── 抽词：命中面与不误召 ───────────────────

def test_抽词引号与书名号整段当专名():
    assert _em.extract_terms("「长风渡」讲的是什么") == ["长风渡"]
    assert _em.extract_terms("《三体》里的情节") == ["三体"]
    assert _em.extract_terms("「这是一段很长很长的话超过了十二个字所以不该被当成专名来处理」怎么办") == []


def test_抽词字典称谓最长优先():
    """「女朋友」命中后不留「女朋」碎片（最长优先 + 位置占位）。"""
    assert _em.extract_terms("女朋友今天加班") == ["女朋友"]
    assert _em.expand_terms(["女朋友"])["对象"][1] == "alias"
    got = _em.hit_reasons("女友今天加班到很晚", _em.extract_terms("女朋友今天加班"))
    assert got and got[0]["via"] == "alias" and got[0]["literal"] == "女友"


def test_抽词介词锚定人名且拦掉粘连短语():
    assert _em.extract_terms("和阿明聊过这事") == ["阿明"]
    assert _em.extract_terms("和我们聊了会儿") == []      # 「我们」是代词粘连，不是人名
    assert _em.extract_terms("今天外卖到了吗") == []      # 停用词形状像专名但不是专名


def test_抽词拦掉前后缀形状的高频通用词():
    assert _em.extract_terms("小时候养过狗") == []        # 「小时」不是昵称
    assert _em.extract_terms("小区门口快递") == []        # 「小区」不是昵称
    assert _em.extract_terms("老人的东西") == []          # 「老人」不是昵称


def test_抽词只吃一个字不越界():
    """收窄回归钉：贪婪到两字会把「阿明养的那只」抽成「阿明养」、「推荐明哥的店」抽成「荐明哥」。"""
    assert _em.extract_terms("阿明养的那只猫怎么样") == ["阿明"]
    assert _em.extract_terms("推荐明哥的店") == ["明哥"]


def test_抽词钳到四个词面():
    assert len(_em.extract_terms("阿明 小红 明哥 张哥 李姐 王哥")) == _em.ENTITY_TERMS_MAX


def test_昵称派生族与字典词不派生():
    fam = _em.expand_terms(["阿明"])
    assert fam["小明"][1] == "nickname" and fam["明哥"][1] == "nickname"
    assert "阿明" in fam and fam["阿明"][1] == "surface"
    # 字典词（称谓）绝不派生昵称：否则「妈妈」会派生出「小妈」这类把无关条目全捞进来的词面
    assert all(v[1] != "nickname" for v in _em.expand_terms(["妈妈"]).values())


def test_hit_reasons记录途径与折叠且不误召():
    assert _em.hit_reasons(HIT_NICK, ["阿明"])[0]["via"] == "nickname"
    assert _em.hit_reasons("MIKE 只吃辣的", ["mike"])[0]["folded"] is True       # 大小写折叠后才对上
    assert _em.hit_reasons("ｍｉｋｅ 只吃辣的", ["mike"])[0]["folded"] is True  # 全角同理
    assert _em.hit_reasons("mike 只吃辣的", ["mike"])[0]["folded"] is False      # 字面原样命中
    assert _em.hit_reasons(NOISE, ["阿明"]) == []
    assert _em.hit_reasons("", ["阿明"]) == []


# ─────────────────────────── 公共临时库夹具 ───────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 全副作用打桩（嵌入/向量/BM25/插件 hook/trace），只观察专名路与排序。"""
    engine = clone_engine(tmp_path / "ent11.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="ent11_u1", nickname="专名用户"))
            db.add(AICharacter(id=101, user_id=1, name="专名角色101"))
            db.add(AICharacter(id=102, user_id=1, name="专名角色102"))
            await db.commit()

    asyncio.run(_seed())

    import app.memory.service as svc

    async def _embed(_text):
        return [0.0] * 8

    async def _no_hits(**_kw):
        return []

    async def _no_sparse(*_a, **_kw):
        return []

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _no_hits)
    monkeypatch.setattr(svc, "bm25_search", _no_sparse)
    monkeypatch.setattr(_retrieve, "_logger", type("Silent", (), {
        "warning": staticmethod(lambda *_a, **_kw: None),
        "info": staticmethod(lambda *_a, **_kw: None),
    })())   # 专名/融合失败只写日志，用例里静音（保留 .warning 形状，别把 logger 换成裸函数）
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", _noop)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **_kw: None)

    yield {"factory": factory}
    asyncio.run(engine.dispose())


def _add(factory, content, *, minutes_ago=0, importance=50.0, char=101, mtype="event",
         source="chat", **kw):
    """直接落一条记忆（精确控制时间与属性）。"""
    from app.models.memory import Memory
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=char, memory_type=mtype, content=content,
                       source=source, importance=importance,
                       created_at=now_naive_utc() - timedelta(minutes=minutes_ago), **kw)
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _search(query, limit=5):
    return asyncio.run(_retrieve.search_memories(101, query, limit=limit))


def _ids(rows):
    return [r["id"] for r in rows]


# ─────────────────── flag 关＝零行为 ───────────────────

def test_专名_flag关_连那条查询都不发(env, monkeypatch):
    """关＝零行为：专名路函数一次都不被调用；库里有等价写法也不得从这条通道出条。"""
    _add(env["factory"], HIT_NICK)

    def _boom(*_a, **_kw):
        raise AssertionError("flag 关时不应进入专名第三路")

    monkeypatch.setattr(_retrieve, "_entity_route", _boom)
    assert _search(Q_NICK) == []


def test_专名_flag关_trace不含新增键(env, monkeypatch):
    """关 flag 时 steps_json 逐字节旧：不得出现 entity_hits / entity_route 两键。"""
    import json

    captured: list[dict] = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: captured.append(kw))
    _loop.AGENT_FLAGS["memory_trace_debug"] = True
    _entity(False)
    _search(Q_NICK)
    steps = json.loads(captured[-1]["steps_json"])
    assert "entity_hits" not in steps and "entity_route" not in steps
    assert steps["route"] != "entity"


# ─────────────────── flag 开：三条途径各出一例 ───────────────────

def test_专名_flag开_昵称派生命中(env):
    """问「阿明」而记忆写「小明」：向量/BM25 皆空（已打桩），只能由专名路捞出。"""
    hit = _add(env["factory"], HIT_NICK)
    _add(env["factory"], NOISE)
    assert _search(Q_NICK) == []                     # 关＝一条不出
    _entity(True)
    got = _search(Q_NICK)
    assert _ids(got) == [hit]
    assert got[0]["content"] == HIT_NICK


def test_专名_flag开_别名组命中(env):
    """问「我妈」而记忆写「母亲」：走 alias 途径。"""
    hit = _add(env["factory"], HIT_ALIAS)
    assert _search(Q_ALIAS) == []
    _entity(True)
    assert _ids(_search(Q_ALIAS)) == [hit]


def test_专名_flag开_字面直命中(env):
    hit = _add(env["factory"], "阿明今天去菜市场买菜")
    _entity(True)
    assert _ids(_search(Q_NICK)) == [hit]


@pytest.mark.parametrize("content", ["MIKE 只吃辣的", "ｍｉｋｅ 只吃辣的", "mike  只吃 辣的"])
def test_专名_flag开_大小写与全半角都命中(env, content):
    """同一条语义的三种写法都应被折叠命中（SQL 只做粗筛，判定在归一化之后）。"""
    hit = _add(env["factory"], content)
    _entity(True)
    assert _ids(_search("mike 爱吃什么")) == [hit]


# ─────────────────── flag 开：不误召的边界 ───────────────────

def test_专名_flag开_库中无对应字面_一条不出(env):
    """未命中不误召：库里只有与专名无关的条目 ⇒ 返回空（也不得走整句 LIKE 兜底蒙对）。"""
    _add(env["factory"], NOISE)
    _add(env["factory"], "用户三年前学过游泳")
    _entity(True)
    assert _search(Q_NICK) == []


def test_专名_flag开_抽不出词面_不发查询(env, monkeypatch):
    """停用词句（「今天外卖到了吗」）⇒ 词面为空，连 SQL 都不该拼出来。"""
    def _boom(*_a, **_kw):
        raise AssertionError("抽不出词面时不应查库")

    monkeypatch.setattr(_retrieve, "_logger", lambda *a, **kw: None)
    monkeypatch.setattr("app.memory.entity_match.pull_literals", _boom)
    _entity(True)
    assert _search("今天外卖到了吗") == []


def test_专名_flag开_跨角色不带出(env):
    _add(env["factory"], HIT_NICK, char=102)   # 别的角色的条
    _entity(True)
    assert _search(Q_NICK) == []


def test_专名_flag开_工作记忆不带出(env):
    """M3-a 同口径：working_state 属注入专用分区，不得被专名路从侧门灌进召回。"""
    _add(env["factory"], "小明只吃辣的工作态", mtype="working_state")
    _entity(True)
    assert _search(Q_NICK) == []


def test_专名_flag开_粗筛捞进但判定不过的行不留(env):
    """LIKE 用 `%明%` 之类的形状可能捞进「明白」，判定必须以词面整体折叠比对为准。"""
    _add(env["factory"], "用户说明白这个道理了")     # 「明」单字不构成词面「阿明/小明/明哥」
    _entity(True)
    assert _search(Q_NICK) == []


# ─────────────────── 并入既有融合重排 ───────────────────

def test_专名_flag开_与向量路重叠即多一路_次序翻转(env, monkeypatch):
    """重叠命中＝多一路（_rerank 每多一路 +5）：专名只命中 A ⇒ A 反超向量路里更靠前的 B。"""
    import app.memory.service as svc

    b = _add(env["factory"], NOISE, minutes_ago=10, importance=50.0)
    a = _add(env["factory"], HIT_NICK, minutes_ago=20, importance=50.0)

    async def _dense(*_a, **_kw):
        return [{"id": b, "content": NOISE, "type": "event", "importance": 50.0, "distance": 0.1},
                {"id": a, "content": HIT_NICK, "type": "event", "importance": 50.0, "distance": 0.2}]

    monkeypatch.setattr(svc, "vector_search", _dense)
    _entity(False)
    off = _ids(_search(Q_NICK))
    _entity(True)
    on = _ids(_search(Q_NICK))
    assert set(off) == set(on) == {a, b}    # 一条不丢、一条不多
    assert off[0] == b and on[0] == a       # 只有「多一路」这一处差异


def test_专名_flag开_条数不超上限且字段集合不变(env):
    """出口仍受 limit 截断；专名路带出的行与普通行**键集合逐字节一致**（不泄漏内部临时键）。"""
    ids = [_add(env["factory"], f"小明第{i}次去买菜", minutes_ago=i * 10) for i in range(4)]
    _entity(True)
    for lim in (1, 2, 3):
        got = _search(Q_NICK, limit=lim)
        assert len(got) <= lim <= 3
        assert {r["id"] for r in got} <= set(ids)
    got = _search(Q_NICK, limit=3)
    assert set(got[0].keys()) == {"id", "content", "type", "importance", "created_at",
                                  "epistemic_status", "speaker_id", "speaker_type",
                                  "reliability_score", "contradiction_count", "why_it_matters", "status"}
    assert "_score" not in got[0] and "_neighbor" not in got[0]


def test_专名_flag开_trace写入命中与理由(env, monkeypatch):
    """命中原因可查：trace 里 entity_hits + entity_route{terms, reasons[{term,via,literal,folded}]}。"""
    import json

    hit = _add(env["factory"], HIT_NICK)
    captured: list[dict] = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: captured.append(kw))
    _loop.AGENT_FLAGS["memory_trace_debug"] = True
    _entity(True)
    got = _search(Q_NICK)
    assert _ids(got) == [hit]
    steps = json.loads(captured[-1]["steps_json"])
    assert steps["entity_hits"] == [hit]
    assert steps["route"] == "entity"
    reason = steps["entity_route"]["reasons"][0]
    assert reason["id"] == hit and reason["term"] == "阿明"
    assert reason["via"] == "nickname" and reason["literal"] == "小明"


def test_专名_flag开_专名路异常不影响双路(env, monkeypatch):
    """第三路整体 fail-open：抽词/查库任何异常只退化为空路，向量路结果一条不少。"""
    import app.memory.service as svc

    hit = _add(env["factory"], NOISE)

    async def _dense(*_a, **_kw):
        return [{"id": hit, "content": NOISE, "type": "event", "importance": 50.0, "distance": 0.1}]

    def _kaboom(*_a, **_kw):
        raise RuntimeError("抽词炸了")

    monkeypatch.setattr(svc, "vector_search", _dense)
    monkeypatch.setattr("app.memory.entity_match.extract_terms", _kaboom)
    _entity(True)
    assert _ids(_search(Q_NICK)) == [hit]


# ─────────────── 与批 0-7 两键的相互作用（派单要求④） ───────────────

def test_与recency叠加_新鲜专名条反超旧高分条_条数不变(env):
    """专名决定「进不进候选池」，recency 决定「进池后排第几」：两键同开时次序翻转、条数恒等。"""
    old = _add(env["factory"], "小明以前只吃辣的", minutes_ago=25 * 60, importance=60.0)
    new = _add(env["factory"], "小明今天买了辣椒", minutes_ago=60, importance=58.0)
    _entity(True)
    _recency(False)
    assert _ids(_search(Q_NICK)) == [old, new]
    _recency(True)
    on = _search(Q_NICK)
    assert _ids(on) == [new, old]
    assert len(on) == 2                       # 只改次序，不剔除


def test_与邻居块并存_条数恒不超limit(env):
    """专名路把候选池填满 ⇒ 邻居的空缺槽位变少；两键同开仍恒 ≤ limit。"""
    hit = _add(env["factory"], "小明今天去了菜市场", minutes_ago=10)
    _add(env["factory"], "小明顺路买了辣椒", minutes_ago=12)
    _entity(True)
    _loop.AGENT_FLAGS["recall_neighbor_block"] = True
    for lim in (1, 2, 3, 5):
        assert len(_search(Q_NICK, limit=lim)) <= lim
    assert _ids(_search(Q_NICK, limit=1)) == [hit]     # limit=1 已被专名命中占满，邻居无槽可补
