# -*- coding: utf-8 -*-
"""RRF（Reciprocal Rank Fusion，倒数排名融合）：dense / sparse 两路按 rank 融合。

========================= 设计说明（RRF 融合) =========================

【背景】
上轮检索增强里，向量（dense）与 BM25（sparse）两路只是『候选池按 memory_id 合并去重』
后交给 _rerank（importance/时效/置顶/话题/多路命中加权）——两路各自的「相关性排序」信息
被丢弃：一个被向量路排第 1、被 BM25 路排第 2 的记忆，与一个只在某路勉强命中的记忆，在
纯合并里拿到的是相同的 importance 排序，无法反映「两路都高排位」的强相关信号。

RRF 解决这一点：对每路各自按相关性降序编号 rank（1 起），融合分 = Σ_list 1/(k + rank)。
同一记忆被多路高排位召回时分数显著更高——这正是 RRF 相对纯合并的核心增益。k 建议 60
（可配置，默认 _RRF_K），k 越大对 rank 的区分越钝、越聚焦『多路共现』；k 越小对 top-rank
越敏感。

【与 _rerank 的叠加】
RRF 融合分不直接当作最终排序，而是归一化到 [0, _RRF_WEIGHT] 后作为 relevance_bonus
注入 _rerank，与现有 importance/时效/置顶/话题/多路命中加权叠加。RRF 提供的是「相关性
证据」的权重，不与置顶（+500）争夺主序，但能在 importance 相近的候选间给出有原则的排序。
RRF 计算异常时在 service 侧静默退化为纯合并（relevance_bonus 为空），不影响主链路。

本模块为纯函数、可单测，不依赖 DB / 向量 / jieba，方便对『语义近但词不近』
『词命中但语义弱』两类查询验证 RRF 排序优于纯合并。
"""

_BRRF_DEFAULT_K = 60       # 默认 k（可配置：传给 reciprocal_rank_fusion / fuse 的 k 参数）
_RRF_WEIGHT = 30.0         # relevance_bonus 权重上限（归一化后 × 该值叠加进 _rerank）


def reciprocal_rank_fusion(ranked_lists, k: int = _BRRF_DEFAULT_K) -> dict:
    """RRF 融合：返回 {item_id: rrf_score}。

    参数
    ----
    ranked_lists: list[list]，每路一个列表，元素为 item id，**按相关性降序**，rank 从 1 起。
                  例如 dense 路按向量相似度降序、sparse 路按 BM25 分降序。
    k: int，调和常数（默认 60）。同一 item 在多路出现则分数累加（共现加分）。

    例：ranked_lists=[[1,2,3],[2,3,4]], k=60 →
        score[1]=1/61, score[2]=1/62+1/61, score[3]=1/63+1/62, score[4]=1/63。
    """
    scores: dict = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def normalized_bonus(scores: dict, weight: float = _RRF_WEIGHT) -> dict:
    """把 RRF 分数归一化到 [0, weight]，作为 _rerank 的 relevance_bonus。

    归一化除以当前候选集内的最大 RRF 分，使权重与候选集规模/路数无关，
    并保持候选间的相对次序。空集或全 0 时返回 {}（视为无 RRF 证据）。
    """
    if not scores:
        return {}
    m = max(scores.values())
    if m <= 0:
        return {}
    return {item: (v / m) * weight for item, v in scores.items()}


def fuse(ranked_lists, k: int = _BRRF_DEFAULT_K) -> list:
    """RRF 融合后按分数降序返回 item id 列表（供直接观察融合排序）。"""
    scores = reciprocal_rank_fusion(ranked_lists, k=k)
    return sorted(scores, key=lambda x: scores[x], reverse=True)
