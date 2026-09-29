# -*- coding: utf-8 -*-
"""A4 批 1 / P4（2026-09-29，B9 十三拍干跑判读后拍板）：事实生命周期策略表**生效**。

本批范围钉死为「**只让 plan 的 TTL 落地，槽层与其它 kind 继续干跑**」，守的四条：
- 档位归一唯一真源＝`lifecycle_policy.gear_of`：**默认档＝关 ⇒ 上线零行为**；既有 bool 拨开＝干跑（向后兼容）；
  只有显式 `"apply_plan"` 才作用；**认不出的值一律回落干跑**（绝不擅自生效）。
- 失效动作复用既有落点 `maintain_plan_expiry.expire_stale_plans`（classify_tense + is_plan_expired +
  plan_valid_until，与策略表 `is_expired` 同源）⇒ **不新造数学、不新造第二套失效机制**。
- 动作面＝既有三字段（status→stale / valid_to→实际有效期 / next_review_at→None），**无物理删除**；
  语义＝「不再作为现状被取用」（现状面 current_facts_active_only 不取，检索/复习面保留 + rerank 降权）。
- 可回退：`fact_lifecycle_policy` 置回 `True`（干跑档）或 `False`（关）⇒ 逐字节恢复本批之前行为。

真库用例走 _dbclone 私有临时库（页级克隆），**不连生产库**；用例前后做全表逐列快照比对。
项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop
from app.flags.agent_flags import AGENT_FLAGS
from app.memory import lifecycle_policy as pol
from app.memory.maintenance_schedule import scan_lifecycle_policy

# 重量级/集成型用例（每例起一次临时库），与 test_plan_expiry 同档：全量照跑、日常 -m "not slow" 跳过
pytestmark = pytest.mark.slow

NOW = datetime(2026, 9, 29, 12, 0, 0)      # 库内 naive UTC
PLAN_TEXT = "用户明天交材料"                 # 线上真实语料形态（显式 sub_type='plan'）


# ────────────────────────── 档位归一（纯函数，无库） ──────────────────────────


def test_gear_of_falsy_values_are_off():
    """关档：False / None / 0 / 空串 ⇒ 连扫描都不跑（逐字节旧行为）。"""
    for v in (False, None, 0, ""):
        assert pol.gear_of(v) == pol.GEAR_OFF


def test_gear_of_true_is_dry_run_for_backward_compat():
    """既有 bool 拨开（线上 DB enabled=1 合并的结果）＝干跑档，**本批不把它升级成生效**。"""
    assert pol.gear_of(True) == pol.GEAR_DRY_RUN
    assert pol.gear_of(1) == pol.GEAR_DRY_RUN


def test_gear_of_apply_plan_tolerates_case_and_space():
    for v in ("apply_plan", " Apply_Plan ", "APPLY_PLAN"):
        assert pol.gear_of(v) == pol.GEAR_APPLY_PLAN
    assert pol.gear_of("dry_run") == pol.GEAR_DRY_RUN


def test_gear_of_unknown_values_fall_back_to_dry_run():
    """认不出的一律回落干跑（保守侧）：拼错、半成品档位、数字档都不得触发生效。"""
    for v in ("apply", "apply_all", "applyplan", "on", "true", "2", 2, 3.5, object(), ["apply_plan"]):
        assert pol.gear_of(v) == pol.GEAR_DRY_RUN, v


def test_current_gear_reads_agent_flags_and_defaults_off(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, pol.FLAG_KEY, "apply_plan")
    assert pol.current_gear() == pol.GEAR_APPLY_PLAN
    monkeypatch.setitem(AGENT_FLAGS, pol.FLAG_KEY, True)
    assert pol.current_gear() == pol.GEAR_DRY_RUN
    monkeypatch.delitem(AGENT_FLAGS, pol.FLAG_KEY)          # 键缺失 ⇒ 关（最保守）
    assert pol.current_gear() == pol.GEAR_OFF


def test_plan_apply_allowed_has_two_existing_channels_only(monkeypatch):
    """授权＝「L4 既有灰度」或「策略表生效档」任一；两把都是既有开关，没有第三把。"""
    monkeypatch.setitem(AGENT_FLAGS, pol.FLAG_KEY, False)
    monkeypatch.setitem(AGENT_FLAGS, pol.APPLY_FLAG_KEY, True)
    assert pol.plan_apply_allowed() is True                 # ① L4 日终维护通道
    monkeypatch.setitem(AGENT_FLAGS, pol.FLAG_KEY, "apply_plan")
    monkeypatch.setitem(AGENT_FLAGS, pol.APPLY_FLAG_KEY, False)
    assert pol.plan_apply_allowed() is True                 # ② P4 生效档
    monkeypatch.setitem(AGENT_FLAGS, pol.FLAG_KEY, True)    # 干跑档不放行
    assert pol.plan_apply_allowed() is False
    monkeypatch.setitem(AGENT_FLAGS, pol.FLAG_KEY, False)
    assert pol.plan_apply_allowed() is False


def test_no_second_policy_switch_is_registered():
    """守「不新造第二套」：生效档复用既有键，原 P3 占位名不得进开关表/目录。"""
    assert pol.APPLY_FLAG_KEY == "review_plan_expire_stale"
    for ghost in ("fact_lifecycle_policy_apply", "lifecycle_policy_apply"):
        assert ghost not in AGENT_FLAGS
        assert ghost not in loop.AGENT_FLAGS
    from app.application.flag_catalog import FLAG_CATALOG

    assert pol.FLAG_KEY in FLAG_CATALOG
    assert pol.APPLY_FLAG_KEY in FLAG_CATALOG


def test_deploy_default_is_apply_plan_with_documented_rollback():
    """2026-09-29 用户拍板「B9 进 P4」：代码默认值＝生效档 apply_plan，只让 plan 的 TTL 落地。

    回退＝把该行改回 True（干跑档）或 False（关档）；键变成 str 后启动加载器不再让旧 DB 行覆盖，
    所以「这一行的默认值」就是唯一档位开关。同时钉住三档语义与「认不出就只统计」的保守口径。
    """
    assert pol.gear_of(loop.AGENT_FLAGS[pol.FLAG_KEY]) == pol.GEAR_APPLY_PLAN
    assert pol.gear_of(False) == pol.GEAR_OFF
    assert pol.gear_of(True) == pol.GEAR_DRY_RUN
    assert pol.gear_of("typo-gear") == pol.GEAR_DRY_RUN


def test_catalog_documents_the_apply_gear():
    from app.application.flag_catalog import FLAG_CATALOG

    entry = FLAG_CATALOG[pol.FLAG_KEY]
    assert entry["visible"] is False
    assert "最高档" in entry["desc_zh"] and "过时" in entry["desc_zh"]
    assert "outdated" in entry["desc_en"]


def test_apply_observation_line_is_always_present():
    assert pol.apply_observation_line(pol.GEAR_DRY_RUN, 0) == "gear=dry_run plan_expired_applied=0"
    assert "gear=apply_plan" in pol.apply_observation_line(pol.GEAR_APPLY_PLAN, 2)


# ────────────────────────── 临时库夹具 ──────────────────────────


@pytest.fixture()
def p4_db(monkeypatch, tmp_path):
    """克隆库（user 1 + char 1）+ 把 maintain_plan_expiry 的会话/钟点与向量同步口换成替身。"""
    import app.memory.maintain_plan_expiry as mpe
    import app.memory.supersede as sup
    from app.models.character import AICharacter
    from app.models.user import User

    engine = clone_engine(os.path.join(str(tmp_path), "p4.db"))
    factory = make_session_factory(engine)

    async def _seed_parents():
        async with factory() as db:
            db.add(User(id=1, username="p4_u1", nickname="用户"))
            await db.commit()
            db.add(AICharacter(id=1, user_id=1, name="角色1", is_active=True))
            await db.commit()

    asyncio.run(_seed_parents())
    monkeypatch.setattr(mpe, "async_session_factory", factory)
    monkeypatch.setattr(mpe, "now_naive_utc", lambda: NOW)

    calls = {"vectors": [], "bm25": []}

    async def _fake_mark_vectors(ids, status_map):
        calls["vectors"].append((list(ids), dict(status_map)))

    async def _fake_bm25(cid):
        calls["bm25"].append(cid)

    monkeypatch.setattr(sup, "_mark_vectors", _fake_mark_vectors)
    monkeypatch.setattr(sup, "_bm25_invalidate_safe", _fake_bm25)
    yield factory, calls
    asyncio.run(engine.dispose())


def _set_gear(monkeypatch, observe, review):
    monkeypatch.setitem(AGENT_FLAGS, pol.FLAG_KEY, observe)
    monkeypatch.setitem(AGENT_FLAGS, pol.APPLY_FLAG_KEY, review)


def _seed(factory, **kw):
    """写一条记忆（默认 active、未归档、带复习轮转指针）。"""
    from app.models.memory import Memory

    base = dict(user_id=1, character_id=1, memory_type="event", sub_type=None,
                content="", status="active", scope="private", importance=60.0,
                created_at=NOW - timedelta(days=3), is_archived=False,
                next_review_at=NOW + timedelta(days=1))
    base.update(kw)

    async def _run():
        async with factory() as db:
            db.add(Memory(**base))
            await db.commit()

    asyncio.run(_run())
    return base["id"] if "id" in kw else None


def _seed_plan(factory, mid, valid_to):
    return _seed(factory, id=mid, memory_type="event", sub_type="plan",
                 content=PLAN_TEXT, valid_to=valid_to)


def _get(factory, mid):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return await db.get(Memory, mid)

    return asyncio.run(_run())


def _snapshot(factory):
    """memories 全表逐列快照（只比对会被动作碰到的列，用于「零行为」断言）。"""
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(Memory.id, Memory.status, Memory.valid_to, Memory.next_review_at,
                       Memory.is_archived, Memory.content, Memory.updated_at)
                .order_by(Memory.id)
            )).fetchall()
            return [tuple(r) for r in rows]

    return asyncio.run(_run())


# ────────────────────────── 关档 / 干跑档：零行为 ──────────────────────────


def test_gear_off_returns_none_and_touches_nothing(p4_db, monkeypatch):
    factory, calls = p4_db
    _set_gear(monkeypatch, False, True)          # 观测关、L4 灰度开 ⇒ 拍子仍不跑这段
    _seed_plan(factory, 901, NOW - timedelta(days=1))
    before = _snapshot(factory)
    assert asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW)) is None
    assert _snapshot(factory) == before
    assert not calls["vectors"]


def test_dry_run_gear_observes_but_never_acts(p4_db, monkeypatch):
    """干跑档（＝线上现状）：过期 plan 照统计、照打 INFO，但一行都不改。"""
    factory, calls = p4_db
    _set_gear(monkeypatch, True, False)
    mid = _seed_plan(factory, 902, NOW - timedelta(days=1))
    before = _snapshot(factory)
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["gear"] == pol.GEAR_DRY_RUN
    assert out["plan_expired_applied"] == 0
    assert out["expired"].get("plan") == 1
    assert _snapshot(factory) == before
    assert _get(factory, mid).status == "active"
    assert not calls["vectors"]


def test_review_flag_alone_does_not_make_the_tick_act(p4_db, monkeypatch):
    """L4 灰度只授权日终维护那条通道；策略表拍子在干跑档依然只统计（上线零变化）。"""
    factory, calls = p4_db
    _set_gear(monkeypatch, True, True)
    _seed_plan(factory, 903, NOW - timedelta(days=1))
    before = _snapshot(factory)
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["gear"] == pol.GEAR_DRY_RUN and out["plan_expired_applied"] == 0
    assert _snapshot(factory) == before
    assert not calls["vectors"]


# ────────────────────────── 生效档：只 plan 的 TTL 落地 ──────────────────────────


def test_apply_gear_stales_expired_plan_only(p4_db, monkeypatch):
    """生效档唯一动作：过期 plan 置 stale（现状面不再取用）+ 双通道同步，无物理删除。"""
    import app.memory.supersede as sup  # noqa: F401  （替身由夹具装配，这里只声明依赖存在）

    factory, calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)      # 只靠策略表生效档授权
    mid = _seed_plan(factory, 911, NOW - timedelta(days=1))
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["gear"] == pol.GEAR_APPLY_PLAN
    assert out["plan_expired_applied"] == 1
    row = _get(factory, mid)
    assert row.status == "stale"                     # 仍在库里（不是 archived、没被删）
    assert row.is_archived is False
    assert row.content == PLAN_TEXT
    assert row.next_review_at is None                # 停主动复习轮转（既有语义）
    assert calls["vectors"] and calls["bm25"]        # 向量 metadata 同步照走


def test_apply_gear_keeps_unexpired_plan(p4_db, monkeypatch):
    """未过期（有效期在未来）⇒ 不过滤、不动作。"""
    factory, calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    mid = _seed_plan(factory, 921, NOW + timedelta(days=1))
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["plan_expired_applied"] == 0
    assert _get(factory, mid).status == "active"
    assert not calls["vectors"]


def test_apply_boundary_valid_to_exactly_now_is_not_expired(p4_db, monkeypatch):
    """边界一侧：valid_to == now ⇒ 既有口径「严格晚于」才算过期 ⇒ 本拍不动。

    注：策略表 `is_expired` 用 `<=`（观测侧偏保守、会把它计进 expired），
    动作侧仍以既有 `is_plan_expired`（`now > vu`）为唯一权威 —— 本例把两侧差异钉住。
    """
    factory, calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    mid = _seed_plan(factory, 931, NOW)
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["expired"].get("plan") == 1           # 观测计为过期
    assert out["plan_expired_applied"] == 0          # 动作不放行（以既有口径为准）
    assert _get(factory, mid).status == "active"


def test_apply_boundary_one_second_past_is_expired(p4_db, monkeypatch):
    """边界另一侧：valid_to 刚过 1 秒 ⇒ 判过期并置 stale。"""
    factory, _calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    mid = _seed_plan(factory, 941, NOW - timedelta(seconds=1))
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["plan_expired_applied"] == 1
    assert _get(factory, mid).status == "stale"


def test_apply_writes_valid_to_from_existing_rule(p4_db, monkeypatch):
    """valid_to 由既有 plan_valid_until 落值（无显式 valid_to 时按创建时间 + 水平窗），不新造数学。"""
    factory, _calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    created = NOW - timedelta(days=30)
    _seed(factory, id=951, memory_type="event", sub_type="plan",
          content="用户近期将去长沙", created_at=created, valid_to=None)
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["plan_expired_applied"] == 1
    row = _get(factory, 951)
    assert row.status == "stale"
    assert row.valid_to is not None and row.valid_to > created and row.valid_to <= NOW


@pytest.mark.parametrize("kind,mtype,sub,is_core,content", [
    ("event", "event", None, False, "用户上周去了学校"),
    ("identity", "user_info", None, True, "用户叫小明"),
    ("insight", "insight", None, True, "用户觉得自己太急躁"),
    ("preference", "preference", None, False, "用户喜欢吃辣"),
    ("transient", "insight", "status", False, "用户状态更新：正在开会"),
])
def test_other_kinds_stay_dry_run_even_when_policy_says_expired(p4_db, monkeypatch, kind, mtype,
                                                                sub, is_core, content):
    """逐例：其它 kind 即使被策略表判为 expired，生效档也**只统计不动作**（范围限定）。"""
    factory, calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    _seed(factory, id=961, memory_type=mtype, sub_type=sub, is_core=is_core,
          content=content, created_at=NOW - timedelta(days=400),
          valid_to=NOW - timedelta(days=1))
    before = _snapshot(factory)
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["by_kind"].get(kind), "该条应被识别为 %s" % kind
    assert out["expired"].get(kind) == 1
    assert out["plan_expired_applied"] == 0
    assert _snapshot(factory) == before
    assert not calls["vectors"]


def test_slot_layer_stays_dry_run_under_apply_gear(p4_db, monkeypatch):
    """槽层保持干跑：user_facts 过期计数与旧值镜像候选照报，槽行与镜像记忆都不动。"""
    from app.models.user import GlobalUserFact

    factory, calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)

    async def _seed_fact():
        async with factory() as db:
            db.add(GlobalUserFact(user_id=1, slot="location", value="示例市",
                                  previous_value="示例城", source="chat", confidence=1.0,
                                  valid_to=NOW - timedelta(days=1)))
            await db.commit()

    asyncio.run(_seed_fact())
    _seed(factory, id=971, memory_type="user_info", content="用户住在示例城",
          created_at=NOW - timedelta(days=5), valid_to=NOW - timedelta(days=1))
    before = _snapshot(factory)

    async def _facts_snapshot():
        async with factory() as db:
            rows = (await db.execute(
                select(GlobalUserFact.id, GlobalUserFact.slot, GlobalUserFact.value,
                       GlobalUserFact.previous_value, GlobalUserFact.valid_to,
                       GlobalUserFact.updated_at)
                .order_by(GlobalUserFact.id)
            )).fetchall()
            return [tuple(r) for r in rows]

    facts_before = asyncio.run(_facts_snapshot())
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["slots"]["expired"] == 1                    # 只统计
    assert out["slots"]["stale_candidates"] == 1           # 候选照报
    assert "expired=1" in out["line"]
    assert _snapshot(factory) == before                    # 镜像记忆一条没动
    assert asyncio.run(_facts_snapshot()) == facts_before   # 槽行一条没动
    assert not calls["vectors"]


# ────────────────────────── 回退 / 异常路径 / 日志 ──────────────────────────


def test_rollback_to_dry_run_restores_previous_behaviour(p4_db, monkeypatch):
    """回退动作＝把键置回干跑档：同一份数据、同一个拍子，行为回到本批之前（逐列不变）。"""
    factory, calls = p4_db
    _seed_plan(factory, 981, NOW - timedelta(days=1))
    _set_gear(monkeypatch, "apply_plan", False)
    assert asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))["plan_expired_applied"] == 1
    assert _get(factory, 981).status == "stale"

    _seed_plan(factory, 982, NOW - timedelta(days=2))
    _set_gear(monkeypatch, True, False)                   # ← 置回干跑档
    out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out["gear"] == pol.GEAR_DRY_RUN and out["plan_expired_applied"] == 0
    assert _get(factory, 982).status == "active"          # 新过期条不再被作用
    assert len(calls["vectors"]) == 1                     # 回退后没有新增动作


def test_dry_run_info_line_still_logged_under_apply_gear(p4_db, monkeypatch, caplog):
    """干跑日志仍在打：INFO 关键字不变（13 拍判读靠它 grep），档位与动作量并进行里。"""
    factory, _calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    _seed_plan(factory, 991, NOW - timedelta(days=1))
    with caplog.at_level(logging.INFO, logger="memory.maintenance"):
        asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    lines = [r.getMessage() for r in caplog.records if "Lifecycle policy dry-run" in r.getMessage()]
    assert len(lines) == 1
    assert "sampled=1" in lines[0] and "gear=apply_plan" in lines[0]
    assert "plan_expired_applied=1" in lines[0] and "facts=0" in lines[0]


def test_apply_failure_does_not_block_observation_or_tick(p4_db, monkeypatch, caplog):
    """异常路径：失效动作炸了也只 WARNING —— 观测照返、日志照打、本拍维护不受影响。"""
    import app.memory.maintain_plan_expiry as mpe

    factory, _calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    _seed_plan(factory, 1001, NOW - timedelta(days=1))

    async def _boom(*_a, **_k):
        raise RuntimeError("expiry write failed")

    monkeypatch.setattr(mpe, "expire_stale_plans", _boom)
    with caplog.at_level(logging.INFO, logger="memory.maintenance"):
        out = asyncio.run(scan_lifecycle_policy(session_factory=factory, now=NOW))
    assert out is not None and out["sampled"] == 1
    assert out["plan_expired_applied"] == 0
    assert _get(factory, 1001).status == "active"
    assert any("plan-apply failed" in r.getMessage() for r in caplog.records)
    assert any("Lifecycle policy dry-run" in r.getMessage() for r in caplog.records)


def test_scan_query_failure_is_isolated_under_apply_gear(p4_db, monkeypatch, caplog):
    """观测查询失败 ⇒ 返回 None + WARNING，不抛到维护拍子外面（也不因档位而改写）。"""
    factory, calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    _seed_plan(factory, 1011, NOW - timedelta(days=1))
    before = _snapshot(factory)

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    with caplog.at_level(logging.WARNING, logger="memory.maintenance"):
        assert asyncio.run(scan_lifecycle_policy(session_factory=_boom, now=NOW)) is None
    assert any("Lifecycle policy scan failed" in r.getMessage() for r in caplog.records)
    assert _snapshot(factory) == before
    assert not calls["vectors"]


def test_expire_stale_plans_returns_zero_without_any_query(monkeypatch):
    """两把闸都关 ⇒ 首行即返回 0，一次 SELECT 都不发（零开销、零行为）。"""
    import app.memory.maintain_plan_expiry as mpe

    _set_gear(monkeypatch, False, False)
    calls = []

    def _spy(*_a, **_k):
        calls.append(1)
        raise RuntimeError("must not be reached")

    monkeypatch.setattr(mpe, "async_session_factory", _spy)
    assert asyncio.run(mpe.expire_stale_plans()) == 0
    assert not calls


def test_expire_stale_plans_authorized_by_gear_alone(p4_db, monkeypatch):
    """生效档单独授权：L4 灰度关着也能落地（本批的生效入口），动作口径与 L4 完全一致。"""
    import app.memory.maintain_plan_expiry as mpe

    factory, calls = p4_db
    _set_gear(monkeypatch, "apply_plan", False)
    mid = _seed_plan(factory, 1021, NOW - timedelta(days=1))
    assert asyncio.run(mpe.expire_stale_plans()) == 1
    assert _get(factory, mid).status == "stale"
    assert calls["vectors"]
