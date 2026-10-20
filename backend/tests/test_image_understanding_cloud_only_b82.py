"""Ollama 本地识图分支已于 2026-10-17 移除：识图只走云端 VLM。

守卫目的：防止后来者「为了离线可用」把本地分支悄悄加回来——那会让服务端重新依赖
本机可执行文件与模型目录，而线上配置已全部走云端视觉 API。
"""
import asyncio
import pathlib

from app.application import image_understanding_service as ius
from app.config import settings


def _cfg(**over):
    cfg = {"enabled": True, "base_url": "https://example.invalid/v1",
           "api_key": "", "model": "qwen-vl-max", "timeout_sec": 5}
    cfg.update(over)
    return cfg


def test_本地识图函数已不存在():
    assert not hasattr(ius, "_restart_ollama")
    assert not hasattr(ius, "_ollama_vlm_sync")


def test_没有api_key时不发起任何请求(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("未配置 Key 时不应发起任何请求")

    monkeypatch.setattr(ius, "_describe_vlm_cloud", boom)
    assert asyncio.run(ius.describe_vlm("x.jpg", _cfg())) == ""


def test_配置不再提供本地ollama字段():
    for name in ("vlm_ollama_exe", "vlm_ollama_models_dir", "vlm_garbage_restart_threshold"):
        assert not hasattr(settings, name), name


def test_服务代码里不再写死本地端点(monkeypatch):
    src = pathlib.Path(ius.__file__).read_text(encoding="utf-8")
    assert "11434" not in src
    assert "urllib.request" not in src
