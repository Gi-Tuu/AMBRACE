# -*- coding: utf-8 -*-
"""bge-m3 向量模型预热（warmup_embedding）单元测试。

不依赖真实模型文件：通过 monkeypatch 替换 _load_embed_model / check_model_available，
验证预热成功返回 True、模型缺失/加载失败静默（记录 warning 但不抛出）、以及
模型缺失时底层 _load_embed_model 按预期抛错。
"""
import asyncio

import pytest

import app.memory.embedding as emb


def test_warmup_success_returns_true(monkeypatch):
    """模型可加载时 warmup 返回 True，并确实调用了一次 _load_embed_model。"""
    calls = []

    def _fake_load():
        calls.append(True)

    monkeypatch.setattr(emb, "_load_embed_model", _fake_load)
    result = asyncio.run(emb.warmup_embedding())
    assert result is True
    assert calls == [True]


def test_warmup_model_missing_returns_false_no_raise(monkeypatch):
    """模型缺失时 warmup 静默返回 False，绝不抛出（服务照常启动）。"""

    def _boom():
        raise emb._embed_model_missing_error()

    monkeypatch.setattr(emb, "_load_embed_model", _boom)
    # 不应抛出
    result = asyncio.run(emb.warmup_embedding())
    assert result is False


def test_warmup_generic_failure_returns_false(monkeypatch):
    """加载过程抛任意异常（如 ONNX 会话创建失败）时安静返回 False。"""

    def _boom():
        raise RuntimeError("onnx session create failed")

    monkeypatch.setattr(emb, "_load_embed_model", _boom)
    result = asyncio.run(emb.warmup_embedding())
    assert result is False


def test_load_embed_model_raises_when_model_missing(monkeypatch):
    """模型缺失时底层同步加载函数按预期抛 RuntimeError（含下载指引）。"""
    monkeypatch.setattr(emb, "check_model_available", lambda: False)
    with pytest.raises(RuntimeError):
        emb._load_embed_model()
