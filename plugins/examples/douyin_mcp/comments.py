"""抖音 MCP：评论抓取/回复（#67，2026-08-27）。

P0/P3 核心：评论回复从「纯 DOM 操作」升级为「双轨制 —— 内部 API 优先（page.evaluate fetch，
浏览器自动带 cookie + 签名 header），DOM 兜底」。评论抓取同样优先解析内部 API 响应（JSON 结构化，
不依赖 CSS hash class），DOM 作兜底。

> ⚠️ 抓包确认（方案第六节）：评论内部 API 的 URL/参数需登录态 + 人工 DevTools 抓包确认。
> dsh 无法实测，故把 URL/参数做成可配置常量 + 清晰 TODO 注释；「page.evaluate fetch 优先，
> 失败回退 DOM」的双轨代码先落地，抓包后只需核对/调整常量。实施后【未实测】。

对外暴露：``_sync_reply_comment_v2``（主入口）/ ``_sync_reply_comment_dom``（兜底）/
``_JS_REPLY_COMMENT`` / ``_JS_FETCH_COMMENTS`` / ``_find_comment_items``。
"""
from __future__ import annotations

import json
import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from browser import (
    _COMMENT_MANAGE_URL,
    _close_ctx,
    _has_login_cookie,
    _human_wait,
    _launch,
    _shot,
)

# ------------------------------------------------------------------ 抓包确认常量（TODO）
# 以下 URL/字段名来自方案 3.4 的调研推断，需人工在 DevTools Network（筛选 comment）抓包确认。
# 抓包确认步骤见 docs/douyin-mcp-upgrade-plan.md 第六节。确认后只需调整这些常量，代码无需改动。
# 若确认带签名参数（x-bogus/a-bogus）：保持 page.evaluate fetch（浏览器自动带签名），不要改 Python requests。
_COMMENT_REPLY_API_URL = "https://creator.douyin.com/aweme/v1/comment/reply/"  # TODO(抓包确认)：可能是 /aweme/v2/comment/reply/
_COMMENT_LIST_API_URL = "https://creator.douyin.com/aweme/v1/comment/list/"  # TODO(抓包确认)：评论列表接口

# 从浏览器页面上下文发 fetch（自动带 cookie + 签名 header；比 Python requests 稳定 10 倍）
_JS_REPLY_COMMENT = """
async (params) => {
    const formData = new URLSearchParams();
    formData.append('comment_id', params.comment_id || '');
    formData.append('text', params.text || '');
    formData.append('item_id', params.item_id || '');
    formData.append('reply_id', '0');
    try {
        const resp = await fetch(params.url, {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: formData,
            credentials: 'include',
        });
        const data = await resp.json();
        return { ok: data.status_code === 0, data: data };
    } catch (e) {
        return { ok: false, error: String(e) };
    }
}
"""

_JS_FETCH_COMMENTS = """
async (params) => {
    const url = new URL(params.url);
    url.searchParams.set('item_id', params.item_id || '');
    url.searchParams.set('count', params.count ? String(params.count) : '20');
    url.searchParams.set('cursor', params.cursor ? String(params.cursor) : '0');
    try {
        const resp = await fetch(url, {credentials: 'include'});
        const data = await resp.json();
        return { ok: true, comments: (data.comments || []).map(c => ({
            cid: c.cid,
            text: c.text,
            nickname: (c.user || {}).nickname,
            is_fans: !!(c.user && c.user.follow_status),
            create_time: c.create_time,
        }))};
    } catch (e) {
        return { ok: false, error: String(e) };
    }
}
"""


async def _get_cached_comment_ids(post_key: str, commenter: str) -> tuple[str, str]:
    """从 DB 读缓存的目标评论真实 comment_id + item_id（aweme_id）（轮询时从 API 拦截存入）。

    无缓存返回 ("", "")。DB 查询失败静默回退 DOM。
    """
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.douyin import DouyinComment
        async with async_session_factory() as db:
            row = (await db.execute(
                select(DouyinComment).where(
                    DouyinComment.douyin_post_id == post_key,
                    DouyinComment.commenter == commenter,
                ).order_by(DouyinComment.created_at.asc()).limit(1)
            )).scalars().first()
            if row:
                return (row.comment_id or "", row.aweme_id or "")
    except Exception:
        pass
    return "", ""


def _find_comment_items(page) -> list:
    """DOM 兜底：优先用文本+结构定位评论项，class 前缀只作兜底（CSS hash 易变）。

    多策略：
    1. 找包含「回复」按钮的容器（作者评论除外）；
    2. 退化为 class 前缀 `[class^="cmt-li-"]`。
    """
    try:
        items = page.evaluate(
            """() => {
                const all = Array.from(document.querySelectorAll('[class^="cmt-li-"], [class*="comment"], [class*="comment-item"]'));
                const out = [];
                for (const li of all) {
                    const txt = (li.innerText || '').trim();
                    if (!txt) continue;
                    if (/作者/.test(txt) && !/回复/.test(txt)) continue;
                    out.push(li);
                }
                return out.length;
            }"""
        )
        return items or []
    except Exception:
        return []


def _sync_reply_comment_dom(page, post_title: str, commenter: str, reply_text: str) -> dict:
    """DOM 兜底回复：切到目标作品 → 点评论「回复」→ 输入 → 发送；返回 {ok, message}。"""
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
    return {"ok": True, "message": "回复已发送"}


def _sync_reply_comment_v2(post_key: str, commenter: str, reply_text: str,
                           comment_id: str = "", item_id: str = "") -> dict:
    """回复评论 v2（#67 P0）：优先走内部 API（page.evaluate fetch），失败回退 DOM。

    - 有缓存的 comment_id + item_id（由 main.py 异步查 _get_cached_comment_ids 传入）→ 先走 API；
    - API 失败/无缓存 → DOM 兜底（_sync_reply_comment_dom）。
    返回 {ok, message, mode}（mode ∈ api/dom）。同步函数（跑在 playwright 执行器线程）。
    """
    p, ctx = _launch(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(_COMMENT_MANAGE_URL, timeout=60000)
        page.wait_for_timeout(7000)
        if not _has_login_cookie(ctx):
            return {"ok": False, "message": "未登录，无法回复", "mode": "none"}

        if comment_id and item_id:
            try:
                result = page.evaluate(_JS_REPLY_COMMENT, {
                    "url": _COMMENT_REPLY_API_URL,
                    "comment_id": comment_id, "text": reply_text, "item_id": item_id,
                })
                if isinstance(result, dict) and result.get("ok"):
                    return {"ok": True, "message": "回复已发送（内部 API）", "mode": "api"}
                sdk_log("API 回复失败，回退 DOM: %s", result.get("error") if isinstance(result, dict) else result)
            except Exception as e:
                sdk_log("API 回复异常，回退 DOM: %s", e)

        # DOM 兜底
        result = _sync_reply_comment_dom(page, post_key, commenter, reply_text)
        result["mode"] = "dom"
        return result
    except Exception as e:
        return {"ok": False, "message": f"回复失败: {e}", "mode": "none"}
    finally:
        _close_ctx(p, ctx)


def sdk_log(msg: str, *args) -> None:
    """轻量日志封装（避免与 main 耦合成循环导入）。"""
    try:
        from app.plugins import sdk
        sdk.log(msg, *args)
    except Exception:
        pass
