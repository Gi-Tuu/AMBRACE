"""一键验证脚本：ruff → 公开仓卫生扫描 → py_compile → pytest → flutter analyze/test → 接口冒烟。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\verify.py [--smoke] [--pruned] [--parallel [N]] [--fast]

--smoke：额外跑接口冒烟（登录 + 角色 + 朋友圈 + 归档，test/test123 账号）。
--pruned：额外在**裁剪树**（＝CI 实际检出的那份文件集合）上跑一遍后端测试。
  必要性有两次前科：第 117 棒守卫读 `docs/`、第 124 棒守卫读被隐私清单挡掉的题库——
  两次都是本地全量六千多例全绿、推上去四条档同时红。
--parallel [N]：后端 pytest 改走 xdist 并行（默认 8，上限＝逻辑核数）。**实验档，默认仍串行**。2026-10-25 实测：
  封存 HEAD 上 `-n 8 --dist loadfile` 与串行**用例集合/逐例状态完全一致**（8895 例、
  8890 passed/5 skipped、差异 0），墙钟 1628.81s → 447~459s（3.6~3.7×）。
  注意：串行与并行均传 `--basetemp=.pytest_tmp`；xdist 自动为各 worker 分配其下独占子目录。
  并行会暴露既有测试的「共享库写锁 / 顺序敏感全局状态」脆弱点（已修 2 文件隔离，仍有 1 例待查，见
  docs/testing-baseline.md §四）；并行红一律先单独串行复跑定位，禁止改断言/加 skip 消红。
--fast：后端 pytest 只跑快测档（`-m "not slow"`），适合改完先自检；默认仍是全量。
"""
import os as _os
import shutil as _shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# P1-5：跨平台 venv python 路径（Windows=Scripts/python.exe，Linux/macOS=bin/python）
PY = str((ROOT / "backend/.venv/Scripts/python.exe") if _os.name == "nt" else (ROOT / "backend/.venv/bin/python"))
FLUTTER = _shutil.which("flutter") or "flutter.bat"  # P1：优先 PATH
STEPS = []
DEFAULT_PARALLEL_WORKERS = 8


def step(name: str, cmd: list[str], cwd: Path) -> None:
    print(f"\n===== {name} =====")
    r = subprocess.run(cmd, cwd=str(cwd), check=False)
    if r.returncode != 0:
        print(f"[FAIL] {name}")
        sys.exit(1)
    print(f"[OK] {name}")


def parallel_workers(argv: list[str]) -> int | None:
    """解析 `--parallel [N]`：未出现返回 None；出现但无数字则用默认（不超过逻辑核数）。"""
    if "--parallel" not in argv:
        return None
    idx = argv.index("--parallel")
    if idx + 1 < len(argv) and argv[idx + 1].isdigit():
        wanted = int(argv[idx + 1])
    else:
        wanted = DEFAULT_PARALLEL_WORKERS
    cpu = _os.cpu_count() or wanted
    return max(1, min(wanted, cpu))


def pytest_cmd(argv: list[str]) -> list[str]:
    """组装后端 pytest 命令：默认串行全量（行为与历史逐字一致）。

    - 默认：`tests -q --basetemp=.pytest_tmp`（AGENTS.md 规定口径，禁止改）；
    - `--fast`：追加 `-m "not slow"`；
    - `--parallel [N]`：追加 `-n N --dist loadfile`，仍传项目内 basetemp，由 xdist 隔离 worker 子目录。
    """
    cmd = [PY, "-m", "pytest", "tests", "-q"]
    workers = parallel_workers(argv)
    if "--fast" in argv:
        cmd += ["-m", "not slow"]
    if workers:
        cmd += ["-n", str(workers), "--dist", "loadfile"]
    cmd += ["--basetemp=.pytest_tmp"]
    return cmd


def main() -> None:
    """按 ruff → py_compile → pytest → flutter → smoke 顺序跑一遍。

    pytest 段必须带 `--basetemp=.pytest_tmp`：不带时 pytest 用系统 `%TEMP%\\pytest-of-<user>`，
    该目录权限坏掉时整段用例直接全灭（实测不带 8 errors / 带 8 passed），与代码本身无关；
    指向仓库内 basetemp 后每轮自行清空重建，也是 AGENTS.md 规定的本机统一口径。
    并行档同样保留该根目录，xdist 自动分配 worker 子目录；不得并发复用同一 basetemp。
    """
    workers = parallel_workers(sys.argv)
    label = f"pytest 后端测试{'（快测档）' if '--fast' in sys.argv else ''}"
    if workers:
        label += f"（并行 -n {workers} --dist loadfile）"
    step("ruff 静态检查（backend/app）", [PY, "-m", "ruff", "check", "backend/app"], ROOT)
    step("公开仓卫生扫描（C2：作者机器路径＋凭据形态）", [PY, "scripts/check_public_leak.py"], ROOT)
    step("py_compile 全量语法校验", [PY, "-m", "compileall", "-q", "-f", "backend/app"], ROOT)
    step(label, pytest_cmd(sys.argv), ROOT / "backend")
    step("flutter analyze", [FLUTTER, "analyze"], ROOT / "flutter_app")
    step("flutter test", [FLUTTER, "test"], ROOT / "flutter_app")

    if "--pruned" in sys.argv:
        step("裁剪树复跑（CI 那份文件集合，读不到 docs 的债在这里先红）",
             [PY, "scripts/check_pruned_tree.py"], ROOT)

    if "--smoke" in sys.argv:
        step("接口冒烟（登录/角色/朋友圈/归档）", [PY, "scripts/smoke_test.py"], ROOT)

    print("\n===== 全部通过 =====")


if __name__ == "__main__":
    main()
