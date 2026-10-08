"""一键验证脚本：ruff → 公开仓卫生扫描 → py_compile → pytest → flutter analyze/test → 接口冒烟。

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
    """按 ruff → py_compile → pytest → flutter → smoke 顺序跑一遍。

    pytest 段必须带 `--basetemp=.pytest_tmp`：不带时 pytest 用系统 `%TEMP%\\pytest-of-<user>`，
    该目录权限坏掉时整段用例直接全灭（实测不带 8 errors / 带 8 passed），与代码本身无关；
    指向仓库内 basetemp 后每轮自行清空重建，也是 AGENTS.md 规定的本机统一口径。
    """
    step("ruff 静态检查（backend/app）", [PY, "-m", "ruff", "check", "backend/app"], ROOT)
    step("公开仓卫生扫描（C2：作者机器路径＋凭据形态）", [PY, "scripts/check_public_leak.py"], ROOT)
    step("py_compile 全量语法校验", [PY, "-m", "compileall", "-q", "-f", "backend/app"], ROOT)
    step("pytest 后端测试", [PY, "-m", "pytest", "tests", "-q", "--basetemp=.pytest_tmp"], ROOT / "backend")
    step("flutter analyze", [FLUTTER, "analyze"], ROOT / "flutter_app")
    step("flutter test", [FLUTTER, "test"], ROOT / "flutter_app")

    if "--smoke" in sys.argv:
        step("接口冒烟（登录/角色/朋友圈/归档）", [PY, "scripts/smoke_test.py"], ROOT)

    print("\n===== 全部通过 =====")


if __name__ == "__main__":
    main()
