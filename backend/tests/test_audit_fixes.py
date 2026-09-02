"""审计第一批修复测试（P1-01/03/05/06，2026-08-15）"""
from app.memory.extractor import _is_empty_val
from app.scheduling.arbiter import REJECTED_LOG_THROTTLE_SECONDS


def test_is_empty_val():
    assert _is_empty_val("无")
    assert _is_empty_val("（无）")
    assert _is_empty_val("(无)")
    assert _is_empty_val("（空）")
    assert _is_empty_val("空")
    assert _is_empty_val("无。")
    assert not _is_empty_val("")  # 空串由调用方 val 条件拦截
    assert not _is_empty_val("用户喜欢喝美式咖啡")
    assert not _is_empty_val("无糖奶茶很好喝")


def test_rejected_throttle_constant():
    assert REJECTED_LOG_THROTTLE_SECONDS == 300
