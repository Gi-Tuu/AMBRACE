"""图片理解服务（本地）：
- VLM（Ollama Qwen2.5-VL）：自然语言描述图片内容（GPU 加速）
- OCR（RapidOCR）：提取图片中的文字
- 约束：图片文件/二进制绝不传入 deepseek，只传文字描述（见 AGENTS.md）
"""
import asyncio
import base64
from collections import OrderedDict
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
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


_garbage_streak = 0  # 连续垃圾输出计数（达到阈值触发 Ollama 自愈重启）


def _restart_ollama() -> bool:
    """重启本地 Ollama 服务（垃圾输出/卡死自愈）。仅在本机部署时启用。"""
    exe = settings.vlm_ollama_exe
    if not exe or not os.path.exists(exe):
        return False
    try:
        port = urllib.parse.urlparse(settings.vlm_base_url).port or 11434
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p = Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | "
             "Select-Object -ExpandProperty OwningProcess; if ($p) { Stop-Process -Id $p -Force }"],
            capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        time.sleep(2)
        env = dict(os.environ)
        env.setdefault("OLLAMA_MODELS", settings.vlm_ollama_models_dir)
        subprocess.Popen(
            [exe, "serve"], env=env,
            creationflags=subprocess.CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        time.sleep(4)
        return True
    except Exception as e:
        _logger.warning("Ollama restart failed: %s", e)
        return False


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


async def get_vlm_config() -> dict:
    """识图生效配置：服务器级 DB（vlm_configs user_id=0）优先，.env 兜底"""
    cfg = {
        "enabled": bool(settings.vlm_enabled),
        "base_url": settings.vlm_base_url,
        "api_key": settings.vlm_api_key,
        "model": settings.vlm_model,
        "timeout_sec": settings.vlm_timeout_sec,
    }
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.agent.llm_client import SERVER_CONFIG_UID
        from app.models.config import VlmConfig
        async with async_session_factory() as db:
            row = (
                await db.execute(select(VlmConfig).where(VlmConfig.user_id == SERVER_CONFIG_UID))
            ).scalar_one_or_none()
        if row is not None and row.enabled and (row.base_url or row.api_key):
            cfg = {
                "enabled": True,
                "base_url": row.base_url or settings.vlm_base_url,
                "api_key": row.api_key or settings.vlm_api_key,
                "model": row.model or settings.vlm_model,
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


def _ollama_vlm_sync(image_path: str, cfg: dict) -> str:
    """同步调用本地 Ollama VLM（含文件读取与阻塞网络）。

    必须经 asyncio.to_thread 调用，避免在事件循环线程里阻塞整机 AI。
    """
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": VLM_PROMPT, "images": [b64]}],
        "stream": False,
        "options": {"num_predict": 120},
    }
    url = cfg["base_url"].rstrip("/") + "/api/chat"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=cfg["timeout_sec"]) as resp:
        data = json.loads(resp.read().decode())
    return (data.get("message") or {}).get("content", "").strip()


async def describe_vlm(image_path: str, cfg: dict) -> str:
    """生成图片自然语言描述：cfg.api_key 非空优先走云端视觉 API，否则走本地 Ollama VLM。
    失败/垃圾输出返回空串（不影响主流程）；本地连续垃圾输出时自动重启 Ollama 并重试一次。"""
    global _garbage_streak
    if cfg["api_key"]:
        return await _describe_vlm_cloud(image_path, cfg)
    if not cfg["enabled"]:
        return ""
    for attempt in range(2):
        try:
            text = await asyncio.to_thread(_ollama_vlm_sync, image_path, cfg)
            if _is_garbage(text):
                _garbage_streak += 1
                _logger.warning(
                    "VLM garbage (streak=%d) for %s: %r",
                    _garbage_streak, image_path, text[:80],
                )
                if _garbage_streak >= settings.vlm_garbage_restart_threshold and attempt == 0:
                    _garbage_streak = 0
                    _logger.warning("VLM garbage threshold hit, restarting Ollama...")
                    await asyncio.to_thread(_restart_ollama)
                    continue
                return ""
            _garbage_streak = 0
            return text
        except Exception as e:
            if attempt == 0:
                _logger.warning("VLM describe failed (retrying after restart) for %s: %s", image_path, e)
                await asyncio.to_thread(_restart_ollama)
                continue
            _logger.warning("VLM describe failed for %s: %s", image_path, e)
            return ""
    return ""


async def describe_image(image_path: str, user_id: int | None = None) -> str:
    """本地图片理解（OCR + VLM）。user_id 非空时受「识图」权限约束（ask/forbid 跳过识图）"""
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
