# -*- coding: utf-8 -*-
"""冷战 v5 增强测试（2026-08-16）：敷衍更冷回应 / 关系恶化支线判定 / 节点防重。"""

from app.scheduler.state_triggers import (
    _deteriorate_hit,
    DETERIORATE_SOOTHE_MIN,
    DETERIORATE_MINUTES,
    DETERIORATE_POSSESSIVENESS,
    SOOTHE_SINCERE,
    SOOTHE_DISMISSIVE,
)
from app.scheduler.storyline_engine import NODE_DISMISSIVE, NODE_DETERIORATE


def test_deteriorate_纯函数阈值():
    # 占有维不达标 → 不触发（即使敷衍很多）
    assert not _deteriorate_hit(soothe_count=5, elapsed_min=60, possessiveness=50)
    # 占有维达标 + 敷衍>=2 → 触发
    assert _deteriorate_hit(soothe_count=2, elapsed_min=60, possessiveness=80)
    # 占有维达标 + 冷战>=6h → 触发
    assert _deteriorate_hit(soothe_count=0, elapsed_min=360, possessiveness=80)
    # 都不达标 → 不触发
    assert not _deteriorate_hit(soothe_count=1, elapsed_min=100, possessiveness=80)


def test_deteriorate_阈值常量合理():
    assert DETERIORATE_SOOTHE_MIN == 2
    assert DETERIORATE_MINUTES == 360
    assert DETERIORATE_POSSESSIVENESS == 70


def test_敷衍词库命中():
    # 敷衍 = 道歉词 + 不耐烦词（行了吧/好了吧…）
    assert any(k in "行了吧我错了" for k in SOOTHE_DISMISSIVE)
    assert any(k in "行了吧我错了" for k in SOOTHE_SINCERE)
    # 纯道歉不误判为敷衍
    assert not any(k in "对不起" for k in SOOTHE_DISMISSIVE)


def test_剧情节点常量():
    # 敷衍更冷 / 关系恶化是独立节点（防重用）
    assert NODE_DISMISSIVE == 6
    assert NODE_DETERIORATE == 7
    assert NODE_DISMISSIVE != NODE_DETERIORATE