# -*- coding: utf-8 -*-
"""批 61（2026-10-11）：编纂层**写侧语义查重**的守卫（`app/events/facts.py`）。

为什么必须做这一档（实测，见 output/b61_semantic.txt／b61_novel_plan.txt）：
生产既有字面判据 `_same_curated_value`（C13）在 72 条 active curated 上归并出 **0 簇**；
字符二元组两两最高只有 **0.56**；而「用户与 sam 是伴侣」这一件事被写成 **13 条**换措辞的行。
⇒ 字面判据结构性够不到，页面只会越堆越多。

判据两道守护，**顺序固定**（这条顺序就是成本与安全的核心）：
① 先做纯字符串的**实义零新增**判断（同义折叠＋去虚词，`curated_is_covered`）——带新信息的新条
   在这里就放过去、**一次推理都不做**；
② 才比本地 bge-m3 余弦（≥0.85，线是量出来的），防「措辞覆盖但其实是两件事」。
任何异常（模型缺失／推理失败）**fail-open＝照常新增**，绝不把写入卡死。

⚠ 测试口径如实：桩向量测的是**接线、守护顺序与落库结果**，**不是** bge-m3 的语义能力
（真模型的余弦分布由 `output/b61_semantic_measure.py` 在真模型上离线量过并据此定线；
CI 跑的裁剪树里没有模型文件，真模型用例在 CI 根本没法跑）。
「覆盖守护没命中时一次都不调模型」这类断言，正是防止后来者把推理挪到守护前面。

纪律：临时库走 `tmp_path`＋`_dbclone`；不碰生产库；零 LLM、零计费。
（项目未装 pytest-asyncio，统一 asyncio.run。）
"""
import asyncio
import math
import os
from collections import OrderedDict

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.events import facts as F
from app.models.memory import WorldFact

pytestmark = pytest.mark.slow

CHAR = 11
USER = 1
GATE = "memory_admission_gate"
TAG = "perception_source_tag"

# 库里已有的那条（生产真实形状）
OLD_ROW = "用户是我的老公，我们关系亲密稳定"
# 只是换了措辞、没有新信息 ⇒ 应当被并掉
PARAPHRASE = "用户与sam是伴侣，sam是用户的老公"
# 带新信息（住／起）⇒ 覆盖守护就拦下，不许做推理
NEW_INFO = "用户是我老公，我们住在一起"


@pytest.fixture()
def cf_db(monkeypatch, tmp_path):
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(F, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _isolate_vec_cache(monkeypatch):
    """向量缓存是模块级单例——**不清就会串用例**（实测：桩向量换了，命中旧缓存后余弦恒 1，
    「相似度不足该多留一条」那几条直接假绿）。"""
    monkeypatch.setattr(F, "_VEC_CACHE", OrderedDict())


def _stub(monkeypatch, table, default=(1.0, 0.0)):
    """桩向量：**按整条正文精确取向量**（早先用「关键词包含」取向量时，一条同时含
    「老公」「伴侣」的句子会被先命中的关键词劫持，测出来的根本不是我想测的那条分支）。
    返回调用清单，用来钉「守护先于推理」与缓存命中。"""
    calls = []

    async def fake(text):
        calls.append(text)
        return list(table.get(text, default))

    import app.memory.embedding as emb
    monkeypatch.setattr(emb, "text_embedding", fake)
    return calls


def _u(cos):
    """与 (1,0) 余弦恰好＝cos 的单位向量（测阈值边界，不用近似）。"""
    return [cos, math.sqrt(max(0.0, 1.0 - cos * cos))]


def _pool_row(rid, text, kind=None, source="extractor"):
    return WorldFact(id=rid, character_id=CHAR, kind=kind or F.KIND_RELATION_BASE,
                     object_value=text, source=source, status="active")


def _match(monkeypatch, obj, kind=None, pool=None, source="extractor", th=None):
    kw = {} if th is None else {"threshold": th}
    return asyncio.run(F._semantic_curated_match(
        obj, kind or F.KIND_RELATION_BASE,
        pool if pool is not None else [_pool_row(1, OLD_ROW)], source, **kw))


def _gate(monkeypatch, **kv):
    for k, v in kv.items():
        monkeypatch.setitem(_loop.AGENT_FLAGS, k, v)


def _curate(factory, *, value, kind=None, source="extractor", **kw):
    async def _run():
        async with factory() as db:
            row = await F.assert_curated(db, character_id=CHAR, user_id=USER,
                                         kind=kind or F.KIND_RELATION_BASE,
                                         object_value=value, source=source,
                                         verify_state=F.VERIFY_MACHINE, **kw)
            await db.commit()
            return getattr(row, "id", None)
    return asyncio.run(_run())


def _active(factory, kind=None):
    async def _run():
        async with factory() as db:
            rows = (await db.execute(select(WorldFact).where(
                WorldFact.character_id == CHAR, WorldFact.status == "active",
                WorldFact.kind == (kind or F.KIND_RELATION_BASE)).order_by(WorldFact.id)
            )).scalars().all()
            return [(r.id, r.object_value, r.sources_json) for r in rows]
    return asyncio.run(_run())


# ─────────────────────── 纯函数：实义零新增判据 ───────────────────────

def test_纯函数_同义折叠与虚词不计():
    assert F.curated_is_covered(PARAPHRASE, OLD_ROW) is True      # 换措辞＝没有新信息
    assert F.curated_is_covered(NEW_INFO, OLD_ROW) is False       # 「住在一起」是新信息
    assert F.curated_is_covered("", OLD_ROW) is False             # 空内容不许并
    assert F.curated_is_covered(OLD_ROW, PARAPHRASE) is False     # 反向：信息多的不被少的覆盖
    # 丈夫／夫夫／老公 折成同一个词；实义字（关／密／稳）仍算信息
    assert F.curated_content_chars("用户有丈夫sam，两人是夫夫") == \
        F.curated_content_chars("用户是sam的老公")
    assert "关" in F.curated_content_chars(OLD_ROW)
    assert "住" not in F.curated_content_chars(OLD_ROW)


def test_阈值取自常量不许散写():
    assert F.CURATED_SEMANTIC_THRESHOLD == 0.85


# ───────────────── 语义档本体：守护顺序／过滤／阈值／fail-open ─────────────────

def test_守护在先_带新信息一次推理都不做(monkeypatch):
    """成本与安全的核心：新条有独有实义词时直接放行，**不许碰模型**。
    把守护挪到推理之后（或删掉）→ 本例当场红。"""
    calls = _stub(monkeypatch, {})
    assert _match(monkeypatch, NEW_INFO, pool=[_pool_row(1, OLD_ROW)]) is None
    assert calls == []


def test_语义命中_返回被覆盖的那条(monkeypatch):
    _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: [1.0, 0.0]})
    hit = _match(monkeypatch, PARAPHRASE)
    assert hit is not None and hit.id == 1


def test_相似度不足_不许只靠覆盖就并(monkeypatch):
    """反向钉：覆盖判据是字符级的、本来就粗；向量不够像就得多留一条，否则等于给误并开口子。"""
    _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: [0.0, 1.0]})
    assert _match(monkeypatch, PARAPHRASE) is None


def test_阈值边界_等于线才并(monkeypatch):
    _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: _u(0.85)})
    assert _match(monkeypatch, PARAPHRASE) is not None
    # 换桩＝换模型输出；缓存按（行 id, 正文）键存，必须清掉才能测第二档相似度
    F._VEC_CACHE.clear()
    _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: _u(0.849)})
    assert _match(monkeypatch, PARAPHRASE) is None


def test_跨kind不互并(monkeypatch):
    """铁律不许吞长期偏好（跨层翻动是既有禁令；语义档比字面档更严——只在同 kind 内比）。"""
    calls = _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: [1.0, 0.0]})
    pool = [_pool_row(1, OLD_ROW, kind=F.KIND_CONSTRAINT)]
    assert _match(monkeypatch, PARAPHRASE, kind=F.KIND_RELATION_BASE, pool=pool) is None
    assert calls == []          # 连推理都不该发生


def test_跨来源禁令在语义档同样成立(monkeypatch):
    """批 0-2 M1a：感知来源与非感知来源不互并，否则「屏幕上看到的东西」会并成关系基线。"""
    _gate(monkeypatch, **{TAG: True})
    _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: [1.0, 0.0]})
    pool = [_pool_row(1, OLD_ROW, source="chat")]
    assert _match(monkeypatch, PARAPHRASE, pool=pool, source="perception") is None


def test_模型坏了好过卡死_fail_open(monkeypatch):
    async def boom(text):
        raise RuntimeError("本地向量模型缺失")

    import app.memory.embedding as emb
    monkeypatch.setattr(emb, "text_embedding", boom)
    assert _match(monkeypatch, PARAPHRASE) is None       # 不抛异常，交回调用侧照常新增


def test_向量缓存_池里同一行不重复推理(monkeypatch):
    """池里那些行每轮重推＝白烧 CPU：第一次要推（新条＋池行各一次），第二次起池行命中缓存。"""
    calls = _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: [1.0, 0.0]})
    pool = [_pool_row(1, OLD_ROW)]
    assert _match(monkeypatch, PARAPHRASE, pool=pool) is not None
    assert len(calls) == 2, f"第一次应当推两条（新条＋池行），实推 {len(calls)} 次"
    calls.clear()
    assert _match(monkeypatch, PARAPHRASE, pool=pool) is not None
    assert OLD_ROW not in calls, f"池行没走缓存，每轮白烧 CPU：{calls}"


# ─────────────────── 落库面：assert_curated 的真实结局 ───────────────────

def test_闸门关_零推理且照常新增两行(cf_db, monkeypatch):
    """闸门关＝逐字节旧行为：不碰模型，字面不命中就各写一行。"""
    _gate(monkeypatch, **{GATE: False})
    calls = _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: [1.0, 0.0]})
    _curate(cf_db, value=OLD_ROW, sources=[{"src": "chat_extract", "message_id": 1}])
    _curate(cf_db, value=PARAPHRASE, sources=[{"src": "chat_extract", "message_id": 2}])
    assert len(_active(cf_db)) == 2
    assert calls == []


def test_语义命中_库里只留一行且证据并入(cf_db, monkeypatch):
    """端到端落库（不是打桩测调用）：换措辞的同一件事**不多开行**，新证据并进既有行。"""
    _gate(monkeypatch, **{GATE: True})
    _stub(monkeypatch, {OLD_ROW: [1.0, 0.0], PARAPHRASE: [0.97, 0.25]})
    rid = _curate(cf_db, value=OLD_ROW, sources=[{"src": "chat_extract", "message_id": 1}])
    rid2 = _curate(cf_db, value=PARAPHRASE, sources=[{"src": "chat_extract", "message_id": 2}])
    rows = _active(cf_db)
    assert rid2 == rid and len(rows) == 1, "语义同一条却新写了一行＝去重没落到库里"
    assert '"message_id": 2' in rows[0][2], "新证据没并入既有行"
    assert rows[0][1] == OLD_ROW, "代表行正文被改写＝不许（并档只并证据，不动文字）"


def test_带新信息_照常新增一行(cf_db, monkeypatch):
    _gate(monkeypatch, **{GATE: True})
    calls = _stub(monkeypatch, {OLD_ROW: [1.0, 0.0]})
    _curate(cf_db, value=OLD_ROW, sources=[{"src": "chat_extract", "message_id": 1}])
    _curate(cf_db, value=NEW_INFO, sources=[{"src": "chat_extract", "message_id": 2}])
    assert len(_active(cf_db)) == 2
    assert calls == []


def test_模型缺失_写入不受影响(cf_db, monkeypatch):
    """CI 的裁剪树里没有 bge-m3 文件＝这条就是那条路的形状。"""
    _gate(monkeypatch, **{GATE: True})

    async def boom(text):
        raise RuntimeError("本地向量模型缺失")

    import app.memory.embedding as emb
    monkeypatch.setattr(emb, "text_embedding", boom)
    _curate(cf_db, value=OLD_ROW, sources=[{"src": "chat_extract", "message_id": 1}])
    _curate(cf_db, value=PARAPHRASE, sources=[{"src": "chat_extract", "message_id": 2}])
    assert len(_active(cf_db)) == 2


def test_接入点唯一_不许别处私调语义档():
    """源码守卫：全仓只允许「定义 1 处＋assert_curated 里 1 处调用」。
    别处再调一次＝第二套去重口径（本项目反复登记过的口径病）。"""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "events", "facts.py"),
               encoding="utf-8").read()
    assert src.count("async def _semantic_curated_match") == 1
    assert src.count("await _semantic_curated_match(") == 1
    assert src.count("curated_is_covered(obj, r.object_value)") == 1
