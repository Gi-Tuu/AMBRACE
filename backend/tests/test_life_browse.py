"""Phase B 补充测试：browse 真实化降级逻辑（2026-08-14）"""
import asyncio
import sys as _sys

from app.life.activity import _BROWSE_FALLBACK_KEYWORDS, _real_browse


def test_real_browse_no_plugin_returns_none():
    """无浏览器插件加载时静默降级（返回 None，上层走 LLM 模式）"""
    _sys.modules.pop("ai_plugin_browser_mcp", None)

    async def run():
        return await _real_browse(None, 4, None, "browse")

    assert asyncio.run(run()) is None


def test_fallback_keywords_nonempty():
    assert _BROWSE_FALLBACK_KEYWORDS
    assert all(isinstance(k, str) and k for k in _BROWSE_FALLBACK_KEYWORDS)
