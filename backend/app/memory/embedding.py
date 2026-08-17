"""本地向量嵌入（bge-small-zh ONNX，CPU 推理）

从 agent/llm_client 移出（2026-08-04 Phase 2.2）：LLM 对话与向量嵌入是不同领域，
embedding 供记忆/向量检索使用，避免 llm_client 职责混杂。
"""
import asyncio
import os
import numpy as np
from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("memory.embedding")

_embed_model = None


def _load_embed_model():
    """加载本地 bge-small-zh ONNX 向量模型（首次调用时加载，之后复用）"""
    global _embed_model
    if _embed_model is None:
        model_dir = os.path.join(settings.PROJECT_ROOT, "models", "bge-small-zh-v1.5")
        from tokenizers import Tokenizer
        import onnxruntime as ort
        tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        session = ort.InferenceSession(
            os.path.join(model_dir, "onnx", "model_int8.onnx"),
            providers=["CPUExecutionProvider"],
        )
        _embed_model = (tokenizer, session)
        _logger.info("Embedding model loaded: bge-small-zh-v1.5 (int8, 512d)")
    return _embed_model


def _embed_sync(text: str) -> list[float]:
    """同步执行本地 bge-small-zh ONNX 推理（含首次模型加载）。

    由 text_embedding 放入线程池执行：ONNX 推理和模型加载都是 CPU 密集，
    直接跑在事件循环里会阻塞所有并发请求（此前消息周期性卡顿的主因）。
    """
    tokenizer, session = _load_embed_model()
    enc = tokenizer.encode(text)
    ids = np.array([enc.ids], dtype=np.int64)
    mask = np.array([enc.attention_mask], dtype=np.int64)
    ttype = np.array([enc.type_ids], dtype=np.int64)
    token_emb = session.run(
        None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": ttype}
    )[0].astype(np.float32)
    # mean pooling + L2 归一化（bge 系列标准后处理）
    m = mask.astype(np.float32)[..., None]
    sum_emb = (token_emb * m).sum(axis=1)
    count = m.sum(axis=1)
    mean = sum_emb / np.maximum(count, 1e-9)
    norm = np.linalg.norm(mean, axis=1, keepdims=True)
    return (mean / np.maximum(norm, 1e-9))[0].tolist()


async def text_embedding(text: str) -> list[float]:
    """获取文本向量嵌入（本地 bge-small-zh ONNX；推理在独立线程执行，不阻塞事件循环）"""
    return await asyncio.to_thread(_embed_sync, text)
