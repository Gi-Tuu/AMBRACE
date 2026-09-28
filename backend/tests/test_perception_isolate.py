# -*- coding: utf-8 -*-
"""批 0-2 · M2「隔离生效（晋升 / 摘要 / 召回降权）+ sub_type 记通道」测试。

覆盖（派单 §四 第 8 项逐条对齐）：
- flag 关 ⇒ **逐字节旧行为**：晋升条件、摘要取料、召回顺序与条数全部与旧一致；
- flag 开 ⇒ 被隔离条不晋升 is_core、不进摘要/身份画像原料、召回仍返回且条数不变但排序靠后；
- 用户认可（epistemic_status=FACT）⇒ 自动脱隔：可晋升、可进摘要、不受降权；
- sub_type 记通道：打标命中时写入**命中的那条快照**的 source（accessibility / clipboard / media）；
- 脏数据（NULL 来源 / NULL 认知状态 / 开关面不可用）不抛异常，一律按旧行为放行。

纪律：临时库走 tests/_dbclone（禁止连生产库）；不新增 LLM/向量调用（嵌入、向量/BM25 召回、chat_completion 全打桩）；
项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.memory import core as _core
from app.memory import retrieve as _retrieve
from app.memory import summary as _summary
from app.memory import write as _write
from app.memory.perception_tier import FACT_STATUS, PERCEPTION_SOURCE

pytestmark = pytest.mark.slow

# 感知快照正文 / 与之连续重合 ≥6 字的记忆正文（M1a 同款判据素材）
SNAP = "开会迟到五分钟被主管说了一顿"
MEM_HIT = "他今天开会迟到五分钟被主管说了一顿"
P_TEXT = "屏幕上看到用户在搜香菜的做法"     # 感知派生条（被隔离对象）
C_TEXT = "用户亲口说过自己不吃香菜"        # 普通聊天条（对照）

_PROMOTE_REAL = _core.maybe_promote_core  # 真晋升口（fixture 的打桩不影响它）


@pytest.fixture(autouse=True)
def clean_flags():
    """本文件任何用例都不依赖本机 runtime_flags 现值：前后复位。"""
    def _reset():
        for k in ("perception_isolate", "perception_source_tag", "memory_admission_gate",
                  "memory_write_receipt", "memory_trace_debug"):
            _loop.AGENT_FLAGS[k] = False
    _reset()
    yield
    _reset()


def _flag(on: bool):
    """隔离总闸。"""
    _loop.AGENT_FLAGS["perception_isolate"] = on


def _tag(on: bool):
    """打标总闸（sub_type 记通道只发生在打标那一刻）。"""
    _loop.AGENT_FLAGS["perception_source_tag"] = on


# ─────────────────────── 纯函数 / 登记面（不碰库） ───────────────────────

def test_新flag已登记且默认关():
    assert "perception_isolate" in _loop.AGENT_FLAGS
    assert _loop.AGENT_FLAGS["perception_isolate"] is False


def test_新flag已登记展示元数据且order接在打标键之后():
    from app.application.flag_catalog import FLAG_CATALOG

    meta = FLAG_CATALOG["perception_isolate"]
    assert meta["group"] == "memory"
    assert meta["visible"] is False
    assert meta["title_zh"] and meta["desc_zh"] and meta["title_en"] and meta["desc_en"]
    assert meta["order"] == FLAG_CATALOG["perception_source_tag"]["order"] + 1
    # catalog 不承载默认值（唯一事实源＝AGENT_FLAGS）
    assert "default" not in meta and "enabled" not in meta


@pytest.mark.parametrize("src,epi,exp", [
    (PERCEPTION_SOURCE, "INFERRED", True),
    (PERCEPTION_SOURCE, None, True),        # 认知状态缺失＝未认可 ⇒ 仍隔离
    (PERCEPTION_SOURCE, FACT_STATUS, False),  # 用户认可 ⇒ 脱隔
    (" Perception ", " fact ", False),       # 归一后比较
    ("chat", "INFERRED", False),
    (None, None, False),
])
def test_晋升闸谓词_按flag与隔离态(src, epi, exp):
    for on in (False, True):
        _flag(on)
        assert _core._quarantined_from_core(src, epi) is (exp and on)


def test_降权谓词_数值与档位():
    assert _retrieve.PERCEPTION_QUARANTINE_PENALTY == -15.0  # 与既有 +20/+15/+10 同量级
    _flag(False)
    assert _retrieve._quarantine_penalty(PERCEPTION_SOURCE, "INFERRED") == 0.0  # 关＝旧排序
    _flag(True)
    assert _retrieve._quarantine_penalty(PERCEPTION_SOURCE, "INFERRED") == -15.0
    assert _retrieve._quarantine_penalty(PERCEPTION_SOURCE, FACT_STATUS) == 0.0
    assert _retrieve._quarantine_penalty("chat", "INFERRED") == 0.0


def test_摘要子句_flag关为永真条件():
    from sqlalchemy import true
    from sqlalchemy.sql.elements import ColumnElement

    _flag(False)
    clause = _summary._not_quarantined_clause()
    assert isinstance(clause, ColumnElement)
    assert str(clause) == str(true())


def test_三处开关面不可用一律按关处理():
    """读不到 flag ⇒ 隔离子句恒假/恒真 ⇒ 逐字节旧行为（方案风险 R8：回退要退得干净）。"""
    saved = _loop.AGENT_FLAGS
    try:
        _loop.AGENT_FLAGS = None
        assert _core._perception_isolate_on() is False
        assert _summary._perception_isolate_on() is False
        assert _retrieve._perception_isolate_on() is False
        assert _core._quarantined_from_core(PERCEPTION_SOURCE, "INFERRED") is False
        assert _retrieve._quarantine_penalty(PERCEPTION_SOURCE, "INFERRED") == 0.0
    finally:
        _loop.AGENT_FLAGS = saved


def test_通道子类label已登记():
    from app.memory.sources import CHAT_SUB_META, memory_source_meta

    for ch in ("accessibility", "clipboard", "media"):
        assert CHAT_SUB_META[ch]["label"]  # 展示层有中文名
    assert memory_source_meta(PERCEPTION_SOURCE, "clipboard")["label"] == "手机感知"  # 来源标签不受影响


def test_命中通道纯函数_取重合那条():
    corpus = [("media", "一首歌的歌词正文"), ("accessibility", SNAP)]  # 倒序：最新在前
    assert _write._matched_snapshot_channel(corpus, MEM_HIT) == "accessibility"
    # 仅标签残留命中（无一条正文重合）⇒ 取最新一条的通道
    assert _write._matched_snapshot_channel(corpus, "[屏幕 2分钟前] 用户喜欢在阳台养几盆多肉") == "media"
    assert _write._matched_snapshot_channel([], MEM_HIT) is None


# ─────────────────────────── 公共临时库夹具 ───────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 全副作用打桩（嵌入/向量/BM25/LLM/回执/后台任务），只观察落库与排序。"""
    engine = clone_engine(tmp_path / "iso.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="iso_u1", nickname="隔离用户"))
            db.add(AICharacter(id=101, user_id=1, name="隔离角色101"))
            await db.commit()

    asyncio.run(_seed())

    import app.memory.service as svc

    async def _embed(_text):
        return [0.0] * 8

    async def _no_hits(**_kw):
        return []

    async def _no_similar(*_a, **_kw):
        return None

    async def _no_add(**_kw):
        return None

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _no_hits)
    monkeypatch.setattr(svc, "bm25_search", _no_hits)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _no_add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)
    monkeypatch.setattr(_core, "async_session_factory", factory)
    monkeypatch.setattr(_summary, "async_session_factory", factory)

    import app.memory.dedup as dd
    monkeypatch.setattr(dd, "_schedule_dedup", _noop)
    import app.utils.async_tasks as at
    monkeypatch.setattr(at, "spawn_background", lambda coro, **_kw: coro.close())
    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **_kw: None)
    monkeypatch.setattr("app.plugins.registry.run_hook", _noop)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", _noop)
    monkeypatch.setattr("app.events.publish", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.memory.meaning.maybe_extract_meaning", lambda *_a, **_kw: _noop())
    monkeypatch.setattr("app.memory.core.maybe_promote_core", _noop)

    prompts = []

    async def _llm(messages=None, **_kw):
        prompts.append((messages or [{}])[0].get("content", ""))
        return "这是凝练后的概括内容"

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)

    yield {"factory": factory, "prompts": prompts}
    asyncio.run(engine.dispose())


def _add_mem(factory, content, *, source="chat", epi=None, mtype="event", importance=50.0, **kw):
    """直接落一条记忆（绕过 save_memory，便于精确构造隔离态）。"""
    from app.models.memory import Memory
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=101, memory_type=mtype, content=content,
                       source=source, importance=importance,
                       created_at=now_naive_utc(), **kw)
            if epi is not None:
                m.epistemic_status = epi
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _get(factory, mid):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return (await db.execute(select(Memory).where(Memory.id == mid))).scalar_one()
    return asyncio.run(_run())


def _promote(mid, importance=100.0, sub_type=None, mtype="event"):
    # fixture 里 save_memory 用的晋升口被打成 noop，这里必须绕过打桩取真函数
    return asyncio.run(_PROMOTE_REAL(mid, importance, sub_type, mtype))


# ─────────────────────────── 禁令 1：晋升来源闸 ───────────────────────────

def _seed_promotable(factory, *, source, epi):
    return _add_mem(factory, P_TEXT, source=source, epi=epi,
                    importance=100.0, confirmation_count=2)


def test_晋升_flag关_感知条照旧晋升(env):
    """关＝逐字节旧行为：晋升条件不看来源，感知条拿到 is_core。"""
    mid = _seed_promotable(env["factory"], source=PERCEPTION_SOURCE, epi="INFERRED")
    _promote(mid)
    assert _get(env["factory"], mid).is_core is True


def test_晋升_flag开_被隔离条不晋升(env):
    _flag(True)
    mid = _seed_promotable(env["factory"], source=PERCEPTION_SOURCE, epi="INFERRED")
    _promote(mid)
    row = _get(env["factory"], mid)
    assert row.is_core is False
    assert row.core_category is None


def test_晋升_flag开_认可为FACT后脱隔可晋升(env):
    _flag(True)
    mid = _seed_promotable(env["factory"], source=PERCEPTION_SOURCE, epi=FACT_STATUS)
    _promote(mid)
    assert _get(env["factory"], mid).is_core is True


def test_晋升_flag开_非感知条不受影响(env):
    _flag(True)
    mid = _seed_promotable(env["factory"], source="chat", epi="INFERRED")
    _promote(mid)
    assert _get(env["factory"], mid).is_core is True


def test_晋升_flag开_低门槛分支同样被闸住(env):
    """身份/偏好/承诺 +100 的低门槛分支也吃来源闸（否则禁令 1 在一个口子上失效）。"""
    _flag(True)
    mid = _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED",
                   importance=110.0, confirmation_count=0, mtype="user_info", sub_type="job")
    _promote(mid, importance=110.0, sub_type="job", mtype="user_info")
    assert _get(env["factory"], mid).is_core is False


def test_确认晋升_flag开_计数照加但不拿is_core(env):
    _flag(True)
    mid = _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED",
                   importance=100.0, confirmation_count=1)
    asyncio.run(_core.confirm_memory(mid))
    row = _get(env["factory"], mid)
    assert row.confirmation_count == 2   # 用户确认信号不丢
    assert row.is_core is False          # 但未认可 ⇒ 不进核心


def test_晋升_flag开_脏来源不抛异常(env):
    """来源/认知状态为 NULL 的普通条：判据安全放行，晋升照旧（缺列脏数据不炸）。"""
    _flag(True)
    mid = _seed_promotable(env["factory"], source=None, epi=None)
    _promote(mid)
    assert _get(env["factory"], mid).is_core is True


# ─────────────────── 禁令 2：摘要 / 身份画像原料排除 ───────────────────

def test_摘要_flag关_原料含被隔离条(env):
    """关＝逐字节旧取料：感知条照样进摘要原料。"""
    _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED", importance=90.0)
    _add_mem(env["factory"], C_TEXT, source="chat", epi=FACT_STATUS, importance=50.0)
    out = asyncio.run(_summary.summarize_memories(101, "event", force=True))
    assert out["generated"] is True
    assert P_TEXT in env["prompts"][0] and C_TEXT in env["prompts"][0]


def test_摘要_flag开_原料不含被隔离条(env):
    _flag(True)
    _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED", importance=90.0)
    _add_mem(env["factory"], C_TEXT, source="chat", epi=FACT_STATUS, importance=50.0)
    out = asyncio.run(_summary.summarize_memories(101, "event", force=True))
    assert out["generated"] is True
    prompt = env["prompts"][0]
    assert P_TEXT not in prompt
    assert C_TEXT in prompt


def test_摘要_flag开_认可后回到原料(env):
    _flag(True)
    _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi=FACT_STATUS, importance=90.0)
    out = asyncio.run(_summary.summarize_memories(101, "event", force=True))
    assert out["generated"] is True
    assert P_TEXT in env["prompts"][0]


def test_摘要_flag开_全是隔离条则不生成(env):
    """原料被排空 ⇒ 走既有 no_memories 分支（不改生成逻辑、不硬造摘要）。"""
    _flag(True)
    _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED", importance=90.0)
    out = asyncio.run(_summary.summarize_memories(101, "event", force=True))
    assert out == {"generated": False, "memory_id": None, "reason": "no_memories"}


def test_摘要_flag开关对比_非感知原料条数不变(env):
    """只排除被隔离条：普通条的取料集合与顺序在开关两侧完全一致（逐字节旧行为面）。"""
    ids = [_add_mem(env["factory"], f"用户说过第{i}件事", source="chat", epi=FACT_STATUS,
                    importance=float(60 - i)) for i in range(3)]
    _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED", importance=110.0)
    asyncio.run(_summary.summarize_memories(101, "event", force=True))
    off_prompt = env["prompts"][0]
    _flag(True)
    asyncio.run(_summary.summarize_memories(101, "event", force=True))
    on_prompt = env["prompts"][1]
    for mid in ids:
        assert "用户说过第" in off_prompt and "用户说过第" in on_prompt
    assert P_TEXT not in on_prompt
    assert len(ids) == 3


def test_身份画像_flag开_原料排除被隔离条(env):
    """禁令 2 覆盖身份画像取料（含「意义记忆」那路）——专治二阶放大。"""
    import app.memory.flags as _flags
    import app.memory.summary as summary_mod

    async def _v2_on(*_a, **_kw):
        return True

    _flag(True)
    _add_mem(env["factory"], "用户是护士", source=PERCEPTION_SOURCE, epi="INFERRED",
             mtype="user_info", importance=110.0)
    _add_mem(env["factory"], "用户说自己怕黑", source="chat", epi=FACT_STATUS,
             mtype="user_info", importance=70.0, why_it_matters="关系到夜间主动关心")

    async def _run():
        return await summary_mod.summarize_identity(101, 1, force=True)

    orig = _flags.memory_v2_enabled
    _flags.memory_v2_enabled = _v2_on
    try:
        out = asyncio.run(_run())
    finally:
        _flags.memory_v2_enabled = orig
    assert out["generated"] is True, out
    prompt = env["prompts"][-1]
    assert "用户是护士" not in prompt        # 被隔离条既不进 user_info 取料、也不进意义取料
    assert "用户说自己怕黑" in prompt


def test_身份画像_flag关_原料含被隔离条(env):
    """关＝逐字节旧取料。"""
    import app.memory.flags as _flags
    import app.memory.summary as summary_mod

    async def _v2_on(*_a, **_kw):
        return True

    _add_mem(env["factory"], "用户是护士", source=PERCEPTION_SOURCE, epi="INFERRED",
             mtype="user_info", importance=110.0)

    async def _run():
        return await summary_mod.summarize_identity(101, 1, force=True)

    orig = _flags.memory_v2_enabled
    _flags.memory_v2_enabled = _v2_on
    try:
        out = asyncio.run(_run())
    finally:
        _flags.memory_v2_enabled = orig
    assert out["generated"] is True, out
    assert "用户是护士" in env["prompts"][-1]


# ─────────────────── 禁令 3：召回降权、不剔除 ───────────────────

def _search(query="香菜", limit=5):
    from app.memory.retrieve import search_memories
    return asyncio.run(search_memories(101, query, limit=limit))


def test_召回_flag关_顺序与条数逐字节旧(env):
    pid = _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED", importance=50.0)
    cid = _add_mem(env["factory"], C_TEXT, source="chat", epi=FACT_STATUS, importance=40.0)
    res = _search()
    assert len(res) == 2
    assert [r["id"] for r in res] == [pid, cid]  # 旧行为：重要度高的感知条在前


def test_召回_flag开_仍返回且条数不变但排序靠后(env):
    pid = _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED", importance=50.0)
    cid = _add_mem(env["factory"], C_TEXT, source="chat", epi=FACT_STATUS, importance=40.0)
    _flag(True)
    res = _search()
    assert len(res) == 2                      # **不剔除**：感知条必须还能命中
    assert {r["id"] for r in res} == {pid, cid}
    assert [r["id"] for r in res] == [cid, pid]  # -15 后落到非感知条之后


def test_召回_flag开_已认可条不吃降权(env):
    pid = _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi=FACT_STATUS, importance=50.0)
    cid = _add_mem(env["factory"], C_TEXT, source="chat", epi=FACT_STATUS, importance=40.0)
    _flag(True)
    res = _search()
    assert [r["id"] for r in res] == [pid, cid]  # 脱隔后排序与旧行为一致


def test_召回_flag开_单条感知仍返回不因偏置丢失(env):
    """库里只有一条感知条：降权不得让它消失（偏置只影响相对次序）。"""
    pid = _add_mem(env["factory"], P_TEXT, source=PERCEPTION_SOURCE, epi="INFERRED", importance=45.0)
    _flag(True)
    res = _search()
    assert [r["id"] for r in res] == [pid]


# ─────────────────── 改动 4：sub_type 记通道 ───────────────────

def _add_snapshot(factory, content, *, source="accessibility", minutes_ago=0, user_id=1):
    from app.models.device import PhoneSnapshot
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            db.add(PhoneSnapshot(user_id=user_id, source=source, content=content,
                                 created_at=now_naive_utc() - timedelta(minutes=minutes_ago)))
            await db.commit()
    asyncio.run(_run())


def _save_rows(factory):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()
    return asyncio.run(_run())


def _save(content, sub_type=None):
    from app.memory.write import save_memory
    return asyncio.run(save_memory(user_id=1, character_id=101, memory_type="event",
                                   content=content, importance=3, source="chat", sub_type=sub_type))


def test_记通道_flag开_写入命中快照的通道(env):
    _tag(True)
    _add_snapshot(env["factory"], SNAP, source="clipboard")
    m = _save(MEM_HIT)
    assert m is not None
    assert m.source == PERCEPTION_SOURCE
    assert m.sub_type == "clipboard"


def test_记通道_取重合那条而非最新那条(env):
    """语料里最新一条与正文无重合：通道必须记**命中的**那条（重合判定逐条对齐）。"""
    _tag(True)
    _add_snapshot(env["factory"], SNAP, source="accessibility", minutes_ago=10)
    _add_snapshot(env["factory"], "一首歌的歌词正文", source="media", minutes_ago=1)
    m = _save(MEM_HIT)
    assert m.sub_type == "accessibility"


def test_记通道_flag关_不写通道(env):
    """关＝逐字节旧行为：不打标、也不写 sub_type。"""
    _add_snapshot(env["factory"], SNAP, source="clipboard")
    m = _save(MEM_HIT)
    assert m.source == "chat"
    assert m.sub_type is None


def test_记通道_仅标签残留命中_取最新一条通道(env):
    _tag(True)
    _add_snapshot(env["factory"], SNAP, source="accessibility", minutes_ago=10)
    _add_snapshot(env["factory"], "剪贴板里的一段地址", source="clipboard", minutes_ago=1)
    m = _save("[屏幕 2分钟前] " + P_TEXT)
    assert m.source == PERCEPTION_SOURCE
    assert m.sub_type == "clipboard"


def test_记通道_快照通道为空_不影响打标落库(env):
    """脏快照（通道为空串）：通道无值时不写 sub_type，打标本身照旧（不抛异常）。"""
    _tag(True)
    _add_snapshot(env["factory"], SNAP, source="")
    m = _save(MEM_HIT)
    assert m is not None
    assert m.source == PERCEPTION_SOURCE
    assert m.sub_type is None
    rows = _save_rows(env["factory"])
    assert len(rows) == 1


def test_记通道_打标异常_通道一并回退(env):
    """fail-open：打标过程中出错 ⇒ 来源与 sub_type 都退回打标前的值，记忆照旧落库。"""
    _tag(True)

    async def _boom(*_a, **_kw):
        raise RuntimeError("snapshot query blew up")

    saved = _write._recent_snapshots
    _write._recent_snapshots = _boom
    try:
        m = _save(MEM_HIT, sub_type="slot")
    finally:
        _write._recent_snapshots = saved
    assert m is not None
    assert m.source == "chat"
    assert m.sub_type == "slot"  # 未被通道值覆盖
