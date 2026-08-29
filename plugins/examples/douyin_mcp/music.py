"""抖音 MCP：音乐选择（#67，2026-08-27）。

- ``MUSIC_MOODS``：安全 BGM 情绪关键词池（AI 从内容情绪里挑，不指定具体歌名——歌名易变/搜索不稳）。
- ``parse_music_mood``：从 LLM 输出解析「音乐:情绪」行，非白名单返回空串。
- ``_select_music``：在抖音发布页操作「选择音乐」面板（关键词搜索或选推荐），图文/视频发布流程通用。

纯函数（``MUSIC_MOODS`` / ``parse_music_mood``）不依赖 Playwright，便于单测；``_select_music``
为浏览器 page 操作，仅在真实发布流程调用。
"""
from __future__ import annotations

import random
import re

# AI 选音乐策略：不指定具体歌名，从情绪关键词池中选（歌名变化大、搜索不稳定）
MUSIC_MOODS = ["治愈", "欢快", "安静", "伤感", "可爱", "日常", "文艺", "轻松"]

# 音乐情绪关键词 → 搜索词（可用中文直接搜；可扩展更多别名）。
# 键为「AI 可能输出的情绪词」（含白名单 + 常见别名），值为归一化后的白名单情绪。
_MUSIC_MOOD_ALIASES: dict[str, str] = {
    "治愈": "治愈", "愈": "治愈",
    "欢快": "欢快", "开心": "欢快", "快乐": "欢快", "愉悦": "欢快",
    "安静": "安静", "宁静": "安静", "舒缓": "安静", "平静": "安静",
    "伤感": "伤感", "悲伤": "伤感", "难过": "伤感", "忧伤": "伤感",
    "可爱": "可爱", "萌": "可爱",
    "日常": "日常", "生活": "日常",
    "文艺": "文艺", "民谣": "文艺",
    "轻松": "轻松", "轻快": "轻松",
}

# LLM 输出里「音乐:情绪」行的识别模式（兼容 音乐：/音乐:，中英文冒号）
_MUSIC_LINE_RE = re.compile(r"[\[【]?\s*音乐\s*[:：]\s*([^\s\]】\n]+)", re.I)

# 「使用」按钮的候选文本（图/视频发布页共用）
_MUSIC_BTN_TEXTS = ("选择音乐", "添加音乐", "背景音乐")


def pick_music_mood(fallback: str = "") -> str:
    """随机挑一个情绪关键词（vlog/日常安全池）；fallback 非空且在白名单内则用 fallback。"""
    mood = (fallback or "").strip()
    if mood in MUSIC_MOODS:
        return mood
    return random.choice(MUSIC_MOODS)


def normalize_music_mood(mood: str) -> str:
    """把任意情绪词归一化到 MUSIC_MOODS 白名单；无命中返回空串。"""
    m = (mood or "").strip().lower()
    for key, canonical in _MUSIC_MOOD_ALIASES.items():
        if m == key.lower():
            return canonical
    # 直接匹配白名单（含大写/全半角已由 lower 处理）
    for mm in MUSIC_MOODS:
        if m == mm.lower():
            return mm
    return ""


def parse_music_mood(text: str, default: str = "") -> str:
    """从 LLM 输出文本解析「音乐:情绪」行，返回归一化后的白名单情绪；找不到返回 default（已归一化）。

    例：``...（最后一行）音乐:治愈`` → ``"治愈"``；``音乐：欢快``（全角冒号）→ ``"欢快"``。
    """
    text = text or ""
    m = _MUSIC_LINE_RE.search(text)
    if m:
        mood = normalize_music_mood(m.group(1))
        if mood:
            return mood
    if default:
        return normalize_music_mood(default) or default
    return ""


def _human_click(page, locator, timeout: int = 5000):
    """点击前加随机人反应延迟（200-800ms），模拟真人点击（2026-08-27 反检测）。"""
    try:
        page.wait_for_timeout(random.randint(200, 800))
    except Exception:
        pass
    locator.click(timeout=timeout)


def _select_music(page, keyword: str = "", prefer_trending: bool = True) -> bool:
    """在抖音发布页选择背景音乐（图文+视频通用）。

    - keyword：搜索关键词（情绪词）；空则选第一首推荐；
    - prefer_trending：优先选推荐列表第一首（无关键词时）。
    返回是否已成功点「使用」。
    """
    try:
        music_btn = None
        for txt in _MUSIC_BTN_TEXTS:
            loc = page.get_by_text(txt, exact=False)
            if loc.count() > 0:
                music_btn = loc.first
                break
        if music_btn is None:
            return False
        _human_click(page, music_btn)
        page.wait_for_timeout(2000)

        if keyword:
            try:
                search = page.locator('input[placeholder*="搜索"], input[placeholder*="音乐"]').first
                if search.count() > 0:
                    search.click(timeout=3000)
                    page.wait_for_timeout(random.randint(200, 500))
                    search.fill(keyword)
                    page.wait_for_timeout(random.randint(500, 900))
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(2000)
            except Exception:
                pass

        # 点第一首歌的「使用」
        use_btn = page.get_by_text("使用", exact=True).first
        if use_btn.count() > 0:
            _human_click(page, use_btn)
            page.wait_for_timeout(1500)
            return True

        # 兜底：双击歌曲名（推荐列表）
        songs = page.locator('[class*="music-item"], [class*="song-item"]')
        if songs.count() > 0:
            try:
                page.wait_for_timeout(random.randint(200, 600))
                songs.first.dblclick(timeout=3000)
                page.wait_for_timeout(1500)
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False
