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
    "app.agent.context.legacy",
    "app.agent.context.section_persona",
])
def test_调用方已改用新路径(consumer):
    mod = importlib.import_module(consumer)
    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert OLD_MODULE not in text
    assert NEW_MODULE in text


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
