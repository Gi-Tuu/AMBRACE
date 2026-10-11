# -*- coding: utf-8 -*-
"""批 66（A52 剩②）：curated 每角色每 kind 容量软上限的守卫。

治的是「表侧无上限」：注入侧本来就只取 TopN（constraint 8／其余 4），但编纂层一直往里加行，
实测 char13 曾堆到 88 条 ⇒ 世界设定页翻不完。本档超限把**尾部置 superseded（留痕不删）**，
并且只在**下一次写入时**收敛——正常聊天就会把存量压回上限，不需要专门跑一次清库。

关键判据（每条都有对应用例，别当装饰）：
- 保留顺序＝用户权威 ＞ 已核实程度 ＞ 置信度 ＞ 新近；
- `verify_state` **不许按字符串字典序**排（'machine-confirmed' < 'unverified' 语义正好反），
  必须映射成 rank 下推 SQL；
- **本次刚写入的那条不参与淘汰**（否则「记下了」＝「记了但被自己挤掉」，满员后 AI 再也记不进）；
- 淘汰是留痕（status=superseded、正文不动、行不删），且刚过上限只挤一条；
- 容量档坏了不能把编纂写入带崩（fail-open）。

纪律：临时库走 `tmp_path`＋`_dbclone`；不碰生产库；零 LLM、零计费。
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.events import facts as F
from app.models.memory import WorldFact

pytestmark = pytest.mark.slow

CHAR = 11
USER = 1
GATE = "memory_admission_gate"


@pytest.fixture()
def cf_db(monkeypatch, tmp_path):
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(F, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _curate(factory, *, value, kind, **kw):
    async def _run():
        async with factory() as db:
            row = await F.assert_curated(db, character_id=CHAR, user_id=USER, kind=kind,
                                         object_value=value, source="extractor", **kw)
            await db.commit()
            return getattr(row, "id", None)
    return asyncio.run(_run())


def _seed(factory, *, kind, value, verify_state=F.VERIFY_MACHINE, authoritative=False,
          confidence=0.5, author="system", asserted_at=None):
    ts = asserted_at or (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30))
    async def _run():
        async with factory() as db:
            db.add(WorldFact(
                user_id=USER, character_id=CHAR, subject_type="character", subject_id=CHAR,
                predicate="curated", object_value=value, status="active", confidence=confidence,
                epistemic_status="FACT", audience='["public"]', author=author,
                is_authoritative=authoritative, source=author, kind=kind,
                verify_state=verify_state, sources_json="[]", links_json="[]",
                asserted_at=ts,
            ))
            await db.commit()
    asyncio.run(_run())


def _counts(factory, kind):
    async def _run():
        async with factory() as db:
            act = (await db.execute(select(func.count()).select_from(WorldFact).where(
                WorldFact.character_id == CHAR, WorldFact.kind == kind,
                WorldFact.status == "active"))).scalar()
            sup = (await db.execute(select(func.count()).select_from(WorldFact).where(
                WorldFact.character_id == CHAR, WorldFact.kind == kind,
                WorldFact.status == "superseded"))).scalar()
            rows = (await db.execute(select(WorldFact).where(
                WorldFact.character_id == CHAR, WorldFact.kind == kind,
                WorldFact.status == "active").order_by(WorldFact.id))).scalars().all()
            return act, sup, [r.object_value for r in rows]
    return asyncio.run(_run())


def _gate(monkeypatch, on):
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, on)


# ─────────────────────────── 上限本身 ───────────────────────────

def test_上限表就地登记不许散写():
    assert F.CURATED_ACTIVE_CAP == {
        F.KIND_CONSTRAINT: 30, F.KIND_FACT: 20,
        F.KIND_PREFERENCE: 20, F.KIND_RELATION_BASE: 20,
    }


def test_写入超上限时自动收敛到上限(cf_db, monkeypatch):
    """没超限一条都不动；刚过上限只挤尾部一条（留痕），不是整层清掉。"""
    _gate(monkeypatch, False)
    cap = F.CURATED_ACTIVE_CAP[F.KIND_FACT]
    for i in range(cap):
        _curate(cf_db, kind=F.KIND_FACT, value=f"事实条目{i}")
    act, sup, _ = _counts(cf_db, F.KIND_FACT)
    assert (act, sup) == (cap, 0), "没超限就被收敛＝把还没堆多的内容清掉了"

    _curate(cf_db, kind=F.KIND_FACT, value="挤爆上限的那条")
    act, sup, active = _counts(cf_db, F.KIND_FACT)
    assert act == cap, f"写入后仍超上限＝容量档没接进 assert_curated（active={act}）"
    assert sup == 1, "刚过上限只该挤一条"
    assert "挤爆上限的那条" in active, "被淘汰的不该是刚写下的这条"
    assert "事实条目0" not in active, "该挤的是最旧的低优先存量"


def test_淘汰是留痕不是删除(cf_db, monkeypatch):
    _gate(monkeypatch, False)
    cap = F.CURATED_ACTIVE_CAP[F.KIND_FACT]
    for i in range(cap + 2):
        _curate(cf_db, kind=F.KIND_FACT, value=f"留痕检查{i}")

    async def _check():
        async with cf_db() as db:
            rows = (await db.execute(select(WorldFact).where(
                WorldFact.character_id == CHAR, WorldFact.kind == F.KIND_FACT,
                WorldFact.status == "superseded"))).scalars().all()
            return rows
    rows = asyncio.run(_check())
    assert len(rows) == 2
    for r in rows:
        assert r.object_value.startswith("留痕检查")     # 正文一字未改
        assert r.superseded_at is not None
        assert r.superseded_by is None, "容量淘汰没有唯一替代行，不许硬指一个"


async def _total(factory):
    async with factory() as db:
        return (await db.execute(select(func.count()).select_from(WorldFact).where(
            WorldFact.character_id == CHAR))).scalar()


def test_总行数不变只改状态(cf_db, monkeypatch):
    _gate(monkeypatch, False)
    cap = F.CURATED_ACTIVE_CAP[F.KIND_FACT]
    for i in range(cap + 3):
        _curate(cf_db, kind=F.KIND_FACT, value=f"总量检查{i}")
    assert asyncio.run(_total(cf_db)) == cap + 3
    act, sup, _ = _counts(cf_db, F.KIND_FACT)
    assert act == cap and sup == 3


# ─────────────────────── 保留优先级（谁先被挤掉） ───────────────────────

def test_用户权威行不被挤掉(cf_db, monkeypatch):
    _gate(monkeypatch, False)
    cap = F.CURATED_ACTIVE_CAP[F.KIND_FACT]
    _seed(cf_db, kind=F.KIND_FACT, value="用户亲手写的设定不许被淘汰",
          verify_state=F.VERIFY_UNVERIFIED, authoritative=True, author="user")
    for i in range(cap + 1):
        _curate(cf_db, kind=F.KIND_FACT, value=f"堆积条目{i}")
    _, _, active = _counts(cf_db, F.KIND_FACT)
    assert "用户亲手写的设定不许被淘汰" in active
    assert len(active) == cap


def test_核实程度按rank不按字符串序(cf_db, monkeypatch):
    """这条专门钉 rank 映射：若按 `verify_state` 字符串序排，'unverified' > 'machine-confirmed'
    ⇒ 已核实的反而先被挤掉，方向正好反。两条种子刻意同置信度、同断言时刻，只剩 rank 能区分。"""
    _gate(monkeypatch, False)
    cap = F.CURATED_ACTIVE_CAP[F.KIND_FACT]
    same_ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    for i in range(cap - 1):
        _seed(cf_db, kind=F.KIND_FACT, value=f"垫场条目{i}", asserted_at=same_ts)
    _seed(cf_db, kind=F.KIND_FACT, value="已核实的那条", verify_state=F.VERIFY_MACHINE,
          asserted_at=same_ts)
    _seed(cf_db, kind=F.KIND_FACT, value="待确认的那条", verify_state=F.VERIFY_UNVERIFIED,
          asserted_at=same_ts)
    before = asyncio.run(_total(cf_db))
    _curate(cf_db, kind=F.KIND_FACT, value="把上限挤爆的新条目")
    _, _, active = _counts(cf_db, F.KIND_FACT)
    assert "已核实的那条" in active, "machine-confirmed 先被挤掉＝按字符串序排了（rank 失效）"
    assert "待确认的那条" not in active
    assert "把上限挤爆的新条目" in active
    assert before == cap + 1


def test_同rank下新近优先(cf_db, monkeypatch):
    _gate(monkeypatch, False)
    cap = F.CURATED_ACTIVE_CAP[F.KIND_FACT]
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=60)
    for i in range(cap):
        _seed(cf_db, kind=F.KIND_FACT, value=f"旧条目{i}", verify_state=F.VERIFY_UNVERIFIED,
              asserted_at=old)
    _curate(cf_db, kind=F.KIND_FACT, value="刚刚写下的条目")
    _, sup, active = _counts(cf_db, F.KIND_FACT)
    assert "刚刚写下的条目" in active
    assert (len(active), sup) == (cap, 1)
    assert "旧条目0" not in active, "同 rank 下该淘汰最旧的那条"


def test_新行不被自己这次写入挤掉(cf_db, monkeypatch):
    """钉住这条口径（代价写在 facts.py 注释里）：存量全是 machine-confirmed 时，新写入的
    unverified 条目仍然留下，挤掉的是一条最旧存量。不保护的话「TA 记下的·待你确认」满员后
    永久冻结，而且写入方会拿到一条已经 superseded 的行（记了等于没记）。"""
    _gate(monkeypatch, False)
    cap = F.CURATED_ACTIVE_CAP[F.KIND_FACT]
    for i in range(cap):
        _seed(cf_db, kind=F.KIND_FACT, value=f"已核实存量{i}")
    assert _counts(cf_db, F.KIND_FACT)[1] == 0, "种子刚铺满就收敛＝上限判断写反"
    _curate(cf_db, kind=F.KIND_FACT, value="刚记下但还没确认的")
    _, sup, active = _counts(cf_db, F.KIND_FACT)
    assert "刚记下但还没确认的" in active
    assert (len(active), sup) == (cap, 1)
    assert sum(1 for v in active if v.startswith("已核实存量")) == cap - 1, "只挤一条，不许整层清掉"


# ─────────────────────────── fail-open ───────────────────────────

def test_容量档抛异常也不影响写入(cf_db, monkeypatch):
    """这一档只是卫生动作，坏了不能把编纂写入带崩（也不许把整轮抽取拖挂）。"""
    _gate(monkeypatch, False)

    async def boom(db, *, character_id, kind, protect=None):
        raise RuntimeError("容量档炸了")

    monkeypatch.setattr(F, "enforce_curated_cap", boom)
    rid = _curate(cf_db, kind=F.KIND_FACT, value="容量档坏了也要写得进去")
    assert rid is not None
    act, sup, _ = _counts(cf_db, F.KIND_FACT)
    assert (act, sup) == (1, 0)


def test_接入点唯一():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "events", "facts.py"),
               encoding="utf-8").read()
    assert src.count("await enforce_curated_cap(") == 1
    assert src.count("async def enforce_curated_cap") == 1
    # 接入点必须把本次新行交给它保护（去掉 protect= 就退回「新行被自己挤掉」）
    assert "protect=row" in src
