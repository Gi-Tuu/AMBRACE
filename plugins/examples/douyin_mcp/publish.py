"""抖音 MCP：发布逻辑（#67，2026-08-27）。

- ``_sync_publish_image``：图文发布（上传图片 → 填标题/描述 → 选音乐(可选) → 点发布）；
- ``_sync_publish_video``：视频发布（上传 → 等转码 → 填标题/描述 → 选音乐 → 选封面 → 发布；
  create_v2 响应拦截取 item_id；转码超时 120s 返回明确失败）；
- ``_images_to_video``：FFmpeg 把多张图+BGM 合成 9:16 竖版视频（Ken Burns 缓动；
  本机 ffmpeg 不可用时返回 False 并留 TODO）。

浏览器 page 操作（``_click_publish`` / ``_fill_image_form`` / ``_clear_publish_form_cache`` /
``_close_draft_modal`` / 短信验证码 / ``_resolve_upload_path``）集中在 publish.py，供图文/视频通用。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import subprocess
from pathlib import Path

from browser import (
    _COMMENT_MANAGE_URL,
    _CONTENT_MANAGE_URL,
    _close_ctx,
    _has_login_cookie,
    _human_tap,
    _human_typing,
    _human_wait,
    _launch,
    _shot,
)
from music import _select_music

# 视频上传页（创作者中心 → 发布视频）
_VIDEO_UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
_UPLOAD_IMAGE_URL = "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"


def _post_key(title: str) -> str:
    return hashlib.md5((title or "").encode("utf-8")).hexdigest()[:16]


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
    """真实点击抖音发布表单的「发布」提交按钮（避开导航入口/「暂存离开」）。"""
    for _ in range(3):
        try:
            loc = page.get_by_role("button", name="发布", exact=True)
            if loc.count() > 0:
                el = loc.last
                if el.is_enabled(timeout=2000):
                    el.scroll_into_view_if_needed(timeout=4000)
                    _human_tap(page, el, timeout=5000)
                    return True
        except Exception:
            pass
        try:
            btns = page.locator("button[class*=primary-]")
            for i in range(btns.count()):
                el = btns.nth(i)
                txt = (el.inner_text(timeout=1500) or "").strip()
                if txt == "发布" and el.is_enabled(timeout=1500):
                    el.scroll_into_view_if_needed(timeout=4000)
                    _human_tap(page, el, timeout=5000)
                    return True
        except Exception:
            pass
        page.wait_for_timeout(2000)
    return False


def _clear_publish_form_cache(page) -> None:
    """清除抖音发布表单缓存 localStorage publish_form_cache:*（「你还有上次未发布的图文」弹窗根因）"""
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
    """填充抖音图文上传表单：上传图片（已有预览则跳过）→ 填标题/描述（随机打字延迟）"""
    try:
        _b = page.evaluate("() => document.body.innerText || ''")
        _has_upload = ("点击上传" in _b) or ("直接将图片文件拖入此区域" in _b) or ("添加作品标题" in _b)
        existing = [str(_resolve_upload_path(im)) for im in images if _resolve_upload_path(im) is not None]
        if existing and _has_upload:
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
            _ed = page.locator("[contenteditable=true]").first
            _ed.click(timeout=5000)
            _human_typing(page, (desc or "")[:1000], (30, 80))
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
                    return {"ok": False, "need_manual": True, "message": "抖音发布要求短信验证码（风控），需人工完成验证后发布"}
                if any(kw in body for kw in ("发布成功", "作品已发布", "发布完成")):
                    return {"ok": True, "message": "发布成功", "post_id": _post_id["id"] or _post_key(title or "")}
                if "content/manage" in url:
                    return {"ok": True, "message": "已提交发布（已跳转内容管理）", "post_id": _post_id["id"] or _post_key(title or "")}
                if "是否继续编辑" in body or "继续编辑" in body:
                    _modal_hit = True
                    try:
                        _clear_publish_form_cache(page)
                        page.get_by_text("放弃", exact=True).first.click(timeout=4000)
                        page.wait_for_timeout(2500)
                        _clear_publish_form_cache(page)
                        _close_draft_modal(page)
                        _fill_image_form(page, images, title, desc)
                    except Exception:
                        _close_draft_modal(page)
                    break
            if _modal_hit:
                continue
            if _UPLOAD_IMAGE_URL not in url and "creator-micro" in url and "/content/" not in url:
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
        return {"ok": False, "message": "发布后未确认成功（请人工检查）：" + body[:120]}
    except Exception as e:
        return {"ok": False, "message": f"发布失败: {e}"}
    finally:
        _close_ctx(p, ctx)


def _sync_publish_video(video_path: str, title: str, desc: str,
                        music_keyword: str = "", cover_path: str = "") -> dict:
    """发布视频（#67 P2）：上传 → 等转码（≤120s） → 填标题/描述 → 选音乐 → 选封面 → 发布。

    通过 create_v2 响应拦截取 item_id；转码超时返回明确失败。
    """
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
        page.goto(_VIDEO_UPLOAD_URL, timeout=60000)
        page.wait_for_timeout(5000)
        if not _has_login_cookie(ctx):
            return {"ok": False, "message": "未登录，无法发布"}
        _clear_publish_form_cache(page)
        _close_draft_modal(page)

        # 1. 上传视频
        # 路径规范化（#67 P2 修复）：video_path 可能是 /uploads/... 相对 URL，需解析为文件系统绝对路径
        if not os.path.isfile(video_path):
            _resolved = _resolve_upload_path(video_path)
            if _resolved is None:
                return {"ok": False, "message": f"视频文件不存在: {video_path}"}
            video_path = str(_resolved)
        if not os.path.isfile(video_path):
            return {"ok": False, "message": f"视频文件不存在: {video_path}"}
        try:
            fi = page.locator("input[type=file]").first
            if fi.count() > 0:
                fi.set_input_files(video_path)
            else:
                return {"ok": False, "message": "未找到视频上传入口"}
        except Exception as e:
            return {"ok": False, "message": f"视频上传失败: {e}"}

        # 2. 等待转码（关键：视频需转码，比图文久；标题输入框出现即视为转码完成）
        try:
            page.wait_for_selector('input[placeholder*="标题"]', timeout=120000)
        except Exception:
            return {"ok": False, "message": "视频上传/转码超时（>120s）"}

        # 3. 填标题/描述
        page.wait_for_timeout(2000)
        try:
            page.locator('input[placeholder*="标题"]').first.fill((title or "")[:30])
        except Exception:
            pass
        if desc:
            try:
                _ed = page.locator("[contenteditable=true]").first
                _ed.click()
                _human_typing(page, (desc or "")[:1000], (30, 80))
            except Exception:
                pass

        # 4. 选音乐（可选）
        if music_keyword:
            _select_music(page, music_keyword)

        # 5. 选封面（可选，用户提供图片）
        if cover_path:
            try:
                page.get_by_text("上传封面", exact=False).first.click(timeout=3000)
                page.wait_for_timeout(1000)
                page.locator("input[type=file]").last.set_input_files(cover_path)
                page.wait_for_timeout(3000)
            except Exception:
                pass

        # 6. 点发布
        page.wait_for_timeout(2000)
        for _attempt in range(3):
            _close_draft_modal(page)
            _click_publish(page)
            page.wait_for_timeout(5000)
            if _has_sms_verify_modal(page):
                _close_sms_verify_modal(page)
                _shot(page, "video_publish_need_sms_verify")
                return {"ok": False, "need_manual": True, "message": "抖音发布要求短信验证码（风控），需人工完成验证后发布"}
            try:
                body = page.evaluate("() => (document.body.innerText || '').slice(0, 800)")
                url = page.url or ""
            except Exception:
                body, url = "", ""
            if any(kw in body for kw in ("发布成功", "作品已发布", "发布完成")):
                return {"ok": True, "message": "视频发布成功", "post_id": _post_id["id"]}
            if "content/manage" in url:
                return {"ok": True, "message": "视频已提交发布（已跳转内容管理）", "post_id": _post_id["id"]}
            if _post_id["id"]:
                return {"ok": True, "message": "视频发布成功（create_v2 已返回 item_id）", "post_id": _post_id["id"]}
        return {"ok": False, "message": "视频发布后未确认成功（请人工检查）"}
    except Exception as e:
        return {"ok": False, "message": f"视频发布失败: {e}"}
    finally:
        _close_ctx(p, ctx)


# ------------------------------------------------------------------ FFmpeg（P3）
def _ffmpeg_available() -> bool:
    """本机 ffmpeg 是否可用（where ffmpeg；P3 能力开关）。"""
    try:
        r = subprocess.run(
            ["where", "ffmpeg"], capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


# 包 D①（2026-09-06 待排期清理）：多图轮播 + Ken Burns + BGM。
# 纯函数（_build_carousel_filter / _build_video_cmd）不依赖 ffmpeg 可执行，独立单测；
# _images_to_video 保持「ffmpeg 不可用/无图/无音乐 → False」的可用优先退路（调用方留提示）。
def _build_carousel_filter(n_images: int, duration_per_image: float = 3.0, fps: int = 30) -> str:
    """构造多图轮播 filter_complex（纯函数）：每图一段 9:16 裁剪 + Ken Burns（奇偶交替放大/缩小），
    concat 成一条视频轨。n_images>=1；单图退化为原 Ken Burns 行为。"""
    n = max(1, int(n_images))
    dur = max(1.0, float(duration_per_image))
    frames = max(int(dur * fps), 1)
    parts = []
    for i in range(n):
        if i % 2 == 0:
            z = f"zoompan=z='min(1.0+0.10*on/{frames},1.15)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={fps}"
        else:
            z = f"zoompan=z='max(1.15-0.10*on/{frames},1.0)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={fps}"
        parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,{z},format=yuv420p[s{i}]"
        )
    concat_in = "".join(f"[s{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=0,format=yuv420p[v]")
    return ";".join(parts)


def _build_video_cmd(images: list[str], music_path: str, output_path: str,
                     duration_per_image: float = 3.0) -> list[str]:
    """构造 ffmpeg 参数列表（纯函数，shell=False 安全）：每图一个 -loop 输入段 + 音乐轨（-shortest）。"""
    dur = max(1.0, float(duration_per_image))
    cmd = ["ffmpeg", "-y"]
    for img in images:
        cmd += ["-loop", "1", "-t", f"{dur:g}", "-i", img]
    cmd += ["-i", music_path]
    cmd += ["-filter_complex", _build_carousel_filter(len(images), dur),
            "-map", "[v]", "-map", f"{len(images)}:a",
            "-shortest", "-r", "30", output_path]
    return cmd


def _images_to_video(images: list[str], music_path: str, output_path: str,
                     duration_per_image: float = 3.0) -> bool:
    """用 FFmpeg 把多张图+BGM 合成 9:16 竖版视频（多图轮播 + Ken Burns 缓动 + BGM）。

    本机 ffmpeg 不可用或无图片/音乐时返回 False（调用方留提示）。
    """
    if not _ffmpeg_available():
        return False
    if not images or not music_path:
        return False
    cmd = _build_video_cmd(images, music_path, output_path, duration_per_image)
    try:
        r = subprocess.run(cmd, shell=False, capture_output=True, timeout=180)
        return r.returncode == 0 and os.path.isfile(output_path)
    except Exception:
        return False
