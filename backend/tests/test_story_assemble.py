# -*- coding: utf-8 -*-
"""Ariadne 模块 C：沿链半故事化组装单测（纯函数 + section 空实现退化等价）。"""
import asyncio

from app.memory.story_assemble import assemble_story_lines, get_chain_index_for_hits


def _m(id_, content, created_at, chain_id=None):
    return {"id": id_, "content": content, "created_at": created_at, "chain_id": chain_id}


def test_两节点成链_时间升序_前缀与连接词():
    chain = [
        _m(1, "第一次一起去看海", "2025-07-01 10:00:00", "c1"),
        _m(2, "后来又去了青岛吃烧烤", "2025-07-20 18:00:00", "c1"),
    ]
    out = assemble_story_lines([_m(2, "后来又去了青岛吃烧烤", "2025-07-20 18:00:00", "c1")],
                               {2: chain})
    assert len(out) == 2
    assert out[0].startswith("┌ [2025-07-01]") and "第一次一起去看海" in out[0]
    assert out[1].startswith("└") and any(w in out[1] for w in ("之后", "后来", "紧接着"))
    assert "2025-07-20" in out[1]


def test_同链成员不重复平铺():
    c1 = _m(1, "事件甲", "2025-01-01", "c1")
    c2 = _m(2, "事件乙", "2025-02-01", "c1")
    idx = {1: [c1, c2], 2: [c1, c2]}
    out = assemble_story_lines([c1, c2], idx)
    assert len(out) == 2  # 一条链只成块一次
    assert sum(1 for ln in out if "事件甲" in ln) == 1
    assert sum(1 for ln in out if "事件乙" in ln) == 1


def test_孤立点走单行_链成员混排():
    chain = [_m(1, "链上节点", "2025-01-01", "c1"), _m(2, "链上后继", "2025-03-01", "c1")]
    orphan = _m(9, "孤立记忆点", "2025-06-01")
    out = assemble_story_lines([orphan, _m(1, "链上节点", "2025-01-01", "c1")], {1: chain})
    assert out[0].startswith("- [2025-06-01] 孤立记忆点")
    assert out[1].startswith("┌ [2025-01-01] 链上节点")


def test_单节点链不成块_内容截断():
    single = _m(1, "长" * 200, "2025-01-01", "c1")
    out = assemble_story_lines([single], {1: [single]})
    # len(chain)==1 不满足 ≥2 → 孤立点路径，150 字截断
    assert out[0].startswith("- [2025-01-01]")
    assert len(out[0]) < len("- [2025-01-01] ") + 160


def test_链内截断120():
    chain = [_m(1, "长" * 200, "2025-01-01", "c1"), _m(2, "尾节点", "2025-02-01", "c1")]
    out = assemble_story_lines([chain[0]], {1: chain})
    assert len(out[0]) < len("┌ [2025-01-01] ") + 130


def test_空index_全部孤立路径():
    m1 = _m(1, "内容一", "2025-01-01")
    out = assemble_story_lines([m1], {})
    assert out == ["- [2025-01-01] 内容一"]


def test_get_chain_index_for_hits_空实现():
    assert asyncio.run(get_chain_index_for_hits([1, 2, 3])) == {}


def test_section_空实现退化_与flag关逐字节等价(monkeypatch):
    """flag 开但 chain_index 为空（建链器未落地）→ memories_section 输出与 flag 关完全一致。"""
    from app.agent import loop
    from app.agent.context import section_memories as sm

    retrieved = [{"id": 1, "content": "记忆甲", "created_at": "2025-01-01 10:00:00",
                  "importance": 60.0, "memory_type": "event"}]
    monkeypatch.setattr(sm, "_mark_memories_injected", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_filter_recently_injected", lambda cid, r: list(r))

    async def _empty_index(ids):
        return {}

    monkeypatch.setattr("app.memory.story_assemble.get_chain_index_for_hits", _empty_index)

    state = {"character_id": 1, "retrieved_memories": retrieved}

    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_story_assemble": False})
    off = asyncio.run(sm.memories_section(state, {}))
    monkeypatch.setattr(loop, "AGENT_FLAGS", {**loop.AGENT_FLAGS, "memory_story_assemble": True})
    on_empty = asyncio.run(sm.memories_section(state, {}))
    assert off == on_empty and "记忆甲" in off
