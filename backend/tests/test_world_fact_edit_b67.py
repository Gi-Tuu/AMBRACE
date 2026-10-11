# -*- coding: utf-8 -*-
"""批 67（A55，用户拍板方案①）：世界设定页「改一下保存」不许把条目挪出自己的层。

治的是实测过的两个后果（不是假想）：
① `update_world_fact` 旧写法 `predicate = data.predicate or "setting"` 会把 `predicate='curated'`
   的行改成 `setting`。此后 `assert_curated` 的「同键 active 行」查找（按 predicate 过滤）再也命中不到它
   ⇒ 同样内容再抽取一次就**新建重复行**，而不是更新这条已经人工确认过的。
   线上实证＝#133（用户 10-11 11:38 编辑过，已变 setting）与 #21「用户养有宠物团子和仓鼠芒芒」现在并存。
② 同函数那条「同 subject+predicate 只留一个现值」的 blanket supersede 会跟着改写后的谓词跑，
   把**别的活跃条目**一起顶掉。线上实证＝编辑 #133 那次顶掉了无关的 #73。

关键判据（每条都有对应用例）：
- curated 行编辑后 `predicate` 与 `kind` 都不动，只升「用户权威＋人工复核」；
- 调用方显式传 `predicate='setting'` 也不许降级（层归属由服务端定，不靠客户端自觉）；
- 编辑一条 curated 行不许清掉同层其他条目；
- 用户自己的 setting/preference 行**逐字照旧**（反向钉，防止我越界改用户层语义）；
- 采纳后 `stale_after` 清空（否则刚确认的条目几天后被复核窗自动停用＝「我确认了它却又不见了」）；
- 采纳过的同值内容再抽取＝更新原行，不新建重复。

纪律：临时库走 `tmp_path`＋`_dbclone`；不碰生产库；零 LLM、零计费。
"""
import asyncio
import os

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.api import characters as characters_api
from app.application import characters as characters_svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.events import facts as F
from app.models.character import AICharacter
from app.models.memory import WorldFact
from app.models.user import User

pytestmark = pytest.mark.slow

CHAR = 7
USER = 1
GATE = "memory_admission_gate"


@pytest.fixture()
def wf_db(monkeypatch, tmp_path):
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)

    async def _init():
        async with factory() as db:
            db.add(User(id=USER, username="a55_u", nickname="本人"))
            db.add(AICharacter(id=CHAR, user_id=USER, name="测试角色"))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(characters_svc, "async_session_factory", factory)
    monkeypatch.setattr(F, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _client(factory) -> TestClient:
    app = FastAPI()
    app.include_router(characters_api.router)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: USER
    return TestClient(app)


def _seed(factory, **over) -> int:
    kw = dict(
        user_id=USER, character_id=CHAR, subject_type="character", subject_id=CHAR,
        predicate=F.CURATED_PREDICATE, kind=F.KIND_FACT, object_value="用户养了仓鼠叫芒芒",
        status="active", confidence=0.5, epistemic_status=F.EPISTEMIC_UNVERIFIED,
        audience='["user:1", "char:7"]', author="system", is_authoritative=False,
        source="extractor", verify_state=F.VERIFY_MACHINE,
    )
    kw.update(over)
    async def _run():
        async with factory() as db:
            r = WorldFact(**kw)
            db.add(r)
            await db.commit()
            await db.refresh(r)
            return r.id
    return asyncio.run(_run())


def _row(factory, fid) -> WorldFact:
    async def _run():
        async with factory() as db:
            return await db.get(WorldFact, fid)
    return asyncio.run(_run())


def _total(factory):
    async def _run():
        async with factory() as db:
            return (await db.execute(select(func.count()).select_from(WorldFact))).scalar()
    return asyncio.run(_run())


def _active_ids(factory, predicate):
    async def _run():
        async with factory() as db:
            rows = (await db.execute(select(WorldFact.id).where(
                WorldFact.predicate == predicate, WorldFact.status == "active"))).scalars().all()
            return set(rows)
    return asyncio.run(_run())


def _put(factory, fid, body):
    return _client(factory).put(f"/api/v1/characters/{CHAR}/world-facts/{fid}", json=body)


# ─────────────── 层归属：编辑不许把条目挪出 curated ───────────────

def test_编辑curated行不改层归属(wf_db):
    """内容改了，但 predicate 与 kind 一个都不动；只升「用户权威＋人工复核」。"""
    fid = _seed(wf_db, kind=F.KIND_CONSTRAINT, object_value="用户腰不好，需避免久站")
    r = _put(wf_db, fid, {"content": "用户腰有旧伤，避免久站"})
    assert r.status_code == 200, r.text
    f = _row(wf_db, fid)
    assert f.predicate == F.CURATED_PREDICATE, "编辑把 curated 改成 setting＝条目被挪出编纂层"
    assert f.kind == F.KIND_CONSTRAINT
    assert f.object_value == "用户腰有旧伤，避免久站"
    assert (f.author, f.is_authoritative, f.verify_state) == ("user", True, F.VERIFY_HUMAN)
    assert f.epistemic_status == "FACT" and f.status == "active"


def test_客户端传setting也不许把curated行降级(wf_db):
    """层归属由服务端定：不能因为调用方递了个 predicate 就把已治理层的条目搬走。"""
    fid = _seed(wf_db)
    r = _put(wf_db, fid, {"content": "用户养了仓鼠叫芒芒，sam养了猫叫团子",
                          "predicate": "setting"})
    assert r.status_code == 200, r.text
    assert _row(wf_db, fid).predicate == F.CURATED_PREDICATE


def test_编辑curated行不清掉同层其他条目(wf_db):
    """同角色同谓词在 curated 层天然几十条并存；blanket supersede 跟着跑＝编辑一条清掉整层。"""
    keep = _seed(wf_db, object_value="要改的那条")
    others = [_seed(wf_db, object_value=f"同层第{i}条") for i in range(3)]
    r = _put(wf_db, keep, {"content": "改过的内容"})
    assert r.status_code == 200, r.text
    assert _active_ids(wf_db, F.CURATED_PREDICATE) >= set(others) | {keep}
    for oid in others:
        assert _row(wf_db, oid).status == "active", "同层其他条目被这次编辑顶掉了"
        assert _row(wf_db, oid).superseded_by is None


def test_采纳过的内容再抽取走更新不新建重复(wf_db, monkeypatch):
    """这条钉的是实测到的真伤害：改完 predicate 之后，assert_curated 的「同键 active 行」查找
    （按 predicate 过滤）命中不到这条，同样内容再抽取一次就长出一条重复行。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, GATE, False)
    fid = _seed(wf_db, object_value="用户养了仓鼠叫芒芒")
    assert _put(wf_db, fid, {"content": "用户养了仓鼠叫芒芒"}).status_code == 200
    before = _total(wf_db)

    async def _re_extract():
        async with wf_db() as db:
            row = await F.assert_curated(db, character_id=CHAR, user_id=USER,
                                         kind=F.KIND_FACT, object_value="用户养了仓鼠叫芒芒",
                                         source="extractor")
            await db.commit()
            return row.id
    assert asyncio.run(_re_extract()) == fid, "同值再抽取没命中原行＝它已经不在编纂层了"
    assert _total(wf_db) == before, "多出来的那一条＝重复长回来"


# ─────────────── 复核窗：人工确认过＝不再自动到期 ───────────────

def test_采纳后复核窗不再自动到期(wf_db):
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(days=7)).replace(tzinfo=None)
    fid = _seed(wf_db, kind=F.KIND_CONSTRAINT, object_value="用户腰部有旧疾", stale_after=future)
    assert _put(wf_db, fid, {"content": "用户腰有旧疾，已知"}).status_code == 200
    assert _row(wf_db, fid).stale_after is None, "确认过的条目还挂着复核窗＝几天后被自动停用"

    other = _seed(wf_db, object_value="没被动过的条目", stale_after=future)
    assert _row(wf_db, other).stale_after is not None, "不该顺手清掉别人家的复核窗"


# ─────────────── 反向钉：用户层语义一行没改 ───────────────

def test_setting行编辑行为逐字照旧(wf_db):
    """「同 subject+predicate 只留一个现值」是老规则，只对瞬时/单值类成立——本批不许越界改它。"""
    keep = _seed(wf_db, predicate="setting", kind=None, object_value="我的设定A")
    same = _seed(wf_db, predicate="setting", kind=None, object_value="我的设定B")
    other_pred = _seed(wf_db, predicate="preference", kind=None, object_value="长期偏好C")
    assert _put(wf_db, keep, {"content": "我的设定A改过"}).status_code == 200
    assert _row(wf_db, keep).predicate == "setting"
    assert _row(wf_db, same).status == "superseded"
    assert _row(wf_db, same).superseded_by == keep
    assert _row(wf_db, other_pred).status == "active"


def test_未传predicate时尊重行自己的谓词(wf_db):
    """旧写法会把任何被编辑的行统一改写成 setting；现在没传就保持原样。"""
    fid = _seed(wf_db, predicate="preference", kind=None, object_value="用户偏好安静")
    assert _put(wf_db, fid, {"content": "用户偏好安静环境"}).status_code == 200
    assert _row(wf_db, fid).predicate == "preference", "编辑把 preference 行改写成 setting＝又换了类"


def test_谓词常量单一真源():
    """`curated` 这个字面量在 facts.py 里只许出现在常量定义处（层归属判据不许散写第二份）。"""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "events", "facts.py"),
               encoding="utf-8").read()
    assert 'CURATED_PREDICATE = "curated"' in src
    assert src.count('predicate: str = CURATED_PREDICATE') == 1
    chars = open(os.path.join(os.path.dirname(__file__), "..", "app", "application",
                              "characters.py"), encoding="utf-8").read()
    # 编辑路径必须问「这一条本来是哪一层」；「无脑落到 setting」只许留在**创建**路径那一份
    assert chars.count('predicate = (data.predicate or "setting")') == 1
    assert chars.count('predicate = (data.predicate or f.predicate or "setting")') == 1
    assert chars.count("f.predicate == CURATED_PREDICATE") == 1
    # schema 不许再给 predicate 兜 setting 默认，否则服务端分不清「没填」与「就是要 setting」
    assert chars.count("predicate: str | None = None") == 1
