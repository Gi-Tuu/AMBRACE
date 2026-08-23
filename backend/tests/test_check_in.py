"""查岗：前台应用解析测试（2026-08-15）"""
import re


def _extract_foreground_app(content: str) -> str:
    """与 phone_service.get_check_in_foreground_app 相同的解析逻辑"""
    m = re.search(r"前台应用[:：]\s*([^；;\n]+)", content or "")
    if not m:
        return ""
    app = m.group(1).strip()
    if app and app not in ("无", "unknown", "未识别", "未知"):
        return app
    return ""


def test_extract_foreground_app():
    assert _extract_foreground_app("手机状态：屏幕亮起；前台应用：微信；电池 92%") == "微信"
    assert _extract_foreground_app("手机状态：屏幕亮起；前台应用：抖音") == "抖音"


def test_extract_no_foreground():
    assert _extract_foreground_app("手机状态：屏幕熄灭") == ""
    assert _extract_foreground_app("") == ""
    assert _extract_foreground_app("前台应用：无") == ""
