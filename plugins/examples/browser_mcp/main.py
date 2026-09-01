"""浏览器 MCP 插件（计划 16，2026-08-10 实施）：通用网页浏览，严格记忆隔离，默认关闭

- 浏览器：Playwright 驱动系统 Edge（channel=msedge），独立 profile（backend/data/browser_profile/，不入 git）
- 能力：POST /api/v1/plugins/browser_mcp/browse {url} → 打开页面 → 提取 title/正文/图片/链接 → 短期快照
- 记忆隔离：浏览内容只进 browser_snapshots 短期表（30 分钟过期清理），绝不写 memories 表
- 注入：context_inject 默认关闭（config.enabled=false）；开启后仅注入「域名+标题摘要」（节流），不注入正文
- 安全：敏感域名黑名单拦截（支付/银行/邮箱/私密）；浏览失败只降级不影响主链路
- Edge 预热（plans #39，2026-08-16）：常驻浏览器上下文，启动后后台预热 + 首次使用惰性预热，
  避免每次搜索冷启动；空闲 10 分钟自动回收；上下文异常自动重建一次
"""
import asyncio
import concurrent.futures
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.plugins import sdk

_PROFILE_DIR = Path(__file__).resolve().parents[3] / "backend" / "data" / "browser_profile"
_SCREENSHOT_DIR = _PROFILE_DIR / "screenshots"
_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Playwright sync API 必须运行在无 running-loop 的线程；单 worker 串行化浏览器操作
_PLAYWRIGHT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-pw")

_last_inject_ts: float = 0.0

# 敏感域名黑名单关键词（支付/银行/邮箱/私密内容，禁止浏览）
_BLOCKED_KEYWORDS = [
    "alipay", "weixin.qq.com/cgi-bin", "pay.", "bank", "icbc", "ccb", "abcchina", "boc.cn",
    "mail.", "gmail", "outlook", "icloud.com", "password", "login/", "admin",
]


def _is_blocked(url: str) -> str:
    low = (url or "").lower()
    for k in _BLOCKED_KEYWORDS:
        if k in low:
            return k
    # P1 安全加固（2026-08-16）：拦截内网/保留 IP 段（防 SSRF 打本机 8000/11434 等内部服务）
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return "local address"
        import ipaddress
        ip = ipaddress.ip_address(host.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return "private address"
    except Exception:
        pass
    return ""


async def _run_sync(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_PLAYWRIGHT_EXECUTOR, func, *args)


def _launch(headless: bool):
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(_PROFILE_DIR / "profile"),
        channel="msedge",
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 800},
    )
    return p, ctx


def _close_ctx(p, ctx) -> None:
    try:
        ctx.close()
    except Exception:
        pass
    try:
        p.stop()
    except Exception:
        pass


# ── Edge 预热（plans #39，2026-08-16）──
# 常驻浏览器上下文：启动后保持打开，browse/search 复用，避免每次冷启动 Edge（曾 10-20s+ 超时）
_WARM: dict = {"pw": None, "ctx": None, "ready": False, "last_used": 0.0}
_DEFAULT_IDLE_MINUTES = 10.0  # 空闲回收默认值（可经插件配置 warm_idle_minutes 覆盖，0=常驻不回收）
_last_warm_attempt_ts: float = 0.0


def _idle_timeout_sec() -> float:
    """空闲回收超时（秒）：优先插件配置 warm_idle_minutes（0=常驻不回收），默认 10 分钟。"""
    try:
        minutes = float(sdk.get_config().get("warm_idle_minutes", _DEFAULT_IDLE_MINUTES))
        if minutes <= 0:
            return float("inf")
        return minutes * 60.0
    except Exception:
        return _DEFAULT_IDLE_MINUTES * 60.0


def _reset_warm() -> None:
    """关闭并清空常驻上下文（空闲回收 / 上下文异常后重建）"""
    _close_ctx(_WARM.get("pw"), _WARM.get("ctx"))
    _WARM["pw"] = None
    _WARM["ctx"] = None
    _WARM["ready"] = False


def _ensure_warm_ctx():
    """获取常驻 Edge 上下文：惰性启动并保持；空闲超时自动回收重建。抛异常=启动失败。

    必须在 _PLAYWRIGHT_EXECUTOR 单线程内调用（Playwright sync API 线程约束）。
    """
    ctx = _WARM.get("ctx")
    now = time.time()
    if ctx is not None and now - _WARM.get("last_used", 0.0) > _idle_timeout_sec():
        _reset_warm()  # 空闲回收
        ctx = None
    if ctx is None:
        pw, ctx = _launch(headless=True)
        _WARM["pw"], _WARM["ctx"], _WARM["ready"] = pw, ctx, True
    _WARM["last_used"] = now
    return _WARM["pw"], _WARM["ctx"]


async def warmup() -> None:
    """Edge 预热：后台启动并保持常驻浏览器上下文（幂等，失败静默，后续按需冷启动兜底）"""
    try:
        if _WARM.get("ctx") is not None and _WARM.get("ready"):
            return  # 已就绪
        await _run_sync(_ensure_warm_ctx)
        sdk.log("Edge 浏览器已预热（常驻上下文就绪）")
    except Exception as e:
        sdk.log("Edge 预热失败（按需冷启动兜底）: %s", e)


def _sync_browse(url: str) -> dict:
    """打开网页，提取 title/正文/图片/链接（复用常驻 Edge 上下文，异常自动重建一次）"""
    for attempt in (1, 2):
        try:
            _p, ctx = _ensure_warm_ctx()
        except Exception as e:
            if attempt == 2:
                return {"ok": False, "message": f"浏览失败: {e}"}
            _reset_warm()
            continue
        page = None
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, timeout=45000)
            page.wait_for_timeout(6000)
            final_url = page.url
            title = (page.title() or "")[:200]
            text = page.evaluate("() => (document.body.innerText || '').slice(0, 3000)")
            imgs = page.evaluate(
                """() => Array.from(document.querySelectorAll('img')).map(i => i.src || '').filter(s => s.startsWith('http')).slice(0, 10)"""
            )
            links = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href).filter(h => h.startsWith('http')).slice(0, 20)"""
            )
            return {"ok": True, "url": final_url, "title": title, "text": text, "images": imgs, "links": links}
        except Exception as e:
            if attempt == 2:
                return {"ok": False, "message": f"浏览失败: {e}"}
            _reset_warm()  # 常驻 ctx 异常 → 重建一次
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
    return {"ok": False, "message": "浏览失败"}



_ensure_done = False


async def _ensure_schema() -> None:
    """幂等确保 browser_snapshots 表存在"""
    global _ensure_done
    if _ensure_done:
        return
    from app.db.database import async_session_factory
    from sqlalchemy import text
    try:
        async with async_session_factory() as db:
            await db.execute(text(
                "CREATE TABLE IF NOT EXISTS browser_snapshots ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER DEFAULT 1, "
                "url VARCHAR(500) NOT NULL UNIQUE, domain VARCHAR(200) DEFAULT '', "
                "title VARCHAR(300) DEFAULT '', text TEXT DEFAULT '', "
                "image_urls_json TEXT DEFAULT '[]', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            ))
            await db.commit()
        _ensure_done = True
    except Exception:
        pass

async def _save_snapshot(url: str, domain: str, title: str, text: str, images: list) -> None:
    from app.db.database import async_session_factory
    from app.models.user import BrowserSnapshot
    from sqlalchemy import select
    try:
        async with async_session_factory() as db:
            row = (await db.execute(select(BrowserSnapshot).where(BrowserSnapshot.url == url[:500]))).scalars().first()
            if row is None:
                db.add(BrowserSnapshot(
                    user_id=1, url=url[:500], domain=domain[:200], title=title[:300],
                    text=text[:8000], image_urls_json=json.dumps(images, ensure_ascii=False)[:4000],
                ))
            else:
                row.domain, row.title, row.text = domain[:200], title[:300], text[:8000]
                row.image_urls_json = json.dumps(images, ensure_ascii=False)[:4000]
            await db.commit()
    except Exception as e:
        sdk.log("browser 快照保存失败: %s", e)


async def _recent_snapshots(limit: int = 5) -> list[dict]:
    from app.db.database import async_session_factory
    from app.models.user import BrowserSnapshot
    from sqlalchemy import select
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(select(BrowserSnapshot).order_by(BrowserSnapshot.id.desc()).limit(limit))).scalars().all()
            return [{"url": r.url[:120], "domain": r.domain, "title": r.title, "text": (r.text or "")[:200]} for r in rows]
    except Exception:
        return []


async def browse(url: str) -> dict:
    """浏览网页并保存短期快照；返回标题/正文摘要（v1 不返回全文给调用方）"""
    await _ensure_schema()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    b = _is_blocked(url)
    if b:
        return {"ok": False, "message": f"命中敏感关键词「{b}」，已拦截浏览"}
    res = await _run_sync(_sync_browse, url)
    if not res.get("ok"):
        return res
    from urllib.parse import urlparse
    domain = urlparse(res.get("url") or url).netloc[:200]
    await _save_snapshot(res["url"], domain, res.get("title", ""), res.get("text", ""), res.get("images") or [])
    sdk.log("浏览器快照已保存: %s（%s 字）", domain, len(res.get("text") or ""))
    return {
        "ok": True,
        "url": res["url"],
        "title": (res.get("title") or "")[:100],
        "text": (res.get("text") or "")[:800],
        "images": (res.get("images") or [])[:5],
    }

def _decode_redirect_url(href: str) -> str:
    """还原搜索引擎跳转链接为真实 URL：Bing ck/a 的 u 参数（UrlBase64）、DuckDuckGo 的 uddg 参数。"""
    href = (href or "").strip()
    if not href:
        return ""
    try:
        import base64
        from urllib.parse import parse_qs, unquote, urlparse
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        if "bing.com" in parsed.netloc and qs.get("u"):
            raw = qs["u"][0].replace("-", "+").replace("_", "/")
            raw += "=" * (-len(raw) % 4)
            return unquote(base64.b64decode(raw).decode("utf-8", "replace"))
        if qs.get("uddg"):
            return qs["uddg"][0]
    except Exception:
        pass
    return href


# 中文 2-gram 停用词（无信息量的片段，避免误判相关性）
_STOP_2GRAM = {"什么", "怎么", "如何", "为什么", "为啥", "一下", "哪些", "一个", "这个", "那个", "怎样", "能不能", "是不是", "可以", "应该", "到底"}


def _is_relevant(title: str, snippet: str, query: str) -> bool:
    """简单相关性（2026-08-16）：查询词的中文 2-gram 或英文词任一出现在标题/摘要即算相关。
    用于过滤 Bing 中文拆词的无关结果（如搜"给猫洗澡"返回"给"字百科）。"""
    try:
        text = f"{title} {snippet}".lower()
        q = query.lower()
        for w in re.findall(r"[a-z0-9]{3,}", q):
            if w in text:
                return True
        grams = {
            q[i:i + 2]
            for i in range(len(q) - 1)
            if "\u4e00" <= q[i] <= "\u9fff" and "\u4e00" <= q[i + 1] <= "\u9fff"
        }
        grams -= _STOP_2GRAM
        return any(g in text for g in grams)
    except Exception:
        return True


def _has_cjk(text: str) -> bool:
    """是否含中日韩文字（用于决定搜索引擎市场参数）"""
    return any("\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af" for ch in text)


def _sync_search(query: str) -> dict:
    """真实搜索（复用常驻 Edge 上下文，避免冷启动；上下文异常自动重建一次）"""
    for attempt in (1, 2):
        try:
            _p, ctx = _ensure_warm_ctx()
        except Exception as e:
            if attempt == 2:
                return {"ok": False, "message": f"搜索失败: {e}"}
            _reset_warm()
            continue
        try:
            return _sync_search_on_ctx(ctx, query)
        except Exception as e:
            if attempt == 2:
                return {"ok": False, "message": f"搜索失败: {e}"}
            _reset_warm()  # 常驻 ctx 异常 → 重建一次
    return {"ok": False, "message": "搜索失败"}


def _sync_search_on_ctx(ctx, query: str) -> dict:
    """在指定浏览器上下文内执行真实搜索：优先 Bing（就绪检测替代固定等待 + 跳转链接还原），
    无结果/验证码自动降级 DuckDuckGo lite。返回结构化结果 [{title, url, snippet}]，url 可点击。"""
    from urllib.parse import quote
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        results, engine = [], "bing"
        try:
            # 先访问 bing 首页建立会话 cookie，再搜索（提高结果链接完整性）
            page.goto("https://www.bing.com/", timeout=8000, wait_until="domcontentloaded")
            # 中文 query 强制 zh-CN 市场，避免被重定向到默认英文低质结果（2026-08-16）
            _search_url = ("https://www.bing.com/search?q=" + quote(query)
                           + "&mkt=zh-CN&setlang=zh-hans" if _has_cjk(query) else "https://www.bing.com/search?q=" + quote(query))
            page.goto(_search_url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_selector("li.b_algo", timeout=8000)
            # 等结果流式渲染完整（多数查询 ≥6 条；超时用已有结果）
            try:
                page.wait_for_function("() => document.querySelectorAll('li.b_algo').length >= 6", timeout=2500)
            except Exception:
                pass
            page.wait_for_timeout(500)
            results = page.evaluate(
                """() => Array.from(document.querySelectorAll('li.b_algo')).slice(0, 12).map(li => {
                    const a = li.querySelector('h2 a');
                    const p = li.querySelector('.b_caption p') || li.querySelector('.b_caption');
                    return {title: a ? a.textContent.trim() : '', href: a ? a.href : '', snippet: p ? p.textContent.trim() : ''};
                })"""
            )
        except Exception:
            results = []
        # 链接还原：u 参数优先（老格式），其余 ck/a 通过带 referer 的点击跳转解析
        raw = (results or [])[:12]
        decoded = {}
        for it in raw:
            h = it.get("href") or ""
            if h:
                u = _decode_redirect_url(h)
                # 仅当解码出真实 http 链接才采用；乱码/解密失败回退 ck/a 交给 popup 还原
                decoded[h] = u if u.startswith(("http://", "https://")) else h
        pending = [h for h, u in decoded.items() if u and "bing.com/ck/a" in u][:4]
        resolved = _resolve_bing_links(page, pending)
        clean = []
        for it in raw:
            h = it.get("href") or ""
            url = resolved.get(h) or decoded.get(h) or h
            title = (it.get("title") or "").strip()[:120]
            snippet = (it.get("snippet") or "").strip()[:220]
            if title and url.startswith("http"):
                clean.append({"title": title, "url": url, "snippet": snippet})
            if len(clean) >= 10:
                break
        # 相关性过滤（2026-08-16）：Bing 中文常拆词/自动化检测会返回无关结果；
        # 保留未过滤副本做最终兜底；过滤后全无关 → 视为无结果走 DDG 补充
        raw_clean = list(clean)
        if clean:
            relevant = [r for r in clean if _is_relevant(r.get("title", ""), r.get("snippet", ""), query)]
            clean = relevant if relevant else []
        # 有效结果不足 3 条 → 降级/补充 DuckDuckGo lite（可能触发验证码，仅一次；结果同样过滤+按 url 去重）
        if len(clean) < 3:
            engine = "duckduckgo"
            try:
                page.goto("https://html.duckduckgo.com/html/?q=" + quote(query), timeout=45000, wait_until="domcontentloaded")
                page.wait_for_selector(".result", timeout=8000)
                ddg = page.evaluate(
                    """() => Array.from(document.querySelectorAll('.result')).slice(0, 12).map(r => {
                        const a = r.querySelector('a.result__a');
                        const s = r.querySelector('a.result__snippet');
                        return {title: a ? a.textContent.trim() : '', href: a ? a.href : '', snippet: s ? s.textContent.trim() : ''};
                    })"""
                )
                seen = {r["url"] for r in clean}
                for it in (ddg or []):
                    url = _decode_redirect_url(it.get("href") or "")
                    title = (it.get("title") or "").strip()[:120]
                    snippet = (it.get("snippet") or "").strip()[:220]
                    if (title and url.startswith("http") and url not in seen
                            and _is_relevant(title, snippet, query)):
                        clean.append({"title": title, "url": url, "snippet": snippet})
                        seen.add(url)
                    if len(clean) >= 10:
                        break
            except Exception:
                pass  # DDG 失败不阻断：最终仍空时回退未过滤 Bing 结果
        if not clean:
            if raw_clean:
                # 过滤过严：回退未过滤的 Bing 结果（至少让用户/角色有内容可用）
                return {"ok": True, "engine": "bing", "results": raw_clean}
            return {"ok": False, "message": "未找到相关结果（可能触发验证码，可稍后重试）"}
        return {"ok": True, "engine": engine, "results": clean}
    except Exception as e:
        return {"ok": False, "message": f"搜索失败: {e}"}
    finally:
        try:
            page.close()
        except Exception:
            pass


def _resolve_bing_links(page, hrefs: list) -> dict:
    """在 Bing 搜索页上下文内，通过带 referer 的点击跳转还原 ck/a 链接为真实 URL。
    返回 {原链接: 真实链接}；无法还原的保持原链接。"""
    out = {}
    for h in hrefs or []:
        if not h or "bing.com/ck/a" not in h:
            continue
        try:
            with page.expect_popup(timeout=8000) as popup_info:
                page.evaluate("""(h) => {
                    const a = document.createElement('a');
                    a.href = h; a.target = '_blank'; a.rel = 'opener';
                    document.body.appendChild(a); a.click(); a.remove();
                }""", h)
            popup = popup_info.value
            try:
                popup.wait_for_url(lambda u: "bing.com/ck/a" not in u, timeout=8000)
            except Exception:
                pass
            popup.wait_for_timeout(800)
            final = popup.url or ""
            out[h] = final if final and "bing.com/ck/a" not in final else h
            try:
                popup.close()
            except Exception:
                pass
        except Exception:
            out[h] = h
    return out


async def search_web(query: str) -> dict:
    """搜索网页，返回结构化结果 [{title, url, snippet}]（Bing 优先，自动降级 DuckDuckGo）"""
    query = (query or "").strip()[:100]
    if not query:
        return {"ok": False, "message": "关键词不能为空"}
    return await _run_sync(_sync_search, query)


router = sdk.router()


@router.post("/browse")
async def browse_route(payload: dict):
    """浏览一个网页：{url}；返回标题/正文摘要/图片链接"""
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"ok": False, "message": "url 必填"}
    return await browse(url)


@router.get("/latest")
async def latest():
    """最近浏览快照（仅元信息）"""
    return {"ok": True, "snapshots": await _recent_snapshots(5)}


@router.post("/search")
async def search_route(payload: dict):
    """搜索网页：{query}；返回结构化结果列表（真实 URL，可点击打开）"""
    return await search_web(str(payload.get("query") or ""))


@sdk.hook("context_inject")
async def inject(ctx):
    try:
        await _ensure_schema()
        cfg = sdk.get_config()
        if not bool(cfg.get("enabled", False)):
            return
        global _last_inject_ts
        inject_min = int(cfg.get("inject_minutes", 480))
        if time.time() - _last_inject_ts < inject_min * 60:
            return
        snaps = await _recent_snapshots(3)
        if not snaps:
            return
        lines = ["【你最近浏览过的网页】（仅域名与标题摘要，不含正文）："]
        for s in snaps:
            lines.append(f"- {s['domain']}：{(s['title'] or '')[:60]}")
        _last_inject_ts = time.time()
        ctx["context_messages"].append({"role": "system", "content": "\n".join(lines)})
        sdk.log("browser 已注入浏览摘要 (char=%s)", ctx.get("character_id"))
    except Exception as e:
        sdk.log("browser 注入失败: %s", e)


@sdk.hook("schedule_tick")
async def on_tick(ctx):
    """清理 >30 分钟过期快照（记忆隔离：短期上下文 24h 内自然消失）+ Edge 常驻预热兜底"""
    try:
        await _ensure_schema()
        from app.db.database import async_session_factory
        from app.models.user import BrowserSnapshot
        from sqlalchemy import delete
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        async with async_session_factory() as db:
            await db.execute(delete(BrowserSnapshot).where(BrowserSnapshot.created_at < cutoff))
            await db.commit()
    except Exception:
        pass
    # Edge 预热兜底（plans #39）：常驻 ctx 未就绪时后台预热（每 5 分钟最多尝试一次；失败静默）
    try:
        global _last_warm_attempt_ts
        if _WARM.get("ctx") is None and time.time() - _last_warm_attempt_ts > 300:
            _last_warm_attempt_ts = time.time()
            asyncio.ensure_future(warmup())
    except Exception:
        pass
