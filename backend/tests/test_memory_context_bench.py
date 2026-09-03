# -*- coding: utf-8 -*-
"""Ariadne 模块 E：memory_context_bench 纯函数单测（按路径加载脚本，仿 test_audit_batch3 模式）。"""
import importlib.util
import os

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "..", "scripts", "diagnostics", "memory_context_bench.py")


def _load():
    spec = importlib.util.spec_from_file_location("memctx_bench", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_gold_coverage_子串与空白归一():
    m = _load()
    hit, miss = m.gold_coverage(
        ["美式咖啡", "橘猫"], "- [记录于 2026-06-01] 用户喜欢喝美式咖啡，不加糖\n- [记录于 2026-05-20] 橘  猫叫橘子")
    assert hit == ["美式咖啡", "橘猫"] and miss == []


def test_gold_coverage_未命中():
    m = _load()
    hit, miss = m.gold_coverage(["不存在的记忆点"], "块文本")
    assert hit == [] and miss == ["不存在的记忆点"]


def test_gold_coverage_空gold():
    m = _load()
    assert m.gold_coverage([], "任意块") == ([], [])


def test_abstain_ok_阈值():
    m = _load()
    assert m.abstain_ok("", 0) is True
    assert m.abstain_ok("x" * 40, 20) is True   # 20 token 估算=边界内
    assert m.abstain_ok("x" * 42, 21) is False  # >20 token → 弃权失败
