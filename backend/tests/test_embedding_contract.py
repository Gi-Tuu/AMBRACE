# -*- coding: utf-8 -*-
"""向量契约冒烟（非 slow，CI 必跑）。

目的：保证「向量写入 / 检索」链路在 CI 真的被执行，而不是因为无 bge-m3 模型
静默退化成 embedding=None 的假绿。CI 侧的兜底见 tests/conftest.py 的
_deterministic_fake_embedding_when_model_missing（模型缺失时注入 1024 维确定性假向量）。
"""
import asyncio
import math

DIM = 1024  # 与 bge-m3 一致；维度不符会让 Chroma collection 报错


def test_text_embedding_contract_1024d_and_normalized(monkeypatch):
    from app.memory import embedding as emb
    from app.memory.embedding import text_embedding

    # 真实模型加载会写进 emb._embed_model 进程级缓存；不还原会污染同 worker 的
    # test_embedding_warmup::test_load_embed_model_raises_when_model_missing（它靠「模型未加载」才可见
    # check_model_available()=False 的抛错路径）。这里用 monkeypatch 把它还原回用例前的值。
    monkeypatch.setattr(emb, "_embed_model", None, raising=False)

    vec = asyncio.run(text_embedding("向量契约冒烟"))
    assert isinstance(vec, list)
    assert len(vec) == DIM, f"向量维度必须是 {DIM}，实际 {len(vec)}"
    norm = math.sqrt(sum(float(x) ** 2 for x in vec))
    assert abs(norm - 1.0) < 1e-3, f"向量必须 L2 归一化，实际范数 {norm}"


def test_text_embedding_deterministic_and_text_sensitive(monkeypatch):
    from app.memory import embedding as emb
    from app.memory.embedding import text_embedding

    monkeypatch.setattr(emb, "_embed_model", None, raising=False)  # 同上：不污染模型缓存

    a1 = asyncio.run(text_embedding("同一条文本"))
    a2 = asyncio.run(text_embedding("同一条文本"))
    b = asyncio.run(text_embedding("完全另一条文本"))
    assert a1 == a2, "同一文本必须得到相同向量"
    assert a1 != b, "不同文本必须得到不同向量"
    assert len(b) == DIM
