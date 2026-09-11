# -*- coding: utf-8 -*-
"""M3-b 工作记忆注入灰度（2026-09-07）：纯函数 + section 端到端（临时库）。

覆盖：
- render_working_state：三桶按优先级渲染一行式短句；空/非法输入返回空串；每桶上限 3；
- traffic_hit：同 key 恒定（确定性分桶）、ratio<=0 恒 False / >=1 恒 True、命中率≈ratio；
- inject_allowed：仅灰度白名单角色（char13）命中比例桶才注入；其余角色恒 False；
  （2026-09-11 扩量：白名单内比例 15%→100%，故 char13 恒命中；门控逻辑仍保留「白名单 + 分桶」两段）
  全量开关 working_state_inject 开=任意角色注入；
- section 端到端：有行+灰度命中→注入【工作记忆】块；无行→空；非灰度角色→空（零行为）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import json
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.context import section_working_state as sws


# ────── render_working_state（纯函数）──────────────────────────────────────


def test_render_three_buckets_in_priority_order():
    state = {
        "version": 1,
        "ongoing": [{"topic": "准备考试", "detail": "下周", "evidence_ids": [1]}],
        "relationship_notes": [{"note": "最近有点疏远", "evidence_ids": [2]}],
        "open_questions": [{"question": "周末去不去", "evidence_ids": [3]}],
    }
    text = sws.render_working_state(state)
    lines = text.split("\n")
    assert lines[0] == "- 正在进行：准备考试（下周）"
    assert lines[1] == "- 悬而未决：周末去不去"
    assert lines[2] == "- 近期关系：最近有点疏远"


def test_render_empty_or_invalid_returns_empty():
    assert sws.render_working_state(None) == ""
    assert sws.render_working_state({}) == ""
    assert sws.render_working_state("not a dict") == ""
    assert sws.render_working_state({"ongoing": "oops"}) == ""


def test_render_bucket_capped_at_three():
    state = {"ongoing": [{"topic": f"t{i}", "evidence_ids": [1]} for i in range(6)]}
    text = sws.render_working_state(state)
    assert len(text.split("\n")) == 3


def test_render_skips_entry_without_identity():
    state = {"ongoing": [{"detail": "没有身份键", "evidence_ids": [1]}]}
    assert sws.render_working_state(state) == ""


# ────── traffic_hit（确定性小流量分桶）─────────────────────────────────────


def test_traffic_hit_is_deterministic_for_same_key():
    assert sws.traffic_hit("13:7") == sws.traffic_hit("13:7")
    assert sws.traffic_hit("13:7", 0.15) == sws.traffic_hit("13:7", 0.15)
    # 与模块默认比例解耦：显式 15% 口径的自洽性由本用例保证


def test_traffic_hit_boundaries():
    assert sws.traffic_hit("13:7", 0) is False
    assert sws.traffic_hit("13:7", -1) is False
    assert sws.traffic_hit("13:7", 1) is True


def test_traffic_hit_ratio_roughly_holds():
    hits = sum(1 for i in range(2000) if sws.traffic_hit(f"13:{i}", 0.15))
    ratio = hits / 2000
    assert 0.10 <= ratio <= 0.20  # 显式按 15% 口径：2000 样本下应落在该区间


# ────── inject_allowed（灰度门控）─────────────────────────────────────────


def _session_hit(cid: int = 13) -> int:
    for s in range(1, 5000):
        if sws.traffic_hit(f"{cid}:{s}"):
            return s
    raise AssertionError("15% 比例下应存在命中会话")


def _session_miss(cid: int = 13) -> int:
    """按 15% 口径找一个未命中会话（模块默认比例 2026-09-11 起为 1.0，故必须显式传入）。"""
    for s in range(1, 5000):
        if not sws.traffic_hit(f"{cid}:{s}", 0.15):
            return s
    raise AssertionError("15% 口径下应存在未命中会话")


def test_inject_allowed_gray_char_hit():
    assert sws.inject_allowed(13, _session_hit())


def test_inject_allowed_gray_char_miss(monkeypatch):
    """分桶未命中 → 不注入（用固定 miss 复核门控，与当前比例常量解耦）。"""
    monkeypatch.setattr(sws, "traffic_hit", lambda key, ratio=0.15: False)
    assert not sws.inject_allowed(13, 7)
    assert _session_miss() > 0  # 15% 口径下确实存在未命中会话


def test_inject_allowed_other_chars_never():
    """其余角色（含活跃角色 101/6）恒不注入——零行为变化。"""
    for cid in (1, 6, 11, 101, 999):
        assert not sws.inject_allowed(cid, _session_hit(13))
        assert not sws.inject_allowed(cid, 9999)


def test_inject_allowed_full_flag_on(monkeypatch):
    """全量开关开 → 任意角色均注入（后续扩量/热回滚用）。"""
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "working_state_inject", True)
    assert sws.inject_allowed(101, 1)
    assert sws.inject_allowed(6, 1)


def test_inject_allowed_none_char():
    assert not sws.inject_allowed(None, 1)


# ────── section 端到端（临时库）───────────────────────────────────────────


@pytest.fixture()
def ws_inject_db(monkeypatch, tmp_path):
    tmp = str(tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=13, user_id=1, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _seed_working_state(factory, character_id: int, content: dict):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            db.add(Memory(
                user_id=1, character_id=character_id, memory_type="working_state",
                content=json.dumps(content, ensure_ascii=False), scope="private",
                source="system", importance=60.0,
            ))
            await db.commit()
    asyncio.run(_run())


def test_section_injects_for_gray_char_hit(ws_inject_db):
    """灰度角色 + 命中会话 + 有行 → 注入【工作记忆】块。"""
    _seed_working_state(ws_inject_db, 13, {
        "version": 1,
        "ongoing": [{"topic": "准备考试", "detail": "下周", "evidence_ids": [1]}],
        "open_questions": [], "relationship_notes": [],
    })
    state = {"character_id": 13, "user_id": 1, "session_id": _session_hit()}
    out = asyncio.run(sws.working_state_section(state, {}))
    assert len(out) == 1
    assert "【工作记忆" in out[0]
    assert "正在进行：准备考试（下周）" in out[0]


def test_section_no_row_no_inject(ws_inject_db):
    """有灰度命中但无 working_state 行 → 不输出空标记。"""
    state = {"character_id": 13, "user_id": 1, "session_id": _session_hit()}
    assert asyncio.run(sws.working_state_section(state, {})) == []


def test_section_other_character_zero_behavior(ws_inject_db):
    """非灰度角色：即使库里有行也绝不注入（零行为变化）。"""
    _seed_working_state(ws_inject_db, 101, {
        "version": 1,
        "ongoing": [{"topic": "准备考试", "evidence_ids": [1]}],
        "open_questions": [], "relationship_notes": [],
    })
    state = {"character_id": 101, "user_id": 1, "session_id": _session_hit()}
    assert asyncio.run(sws.working_state_section(state, {})) == []


def test_section_rendered_block_within_quota(ws_inject_db):
    """注入块受 300 token 正配额约束（2 字符≈1 token）。"""
    long_detail = "很长的细节" * 100
    _seed_working_state(ws_inject_db, 13, {
        "version": 1,
        "ongoing": [{"topic": f"话题{i}", "detail": long_detail, "evidence_ids": [1]} for i in range(3)],
        "open_questions": [], "relationship_notes": [],
    })
    state = {"character_id": 13, "user_id": 1, "session_id": _session_hit()}
    out = asyncio.run(sws.working_state_section(state, {}))
    assert len(out) == 1
    assert len(out[0]) <= 300 * 2
