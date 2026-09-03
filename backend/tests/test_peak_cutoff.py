# -*- coding: utf-8 -*-
"""Ariadne 模块 D：peak_cutoff 自然收敛纯函数单测。

语义锚定（含与方案原稿的有意偏离）：
- 地板先行：候选整体低于 min_score → 返回 []（弃权场景归零；原稿无条件 min_keep 会让
  abstention 永远注入 3 条，此处已在回报说明偏离理由）；
- 断档截断：相邻分差 > score_gap 即止；min_keep 保底；max_keep 封顶。
"""
from app.memory.retrieve import peak_cutoff


def _r(i, score):
    return {"id": i, "_score": score}


def test_空输入返回空():
    assert peak_cutoff([]) == []


def test_整体低于地板_收敛为空():
    ranked = [_r(1, 10.0), _r(2, 8.0), _r(3, 6.0), _r(4, 5.0)]
    assert peak_cutoff(ranked) == []  # 全部 < 18 地板（弃权场景）


def test_全部高于地板_保持到max_keep():
    ranked = [_r(i, 100.0 - i * 2) for i in range(1, 12)]  # 98,96,...（gap=2 < 12）
    out = peak_cutoff(ranked)
    assert [r["id"] for r in out] == list(range(1, 9))  # max_keep=8 截断


def test_断档截断():
    ranked = [_r(1, 90), _r(2, 88), _r(3, 86), _r(4, 70), _r(5, 68)]
    # min_keep=3 保留 1-3；86→70 断档 16 > 12 → 止
    out = peak_cutoff(ranked)
    assert [r["id"] for r in out] == [1, 2, 3]


def test_地板中途截断():
    ranked = [_r(1, 90), _r(2, 88), _r(3, 60), _r(4, 58)]
    # min_keep=3 → 1-3；60 ≥ 18 保留；58 ≥ 18 但 60-58=2 无断档 → 4 也保留？——
    # 地板只过滤「进不了 min_keep 的候选」：4 在 above 里且无断档 → 保留
    out = peak_cutoff(ranked)
    assert [r["id"] for r in out] == [1, 2, 3, 4]


def test_地板先行_首条即低于地板():
    ranked = [_r(1, 15.0), _r(2, 95.0), _r(3, 94.0)]  # 排序输入按 _score 降序（首条最高）
    ranked.sort(key=lambda x: x["_score"], reverse=True)
    out = peak_cutoff(ranked)
    # 首条 95 ≥ 18 → above=[95,94,...]；15 被地板过滤
    assert [r["id"] for r in out] == [2, 3]


def test_min_keep保底_不超可用数():
    ranked = [_r(1, 90), _r(2, 89)]
    out = peak_cutoff(ranked)
    assert [r["id"] for r in out] == [1, 2]


def test_自定义参数():
    ranked = [_r(1, 50), _r(2, 40), _r(3, 30)]
    out = peak_cutoff(ranked, min_keep=1, max_keep=2, score_gap=5, min_score=10)
    # min_keep=1 保 1；40→50 差 10 > 5 → 止
    assert [r["id"] for r in out] == [1]
