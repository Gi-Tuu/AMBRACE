# -*- coding: utf-8 -*-
"""批次一任务4（2026-09-16）：复习命中 = 回忆（过去时），与「当前现状」物理分区。

用户 09-09 定调、体检报告 P0-3 复述：「复习应该是回忆，不是当最近的记忆」。
本文件只测纯函数（快测档，零 DB）：
- build_review_recall_block：显式「回忆·过去时」标签 + 封在独立分区内；
- build_review_current_state_block：现状块自带「禁止回忆块参与本块拼装」声明；
- 两条块互不包含 → 复习命中物理上进不了 current_state 拼装。
"""
from app.scheduling.memory_review import (
    build_review_current_state_block,
    build_review_recall_block,
)

_MEM = "用户近期将去长沙，期间可能因不便携带电脑而断联"
_STATE = "\nTA 当前已知现状（以此为准，旧记忆不得与此矛盾）：位置：示例市；状态：在读学生。\n"


def test_recall_block_怀旧带过去时标签():
    blk = build_review_recall_block("你回忆起一段**往事**（记录于 08月15日，距今约 32 天）", _MEM)
    assert blk.startswith("【回忆·过去时")
    assert "已经发生过的往事" in blk
    assert "禁止当作当下场景描述" in blk
    assert "［回忆·过去时］" in blk
    assert "你回忆起一段**往事**" in blk and _MEM in blk


def test_recall_block_非怀旧用中性标题():
    """仍有效期内的安排不是「往事」：用中性「想起的事」标题，但仍与现状分区。"""
    blk = build_review_recall_block("你想起 TA 之前跟你提过的一个还没到的安排", "下周去成都", nostalgia=False)
    assert blk.startswith("【想起的事")
    assert "［想起的事］" in blk
    assert "回忆·过去时" not in blk


def test_recall_block_内容截断120字():
    blk = build_review_recall_block("引导语", "长" * 300)
    assert "长" * 120 in blk and "长" * 121 not in blk


def test_current_state_block_带分区与禁拼装声明():
    blk = build_review_current_state_block(_STATE)
    assert blk.startswith("【当前现状")
    assert "禁止参与本块拼装" in blk
    assert "示例市" in blk
    # status_anchor 自身带的换行被 strip，不留空行
    assert "\n\n\n" not in blk


def test_current_state_block_无锚点占位():
    blk = build_review_current_state_block("")
    assert blk.startswith("【当前现状")
    assert "（暂无已知现状锚点）" in blk


def test_回忆块与现状块物理分区_内容互不进入():
    """核心不变式：复习命中的记忆内容绝不出现于 current_state 块内。"""
    recall = build_review_recall_block("你回忆起一段**往事**", _MEM)
    state = build_review_current_state_block(_STATE)
    assert _MEM not in state          # 复习命中不参与 current_state 拼装
    assert "示例市" not in recall     # 现状内容也不倒灌进回忆块
    assert recall != state
