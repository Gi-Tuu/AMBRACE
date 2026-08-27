"""AI 专属抖音账号 MCP 插件（Phase 1.1：登录绑定 + 只读感知 + 新评论提醒，选择器已校准 2026-08-09）

- 浏览器：Playwright 驱动系统 Edge（channel=msedge），独立 profile（backend/data/douyin_profile/，不入 git）
- 登录：POST /api/v1/plugins/douyin_mcp/bind 弹出可见窗口扫码，cookie 持久化在 profile
- 轮询：schedule_tick 每 30 分钟：登录保活 + 抓发布列表（内容管理）+ 抓新评论（评论管理，默认最新作品）
- 注入：context_inject 注入「你的抖音账号」段落（账号概况 + 最新作品数据 + 未回复评论）
- 隔离：全部操作 try/except；未登录/浏览器异常只降级，不影响主链路；数据仅写 douyin_* 表

校准记录（2026-08-09 真机实测）：
- 内容管理: https://creator.douyin.com/creator-micro/content/manage
  卡片容器 [class^="video-card-"]（含 video-card-v2-）；标题 [class^="info-title-text-"]；
  时间 [class^="info-time-"]；状态 [class^="info-status-"]；指标 [class^="metric-item-"]；
  封面 [class^="video-card-cover-"] 内文本「N张」
- 评论管理: https://creator.douyin.com/creator-micro/data/following/comment（旧 /data/comment 会 302 跳转到关注列表）
  评论项 [class^="cmt-li-"]；名字 [class^="cmt-name-"]；时间 [class^="cmt-time-"]；文本 [class^="cmt-text-"]；
  作者标签 [class^="cmt-label-"]（作者自己的评论/回复，跳过不视为粉丝新评论）；SPA 切作品 URL 不变
- douyin_post_id：页面无作品外链，用标题 md5 前 16 位作稳定键（Phase 2 发布时替换为真实 aweme_id）
"""
import asyncio
import concurrent.futures
import hashlib
import json
import math
import random
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import File, Form, UploadFile

from app.plugins import sdk

# #67 拆分：允许同目录兄弟模块互相导入（插件 dir 非 package，loader 以 ai_plugin_* 单文件加载）。
# browser.py / publish.py / comments.py / content.py / music.py 提供 @author 拆分后的能力；
# 纯函数（MUSIC_MOODS/_de_ai/内容类型轮换/人味指令）模块化以复用并便于单测。
import os as _os
import sys as _sys

_PLUGIN_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _PLUGIN_DIR not in _sys.path:
    _sys.path.insert(0, _PLUGIN_DIR)

from music import (  # noqa: E402
    MUSIC_MOODS,
    normalize_music_mood,
    parse_music_mood,
    pick_music_mood,
    _select_music as _select_music,
)
from content import (  # noqa: E402
    _de_ai,
    content_type_hint,
    humanize_image_prompt,
    humanize_reply_prompt,
    pick_content_type,
)
from publish import _sync_publish_video  # noqa: E402
from comments import (  # noqa: E402
    _sync_reply_comment_v2,
    _sync_reply_comment_dom,
    _get_cached_comment_ids,
)

_PROFILE_DIR = Path(__file__).resolve().parents[3] / "backend" / "data" / "douyin_profile"
_SCREENSHOT_DIR = _PROFILE_DIR / "screenshots"
_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_BIND_TIMEOUT_S = 300
_DOUYIN_HOME = "https://www.douyin.com"
_CREATOR_HOME = "https://creator.douyin.com/creator-micro/home"
_CONTENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
_COMMENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/data/following/comment"

# Playwright sync API 必须运行在无 running-loop 的线程（to_thread 会复制 context 导致误报），
# 单 worker 同时天然串行化浏览器操作，避免并发抢同一 profile。
_PLAYWRIGHT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-pw")

async def _run_sync(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_PLAYWRIGHT_EXECUTOR, func, *args)

_last_poll_ts: float = 0.0
_last_flush_ts: float = 0.0
_last_note_fetch_ts: float = 0.0  # 图文抓取节流（≥5 分钟 1 次）
_last_inject_ts: float = 0.0
_last_status: dict = {"bound": False, "logged_in": False, "message": "未初始化"}

_JS_FETCH_POSTS = """
() => {
    const cards = Array.from(document.querySelectorAll('[class^="video-card-"]')).filter(
        e => /video-card-v2-/.test(e.className || '')
    );
    return cards.map(card => {
        const q = sel => { const e = card.querySelector(sel); return e ? e.innerText.trim() : ''; };
        const cover = card.querySelector('[class^="video-card-cover-"]');
        const imgMatch = cover ? (cover.innerText.match(/(\\d+)\\s*张/) || []) : [];
        const stats = {};
        card.querySelectorAll('[class^="metric-item-"]').forEach(m => {
            const t = (m.innerText || '').trim();
            const mm = t.match(/^(\\S+)\\s+(.+)$/);
            if (mm) stats[mm[1]] = mm[2];
        });
        return {
            title: q('[class^="info-title-text-"]'),
            time: q('[class^="info-time-"]'),
            status: q('[class^="info-status-"]'),
            img_count: imgMatch.length ? imgMatch[1] : '',
            pinned: !!card.querySelector('[class*="badge-top-"]'),
            stats: stats,
        };
    });
}
"""

_JS_FETCH_COMMENTS = """
() => {
    const titleEl = document.querySelector('[class^="info-title-text-"]');
    const postTitle = titleEl ? titleEl.innerText.trim() : '';
    const comments = Array.from(document.querySelectorAll('[class^="cmt-li-"]')).map(li => {
        const q = sel => { const e = li.querySelector(sel); return e ? e.innerText.trim() : ''; };
        const name = q('[class^="cmt-name-"]').replace(/作者/g, '').trim();
        const labels = Array.from(li.querySelectorAll('[class^="cmt-label-"]')).map(e => e.innerText.trim());
        return {
            name: name,
            time: q('[class^="cmt-time-"]'),
            text: q('[class^="cmt-text-"]'),
            is_author: labels.some(t => t.includes('作者')),
            is_fan: labels.some(t => t.includes('粉丝')),
        };
    });
    return { post_title: postTitle, comments: comments };
}
"""


_UPLOAD_IMAGE_URL = "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"

# 本地违禁词拦截（发布/回复前过滤，命中即拒绝；仅做基础合规兜底，实际以平台审核为准）
_BANNED_WORDS = [
    "贷款", "借贷", "理财", "股票", "基金", "博彩", "赌博", "彩票", "刷单", "返利",
    "代购", "代开发票", "办证", "兼职日结", "裸聊", "色情", "约炮", "迷药", "管制刀具",
    "违禁药", "加微信", "加V", "加QQ", "私聊领", "转账", "汇款", "充值返现",
]


def _check_banned(text: str) -> str:
    """返回命中的第一个违禁词；无命中返回空串"""
    for w in _BANNED_WORDS:
        if w in (text or ""):
            return w
    return ""


def _is_quiet_hours() -> bool:
    """北京时间 0-7 点为深夜静默，不执行任何发布/回复"""
    cn = datetime.now(timezone(timedelta(hours=8)))
    return cn.hour < 7


def _random_execute_at() -> datetime:
    """随机 15-120 分钟后的执行时间（naive UTC）；落在北京时间 0-7 点则顺延到当天 7 点后"""
    delay_min = random.randint(15, 120)
    t = datetime.now(timezone.utc) + timedelta(minutes=delay_min)
    cn = t.astimezone(timezone(timedelta(hours=8)))
    if cn.hour < 7:
        cn = cn.replace(hour=7, minute=random.randint(30, 60), second=0, microsecond=0)
        t = cn.astimezone(timezone.utc).replace(tzinfo=None)
    return t.replace(tzinfo=None)


def _require_approval() -> bool:
    """require_approval 开关：True=人工确认；False=全自动发布"""
    try:
        cfg = sdk.get_config()
        return bool(cfg.get("require_approval", True))
    except Exception:
        return True


def _post_key(title: str) -> str:
    return hashlib.md5((title or "").encode("utf-8")).hexdigest()[:16]


async def _active_char_name() -> str:
    """取白名单第一个角色的名字（抖音回复签名用）；无配置/异常返回空"""
    try:
        cfg = sdk.get_config()
        raw = str(cfg.get("allowed_character_ids", "") or "").strip()
        char_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        if not char_ids:
            return ""
        from app.db.database import async_session_factory
        from app.models.character import AICharacter
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_ids[0])
            return (char.name or "").strip() if char else ""
    except Exception:
        return ""


def _append_sign(content: str, char_name: str) -> str:
    """回复文本末尾追加角色签名「-角色名」（已带或名称为空则不重复）"""
    content = (content or "").strip()
    name = (char_name or "").strip()
    if not content or not name:
        return content
    sig = f"-{name}"
    if content.endswith(sig):
        return content
    return (content + sig)[:1000]


def _is_ai_comment(content: str, char_name: str) -> bool:
    """作者评论是否为 AI 自己发的（以「-角色名」签名结尾）"""
    name = (char_name or "").strip()
    if not name:
        return False
    return (content or "").rstrip().endswith(f"-{name}")


def _parse_publish_time(s: str):
    """'2026年08月07日 19:58' -> naive UTC datetime（页面为北京时间，转 UTC 存储）"""
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{1,2})", s or "")
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    try:
        return datetime(y, mo, d, h, mi) - timedelta(hours=8)
    except ValueError:
        return None


# ================= Playwright 同步封装（外部一律 asyncio.to_thread 调用） =================
# #67 P0 反检测：本地 Edge（channel=msedge）+ add_init_script 内嵌 stealth 补丁（零依赖，
# 不强制 playwright-stealth）+ 反自动化启动参数；人类行为模拟（随机打字/点击延迟、鼠标移动）
# 见下方 _human_typing/_human_wait/_human_tap。
_JS_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
try { delete window.__playwright__; } catch (e) {}
try { delete window.__pw_manual; } catch (e) {}
"""


def _launch(headless: bool):
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


def _human_typing(page, text, delay_ms=(30, 120)) -> None:
    """模拟真人打字：随机 30-120ms 按键延迟。"""
    try:
        page.keyboard.type((text or ""), delay=random.randint(*delay_ms))
    except Exception:
        pass


def _human_wait(page, lo=200, hi=800) -> None:
    """操作前随机人反应延迟（200-800ms）。"""
    try:
        page.wait_for_timeout(random.randint(lo, hi))
    except Exception:
        pass


def _human_tap(page, locator, timeout=5000) -> None:
    """点击前随机延迟 + 鼠标移动到目标附近再点击。"""
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


def _sync_bind() -> dict:
    """弹出可见 Edge 窗口，等待用户扫码登录；成功保存 cookie 到 profile"""
    p, ctx = _launch(headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(_DOUYIN_HOME, timeout=60000)
        sdk.log("请在弹出的抖音页面扫码登录（等待最多 %s 秒）", _BIND_TIMEOUT_S)
        deadline = time.time() + _BIND_TIMEOUT_S
        while time.time() < deadline:
            if _has_login_cookie(ctx):
                _shot(page, "bind_success")
                return {"ok": True, "message": "登录成功，账号已绑定"}
            page.wait_for_timeout(3000)
        _shot(page, "bind_timeout")
        return {"ok": False, "message": "超时未检测到登录，请重试（扫码后页面需保持打开）"}
    except Exception as e:
        sdk.log("绑定失败: %s", e)
        return {"ok": False, "message": f"绑定失败: {e}"}
    finally:
        _close_ctx(p, ctx)


def _fetch_account_name(page) -> str:
    """创作者中心首页提取「账号名（抖音号 xxx）」，失败返回空串"""
    try:
        page.goto(_CREATOR_HOME, timeout=45000)
        page.wait_for_timeout(2000)
        return page.evaluate(
            """
            () => {
                const lines = (document.body.innerText || '').split('\\n').map(l => l.trim()).filter(Boolean);
                const i = lines.findIndex(l => l.startsWith('抖音号'));
                if (i < 0) return '';
                const name = i > 0 ? lines[i - 1] : '';
                const id = lines[i].replace(/^抖音号[:：]/, '').trim();
                return (name + (id ? '（抖音号 ' + id + '）' : '')).slice(0, 60);
            }
            """
        )
    except Exception:
        return ""


def _sync_check_login() -> dict:
    """headless 检查登录态；已登录时抓账号名与抖音号"""
    p, ctx = _launch(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(_DOUYIN_HOME, timeout=45000)
        page.wait_for_timeout(2500)
        logged = _has_login_cookie(ctx)
        name = ""
        if logged:
            try:
                name = _fetch_account_name(page)
            except Exception:
                pass
            if not name:
                try:
                    name = page.title()[:60]
                except Exception:
                    name = ""
        return {"ok": True, "logged_in": logged, "account_name": name}
    except Exception as e:
        return {"ok": False, "logged_in": False, "message": f"检查失败: {e}"}
    finally:
        _close_ctx(p, ctx)


def _fetch_my_posts(page) -> list[dict]:
    """抓内容管理页发布列表（2026-08-16 B：条件等待替代固定 7s，复用调用方 ctx）"""
    try:
        page.goto(_CONTENT_MANAGE_URL, timeout=60000)
        try:
            page.wait_for_selector('[class^="video-card-"]', timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        return page.evaluate(_JS_FETCH_POSTS) or []
    except Exception as e:
        sdk.log("抓取发布列表失败: %s", e)
        return []


def _fetch_all_comments(page, max_posts: int = 5) -> dict:
    """抓评论管理页评论：默认作品 + 下拉里最多 max_posts 个作品的评论（2026-08-16 B：条件等待替代固定 sleep，复用调用方 ctx）"""
    try:
        page.goto(_COMMENT_MANAGE_URL, timeout=60000)
        try:
            page.wait_for_selector('[class^="cmt-li-"]', timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        # 默认作品（页面当前显示）
        default = page.evaluate(_JS_FETCH_COMMENTS) or {"post_title": "", "comments": []}
        results = [{"post_title": default.get("post_title", ""), "comments": default.get("comments", [])}]
        # 打开「选择作品」下拉，收集其余作品标题
        try:
            page.get_by_text("选择作品", exact=True).first.click(timeout=5000)
            page.wait_for_timeout(800)
            titles = page.evaluate(
                """
                () => {
                    const seen = [];
                    for (const e of document.querySelectorAll('[class^="info-title-text-"]')) {
                        const t = (e.innerText || '').trim();
                        if (t && !seen.includes(t)) seen.push(t);
                    }
                    return seen;
                }
                """
            ) or []
        except Exception:
            titles = []
        default_title = results[0]["post_title"]
        rest = [t for t in titles if t != default_title][: max(0, int(max_posts) - 1)]
        for title in rest:
            try:
                # 下拉已关闭则重新打开
                if page.locator("text=作品列表").count() == 0:
                    page.get_by_text("选择作品", exact=True).first.click(timeout=5000)
                    page.wait_for_timeout(800)
                page.get_by_text(title, exact=False).first.click(timeout=5000)
                try:
                    page.wait_for_selector('[class^="cmt-li-"]', timeout=4000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)
                cmt = page.evaluate(_JS_FETCH_COMMENTS) or {"post_title": title, "comments": []}
                results.append({"post_title": cmt.get("post_title", title), "comments": cmt.get("comments", [])})
            except Exception as e:
                sdk.log("切换作品失败 %s: %s", (title or "")[:20], e)
        return {"posts": results}
    except Exception as e:
        sdk.log("抓取评论失败: %s", e)
        return {"posts": []}


def _sync_poll_once(max_posts: int = 5) -> dict:
    """单次轮询（2026-08-16 A：合并冷启动——一次 Edge 启动内完成 登录检查/保活 + 账号名 + 发布列表 + 新评论）"""
    p, ctx = _launch(headless=True)
    try:
        if not _has_login_cookie(ctx):
            return {"ok": True, "logged_in": False, "account_name": "", "posts": [], "comments": {"posts": []}}
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        name = ""
        try:
            name = _fetch_account_name(page)
        except Exception:
            pass
        if not name:
            try:
                name = page.title()[:60]
            except Exception:
                name = ""
        posts = _fetch_my_posts(page)
        cmt = _fetch_all_comments(page, int(max_posts))
        return {"ok": True, "logged_in": True, "account_name": name, "posts": posts, "comments": cmt}
    except Exception as e:
        sdk.log("douyin 轮询异常: %s", e)
        return {"ok": False, "logged_in": False, "account_name": "", "posts": [], "comments": {"posts": []}}
    finally:
        _close_ctx(p, ctx)


def _resolve_upload_path(url: str) -> Path | None:
    """把 /uploads/... 相对 URL 转为服务器绝对路径（不存在/路径异常返回 None）"""
    try:
        rel = (url or "").removeprefix("/uploads/").lstrip("/")
        if not rel or ".." in rel or rel.startswith(("/", "\\")):
            return None
        from app.config import settings
        p = settings.PROJECT_ROOT / "data" / "uploads" / rel
        return p if p.is_file() else None
    except Exception:
        return None


def _close_draft_modal(page) -> None:
    """关闭「你还有上次未发布的图文，是否继续编辑？」弹窗（点「放弃」）；页面刷新后会再弹，可重复调用"""
    try:
        _b = page.evaluate("() => document.body.innerText || ''")
        if "是否继续编辑" in _b or "继续编辑" in _b:
            # 优先 JS 直接点「放弃」可点击元素（Playwright 文本点击可能被遮罩/可见性检查干扰）
            _ok = page.evaluate(
                """() => {
                    const els = Array.from(document.querySelectorAll("button, [role=button], span, div"));
                    const hits = els.filter(e => (e.innerText || "").trim() === "放弃" && e.offsetParent !== null);
                    if (hits.length) { hits[hits.length - 1].click(); return true; }
                    return false;
                }"""
            )
            if not _ok:
                page.get_by_text("放弃", exact=True).first.click(timeout=4000)
            page.wait_for_timeout(2500)
            _b2 = page.evaluate("() => document.body.innerText || ''")
            sdk.log("关闭草稿弹窗: 点击放弃 ok=%s 残留=%s", _ok, "是" if ("是否继续编辑" in _b2 or "继续编辑" in _b2) else "否")
    except Exception:
        pass


def _has_sms_verify_modal(page) -> bool:
    """检测抖音发布/回复时的短信验证码弹窗（风控：需人工输入短信验证码，AI 无法代收）"""
    try:
        _b = page.evaluate("() => document.body.innerText || ''")
        return ("接收短信验证码" in _b) and ("请输入验证码" in _b)
    except Exception:
        return False


def _close_sms_verify_modal(page) -> bool:
    """退出短信验证码弹窗（点「返回/取消」），保留表单内容不提交；返回是否已退出"""
    try:
        for _txt in ("返回", "取消"):
            _ok = page.evaluate(
                """(txt) => {
                    const els = Array.from(document.querySelectorAll("button, [role=button], span, div, p"));
                    const hits = els.filter(e => e.offsetParent !== null && (e.innerText || "").trim() === txt && e.getBoundingClientRect().height < 60);
                    if (hits.length) { hits[hits.length - 1].click(); return true; }
                    return false;
                }""",
                _txt,
            )
            if _ok:
                page.wait_for_timeout(1000)
                return True
    except Exception:
        pass
    return False


def _click_publish(page) -> bool:
    """真实点击抖音发布表单的「发布」提交按钮（避免 JS click 对 Vue/React 无效；避开导航入口）。

    2026-08-10 实测：发布表单按钮区为「发布(primary-cECiOJ)/暂存离开(cancel fixed)/清空并重新上传」，
    旧选择器 button[class*=fixed-] 的 .last 会命中 DOM 更靠后的「暂存离开」，导致表单被暂存、
    下次进来自动弹「你还有上次未发布的图文」草稿窗循环拦截；因此必须优先按文本精确匹配「发布」。"""
    for _ in range(3):
        try:
            loc = page.get_by_role("button", name="发布", exact=True)
            if loc.count() > 0:
                el = loc.last
                if el.is_enabled(timeout=2000):
                    txt = (el.inner_text(timeout=2000) or "").strip()
                    el.scroll_into_view_if_needed(timeout=4000)
                    el.click(timeout=5000)
                    sdk.log("点击发布按钮: 文本=%r", txt)
                    return True
        except Exception:
            pass
        try:
            # 兜底：遍历主样式按钮，只点文本恰为「发布」的（排除「暂存离开」/导航入口）
            btns = page.locator("button[class*=primary-]")
            for i in range(btns.count()):
                el = btns.nth(i)
                txt = (el.inner_text(timeout=1500) or "").strip()
                if txt == "发布" and el.is_enabled(timeout=1500):
                    el.scroll_into_view_if_needed(timeout=4000)
                    el.click(timeout=5000)
                    sdk.log("点击发布按钮(primary兜底): 文本=%r", txt)
                    return True
        except Exception:
            pass
        page.wait_for_timeout(2000)
    return False


def _clear_publish_form_cache(page) -> None:
    """清除抖音发布表单缓存 localStorage publish_form_cache:*（「你还有上次未发布的图文」弹窗根因；
    进入上传页/发布前调用，避免旧草稿弹窗循环拦截）"""
    try:
        page.evaluate(
            """() => {
                const keys = [];
                for (let i = localStorage.length - 1; i >= 0; i--) {
                    const k = localStorage.key(i) || '';
                    if (k.indexOf('publish_form_cache') === 0) { keys.push(k); localStorage.removeItem(k); }
                }
                return keys;
            }"""
        )
    except Exception:
        pass


def _fill_image_form(page, images: list[str], title: str, desc: str) -> None:
    """填充抖音图文上传表单：上传图片（已有预览则跳过）→ 填标题/描述"""
    try:
        _b = page.evaluate("() => document.body.innerText || ''")
        _has_upload = ("点击上传" in _b) or ("直接将图片文件拖入此区域" in _b) or ("添加作品标题" in _b)
        existing = [str(_resolve_upload_path(im)) for im in images if _resolve_upload_path(im) is not None]
        if existing and _has_upload:
            # input[type=file] 上传后会被消费移除；无 file input 说明预览仍在，跳过重传
            _fi = page.locator("input[type=file]").first
            if _fi.count() > 0:
                _fi.set_input_files(existing)
                page.wait_for_timeout(12000)
    except Exception:
        pass
    if title:
        try:
            page.locator('input[placeholder="添加作品标题"]').first.fill((title or "")[:20])
            page.wait_for_timeout(600)
        except Exception:
            pass
    if desc:
        try:
            # 抖音上传页描述框是 contenteditable（textarea 定位不到会静默丢正文）
            _ed = page.locator("[contenteditable=true]").first
            _ed.click(timeout=5000)
            page.keyboard.type((desc or "")[:1000], delay=8)
            page.wait_for_timeout(600)
        except Exception:
            try:
                page.locator("textarea").first.fill((desc or "")[:1000])
                page.wait_for_timeout(600)
            except Exception:
                pass


def _sync_publish_image(images: list[str], title: str, desc: str, music_keyword: str = "") -> dict:
    """发布图文（Phase 2 + #67 音乐）：上传图片 → 填标题/描述 → 选音乐(可选) → 点发布；返回 {ok, message}"""
    p, ctx = _launch(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _post_id = {"id": None}
        def _on_create_v2(resp):
            if "create_v2" in resp.url:
                try:
                    _d = json.loads(resp.text() or "{}")
                    if _d.get("item_id"):
                        _post_id["id"] = str(_d["item_id"])
                except Exception:
                    pass
        page.on("response", _on_create_v2)
        page.goto(_UPLOAD_IMAGE_URL, timeout=60000)
        page.wait_for_timeout(6000)
        if not _has_login_cookie(ctx):
            return {"ok": False, "message": "未登录，无法发布"}
        _clear_publish_form_cache(page)
        _close_draft_modal(page)
        _fill_image_form(page, images, title, desc)
        # #67：图文也可选音乐（AI 情绪关键词）
        if music_keyword:
            _select_music(page, music_keyword)
        _close_draft_modal(page)
        try:
            _up_info = page.evaluate(
                """() => ({
                    img_count: Array.from(document.querySelectorAll("img")).filter(i => i.offsetParent !== null && !(i.src || "").includes("data:image/svg")).length,
                    file_inputs: Array.from(document.querySelectorAll("input[type=file]")).filter(i => i.offsetParent !== null).length,
                    btns: Array.from(document.querySelectorAll("button")).filter(b => b.offsetParent !== null).map(b => (b.innerText || "").trim().slice(0, 16)).filter(t => t),
                })"""
            )
            sdk.log("发布页图片状态: %s", json.dumps(_up_info, ensure_ascii=False)[:700])
        except Exception:
            pass
        url, body = "", ""
        for _attempt in range(3):
            _clear_publish_form_cache(page)
            _close_draft_modal(page)
            _click_publish(page)
            page.wait_for_timeout(4000)
            _modal_hit = False
            for _ in range(6):
                page.wait_for_timeout(3000)
                try:
                    url = page.url or ""
                    body = page.evaluate("() => (document.body.innerText || '').slice(0, 1500)")
                except Exception:
                    pass
                if _has_sms_verify_modal(page):
                    _close_sms_verify_modal(page)
                    _shot(page, "publish_need_sms_verify")
                    return {"ok": False, "need_manual": True, "message": "抖音发布要求短信验证码（风控），需人工完成验证后发布；发布内容已保留在编辑页"}
                if any(kw in body for kw in ("发布成功", "作品已发布", "发布完成")):
                    return {"ok": True, "message": "发布成功", "post_id": _post_id["id"] or _post_key(title or "")}
                if "content/manage" in url:
                    return {"ok": True, "message": "已提交发布（已跳转内容管理）", "post_id": _post_id["id"] or _post_key(title or "")}
                if "是否继续编辑" in body or "继续编辑" in body:
                    # 草稿弹窗拦截发布：清表单缓存 + 点「放弃」丢弃残留草稿（点「继续编辑」载入旧草稿会循环拦截），
                    # 再重填表单重试发布；若重填失败则放弃本次，标记 failed 由人工处理
                    _modal_hit = True
                    try:
                        _clear_publish_form_cache(page)
                        page.get_by_text("放弃", exact=True).first.click(timeout=4000)
                        page.wait_for_timeout(2500)
                        _clear_publish_form_cache(page)
                        _close_draft_modal(page)
                        _fill_image_form(page, images, title, desc)
                        sdk.log("发布被草稿弹窗拦截（第 %s 次），清缓存并放弃残留草稿后重试", _attempt + 1)
                    except Exception:
                        _close_draft_modal(page)
                    break
            if _modal_hit:
                continue
            if _UPLOAD_IMAGE_URL not in url and "creator-micro" in url and "/content/" not in url:
                # 离开发布表单（可能跳到首页等）：主动去内容管理核实作品是否上架
                page.goto(_CONTENT_MANAGE_URL, timeout=30000)
                page.wait_for_timeout(5000)
                try:
                    titles = page.evaluate(
                        """() => Array.from(document.querySelectorAll('[class^="info-title-text-"]')).slice(0, 8).map(e => (e.innerText || '').trim())"""
                    )
                except Exception:
                    titles = []
                head = (title or "")[:10].strip()
                if titles and head and any(head in t for t in titles):
                    return {"ok": True, "message": "已提交发布（内容管理可见）", "post_id": _post_id["id"] or _post_key(title or "")}
                return {"ok": False, "message": "发布疑似未成功（内容管理未出现作品）"}
            break
        sdk.log("发布最终失败页面尾部: %s", (body[-300:].replace("\n", " ") if body else ""))
        return {"ok": False, "message": "发布后未确认成功（请人工检查）：" + body[:120]}
    except Exception as e:
        sdk.log("发布图文失败: %s", e)
        return {"ok": False, "message": f"发布失败: {e}"}
    finally:
        _close_ctx(p, ctx)



# ================= Phase 3.5：抖音图文感知（计划 15：AI 可看抖音图文作品，VLM 理解） =================
def _sync_fetch_note(aweme_id: str) -> dict:
    """打开抖音图文作品页，监听 aweme/post 响应拿 desc+图片 URL，下载图片到本地；返回 {ok, ...}"""
    p, ctx = _launch(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        data = {"desc": "", "author": "", "images": []}

        def _on_resp(resp):
            try:
                if "aweme/post" not in resp.url:
                    return
                t = resp.text() or ""
                if not t.startswith("{"):
                    return
                j = json.loads(t)
                aw = (j.get("aweme_list") or [{}])[0]
                if not aw.get("aweme_id"):
                    return
                data["desc"] = str(aw.get("desc") or "")[:1000]
                data["author"] = ((aw.get("author") or {}).get("nickname") or "")[:100]
                imgs = []
                for it in (aw.get("images") or [])[:3]:
                    ul = it.get("url_list") or []
                    url = str(ul[-1]) if ul else str(it.get("download_url") or "")
                    if url.startswith("http"):
                        imgs.append(url)
                data["images"] = imgs
            except Exception:
                pass

        page.on("response", _on_resp)
        page.goto(f"https://www.douyin.com/note/{aweme_id}", timeout=45000)
        page.wait_for_timeout(15000)
        if not data["desc"]:
            return {"ok": False, "message": "未拿到作品数据（接口结构变化或未渲染）"}
        local_paths = []
        for i, url in enumerate(data["images"][:3]):
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"})
                raw = urllib.request.urlopen(req, timeout=20).read()
                if len(raw) < 500:
                    continue
                path = _SCREENSHOT_DIR / f"note_{aweme_id}_{i}.jpg"
                path.write_bytes(raw)
                local_paths.append(str(path))
            except Exception:
                continue
        return {"ok": True, "desc": data["desc"], "author": data["author"], "images": data["images"], "local_paths": local_paths}
    except Exception as e:
        sdk.log("抓取抖音图文失败: %s", e)
        return {"ok": False, "message": f"抓取失败: {e}"}
    finally:
        _close_ctx(p, ctx)


async def _save_viewed_note(aweme_id: str, author: str, desc: str, image_urls: list, image_descs: list) -> None:
    """图文理解结果入库（幂等 upsert）"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinViewedNote
    try:
        async with async_session_factory() as db:
            row = (await db.execute(select(DouyinViewedNote).where(DouyinViewedNote.aweme_id == aweme_id))).scalars().first()
            if row is None:
                db.add(DouyinViewedNote(
                    user_id=1, aweme_id=aweme_id, author=author[:100], desc=desc[:1000],
                    images_urls_json=json.dumps(image_urls, ensure_ascii=False),
                    image_descs_json=json.dumps(image_descs, ensure_ascii=False),
                ))
            else:
                row.author, row.desc = author[:100], desc[:1000]
                row.images_urls_json = json.dumps(image_urls, ensure_ascii=False)
                row.image_descs_json = json.dumps(image_descs, ensure_ascii=False)
            await db.commit()
    except Exception as e:
        sdk.log("保存图文理解失败: %s", e)


async def _recent_viewed_notes(limit: int = 2) -> list[dict]:
    """最近看过的抖音图文（供上下文注入/回复增强）"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinViewedNote
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(select(DouyinViewedNote).order_by(DouyinViewedNote.id.desc()).limit(limit))).scalars().all()
            out = []
            for r in rows:
                try:
                    descs = json.loads(r.image_descs_json or "[]")
                except Exception:
                    descs = []
                out.append({"aweme_id": r.aweme_id, "author": r.author, "desc": r.desc, "image_descs": descs})
            return out
    except Exception:
        return []


async def fetch_note_content(aweme_id: str) -> dict:
    """抓取一条抖音图文 + VLM 理解图片 + 入库；返回 {ok, note}"""
    res = await _run_sync(_sync_fetch_note, aweme_id)
    if not res.get("ok"):
        return res
    from app.services.image_understanding_service import describe_image
    descs = []
    for path in res.get("local_paths") or []:
        try:
            d = await describe_image(path)
            if d:
                descs.append(d)
        except Exception:
            continue
    await _save_viewed_note(aweme_id, res.get("author", ""), res.get("desc", ""), res.get("images") or [], descs)
    note = {
        "aweme_id": aweme_id,
        "author": (res.get("author") or "")[:100],
        "desc": (res.get("desc") or "")[:200],
        "image_descs": [d[:200] for d in descs],
    }
    sdk.log("抖音图文理解完成: %s 图片=%s", aweme_id, len(descs))
    return {"ok": True, "note": note}


def _sync_reply_comment(post_title: str, commenter: str, reply_text: str) -> dict:
    """回复评论（Phase 2）：切到目标作品 → 点评论「回复」→ 输入 → 发送；返回 {ok, message}"""
    p, ctx = _launch(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(_COMMENT_MANAGE_URL, timeout=60000)
        page.wait_for_timeout(7000)
        if not _has_login_cookie(ctx):
            return {"ok": False, "message": "未登录，无法回复"}
        if post_title:
            current = ""
            try:
                current = page.evaluate(
                    """() => { const e = document.querySelector('[class^="info-title-text-"]'); return e ? e.innerText.trim() : ''; }"""
                ) or ""
            except Exception:
                pass
            if post_title != current:
                try:
                    page.get_by_text("选择作品", exact=True).first.click(timeout=5000)
                    page.wait_for_timeout(1500)
                    page.get_by_text(post_title, exact=False).first.click(timeout=5000)
                    page.wait_for_timeout(5000)
                except Exception as e:
                    return {"ok": False, "message": f"切换作品失败: {e}"}
        clicked = page.evaluate(
            """
            (cname) => {
                const lis = Array.from(document.querySelectorAll('[class^="cmt-li-"]'));
                for (const li of lis) {
                    if (li.querySelector('[class^="cmt-label-"]')) continue;
                    const nameEl = li.querySelector('[class^="cmt-name-"]');
                    const nm = nameEl ? (nameEl.innerText || '').trim().replace(/作者/g, '') : '';
                    if (nm.includes(cname)) {
                        const btns = Array.from(li.querySelectorAll('*')).filter(e => /^回复/.test((e.innerText || '').trim()));
                        if (btns.length) { btns[btns.length - 1].click(); return true; }
                    }
                }
                return false;
            }
            """,
            commenter or "",
        )
        if not clicked:
            return {"ok": False, "message": f"未找到评论者「{commenter}」的评论"}
        page.wait_for_timeout(2500)
        try:
            page.locator('textarea[placeholder^="回复"]').first.fill((reply_text or "")[:500])
            page.wait_for_timeout(600)
        except Exception as e:
            return {"ok": False, "message": f"回复输入框定位失败: {e}"}
        sent = page.evaluate(
            """
            () => {
                const cancel = Array.from(document.querySelectorAll('*')).find(e => e.children.length === 0 && /^取消$/.test((e.innerText || '').trim()));
                if (!cancel) return 'no_cancel';
                let p = cancel.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    const s = Array.from(p.querySelectorAll('*')).find(e => e.children.length === 0 && /^发送$/.test((e.innerText || '').trim()));
                    if (s) { s.click(); return 'clicked'; }
                    p = p.parentElement;
                }
                return 'no_send';
            }
            """
        )
        if sent != "clicked":
            return {"ok": False, "message": f"发送按钮定位失败: {sent}"}
        page.wait_for_timeout(3000)
        if _has_sms_verify_modal(page):
            _close_sms_verify_modal(page)
            _shot(page, "reply_need_sms_verify")
            return {"ok": False, "need_manual": True, "message": "抖音回复要求短信验证码（风控），需人工介入；回复内容已保留"}
        return {"ok": True, "message": "回复已发送"}
    except Exception as e:
        sdk.log("回复评论失败: %s", e)
        return {"ok": False, "message": f"回复失败: {e}"}
    finally:
        _close_ctx(p, ctx)


# ================= DB 操作（异步） =================
async def _get_account() -> dict:
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinAccount
    async with async_session_factory() as db:
        row = (await db.execute(select(DouyinAccount).order_by(DouyinAccount.id.asc()).limit(1))).scalar_one_or_none()
        if row is None:
            return {"bound": False, "logged_in": False, "account_name": ""}
        return {"bound": bool(row.bound), "logged_in": bool(row.logged_in), "account_name": row.account_name or ""}


async def _upsert_account(state: dict) -> None:
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinAccount
    async with async_session_factory() as db:
        row = (await db.execute(select(DouyinAccount).order_by(DouyinAccount.id.asc()).limit(1))).scalar_one_or_none()
        if row is None:
            db.add(DouyinAccount(
                user_id=1, account_name=state.get("account_name", ""),
                bound=state.get("bound", False), logged_in=state.get("logged_in", False),
                last_check_at=datetime.now(timezone.utc),
            ))
        else:
            row.bound = state.get("bound", row.bound)
            row.logged_in = state.get("logged_in", row.logged_in)
            if state.get("account_name"):
                row.account_name = state["account_name"]
            row.last_check_at = datetime.now(timezone.utc)
        await db.commit()


_ensure_source_col_done = False


async def _ensure_douyin_schema() -> None:
    """幂等确保 douyin_posts.source 列存在（auto=AI自主发布，AI 图文日额度只统计 auto）"""
    global _ensure_source_col_done
    if _ensure_source_col_done:
        return
    from sqlalchemy import text
    from app.db.database import async_session_factory
    try:
        async with async_session_factory() as db:
            ncols = [c[1] for c in (await db.execute(text("PRAGMA table_info(douyin_viewed_notes)"))).fetchall()]
            if not ncols:
                await db.execute(text(
                    "CREATE TABLE IF NOT EXISTS douyin_viewed_notes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER DEFAULT 1, "
                    "aweme_id VARCHAR(64) NOT NULL UNIQUE, author VARCHAR(100) DEFAULT '', "
                    "desc VARCHAR(1000) DEFAULT '', images_urls_json TEXT DEFAULT '[]', "
                    "image_descs_json TEXT DEFAULT '[]', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
                ))
            cols = (await db.execute(text("PRAGMA table_info(douyin_posts)"))).fetchall()
            if "source" not in [c[1] for c in cols]:
                await db.execute(text("ALTER TABLE douyin_posts ADD COLUMN source VARCHAR(10) DEFAULT 'manual'"))
            ccols = (await db.execute(text("PRAGMA table_info(douyin_comments)"))).fetchall()
            cnames = [c[1] for c in ccols]
            if "is_author" not in cnames:
                await db.execute(text("ALTER TABLE douyin_comments ADD COLUMN is_author BOOLEAN DEFAULT 0"))
            if "author_role" not in cnames:
                await db.execute(text("ALTER TABLE douyin_comments ADD COLUMN author_role VARCHAR(10) DEFAULT ''"))
            if "mentioned_at" not in cnames:
                await db.execute(text("ALTER TABLE douyin_comments ADD COLUMN mentioned_at DATETIME"))
            # #67（2026-08-27）：评论真实 aweme_id / comment_id（评论 API 拦截方案需真实 ID）
            if "aweme_id" not in cnames:
                await db.execute(text("ALTER TABLE douyin_comments ADD COLUMN aweme_id VARCHAR(64) DEFAULT ''"))
            if "comment_id" not in cnames:
                await db.execute(text("ALTER TABLE douyin_comments ADD COLUMN comment_id VARCHAR(64) DEFAULT ''"))
            pcols = (await db.execute(text("PRAGMA table_info(douyin_pending)"))).fetchall()
            pnames = [c[1] for c in pcols]
            # #67（2026-08-27）：pending 队列支持音乐情绪 / 视频路径 / 发布类型
            if "music_mood" not in pnames:
                await db.execute(text("ALTER TABLE douyin_pending ADD COLUMN music_mood VARCHAR(20) DEFAULT ''"))
            if "video_path" not in pnames:
                await db.execute(text("ALTER TABLE douyin_pending ADD COLUMN video_path VARCHAR(500) DEFAULT ''"))
            if "post_type" not in pnames:
                await db.execute(text("ALTER TABLE douyin_pending ADD COLUMN post_type VARCHAR(10) DEFAULT 'image'"))
            await db.commit()
        _ensure_source_col_done = True
    except Exception:
        pass


async def _upsert_posts(posts: list[dict]) -> int:
    """发布列表按 post_key 幂等 upsert，返回新增数"""
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPost
    if not posts:
        return 0
    added = 0
    async with async_session_factory() as db:
        existing = {r.douyin_post_id: r for r in (await db.execute(select(DouyinPost))).scalars().all()}
        for post in posts:
            key = _post_key(post.get("title", ""))
            if not key:
                continue
            stats = post.get("stats") or {}
            try:
                stats_json = json.dumps(stats, ensure_ascii=False)
            except Exception:
                stats_json = "{}"
            pub = _parse_publish_time(post.get("time", ""))
            row = existing.get(key)
            if row is None:
                db.add(DouyinPost(
                    user_id=1, douyin_post_id=key, title=(post.get("title", "") or "")[:500],
                    post_type="image", stats_json=stats_json, published_at=pub,
                ))
                added += 1
            else:
                row.title = (post.get("title", "") or row.title)[:500]
                row.stats_json = stats_json
                if pub:
                    row.published_at = pub
        await db.commit()
    return added


async def _upsert_comments_one(post_title: str, comments: list[dict]) -> int:
    """评论按 (user_id, douyin_post_id, content) 唯一约束去重写入；作者评论也入库（author_role 区分 AI/账号主人），返回新增数。
    新增的非作者评论同步写社交记忆（social_memories，2026-08-10 社交交互层 v2）。"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinComment
    if not comments:
        return 0
    key = _post_key(post_title or "")
    char_name = await _active_char_name()
    values = []
    for c in comments:
        content = (c.get("text") or "").strip()
        commenter = (c.get("name") or "").strip()
        if not content:
            continue
        is_author = bool(c.get("is_author"))
        # 作者评论（账号自己发的）也落库：按角色签名区分 AI 发的与账号主人发的
        author_role = ""
        if is_author:
            author_role = "ai" if _is_ai_comment(content, char_name) else "user"
        values.append({
            "user_id": 1,
            "douyin_post_id": key,
            "commenter": commenter[:100],
            "content": content[:1000],
            "commented_at": None,
            "is_fan": bool(c.get("is_fan")),
            "is_author": is_author,
            "author_role": author_role,
            "replied": False,
        })
    if not values:
        return 0
    # 过滤出真正的新增行（既有 content 不重复计数/写社交记忆）
    from sqlalchemy import select
    async with async_session_factory() as db:
        existing = set((await db.execute(
            select(DouyinComment.content).where(DouyinComment.douyin_post_id == key)
        )).scalars().all())
    fresh = [v for v in values if v["content"] not in existing]
    if not fresh:
        return 0
    added = 0
    async with async_session_factory() as db:
        stmt = sqlite_insert(DouyinComment).values(fresh).on_conflict_do_nothing(
            index_elements=["user_id", "douyin_post_id", "content"]
        )
        result = await db.execute(stmt)
        await db.commit()
        added = result.rowcount or 0
    # 社交记忆：只对新增的非作者评论 upsert（粉丝→follower / 非粉丝→stranger）
    try:
        from app.services.social_memory_service import upsert_social_memory
        for v in fresh:
            if v.get("is_author"):
                continue
            await upsert_social_memory(
                "douyin", v["commenter"], nickname=v["commenter"],
                relationship_level="follower" if v.get("is_fan") else "stranger",
            )
    except Exception as e:
        sdk.log("社交记忆写入失败: %s", e)
    return added


async def _upsert_comments(posts: list[dict]) -> int:
    """遍历多个作品的评论并落库，返回新增总数"""
    total = 0
    for item in posts or []:
        total += await _upsert_comments_one(item.get("post_title", ""), item.get("comments", []))
    return total


async def _recent_posts(limit: int = 2) -> list[dict]:
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPost
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(DouyinPost).order_by(DouyinPost.published_at.desc().nullslast()).limit(limit)
        )).scalars().all()
        out = []
        for r in rows:
            stats = {}
            try:
                stats = json.loads(r.stats_json or "{}")
            except Exception:
                pass
            out.append({"title": r.title or "", "stats": stats})
        return out


async def _recent_unreplied_comments(limit: int = 5, exclude_mentioned: bool = False) -> list[dict]:
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinComment
    async with async_session_factory() as db:
        _q = select(DouyinComment).where(
            DouyinComment.replied == False, DouyinComment.is_author.isnot(True),
        )
        if exclude_mentioned:
            _q = _q.where(DouyinComment.mentioned_at.is_(None))  # 已提及过的评论不再重复提及（2026-08-15）
        rows = (await db.execute(
            _q.order_by(DouyinComment.created_at.desc()).limit(limit)
        )).scalars().all()
        out = []
        for r in rows:
            item = {"commenter": r.commenter, "content": r.content, "is_fan": bool(r.is_fan), "post_key": r.douyin_post_id or ""}
            try:
                from app.db.database import async_session_factory as _asf2
                from sqlalchemy import select as _sel2
                from app.models.douyin import DouyinPost as _DP2
                async with _asf2() as db:
                    post = (await db.execute(_sel2(_DP2).where(_DP2.douyin_post_id == r.douyin_post_id).limit(1))).scalars().first()
                    if post:
                        item["post_title"] = post.title or ""
            except Exception:
                pass
            out.append(item)
        return out


async def _recent_author_comments(limit: int = 6) -> list[dict]:
    """最近作者评论（账号自己发的：AI 或账号主人），按 id 升序成对话链；返回 {author_role, commenter, content, post_title}"""
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.models.douyin import DouyinComment, DouyinPost
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(DouyinComment)
                .where(DouyinComment.is_author == True)
                .order_by(DouyinComment.id.desc())
                .limit(limit)
            )).scalars().all()
            rows = list(reversed(rows))
            titles = {}
            post_ids = {r.douyin_post_id for r in rows if r.douyin_post_id}
            if post_ids:
                posts = (await db.execute(
                    select(DouyinPost).where(DouyinPost.douyin_post_id.in_(post_ids))
                )).scalars().all()
                titles = {pp.douyin_post_id: pp.title or "" for pp in posts}
            return [
                {
                    "author_role": r.author_role or "", "commenter": r.commenter,
                    "content": r.content or "", "post_title": titles.get(r.douyin_post_id, ""),
                }
                for r in rows
            ]
    except Exception:
        return []


async def _comment_is_fan(post_title: str, commenter: str) -> bool:
    """按「作品标题 + 评论者」定位最新一条目标评论的粉丝标记，找不到默认 False"""
    try:
        from app.db.database import async_session_factory
        from sqlalchemy import select
        from app.models.douyin import DouyinComment
        async with async_session_factory() as db:
            row = (await db.execute(
                select(DouyinComment)
                .where(DouyinComment.douyin_post_id == _post_key(post_title or ""),
                       DouyinComment.commenter == commenter)
                .order_by(DouyinComment.id.desc())
                .limit(1)
            )).scalars().first()
            return bool(row and row.is_fan)
    except Exception:
        return False


async def _latest_comment(post_title: str, commenter: str) -> dict | None:
    """按「作品标题 + 评论者」定位最新一条未回复评论，返回 {content, is_fan} 或 None"""
    try:
        from app.db.database import async_session_factory
        from sqlalchemy import select
        from app.models.douyin import DouyinComment
        async with async_session_factory() as db:
            row = (await db.execute(
                select(DouyinComment)
                .where(DouyinComment.douyin_post_id == _post_key(post_title or ""),
                       DouyinComment.commenter == commenter)
                .order_by(DouyinComment.id.desc())
                .limit(1)
            )).scalars().first()
            if row is None:
                return None
            return {"content": row.content or "", "is_fan": bool(row.is_fan)}
    except Exception:
        return None


async def _persona_context(character_id: int | None = None) -> str:
    """组装「角色自我」语境（与私聊同源）：角色人设 + 主链路人格块（关系/状态/八维/情绪/话题/身份画像）+ 记忆 top3 + 账号内容。
    适用于白名单任意角色（character_id 指定，默认第一个）；抖音数据仍隔离（不写记忆库）"""
    cfg = sdk.get_config()
    raw = str(cfg.get("allowed_character_ids", "") or "").strip()
    char_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    if character_id is not None and character_id in char_ids:
        cid = character_id
    elif char_ids:
        cid = char_ids[0]
    else:
        cid = None
    parts = []
    if cid is not None:
        user_id = 1
        try:
            from app.db.database import async_session_factory
            from app.models.character import AICharacter
            from app.models.user import User
            async with async_session_factory() as db:
                char = await db.get(AICharacter, cid)
                _user = await db.get(User, char.user_id or 1) if char is not None else None
            if char is not None:
                user_id = char.user_id or 1
                desc = "；".join(x for x in [char.personality, char.bio, char.chat_style] if x)
                parts.append(f"你是角色「{char.name}」" + (f"：{desc}" if desc else ""))
                if _user is not None and _user.gender in ("male", "female"):
                    parts.append(f"账号主人（用户）性别：{'男' if _user.gender == 'male' else '女'}")
        except Exception:
            pass
        # 主链路人格块（与私聊同源：关系/当前状态/八维感受/情绪/身份画像/进行中话题）
        try:
            from app.agent.persona import assemble_persona_context
            pc = await assemble_persona_context(cid, user_id, platform="douyin")
            # 公开平台（douyin）：注入公开裁剪约束文本，不注入「你与用户的关系」私密标签
            pp_text = (pc.get("platform_profile_text") or "").strip()
            if pp_text:
                parts.append(pp_text)
            else:
                rel = (pc.get("relationship") or "普通朋友").strip()
                parts.append(f"你与用户的关系：{rel}")
            for _label, _key in (
                ("你当前的状态", "current_status"),
                ("你的八维感受", "character_feelings"),
                ("最近的情绪事件", "recent_emotion"),
                ("进行中的话题", "active_topics"),
            ):
                _v = (pc.get(_key) or "").strip()
                if _v and _v not in ("无", "普通朋友", "你们正在聊天"):
                    parts.append(f"{_label}：{_v[:120]}")
        except Exception:
            pass
        # 公开平台（douyin，memory_access=limited）：注入「公开安全记忆」——
        # 排除身份画像（identity）与含用户姓名的私密内容，保留中性记忆（共同兴趣/角色感悟）；
        # 收紧开关 platform_profiles.memory_restrict：
        #   off=现状；relationship=额外排除 relationship 子类型（表白/金钱等无姓名但私密内容）
        # 评论回复与图文创作共用（2026-08-10 用户拍板；收紧开关 2026-08-12）
        try:
            from app.db.database import async_session_factory
            from sqlalchemy import select
            from app.models.memory import Memory
            from app.models.social import PlatformProfile
            _restrict = "off"
            async with async_session_factory() as db:
                rows = (await db.execute(
                    select(Memory)
                    .where(Memory.character_id == cid, Memory.is_archived == False)
                    .order_by(Memory.importance.desc(), Memory.created_at.desc())
                    .limit(8)
                )).scalars().all()
                _pp = (await db.execute(
                    select(PlatformProfile).where(PlatformProfile.platform == "douyin")
                )).scalar_one_or_none()
            if _pp is not None:
                _restrict = str(getattr(_pp, "memory_restrict", "off") or "off")
            # 用户姓名黑名单（username + nickname，避免公开平台暴露私密内容）
            _names = []
            try:
                _names = [n for n in ((_user.username if _user else ""), (_user.nickname if _user else "")) if n and n.strip()]
            except Exception:
                _names = []
            _safe = []
            for _r in rows:
                _c = (_r.content or "").strip()
                if not _c or (_r.sub_type or "") == "identity":
                    continue
                if _restrict == "relationship" and (_r.sub_type or "") == "relationship":
                    continue
                if any(_n in _c for _n in _names):
                    continue
                _safe.append(_c[:80])
                if len(_safe) >= 3:
                    break
            if _safe:
                parts.append("你的相关记忆（可公开的安全内容）：" + "；".join(_safe))
        except Exception:
            pass
        # 社交记忆档案（Module B）：粉丝/常互动用户关系（物理隔离，不参与上述记忆筛选）
        try:
            from app.services.social_memory_service import build_social_context
            social_txt = await build_social_context("douyin", 5)
            if social_txt:
                parts.append(social_txt)
        except Exception:
            pass
    posts = await _recent_posts(3)
    titles = "；".join(pp["title"] for pp in posts if pp.get("title"))
    if titles:
        parts.append(f"账号近期发布的内容（供风格参考，多为账号主人记录的日常）：{titles}")
    return "；".join(parts)


async def _count_replied(since, is_fan: bool | None = None) -> int:
    """统计 since 之后已回复的评论数（is_fan=None 不分粉丝/非粉丝）"""
    from sqlalchemy import func, select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinComment
    async with async_session_factory() as db:
        cond = [DouyinComment.replied == True, DouyinComment.created_at >= since]
        if is_fan is not None:
            cond.append(DouyinComment.is_fan == is_fan)
        r = await db.execute(select(func.count()).select_from(DouyinComment).where(*cond))
        return r.scalar_one() or 0


async def _pending_count(kind: str, since, is_fan: bool | None = None, exclude_task_id: int | None = None) -> int:
    """统计 since 之后处于 pending/confirmed 的待确认任务数（占位频率；is_fan=None 不分；exclude_task_id 排除自身防执行死锁）"""
    from sqlalchemy import func, select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    async with async_session_factory() as db:
        cond = [
            DouyinPending.kind == kind,
            DouyinPending.status.in_(("pending", "confirmed")),
            DouyinPending.created_at >= since,
        ]
        if exclude_task_id is not None:
            cond.append(DouyinPending.id != exclude_task_id)
        if is_fan is not None and kind == "reply_comment":
            cond.append(DouyinPending.is_fan == is_fan)
        r = await db.execute(select(func.count()).select_from(DouyinPending).where(*cond))
        return r.scalar_one() or 0


# 评论回复每日额度：60% 给粉丝（小数进一取整），剩余给非粉丝（2026-08-09 拍板）
_REPLY_DAY_LIMIT = 25
_REPLY_HOUR_LIMIT = 10
_REPLY_FAN_QUOTA = math.ceil(_REPLY_DAY_LIMIT * 0.6)   # 15
_REPLY_NON_FAN_QUOTA = _REPLY_DAY_LIMIT - _REPLY_FAN_QUOTA  # 10


async def _check_frequency(kind: str, *, is_fan: bool = False, exclude_task_id: int | None = None) -> dict:
    """风控频率检查：图文 ≤2/天；评论回复 ≤10/小时，每日 60% 给粉丝（ceil）剩余给非粉丝（含待确认占位；exclude_task_id 排除自身）"""
    now = datetime.now(timezone.utc)
    # 自然日按北京时间 0 点计算（抖音运营视角；避免 UTC 日与用户体感错一天）
    cn_now = now.astimezone(timezone(timedelta(hours=8)))
    cn_day_start = cn_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = cn_day_start.astimezone(timezone.utc).replace(tzinfo=None)
    if kind in ("image_post", "video_post"):
        # #67：视频与图文共用「每日 2 条」发布上限（读 DouyinPost.source=auto + pending video_post）
        from sqlalchemy import func, select
        from app.db.database import async_session_factory
        from app.models.douyin import DouyinPost
        async with async_session_factory() as db:
            r = await db.execute(
                select(func.count()).select_from(DouyinPost)
                .where(DouyinPost.published_at >= day_start, DouyinPost.source == "auto")
            )
            posts_today = r.scalar_one() or 0
        total = posts_today + await _pending_count(kind, day_start, exclude_task_id=exclude_task_id)
        _label = "图文/视频发布"
        if total >= 2:
            return {"ok": False, "message": f"{_label}已达每日上限（2 条/天，今日已占用 {total} 条）"}
    elif kind == "reply_comment":
        hour_start = now - timedelta(hours=1)
        h = await _count_replied(hour_start) + await _pending_count("reply_comment", hour_start, exclude_task_id=exclude_task_id)
        if h >= _REPLY_HOUR_LIMIT:
            return {"ok": False, "message": f"评论回复已达每小时上限（{_REPLY_HOUR_LIMIT} 条/小时，近 1 小时已占用 {h} 条）"}
        d = await _count_replied(day_start, is_fan) + await _pending_count("reply_comment", day_start, is_fan, exclude_task_id=exclude_task_id)
        if is_fan:
            if d >= _REPLY_FAN_QUOTA:
                return {"ok": False, "message": f"粉丝评论回复已达每日上限（{_REPLY_FAN_QUOTA} 条/天，今日已占用 {d} 条）"}
        else:
            if d >= _REPLY_NON_FAN_QUOTA:
                return {"ok": False, "message": f"非粉丝评论回复已达每日上限（{_REPLY_NON_FAN_QUOTA} 条/天，今日已占用 {d} 条）"}
    return {"ok": True}


async def _auto_gen_post_image(task_id: int, user_id: int, title: str, content: str) -> list[str]:
    """抖音图文无图时用生图服务自动生成配图（2026-08-09：抖音图文必须带图，实测无图无法发布）。

    返回 /uploads/... 相对路径列表；失败返回 []（由调用方提示人工补图）。
    """
    try:
        from app.services.image_gen_service import create_image_gen_task, run_image_gen_task
        body = ((content or "")[:120]).replace("\n", " ").strip()
        prompt = (
            f"为一条抖音图文生成配图。主题：《{(title or '')[:40]}》"
            + (f"；正文：（{body}）。" if body else "。")
            + "风格贴近内容氛围，构图自然，适合社交媒体配图，不要出现文字水印。"
        )
        task = await create_image_gen_task(user_id, prompt)
        url = await run_image_gen_task(task.id)
        if url:
            sdk.log("图文自动配图成功 task=%s: %.60s", task_id, url)
            return [url]
        sdk.log("图文自动配图失败 task=%s（生图服务无结果）", task_id)
        return []
    except Exception as e:
        sdk.log("图文自动配图异常 task=%s: %s", task_id, e)
        return []


async def _run_pending_task(task_id: int) -> dict:
    """执行单个已确认任务（由 schedule_tick 随机队列触发；含结果回写）"""
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.models.douyin import DouyinPending, DouyinPost, DouyinComment
    async with async_session_factory() as db:
        row = await db.get(DouyinPending, task_id)
        if row is None:
            return {"ok": False, "message": "任务不存在"}
        if row.status not in ("confirmed", "running"):
            return {"ok": False, "message": f"任务状态为 {row.status}，无法执行"}
        freq = await _check_frequency(row.kind, is_fan=bool(row.is_fan), exclude_task_id=task_id)
        if not freq["ok"]:
            return freq
        kind, title, content, commenter = row.kind, row.title, row.content, row.commenter
        images = []
        try:
            images = json.loads(row.image_paths_json or "[]")
        except Exception:
            pass
        row.status = "running"  # 防止并发重复执行
        await db.commit()
    try:
        if kind == "image_post":
            # 无图图文自动配图：抖音发布图文必须带图（实测无图点发布会被拦截）
            if not images:
                gen = await _auto_gen_post_image(task_id, row.user_id, title, content)
                if gen:
                    async with async_session_factory() as db2:
                        _r = await db2.get(DouyinPending, task_id)
                        if _r:
                            _r.image_paths_json = json.dumps(gen, ensure_ascii=False)
                            await db2.commit()
                    images = gen
            if images:
                # #67：图文发布可选 BGM（AI 情绪关键词，行 music_mood）
                res = await _run_sync(_sync_publish_image, images, title, content, getattr(row, "music_mood", "") or "")
            else:
                res = {"ok": False, "message": "无配图且自动生成配图失败，请在小信封/扩展里为这条图文补充图片后重新确认"}
        elif kind == "video_post":
            # #67 P2：视频发布（上传→等转码→填表→选音乐→选封面→发布）
            vpath = getattr(row, "video_path", "") or ""
            if not vpath:
                res = {"ok": False, "message": "未上传视频文件，请先在扩展页上传视频后重新确认"}
            else:
                res = await _run_sync(_sync_publish_video, vpath, title, content,
                                      getattr(row, "music_mood", "") or "", "")
        else:
            # #67 P0：评论回复双轨制（内部 API 优先 + DOM 兜底；先异步查缓存的 comment_id/item_id）
            _pk = getattr(row, "post_key", "") or _post_key(title or "")
            _cid, _iid = await _get_cached_comment_ids(_pk, commenter)
            res = await _run_sync(_sync_reply_comment_v2, _pk, commenter, content, _cid, _iid)
    except Exception as e:
        res = {"ok": False, "message": f"执行异常: {e}"}
    async with async_session_factory() as db:
        row2 = await db.get(DouyinPending, task_id)
        if res.get("ok"):
            row2.status = "executed"
            if kind in ("image_post", "video_post"):
                db.add(DouyinPost(
                    user_id=1, douyin_post_id=res.get("post_id") or _post_key(title or ""), title=(title or "")[:500],
                    post_type=("video" if kind == "video_post" else "image"), stats_json="{}",
                    published_at=datetime.now(timezone.utc), source="auto",
                ))
            else:
                # 草稿里的作品标题可能是短标题，评论表 douyin_post_id 是完整标题哈希；
                # 按「评论者 + 未回复」定位最新一条目标评论
                target = (await db.execute(
                    select(DouyinComment)
                    .where(
                        DouyinComment.user_id == 1,
                        DouyinComment.commenter == commenter,
                        DouyinComment.replied == False,
                    )
                    .order_by(DouyinComment.id.desc())
                    .limit(1)
                )).scalars().first()
                if target:
                    target.replied = True
                    target.reply_content = (content or "")[:1000]
                # 记录 AI 自己的作者评论（供作品内小对话感知，author_role=ai；防重复）
                _dup = (await db.execute(
                    select(DouyinComment)
                    .where(
                        DouyinComment.user_id == 1,
                        DouyinComment.douyin_post_id == (row2.post_key or ""),
                        DouyinComment.content == (content or "")[:1000],
                    )
                    .limit(1)
                )).scalars().first()
                if _dup is None:
                    _acc = await _get_account()
                    db.add(DouyinComment(
                        user_id=1, douyin_post_id=row2.post_key or "", commenter=(_acc.get("account_name") or "账号")[:100],
                        content=(content or "")[:1000], commented_at=None, is_fan=False,
                        is_author=True, author_role="ai", replied=False,
                    ))
        else:
            if res.get("need_manual"):
                row2.status = "manual"  # 抖音风控需短信验证（AI 无法代收），标记待人工而非失败
                row2.error = (res.get("message") or "")[:500]
            else:
                row2.status = "failed"
                row2.error = (res.get("message") or "")[:500]
        await db.commit()
    return {"ok": bool(res.get("ok")), "message": res.get("message", "")}


async def _flush_pending_queue() -> None:
    """执行已到期的 confirmed 任务（随机节奏；深夜静默跳过顺延；每条间隔随机 5-15s）"""
    try:
        if _is_quiet_hours():
            return
        from app.db.database import async_session_factory
        from sqlalchemy import select
        from app.models.douyin import DouyinPending
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(DouyinPending)
                .where(DouyinPending.status == "confirmed", DouyinPending.execute_at <= now)
                .order_by(DouyinPending.execute_at.asc())
                .limit(3)
            )).scalars().all()
            ids = [r.id for r in rows]
        for tid in ids:
            res = await _run_pending_task(tid)
            sdk.log("执行随机队列任务 %s: %s", tid, res.get("message", ""))
            await asyncio.sleep(random.randint(5, 15))
    except Exception as e:
        sdk.log("执行队列异常: %s", e)


async def _has_pending_reply(post_key: str, commenter: str) -> bool:
    """该评论是否已有 pending/confirmed 的回复任务（防重复生成草稿，2026-08-10）"""
    from sqlalchemy import func, select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    try:
        async with async_session_factory() as db:
            r = await db.execute(
                select(func.count()).select_from(DouyinPending)
                .where(
                    DouyinPending.kind == "reply_comment",
                    DouyinPending.status.in_(("pending", "confirmed")),
                    DouyinPending.post_key == (post_key or ""),
                    DouyinPending.commenter == (commenter or ""),
                )
            )
            return (r.scalar_one() or 0) > 0
    except Exception:
        return False


async def _generate_reply_for_comment(commenter: str, content: str, post_key: str, post_title: str, is_fan: bool) -> bool:
    """为新评论生成回复并入随机执行队列（保留 require_approval / 风控额度 / 违禁词 / 署名 / 防重），返回是否生成。

    由 arbiter 的 handle_social_event action 调用（2026-08-10 社交交互层 v2，
    取代原 schedule_tick 内旁路 _auto_reply_new_comments）。
    """
    try:
        if _require_approval():
            return False  # 人工模式：评论回复走小信封 ai_draft 确认流，不自动
        freq = await _check_frequency("reply_comment", is_fan=is_fan)
        if not freq["ok"]:
            return False
        if await _has_pending_reply(post_key, commenter):
            return False
        persona = await _persona_context(None)
        from app.agent.llm_client import chat_completion
        sys_p = "你是抖音创作者。根据粉丝评论生成一条自然、友好、真诚的回复（≤100字），不要加引号和话题标签。只输出回复内容。"
        if persona:
            sys_p = (f"（角色自我语境：{persona}）\n你是这个抖音账号的运营者，也是一位 AI 角色（虚拟伙伴）。"
                     "你在抖音这个公开平台面对的是陌生人和粉丝，不是用户本人。"
                     "请以你自己的身份和口吻回复粉丝评论（≤100字），自然、真诚、体现你的个性，表达你自己的态度与想法。"
                     "注意：你不是真人，不要虚构真人生活；账号作品可能是账号主人（用户）记录的日常，"
                     "不要代替账号主人以他的第一人称叙述其个人经历（他的家人、宠物、生活细节），"
                     "不要使用「我家机」这类用户视角称谓，也不要向陌生人透露与用户的过度私密细节；提及账号主人时使用其正确性别代词（用户性别已在角色自我语境中说明，男性用「他」）。"
                     "不要加引号和话题标签。只输出回复内容。")
        try:
            _custom = str(sdk.get_config().get("custom_prompt", "") or "").strip()
            if _custom:
                sys_p = sys_p + f"\n（用户自定义设定：{_custom[:300]}）"
        except Exception:
            pass
        reply = (await chat_completion(
            messages=[
                {"role": "system", "content": sys_p},
                {"role": "user", "content": f"粉丝「{commenter}」评论：{content[:200]}"},
            ],
            temperature=0.9, max_tokens=256,
        ) or "").strip()
        if len(reply) < 2 or _check_banned(reply):
            return False
        reply = _append_sign(reply, await _active_char_name())  # 末尾署名「-角色名」
        from app.db.database import async_session_factory
        from app.models.douyin import DouyinPending
        async with async_session_factory() as db:
            db.add(DouyinPending(
                user_id=1, kind="reply_comment", title=(post_title or "")[:300], content=reply[:1000],
                commenter=(commenter or "")[:100], post_key=(post_key or "")[:50], is_fan=is_fan, status="confirmed",
                execute_at=_random_execute_at(),
            ))
            await db.commit()
        sdk.log("社交事件回复已生成并入随机执行队列: %s", commenter)
        return True
    except Exception as e:
        sdk.log("社交事件回复生成异常: %s", e)
        return False


async def _mark_comment_mentioned(commenter: str, content: str) -> None:
    """标记评论已提及（防止同一评论被反复主动提及；2026-08-15）"""
    try:
        from app.db.database import async_session_factory
        from sqlalchemy import update
        from app.models.douyin import DouyinComment
        async with async_session_factory() as db:
            await db.execute(
                update(DouyinComment)
                .where(
                    DouyinComment.commenter == (commenter or "")[:100],
                    DouyinComment.content == (content or "")[:1000],
                    DouyinComment.replied == False,
                )
                .values(mentioned_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
            await db.commit()
    except Exception as e:
        sdk.log("标记评论已提及失败: %s", e)


@sdk.action("handle_mention")
async def handle_mention(payload: dict) -> bool:
    """主动提及（2026-08-15 修复）：向用户自然提起抖音新评论。

    替代原 hint 通道：注入完整角色人设（_persona_context），并明确
    「观众/粉丝评论是别人的留言，不是用户说的话」，防止把观众评论当成用户评论。
    """
    try:
        ev = payload.get("social_event") or {}
        if ev.get("event_type") != "mention":
            return False
        character_id = int(payload.get("character_id") or 0)
        user_id = int(payload.get("user_id") or 0)
        session_id = payload.get("session_id")
        if not character_id or not user_id or not session_id:
            return False
        commenter = ev.get("external_user_key") or "粉丝"
        content = (ev.get("content") or "")[:80]
        persona = await _persona_context(character_id)
        from app.agent.llm_client import chat_completion
        sys_p = (
            "你是这个抖音账号的运营者，也是一位 AI 角色（虚拟伙伴）。"
            "现在要生成一条发给用户（你的对象/好友）的消息。"
            "注意：抖音观众/粉丝的评论是别人在你的账号下留的言，不是用户说的话；"
            "你是以你自己的身份向用户提起这件事，不要把它说成'用户说过'，也不要模仿用户的语气。"
        )
        if persona:
            sys_p = f"（角色自我语境：{persona}）\n" + sys_p
        content_out = (await chat_completion(
            messages=[
                {"role": "system", "content": sys_p},
                {"role": "user", "content": (
                    f"你的抖音账号收到粉丝「{commenter}」的新评论：「{content}」。"
                    "请像朋友一样自然地用 1-2 句话向用户提起这件事（可以带你的态度/调侃/开心），"
                    "不要说'我的抖音账号'或平台字眼，不要提'AI'，不要加话题标签。"
                )},
            ],
            temperature=0.9, max_tokens=256,
        ) or "").strip().strip('"').strip("'")
        if len(content_out) < 2:
            return False
        from app.scheduler.scheduler import send_to_session
        await send_to_session(session_id, character_id, user_id, content_out, message_type="plugin")
        await _mark_comment_mentioned(commenter, content)  # 标记已提及，防重复
        sdk.log("主动提及已发送 char=%d commenter=%s", character_id, commenter)
        return True
    except Exception as e:
        sdk.log("handle_mention 异常: %s", e)
        return False


@sdk.action("handle_social_event")
async def handle_social_event(payload: dict) -> bool:
    """arbiter 执行的社交事件 action（2026-08-10 社交交互层 v2）：
    抖音新评论 → 生成回复入随机执行队列（保留 require_approval / 风控额度 / 违禁词 / 署名）。
    返回 True=已生成回复。
    """
    try:
        ev = payload.get("social_event") or {}
        if ev.get("event_type") != "comment":
            return False
        return await _generate_reply_for_comment(
            commenter=ev.get("external_user_key") or "",
            content=ev.get("content") or "",
            post_key=ev.get("post_key") or "",
            post_title=ev.get("post_title") or "",
            is_fan=bool(ev.get("is_fan")),
        )
    except Exception as e:
        sdk.log("handle_social_event 异常: %s", e)
        return False


async def _auto_generate_image_post() -> None:
    """AI 自主图文（B 通道）：轮询低频生成（持久化 24h 一次，服务器重启不重置 + 频率上限兜底 + 深夜静默跳过）。
    人工模式：生成草稿进小信封确认；全自动模式：ai_draft 直接入随机执行队列。"""
    global _last_auto_image_ts
    try:
        if _is_quiet_hours():
            return
        if time.time() - _last_auto_image_ts < 24 * 3600:
            return
        cfg = sdk.get_config()
        raw = str(cfg.get("allowed_character_ids", "") or "").strip()
        char_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        if not char_ids:
            return
        freq = await _check_frequency("image_post")
        if not freq["ok"]:
            return
        _last_auto_image_ts = time.time()
        _save_auto_image_ts(_last_auto_image_ts)
        hint = "请以你自己的身份和想法，发布一条图文动态：说说你今天想分享的（结合你的记忆、情绪与状态）。"
        res = await ai_draft({"kind": "image_post", "hint": hint, "character_id": char_ids[0]})
        sdk.log("AI 自主图文生成: %s", res.get("message", ""))
    except Exception as e:
        sdk.log("AI 自主图文生成异常: %s", e)


# ================= 注入段落 ================
async def _build_section() -> str:
    global _last_inject_ts
    await _ensure_douyin_schema()
    cfg = sdk.get_config()
    inject_min = int(cfg.get("inject_minutes", 120))
    if time.time() - _last_inject_ts < inject_min * 60:
        return ""
    acc = await _get_account()
    if not acc.get("bound"):
        _last_inject_ts = time.time()
        return "【你的抖音账号】你有一个专属抖音账号，但尚未绑定登录。用户可在「扩展」里对 douyin_mcp 插件执行绑定后，你就能感知自己的账号动态。"
    if not acc.get("logged_in"):
        _last_inject_ts = time.time()
        return "【你的抖音账号】登录态已失效，需要用户重新扫码绑定后才能继续感知。"
    name = acc.get("account_name") or "未命名账号"
    posts = await _recent_posts(int(cfg.get("max_post_inject", 2)))
    comments = await _recent_unreplied_comments(int(cfg.get("max_comment_inject", 3)))
    author_dialog = await _recent_author_comments(int(cfg.get("max_author_comment_inject", 6)))
    parts = [f"【你的抖音账号】你的账号「{name}」近况："]
    if posts:
        for pp in posts:
            st = pp["stats"]
            parts.append(
                f"- 作品《{pp['title'][:40]}》：播放 {st.get('播放', '-')}、点赞 {st.get('点赞', '-')}、评论 {st.get('评论', '-')}"
            )
    else:
        parts.append("- 暂无发布数据。")
    if author_dialog:
        parts.append("你与账号主人在作品评论区的对话（区分你与账号主人的发言，勿与非作者评论混淆）：")
        for c in author_dialog:
            who = "你" if c["author_role"] == "ai" else "账号主人"
            post_note = f"（作品《{c['post_title'][:20]}》）" if c.get("post_title") else ""
            parts.append(f"- {who}{post_note}说：「{c['content'][:60]}」")
    if comments:
        parts.append("待回复的粉丝评论：")
        for c in comments:
            parts.append(f"- {c['commenter']} 说「{c['content'][:60]}」")
    if not comments and not author_dialog:
        parts.append("- 暂无评论动态。")
    notes = await _recent_viewed_notes(2)
    if notes:
        parts.append("你最近看过的抖音图文（VLM 图片理解）：")
        for n in notes:
            desc_part = f"《{n['desc'][:40]}》" if n.get("desc") else ""
            author_part = f"（作者：{n['author'][:20]}）" if n.get("author") else ""
            img_part = ""
            if n.get("image_descs"):
                img_part = "；图片内容：" + "；".join(d[:80] for d in n["image_descs"][:2])
            parts.append(f"- {desc_part}{author_part}{img_part[:220]}")
    _last_inject_ts = time.time()
    return "\n".join(parts)


# ================= 角色白名单（allowed_character_ids 逗号分隔角色 ID，空=全部角色） =================
def _char_allowed(char_id) -> bool:
    try:
        cfg = sdk.get_config()
        raw = str(cfg.get("allowed_character_ids", "") or "").strip()
        if not raw:
            return True
        ids = [x.strip() for x in raw.split(",") if x.strip()]
        if not ids:
            return True
        return str(char_id) in ids
    except Exception:
        return True


# ================= Hook 实现 =================
router = sdk.router()


@router.get("/status")
async def status():
    global _last_status
    try:
        st = await _run_sync(_sync_check_login)
        acc = await _get_account()
        await _upsert_account({"bound": acc.get("bound", False), "logged_in": st.get("logged_in", False), "account_name": st.get("account_name", "")})
        _last_status = {"bound": acc.get("bound", False), "logged_in": bool(st.get("logged_in")), "message": st.get("message", "")}
    except Exception as e:
        _last_status = {"bound": False, "logged_in": False, "message": f"状态检查异常: {e}"}
    return _last_status


@router.post("/bind")
async def bind():
    result = await _run_sync(_sync_bind)
    ok = bool(result.get("ok"))
    await _upsert_account({"bound": ok, "logged_in": ok})
    return result


@router.post("/notes/fetch")
async def fetch_note(payload: dict):
    """抓取并理解一条抖音图文作品（VLM）；节流 ≥5 分钟 1 次"""
    global _last_note_fetch_ts
    aweme_id = str(payload.get("aweme_id") or "").strip()
    if not aweme_id.isdigit():
        return {"ok": False, "message": "aweme_id 必须是数字作品 ID"}
    if time.time() - _last_note_fetch_ts < 300:
        left = int(300 - (time.time() - _last_note_fetch_ts))
        return {"ok": False, "message": f"抓取过于频繁，请 {left} 秒后再试"}
    _last_note_fetch_ts = time.time()
    return await fetch_note_content(aweme_id)


@router.get("/notes/latest")
async def latest_notes():
    """最近看过的抖音图文（含 VLM 图片理解）"""
    return {"ok": True, "notes": await _recent_viewed_notes(5)}


@router.post("/draft/image_post")
async def draft_image_post(payload: dict):
    """图文发布草稿：检查违禁词/频率 → 写入 douyin_pending（待确认）"""
    title = str(payload.get("title") or "").strip()
    desc = str(payload.get("desc") or "").strip()
    images = list(payload.get("image_paths") or [])
    if not images:
        return {"ok": False, "message": "请提供至少一张图片路径"}
    if not (title or desc):
        return {"ok": False, "message": "标题或描述至少填一个"}
    bw = _check_banned(f"{title} {desc}")
    if bw:
        return {"ok": False, "message": f"内容包含违禁词「{bw}」，已拦截"}
    freq = await _check_frequency("image_post")
    if not freq["ok"]:
        return freq
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    async with async_session_factory() as db:
        row = DouyinPending(
            user_id=1, kind="image_post", title=title[:300], content=desc[:2000],
            image_paths_json=json.dumps(images, ensure_ascii=False),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        pid = row.id
    return {"ok": True, "id": pid, "message": "图文草稿已生成，请在 App 确认后发布"}


@router.post("/draft/video_post")
async def draft_video_post(payload: dict):
    """视频发布草稿（#67 P2）：title + desc + music_keyword(可选) + video_path(可后补)。
    与 image_post 共用 pending 队列流程，kind=video_post。"""
    title = str(payload.get("title") or "").strip()
    desc = str(payload.get("desc") or "").strip()
    video_path = str(payload.get("video_path") or "").strip()
    music_mood = normalize_music_mood(str(payload.get("music_keyword") or "").strip())
    if not (title or desc):
        return {"ok": False, "message": "标题或描述至少填一个"}
    bw = _check_banned(f"{title} {desc}")
    if bw:
        return {"ok": False, "message": f"内容包含违禁词「{bw}」，已拦截"}
    freq = await _check_frequency("video_post")
    if not freq["ok"]:
        return freq
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    async with async_session_factory() as db:
        row = DouyinPending(
            user_id=1, kind="video_post", title=title[:300], content=desc[:2000],
            video_path=video_path[:500], music_mood=music_mood, post_type="video",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        pid = row.id
    return {"ok": True, "id": pid, "message": "视频草稿已生成，请在 App 确认后发布（未传视频可先上传）"}


@router.post("/draft/reply_comment")
async def draft_reply_comment(payload: dict):
    """评论回复草稿：post_title/commenter 定位目标评论，reply_text 为回复内容"""
    post_title = str(payload.get("post_title") or "").strip()
    commenter = str(payload.get("commenter") or "").strip()
    reply_text = str(payload.get("reply_text") or "").strip()
    if not (post_title and commenter and reply_text):
        return {"ok": False, "message": "post_title/commenter/reply_text 均不能为空"}
    bw = _check_banned(reply_text)
    if bw:
        return {"ok": False, "message": f"回复内容包含违禁词「{bw}」，已拦截"}
    reply_text = _append_sign(reply_text, await _active_char_name())  # 末尾署名「-角色名」
    target_is_fan = await _comment_is_fan(post_title, commenter)
    freq = await _check_frequency("reply_comment", is_fan=target_is_fan)
    if not freq["ok"]:
        return freq
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    async with async_session_factory() as db:
        row = DouyinPending(
            user_id=1, kind="reply_comment", title=post_title[:300], content=reply_text[:1000],
            commenter=commenter[:100], post_key=_post_key(post_title), is_fan=target_is_fan,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        pid = row.id
    return {"ok": True, "id": pid, "message": "回复草稿已生成，请在 App 确认后发送"}


@router.post("/ai_draft")
async def ai_draft(payload: dict):
    """AI 生成草稿（复用聊天生成链路，独立低频调用）：kind=image_post|reply_comment
    自我性：注入角色人设/记忆/近期作品，让 AI 以自己的身份和想法创作（character_id 可选，默认取白名单第一个）"""
    kind = payload.get("kind")
    hint = str(payload.get("hint") or "").strip()
    try:
        character_id = int(payload.get("character_id") or 0) or None
    except Exception:
        character_id = None
    if kind not in ("image_post", "reply_comment"):
        return {"ok": False, "message": "kind 必须是 image_post 或 reply_comment"}
    post_title = str(payload.get("post_title") or "").strip()
    commenter = str(payload.get("commenter") or "").strip()
    persona = await _persona_context(character_id)
    char_name = "这个角色"
    if persona:
        _m = re.search(r"你是角色「([^」]+)」", persona)
        if _m:
            char_name = _m.group(1)
    if not char_name or char_name == "这个角色":
        char_name = await _active_char_name()
    custom = ""
    try:
        custom = str(sdk.get_config().get("custom_prompt", "") or "").strip()
    except Exception:
        pass
    # 回复场景：定位目标评论（原文 + 粉丝标记），确保以角色身份回应真实评论
    if kind == "reply_comment":
        if not (post_title and commenter):
            latest = await _recent_unreplied_comments(1)
            if latest:
                post_title = latest[0].get("post_title", "") or post_title
                commenter = latest[0].get("commenter", "") or commenter
        comment = await _latest_comment(post_title, commenter) if (post_title and commenter) else None
    else:
        comment = None
    comment_content = (comment or {}).get("content", "") or ""
    # #67 P1：AI 自主图文随机选一种内容类型（避免风格重复）
    _content_type = pick_content_type()
    from app.agent.llm_client import chat_completion
    if kind == "image_post":
        sys_prompt = ("你是抖音图文创作者。根据灵感生成图文草稿：第一行标题（≤20字，简洁有吸引力），"
                      "第二行起为作品正文（1-3 句完整内容，必须写实际文字，不能只有话题标签），最后可附 1-3 个话题标签。只输出内容，不要解释。")
        if persona:
            sys_prompt = (f"（角色自我语境：{persona}）\n你是「{char_name}」本人，正在经营自己的抖音账号。"
                          "创作图文时用你自己平时说话的方式和想法，鲜明体现你的性格（冷漠/爱吃醋/话少一针见血/口是心非/偶尔别扭等——严格按你真实人设），"
                          "围绕你自己的状态、心情、想法与感悟来写，表达自己的个性与观点，不要写成对某个人的回复，"
                          "不要写广告推销、客套话或自我介绍。"
                          "注意边界：你不是真人，不要虚构「上班、打工、上学、赶稿」等真人生活细节；"
                          "不要代替账号主人（用户）以他的第一人称叙述他的生活；不要向陌生人透露与用户的过度私密细节"
                          "（如亲密称呼、具体隐私）；可以一般性地提到你有一个珍视的人。"
                          "提及账号主人时使用其正确性别代词（用户性别已在角色自我语境中说明，男性用「他」）。"
                          "不要自称「虚拟世界的访客」「AI 助手」这类，不要写「初来乍到、请多关照」等客套文案——"
                          "你就是一个有性格、有自己的账号的角色，作品要有你自己的口吻和态度。"
                          "第一行标题（≤20字，简洁有吸引力），"
                          "第二行起为作品正文（1-3 句完整内容，必须写实际文字，不能只有话题标签），最后可附 1-3 个话题标签。只输出内容，不要解释。")
        if custom:
            sys_prompt += f"\n（用户自定义设定：{custom[:300]}）"
        # #67 P1 人味优化：注入人味写作指令 + 随机内容类型（避免风格重复）
        sys_prompt += "\n\n" + humanize_image_prompt(char_name or "这个角色", "日常口语化", _content_type)
    else:
        sys_prompt = ("你是抖音创作者。根据粉丝评论生成一条自然、友好、真诚的回复（≤100字），"
                      "不要加引号和话题标签。只输出回复内容。")
        if persona:
            sys_prompt = (f"（角色自我语境：{persona}）\n你是「{char_name}」本人，正在自己的抖音账号下回复粉丝评论。"
                          "用你自己平时说话的口吻回复（话少、一针见血、口是心非等——严格按你真实人设），"
                          "自然、真诚、体现你的个性与态度，不要写客套话、广告话术或「感谢支持」式官腔。"
                          "注意边界：你不是真人，不要虚构真人生活；账号作品可能是账号主人（用户）记录的日常，"
                          "不要代替账号主人以他的第一人称叙述其个人经历（他的家人、宠物、生活细节）；"
                          "不要使用「我家机」这类用户视角称谓，也不要向陌生人透露与用户的过度私密细节。"
                          "回复 ≤100字，不要加引号和话题标签。只输出回复内容。")
        if custom:
            sys_prompt += f"\n（用户自定义设定：{custom[:300]}）"
        # #67 P1 人味优化：注入评论回复人味指令
        sys_prompt += "\n\n" + humanize_reply_prompt(char_name or "这个角色")
    try:
        if kind == "reply_comment" and comment_content:
            user_msg = f"粉丝「{commenter}」评论：{comment_content[:200]}"
            # 计划 15：回复时结合作品图片内容（VLM 理解），让回复更精准
            try:
                for _n in await _recent_viewed_notes(5):
                    if post_title and (_n.get("desc", "").startswith(post_title[:20]) or post_title[:10] in _n.get("desc", "")):
                        if _n.get("image_descs"):
                            user_msg += "\n作品图片内容：" + "；".join(d[:120] for d in _n["image_descs"][:2])
                        break
            except Exception:
                pass
            if hint:
                user_msg += f"\n（补充要求：{hint}）"
        else:
            user_msg = hint or "今天发一条图文：以你自己的口吻说说你此刻的心情、状态或想法，可以从你的记忆和状态里取材。"
        if kind == "image_post":
            # 生成 → 剥离标签检测正文 → 空正文带提示重试（最多 3 次，防模型只输出标题+标签）
            for _attempt in range(3):
                content = (await chat_completion(
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.9, max_tokens=512,
                ) or "").strip()
                _lines = [l.strip() for l in content.splitlines() if l.strip()]
                _body = [l for l in _lines[1:] if not l.startswith("#")]
                if _lines and _body:
                    break
                if _attempt < 2:
                    user_msg = f"{user_msg}\n（注意：刚才输出缺少正文，请先写 1-3 句完整正文，再附话题标签。）"
        else:
            content = (await chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.9, max_tokens=512,
            ) or "").strip()
    except Exception as e:
        return {"ok": False, "message": f"AI 生成失败: {e}"}
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    if kind == "image_post":
        # #67 P1：解析「音乐:情绪」行 → 存 music_mood；剥离该行后按行拆标题/正文
        music_mood = parse_music_mood(content)
        content = re.sub(r"[\[【]?\s*音乐\s*[:：]\s*[^\s\]】\n]+[\]】]?", "", content).strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        title = (lines[0] if lines else "")[:20]
        body = [l for l in lines[1:] if not l.startswith("#")]
        if not body:
            return {"ok": False, "message": "AI 生成内容不完整（缺少正文），请重试"}
        desc = _de_ai("\n".join(lines[1:]))[:1000] if len(lines) > 1 else ""
        if not desc:
            desc = ("\n".join(lines[1:]))[:1000]  # 反 AI 腔清理后为空则回退原始正文
        bw = _check_banned(f"{title} {desc}")
        if bw:
            return {"ok": False, "message": f"生成内容含违禁词「{bw}」"}
        freq = await _check_frequency("image_post")
        if not freq["ok"]:
            return freq
        async with async_session_factory() as db:
            row = DouyinPending(user_id=1, kind="image_post", title=title, content=desc,
                                image_paths_json="[]", music_mood=music_mood, post_type="image")
            if not _require_approval():
                row.status = "confirmed"
                row.execute_at = _random_execute_at()
            db.add(row)
            await db.commit()
            await db.refresh(row)
            pid = row.id
        msg = "AI 已生成图文草稿并进入随机发布队列（全自动模式）" if not _require_approval() else "AI 已生成图文草稿（尚未选图，确认前请先补充图片）"
        return {"ok": True, "id": pid, "kind": kind, "title": title, "desc": desc,
                "music_mood": music_mood, "message": msg}
    else:
        reply = _de_ai(content)[:500]
        if not reply:
            reply = content[:500]  # 反 AI 腔清理后为空则回退原始内容，避免生成空回复
        bw = _check_banned(reply)
        if bw:
            return {"ok": False, "message": f"生成内容含违禁词「{bw}」"}
        reply = _append_sign(reply, char_name)  # 末尾署名「-角色名」
        target_is_fan = bool(comment and comment["is_fan"]) if comment else await _comment_is_fan(post_title, commenter)
        freq = await _check_frequency("reply_comment", is_fan=target_is_fan)
        if not freq["ok"]:
            return freq
        async with async_session_factory() as db:
            row = DouyinPending(
                user_id=1, kind="reply_comment", title=post_title[:300], content=reply,
                commenter=commenter[:100], post_key=_post_key(post_title), is_fan=target_is_fan,
            )
            if not _require_approval():
                row.status = "confirmed"
                row.execute_at = _random_execute_at()
            db.add(row)
            await db.commit()
            await db.refresh(row)
            pid = row.id
        msg = "AI 已生成回复并进入随机发送队列（全自动模式）" if not _require_approval() else "AI 已生成回复草稿，请在 App 确认后发送"
        return {"ok": True, "id": pid, "kind": kind, "reply_text": reply, "message": msg}


@router.get("/pending")
async def pending_list():
    """待确认任务列表（默认人工确认：图文发布 / 评论回复）"""
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(DouyinPending)
            .where(DouyinPending.status.in_(("pending", "manual")))
            .order_by(DouyinPending.id.desc())
            .limit(20)
        )).scalars().all()
        items = []
        for r in rows:
            images = []
            try:
                images = json.loads(r.image_paths_json or "[]")
            except Exception:
                pass
            items.append({
                "id": r.id, "kind": r.kind, "title": r.title, "content": r.content,
                "commenter": r.commenter, "is_fan": bool(r.is_fan), "images": images,
                "created_at": r.created_at.isoformat() if r.created_at else "", "status": r.status,
                # #67：发布类型 / 音乐情绪 / 视频路径（前端展示与确认）
                "post_type": r.post_type or "image", "music_mood": r.music_mood or "",
                "video_path": r.video_path or "",
            })
    return {"items": items}


@router.get("/upcoming")
async def upcoming_list():
    """已确认待发布任务列表（发布倒计时：小信封展示 confirmed/running 任务与剩余秒数）"""
    from app.db.database import async_session_factory
    from sqlalchemy import select
    from app.models.douyin import DouyinPending
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(DouyinPending)
            .where(DouyinPending.status.in_(("confirmed", "running")))
            .order_by(DouyinPending.execute_at.asc())
            .limit(30)
        )).scalars().all()
        items = []
        for r in rows:
            eta = 0
            if r.execute_at:
                eta = max(0, int((r.execute_at - now).total_seconds()))
            items.append({
                "id": r.id, "kind": r.kind, "title": r.title, "content": r.content,
                "commenter": r.commenter, "is_fan": bool(r.is_fan), "status": r.status,
                "execute_at": r.execute_at.isoformat() if r.execute_at else "",
                "eta_seconds": eta,
            })
    return {"items": items}


@router.post("/upload_image")
async def upload_image(task_id: int = Form(...), file: UploadFile = File(...)):
    """为图文草稿上传配图：保存到 uploads/douyin/{task_id}/，追加进 image_paths_json"""
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    from app.services.upload_service import save_image
    async with async_session_factory() as db:
        row = await db.get(DouyinPending, task_id)
        if row is None:
            return {"ok": False, "message": "任务不存在"}
        if row.kind != "image_post":
            return {"ok": False, "message": "仅图文任务可上传图片"}
        url = await save_image(file, f"douyin/{task_id}")
        imgs = []
        try:
            imgs = json.loads(row.image_paths_json or "[]")
        except Exception:
            pass
        if url not in imgs:
            imgs.append(url)
        row.image_paths_json = json.dumps(imgs, ensure_ascii=False)
        await db.commit()
    return {"ok": True, "images": imgs, "message": f"图片已上传（共 {len(imgs)} 张）"}


@router.post("/upload_video")
async def upload_video(task_id: int = Form(...), file: UploadFile = File(...)):
    """为视频草稿上传视频文件（#67 P2）：保存到 uploads/douyin/{task_id}/ 并写 row.video_path"""
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    from app.services.upload_service import save_video
    async with async_session_factory() as db:
        row = await db.get(DouyinPending, task_id)
        if row is None:
            return {"ok": False, "message": "任务不存在"}
        if row.kind != "video_post":
            return {"ok": False, "message": "仅视频任务可上传视频"}
        url = await save_video(file, f"douyin/{task_id}")
        row.video_path = url[:500]
        await db.commit()
    return {"ok": True, "video_path": url, "message": "视频已上传，确认后可发布"}


@router.post("/confirm/{task_id}")
async def confirm_task(task_id: int):
    """确认草稿：进入随机执行队列（避开深夜静默），不立即发布"""
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    async with async_session_factory() as db:
        row = await db.get(DouyinPending, task_id)
        if row is None:
            return {"ok": False, "message": "任务不存在"}
        if row.status != "pending":
            return {"ok": False, "message": f"任务状态为 {row.status}，无法确认"}
        freq = await _check_frequency(row.kind, is_fan=bool(row.is_fan), exclude_task_id=task_id)
        if not freq["ok"]:
            return freq
        # 图文无图也可确认：抖音支持纯文字发布（自动生成配图）；有图则走图文发布
        row.status = "confirmed"
        row.execute_at = _random_execute_at()
        await db.commit()
    return {"ok": True, "message": "已确认，将在随机时间发布/回复（避开深夜静默）"}


@router.post("/reject/{task_id}")
async def reject_task(task_id: int):
    """拒绝草稿：任务标记 rejected，不执行"""
    from app.db.database import async_session_factory
    from app.models.douyin import DouyinPending
    async with async_session_factory() as db:
        row = await db.get(DouyinPending, task_id)
        if row is None:
            return {"ok": False, "message": "任务不存在"}
        if row.status != "pending":
            return {"ok": False, "message": f"任务状态为 {row.status}，无法拒绝"}
        row.status = "rejected"
        await db.commit()
    return {"ok": True, "message": "已拒绝该草稿"}


@router.post("/refresh")
async def refresh():
    """手动刷新：登录保活 + 抓发布列表与新评论并落库（返回样本供调试）"""
    cfg = sdk.get_config()
    st = await _run_sync(_sync_poll_once, int(cfg.get("max_comment_posts", 5)))
    acc = await _get_account()
    await _upsert_account({"bound": acc.get("bound", False), "logged_in": st.get("logged_in", False), "account_name": st.get("account_name", "")})
    posts: list[dict] = st.get("posts") or []
    cmt: dict = st.get("comments") or {"post_title": "", "comments": []}
    if st.get("logged_in"):
        await _upsert_posts(posts)
        await _upsert_comments(cmt.get("posts", []))
    sample = (cmt.get("posts") or [{}])[0] if cmt else {}
    return {
        "logged_in": bool(st.get("logged_in")),
        "account_name": st.get("account_name", ""),
        "posts": posts[:3],
        "post_title": sample.get("post_title", ""),
        "comments": (sample.get("comments") or [])[:5],
        "note": "Phase 1.1 已校准（内容管理/评论管理真实 URL 与选择器）",
    }


@sdk.hook("schedule_tick")
async def on_tick(ctx):
    global _last_poll_ts, _last_flush_ts
    try:
        # 发布队列独立短节流（60s）：倒计时到点后尽快执行，不绑定 30 分钟轮询
        if time.time() - _last_flush_ts >= 60:
            _last_flush_ts = time.time()
            await _flush_pending_queue()
        await _ensure_douyin_schema()
        cfg = sdk.get_config()
        interval = int(cfg.get("comment_poll_minutes", 30)) * 60
        if time.time() - _last_poll_ts < interval:
            return
        _last_poll_ts = time.time()
        st = await _run_sync(_sync_poll_once, int(cfg.get("max_comment_posts", 5)))
        acc = await _get_account()
        await _upsert_account({"bound": acc.get("bound", False), "logged_in": st.get("logged_in", False), "account_name": st.get("account_name", "")})
        if st.get("logged_in"):
            posts = st.get("posts") or []
            added_posts = await _upsert_posts(posts)
            cmt = st.get("comments") or {"posts": []}
            added_cmts = await _upsert_comments(cmt.get("posts", []))
            total_cmts = sum(len(x.get("comments") or []) for x in (cmt.get("posts") or []))
            sdk.log("douyin 轮询完成: posts=%d(新%d) comments=%d(新%d)",
                    len(posts), added_posts, total_cmts, added_cmts)
            # 随机执行队列已上移到 tick 开头独立节流（60s）；评论回复改由 arbiter 社交事件调度（2026-08-10 社交交互层 v2）
            await _auto_generate_image_post()
        else:
            sdk.log("douyin 轮询完成: 未登录，跳过抓取")
    except Exception as e:
        sdk.log("douyin schedule_tick 异常: %s", e)


@sdk.hook("context_inject")
async def inject(ctx):
    try:
        # 角色白名单：仅允许配置的角色感知抖音账号（空=全部角色）
        if not _char_allowed(ctx.get("character_id")):
            return
        section = await _build_section()
        if section:
            ctx["context_messages"].append({"role": "system", "content": section})
            sdk.log("已注入抖音账号段落 (char=%s)", ctx.get("character_id"))
    except Exception as e:
        sdk.log("抖音注入失败: %s", e)


# ================= Phase 3：主动提及（arbiter proactive_candidate 通道） =================
_LAST_MENTION_STATE_FILE = Path(__file__).resolve().parents[3] / "backend" / "data" / "plugins" / "douyin_mention_state.json"


def _load_mention_ts() -> float:
    """读取主动提及节流时间（持久化：重启不重置，防重启后对同评论重复提及；2026-08-15）"""
    try:
        return float(json.loads(_LAST_MENTION_STATE_FILE.read_text(encoding="utf-8")).get("last_mention_ts", 0) or 0)
    except Exception:
        return 0.0


def _save_mention_ts(ts: float) -> None:
    try:
        _LAST_MENTION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_MENTION_STATE_FILE.write_text(json.dumps({"last_mention_ts": ts}), encoding="utf-8")
    except Exception:
        pass


_last_proactive_ts: float = _load_mention_ts()

# AI 自主图文节流状态（持久化到 backend/data/plugins，服务器重启不重置）
_AUTO_IMAGE_STATE_FILE = Path(__file__).resolve().parents[3] / "backend" / "data" / "plugins" / "douyin_mcp_state.json"


def _load_auto_image_ts() -> float:
    try:
        return float(json.loads(_AUTO_IMAGE_STATE_FILE.read_text(encoding="utf-8")).get("last_auto_image_ts", 0) or 0)
    except Exception:
        return 0.0


def _save_auto_image_ts(ts: float) -> None:
    try:
        _AUTO_IMAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _AUTO_IMAGE_STATE_FILE.write_text(json.dumps({"last_auto_image_ts": ts}), encoding="utf-8")
    except Exception:
        pass


_last_auto_image_ts: float = _load_auto_image_ts()


@sdk.hook("proactive_candidate")
async def proactive_candidate(ctx):
    """社交事件候选（2026-08-10 社交交互层 v2，支持返回 list[dict]）：
    - 评论回复事件（全自动模式）：action=handle_social_event → arbiter 执行插件内部回复逻辑
    - 主动提及（App 会话提醒，24h 节流）：hint → arbiter 用 LLM 生成自然消息发送
    """
    try:
        global _last_proactive_ts
        cfg = sdk.get_config()
        raw = str(cfg.get("allowed_character_ids", "") or "").strip()
        if not raw:
            return None
        comments = await _recent_unreplied_comments(1)
        mention_comments = await _recent_unreplied_comments(1, exclude_mentioned=True)
        from app.services.chat_service import get_latest_session_id
        char_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        all_candidates = []
        for cid in char_ids:
            # 角色可能属于其他账号，动态取角色所属用户
            from app.models.character import AICharacter
            from app.db.database import async_session_factory as _asf
            async with _asf() as db:
                char = await db.get(AICharacter, cid)
            if char is None:
                continue
            uid = char.user_id or 1
            try:
                sid = await get_latest_session_id(uid, cid)
            except Exception:
                sid = None
            if not sid:
                continue
            candidates = []
            c = comments[0] if comments else None
            # 1) 评论回复事件（全自动模式；额度/违禁词/防重在 handle_social_event 内）
            if c is not None and not _require_approval():
                candidates.append({
                    "character_id": cid, "user_id": uid, "session_id": sid,
                    "action": "handle_social_event",
                    "social_event": {
                        "source": "douyin",
                        "event_type": "comment",
                        "external_user_key": c["commenter"],
                        "content": c["content"][:200],
                        "post_key": c.get("post_key", ""),
                        "post_title": c.get("post_title", ""),
                        "is_fan": c.get("is_fan", False),
                    },
                })
            # 2) 主动提及（有新评论且距上次提及 ≥24h；节流持久化 + 已提及评论去重）
            # 2026-08-15 修复：改走插件 action（完整人设 + 观众评论≠用户 + 提及去重 + 节流持久化）
            mc = mention_comments[0] if mention_comments else None
            if mc is not None and time.time() - _last_proactive_ts >= 24 * 3600:
                _last_proactive_ts = time.time()
                _save_mention_ts(_last_proactive_ts)
                candidates.append({
                    "character_id": cid, "user_id": uid, "session_id": sid,
                    "action": "handle_mention",
                    "social_event": {
                        "source": "douyin",
                        "event_type": "mention",
                        "external_user_key": mc["commenter"],
                        "content": mc["content"][:80],
                    },
                })
            all_candidates.extend(candidates)
        return all_candidates or None
    except Exception as e:
        sdk.log("proactive_candidate 异常: %s", e)
        return None
