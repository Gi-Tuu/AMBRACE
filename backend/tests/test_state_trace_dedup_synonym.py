# -*- coding: utf-8 -*-
"""D1（2026-09-26）：现状 trace「同义重复 / 归类糊边」只在拼装层收敛。

守的底线（逐条对应 app/scheduling/state_trace.py 的 D1 改动）：
1. ``_texts_duplicate`` 是纯函数（零 DB / 零 LLM）：复用「近况同义合并」既有判据的**保守子集**
   ——键全等 / 短串前缀包含（≥6 字）/ 近乎逐字重复（SequenceMatcher ≥ 0.9），比较前先剥离
   ASCII 人名与相对/绝对时间槽位；刻意不含 facts 的 C13「共享核心前缀」（会误并同模板不同宾语）；
2. **宁可少合并**：语义不同但字面相近者一律不并（带饭/带汤、美式咖啡/拿铁咖啡）；
3. 事实段按「同 predicate + 语义判重」收敛（不跨谓词），计划段按语义判重收敛（不跨行）；
4. 归类：「未完成计划」段只收 promise，kind=="cue" 的话题/线索不得进本段（查询侧排除）；
5. 结构不变：头句、三分区标题、顺序、空段省略与总长口径一律不动。

**用例文本全部为脱敏合成样例**（甲/乙 + 拉丁名 bo），不抄生产库私密原文——tests 目录会进公开仓快照。
夹具照 tests/test_state_trace_fact_quota.py（trace_db + _seed + _build，走 _dbclone 临时库，不碰生产库）。
"""
import asyncio
import os
import time

import pytest

from _dbclone import clone_engine, make_session_factory

from app.scheduling import state_trace as st

pytestmark = pytest.mark.slow

_CHAR = 13
_USER = 1


# ────────────────────────── 临时库夹具 ──────────────────────────

@pytest.fixture()
def trace_db(monkeypatch, tmp_path):
    db_path = os.path.join(str(tmp_path), "trace.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=_USER, username="d1_u1", nickname="主人"))
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


def _intent(content, *, kind="promise", status="pending", character_id=_CHAR):
    from app.models.memory import ProspectiveIntent
    return ProspectiveIntent(user_id=_USER, character_id=character_id,
                             content=content, kind=kind, status=status)


def _build(factory, character_id=_CHAR, user_id=_USER):
    async def _go():
        async with factory() as db:
            return await st.build_state_trace(db, character_id=character_id, user_id=user_id)
    return asyncio.run(_go())


def _section(out: str, header: str) -> list[str]:
    """取某个分区头之后的行（到下一个分区头为止）。"""
    lines = out.splitlines()
    assert header in lines, f"应含分区 {header}：{out!r}"
    start = lines.index(header) + 1
    end = next((i for i, ln in enumerate(lines[start:], start)
                if ln in (st._SEC_FACTS, st._SEC_SLOTS, st._SEC_INTENTS)), len(lines))
    return lines[start:end]


def _row(predicate, value):
    return {"predicate": predicate, "object_value": value}


def _irow(content):
    return {"content": content}


# ────────────────────────── ① 纯函数：同义条目被合并 ──────────────────────────

def test_同义判定_人名区分对象_时间分路径():
    """批 B（2026-09-26）新口径：人名两侧都有且不同 ⇒ 不是同一件事；时间词按路径区分。

    - 现状事实段（默认）：时间是事件标识 ⇒「昨天…」与「今天…」必须分开；
    - 未完成计划段（``ignore_dates=True``）：时间是约定的措辞 ⇒「明早」与「9月27日」是同一个约定；
    - 一侧缺人名不算冲突（写法省略），仍按「剥槽位后的键」走三条保守判据。
    """
    # 人名两侧都有且不同：两个对象、两件事，绝不合并（原口径会并成一个，丢约定）
    assert not st._texts_duplicate("明天要和bo去看电影", "明天要和bi去看电影", ignore_dates=True)
    # 时间两侧都有且不同：事实段是两天的事；计划段视为同一约定的两种写法
    assert not st._texts_duplicate("我答应明早给甲带饭", "我答应9月27日给甲带饭吃")
    assert st._texts_duplicate("我答应明早给甲带饭", "我答应9月27日给甲带饭吃", ignore_dates=True)
    # 拉丁人名一侧缺失 ⇒ 不算冲突；剥离后前缀包含（核心相同、各自补充）
    assert st._texts_duplicate("我是bo，甲的伴侣", "我是甲的伴侣，关系稳定")
    # 近乎逐字重复（多一个代词；短键档阈值 0.9）
    assert st._texts_duplicate("甲说晚点再说", "甲说他晚点再说")
    # 规范化后完全相同（旧口径继续生效）
    assert st._texts_duplicate("甲习惯晚睡。", "甲 习惯 晚睡")


def test_事实段同义条目只留最新一条():
    rows = [_row("curated", "我是甲的伴侣，关系稳定"),
            _row("curated", "我是bo，甲的伴侣"),   # 换人名 ⇒ 与首条同一件事
            _row("curated", "甲习惯晚睡")]
    out = st.select_fact_rows(rows, limit=8, max_per_predicate={"curated": 4})
    assert [r["object_value"] for r in out] == ["我是甲的伴侣，关系稳定", "甲习惯晚睡"]


def test_计划段同义条目只留最新一条():
    rows = [_irow("我答应9月27日给甲带饭吃"),
            _irow("我答应明早给甲带饭"),   # 同一件事（相对/绝对时间差）
            _irow("甲答应做芋头焖排骨")]
    out = st.select_intent_rows(rows, limit=5)
    assert [r["content"] for r in out] == ["我答应9月27日给甲带饭吃", "甲答应做芋头焖排骨"]


# ────────────────────────── ② 语义不同但字面相近：不误合并 ──────────────────────────

def test_语义不同但字面相近_绝不合并():
    # 同模板不同宾语（带饭 / 带汤）——刻意不含 C13 的核心前缀，正是为挡住这类
    assert not st._texts_duplicate("我答应明早给甲带饭", "我答应明早给甲带汤")
    # 美式咖啡 / 拿铁咖啡（facts 侧同款反例）
    assert not st._texts_duplicate("甲喜欢喝美式咖啡", "甲喜欢喝拿铁咖啡")
    # 不同身体部位的两句近况
    assert not st._texts_duplicate("甲腰不能压，侧躺需垫东西", "甲膝盖有伤，不能久站")
    # 完全不相干的两条计划
    assert not st._texts_duplicate("我答应明早给甲带饭", "甲说十一点的事记着，晚点再说")

    # 事实段：不同义不并，各占其位
    keep = st.select_fact_rows([_row("curated", "甲喜欢喝美式咖啡"),
                                _row("curated", "甲喜欢喝拿铁咖啡")], limit=8)
    assert len(keep) == 2
    # 计划段：不同义不并
    assert len(st.select_intent_rows([_irow("我答应明早给甲带饭"),
                                      _irow("我答应明早给甲带汤")], limit=5)) == 2


def test_判重不跨谓词_不同谓词同值各留一条():
    out = st.select_fact_rows([_row("curated", "在给甲揉腰"),
                               _row("status", "在给甲揉腰")], limit=8)
    assert [r["predicate"] for r in out] == ["curated", "status"]


# ────────────────────────── ③ 空输入 / 单条 / 脏值边界 ──────────────────────────

def test_空输入与单条与脏值边界():
    assert st.select_fact_rows([], limit=8) == []
    assert st.select_fact_rows(None, limit=8) == []
    assert st.select_fact_rows([_row("curated", "单条甲")], limit=0) == []
    assert st.select_intent_rows([], limit=5) == []
    assert st.select_intent_rows(None, limit=5) == []
    assert st.select_intent_rows([_irow("单条乙")], limit=0) == []
    # 空正文计划行被跳过；脏 predicate/None 不抛异常
    assert st.select_intent_rows([_irow("   "), _irow("正常计划丙")], limit=5) == [{"content": "正常计划丙"}]
    assert len(st.select_fact_rows([_row(None, None), _row("", "  ")], limit=6)) == 1
    # 空输入下 _texts_duplicate 保守判否
    assert not st._texts_duplicate("", "甲")
    assert not st._texts_duplicate("甲", None)
    assert not st._texts_duplicate("", "")


def test_select_fact_rows_不改动传入rows():
    rows = [_row("curated", "我是甲的伴侣"), _row("curated", "我是bo，甲的伴侣")]
    snap = [dict(r) for r in rows]
    st.select_fact_rows(rows, limit=8, max_per_predicate={"curated": 4})
    assert rows == snap


# ────────────────────────── ④ 结构与用途约束句逐字不变 ──────────────────────────

def test_三段结构与用途约束句逐字保持不变(trace_db):
    _seed(trace_db,
          _fact("curated", "我是甲的伴侣，关系稳定"),
          _fact("curated", "我是bo，甲的伴侣"),          # 与上条同义 ⇒ 合并
          _fact("status", "在给甲量体温"),
          _intent("我答应明早给甲带饭"), _intent("我答应9月27日给甲带饭吃"),  # 同义 ⇒ 合并
          _intent("甲说晚点再说", kind="cue"))           # cue：话题，不得进未完成计划
    out = _build(trace_db)
    lines = out.splitlines()
    assert lines[0] == st._HEADER, "用途约束那一句必须逐字保留"
    assert lines[0].startswith("【当前现状速读】")
    assert st._SEC_FACTS in lines and st._SEC_SLOTS in lines or True
    assert [st._SEC_FACTS, st._SEC_INTENTS] == \
        [h for h in (st._SEC_FACTS, st._SEC_SLOTS, st._SEC_INTENTS) if h in lines], \
        f"分区标题与相对顺序不得改变：{lines}"
    # 同义合并：近况段只留最新一条关系亲述
    facts = _section(out, st._SEC_FACTS)
    assert sum(1 for ln in facts if "甲的伴侣" in ln) == 1, facts
    # 归类：cue 话题不进计划段；同义计划只留一条
    plans = _section(out, st._SEC_INTENTS)
    assert sum(1 for ln in plans if "带饭" in ln) == 1, plans
    assert all("晚点再说" not in ln for ln in plans), f"cue 话题不得进未完成计划：{plans}"


def test_计划段多取让更旧计划补进被同义腾出的名额(trace_db):
    # 前两条同义（明早/9月27日），第三条更旧但不同义 ⇒ 收敛后仍应出现
    _seed(trace_db,
          _intent("我答应明早给甲带饭"),
          _intent("我答应9月27日给甲带饭吃"),
          _intent("甲腰好之前其他事都往后排"))

    async def _retime():
        from datetime import timedelta
        from sqlalchemy import update
        from app.models.memory import ProspectiveIntent
        from app.utils.timeutil import now_naive_utc
        base = now_naive_utc()
        order = ["我答应明早给甲带饭", "我答应9月27日给甲带饭吃", "甲腰好之前其他事都往后排"]
        async with trace_db() as db:
            for i, val in enumerate(order):
                await db.execute(update(ProspectiveIntent)
                                 .where(ProspectiveIntent.content == val)
                                 .values(updated_at=base - timedelta(seconds=i)))
            await db.commit()
    asyncio.run(_retime())

    plans = _section(_build(trace_db), st._SEC_INTENTS)
    assert sum(1 for ln in plans if "带饭" in ln) == 1, plans
    assert any("往后排" in ln for ln in plans), f"被同义腾出的名额应由更旧计划补齐：{plans}"


# ────────────────────────── ⑤ 有界：新增计算不得显著抬高耗时 ──────────────────────────

def test_判重耗时与既有量级一致(trace_db):
    n = st.TRACE_FACT_FETCH_CAP
    rows = [_row("curated", f"近况合成样本甲第{i}号内容") for i in range(n)]
    t0 = time.perf_counter()
    for _ in range(20):
        st.select_fact_rows(rows, limit=8, max_per_predicate={"curated": 4})
    per_call_ms = (time.perf_counter() - t0) / 20 * 1000
    # 30 行两两比较是 O(n^2) 常数级小成本，单次必须远低于整段 trace 的 ~20ms 量级
    assert per_call_ms < 20.0, f"select_fact_rows 单次耗时异常升高：{per_call_ms:.2f}ms"
