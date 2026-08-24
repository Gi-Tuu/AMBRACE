"""一键验证脚本：ruff → py_compile → pytest → flutter analyze/test → 接口冒烟。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\verify.py [--smoke]

--smoke：额外跑接口冒烟（登录 + 角色 + 朋友圈 + 归档，test/test123 账号）。
"""
import subprocess
import sys
from pathlib import Path
import os as _os

ROOT = Path(__file__).resolve().parent.parent
# P1-5：跨平台 venv python 路径（Windows=Scripts/python.exe，Linux/macOS=bin/python）
PY = str((ROOT / "backend/.venv/Scripts/python.exe") if _os.name == "nt" else (ROOT / "backend/.venv/bin/python"))
import shutil as _shutil
FLUTTER = _shutil.which("flutter") or "flutter.bat"  # P1：优先 PATH
STEPS = []


def step(name: str, cmd: list[str], cwd: Path) -> None:
    print(f"\n===== {name} =====")
    r = subprocess.run(cmd, cwd=str(cwd), check=False)
    if r.returncode != 0:
        print(f"[FAIL] {name}")
        sys.exit(1)
    print(f"[OK] {name}")


def main() -> None:
    step("ruff 静态检查（backend/app）", [PY, "-m", "ruff", "check", "backend/app"], ROOT)
    step("py_compile 全量语法校验", [PY, "-m", "compileall", "-q", "-f", "backend/app"], ROOT)
    step("pytest 后端测试", [PY, "-m", "pytest", "tests", "-q"], ROOT / "backend")
    step("flutter analyze", [FLUTTER, "analyze"], ROOT / "flutter_app")
    step("flutter test", [FLUTTER, "test"], ROOT / "flutter_app")

    if "--smoke" in sys.argv:
        step("接口冒烟（登录/角色/朋友圈/归档）", [PY, "scripts/smoke_test.py"], ROOT)

    print("\n===== 全部通过 =====")


if __name__ == "__main__":
    main()
