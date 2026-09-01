"""生图服务：OpenAI 兼容 images API provider + 异步任务 + 每日限额 + 落盘"""
import base64
import re
import time as _time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from app.config import settings
from app.db.database import async_session_factory
from app.models.life import ImageGenTask
from app.utils.logger import get_logger

_logger = get_logger("services.image_gen")


class ImageGenProvider(ABC):
    """生图 provider 抽象（v1 = OpenAI 兼容 images API；私有协议后续加子类）"""

    @abstractmethod
    async def generate(self, prompt: str) -> bytes:
        """生成图片，返回 PNG/JPEG 字节；失败抛异常"""


class OpenAICompatImageProvider(ImageGenProvider):
    """OpenAI 兼容 images.generate（主流服务可直连）"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    async def generate(self, prompt: str) -> bytes:
        from openai import AsyncOpenAI
        import httpx
        _http_client = httpx.AsyncClient(proxy=None)
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, http_client=_http_client)
        try:
            resp = await client.images.generate(
                model=self.model,
                prompt=prompt,
                size="1024x1024",
                n=1,
            )
            item = resp.data[0]
            if getattr(item, "b64_json", None):
                return base64.b64decode(item.b64_json)
            if getattr(item, "url", None):
                async with httpx.AsyncClient(proxy=None, timeout=120) as dl:
                    r = await dl.get(item.url)
                    r.raise_for_status()
                    return r.content
            raise RuntimeError("生图响应缺少图片数据")
        finally:
            await _http_client.aclose()


class DashScopeChatImageProvider(ImageGenProvider):
    """阿里云百炼企业版 MaaS 兼容端点：qwen-image 系列走 chat/completions
    （content 列表格式），返回 output.choices[].message.content[].image 临时 URL。
    也兼容 OpenAI 风格的多模态返回（image_url）。
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def generate(self, prompt: str) -> bytes:
        import httpx
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
        async with httpx.AsyncClient(proxy=None, timeout=240) as client:
            r = await client.post(
                url,
                headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        img_url = self._extract_image_url(data)
        if not img_url:
            raise RuntimeError("生图响应缺少图片 URL")
        async with httpx.AsyncClient(proxy=None, timeout=120) as dl:
            rr = await dl.get(img_url)
            rr.raise_for_status()
            return rr.content

    @staticmethod
    def _extract_image_url(data: dict) -> str | None:
        choices = (data.get("output") or {}).get("choices") or data.get("choices") or []
        for ch in choices:
            msg = (ch or {}).get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    # qwen-image 实际响应 content 项为 {"image": "url"}（无 type），
                    # 兼容带 type 的 OpenAI 风格与两种 image_url 形态
                    if item.get("image"):
                        return item["image"]
                    if item.get("type") == "image" and item.get("image"):
                        return item["image"]
                    if item.get("type") == "image_url":
                        iu = item.get("image_url")
                        if isinstance(iu, dict) and iu.get("url"):
                            return iu["url"]
                        if isinstance(iu, str):
                            return iu
            elif isinstance(content, str):
                # markdown 图片链接兜底
                m = re.search(r"https?://[^\s)\]]+\.(?:png|jpe?g|webp)", content)
                if m:
                    return m.group(0)
        return None


async def get_image_gen_config() -> dict:
    """生图生效配置：服务器级 DB（image_gen_configs user_id=0）优先，.env 兜底"""
    cfg = {
        "enabled": bool(settings.image_gen_enabled),
        "provider": (settings.image_gen_provider or "openai").lower(),
        "base_url": settings.image_gen_base_url,
        "api_key": settings.image_gen_api_key,
        "model": settings.image_gen_model,
        "daily_limit": settings.image_gen_daily_limit or 10,
    }
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.agent.llm_client import SERVER_CONFIG_UID
        from app.models.life import ImageGenConfig
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(ImageGenConfig).where(ImageGenConfig.user_id == SERVER_CONFIG_UID)
                )
            ).scalar_one_or_none()
        if row is not None and row.enabled and (row.base_url or row.api_key):
            cfg = {
                "enabled": True,
                "provider": (row.provider or "openai").lower(),
                "base_url": row.base_url or settings.image_gen_base_url,
                "api_key": row.api_key or settings.image_gen_api_key,
                "model": row.model or settings.image_gen_model,
                "daily_limit": row.daily_limit or settings.image_gen_daily_limit or 10,
            }
    except Exception as e:
        _logger.warning("get_image_gen_config failed: %s", e)
    return cfg


async def get_image_provider() -> ImageGenProvider | None:
    """按生效配置构建生图 provider；未启用/未配置返回 None"""
    cfg = await get_image_gen_config()
    if not cfg["enabled"]:
        return None
    if not cfg["base_url"] or not cfg["api_key"] or not cfg["model"]:
        return None
    if cfg["provider"] == "dashscope":
        return DashScopeChatImageProvider(base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"])
    return OpenAICompatImageProvider(base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"])


def _day_start_utc() -> datetime:
    bj = timezone(timedelta(hours=8))
    now_bj = datetime.now(bj)
    start_bj = datetime(now_bj.year, now_bj.month, now_bj.day, tzinfo=bj)
    return start_bj.astimezone(timezone.utc).replace(tzinfo=None)


async def check_daily_limit(user_id: int) -> bool:
    """今日生图数是否已达上限（True=已达，拒绝）"""
    cfg = await get_image_gen_config()
    limit = cfg.get("daily_limit") or 10
    async with async_session_factory() as db:
        cnt = await db.execute(
            select(func.count()).where(
                ImageGenTask.user_id == user_id,
                ImageGenTask.created_at >= _day_start_utc(),
            )
        )
        return cnt.scalar_one() >= limit


async def create_image_gen_task(user_id: int, prompt: str, character_id: int | None = None,
                                session_id: int | None = None) -> ImageGenTask:
    """创建生图任务并返回（异步执行由调用方经 spawn_background 调度）"""
    async with async_session_factory() as db:
        task = ImageGenTask(
            user_id=user_id, prompt=prompt,
            character_id=character_id, session_id=session_id,
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task


async def get_image_gen_task(task_id: int, user_id: int) -> ImageGenTask | None:
    async with async_session_factory() as db:
        result = await db.execute(
            select(ImageGenTask).where(ImageGenTask.id == task_id, ImageGenTask.user_id == user_id)
        )
        return result.scalar_one_or_none()


def _save_image_bytes(data: bytes, user_id: int) -> str:
    """生图结果落盘 uploads/images/{user_id}/，返回 /uploads/... 相对路径"""
    subdir = settings.PROJECT_ROOT / "data" / "uploads" / "images" / str(user_id)
    subdir.mkdir(parents=True, exist_ok=True)
    fname = f"{_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    path = subdir / fname
    with open(path, "wb") as f:
        f.write(data)
    return f"/uploads/images/{user_id}/{fname}"


async def run_image_gen_task(task_id: int) -> str | None:
    """执行生图任务（阻塞直到完成），成功返回 image_url，失败返回 None（错误已写任务记录）"""
    provider = await get_image_provider()
    if provider is None:
        await _fail_task(task_id, "未配置生图服务（IMAGE_GEN_ENABLED/BASE_URL/API_KEY/MODEL）")
        return None
    async with async_session_factory() as db:
        task = await db.get(ImageGenTask, task_id)
        if not task:
            return None
        task.status = "generating"
        await db.commit()
    try:
        data = await provider.generate(task.prompt)
        image_url = _save_image_bytes(data, task.user_id)
        async with async_session_factory() as db:
            task = await db.get(ImageGenTask, task_id)
            task.status = "done"
            task.image_url = image_url
            task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
        _logger.info("Image gen done task=%d user=%d: %.40s", task_id, task.user_id, task.prompt)
        return image_url
    except Exception as e:
        _logger.warning("Image gen failed task=%d: %s", task_id, e)
        await _fail_task(task_id, str(e)[:500])
        return None


async def _fail_task(task_id: int, error: str) -> None:
    async with async_session_factory() as db:
        task = await db.get(ImageGenTask, task_id)
        if task:
            task.status = "failed"
            task.error = error
            task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()


def schedule_image_gen(task_id: int) -> None:
    """异步执行生图任务（不阻塞请求线程）"""
    from app.utils.async_tasks import spawn_background
    spawn_background(run_image_gen_task(task_id))
