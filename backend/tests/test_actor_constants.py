# -*- coding: utf-8 -*-
"""Actor / 认知态常量单一来源测试（P0 语义统一 · 第 1 步，2026-09-29；**零行为**）。

钉住五条底线：
1. **取值照现状字面量**：规范值与各处既有事实源逐字相等（``write.py::PERCEPTION_SENDER``、
   ``perception_tier.PERCEPTION_SOURCE`` / ``FACT_STATUS``、``events/schema.EPISTEMIC_*``）；
2. **归一化与旧实现参数化对照**：``actors.normalize_sender`` 对每一枚入参的结果都必须等于
   ``write.py::_normalize_sender``（词表、大小写/空白归一、未登记值→``None``、脏值抛错口径全同）；
3. **词表不放大**：准入裁决面（``admit_memory``）现状只认 character/ai，``char``/``bot`` 不认——
   这处差异必须照实保留（否则第 1 步就偷偷改了裁决结果）；
4. **纯模块**：``app/actors.py`` 除 ``__future__`` 外零 import（零 IO、零业务依赖）；
5. 导出面只含常量与纯函数（不藏状态、不藏副作用）。
"""
import ast
import inspect
import types

import pytest

from app import actors
from app.memory import perception_tier
from app.memory import write as _write

# 归一化对照语料：现状三组别名 + 大小写/空白变体 + 未登记值 + 空/空白/None
SENDER_CORPUS = [
    "ai", "AI", " ai ", "character", "CHARACTER", "char", "bot", "BoT",
    "tool", "TOOL", "mcp", "MCP ", "search", "external",
    "user", "User", "user ", "system", " SYSTEM ",
    "", "   ", None, "npc", "perception", "assistant", "unknown", "ai_user", "0", "工具",
]


# ─────────────────────── ① 取值照现状字面量 ───────────────────────

def test_归属规范值与各处既有事实源逐字相等():
    assert actors.ACTOR_USER == "user"
    assert actors.ACTOR_CHARACTER == "character"
    assert actors.ACTOR_SYSTEM == "system"
    assert actors.ACTOR_TOOL == "tool"
    # 断点 #5 的准入归属 ＋ provenance 兜底文本：值与大小写都不许动
    assert actors.ACTOR_PERCEPTION == _write.PERCEPTION_SENDER == "perception"
    assert actors.PERCEPTION_SOURCE == perception_tier.PERCEPTION_SOURCE == "perception"
    assert actors.ACTOR_UNSET == "unset"


def test_规范值集合无重复且全小写():
    assert len(set(actors.ACTORS)) == len(actors.ACTORS)
    assert all(a == a.strip().lower() for a in actors.ACTORS)
    # perception 属规范值，unset（占位）不属——不许把「没有归属」当成一种归属
    assert actors.ACTOR_PERCEPTION in actors.ACTORS
    assert actors.ACTOR_UNSET not in actors.ACTORS


def test_认知态常量与事件层事实源同值同序():
    from app.events import schema as _schema

    names = ("EPISTEMIC_FACT", "EPISTEMIC_INFERRED", "EPISTEMIC_PLANNED",
             "EPISTEMIC_FICTIONAL", "EPISTEMIC_UNVERIFIED")
    assert [getattr(actors, n) for n in names] == [getattr(_schema, n) for n in names], "认知态取值漂移"
    assert actors.EPISTEMIC_VALUES == _schema.EPISTEMIC_VALUES, "认知态全集或顺序漂移"
    assert actors.EPISTEMIC_FACT == perception_tier.FACT_STATUS == "FACT"


def test_来源面字面量照现状():
    assert actors.CHAT_SOURCE == "chat"
    assert actors.SELF_NARRATIVE_SOURCES == ("diary", "life", "bio")   # write.py:393 顺序照抄
    assert actors.TOOL_SOURCE_PREFIXES == ("mcp", "tool", "search")    # write.py:395


# ─────────────────────── ② 归一化：参数化对照旧实现 ───────────────────────

@pytest.mark.parametrize("value", SENDER_CORPUS)
def test_归一化与write旧实现逐值相同(value):
    assert actors.normalize_sender(value) == _write._normalize_sender(value), f"归一化漂移：{value!r}"


@pytest.mark.parametrize("value", SENDER_CORPUS)
def test_归一化只产出规范值或None(value):
    out = actors.normalize_sender(value)
    assert out is None or out in actors.ACTORS, f"归一化产出了未登记取值：{out!r}"


def test_归一化对非字符串与旧实现同样抛错不擅自吞():
    """现状 ``_normalize_sender`` 对非字符串抛 AttributeError（调用方各自吞掉）；
    第 1 步不许改变这个口径——吞掉异常就等于把「脏值」悄悄归成了某个归属。"""
    for bad in (123, 3.14, ["user"], object()):
        with pytest.raises(AttributeError):
            _write._normalize_sender(bad)
        with pytest.raises(AttributeError):
            actors.normalize_sender(bad)


def test_别名表与旧逻辑三元组一致不扩表():
    assert actors.CHARACTER_SENDER_ALIASES == ("ai", "character", "char", "bot")
    assert actors.TOOL_SENDER_ALIASES == ("tool", "mcp", "search", "external")
    assert actors.PRESERVED_SENDER_ALIASES == ("user", "system")


# ─────────────────────── ③ 准入面窄表差异照实保留 ───────────────────────

def test_准入面词表比归一面窄_差异未放大():
    assert actors.ADMISSION_CHARACTER_ALIASES == ("character", "ai")
    assert "char" not in actors.ADMISSION_CHARACTER_ALIASES and "bot" not in actors.ADMISSION_CHARACTER_ALIASES
    assert actors.normalize_sender("char") == actors.ACTOR_CHARACTER   # 归一面认（照现状）
    assert actors.ADMISSION_TOOL_ALIASES == actors.TOOL_SENDER_ALIASES


def test_admit_memory对char与character裁决不同_现状未被本批改动():
    """第 1 步零行为的直接证据：``admit_memory`` 仍按它自己那组窄表判，本模块不介入。"""
    assert _write.admit_memory("chat", "character", "", 1.0, "event") == ("INFERRED", True)
    assert _write.admit_memory("chat", "char", "", 1.0, "event") == ("FACT", False)
    assert _write.admit_memory("chat", "tool", "", 1.0, "event") == ("UNVERIFIED", False)
    assert _write.admit_memory("chat", "system", "", 1.0, "event") == ("FACT", False)


# ─────────────────────── ④⑤ 纯模块 / 导出面 ───────────────────────

def test_纯模块零业务import零IO():
    tree = ast.parse(inspect.getsource(actors))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in imports:
        if isinstance(node, ast.ImportFrom):
            assert node.module == "__future__", f"actors.py 不得 import 业务模块：{node.module}"
        else:
            for alias in node.names:
                assert alias.name == "__future__", f"actors.py 不得 import 业务模块：{alias.name}"


def test_导出面只含常量与纯函数():
    for name in actors.__all__:
        value = getattr(actors, name)
        assert isinstance(value, (str, tuple, types.FunctionType)), f"{name} 类型超出纯数据面：{type(value)}"
    assert set(actors.__all__) >= {"ACTORS", "normalize_sender", "EPISTEMIC_VALUES"}
