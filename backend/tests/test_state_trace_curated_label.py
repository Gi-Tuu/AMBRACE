"""C7：state_trace 谓词标签中文化 + status 保底（2026-09-24）。

出处＝09-24 全量检查报告 P3-2：①curated 不在 _PREDICATE_LABEL ⇒ 中文 prompt 里夹英文标签；
②事实按 updated_at desc 取 8 条，char13 最新几条几乎全是 curated，当前 status 会被挤出。

纯函数级回归：用 dict / SimpleNamespace 造假行，不连库、不起服务、不 stub 网络。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.scheduling import state_trace as st


def _fact(predicate, value):
    return SimpleNamespace(predicate=predicate, object_value=value, updated_at="2026-09-24 00:00:00")


def test_predicate_labels_keep_existing_values():
    """回归锁定：原有 4 个键的值不许改。"""
    assert st._PREDICATE_LABEL["status"] == "状态"
    assert st._PREDICATE_LABEL["activity"] == "正在做"
    assert st._PREDICATE_LABEL["location"] == "位置"
    assert st._PREDICATE_LABEL["mood"] == "心情"


def test_curated_has_chinese_label():
    assert st._PREDICATE_LABEL["curated"] == "近况"
    line = st.fact_line(_fact("curated", "后腰还压着枕头"))
    assert line == "- 近况：后腰还压着枕头"
    assert "curated" not in line


def test_unknown_predicate_still_falls_back_to_raw():
    """未知谓词保持原样输出（宁漏不编的口径不变）。"""
    assert st.fact_line(_fact("weird_pred", "随便")) == "- weird_pred：随便"


def test_merge_inserts_status_when_missing():
    rows = [_fact("curated", "腰伤")]
    status = _fact("status", "正在做腰部护理")
    out = st._merge_status_row(rows, status)
    assert len(out) == 2
    assert out[0] is status


def test_merge_keeps_rows_when_status_present():
    rows = [_fact("status", "在睡觉"), _fact("curated", "腰伤")]
    out = st._merge_status_row(rows, _fact("status", "更早的"))
    assert out is rows


def test_merge_keeps_rows_when_status_row_none():
    rows = [_fact("curated", "腰伤")]
    assert st._merge_status_row(rows, None) is rows


def test_merge_supports_dict_rows():
    """渲染函数支持 dict 行（_get），保底函数也要跟着支持。"""
    rows = [{"predicate": "curated", "object_value": "腰伤"}]
    status = {"predicate": "status", "object_value": "在护理"}
    out = st._merge_status_row(rows, status)
    assert len(out) == 2
    assert out[0] is status
