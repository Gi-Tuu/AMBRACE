# -*- coding: utf-8 -*-
"""架构断点 #11 · agent/perception.py → message_classifier.py 改名钉桩（零 IO，不连库）。

钉三件事：旧模块路径彻底消失、新模块可导入且旧符号全在、行为零变化（签名与返回结构逐字不动）。
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

NEW_MODULE = "app.agent.message_classifier"
# 拼接而非字面量：避免全仓 grep「旧路径残留」自检出现假命中
OLD_MODULE = "app.agent." + "perception"

# 改名后必须原样保留的公开符号（函数名一律未改）
OLD_SYMBOLS = (
    "perceive",
    "topic_cn",
    "build_perception_section",
    "INTENT_QUERY",
    "INTENT_EMOTION",
    "INTENT_SMALLTALK",
    "INTENT_COMMAND",
    "INTENT_DEEP",
)


def test_旧模块不可导入():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(OLD_MODULE)


def test_旧模块查找器无结果():
    assert importlib.util.find_spec(OLD_MODULE) is None


def test_旧源文件已从磁盘消失():
    pkg_dir = Path(list(importlib.import_module("app.agent").__path__)[0])
    assert not (pkg_dir / "perception.py").exists()
    assert not (pkg_dir / "perception.pyc").exists()


def test_新模块可导入且旧符号全在():
    mod = importlib.import_module(NEW_MODULE)
    assert [name for name in OLD_SYMBOLS if not hasattr(mod, name)] == []


def test_新模块源码文件名正确():
    mod = importlib.import_module(NEW_MODULE)
    assert Path(mod.__file__).name == "message_classifier.py"


def test_文档串写明与设备感知无关并指出链路文件():
    doc = importlib.import_module(NEW_MODULE).__doc__ or ""
    assert "in-message classification" in doc
    assert "device perception" in doc
    for chain_file in ("device/port.py", "perception_tier.py", "section_phone.py"):
        assert chain_file in doc


@pytest.mark.parametrize("consumer", [
    "app.agent.nodes",
    "app.agent.context.section_persona",
])
def test_调用方已改用新路径(consumer):
    mod = importlib.import_module(consumer)
    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert OLD_MODULE not in text
    assert NEW_MODULE in text


def test_legacy_已不再自己做分类_A22第五刀c随迁():
    """R4 随迁（A22 ⑤-c，2026-10-03）：``app.agent.context.legacy`` 从「必须引用新路径」改为「两边都不许引用」。

    原参数化把 legacy 与 nodes/section_persona 并列，断言它引用新分类器模块——但那个调用点位于
    legacy 的 13 段内联兜底之一，⑤-c 已整段删除（前置观测 A/B 双 0 命中，见本文件同目录台账）。
    分类现在**只有**注册表侧一个实现点（``section_persona.py:84`` 调 ``build_perception_section``）。
    断言原意一字未变（**绝不允许有人把旧路径 import 捡回来**）；这里额外钉死「legacy 也不再引用新路径」，
    防止有人往回退化的方向补一份重复分类实现。
    """
    text = Path(importlib.import_module("app.agent.context.legacy").__file__).read_text(encoding="utf-8")
    assert OLD_MODULE not in text, "旧感知模块被捡回来了"
    assert NEW_MODULE not in text, (
        "legacy 又出现分类器调用 ⇒ 与 section_persona 形成双实现（⑤-c 消灭的正是这类漂移）")


def test_perceive_返回结构逐字五键():
    mod = importlib.import_module(NEW_MODULE)
    result = mod.perceive("我今天加班好累，人生的意义是什么")
    assert set(result) == {"intent", "emotion", "emotion_label", "topic", "length_hint"}
    assert result["intent"] == mod.INTENT_DEEP
    assert result["topic"] == "work"


def test_perceive_空输入不抛且落闲聊其他():
    mod = importlib.import_module(NEW_MODULE)
    for blank in ("", None, "   "):
        result = mod.perceive(blank)
        assert result["intent"] == mod.INTENT_SMALLTALK
        assert result["topic"] == "other"


def test_topic_cn_与_build_perception_section_行为不变():
    mod = importlib.import_module(NEW_MODULE)
    assert mod.topic_cn("work") == "工作"
    assert mod.topic_cn("unknown_bucket") == "unknown_bucket"
    assert mod.build_perception_section(None) == ""
    assert mod.build_perception_section({}) == ""
    section = mod.build_perception_section(
        {"intent": mod.INTENT_EMOTION, "topic": "pet", "length_hint": "long"}
    )
    assert section.startswith("用户意图：情绪倾诉")
    assert "话题方向：宠物" in section
    assert "建议篇幅：较长" in section
    assert section.endswith("。")
