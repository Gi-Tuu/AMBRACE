# -*- coding: utf-8 -*-
"""Q1（2026-09-25）：现状 trace「现状事实」段被同一类近况占满 ⇒ 按谓词收敛。

守的底线（逐条对应 app/scheduling/state_trace.py 的 Q1 改动）：
1. ``select_fact_rows`` 是纯函数（零 DB / 零 LLM）；判重只用「同 predicate + 规范化后完全相同」，
   **不用相似度阈值**（实测同类近况两两相似度只有 0.40~0.64，阈值既抓不净也会误合并）；
2. 名额只对 curated 设限（实测重复全集中在 curated），上限表里没有的 predicate 不受限、各谓词独立计数；
3. 事实先「多取」再「收敛」，被跳过的条目不白占 prompt 名额，别的 predicate 得以补进来；
4. C7 的 status 保底判断必须在收敛**之后**（否则会出现「补上又被挤掉」的自我打架）；
5. 三分区顺序、slots/intents 两段、行截断与总长口径一律不变。

夹具照 tests/test_two_pass_trace.py（trace_db + _seed + _fact + _build，走 _dbclone 临时库，不碰生产库）。
**用例文本全部为脱敏合成样例**（甲/乙），不抄生产库私密原文——tests 目录会进公开仓快照。
"""
import asyncio
import os

import pytest

from _dbclone import clone_engine, make_session_factory

from app.scheduling import state_trace as st

pytestmark = pytest.mark.slow

_CHAR = 13
_USER = 1


# ────────────────────────── 临时库夹具（同 test_two_pass_trace.py） ──────────────────────────

@pytest.fixture()
def trace_db(monkeypatch, tmp_path):
    """临时库（模板库克隆）：patch async_session_factory，users 父行就位（FK 生产同款开启）。"""
    db_path = os.path.join(str(tmp_path), "trace.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=_USER, username="q1_u1", nickname="主人"))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    import app.memory.user_facts as _uf
    monkeypatch.setattr(_uf, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _seed(factory, *rows):
    async def _go():
        async with factory() as db:
            for r in rows:
                db.add(r)
            await db.commit()
    asyncio.run(_go())


def _fact(predicate, value, *, status="active", confidence=0.9,
          character_id=_CHAR, user_id=_USER):
    from app.models.memory import WorldFact
    return WorldFact(user_id=user_id, character_id=character_id, subject_type="character",
                     subject_id=character_id, predicate=predicate, object_value=value,
                     status=status, confidence=confidence)


def _build(factory, character_id=_CHAR, user_id=_USER):
    async def _go():
        async with factory() as db:
            return await st.build_state_trace(db, character_id=character_id, user_id=user_id)
    return asyncio.run(_go())


def _retime(factory, ordered_values):
    """updated_at 显式拉开（``ordered_values[0]`` 最新）。

    world_facts.updated_at 是 ``server_default=func.now()``，SQLite 下为 CURRENT_TIMESTAMP（秒级）——
    同批插入的行时间相同、desc 排序不稳定，故集成用例必须自己把顺序钉死。
    """
    async def _go():
        from datetime import timedelta
        from sqlalchemy import update
        from app.models.memory import WorldFact
        from app.utils.timeutil import now_naive_utc
        base = now_naive_utc()
        async with factory() as db:
            for i, val in enumerate(ordered_values):
                await db.execute(update(WorldFact)
                                 .where(WorldFact.object_value == val)
                                 .values(updated_at=base - timedelta(seconds=i)))
            await db.commit()
    asyncio.run(_go())


def _fact_lines(out: str) -> list[str]:
    """取渲染结果里「· 现状事实」段的行（到下一个分区头为止）。"""
    lines = out.splitlines()
    assert st._SEC_FACTS in lines, f"应含现状事实段：{out!r}"
    start = lines.index(st._SEC_FACTS) + 1
    end = next((i for i, ln in enumerate(lines[start:], start)
                if ln in (st._SEC_SLOTS, st._SEC_INTENTS)), len(lines))
    return lines[start:end]


def _row(predicate, value):
    """纯函数用例的假行（dict 形态：``_get`` 同时支持 ORM 行与 dict）。"""
    return {"predicate": predicate, "object_value": value}


# ────────────────────────── ① 纯函数：规范化判重 ──────────────────────────

def test_a_同谓词规范化后同值_只留最先出现的那条():
    rows = [_row("curated", "我是甲的伴侣"),
            _row("curated", "我是 甲 的伴侣。"),     # 去空白 + 去句号 ⇒ 与首条同值
            _row("curated", "我是甲的伴侣！")]        # 去感叹号 ⇒ 与首条同值
    out = st.select_fact_rows(rows, limit=8, max_per_predicate={"curated": 4})
    assert [r["object_value"] for r in out] == ["我是甲的伴侣"], "重复只留最先（＝最新）那条"

    # 规范化后不同值不算重复（不得把「伴侣」与「家人」合并）
    near = st.select_fact_rows([_row("curated", "我是甲的伴侣"),
                                _row("curated", "我是甲的家人")], limit=8)
    assert len(near) == 2, "判据是规范化后完全相同，不是相似度"


# ────────────────────────── ② 纯函数：同谓词名额上限 ──────────────────────────

def test_b_同谓词超过上限_只留输入顺序的前N条():
    rows = [_row("curated", f"近况甲{i}") for i in range(6)]
    out = st.select_fact_rows(rows, limit=10, max_per_predicate={"curated": 3})
    assert [r["object_value"] for r in out] == ["近况甲0", "近况甲1", "近况甲2"], \
        "超上限的后续同谓词行一律跳过，保留输入顺序（＝updated_at desc）的前 N 条"


# ────────────────────────── ③ 纯函数：各谓词独立 / 上限表外不受限 ──────────────────────────

def test_c_各谓词独立计数_上限表外的谓词不受限():
    rows = ([_row("curated", f"近况乙{i}") for i in range(3)]
            + [_row("status", f"状态乙{i}") for i in range(3)]
            + [_row("activity", f"正在做乙{i}") for i in range(5)])
    out = st.select_fact_rows(rows, limit=11, max_per_predicate={"curated": 2})
    assert [r["predicate"] for r in out] == ["curated"] * 2 + ["status"] * 3 + ["activity"] * 5, \
        "curated 被截断不得影响 status/activity 的各自计数"
    # 上限表里没有的谓词完全不受限
    assert len(st.select_fact_rows(rows, limit=11)) == 11
    # 判重键含 predicate：不同谓词同值不算重复
    assert len(st.select_fact_rows([_row("curated", "在护理甲"),
                                    _row("status", "在护理甲")], limit=8)) == 2


# ────────────────────────── ④ 纯函数：limit / 脏行 / 不改输入 ──────────────────────────

def test_d_limit截断与边界值():
    rows = [_row("curated", f"近况丙{i}") for i in range(12)]
    snapshot = [dict(r) for r in rows]
    assert [r["object_value"] for r in st.select_fact_rows(rows, limit=4)] == \
        ["近况丙0", "近况丙1", "近况丙2", "近况丙3"], "多取 12 条只回 limit=4 条"
    assert st.select_fact_rows(rows, limit=0) == [], "limit<=0 返回空列表"
    assert st.select_fact_rows(rows, limit=-1) == []
    assert st.select_fact_rows([], limit=5) == []
    assert st.select_fact_rows(None, limit=5) == []
    assert rows == snapshot and len(rows) == 12, "必须不改动（不 mutate）传入的 rows"


def test_d_脏行不抛异常且不额外挤占名额():
    rows = [_row(None, None), _row("", "   "), {"object_value": "无谓词但有值"},
            _row("curated", "近况丁1"), _row("curated", "近况丁2"), _row("status", "在护理乙")]
    out = st.select_fact_rows(rows, limit=6, max_per_predicate={"curated": 4})
    blanks = [r for r in out if not str(st._get(r, "object_value") or "").strip()]
    assert len(blanks) == 1, "空值脏行按「组键＝空串」收敛为一条，不批量挤掉真实现状"
    assert [r["object_value"] for r in out if r.get("object_value")] == \
        ["无谓词但有值", "近况丁1", "近况丁2", "在护理乙"], "脏行不得挤掉后面的正常行"


# ────────────────────────── ⑤ 集成：curated 收敛 + status 保底（e） ──────────────────────────

_E_CURATED = ("我是甲的伴侣",
              "我负责照顾甲的日常起居",
              "甲的腰伤需要每天护理",
              "我会陪甲一起做腰部康复",
              "甲习惯晚睡",
              "我记着甲不吃香菜",
              "甲最近工作比较忙",
              "甲周末想去公园走走")
_E_STATUS = "正在给甲热毛巾"


def test_e_集成_curated超名额被收敛且status保底仍在(trace_db):
    _seed(trace_db, *[_fact("curated", v) for v in _E_CURATED], _fact("status", _E_STATUS))
    _retime(trace_db, list(_E_CURATED) + [_E_STATUS])   # curated 全部更新，status 最旧

    facts = _fact_lines(_build(trace_db))
    curated = [ln for ln in facts if ln.startswith("- 近况：")]
    assert len(curated) <= st.TRACE_MAX_PER_PREDICATE["curated"], \
        f"curated 不得占满现状事实段：{facts}"
    assert curated == [f"- 近况：{v}" for v in _E_CURATED[:4]], f"应留最新 4 条：{facts}"
    assert f"- 状态：{_E_STATUS}" in facts, "收敛不得把 C7 保底的 status 挤掉"


def test_f_集成_规范化同值在trace里只出现一次(trace_db):
    _seed(trace_db,
          _fact("curated", "我是甲的伴侣。"),      # 最新
          _fact("curated", "我是 甲 的伴侣"),      # 去空白去句号后与上一条同值 ⇒ 重复
          _fact("curated", "甲习惯晚睡"))
    _retime(trace_db, ["我是甲的伴侣。", "我是 甲 的伴侣", "甲习惯晚睡"])

    facts = _fact_lines(_build(trace_db))
    assert len(facts) == 2, f"规范化同值必须只留一条：{facts}"
    assert facts[0] == "- 近况：我是甲的伴侣。", f"留的必须是最先出现（最新）那条：{facts}"
    assert sum(1 for ln in facts
               if st._norm_fact_text(ln.split("：", 1)[1]) == "我是甲的伴侣") == 1


def test_g_集成_先多取让更旧的其它谓词补进来(trace_db):
    """12 条 curated 占满 updated_at desc 的前 12 位，activity 更旧 ⇒ 只有「多取」才补得进来。"""
    curated = [f"近况戊{i}" for i in range(12)]
    _seed(trace_db, *[_fact("curated", v) for v in curated], _fact("activity", "在给甲量体温"))
    _retime(trace_db, curated + ["在给甲量体温"])

    facts = _fact_lines(_build(trace_db))
    assert len(facts) == 5, f"应为 4 条 curated + 1 条 activity：{facts}"
    assert "- 正在做：在给甲量体温" in facts, \
        f"被跳过的名额应由多取的一批补齐，而不是留下空名额：{facts}"
    assert [ln for ln in facts if ln.startswith("- 近况：")] == \
        [f"- 近况：{v}" for v in curated[:4]]


def test_h_集成_保底判断在收敛之后_status落在多取窗口外也置顶补回(trace_db):
    """多取窗口（24 条）被 curated 占满、status 更旧 ⇒ 收敛后仍无 status ⇒ C7 补查并置顶。"""
    curated = [f"近况己{i}" for i in range(st.TRACE_FACT_FETCH_MULTIPLIER * 8 + 1)]
    _seed(trace_db, *[_fact("curated", v) for v in curated], _fact("status", "在给甲揉腰"))
    _retime(trace_db, curated + ["在给甲揉腰"])

    facts = _fact_lines(_build(trace_db))
    assert facts[0] == "- 状态：在给甲揉腰", f"status 必须置顶补回（保底在收敛之后）：{facts}"
    assert len([ln for ln in facts if ln.startswith("- 近况：")]) == \
        st.TRACE_MAX_PER_PREDICATE["curated"]
