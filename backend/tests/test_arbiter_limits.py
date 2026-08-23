"""主动消息限额一致性测试：免打扰窗口 / 限额常量（纯函数）。

对应工程规范（docs/engineering-protocol.md）：Scheduler 负责「什么时候发生」、
主动消息防骚扰限额。
"""

from app.scheduler.arbiter import MAX_PER_HOUR, UNREPLIED_COOLDOWN_LIMIT, _in_dnd_window


def test_免打扰窗口_普通时段():
    # 09:00-12:00：10:00 在内，13:00 不在
    assert _in_dnd_window(10 * 60, (9 * 60, 12 * 60))
    assert not _in_dnd_window(13 * 60, (9 * 60, 12 * 60))
    # 边界：start 在内，end 不在（半开区间）
    assert _in_dnd_window(9 * 60, (9 * 60, 12 * 60))
    assert not _in_dnd_window(12 * 60, (9 * 60, 12 * 60))


def test_免打扰窗口_跨天时段():
    # 23:00-08:00：凌晨 0 点在内，中午 12 点不在
    assert _in_dnd_window(0, (23 * 60, 8 * 60))
    assert _in_dnd_window(23 * 60, (23 * 60, 8 * 60))
    assert not _in_dnd_window(12 * 60, (23 * 60, 8 * 60))


def test_免打扰窗口_起止相同():
    # start == end 视为未配置
    assert not _in_dnd_window(0, (10 * 60, 10 * 60))


def test_限额常量():
    # 每小时最多 2 条；连续 2 条未回复进入 24h 冷却
    assert MAX_PER_HOUR == 2
    assert UNREPLIED_COOLDOWN_LIMIT == 2
