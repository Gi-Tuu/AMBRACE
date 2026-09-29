# -*- coding: utf-8 -*-
"""架构地图断点 #9 收口（2026-09-29 用户拍板）：两处「状态更新」过期口径统一到 12 小时。

背景（同一条现状更新双写两个面，改动前数值不一致）：
  - WorldFact 侧（现状权威面）：events/facts.py::STATUS_FRESH_HOURS = **12 小时**，
    写入 TTL（fold_status_update）与读取新鲜窗（get_active_facts）同源；
  - Memory 侧（长期面）：chat_service::_save_status_update 写 memory_type="insight"
    / sub_type="status" / source="status"，只按通用艾宾浩斯衰减
    （constants.S_BY_TYPE["insight"]=7.0 天 × DECAY_THRESHOLD_PCT=20 ⇒ 约 2.6 天才落下去）。
  - 上一批只统一了「口径来源」（Memory 行带上同源 valid_to 标记），**没有任何读侧消费者**
    （lifecycle_policy.is_expired 只在维护干跑统计里被调用）⇒ 12h 并未真的生效。

本批（唯一的行为变更单）：读侧在召回出口 memory/retrieve.py::_rerank 的 DB 回填池剔除超窗的
「状态派生记忆条」，数值单一来源仍是 facts.STATUS_FRESH_HOURS（判据函数 status_memory_expired）。
身份限定 sub_type='status' **且** source='status'（生产库实测：sub_type='status' 共 873 行，
其中 778 行来自抽取 source='chat' ⇒ 只看 sub_type 会误伤抽取条）。
回退：flag `status_memory_ttl` 置 False＝逐字节旧行为。

覆盖：11h59m/12h01m 边界两侧 / 过期后不再出现在召回结果 / 其它记忆类型逐例不受影响 /
抽取条不误伤 / 常量单源（源码棘轮）/ 回退开关关＝旧行为 / 判定与取闸异常不阻塞主链路 /
真实双写后两侧同进同退 / 通用衰减档未改动 / flag 双向登记。
"""
import asyncio
import ast
import inspect
import os
import re
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

import app.application.chat_service as chat_svc
import app.events.facts as facts_mod
import app.memory.dedup as memdedup
import app.memory.retrieve as retrieve_mod
import app.memory.service as memsvc
from app.events.facts import (
    STATUS_FRESH_HOURS,
    STATUS_MEMORY_SOURCE,
    STATUS_MEMORY_SUB_TYPE,
    get_active_facts,
    is_status_derived_memory,
    status_memory_expired,
    status_memory_expiry,
    status_valid_to,
)
from app.flags.agent_flags import AGENT_FLAGS
from app.memory.constants import DECAY_COUNTDOWN_DAYS, DECAY_THRESHOLD_PCT, S_BY_TYPE
from app.memory.retrieve import (
    STATUS_MEMORY_TTL_FLAG,
    _drop_expired_status_memories,
    _rerank,
    _status_ttl_on,
)
from app.models.character import AICharacter
from app.models.memory import Memory, WorldFact
from app.models.user import User
from tests._dbclone import clone_engine, make_session_factory


def _now():
    """与生产同口径的 naive UTC 当前时刻。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _noop(*_a, **_kw):
    return None


# ───────────────────── 开关复位（新键默认开，用例内可临时拨关）─────────────────────

@pytest.fixture(autouse=True)
def _flag_default_on():
    """每个用例前把新键拨回登记值（True），用例后完整还原（含键是否存在）。"""
    had = STATUS_MEMORY_TTL_FLAG in AGENT_FLAGS
    saved = AGENT_FLAGS.get(STATUS_MEMORY_TTL_FLAG)
    AGENT_FLAGS[STATUS_MEMORY_TTL_FLAG] = True
    yield
    if had:
        AGENT_FLAGS[STATUS_MEMORY_TTL_FLAG] = saved
    else:  # pragma: no cover - 登记后即不成立
        AGENT_FLAGS.pop(STATUS_MEMORY_TTL_FLAG, None)


# ─────────────────────────── 临时库（真实行 + 真实 _rerank）───────────────────────────

@pytest.fixture()
def ttl_db(monkeypatch, tmp_path):
    """模板库克隆的私有临时库；只打桩写入侧的向量/去重/意义提取，_rerank 走真实 SQL。"""
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)

    async def _seed_parents():
        async with factory() as db:
            db.add(User(id=3, username="w1_user", nickname="用户3"))
            await db.commit()
            db.add(AICharacter(id=13, user_id=3, name="角色13"))
            await db.commit()

    asyncio.run(_seed_parents())
    monkeypatch.setattr(chat_svc, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(facts_mod, "async_session_factory", factory)
    monkeypatch.setattr(memdedup, "async_session_factory", factory)
    async def _fake_embed(_text):
        return [0.1, 0.2]

    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "add_memory", _noop)
    monkeypatch.setattr(memdedup, "_schedule_dedup", _noop)
    import app.memory.meaning as meaning_mod
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)

    yield factory
    asyncio.run(engine.dispose())


def _add_mem(factory, *, content, memory_type="insight", sub_type=None, source=None,
             age_hours=0.0, valid_to=None, importance=50.0):
    """直接落一条记忆行（created_at 显式回拨，避免依赖库时钟）。"""
    async def _main():
        async with factory() as db:
            m = Memory(
                user_id=3, character_id=13, memory_type=memory_type, content=content,
                sub_type=sub_type, source=source, importance=importance,
                strength_days=S_BY_TYPE.get(memory_type, 7.0),
                created_at=_now() - timedelta(hours=age_hours), valid_to=valid_to,
                status="active", is_archived=False,
            )
            db.add(m)
            await db.commit()
            return m.id

    return asyncio.run(_main())


def _recall(factory, ids):
    """走真实召回出口 _rerank，返回被保留的 id 列表（顺序不敏感，本文件只断言在/不在）。"""
    async def _main():
        results = [{"id": i, "content": "x", "type": "insight", "importance": 50.0} for i in ids]
        ranked = await _rerank(results, 13)
        return [r["id"] for r in ranked]

    return asyncio.run(_main())


def _run_status_update(text="两人在吃饭", char=13, user=3):
    asyncio.run(chat_svc._save_status_update(char, text, user))


# ─────────────────────────── 1) 边界：11h59m 有效 / 12h01m 失效 ───────────────────────────

def test_判据_11h59m_仍算有效():
    """纯判据：写入后 11h59m 仍在 12h 窗内（不得提前剔除）。"""
    created = _now() - timedelta(hours=11, minutes=59)
    assert status_memory_expired("status", "status", None, created, _now()) is False


def test_判据_12h01m_已失效():
    """纯判据：写入后 12h01m 已超窗 ⇒ 剔除（边界另一侧）。"""
    created = _now() - timedelta(hours=12, minutes=1)
    assert status_memory_expired("status", "status", None, created, _now()) is True


def test_判据_恰好整12小时判失效_与事实侧同口径():
    """比较方向钉死：到期时刻本身即「不再算现状」（<= now 判过期），两侧同进同退。"""
    created = datetime(2026, 9, 29, 0, 0, 0)
    exp = status_valid_to(created)
    assert status_memory_expiry(None, created) == exp
    assert status_memory_expired("status", "status", None, created, exp) is True
    assert status_memory_expired("status", "status", None, created, exp - timedelta(seconds=1)) is False


def test_召回出口_11h59m仍在_12h01m不再出现(ttl_db):
    """真实 SQL 回填池：11h59m 的状态条仍在召回结果里，12h01m 的已被剔除。"""
    fresh = _add_mem(ttl_db, content="状态更新: 刚吃饭", sub_type="status", source="status", age_hours=11 + 59 / 60)
    stale = _add_mem(ttl_db, content="状态更新: 很久前", sub_type="status", source="status", age_hours=12 + 1 / 60)
    kept = _recall(ttl_db, [fresh, stale])
    assert kept == [fresh]


def test_召回出口_超窗后不再出现在注入结果(ttl_db):
    """行为变更点本身：过期状态条不再进召回（=不再被当现状注入对话）。"""
    mid = _add_mem(ttl_db, content="状态更新: 三人在旅游", sub_type="status", source="status", age_hours=26)
    assert _recall(ttl_db, [mid]) == []
    assert _status_ttl_on() is True          # 本用例走的是「开关开」这一侧


# ─────────────────────── 2) 其它记忆类型逐例不受影响（回归证明）───────────────────────

_OTHER_ROWS = [
    # (label, memory_type, sub_type, source)
    ("抽取出的状态条（生产 778 行）", "insight", "status", "chat"),
    ("用户印象-位置", "user_info", "location", "chat"),
    ("用户印象-易变现状", "user_info", "current_state", "chat"),
    ("长期偏好", "preference", "food", "chat"),
    ("往事事件", "event", "moment", "diary"),
    ("计划条（valid_to 另判）", "event", "plan", "chat"),
    ("关系摘要", "insight", "relationship", "chat"),
    ("洞察-普通来源", "insight", "summary", "chat"),
    ("洞察-无子类无来源", "insight", None, None),
    ("群聊逐条", "event", "group", "group"),
    ("剧情来源", "insight", "storyline", "storyline"),
]


@pytest.mark.parametrize("label,mtype,sub,src", _OTHER_ROWS, ids=[r[0] for r in _OTHER_ROWS])
def test_其它记忆类型逐例不受影响(ttl_db, label, mtype, sub, src):
    """非「状态派生条」（sub_type≠status 或 source≠status）一律不剔除，哪怕放了 400 天。"""
    mid = _add_mem(ttl_db, content=f"{label} 正文", memory_type=mtype, sub_type=sub, source=src, age_hours=24 * 400)
    assert _recall(ttl_db, [mid]) == [mid], f'{label} 被误剔除'


def test_身份判定要求两个条件同时成立():
    """只按 sub_type 会误伤抽取条（生产实测 873 行里 778 行是 source='chat'）⇒ 必须双条件。"""
    assert is_status_derived_memory("status", "status") is True
    assert is_status_derived_memory("status", "chat") is False
    assert is_status_derived_memory("relationship", "status") is False
    assert is_status_derived_memory(None, None) is False
    assert STATUS_MEMORY_SUB_TYPE == "status" and STATUS_MEMORY_SOURCE == "status"


def test_存量行无valid_to_按created_at补算(ttl_db):
    """打标链路之前的存量行（生产 95/95 valid_to 为空）⇒ 用 created_at + 同一窗口补算，口径不分裂。"""
    old = _add_mem(ttl_db, content="状态更新: 存量旧条", sub_type="status", source="status", age_hours=13, valid_to=None)
    new = _add_mem(ttl_db, content="状态更新: 存量新条", sub_type="status", source="status", age_hours=1, valid_to=None)
    assert _recall(ttl_db, [old, new]) == [new]


def test_行上有valid_to时以它为准(ttl_db):
    """已带同源标记的行：以 valid_to 为失效时刻（与 WorldFact.expires_at 同一瞬间），created_at 不参与。"""
    vt_future = _now() + timedelta(hours=6)
    keep = _add_mem(ttl_db, content="状态更新: 标记未到期", sub_type="status", source="status", age_hours=30, valid_to=vt_future)
    vt_past = _now() - timedelta(minutes=1)
    drop = _add_mem(ttl_db, content="状态更新: 标记已到期", sub_type="status", source="status", age_hours=0, valid_to=vt_past)
    assert _recall(ttl_db, [keep, drop]) == [keep]


def test_无从判断时不剔除(ttl_db):
    """valid_to 与 created_at 都缺 ⇒ status_memory_expiry 返回 None，判「未过期」（保守不误删）。"""
    assert status_memory_expiry(None, None) is None
    assert status_memory_expired("status", "status", None, None, _now()) is False


# ─────────────────────── 3) 常量单源（源码棘轮：12h 只此一处定义）───────────────────────

_APP_DIR = Path(facts_mod.__file__).resolve().parent.parent


def _app_py_sources():
    return {p: p.read_text(encoding="utf-8-sig", errors="replace")
            for p in _APP_DIR.rglob("*.py") if "__pycache__" not in p.parts}


def test_棘轮_全仓仅一处定义12小时():
    """STATUS_FRESH_HOURS 的定义点必须只有一个（= events/facts.py），且数值 12。"""
    defs = []
    for path, text in _app_py_sources().items():
        for m in re.finditer(r"^[ \t]*STATUS_FRESH_HOURS[ \t]*=[ \t]*([0-9]+)", text, re.M):
            defs.append((path.name, int(m.group(1))))
    assert defs == [("facts.py", STATUS_FRESH_HOURS)], f"12h 出现多处定义：{defs}"
    assert STATUS_FRESH_HOURS == 12


def test_棘轮_读侧与memory侧不得各写一份小时数():
    """读侧只能引用 facts 的判据函数：禁止 timedelta(hours=<数字>) / 秒数换算 / 再定义 TTL 常量。"""
    for fn in (retrieve_mod._drop_expired_status_memories, retrieve_mod._status_ttl_on):
        src = _code_only(fn)   # 只看代码：文档字符串里点名常量属说明，不算绕过
        assert not re.search(r"hours\s*=\s*[0-9]", src), f"{fn.__name__} 硬编码小时数"
        assert "timedelta(" not in src, f"{fn.__name__} 自行算时长"
        assert "STATUS_FRESH_HOURS" not in src, f"{fn.__name__} 绕过判据函数直取窗口常量"
    # 衰减档案侧（constants / lifecycle_policy）同样不得出现第二个 12h 定义
    for mod in ("memory/constants.py", "memory/lifecycle_policy.py"):
        text = (_APP_DIR / mod).read_text(encoding="utf-8-sig")
        assert not re.search(r"^[ \t]*\w*TTL\w*[ \t]*=[ \t]*(12|0\.5)(?![0-9])", text, re.M), \
            f"{mod} 另立 12h 常量（数值唯一来源必须是 events/facts.STATUS_FRESH_HOURS）"


def test_棘轮_判据函数内部只走status_valid_to():
    """补算路径必须复用 status_valid_to（同一数学出口），不得在 facts 里另写一次时长。"""
    src = _code_only(facts_mod.status_memory_expiry)
    assert "status_valid_to(" in src
    assert not re.search(r"hours\s*=\s*[0-9]", src)
    assert "timedelta(hours=STATUS_FRESH_HOURS)" in inspect.getsource(status_valid_to)


def _code_only(fn) -> str:
    """函数「纯代码」视图：剥掉 docstring 与行内 # 注释（棘轮只看代码）。"""
    src = textwrap.dedent(inspect.getsource(fn))
    node = ast.parse(src).body[0]
    first = node.body[0]
    skip = 0
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        skip = first.end_lineno
    out = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i <= skip:
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


def test_统一后两侧窗口长度相等():
    """行为口径的核心断言：Memory 侧补算窗口 == WorldFact 侧新鲜窗（都是 12h）。"""
    t = datetime(2026, 9, 29, 3, 0, 0)
    mem_window = status_memory_expiry(None, t) - t
    assert mem_window == timedelta(hours=STATUS_FRESH_HOURS) == timedelta(hours=12)
    assert facts_mod._TRANSIENT_FRESH_HOURS["status"] == STATUS_FRESH_HOURS


# ─────────────────────── 4) 回退路径 ───────────────────────

def test_回退_开关关_过期状态条照旧召回(ttl_db, monkeypatch):
    """flag status_memory_ttl 置 False＝逐字节旧行为（超窗条不剔除）。"""
    monkeypatch.setitem(AGENT_FLAGS, STATUS_MEMORY_TTL_FLAG, False)
    stale = _add_mem(ttl_db, content="状态更新: 旧近况", sub_type="status", source="status", age_hours=24 * 3)
    assert _status_ttl_on() is False
    assert _recall(ttl_db, [stale]) == [stale]


def test_回退_开关关_其它类型也不受影响(ttl_db, monkeypatch):
    """关＝整体不动：其它类型同样原样返回（回退后无残留影响）。"""
    monkeypatch.setitem(AGENT_FLAGS, STATUS_MEMORY_TTL_FLAG, False)
    ids = [_add_mem(ttl_db, content=f"c{i}", memory_type="user_info", sub_type="location", source="chat", age_hours=24 * 90)
           for i in range(3)]
    assert sorted(_recall(ttl_db, ids)) == sorted(ids)


def test_取闸异常回落旧行为_not_drop(monkeypatch):
    """读不到开关（异常）⇒ 判「关」，一条都不剔除（新逻辑不得影响既有召回）。"""
    import app.flags.agent_flags as af

    class _Boom:
        def get(self, *_a, **_kw):
            raise RuntimeError("flags unavailable")

    monkeypatch.setattr(af, "AGENT_FLAGS", _Boom())
    assert _status_ttl_on() is False


def test_判定异常不阻塞主链路(ttl_db, monkeypatch):
    """判据函数抛错 ⇒ fail-open 原样返回（宁可多留一条过期状态，也不误删其它记忆）。"""
    mid = _add_mem(ttl_db, content="状态更新: 异常样本", sub_type="status", source="status", age_hours=99)

    def _boom(*_a, **_kw):
        raise RuntimeError("facts unavailable")

    monkeypatch.setattr(facts_mod, "status_memory_expired", _boom)
    assert _recall(ttl_db, [mid]) == [mid]


def test_空候选池不触发判定(ttl_db):
    """results 为空时 _rerank 直接返回（不查库、不进剔除逻辑）。"""
    assert asyncio.run(_rerank([], 13)) == []
    assert _drop_expired_status_memories({}, _now()) == {}


# ─────────────────────── 5) 真实双写：两侧同进同退 ───────────────────────

def _mem_and_fact(factory):
    async def _main():
        async with factory() as db:
            mems = (await db.execute(select(Memory).where(Memory.character_id == 13)
                                     .order_by(Memory.id))).scalars().all()
            facts = (await db.execute(select(WorldFact).where(WorldFact.character_id == 13)
                                      .order_by(WorldFact.id))).scalars().all()
            return list(mems), list(facts)

    return asyncio.run(_main())


def _backdate(factory, mem_id, fact_id, hours):
    async def _main():
        async with factory() as db:
            m = (await db.execute(select(Memory).where(Memory.id == mem_id))).scalar_one()
            m.created_at -= timedelta(hours=hours)
            m.valid_to -= timedelta(hours=hours)
            f = (await db.execute(select(WorldFact).where(WorldFact.id == fact_id))).scalar_one()
            f.asserted_at -= timedelta(hours=hours)
            f.expires_at -= timedelta(hours=hours)
            await db.commit()

    asyncio.run(_main())


@pytest.mark.parametrize("hours,expect_mem,expect_fact", [
    (11, True, True),    # 窗内：两侧都还算「现状」
    (13, False, False),  # 超窗：两侧同步失效（本批要的效果）
], ids=["回拨11小时_两侧都还在", "回拨13小时_两侧都没了"])
def test_真实双写后两侧同进同退(ttl_db, hours, expect_mem, expect_fact):
    """一次真实双写 → 整体回拨：记忆召回与世界事实注入要么都有、要么都没有。"""
    _run_status_update("两人在吃晚饭")
    mems, facts = _mem_and_fact(ttl_db)
    assert len(mems) == 1 and len(facts) == 1
    assert mems[0].valid_to == facts[0].expires_at      # 同一瞬间（上一批已钉）
    _backdate(ttl_db, mems[0].id, facts[0].id, hours)
    mems, facts = _mem_and_fact(ttl_db)
    kept = _recall(ttl_db, [mems[0].id])
    visible = asyncio.run(get_active_facts(character_id=13, user_id=3,
                                           viewer_type="character", viewer_id=13))
    assert (kept == [mems[0].id]) is expect_mem
    assert ([f.id for f in visible] == [facts[0].id]) is expect_fact


def test_写入侧字段与衰减档未被本批改动(ttl_db):
    """行为变更只在读侧：写入的 memory_type/sub_type/source/强度/复习排期逐字不变。"""
    _run_status_update("两人在看书")
    m = _mem_and_fact(ttl_db)[0][0]
    assert (m.memory_type, m.sub_type, m.source) == ("insight", "status", "status")
    assert m.content == "状态更新: 两人在看书"
    assert m.strength_days == S_BY_TYPE["insight"] == 7.0
    assert m.next_review_at > m.created_at
    assert m.status == "active" and m.is_archived is False
    # 通用衰减档与阈值未被改动（牵连全部 insight，本批禁止）
    assert DECAY_THRESHOLD_PCT == 20.0 and DECAY_COUNTDOWN_DAYS == 3


# ─────────────────────── 6) 开关双向登记（目录锁）───────────────────────

def test_新键同时登记进开关表与目录():
    """AGENT_FLAGS ↔ flag_catalog 双向一致（否则 test_flag_catalog_metadata 会红）。"""
    from app.application.flag_catalog import FLAG_CATALOG

    assert AGENT_FLAGS[STATUS_MEMORY_TTL_FLAG] is True     # 默认开＝新口径生效
    assert STATUS_MEMORY_TTL_FLAG in FLAG_CATALOG
    meta = FLAG_CATALOG[STATUS_MEMORY_TTL_FLAG]
    assert meta["group"] == "memory"
    for k in ("title_zh", "desc_zh", "title_en", "desc_en"):
        assert str(meta[k]).strip(), f"目录文案缺失：{k}"
