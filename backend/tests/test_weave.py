"""织库纯函数测试：LLM 输出清洗 / 卡片字段规范化 / 幂等哈希（不依赖 DB/LLM）。"""

import pytest

from datetime import datetime, timedelta

from app.models.memory import Memory
from app.weave.card_generator import _clean_llm_json, _cluster_by_time, _content_hash, _normalize_card


def test_clean_llm_json_带markdown围栏():
    raw = '```json\n{"title": "第一次见面", "summary": "s", "detail": {}}\n```'
    assert _clean_llm_json(raw)["title"] == "第一次见面"


def test_clean_llm_json_带前后缀文本():
    raw = '好的，以下是卡片：{"title": "t", "summary": "s", "detail": {}} 希望你喜欢'
    assert _clean_llm_json(raw)["title"] == "t"


def test_clean_llm_json_非对象报错():
    with pytest.raises(ValueError):
        _clean_llm_json('[1, 2, 3]')


def test_normalize_card_缺字段兜底():
    title, summary, detail = _normalize_card({"title": "t", "summary": "s"})
    assert title == "t" and summary == "s"
    assert detail["time"] == "不详"
    assert detail["events"] == []
    assert detail["details"] == []


def test_normalize_card_detail结构():
    data = {
        "title": "t", "summary": "s",
        "detail": {"time": "2026-08-10 下午", "weather": "晴", "location": "北京",
                   "mood": "开心", "events": ["一起吃饭"], "details": ["点了火锅"]},
    }
    _, _, detail = _normalize_card(data)
    assert detail["time"] == "2026-08-10 下午"
    assert detail["weather"] == "晴"
    assert detail["events"] == ["一起吃饭"]


def test_content_hash_顺序无关且稳定():
    assert _content_hash([3, 1, 2]) == _content_hash([2, 1, 3])
    assert _content_hash([1, 2]) != _content_hash([1, 2, 3])
    assert len(_content_hash([1, 2])) == 64


def _mem(mid: int, cid: int, day_offset: int) -> Memory:
    return Memory(
        id=mid,
        user_id=1,
        character_id=cid,
        memory_type="event",
        content=f"记忆{mid}",
        importance=70.0,
        created_at=datetime(2026, 8, 12) + timedelta(days=day_offset),
    )


def test_cluster_by_time_跨角色同窗合并():
    cands = [_mem(1, 11, 0), _mem(2, 12, 1), _mem(3, 11, 3), _mem(4, 12, 30)]
    clusters = _cluster_by_time(cands)
    assert len(clusters) == 2  # 前 3 条同 7 天窗合并（跨角色），第 4 条单独
    assert {m.id for m in clusters[0]} == {1, 2, 3}
    assert [m.id for m in clusters[1]] == [4]


def test_cluster_by_time_每簇上限20条():
    cands = [_mem(i, 11, 0) for i in range(45)]
    clusters = _cluster_by_time(cands)
    sizes = [len(c) for c in clusters]
    assert all(s <= 20 for s in sizes)
    assert sum(sizes) == 45
    assert len(clusters) == 3

def test_dedup_余弦相似与文本相似():
    from app.weave.dedup import _cos_sim, _text_sim

    # 相同向量 → 1.0；正交 → 0.0
    assert _cos_sim([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert _cos_sim([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert _cos_sim([], [1.0]) == 0.0
    # 中文 bigram 相似度：完全一致 1.0，无交集 0.0
    assert _text_sim("一起去看电影", "一起去看电影") == 1.0
    assert _text_sim("一起去看电影", "今天天气很好") == 0.0


def test_dedup_查重归组保留keeper():
    from app.weave.dedup import find_duplicates

    def card(cid, title, summary, emb, mems, imp=70.0):
        return {"id": cid, "title": title, "summary": summary, "importance": imp,
                "embedding": emb, "memory_ids": set(mems)}

    # 两组重复 + 一张独立卡
    cards = [
        card(1, "去看电影", "一起去看电影，看的是科幻片", [1.0, 0.2, 0.1], {101, 102}, 70),
        card(2, "看电影", "一起看了科幻电影，很开心", [0.98, 0.25, 0.12], {101, 103, 104}, 75),
        card(3, "一起去看电影", "一起去看电影，科幻片，爆米花", [0.99, 0.22, 0.1], {102, 105}, 72),
        card(4, "今天天气", "今天天气很好，出去散步", [0.1, 0.9, 0.1], {201}, 70),
        card(5, "天气不错", "今天天气很好，适合散步", [0.12, 0.92, 0.15], {202}, 70),
        card(6, "完全无关", "本周工作计划安排", [0.0, 0.1, 0.9], {301}, 70),
    ]
    groups = find_duplicates(cards)
    assert len(groups) == 2
    for g in groups:
        # keeper = 记忆数最多的一张
        assert len(g[0]["memory_ids"]) == max(len(c["memory_ids"]) for c in g)
    flat = [c["id"] for g in groups for c in g]
    assert sorted(flat) == [1, 2, 3, 4, 5]


def test_dedup_embedding缺失时仅文本兜底():
    from app.weave.dedup import find_duplicates

    cards = [
        {"id": 1, "title": "一样的标题", "summary": "完全一样的概要文本内容", "importance": 70.0, "embedding": [], "memory_ids": {1}},
        {"id": 2, "title": "一样的标题", "summary": "完全一样的概要文本内容", "importance": 70.0, "embedding": [], "memory_ids": {2}},
    ]
    groups = find_duplicates(cards)
    assert len(groups) == 1
    assert groups[0][0]["id"] == 1


def test_private_life_type_聚合():
    """私域画布：参与记忆 sub_type 聚合为节点生活类型（reflection > note > life_event）"""
    from app.weave.graph import _pick_life_type
    assert _pick_life_type(["life_event", "note", "reflection"]) == "reflection"
    assert _pick_life_type(["life_event", "note"]) == "note"
    assert _pick_life_type(["life_event"]) == "life_event"
    assert _pick_life_type([]) == "life_event"
