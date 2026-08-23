"""项目版本读取（P1-2，2026-08-23）：从项目根 VERSION 文件解析，避免多处硬编码版本号漂移。

用法：from app.utils.version import get_project_version
VERSION 文件格式（utf-8-sig，兼容 BOM）：
  PROJECT: 拥爱（AMBRACE）
  VERSION: 3.2.0
"""
from pathlib import Path

_DEFAULT = "unknown"
# __file__ = backend/app/utils/version.py -> parents[2]=backend -> parents[3]=项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_VERSION_FILE = _PROJECT_ROOT / "VERSION"


def get_project_version() -> str:
    """读取项目根 VERSION 中的 VERSION: 行；文件缺失/解析失败时返回 'unknown'。"""
    try:
        text = _VERSION_FILE.read_text(encoding="utf-8-sig")
    except Exception:
        return _DEFAULT
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("VERSION:"):
            val = line[len("VERSION:"):].strip()
            return val or _DEFAULT
    return _DEFAULT
