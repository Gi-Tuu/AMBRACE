"""图片理解服务：
- VLM（云端 OpenAI 兼容视觉 API）：自然语言描述图片内容（本地 Ollama 分支已于 2026-10-17 移除）
- OCR（RapidOCR）：提取图片中的文字
- 约束：图片文件/二进制绝不传入 deepseek，只传文字描述（见 AGENTS.md）
"""
import asyncio
import base64
from collections import OrderedDict
import os
from PIL import Image
from app.config import settings
from app.utils.logger import get_logger

_logger = get_logger("services.image_understanding_service")

MAX_EDGE = 1280  # 压缩长边，控制 OCR/VLM 耗时
_ocr = None
_cache: "OrderedDict[str, str]" = OrderedDict()  # 图片路径 -> 描述文本（同一图片不重复理解）
_CACHE_MAX = 256  # 描述缓存上限，防止临时图路径无限累积
MAX_DESC_CHARS = 800  # 注入上下文的描述文本上限

VLM_PROMPT = "请用简短的中文描述这张图片的内容（场景、物体、人物动作、文字等），30-60字，直接输出描述。"



def _is_garbage(text: str) -> bool:
    """VLM 输出质量校验：异常输出（重复符号、纯符号串等）判定为垃圾，避免写入库。"""
    t = (text or "").strip()
    if not t:
        return True
    if len(t) >= 3 and len(set(t)) <= 1:
        return True
    # 不含中文字符、英文字母、数字的纯符号串（如 @@@@、????）
    has_word = any(
        "\u4e00" <= c <= "\u9fff" or c.isascii() and c.isalnum() for c in t
    )
    return not has_word


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
        _logger.info("RapidOCR initialized")
    return _ocr


def compress_image(input_path: str, output_path: str | None = None) -> str:
    """压缩图片长边到 MAX_EDGE，返回输出路径（无损格式转 JPEG）"""
    img = Image.open(input_path)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_EDGE:
        ratio = MAX_EDGE / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + "_compressed.jpg"
    img.save(output_path, "JPEG", quality=90)
    return output_path


def extract_text_ocr(image_path: str) -> str:
    """RapidOCR 提取图片文字，返回纯文本（无结果返回空串）"""
    try:
        ocr = _get_ocr()
        result, _ = ocr(image_path)
        if not result:
            return ""
        lines = [str(item[1]).strip() for item in result]
        lines = [l for l in lines if l]
        return "\n".join(lines)
    except Exception as e:
        _logger.warning("OCR failed for %s: %s", image_path, e)
        return ""


async def get_vlm_config(user_id: int | None = None) -> dict:
    """识图生效配置：四模态统一回落链（用户/家庭/服务器 DB）优先，.env 兜底。

    账号独立 P1（2026-09-19）：DB 查表收敛到唯一出口
    ``llm_config_service.resolve_modality_config("vlm", ...)``（此前本函数直读
    ``vlm_configs`` 的 SERVER_CONFIG_UID 哨兵行）。``user_id`` 缺省 None 时链路直达
    服务器级默认，行为与改造前逐字段一致。
    """
    cfg = {
        "enabled": bool(settings.vlm_enabled),
        "base_url": settings.vlm_base_url,
        "api_key": settings.vlm_api_key,
        "model": settings.vlm_model,
        "timeout_sec": settings.vlm_timeout_sec,
    }
    try:
        from app.application.llm_config_service import resolve_modality_config
        row = await resolve_modality_config("vlm", user_id=user_id)
        if row is not None:
            cfg = {
                "enabled": True,
                "base_url": row.get("base_url") or settings.vlm_base_url,
                "api_key": row.get("api_key") or settings.vlm_api_key,
                "model": row.get("model") or settings.vlm_model,
                "timeout_sec": settings.vlm_timeout_sec,
            }
    except Exception as e:
        _logger.warning("get_vlm_config failed: %s", e)
    return cfg


async def _describe_vlm_cloud(image_path: str, cfg: dict) -> str:
    """调用云端 OpenAI 兼容视觉 API（VLM_API_KEY 非空时优先，图片仅以 data URI 传给视觉端点）。
    失败/垃圾输出返回空串（不影响主流程）。"""
    try:
        from openai import AsyncOpenAI
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        client = AsyncOpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            timeout=cfg["timeout_sec"],
            max_retries=1,
        )
        resp = await client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": [
                {"type": "text", "text": VLM_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            max_tokens=200,
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
        if _is_garbage(text):
            _logger.warning("cloud VLM garbage for %s: %r", image_path, text[:80])
            return ""
        return text
    except Exception as e:
        _logger.warning("cloud VLM describe failed for %s: %s", image_path, e)
        return ""


async def describe_vlm(image_path: str, cfg: dict) -> str:
    """生成图片自然语言描述：只走云端视觉 API（本地 Ollama 分支已于 2026-10-17 移除）。
    未配置 Key 或未启用时返回空串；失败/垃圾输出同样返回空串（不影响主流程）。"""
    if not cfg["api_key"]:
        return ""
    return await _describe_vlm_cloud(image_path, cfg)


async def describe_image(image_path: str, user_id: int | None = None) -> str:
    """图片理解（OCR + 云端 VLM）。user_id 非空时受「识图」权限约束（ask/forbid 跳过识图）"""
    if user_id is not None:
        try:
            from app.application import permission_service
            _mode = await permission_service.check_mode(user_id, permission_service.SCOPE_IMAGE_UNDERSTAND)
            if _mode != "allow":
                return ""
        except Exception:
            pass
    """生成图片描述：VLM 自然语言描述 + OCR 文字合并，注入 AI 上下文。带缓存。"""
    if image_path in _cache:
        _cache.move_to_end(image_path)
        return _cache[image_path]

    comp = await asyncio.to_thread(compress_image, image_path)
    text = await asyncio.to_thread(extract_text_ocr, comp)
    cfg = await get_vlm_config()
    vlm_desc = await describe_vlm(comp, cfg) if (cfg["enabled"] or cfg["api_key"]) else ""
    try:
        if comp != image_path and os.path.exists(comp):
            os.remove(comp)
    except Exception:
        pass

    parts = []
    if vlm_desc:
        parts.append(vlm_desc[:MAX_DESC_CHARS])
    if text:
        parts.append(f"图中文字：{text[:MAX_DESC_CHARS]}")
    desc = "；".join(parts) if parts else ""
    _cache[image_path] = desc
    _cache.move_to_end(image_path)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return desc
