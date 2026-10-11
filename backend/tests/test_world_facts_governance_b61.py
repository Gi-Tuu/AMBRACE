# -*- coding: utf-8 -*-
"""批 61（2026-10-11）：世界设定（world_facts curated 层）治理 P0 的守卫。

起因（体验级事故）：AI 每轮把「长期知识」写进 world_facts 的 curated 层，而这些行**无条件**拿到
`is_authoritative=1 + machine-confirmed + confidence=1.0`，于是一句修辞里的喻体被字面化成健康事实后，
既当「不可动摇的权威设定」排最前，又**每轮确定性注入**、不衰减、不去重，越滚越多。

本文件钉四刀（A1 权威归位／A2 高风险域待确认／A3 抽取提示词护栏的源码钉／A4 注入拦截）＋一条代价：
- A1：`assert_curated` 写出来的行永远不是权威；默认档＝未核实、非满分。
- A1b：抽取调用处**显式**传 unverified（`merge_curated_evidence` 的 verify 只升不降，
  只改默认值会被第二次抽取自动转正）。
- A2：高风险域（健康/医疗/用药/财务/法律/安全）即便上层标 machine 也降为待确认＋7 天短复核窗。
- A4：注入侧 ①未核实不进每轮前提 ②高风险域必须 human-reviewed 才进（这一条对**存量脏数据**直接生效）。
- 代价：关键词判定宁可多判——命中宽泛子串（如「痛快」含「痛」）的正常内容也只会**降级待确认**，
  不会被删除、不会被改文，用户采纳即恢复。

纪律：临时库走 `tmp_path`（`_dbclone` 克隆模板库）；不碰生产库；纯确定性、零 LLM、零计费。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.events.facts import (
    KIND_CONSTRAINT,
    KIND_FACT,
    VERIFY_HUMAN,
    VERIFY_MACHINE,
    VERIFY_UNVERIFIED,
    _SENSITIVE_DOMAIN_KEYWORDS,
    _SENSITIVE_REVIEW_DAYS,
    _touches_sensitive_domain,
    assert_curated,
    assert_fact,
    get_curated_facts,
)
from app.models.memory import WorldFact

pytestmark = pytest.mark.slow

CHAR = 11
USER = 1

# 有「字面明证形状」的健康事实（用于证明治理只拦推断，用户确认后仍可用）
HEALTHY = "用户腰不好，医生让他贴膏药"
PLAIN = "用户喜欢在工作时听轻音乐"


@pytest.fixture()
def cf_db(monkeypatch, tmp_path):
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)
    import app.db.database as db_mod
    import app.events.facts as facts
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(facts, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _curate(factory, *, kind=KIND_FACT, value, **kw):
    async def _run():
        async with factory() as db:
            row = await assert_curated(db, character_id=CHAR, user_id=USER, kind=kind,
                                       object_value=value, **kw)
            await db.commit()
            if row is not None:
                await db.refresh(row)
            return row
    return asyncio.run(_run())


def _seed(factory, *, kind=KIND_FACT, value, verify_state, authoritative=False):
    """直写库（绕过写入侧），用于单独测**注入侧**过滤。"""
    async def _run():
        async with factory() as db:
            db.add(WorldFact(
                user_id=USER, character_id=CHAR, subject_type="character", subject_id=CHAR,
                predicate="curated", object_value=value, status="active", confidence=0.6,
                epistemic_status="FACT", audience='["public"]', author="system",
                is_authoritative=authoritative, source="extractor", kind=kind,
                verify_state=verify_state, sources_json="[]", links_json="[]",
                asserted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            await db.commit()
    asyncio.run(_run())


def _injected(factory, kind=KIND_FACT):
    grouped = asyncio.run(get_curated_facts(character_id=CHAR, user_id=USER))
    return [r.object_value for r in grouped.get(kind, [])]


# ───────────────────────── A1：权威归位 ─────────────────────────

def test_A1_编纂行不是权威设定(cf_db):
    """curated 写入行的 is_authoritative 恒为 False——「不可动摇的设定」只有用户亲手写才有。
    且**与准入闸门无关**（事故里的 84 条正是闸门开着时产生的）。"""
    prev = _loop.AGENT_FLAGS.get("memory_admission_gate", False)
    try:
        for gate, val in ((False, "闸门关时的编纂行"), (True, "闸门开时的编纂行")):
            _loop.AGENT_FLAGS["memory_admission_gate"] = gate
            row = _curate(cf_db, value=val, verify_state=VERIFY_MACHINE)
            assert row is not None
            assert row.is_authoritative is False
    finally:
        _loop.AGENT_FLAGS["memory_admission_gate"] = prev


def test_A1_默认档收紧为未核实非满分(cf_db):
    """不传 verify_state 时＝unverified + UNVERIFIED + 0.5（旧默认是 machine + FACT + 1.0）。"""
    row = _curate(cf_db, value="用户习惯在周末整理相册")
    assert row.verify_state == VERIFY_UNVERIFIED
    assert row.epistemic_status == "UNVERIFIED"
    assert float(row.confidence) == pytest.approx(0.5)
    assert row.is_authoritative is False


def test_A1_用户手写权威路径没被动(cf_db):
    """反向钉：A1 只收 curated 写入腿，assert_fact(author='user', is_authoritative=True) 照旧。"""
    fid = asyncio.run(assert_fact(
        subject_type="character", subject_id=CHAR, predicate="setting_home",
        object_value="用户住在城西", user_id=USER, character_id=CHAR,
        audience=["public"], author="user", is_authoritative=True, kind=KIND_FACT))
    assert fid is not None

    async def _check():
        async with cf_db() as db:
            r = await db.get(WorldFact, fid)
            return r.author, r.is_authoritative
    author, auth = asyncio.run(_check())
    assert author == "user" and auth is True


# ───────────────────── A1b：抽取腿不会被二次证据转正 ─────────────────────

def test_A1b_同一错误抽两次仍是待确认(cf_db):
    """merge_curated_evidence 的 verify 只升不降 ⇒ 抽取必须**恒传** unverified；
    否则同一条错误被 LLM 第二次抽到时（旧默认 machine）会被自动转正、绕过待确认。"""
    r1 = _curate(cf_db, value="用户习惯把外套搭在椅背上", verify_state=VERIFY_UNVERIFIED)
    r2 = _curate(cf_db, value="用户习惯把外套搭在椅背上", verify_state=VERIFY_UNVERIFIED)
    assert r1.id == r2.id
    assert r2.verify_state == VERIFY_UNVERIFIED


def test_A1b_抽取调用处显式钉住未核实():
    """源码守卫（写入腿的行为要看得见）：抽取便车必须显式传 verify/epistemic/置信上限，
    不许只靠签名默认值——默认值哪天被改回 machine，这一格就悄悄破了。"""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "memory",
                            "extractor.py"), encoding="utf-8").read()
    assert "verify_state=VERIFY_UNVERIFIED" in src
    assert "epistemic=EPISTEMIC_UNVERIFIED" in src
    assert "confidence=max(0.3, min(0.6" in src


def test_A3_提示词三道护栏在位():
    """A3 是提示词文本（无确定性行为可测），所以钉**三道护栏各自的关键词**——
    少任何一道（喻体不是事实／高风险域要字面明证／每条要有字面依据）都算红。"""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "memory",
                            "extractor.py"), encoding="utf-8").read()
    for marker in ("喻体/字眼不是事实", "字面、明确、就事论事", "找到**字面依据**"):
        assert marker in src, marker


# ───────────────────────── A2：高风险域 ─────────────────────────

def test_A2_纯函数命中与放行():
    assert _touches_sensitive_domain(HEALTHY) is True
    assert _touches_sensitive_domain("用户设置了转账密码") is True
    assert _touches_sensitive_domain(PLAIN) is False
    assert _touches_sensitive_domain("") is False
    assert _touches_sensitive_domain(None) is False       # fail-safe：坏输入不炸
    for k in ("膏药", "腰", "密码", "身份证"):
        assert k in _SENSITIVE_DOMAIN_KEYWORDS


def test_A2_高风险域即便machine也降级并给短复核窗(cf_db):
    """上层（或历史默认）给了 machine，落到高风险域仍然只是待确认，且带 7 天复核窗（不永不过期）。"""
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    row = _curate(cf_db, value=HEALTHY, verify_state=VERIFY_MACHINE)
    assert row.verify_state == VERIFY_UNVERIFIED
    assert row.epistemic_status == "UNVERIFIED"
    assert row.stale_after is not None
    span = row.stale_after - before
    assert timedelta(days=_SENSITIVE_REVIEW_DAYS - 1) < span <= timedelta(days=_SENSITIVE_REVIEW_DAYS + 1)


def test_A2_普通内容不受窗口影响(cf_db):
    row = _curate(cf_db, value=PLAIN, verify_state=VERIFY_MACHINE)
    assert row.verify_state == VERIFY_MACHINE
    assert row.stale_after is None


# ───────────────────────── A4：注入拦截 ─────────────────────────

def test_A4_未核实的编纂草稿不进每轮注入(cf_db):
    _seed(cf_db, value=PLAIN, verify_state=VERIFY_UNVERIFIED)
    _seed(cf_db, value="用户习惯用冷水漱口", verify_state=VERIFY_MACHINE)
    got = _injected(cf_db)
    assert PLAIN not in got
    assert "用户习惯用冷水漱口" in got


def test_A4_高风险域必须人工确认才注入(cf_db):
    """这一条对**存量脏数据**直接生效：库里已存在的 machine-confirmed 健康类推断不再当每轮前提；
    用户采纳（human-reviewed）后恢复。"""
    _seed(cf_db, value=HEALTHY, verify_state=VERIFY_MACHINE, authoritative=True)
    assert HEALTHY not in _injected(cf_db)          # 连「权威」标记也拦不住这一刀

    async def _confirm():
        async with cf_db() as db:
            rows = (await db.execute(
                select(WorldFact).where(WorldFact.character_id == CHAR)
            )).scalars().all()
            rows[0].verify_state = VERIFY_HUMAN
            await db.commit()
    asyncio.run(_confirm())
    assert HEALTHY in _injected(cf_db)


def test_A4_拦截不影响非高风险域的配额(cf_db):
    """反向钉：A4 只拦「未核实」与「高风险域未人工确认」两类，别把整层注入清空。"""
    for i in range(6):
        _seed(cf_db, kind=KIND_CONSTRAINT, value=f"铁律{i}：不许在没有告知时改动既定安排",
              verify_state=VERIFY_MACHINE)
    assert len(_injected(cf_db, KIND_CONSTRAINT)) == 6


def test_代价_命中宽泛子串只降级不删除(cf_db):
    """如实记代价：关键词判定会误伤（「痛快」含「痛」）。误伤后果＝待确认＝不注入，
    但行还在、正文没改，用户采纳即恢复——不许顺手删内容。"""
    benign = "用户看比赛看得很痛快"
    assert _touches_sensitive_domain(benign) is True          # 已知误伤
    row = _curate(cf_db, value=benign, verify_state=VERIFY_MACHINE)
    assert row.verify_state == VERIFY_UNVERIFIED
    assert row.status == "active"
    assert row.object_value == benign                          # 正文一字未动
    assert benign not in _injected(cf_db)
