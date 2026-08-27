"""抖音 MCP：浏览器启动 + 反检测（#67，2026-08-27）。

P0 反检测加固：``_launch`` 用本地 Edge（channel=msedge，比 bundled Chromium 更不易被识别），
并注入 ``add_init_script`` 内嵌 stealth 补丁（隐藏 webdriver/plugins/chrome/permissions，
零依赖不强制 playwright-stealth）；人类行为模拟：随机打字延迟、点击前随机微延迟、
重要操作前鼠标移动到目标附近。

对外暴露：``_run_sync`` / ``_launch`` / ``_close_ctx`` / ``_has_login_cookie`` / ``_shot`` /
``_human_typing`` / ``_human_click`` / ``_human_tap`` / 路径常量。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import random
import sys
import time

# 允许同目录兄弟模块互相导入（插件 dir 非 package，loader 以 ai_plugin_* 单文件加载）
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from pathlib import Path

_PROFILE_DIR = Path(__file__).resolve().parents[3] / "backend" / "data" / "douyin_profile"
_SCREENSHOT_DIR = _PROFILE_DIR / "screenshots"
_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Playwright sync API 必须运行在无 running-loop 的线程（to_thread 会复制 context 导致误报），
# 单 worker 同时天然串行化浏览器操作，避免并发抢同一 profile。
_PLAYWRIGHT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-pw")

_BIND_TIMEOUT_S = 300
_DOUYIN_HOME = "https://www.douyin.com"
_CREATOR_HOME = "https://creator.douyin.com/creator-micro/home"
_CONTENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
_COMMENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/data/following/comment"


async def _run_sync(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_PLAYWRIGHT_EXECUTOR, func, *args)


# 内嵌 stealth 补丁（零依赖；隐藏自动化指纹特征，降低风控识别概率）
_JS_STEALTH_SCRIPT = """
// 隐藏 webdriver 标记
Object.defineProperty(navigator, 'webdriver', { get: () => false });
// 补全 plugins
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
// 伪造 chrome 对象
window.chrome = { runtime: {} };
// 伪装 permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
// 掩盖 playwright 痕迹
try { delete window.__playwright__; } catch (e) {}
try { delete window.__pw_manual; } catch (e) {}
"""


def _launch(headless: bool):
    """启动本地 Edge 持久化上下文（channel=msedge），注入 stealth 补丁 + 反检测启动参数。"""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(_PROFILE_DIR / "profile"),
        channel="msedge",
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
    )
    try:
        ctx.add_init_script(_JS_STEALTH_SCRIPT)
    except Exception:
        pass
    return p, ctx


def _has_login_cookie(ctx) -> bool:
    try:
        cookies = ctx.cookies("https://www.douyin.com")
        keys = {c["name"] for c in cookies}
        return bool(keys & {"sessionid", "sessionid_ss", "sid_tt", "uid_tt"})
    except Exception:
        return False


def _shot(page, name: str) -> None:
    try:
        page.screenshot(path=str(_SCREENSHOT_DIR / f"{name}_{int(time.time())}.png"))
    except Exception:
        pass


def _close_ctx(p, ctx) -> None:
    try:
        ctx.close()
    except Exception:
        pass
    try:
        p.stop()
    except Exception:
        pass


# ------------------------------------------------------------------ 人类行为模拟（P0 反检测）
def _human_typing(page, text: str, delay_ms: tuple = (30, 120)) -> None:
    """模拟真人打字：随机 30-120ms 按键延迟（替代固定 delay=8）。"""
    try:
        page.keyboard.type((text or ""), delay=random.randint(*delay_ms))
    except Exception:
        pass


def _human_wait(page, lo: int = 200, hi: int = 800) -> None:
    """点击/操作前随机人反应延迟（200-800ms）。"""
    try:
        page.wait_for_timeout(random.randint(lo, hi))
    except Exception:
        pass


def _human_tap(page, locator, timeout: int = 5000) -> None:
    """点击前随机延迟 + 把鼠标移动到目标附近再点击（模拟真人，降低自动化痕迹）。"""
    try:
        page.wait_for_timeout(random.randint(200, 800))
        box = locator.bounding_box(timeout=timeout)
        if box:
            page.mouse.move(
                box["x"] + box["width"] / 2 + random.randint(-8, 8),
                box["y"] + box["height"] / 2 + random.randint(-8, 8),
            )
            page.wait_for_timeout(random.randint(80, 200))
    except Exception:
        pass
    locator.click(timeout=timeout)
