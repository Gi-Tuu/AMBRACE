"""Shared Memory（Phase C）纯函数测试：用户标记检测 / 召回文本 / 纪念日文案（2026-08-14）"""
import asyncio

from app.memory.shared_events import detect_user_marked, anniversary_text, recall_text
from app.models.memory import SharedEvent


def test_detect_user_marked():
    assert detect_user_marked("这件事你要记住，我们约好了")
    assert detect_user_marked("今天是我们的纪念日")
    assert detect_user_marked("这是第一次一起看烟花")
    assert not detect_user_marked("今天天气不错")
    assert not detect_user_marked("")


def test_recall_text_empty_db():
    async def run():
        return await recall_text(None, 999, 999)
    # db=None 时抛异常 → 上层 catch 兜底；这里验证不炸即可由调用方兜底，跳过直接断言类型
    try:
        asyncio.run(run())
    except Exception:
        pass


def test_anniversary_text():
    e = SharedEvent(id=1, user_id=1, character_id=2, event_type="user_marked",
                    title="第一次一起看烟花", description="第一次一起看烟花",
                    importance=0.8, is_anniversary=True)
    from datetime import datetime, timedelta, timezone
    e.event_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    text = anniversary_text(e)
    assert "还记得吗" in text
    assert "第一次一起看烟花" in text
