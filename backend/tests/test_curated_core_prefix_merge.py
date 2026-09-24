# -*- coding: utf-8 -*-
"""C13 curated 写入侧「共享核心前缀」合并规则测试（2026-09-25）。

背景：生产库 68 条 active curated 里绝大多数是「同一个长核心 + 各自后缀」形态，既有三条判据
（归一化全等 / 前缀包含 / SequenceMatcher>=0.9）只命中 4 对，拦不住那一族。新增判据：
归一化后最长公共前缀 LCP >= 7 且 >= 短串 30% ⇒ 视同一条。

覆盖：a 命中 / b 不误并 / c 阈值边界与归一化 / d~f 集成（flag 开合并、核心不同各成行、flag 关旧行为）。
样例一律用脱敏合成文本（甲/小甲），不抄生产库私密原文（tests 目录会进公开仓快照）。
纪律：临时库走 tmp_path（禁止 tempfile.mkdtemp 裸建）；不碰生产库；纯确定性、零 LLM。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.events.facts import (
    KIND_CONSTRAINT,
    KIND_FACT,
    KIND_RELATION_BASE,
    _CORE_PREFIX_MIN_FRAC,
    _CORE_PREFIX_MIN_LEN,
    _MIN_CORE_LEN,
    _MIN_IDENTITY_CORE_LEN,
    _WORLD_FACT_SIMILARITY,
    _same_curated_value,
    _same_fact_text,
    assert_curated,
)
from app.models.memory import WorldFact

# 快测档：本文件含集成型用例（每例克隆一份会话级模板库，见 tests/_dbclone.py），按项目纪律打 slow。
pytestmark = pytest.mark.slow

CHAR = 11
USER = 1


@pytest.fixture()
def cf_db(monkeypatch, tmp_path):
    """临时库（模板库克隆，见 tests/_dbclone.py）：把 facts / database 的
    async_session_factory 指向临时工厂。"""
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)

    import app.db.database as db_mod
    import app.events.facts as facts
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(facts, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _curate(factory, *, kind, value, **kw):
    async def _run():
        async with factory() as db:
            row = await assert_curated(db, character_id=CHAR, user_id=USER, kind=kind,
                                      object_value=value, **kw)
            await db.commit()
            if row is not None:
                await db.refresh(row)
            return row
    return asyncio.run(_run())


def _active(factory):
    async def _run():
        async with factory() as db:
            return (await db.execute(
                select(WorldFact).where(
                    WorldFact.character_id == CHAR,
                    WorldFact.status == "active",
                ).order_by(WorldFact.id)
            )).scalars().all()
    return asyncio.run(_run())


# ───────────────────────────── a. 新判据命中 ─────────────────────────────

def test_阈值常量与既有判据未被改动():
    assert _CORE_PREFIX_MIN_LEN == 7
    assert _CORE_PREFIX_MIN_FRAC == 0.30
    # 既有三条判据的阈值原样（守卫：C13 只追加，不替换）
    assert _WORLD_FACT_SIMILARITY == 0.9
    assert _MIN_CORE_LEN == 6
    assert _MIN_IDENTITY_CORE_LEN == 4


def test_共享核心前缀_命中():
    # LCP=7（核心「我是小甲的伴侣」）+ 各自后缀：实测互不包含、SequenceMatcher 只有 0.45，
    # 靠新规则才收敛成一条
    assert _same_fact_text("我是小甲的伴侣（sam），与小甲是伴侣关系",
                           "我是小甲的伴侣，会照顾受伤的小甲并为他设好界限") is True
    assert _same_curated_value("我是小甲的伴侣，关系稳定",
                               "我是小甲的伴侣，与甲同住") is True
    # 「我是sam，甲的伴侣」这条既有前缀包含（LCP=9=短串全长）也命中，新规则不改其结论
    assert _same_fact_text("我是sam，甲的伴侣",
                           "我是sam，甲的伴侣，会照顾甲的日常起居") is True


# ───────────────────────────── b. 不误并（关键回归） ─────────────────────────────

def test_共享核心前缀_不误并():
    # LCP 仅 3 字（「甲的腰」）：两条不同约束必须各留各的
    assert _same_fact_text("甲的腰不能压，侧躺需垫东西",
                           "甲的腰部有伤，需避免趴着、扭动或自己乱伸手") is False
    assert _same_fact_text("喜欢咖啡", "喜欢喝茶") is False
    # 身份主张走值比较那条分支保持原样（不被新规则改写）
    assert _same_curated_value("甲是设计师", "甲是设计师助理") is False
    assert _same_curated_value("用户是设计师", "用户是设计师助理") is False
    assert _same_curated_value("用户是设计师", "用户是设计师，从事设计工作") is True


# ───────────────────────────── c. 边界 ─────────────────────────────

def test_边界_LCP为6不并():
    """LCP 恰为 6（< 7）：新规则不命中，既有三条也不命中。"""
    assert _same_fact_text("我是甲的伴侣，关系稳定", "我是甲的伴侣，与甲同住") is False
    assert _same_fact_text("我是甲的伴侣，关系稳定", "我是甲的伴侣，正在长沙工作") is False


def test_边界_占比30分界():
    # LCP=7 / 短串 24 字 = 29.2% < 30% ⇒ 不并
    assert _same_fact_text("我是小甲的伴侣，平时喜欢喝手冲咖啡，周末常去公园跑步",
                           "我是小甲的伴侣，偶尔也自己做饭吃，周末喜欢在家看电影") is False
    # LCP=7 / 短串 23 字 = 30.4% ≥ 30% ⇒ 并
    assert _same_fact_text("我是小甲的伴侣，平时喜欢喝手冲的咖啡周末常去跑步",
                           "我是小甲的伴侣，偶尔也会自己做饭吃周末喜欢在家看电影") is True


def test_边界_归一化生效():
    """空白 / 中英标点 / 全角标点差异不影响判定。"""
    assert _same_fact_text("我是小甲的伴侣（sam），与小甲是伴侣关系",
                           "我是小甲的伴侣 sam - 与小甲是伴侣关系") is True
    assert _same_fact_text("我是小甲的伴侣，关系稳定",
                           "我是小甲的伴侣　关系稳定") is True


def test_边界_空值安全不抛():
    assert _same_fact_text("", "") is False
    assert _same_fact_text(None, "我是小甲的伴侣") is False
    assert _same_fact_text("   ", "我是小甲的伴侣") is False      # 纯空白归一化后为空
    assert _same_curated_value("", None) is False
    assert _same_curated_value("我是小甲的伴侣", "") is False


# ───────────────────────────── d~f. 集成 ─────────────────────────────

def test_flag开_同核心三次写入始终只有一行(cf_db, monkeypatch):
    """落库口径不变：同义写入只合并证据，不新增行、不改既有行文本。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    first = _curate(cf_db, kind=KIND_RELATION_BASE, value="我是小甲的伴侣，关系稳定",
                    sources=[{"src": "chat_extract", "message_id": 1}], confidence=0.6)
    _curate(cf_db, kind=KIND_RELATION_BASE, value="我是小甲的伴侣，与甲同住",
            sources=[{"src": "chat_extract", "message_id": 2}], confidence=0.9)
    _curate(cf_db, kind=KIND_RELATION_BASE, value="我是小甲的伴侣，会照顾甲的饮食起居",
            sources=[{"src": "diary_extract", "message_id": 3}], confidence=0.8)

    rows = _active(cf_db)
    assert len(rows) == 1
    assert rows[0].id == first.id
    assert rows[0].object_value == "我是小甲的伴侣，关系稳定"      # same 分支不更新 object_value
    assert rows[0].confidence == pytest.approx(0.9)               # confidence 取大
    for mid in (1, 2, 3):
        assert f'"message_id": {mid}' in (rows[0].sources_json or "")


def test_flag开_核心不同各成一行(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_RELATION_BASE, value="我是小甲的伴侣，关系稳定")
    _curate(cf_db, kind=KIND_CONSTRAINT, value="小甲的腰不能压，侧躺需垫东西")
    _curate(cf_db, kind=KIND_FACT, value="小甲的腰部有伤，需避免趴着、扭动或自己乱伸手")
    assert len(_active(cf_db)) == 3


def test_flag关_同核心各写一行(cf_db):
    """flag 关 = 逐字节旧行为：新判据不参与（查重仍是 strip 全等）。"""
    _loop.AGENT_FLAGS["memory_admission_gate"] = False
    try:
        _curate(cf_db, kind=KIND_RELATION_BASE, value="我是小甲的伴侣，关系稳定")
        _curate(cf_db, kind=KIND_RELATION_BASE, value="我是小甲的伴侣，与甲同住")
        rows = _active(cf_db)
        assert len(rows) == 2
        assert [r.object_value for r in rows] == ["我是小甲的伴侣，关系稳定",
                                                 "我是小甲的伴侣，与甲同住"]
    finally:
        _loop.AGENT_FLAGS.pop("memory_admission_gate", None)


# ───────────── g~j. C13b：公共前缀必须停在子句边界（2026-09-25 追加） ─────────────
# 追加符号只在下方用例里用到，就近导入以免改动上方既有内容（纯函数、零 DB、不碰 flag）。
from app.events.facts import (  # noqa: E402
    _CLAUSE_BOUNDARY_CHARS,
    _prefix_ends_at_clause_boundary,
)


def test_边界_同模板不同宾语不并_C13b核心回归():
    """LCP=7 但停在词内（美/拿）⇒ 新判据不命中，两条各留各的。"""
    assert _same_fact_text("用户平时喜欢喝美式咖啡", "用户平时喜欢喝拿铁咖啡") is False
    assert _same_fact_text("用户平时喜欢喝美式咖啡不加糖",
                           "用户平时喜欢喝拿铁咖啡要加奶") is False
    # 路由不变：非身份主张仍走文本相似那条分支
    assert _same_curated_value("用户平时喜欢喝美式咖啡", "用户平时喜欢喝拿铁咖啡") is False


def test_边界_前缀后是标点或整串结束仍并():
    """边界成立（一边前缀后是「（」、另一边是「，」）⇒ 结论与 C13a 一致。"""
    assert _same_fact_text("我是用户的老公（sam），与用户是伴侣关系",
                           "我是用户的老公，会照顾受伤的用户并为他设好亲密时的界限") is True
    # 既有「前缀包含」本已命中（前缀吃掉短串整串），顺带断言结论未被改写
    assert _same_fact_text("我是用户的老公", "我是用户的老公，关系稳定") is True


def test_边界_单边成立即可():
    """按定义「任一边成立即可」：一边前缀后是「（」成立，另一边是「与」不成立 ⇒ 仍并。"""
    assert _same_fact_text("我是用户的老公（sam），与用户是伴侣关系",
                           "我是用户的老公与用户共同生活") is True
    assert _prefix_ends_at_clause_boundary("我是用户的老公（sam），与用户是伴侣关系", 7) is True
    assert _prefix_ends_at_clause_boundary("我是用户的老公与用户共同生活", 7) is False


def test_边界_空值纯空白安全且边界字符齐备():
    assert _same_fact_text("", "") is False
    assert _same_fact_text(None, None) is False
    assert _same_fact_text("\u3000 ", "我是用户的老公") is False      # 纯空白归一化后为空
    assert _same_fact_text("我是用户的老公", "") is False
    assert _prefix_ends_at_clause_boundary("", 7) is False            # 反查不可靠/越界不抛
    assert _prefix_ends_at_clause_boundary(None, 7) is False
    assert _prefix_ends_at_clause_boundary("我是用户的老公", 99) is False
    assert _prefix_ends_at_clause_boundary("我是用户的老公", 7) is True   # 前缀吃掉整串
    for ch in (" ", "\u3000", "\t", "\n", "\r", "，", "。", "！", "？", "、", "；", "：",
               ",", ".", "!", "?", ";", ":", "（", "）", "(", ")", "【", "】", "[", "]",
               "「", "」", "『", "』", "\u201c", "\u201d", "\u2018", "\u2019", '"', "'",
               "-", "—", "~", "～"):
        assert ch in _CLAUSE_BOUNDARY_CHARS
