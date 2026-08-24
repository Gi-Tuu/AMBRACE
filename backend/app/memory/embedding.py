"""本地向量嵌入（bge-m3 ONNX，CPU 推理）

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


def check_model_available() -> bool:
    """检查本地 bge-m3 向量模型文件是否齐全（tokenizer.json + onnx/model_int8.onnx）。

    供 /api/v1/system/ready（P2-4）与 _load_embed_model 使用；缺失时可运行
    python scripts/download_models.py 一键下载。
    """
    model_dir = os.path.join(settings.PROJECT_ROOT, "models", "bge-m3")
    return os.path.isfile(os.path.join(model_dir, "tokenizer.json")) and os.path.isfile(
        os.path.join(model_dir, "onnx", "model_int8.onnx")
    )


def _embed_model_missing_error() -> RuntimeError:
    """缺模型时的统一中文报错（含下载指引），供 _load_embed_model 抛出。"""
    return RuntimeError(
        "本地向量模型缺失：未找到 backend/models/bge-m3（需 tokenizer.json 与 onnx/model_int8.onnx）。"
        "请从 Release 下载 bge-m3 模型放置到 backend/models/（可运行 python scripts/download_models.py 一键下载）。"
    )


def _load_embed_model():
    """加载本地 bge-m3 ONNX 向量模型（首次调用时加载，之后复用）"""
    global _embed_model
    if _embed_model is None:
        if not check_model_available():
            raise _embed_model_missing_error()
        model_dir = os.path.join(settings.PROJECT_ROOT, "models", "bge-m3")
        from tokenizers import Tokenizer
        import onnxruntime as ort
        tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        session = ort.InferenceSession(
            os.path.join(model_dir, "onnx", "model_int8.onnx"),
            providers=["CPUExecutionProvider"],
        )
        _embed_model = (tokenizer, session)
        _logger.info("Embedding model loaded: bge-m3 (int8, 1024d, XLM-R CLS)")
    return _embed_model


async def warmup_embedding() -> bool:
    """后台预热本地 bge-m3 向量模型（线程中加载，不阻塞事件循环）。

    供启动生命周期调用：首次模型加载需数秒到数十秒，提前在后台加载可消除首条
    记忆检索/写入的延迟。模型缺失或加载失败仅记 warning，绝不抛出（服务照常启动）。
    返回是否预热成功。
    """
    def _warm() -> bool:
        try:
            _load_embed_model()
            return True
        except Exception as _e:
            _logger.warning("Embedding warmup failed (model may be missing): %s", _e)
            return False

    try:
        return await asyncio.to_thread(_warm)
    except Exception as _e:
        _logger.warning("Embedding warmup task error: %s", _e)
        return False


def _embed_sync(text: str) -> list[float]:
    """同步执行本地 bge-m3 ONNX 推理（含首次模型加载）。

    由 text_embedding 放入线程池执行：ONNX 推理和模型加载都是 CPU 密集，
    直接跑在事件循环里会阻塞所有并发请求（此前消息周期性卡顿的主因）。
    """
    tokenizer, session = _load_embed_model()
    enc = tokenizer.encode(text)
    ids = np.array([enc.ids], dtype=np.int64)
    mask = np.array([enc.attention_mask], dtype=np.int64)
    token_emb = session.run(
        None, {"input_ids": ids, "attention_mask": mask}
    )[0].astype(np.float32)
    # XLM-R / BGE 统一后处理：CLS pooling + L2 归一化
    cls = token_emb[0, 0].copy()
    norm = np.linalg.norm(cls)
    return (cls / np.maximum(norm, 1e-9)).tolist()


async def text_embedding(text: str) -> list[float]:
    """获取文本向量嵌入（本地 bge-m3 ONNX；推理在独立线程执行，不阻塞事件循环）"""
    return await asyncio.to_thread(_embed_sync, text)
