# -*- coding: utf-8 -*-
"""检查「冻结目录」是否被新增文件，防 AMBRACE 3.7 目录双轨收敛回潮。

背景（AMBRACE 3.7 前奏，2026-09-02）：
- 后端 `backend/app/services` 与 `backend/app/scheduler` 只允许修改、不允许新增文件；
  前端 `flutter_app/lib/screens` 不再新增页面文件。
- 冻结目录的当前文件全集（allowlist）写入 `.github/frozen_dirs_allowlist.json`。
- 本脚本校验：冻结目录当前文件集合 ⊆ allowlist。
  删除/迁出允许；新增/改名进入不允许，并提示应放的目标目录。

用法（仓库根）：
    python .github/scripts/check_frozen_dirs.py            # 校验（CI 防回潮 step）
    python .github/scripts/check_frozen_dirs.py --generate # 从当前树重新生成基线

退出码：0 = 通过；1 = 失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_BASELINE = ".github/frozen_dirs_allowlist.json"

# 冻结目录定义（--generate 时用于重建基线；目标提示文案写入基线供校验时用）
FROZEN = (
    ("backend/app/services", ".py",
     "application/（用例编排，已开始迁移）或 domain/（纯领域逻辑）"),
    ("backend/app/scheduler", ".py",
     "scheduling/（触发/编排）或 domain/proactivity/（决策逻辑）"),
    ("flutter_app/lib/screens", ".dart",
     "features/<域>/（页面 + 子组件 + provider 按域内聚）"),
)


def _walk_rel_files(dirpath: Path, suffix: str) -> set[str]:
    """收集 dirpath 下所有指定后缀文件的相对路径（用 / 分隔，稳定跨平台）。"""
    out: set[str] = set()
    if not dirpath.is_dir():
        return out
    for p in dirpath.rglob(f"*{suffix}"):
        if not p.is_file():
            continue
        # 跳过缓存目录（.pyc 等），仅统计源文件
        if "__pycache__" in p.parts:
            continue
        out.add(p.relative_to(dirpath).as_posix())
    return out


def _generate(root: Path, baseline: Path) -> int:
    data = {"version": 1, "directories": {}}
    total = 0
    for rel, suffix, target in FROZEN:
        d = root / rel
        files = sorted(_walk_rel_files(d, suffix))
        total += len(files)
        if not d.is_dir():
            # 冻结目录整体尚未存在（如前端迁空后删除），allowlist 记空
            data["directories"][rel] = {"suffix": suffix, "target_hint": target, "relative_files": []}
            continue
        data["directories"][rel] = {
            "suffix": suffix,
            "target_hint": target,
            "relative_files": files,
        }
    baseline.parent.mkdir(parents=True, exist_ok=True)
    # 显式 LF（newline=\"\\n\"）保证跨平台生成一致，与 .github 下其它文件保持一致
    baseline.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"[OK] 已生成基线: {baseline.relative_to(root)}（{total} 个文件）")
    return 0


def _check(root: Path, baseline: Path) -> int:
    if not baseline.is_file():
        print(f"[FAIL] 找不到基线文件: {baseline.relative_to(root)}")
        return 1
    data = json.loads(baseline.read_text(encoding="utf-8"))
    dirs = data.get("directories", {})
    all_ok = True
    for rel, spec in dirs.items():
        d = root / rel
        suffix = spec.get("suffix", ".py")
        target = spec.get("target_hint", "目标目录")
        allow = set(spec.get("relative_files", []))
        if not d.is_dir():
            # 目录已整体删除/迁空：允许（越迁越接近收敛目标）
            print(f"[OK] {rel}: 目录已不存在（整体删除/迁出，允许）")
            continue
        current = _walk_rel_files(d, suffix)
        disallowed = sorted(current - allow)
        if disallowed:
            all_ok = False
            print(f"[FAIL] 冻结目录出现 allowlist 之外的文件（新增/改名进入不允许）: {rel}")
            for f in disallowed:
                print(f"    + {rel}/{f}  -> 应放入 {target}")
        else:
            print(f"[OK] {rel}: {len(current)} 个文件均在本目录 allowlist 内")
    if all_ok:
        print("[PASS] 冻结目录文件集合校验通过")
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--generate", action="store_true", help="从当前树重新生成 allowlist 基线")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent.parent  # .github/scripts -> .github -> 仓库根
    baseline = Path(args.baseline)
    if not baseline.is_absolute():
        baseline = root / baseline

    if args.generate:
        return _generate(root, baseline)
    return _check(root, baseline)


if __name__ == "__main__":
    sys.exit(main())
