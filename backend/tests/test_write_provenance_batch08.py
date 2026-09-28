# -*- coding: utf-8 -*-
"""批 0-8：写入来源链条标注 ＋ 晋升审计（2026-09-28，雷达 13 ＋ 簇5）。

覆盖：
- ①来源链条（纯函数面）：route 实测值空间逐类对齐、取值不越登记集合、证据锚点四类、
  槽名字段单一事实源（user_facts.MUTABLE_SLOTS）、脏输入不抛、推导异常 fail-open；
- ①来源链条（落库面）：create 回执带 provenance 且**既有四键一字未动**、chat 无子类型＝
  model_marked、并入（merge）回执补 incoming/into 且既有 kind/sim 不动、
  感知打标 ⇒ route=perception ＋ gates 记 perception_tagged、L4 改名 plan ⇒ route 仍按改名前判定；
- ②晋升审计：写入自动晋升留一条 action="promote"（reason 含重要度/确认数/类型/来源）、
  未达标不留痕、已是核心不重复留痕、用户确认路径 trigger=confirmation、
  被隔离条不晋升也不留痕、上限挤占记 evicted_memory_id、留痕发射异常不影响晋升；
- ③闸控与零语义：既有 flag `memory_write_receipt` 默认关 ⇒ **表里零行**（这两例还原真发射口与真
  后台调度，不信 recorder——recorder 不看 flag，沿用 M4/批 0-2 口径的 recorder 只用于观察 detail），
  开则真落库并用上面的 json_extract 口径读回；关时行取值/is_core 与旧一致。

**本批不新增 flag 的理由**（派单允许「纯回执增强可不加，需写明」）：产物只进
`memory_write_receipts`（detail_json 加键 ＋ 一类 action="promote" 新行），`memories` 行一字未改
⇒ 检索/注入/查重/晋升判据与阈值零变化；发射口已由既有 `memory_write_receipt`（默认关）闸控，
关＝零写入＝逐字节旧行为；再叠一层闸只会多出两个开关的口径漂移（且本单文件隔离不许改
loop.py / flag_catalog.py，新键也热切不了）。

只读查询口径（sqlite3 `mode=ro`，不写库）：
    SELECT action, reason, detail_json, created_at FROM memory_write_receipts
     WHERE memory_id = ? ORDER BY id;                                   -- 单条来路全链条
    SELECT json_extract(detail_json,'$.provenance.route') r, count(*)
     FROM memory_write_receipts WHERE action='create' GROUP BY r;        -- 来源链条占比
    SELECT json_extract(detail_json,'$.rule') k, count(*)
     FROM memory_write_receipts WHERE action='promote' GROUP BY k;       -- 晋升依据分布

纪律：临时库走 tests/_dbclone（禁止连生产库）；嵌入/向量/BM25/后台任务/LLM 全打桩，
不新增 LLM 或向量调用；项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.memory import core as _core
from app.memory import write as _write
from app.memory.perception_tier import PERCEPTION_SOURCE

pytestmark = pytest.mark.slow

# 晋升素材：重要度 5（★5＝pct 100）＋ 身份类槽（走既有的「高价值类别 + 100」低门槛那条路）
PROMOTABLE = dict(importance=5, sub_type="job", memory_type="user_info")
_TEXT_HIT = "用户爱吃红烧肉"
_TEXT_NEAR = "用户很爱吃红烧肉"


@pytest.fixture(autouse=True)
def clean_flags():
    """用例不依赖本机 runtime_flags 现值：前后复位（本批相关 ＋ 会串到的相邻开关）。"""
    keys = ("memory_write_receipt", "memory_admission_gate", "perception_source_tag",
            "perception_isolate", "review_plan_validity_extract", "write_dedup_active_only",
            "memory_chain_builder")

    def _reset():
        for k in keys:
            _loop.AGENT_FLAGS[k] = False
    _reset()
    yield
    _reset()


# ─────────────────────── ① 纯函数面（不碰库） ───────────────────────

@pytest.mark.parametrize("src,sub,exp", [
    # 生产实测前几名（2026-09-28 只读 group by source, sub_type）
    ("chat", "extracted", "extracted"),
    ("chat", "relationship", "extracted"),
    ("chat", "status", "extracted"),
    ("chat", "meta_guard", "extracted"),
    ("chat", "location", "extracted"),
    ("chat", None, "model_marked"),        # 【记忆：】标记与关键词规则同一落点
    ("chat", "", "model_marked"),
    ("chat", "   ", "model_marked"),
    (PERCEPTION_SOURCE, "clipboard", "perception"),
    ("life", "life_event", "self_narrative"),
    ("diary", "diary", "self_narrative"),
    ("moment", "moment", "self_narrative"),
    ("bio", None, "self_narrative"),
    ("status", "status", "self_narrative"),
    ("state_eval", "emotion", "self_narrative"),
    ("state_trigger", "preoccupation", "self_narrative"),
    ("reflection", None, "self_narrative"),
    ("summary", "summary", "digest"),
    ("game", "game_summary", "digest"),
    ("pet", None, "system_event"),
    ("group", "group", "system_event"),
    ("system", "departure", "system_event"),
    ("privacy_request", "privacy", "system_event"),
    ("global_sync", "job", "cross_role_sync"),
    # 未登记来源 ⇒ 暴露出来，而不是被塞进某个既有桶
    ("storyline", "storyline", "unregistered"),
    ("mcp_search", None, "unregistered"),
    (None, None, "unregistered"),
    ("", None, "unregistered"),
])
def test_route_实测值空间逐类对齐(src, sub, exp):
    assert _write._provenance_route(src, sub) == exp


def test_route_归一大小写与空白():
    assert _write._provenance_route(" Chat ", "extracted") == "extracted"
    assert _write._provenance_route(" PERCEPTION ", None) == "perception"


def test_route_取值不越出登记集合():
    """值空间钉住：新增 route 必须同时登记进 _PROVENANCE_ROUTE_KEYS（防口径漂移）。"""
    seen = set()
    for src in ("chat", "life", "diary", "moment", "bio", "status", "state_eval",
                "state_trigger", "reflection", "summary", "game", "pet", "group", "system",
                "privacy_request", "global_sync", PERCEPTION_SOURCE, "unknown_src", None, ""):
        for sub in (None, "", "extracted", "job", "plan", "relationship"):
            seen.add(_write._provenance_route(src, sub))
    assert seen <= set(_write._PROVENANCE_ROUTE_KEYS)


@pytest.mark.parametrize("src,sid,derived,exp", [
    (PERCEPTION_SOURCE, None, None, "phone_snapshot"),
    (PERCEPTION_SOURCE, 5, None, "phone_snapshot"),   # 感知优先：打标条的锚点是快照不是消息
    ("chat", 5, None, "chat_message"),
    ("life", 88, None, "record"),
    ("chat", None, [12, 13], "derived_memory"),
    ("chat", None, None, "none"),
    ("chat", None, [], "none"),
])
def test_evidence_kind_四类锚点(src, sid, derived, exp):
    assert _write._provenance_evidence_kind(src, sid, derived) == exp


def test_槽名字段单一事实源取user_facts():
    from app.memory.user_facts import MUTABLE_SLOTS

    assert _write._extractor_slot_names() == frozenset(MUTABLE_SLOTS)
    p = _write._derive_provenance(source="chat", sub_type="location", memory_type="user_info",
                                 actor="user", source_id=1, epistemic_status="FACT")
    assert p["slot"] == "location"
    # 非槽名子类型：slot 留空，不硬凑
    p2 = _write._derive_provenance(source="chat", sub_type="extracted", memory_type="event",
                                  actor="user", source_id=1, epistemic_status="FACT")
    assert p2["slot"] is None


def test_derive_脏输入不抛且键齐():
    p = _write._derive_provenance(source=None, sub_type=None, memory_type=None,
                                 actor=None, source_id=None, epistemic_status=None)
    assert set(p) == {"v", "route", "slot", "actor", "evidence", "gates", "stored"}
    assert p["route"] == "unregistered"
    assert p["actor"] == "unset"           # 归属缺失明确写 unset，不假装是 user
    assert p["gates"] == []
    assert p["evidence"]["kind"] == "none"
    assert p["evidence"]["derived_from_ids"] == []
    # 既有列原值一并带出（治理面一条回执看全链条，不必回查行）
    assert p["stored"] == {"source": None, "sub_type": None,
                           "memory_type": None, "epistemic_status": None}


def test_derive_记录本轮生效的改写与改名后原值():
    p = _write._derive_provenance(source="chat", sub_type="extracted", memory_type="event",
                                 actor="character", source_id=None, epistemic_status="INFERRED",
                                 derived_from_ids=[7], node_type="leaf",
                                 gates=["plan_renamed"], stored_sub_type="plan")
    assert p["route"] == "extracted"                  # 判定用**改名前**的 sub_type
    assert p["gates"] == ["plan_renamed"]
    assert p["stored"]["sub_type"] == "plan"          # 但如实记下行里存的是什么
    assert p["evidence"]["derived_from_ids"] == [7]
    assert p["evidence"]["node_type"] == "leaf"


def test_推导异常_fail_open不改写入():
    """留痕永远不得改变写入结果：推导炸 ⇒ 返回 None（回执照旧、只是少这个键）。"""
    saved = _write._derive_provenance
    try:
        def _boom(**_kw):
            raise RuntimeError("derive blew up")
        _write._derive_provenance = _boom
        assert _write._write_provenance(source="chat", sub_type=None, memory_type="event",
                                       actor="user", source_id=None,
                                       epistemic_status=None) is None
    finally:
        _write._derive_provenance = saved
    assert _write._write_provenance(source="chat", sub_type=None, memory_type="event",
                                    actor="user", source_id=None, epistemic_status=None)["route"] \
        == "model_marked"


# ─────────────────────── 落库面夹具 ───────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 ＋ 副作用打桩（嵌入/向量/BM25/后台任务/事件/插件钩子），回执与晋升走真路。

    回执用 recorder 捕获（与 M4/批 0-2 同一口径）：**晋升夹具不桩掉 maybe_promote_core**，
    这样「写入 ⇒ 自动晋升 ⇒ 审计回执」整条真链路都被跑到。
    """
    engine = clone_engine(tmp_path / "prov.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="prov_u1", nickname="来路用户"))
            db.add(AICharacter(id=101, user_id=1, name="来路角色101"))
            await db.commit()

    asyncio.run(_seed())

    import app.memory.service as svc

    async def _embed(_text):
        return [0.0] * 8

    async def _no_similar(*_a, **_kw):
        return None

    async def _no_add(**_kw):
        return None

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _no_add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)
    monkeypatch.setattr(_core, "async_session_factory", factory)

    import app.memory.dedup as dd
    import app.memory.receipt as rec
    import app.utils.async_tasks as at

    # 原函数必须在打桩**之前**取（取晚了拿到的是桩自己）：③ 闸控用例要还原真发射口/真后台调度
    real = {"real_emit": rec.emit_memory_receipt, "real_spawn": at.spawn_background}

    monkeypatch.setattr(dd, "_schedule_dedup", _noop)
    monkeypatch.setattr(at, "spawn_background", lambda coro, **_kw: coro.close())
    monkeypatch.setattr("app.events.publish", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.plugins.registry.run_hook", _noop)
    monkeypatch.setattr("app.memory.meaning.maybe_extract_meaning", lambda *_a, **_kw: _noop())

    receipts = []

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        receipts.append({"character_id": character_id, "memory_id": memory_id,
                         "action": action, "reason": reason, "detail": detail})

    # recorder **不看 flag**（沿用 M4/批 0-2 口径），所以「默认关＝零留痕」不能靠它自证：
    # 真发射口与真后台调度的句柄交给闸控用例还原，那里以「表里到底有几行」为准。
    monkeypatch.setattr(rec, "emit_memory_receipt", _rec)

    yield {"factory": factory, "receipts": receipts, "monkeypatch": monkeypatch, **real}
    asyncio.run(engine.dispose())


def _save(content, **kw):
    base = dict(user_id=1, character_id=101, memory_type="event", content=content)
    base.update(kw)
    return asyncio.run(_write.save_memory(**base))


def _rows(factory):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()
    return asyncio.run(_run())


def _receipts(env, action=None):
    return [r for r in env["receipts"] if action is None or r["action"] == action]


def _get(factory, mid):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return (await db.execute(select(Memory).where(Memory.id == mid))).scalar_one()
    return asyncio.run(_run())


def _promote_real(mid, importance=100.0, sub_type=None, mtype="event"):
    """真晋升口（夹具没桩它，这里直连，便于精确构造达标/不达标）。"""
    return asyncio.run(_core.maybe_promote_core(mid, importance, sub_type, mtype))


# ─────────────────── ① create 回执带来源链条 ───────────────────

def test_create回执带provenance_既有四键一字未动(env):
    m = _save("用户爱吃红烧肉", source="chat", sub_type="extracted", source_id=999,
              importance=2, speaker_type="user", speaker_id=1)
    assert m is not None
    creates = _receipts(env, "create")
    assert len(creates) == 1
    det = creates[0]["detail"]
    # 既有键与既有语义保持（回归钉：本批只加键）
    assert det["memory_type"] == "event"
    assert det["sub_type"] == "extracted"
    assert det["source"] == "chat"
    assert det["importance"] == pytest.approx(float(m.importance or 0))
    prov = det["provenance"]
    assert prov["route"] == "extracted"
    assert prov["actor"] == "user"
    assert prov["evidence"] == {"kind": "chat_message", "source_id": 999,
                                "derived_from_ids": [], "node_type": None}
    assert prov["gates"] == []
    assert prov["stored"] == {"source": "chat", "sub_type": "extracted",
                              "memory_type": "event", "epistemic_status": "FACT"}


def test_create回执_chat无子类型记model_marked(env):
    """【记忆：】标记路径与关键词规则同落点（source=chat 且 sub_type 空）⇒ 如实一个桶。"""
    _save("用户明天要去出差", source="chat", importance=2, speaker_type="user", speaker_id=1)
    prov = _receipts(env, "create")[0]["detail"]["provenance"]
    assert prov["route"] == "model_marked"
    assert prov["evidence"]["kind"] == "none"      # 没带 source_id：无证据锚点也如实写 none


def test_create回执_AI自述与系统事件按来源归类(env):
    _save("今晚有点累", source="diary", importance=2, speaker_type="character", speaker_id=101)
    _save("用户遗弃了宠物", source="pet", importance=1, skip_dedup=True,
          speaker_type="user", speaker_id=1)
    routes = [r["detail"]["provenance"]["route"] for r in _receipts(env, "create")]
    assert routes == ["self_narrative", "system_event"]


def test_打标条回执route为perception并记gates(env):
    _loop.AGENT_FLAGS["perception_source_tag"] = True
    from app.models.device import PhoneSnapshot
    from app.utils.timeutil import now_naive_utc

    async def _seed_snap():
        async with env["factory"]() as db:
            db.add(PhoneSnapshot(user_id=1, source="clipboard",
                                 content="开会迟到五分钟被主管说了一顿",
                                 created_at=now_naive_utc()))
            await db.commit()
    asyncio.run(_seed_snap())

    m = _save("他今天开会迟到五分钟被主管说了一顿", source="chat", importance=2)
    assert m is not None and m.source == PERCEPTION_SOURCE
    creates = _receipts(env, "create")
    prov = creates[0]["detail"]["provenance"]
    assert prov["route"] == "perception"
    assert prov["gates"] == ["perception_tagged"]          # 本轮真实生效的改写
    assert prov["stored"]["source"] == PERCEPTION_SOURCE
    assert prov["stored"]["sub_type"] == "clipboard"       # 命中的那条快照的通道
    assert prov["evidence"]["kind"] == "phone_snapshot"


def test_L4改名plan后route仍按改名前判定(env):
    """plan_renamed 只作为 gates 留痕，不得把 extracted 误判成别的生产者。"""
    _loop.AGENT_FLAGS["review_plan_validity_extract"] = True
    from datetime import datetime

    import app.memory.tense as _tense

    env["monkeypatch"].setattr(_tense, "classify_tense", lambda _m: "plan")
    env["monkeypatch"].setattr(_tense, "plan_valid_until",
                              lambda _m, _now: datetime(2026, 10, 1))
    m = _save("用户下周打算去杭州", source="chat", sub_type="extracted", importance=2)
    assert m is not None and m.sub_type == "plan"
    prov = _receipts(env, "create")[0]["detail"]["provenance"]
    assert prov["route"] == "extracted"
    assert prov["gates"] == ["plan_renamed"]
    assert prov["stored"]["sub_type"] == "plan"


def test_准入待核在回执里留gates与判定后归属(env):
    """闸门解析出的归属今天不落列（行里仍是调用方给的），回执顺带留一份。"""
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    m = _save("角色推断的一段内容", source="diary", importance=2,
              speaker_type="character", speaker_id=101)
    assert m is not None
    creates = _receipts(env, "create")
    prov = creates[0]["detail"]["provenance"]
    assert prov["actor"] == "character"
    assert prov["gates"] == ["admission_pending_review"]
    assert prov["stored"]["epistemic_status"] == "INFERRED"
    assert [r["action"] for r in _receipts(env, "downgrade")] == ["downgrade"]


def test_并入回执补incoming与into_既有kind与sim不动(env):
    """文本查重并条：新内容不留行 ⇒ 回执是它唯一的现场（补「并进来什么、并进了哪条」）。"""
    first = _save(_TEXT_HIT, source="chat", sub_type="extracted", importance=2)
    assert first is not None
    second = _save(_TEXT_NEAR, source="chat", sub_type="status", importance=3)
    assert second is not None and second.id == first.id     # 并进了同一条（旧行为未动）
    merges = _receipts(env, "merge")
    assert len(merges) == 1
    det = merges[0]["detail"]
    assert det["kind"] == "text_dedup"                      # 既有键与取值不变
    assert det["incoming"]["content_excerpt"] == _TEXT_NEAR
    assert det["incoming"]["sub_type"] == "status"
    assert det["incoming"]["memory_type"] == "event"
    assert det["into"] == {"memory_id": first.id, "source": "chat",
                           "sub_type": "extracted", "memory_type": "event"}
    assert det["provenance"]["route"] == "extracted"
    # 并入不产生新行
    assert len(_rows(env["factory"])) == 1


# ─────────────────── ② 晋升审计 ───────────────────

def test_晋升留痕_写入自动晋升一条promote且reason含依据(env):
    m = _save("用户是一名前端工程师", source="chat", importance=PROMOTABLE["importance"],
              sub_type=PROMOTABLE["sub_type"], memory_type=PROMOTABLE["memory_type"],
              speaker_type="user", speaker_id=1)
    assert m is not None
    row = _get(env["factory"], m.id)
    assert row.is_core is True                              # 判据未变：高价值类别 + pct>=100
    promotes = _receipts(env, "promote")
    assert len(promotes) == 1
    assert promotes[0]["memory_id"] == row.id
    _r = promotes[0]["reason"]
    assert _r.startswith("core promote (write)")
    assert "importance>=100" in _r and "category=identity" in _r
    assert "confirmed=0" in _r and "sub_type=job" in _r and "source=chat" in _r
    det = promotes[0]["detail"]
    assert det["trigger"] == "write"
    assert det["rule"] == "high_value_category&importance>=100"
    assert det["importance"] == pytest.approx(100.0)
    assert det["confirmation_count"] == 0
    assert det["core_category"] == "identity"
    assert det["cap_per_char"] == _core.CORE_MAX_PER_CHAR
    assert det["evicted_memory_id"] is None


def test_晋升留痕_未达标不留痕(env):
    m = _save("用户今天喝了美式", source="chat", importance=2)
    assert m is not None
    assert _get(env["factory"], m.id).is_core is False
    assert _receipts(env, "promote") == []
    assert len(_receipts(env, "create")) == 1               # 写入留痕不受影响


def test_晋升留痕_重要度加确认数那条路(env):
    mid = _add_row(env["factory"], importance=100.0, confirmation_count=2, source="chat")
    _promote_real(mid)
    p = _receipts(env, "promote")[0]
    assert p["detail"]["rule"] == "importance>=80&confirmed>=2"
    assert p["detail"]["confirmation_count"] == 2
    assert "importance=100 confirmed=2" in p["reason"]


def test_晋升留痕_已是核心不重复留痕(env):
    mid = _add_row(env["factory"], importance=100.0, confirmation_count=2, source="chat",
                   is_core=True, core_category="identity")
    _promote_real(mid)
    _promote_real(mid)
    assert _receipts(env, "promote") == []                   # 早退分支：不重复写


def test_晋升留痕_用户确认路径trigger为confirmation(env):
    mid = _add_row(env["factory"], importance=100.0, confirmation_count=1, source="chat",
                   memory_type="user_info", sub_type="job")
    asyncio.run(_core.confirm_memory(mid))
    row = _get(env["factory"], mid)
    assert row.is_core is True and row.confirmation_count == 2   # 既有判据未动
    p = _receipts(env, "promote")[0]
    assert p["detail"]["trigger"] == "confirmation"
    assert p["reason"].startswith("core promote (confirmation) via user_confirmation")
    assert "confirmed=2" in p["reason"]


def test_晋升留痕_确认未达阈值不留痕(env):
    mid = _add_row(env["factory"], importance=100.0, confirmation_count=0, source="chat")
    asyncio.run(_core.confirm_memory(mid))                    # +1 ⇒ 1 < 2 ⇒ 不晋升
    assert _get(env["factory"], mid).is_core is False
    assert _receipts(env, "promote") == []


def test_晋升留痕_被隔离条不晋升也不留痕(env):
    """禁令 1（判据未动）：未获认可的感知条不拿 is_core ⇒ 自然也不该有晋升审计。"""
    _loop.AGENT_FLAGS["perception_isolate"] = True
    mid = _add_row(env["factory"], importance=110.0, confirmation_count=3,
                   source=PERCEPTION_SOURCE, epistemic_status="INFERRED")
    _promote_real(mid)
    assert _get(env["factory"], mid).is_core is False
    assert _receipts(env, "promote") == []


def test_晋升留痕_上限挤占记evicted_memory_id(env):
    """挤位是晋升事件的一部分：随本次晋升一并留证，不再单开一类回执。"""
    ids = [_add_row(env["factory"], importance=float(60 + i), confirmation_count=0,
                    source="chat", is_core=True, core_category="identity")
           for i in range(_core.CORE_MAX_PER_CHAR)]
    lowest = ids[0]                                          # 最不重要的一条将被挤掉
    new_id = _add_row(env["factory"], importance=110.0, confirmation_count=2, source="chat")
    _promote_real(new_id)
    p = _receipts(env, "promote")[0]
    assert p["detail"]["evicted_memory_id"] == lowest
    assert _get(env["factory"], lowest).is_core is False
    assert _get(env["factory"], new_id).is_core is True


def test_晋升留痕发射异常不影响晋升(env):
    """留痕是旁路：发射口炸掉也必须照旧晋升（失败静默，本模块既有纪律）。"""
    def _boom(*_a, **_kw):
        raise RuntimeError("receipt emit blew up")
    env["monkeypatch"].setattr("app.memory.receipt.emit_memory_receipt", _boom)
    mid = _add_row(env["factory"], importance=100.0, confirmation_count=2, source="chat")
    _promote_real(mid)
    assert _get(env["factory"], mid).is_core is True


# ─────────────────── ③ 闸控：默认关＝零留痕、语义不变 ───────────────────

def _receipt_rows(factory):
    """直查 memory_write_receipts 表 —— 闸控结论只认「表里有几行」，不认 recorder。"""
    from app.models.memory import MemoryWriteReceipt

    async def _run():
        async with factory() as db:
            return (await db.execute(select(MemoryWriteReceipt).order_by(MemoryWriteReceipt.id))
                    ).scalars().all()
    return asyncio.run(_run())


def _raw_query(factory, sql):
    """跑一条原生 SQL（校验说明里写的只读口径确实可执行，不是写着好看）。"""
    from sqlalchemy import text

    async def _run():
        async with factory() as db:
            return (await db.execute(text(sql))).all()
    return asyncio.run(_run())


def _persist_spy(monkeypatch):
    """把 _persist_receipt 换成「记录调用即返回协程」的桩：只由真发射口调用，
    因此它的调用次数＝闸有没有开（flag 关时首行 return，连协程都不该建）。"""
    seen = []

    def _spy(character_id, memory_id, action, reason, detail):
        seen.append({"action": action, "memory_id": memory_id, "detail": detail})

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr("app.memory.receipt._persist_receipt", _spy)
    return seen


def test_回执flag默认关_真发射口零落库(env, monkeypatch):
    """夹具的 recorder 不看 flag（证不出闸），本例还原真发射口：写入 + 真晋升一路跑完 ⇒ 表里零行。"""
    assert _loop.AGENT_FLAGS.get("memory_write_receipt", False) is False
    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", env["real_emit"])
    called = _persist_spy(monkeypatch)

    m = _save("用户爱吃红烧肉", source="chat", sub_type="extracted", importance=2,
              speaker_type="user", speaker_id=1)
    assert m is not None
    mid = _add_row(env["factory"], importance=100.0, confirmation_count=2, source="chat")
    _promote_real(mid)

    assert _get(env["factory"], mid).is_core is True     # 判据未动：关时该晋升的照旧晋升
    assert called == []                                    # 首行 return：连回执协程都没建
    assert _receipt_rows(env["factory"]) == []             # 逐字节旧行为：表里一行都没有


def test_回执flag开_真落库且只读口径查得到(env, monkeypatch):
    """开闸走真路：真 emit → 真后台调度 → 真 `_persist_receipt` 落库，再用说明里的 SQL 读回。

    夹具把 `spawn_background` 桩成「关协程」（fire-and-forget 在用例里没法等），本例连同真发射口
    一起还原，并在同一轮事件循环里 `await_all` 等在途回执落库后再查表（生产不等待，语义不变）。
    """
    import app.db.database as dbmod
    import app.utils.async_tasks as at

    monkeypatch.setattr(dbmod, "async_session_factory", env["factory"])
    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", env["real_emit"])
    monkeypatch.setattr(at, "spawn_background", env["real_spawn"])
    _loop.AGENT_FLAGS["memory_write_receipt"] = True

    mid = _add_row(env["factory"], importance=100.0, confirmation_count=2, source="chat",
                   memory_type="user_info", sub_type="job")

    async def _run():
        await _write.save_memory(user_id=1, character_id=101, memory_type="event",
                                 content=_TEXT_HIT, importance=2, source="chat",
                                 sub_type="extracted", speaker_type="user", speaker_id=1,
                                 skip_dedup=True)
        await _core.maybe_promote_core(mid, 100.0, "job", "user_info")
        await at.await_all(timeout=5)

    asyncio.run(_run())

    rows = _receipt_rows(env["factory"])
    assert sorted(r.action for r in rows) == ["create", "promote"]
    # 只读口径①：来源链条占比（写入侧）
    routes = _raw_query(env["factory"],
                        "SELECT json_extract(detail_json, '$.provenance.route'), COUNT(*) "
                        "FROM memory_write_receipts WHERE action='create' GROUP BY 1")
    assert list(routes) == [("extracted", 1)]
    # 只读口径②：晋升依据分布（晋升侧）
    rules = _raw_query(env["factory"],
                       "SELECT json_extract(detail_json, '$.rule'), "
                       "json_extract(detail_json, '$.trigger') "
                       "FROM memory_write_receipts WHERE action='promote'")
    assert list(rules) == [("importance>=80&confirmed>=2", "write")]
    reason = _raw_query(env["factory"],
                        "SELECT reason FROM memory_write_receipts WHERE action='promote'")[0][0]
    assert "core promote (write) via importance>=80&confirmed>=2" in reason
    assert "importance=100 confirmed=2" in reason


def test_回执flag关时晋升判据与结果不变(env, monkeypatch):
    """关键回归：审计是旁路——关（默认）时该晋升的仍晋升、行取值与旧一致、表里零行。"""
    assert _loop.AGENT_FLAGS.get("memory_write_receipt", False) is False
    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", env["real_emit"])
    called = _persist_spy(monkeypatch)

    m = _save("用户住在杭州", source="chat", importance=PROMOTABLE["importance"],
              sub_type="location", memory_type="user_info", speaker_type="user", speaker_id=1)
    row = _get(env["factory"], m.id)
    assert row.is_core is True and row.core_category == "identity"
    assert row.source == "chat" and row.sub_type == "location"
    assert row.epistemic_status == "FACT"
    assert called == [] and _receipt_rows(env["factory"]) == []


def _add_row(factory, *, importance, confirmation_count=0, source="chat", memory_type="event", **kw):
    """直接落一行（绕过 save_memory），便于精确构造达标/已核心/上限场景。"""
    from app.models.memory import Memory
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=1, character_id=101, memory_type=memory_type,
                       content="晋升审计素材", source=source, importance=importance,
                       confirmation_count=confirmation_count, created_at=now_naive_utc(), **kw)
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())
