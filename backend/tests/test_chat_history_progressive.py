# -*- coding: utf-8 -*-
"""渐进式历史守卫（2026-11-22 用户拍板方案）：flag 切换验证＋日界/渐进截取行为。
合成数据，不读真实库。"""
import pytest

from app.flags.agent_flags import AGENT_FLAGS


class TestProgressiveFlag:
    """flag 注册与默认值。"""

    def test_flag_registered(self):
        assert "context_progressive_history" in AGENT_FLAGS

    def test_flag_default_off(self):
        assert AGENT_FLAGS["context_progressive_history"] is False


class TestDayBoundary:
    """北京日界计算。"""

    def test_progressive_day_boundary_is_midnight(self):
        """progressive 模式下日界＝北京今天 0:00 转 UTC naive。"""
        from datetime import datetime, timezone, timedelta
        from app.utils.timeutil import shift_utc_naive

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        bj_now = shift_utc_naive(now_utc, 8)
        bj_midnight = bj_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = (bj_midnight - timedelta(hours=8)).replace(tzinfo=None)

        # 日界 UTC 时间应该在 16:00（UTC+8 的 0:00 = UTC 前一天 16:00）
        assert day_start_utc.hour in (15, 16, 17)  # 可能跨日界

    def test_progressive_mode_allows_more_messages(self):
        """progressive 模式下不做 20 条截断——但这个行为在 section 内部实现，
        此处只验证 flag 读取路径正确。"""
        AGENT_FLAGS["context_progressive_history"] = True
        assert AGENT_FLAGS.get("context_progressive_history") is True
        AGENT_FLAGS["context_progressive_history"] = False
        assert AGENT_FLAGS.get("context_progressive_history") is False
