# -*- coding: utf-8 -*-
"""槽值规范化 L1（2026-09-19）——slot_guard.normalize_slot_value 回归用例。

交接口径：只做**形态归一**（去转述主语、剥结尾标点、砍连接词尾巴、按锚点挑分句），
放行与否仍由既有 slot_value_reject_reason 裁决；归一不出值返回 None，调用方（extractor）
原样传 val → 槽闸照旧拒写（fail-closed）。

最终规则（09-19 Codex 复核定稿）：
a) 空 → None；
b) 去转述主语前缀（最多两轮）+ 其后谓语（表示/说/觉得/认为/希望/透露）；
c) 剥结尾句末标点；
d) **叙述尾巴先砍**：以连接词起首的收尾分句（「…，因为…」「…，所以…」）整段丢掉（只砍尾部）；
e) **整串优先**：砍完若整串本身能过闸就直接用（避免「有课，时间赶」被裁成「有课」丢信息）；
f) 整串不过闸才取分句：**锚点命中数最多者胜、并列取最左**，跳过连接词起首的段与超长段；
g) 归一后仍 >30 字 → None（不硬截断词语）；结尾再 strip + 剥标点。

语义落槽（值该不该进这个槽）不是 L1 的职责：SLOT 由提取器给，闸只判形态。要更准走 L2（提示词直出短值）。
"""
import asyncio
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.db.database as _dbmod
from app.memory.extractor import _parse_slot_value_line, _slot_fact_from_response
from app.memory.slot_guard import (
    normalize_slot_value,
    slot_value_reject_reason,
    slot_value_write_ok,
)

pytestmark = pytest.mark.slow


# ── 必须能写进槽（L1 的正题：解冻） ────────────────────────────────────────────

_WRITABLE = (
    ("relationship", "用户与sam是伴侣关系，sam是用户的老公。", "与sam是伴侣关系"),
    ("relationship", "单身多年，最近脱单有了对象", "最近脱单有了对象"),   # 锚点密度：脱单+对象 > 单身
    ("living", "用户住在海珠区，室友是新疆人", "住在海珠区，室友是新疆人"),  # 整串优先
    ("health", "今天头疼、发烧、浑身没劲", "今天头疼、发烧、浑身没劲"),      # 整串优先（并置信息不丢）
    ("job", "有课，时间赶", "有课，时间赶"),                              # 整串优先
    ("job", "用户今晚有课，时间赶", "今晚有课，时间赶"),
    ("goal_state", "用户准备比赛作品，因为他赢了比赛", "准备比赛作品"),      # 叙述尾巴被砍
    ("health", "用户今天没去上课，因为生病了", "今天没去上课"),              # 叙述尾巴被砍
)


@pytest.mark.parametrize("slot,raw,expected", _WRITABLE)
def test_normalize_produces_writable_slot_value(slot, raw, expected):
    got = normalize_slot_value(slot, raw)
    assert got == expected
    assert slot_value_write_ok(slot, got) is True, got


# ── 短值/无锚点等：不越权、不硬截断 ────────────────────────────────────────────

_UNTOUCHED = (("job", "大二在读"), ("health", "吃药了"), ("living", "住学校宿舍"))


@pytest.mark.parametrize("slot,raw", _UNTOUCHED)
def test_short_values_untouched(slot, raw):
    assert normalize_slot_value(slot, raw) == raw


_RETURN_NONE = (
    ("health", ""),
    ("health", "   "),
    ("goal_state", "……"),
    # 无锚点长句（>30 字且每段都剪不动）→ None（规则 g：不硬截断词语）
    ("job", "用户今天在图书馆看了很久的书，安静地坐了一整个下午，直到天黑了才回去"),
)


@pytest.mark.parametrize("slot,raw", _RETURN_NONE)
def test_returns_none_for_unnormalizable(slot, raw):
    assert normalize_slot_value(slot, raw) is None


# ── 形态安全：结果永远不含连接词尾巴，且要么 None 要么由闸定夺 ──────────────────

_NARRATION = (
    ("goal_state", "用户今天心情很好，因为他赢了比赛"),
    ("health", "用户今天没去上课，因为生病了"),
    ("goal_state", "用户准备比赛作品，因为他赢了比赛"),
)


@pytest.mark.parametrize("slot,raw", _NARRATION)
def test_connective_tail_never_survives(slot, raw):
    got = normalize_slot_value(slot, raw)
    if got is None:
        return
    assert not got.startswith(("因为", "所以", "然后", "而且", "但是", "不过", "可是"))
    assert "，因为" not in got and "，所以" not in got
    # 形态安全：归一结果要么由闸放行、要么由闸拒绝——L1 不替闸做决定
    assert slot_value_reject_reason(slot, got) is None or slot_value_write_ok(slot, got) is False


# ── 旗舰样本（交接点名）与幂等 ────────────────────────────────────────────────

def test_flagship_relationship_case_end_to_end():
    raw = "用户与sam是伴侣关系，sam是用户的老公。"
    assert slot_value_write_ok("relationship", raw) is False          # 原样进槽照旧被拒
    norm = normalize_slot_value("relationship", raw)
    assert norm == "与sam是伴侣关系"
    assert slot_value_write_ok("relationship", norm) is True          # 规范化后可写


def test_normalize_is_idempotent_and_never_returns_blank():
    cases = [(s, r) for s, r, _ in _WRITABLE] + list(_UNTOUCHED) + list(_NARRATION) + list(_RETURN_NONE)
    for slot, raw in cases:
        got = normalize_slot_value(slot, raw)
        if got is None:
            continue
        assert got and got == got.strip()
        assert normalize_slot_value(slot, got) == got


# ── L2（2026-09-21）提示词直出 SLOT_VALUE 短语短值 ────────────────────────────────

def test_parse_slot_value_line_prefers_short_value():
    resp = (
        "USER_INFO: 用户最近在备考考研，每天刷题到半夜 | 4\n"
        "SLOT: goal_state\n"
        "SLOT_VALUE: goal_state|备考考研\n"
    )
    assert _parse_slot_value_line(resp) == ("goal_state", "备考考研")


def test_parse_slot_value_line_handles_garbage_backward_compat():
    # 缺 SLOT_VALUE 行 → None（调用方回落旧 SLOT 解析，向后兼容旧格式）
    assert _parse_slot_value_line("USER_INFO: 用户最近在备考考研\nSLOT: goal_state") is None
    # 无 `|` 分隔 → None
    assert _parse_slot_value_line("SLOT_VALUE: goal_state") is None
    # 空值 → None
    assert _parse_slot_value_line("SLOT_VALUE: 无") is None


def test_slot_fact_from_response_prefers_slot_value_short():
    """L2：解析端优先取 SLOT_VALUE 直出短值，而不是把 USER_INFO 整句当槽值。"""
    resp = (
        "USER_INFO: 用户最近在备考考研，每天刷题到半夜，特别辛苦 | 4\n"
        "SLOT: goal_state\n"
        "SLOT_VALUE: goal_state|备考考研\n"
    )
    user_info_val = "用户最近在备考考研，每天刷题到半夜，特别辛苦"
    # 旧解析会把整句规整（L1），但 L2 直出短值优先 → 拿到「备考考研」
    cand = _slot_fact_from_response(resp, user_info_val, ["goal_state"])
    assert cand == ("goal_state", "备考考研")


def test_slot_fact_from_response_falls_back_to_l1_when_no_slot_value():
    """缺 SLOT_VALUE 行 → 回落旧 SLOT 行 + L1 规整（向后兼容旧格式）。"""
    resp = (
        "USER_INFO: 用户今晚有课，时间赶 | 3\n"
        "SLOT: job\n"
    )
    cand = _slot_fact_from_response(resp, "用户今晚有课，时间赶", ["job"])
    assert cand == ("job", "今晚有课，时间赶")  # L1 规整结果


def test_slot_fact_from_response_unknown_slot_returns_none():
    # SLOT_VALUE 槽名不在启用集合 → 回落旧 SLOT 也查不到 → None（不污染未启用槽）
    resp = "USER_INFO: 用户最近在备考考研 | 4\nSLOT_VALUE: goal_state|备考考研\n"
    assert _slot_fact_from_response(resp, "用户最近在备考考研", ["job"]) is None


@pytest.fixture()
def l2_db(monkeypatch, tmp_path):
    """临时库（模板库克隆）：把 database / extractor / user_facts / cross_char_sync 工厂指向临时工厂；
    安全默认——不触碰生产库；save_memory 打桩为 noop（本档只验证 user_facts 短值落库）。
    """
    engine = clone_engine(os.path.join(str(tmp_path), "l2.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="l2u", nickname="L2"))
            await db.commit()
            db.add(AICharacter(id=1, user_id=1, name="L2角"))
            await db.commit()

    asyncio.run(_seed())
    import app.memory.extractor as ex
    import app.memory.user_facts as uf
    import app.memory.cross_char_sync as ccs
    monkeypatch.setattr(_dbmod, "async_session_factory", factory)
    monkeypatch.setattr(ex, "async_session_factory", factory)
    monkeypatch.setattr(uf, "async_session_factory", factory)
    monkeypatch.setattr(ccs, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _enabled_slots_stub(monkeypatch, slots):
    """把「按账号启用槽」打桩为固定列表，规避 flag_service/AGENT_FLAGS 触碰生产库。"""
    import app.memory.user_facts as uf
    async def _stub(user_id=None):
        return list(slots)
    monkeypatch.setattr(uf, "enabled_user_fact_slots_for", _stub)


def _read_user_fact(factory, slot):
    from app.models.user import GlobalUserFact
    async def _run():
        async with factory() as db:
            row = (await db.execute(
                select(GlobalUserFact).where(
                    GlobalUserFact.user_id == 1, GlobalUserFact.slot == slot
                )
            )).scalars().one_or_none()
            return row.value if row else None
    return asyncio.run(_run())


def test_extract_single_writes_short_slot_value(l2_db, monkeypatch):
    """端到端：LLM 直出 SLOT_VALUE 短值 → user_facts 落的是短值（非 USER_INFO 整句）。"""
    from app.memory.extractor import extract_single
    import app.memory as memory_mod
    factory = l2_db
    _enabled_slots_stub(monkeypatch, ["goal_state", "job", "health", "relationship", "living", "location"])
    async def _noop_save(**k):
        return None
    monkeypatch.setattr(memory_mod, "save_memory", _noop_save)  # 只验证 user_facts

    async def _fake_llm(**kw):
        return (
            "USER_INFO: 用户最近在备考考研，每天刷题到半夜，特别辛苦 | 4\n"
            "EVENTS: 无 | 1\nPREFERENCES: 无 | 1\nBIO: 无 | 1\nSTATUS: 无 | 1\n"
            "RELATIONSHIP: 无 | 1\nSTAGE: 无 | 1\n"
            "SLOT: goal_state\nSLOT_VALUE: goal_state|备考考研\n"
        )
    monkeypatch.setattr("app.memory.extractor.llm_call", _fake_llm)

    asyncio.run(extract_single(1, 1, 1, "我最近在备考考研", "加油", source_id=None))

    assert _read_user_fact(factory, "goal_state") == "备考考研"


def test_extract_single_full_sentence_slot_value_rejected_by_gate(l2_db, monkeypatch):
    """红线条款：SLOT_VALUE 若是整句，仍过写侧闸被拒（不写入），不破闸。"""
    from app.memory.extractor import extract_single
    import app.memory as memory_mod
    factory = l2_db
    _enabled_slots_stub(monkeypatch, ["goal_state", "job", "health", "relationship", "living", "location"])
    async def _noop_save(**k):
        return None
    monkeypatch.setattr(memory_mod, "save_memory", _noop_save)

    async def _fake_llm(**kw):
        return (
            "USER_INFO: 用户最近在备考考研 | 4\n"
            "EVENTS: 无 | 1\nPREFERENCES: 无 | 1\nBIO: 无 | 1\nSTATUS: 无 | 1\n"
            "RELATIONSHIP: 无 | 1\nSTAGE: 无 | 1\n"
            "SLOT: goal_state\n"
            "SLOT_VALUE: goal_state|用户最近在备考考研，每天刷题到半夜，特别辛苦，根本没时间休息\n"
        )
    monkeypatch.setattr("app.memory.extractor.llm_call", _fake_llm)

    asyncio.run(extract_single(1, 1, 1, "我最近在备考考研", "加油", source_id=None))

    # 整句被槽闸拒写 → 该槽无行落库（闸不变、fail-closed）
    assert _read_user_fact(factory, "goal_state") is None

