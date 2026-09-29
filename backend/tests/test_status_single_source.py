# -*- coding: utf-8 -*-
"""架构地图断点 #9（2026-09-29）：「状态更新双写」收敛到单一口径来源。

背景：application/chat_service.py::_save_status_update 把同一条「用户现状更新」落两个面 ——
Memory（save_memory source="status"）与 WorldFact（events/facts.fold_status_update）。
只读核实结论（写进两处代码注释，本文件负责把它钉住）：
  - WorldFact 侧：app/events/facts.py:29 STATUS_FRESH_HOURS = 12（小时）；
  - Memory 侧：memory/constants.py:9 S_BY_TYPE["insight"] = 7.0（天）、:3 DECAY_THRESHOLD_PCT = 20.0、
    :5 DECAY_COUNTDOWN_DAYS = 3 ⇒ ≈12.6 天（7·ln6）才进删除倒计时；
  ⇒ 两侧**数值不一致**，故本批走「不改数值、只统一口径来源」的路线：Memory 侧带上与 WorldFact
    同源的过期标记（valid_to = facts.status_valid_to(now)，derived_from=world_fact）。
    权威面：WorldFact 管「现状」、Memory 管「长期」。数值统一属后续批次，需用户拍板。

覆盖：数值现值钉住（证明本批未改）/ 两侧时长与时刻同源 / 源码棘轮（禁止硬编码漂移）/
真实双写后两侧过期判据同进同退 / 既有状态更新字段回归 / 打标失败与空文本的 fail-open。
"""
import asyncio
import ast
import inspect
import os
import re
import textwrap
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.application.chat_service as chat_svc
import app.events.facts as facts_mod
import app.memory.dedup as memdedup
import app.memory.service as memsvc
from app.events.facts import (
    STATUS_FRESH_HOURS,
    STATUS_MEMORY_DERIVED_FROM,
    fold_status_update,
    get_active_facts,
    status_valid_to,
)
from app.memory.constants import DECAY_COUNTDOWN_DAYS, DECAY_THRESHOLD_PCT, S_BY_TYPE
from app.models.character import AICharacter
from app.models.memory import Memory, WorldFact
from app.models.user import User
from tests._dbclone import clone_engine, make_session_factory


def _now():
    """与生产同口径的 naive UTC 当前时刻。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _noop(*_a, **_kw):
    return None


# ─────────────────────────── 临时库（两侧写入都走它）───────────────────────────

@pytest.fixture()
def status_db(monkeypatch, tmp_path):
    """会话模板库克隆出的私有临时库 + 种子父行；向量/去重/意义提取全 noop（同 test_plan_expiry 口径）。

    需要打桩的四处早绑定工厂：chat_service（角色表 + 打标）、memory.service（save_memory）、
    memory.dedup（尾部异步去重）、events.facts（WorldFact 写入与读取）。
    """
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)

    async def _seed_parents():
        async with factory() as db:
            db.add(User(id=3, username="u5_user", nickname="用户3"))
            await db.commit()
            db.add(AICharacter(id=13, user_id=3, name="角色13"))
            await db.commit()

    asyncio.run(_seed_parents())
    monkeypatch.setattr(chat_svc, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(memdedup, "async_session_factory", factory)
    monkeypatch.setattr(facts_mod, "async_session_factory", factory)

    async def _fake_embed(_text):
        return [0.1, 0.2]

    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "add_memory", _noop)
    monkeypatch.setattr(memdedup, "_schedule_dedup", _noop)
    import app.memory.meaning as meaning_mod
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)

    yield factory
    asyncio.run(engine.dispose())


def _run_status_update(text="两人在吃饭", char=13, user=3):
    asyncio.run(chat_svc._save_status_update(char, text, user))


def _rows(factory):
    """读回本次双写的两侧行：(AICharacter, Memory, WorldFact)。"""
    async def _main():
        async with factory() as db:
            char = (await db.execute(
                select(AICharacter).where(AICharacter.id == 13))).scalar_one()
            mems = (await db.execute(
                select(Memory).where(Memory.character_id == 13).order_by(Memory.id))).scalars().all()
            facts = (await db.execute(
                select(WorldFact).where(WorldFact.character_id == 13).order_by(WorldFact.id)
            )).scalars().all()
            return char, list(mems), list(facts)

    return asyncio.run(_main())


# ─────────────────────────── 1) 数值现值钉住（本批未改数值）───────────────────────────

def test_两侧数值现值未被本批改动():
    """钉住核实到的两侧现值：改了任何一侧数值都必须先拍板、并同步改本断言。"""
    assert STATUS_FRESH_HOURS == 12          # WorldFact 侧：12 小时
    assert S_BY_TYPE["insight"] == 7.0       # Memory 侧：初始强度 S = 7 天
    assert DECAY_THRESHOLD_PCT == 20.0       # Memory 侧：保留率阈值 20%
    assert DECAY_COUNTDOWN_DAYS == 3         # Memory 侧：低于阈值后的删除倒计时
    # 两侧口径本来就不同量级（12 小时 vs ≈12.6 天）——本批只统一「来源」，不统一「数值」
    assert timedelta(hours=STATUS_FRESH_HOURS) != timedelta(days=S_BY_TYPE["insight"])


def test_派生标记登记权威面():
    """Memory 侧条上写的来源面值 = world_fact（权威面：WorldFact 管现状、Memory 管长期）。"""
    assert STATUS_MEMORY_DERIVED_FROM == "world_fact"


# ─────────────────────────── 2) 两侧过期时长/时刻同源 ───────────────────────────

def test_status_valid_to_就是新鲜窗时长():
    """status_valid_to(now) == now + STATUS_FRESH_HOURS（唯一数学出口，别处不再算一遍）。"""
    t = datetime(2026, 9, 29, 6, 30, 0)
    assert status_valid_to(t) == t + timedelta(hours=STATUS_FRESH_HOURS)
    assert (status_valid_to(t) - t).total_seconds() == STATUS_FRESH_HOURS * 3600
    # 不传参=取当前时钟，窗口长度不变
    now = _now()
    assert timedelta(hours=STATUS_FRESH_HOURS - 1) < status_valid_to() - now < timedelta(
        hours=STATUS_FRESH_HOURS + 1)


def test_fold_与_标记_时长逐字相等(monkeypatch):
    """WorldFact 的 ttl_minutes 与 Memory 标记的时长必须换算相等（同源、非各写一份）。"""
    captured = {}

    async def _fake_assert(**kw):
        captured.update(kw)

    monkeypatch.setattr(facts_mod, "assert_fact", _fake_assert)
    t = datetime(2026, 9, 29, 8, 0, 0)
    asyncio.run(fold_status_update(13, 3, "两人在吃饭", now=t))
    assert captured.get("predicate") == "status"
    assert captured.get("now") == t                      # 基准时刻由调用方给定（同一瞬间）
    assert captured.get("ttl_minutes") == STATUS_FRESH_HOURS * 60
    assert status_valid_to(t) - t == timedelta(minutes=captured["ttl_minutes"])


def test_fold_默认now_不改变旧行为(monkeypatch):
    """不传 now（历史调用方）⇒ 透传 None，assert_fact 自己取时钟＝逐字节旧行为。"""
    captured = {}

    async def _fake_assert(**kw):
        captured.update(kw)

    monkeypatch.setattr(facts_mod, "assert_fact", _fake_assert)
    asyncio.run(fold_status_update(13, 3, "两人在吃饭"))
    assert captured.get("now") is None
    assert captured.get("ttl_minutes") == STATUS_FRESH_HOURS * 60


# ─────────────────────────── 3) 源码棘轮：单侧改动不得静默漂移 ───────────────────────────

def _code_only(fn) -> str:
    """函数「纯代码」视图：剥掉 docstring 与行内 # 注释（棘轮只看代码，不被注释文本误伤）。"""
    src = textwrap.dedent(inspect.getsource(fn))
    node = ast.parse(src).body[0]
    first = node.body[0]
    skip = 0
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        skip = first.end_lineno  # 首句是 docstring ⇒ 连它一起剥掉
    out = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i <= skip:
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


def test_棘轮_memory侧不得硬编码小时():
    """写入侧只能引用 status_valid_to，禁止出现自己的小时数/新鲜窗字面量（防单侧漂移）。"""
    src = _code_only(chat_svc._save_status_update)
    assert "status_valid_to(" in src
    assert "STATUS_FRESH_HOURS" not in src           # 不得绕过 helper 直接取窗口常量
    assert not re.search(r"timedelta\(hours=\d+", src)
    assert not re.search(r"\b\d+\s*\*\s*60\b", src)  # 不得再手写 12*60 这类 TTL 分数


def test_棘轮_facts侧两处过期都出自同一常量():
    """TTL（事实行）与 status_valid_to（派生标记）都必须写 STATUS_FRESH_HOURS。"""
    assert "STATUS_FRESH_HOURS" in _code_only(facts_mod.fold_status_update)
    assert "timedelta(hours=STATUS_FRESH_HOURS)" in inspect.getsource(status_valid_to)
    # _TRANSIENT_FRESH_HOURS 的 status 档也必须引用同一常量（读取新鲜窗同源）
    assert facts_mod._TRANSIENT_FRESH_HOURS["status"] == STATUS_FRESH_HOURS


# ─────────────────────────── 4) 真实双写后两侧判据一致 ───────────────────────────

def test_双写后两侧过期时刻同一瞬间(status_db):
    """同一次状态更新：Memory.valid_to 与 WorldFact.expires_at 完全相等（同一 now + 同一窗口）。"""
    _run_status_update()
    _char, mems, facts = _rows(status_db)
    assert len(mems) == 1 and len(facts) == 1
    assert mems[0].valid_to is not None
    assert mems[0].valid_to == facts[0].expires_at
    # 事实行的写入基准（asserted_at 由库侧 CURRENT_TIMESTAMP 记，秒级）＋窗口 == 过期时刻
    assert abs((mems[0].valid_to - facts[0].asserted_at) - timedelta(hours=STATUS_FRESH_HOURS)) \
        < timedelta(seconds=1)


def test_新鲜窗内两侧都判有效(status_db):
    """刚写完：事实仍被注入（get_active_facts）且记忆标记未到期——两侧同时「有」。"""
    _run_status_update()
    _char, mems, facts = _rows(status_db)
    now = _now()
    assert len(mems) == 1 and mems[0].valid_to > now
    visible = asyncio.run(get_active_facts(character_id=13, user_id=3,
                                           viewer_type="character", viewer_id=13))
    assert [f.id for f in visible] == [facts[0].id]


def test_超窗后两侧都判过期(status_db):
    """回拨 13 小时：事实不再注入、记忆派生标记同步到期——两侧判据同进同退。"""
    _run_status_update()
    _char, mems, facts = _rows(status_db)

    async def _backdate():
        async with status_db() as db:
            f = (await db.execute(select(WorldFact).where(WorldFact.id == facts[0].id))).scalar_one()
            f.asserted_at -= timedelta(hours=13)
            f.expires_at -= timedelta(hours=13)
            m = (await db.execute(select(Memory).where(Memory.id == mems[0].id))).scalar_one()
            m.valid_to -= timedelta(hours=13)
            await db.commit()

    asyncio.run(_backdate())
    now = _now()
    _c, mems, facts = _rows(status_db)          # 回拨后重新读（ORM 旧对象不反映库内改动）
    assert now > mems[0].valid_to                    # 记忆侧：派生标记已到期
    assert facts[0].expires_at <= now                # 事实侧：同一时刻已过期
    visible = asyncio.run(get_active_facts(character_id=13, user_id=3,
                                           viewer_type="character", viewer_id=13))
    assert visible == []


def test_边界一小时后仍未过期两侧同判有效(status_db):
    """回拨 11 小时（窗内）：事实仍注入、标记未到期——防两侧窗口长度被单边改掉。"""
    _run_status_update()
    _char, mems, facts = _rows(status_db)

    async def _backdate():
        async with status_db() as db:
            f = (await db.execute(select(WorldFact).where(WorldFact.id == facts[0].id))).scalar_one()
            f.asserted_at -= timedelta(hours=11)
            f.expires_at -= timedelta(hours=11)
            m = (await db.execute(select(Memory).where(Memory.id == mems[0].id))).scalar_one()
            m.valid_to -= timedelta(hours=11)
            await db.commit()

    asyncio.run(_backdate())
    assert _rows(status_db)[1][0].valid_to > _now()
    visible = asyncio.run(get_active_facts(character_id=13, user_id=3,
                                           viewer_type="character", viewer_id=13))
    assert len(visible) == 1


# ─────────────────────────── 5) 既有状态更新回归（零行为）───────────────────────────

def test_回归_两侧字段与衰减值不变(status_db):
    """除新增 valid_to 标记外，既有写入字段/强度全部保持原样（数值未被改动）。"""
    _run_status_update("两人在吃晚饭")
    char, mems, facts = _rows(status_db)
    assert char.current_status == "两人在吃晚饭"
    m = mems[0]
    assert (m.memory_type, m.sub_type, m.source) == ("insight", "status", "status")
    assert m.content == "状态更新: 两人在吃晚饭"
    assert m.epistemic_status == "FACT"
    assert m.speaker_type == "character" and m.speaker_id == 13
    assert m.strength_days == S_BY_TYPE["insight"]      # 衰减口径未动
    assert m.next_review_at > m.created_at              # 复习窗口仍按 S 排期
    assert m.is_archived is False and m.status == "active"
    f = facts[0]
    assert (f.predicate, f.source, f.kind, f.status) == ("status", "chat_status", "status", "active")
    assert f.object_value == "两人在吃晚饭"
    assert abs(float(f.confidence) - 0.9) < 1e-9


def test_空文本两面都不写(status_db):
    """空状态文本：不改角色状态、不写记忆、不写事实（旧行为，本批未动判据）。"""
    before = _rows(status_db)[0].current_status
    _run_status_update("")
    char, mems, facts = _rows(status_db)
    assert mems == [] and facts == []
    assert char.current_status == before   # 角色表状态未被空文本覆盖


def test_纯空白文本只按旧行为处理(status_db):
    """空白串不是「假空」：与改动前一致——事实侧 strip 后不写，记忆侧照旧落一条并带上同源标记。"""
    _run_status_update("   ")
    _char, mems, facts = _rows(status_db)
    assert facts == []                       # fold_status_update 内部 strip ⇒ 无事实
    assert len(mems) == 1                    # 旧行为：记忆照旧写
    assert mems[0].valid_to is not None      # 且带上同源过期标记（不改变「是否写入」的判定）
    assert abs((mems[0].valid_to - _now()) - timedelta(hours=STATUS_FRESH_HOURS)) < timedelta(minutes=1)


def test_记忆写失败_事实仍落库且不炸(status_db, monkeypatch):
    """save_memory 抛错时：状态折叠照旧发生（两侧彼此独立，失败静默＝旧行为）。"""
    async def _boom(**_kw):
        raise RuntimeError("memory down")

    monkeypatch.setattr("app.memory.save_memory", _boom)
    _run_status_update("两人在看电影")
    _char, mems, facts = _rows(status_db)
    assert mems == []
    assert len(facts) == 1 and facts[0].expires_at > _now()


def test_打标异常不影响双写(status_db, monkeypatch):
    """helper 取不到（打标记失败）⇒ 记忆与事实照旧落库，valid_to 留空＝回退到改动前形态。"""
    def _boom(*_a, **_kw):
        raise RuntimeError("constants missing")

    monkeypatch.setattr(facts_mod, "status_valid_to", _boom)
    _run_status_update("两人在散步")
    _char, mems, facts = _rows(status_db)
    assert len(mems) == 1 and mems[0].valid_to is None
    assert len(facts) == 1 and facts[0].expires_at > _now()


def test_已有valid_to不被覆盖(status_db, monkeypatch):
    """并入既有行（已带 valid_to）时打标不覆盖——保守：谁先有的口径谁说了算。"""
    sentinel = _now() + timedelta(days=1)

    async def _seed_mem():
        async with status_db() as db:
            m = Memory(user_id=3, character_id=13, memory_type="insight",
                       content="状态更新: 既有行", sub_type="status", source="status",
                       valid_to=sentinel)
            db.add(m)
            await db.commit()
            return m.id

    mem_id = asyncio.run(_seed_mem())

    class _Stub:
        id = mem_id

    async def _fake_save(**_kw):
        return _Stub()

    monkeypatch.setattr("app.memory.save_memory", _fake_save)
    _run_status_update("两人在吃饭")
    _char, mems, _facts = _rows(status_db)
    assert len(mems) == 1
    assert mems[0].valid_to == sentinel
