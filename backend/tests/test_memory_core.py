"""核心记忆 / 开放循环 / 关系锚点（World & Cognition P1，2026-08-15）"""
from app.memory.core import (
    _core_category, CORE_MIN_IMPORTANCE, CORE_MIN_CONFIRMATIONS,
    CORE_CATEGORY_BY_SUBTYPE,
)


def test_core_category_mapping():
    assert _core_category("name", "user_info") == "identity"
    assert _core_category("food", "preference") == "preference"
    assert _core_category("anniversary", "event") == "milestone"
    assert _core_category("commitment", None) == "commitment"
    assert _core_category(None, "user_info") == "identity"
    assert _core_category(None, "event") is None


def test_core_thresholds():
    assert CORE_MIN_IMPORTANCE == 80.0
    assert CORE_MIN_CONFIRMATIONS == 2
    # 身份/偏好/承诺类高重要单次晋升阈值 100
    assert "identity" in CORE_CATEGORY_BY_SUBTYPE.values()
