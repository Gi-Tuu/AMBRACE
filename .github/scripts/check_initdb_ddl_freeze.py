# -*- coding: utf-8 -*-
"""检查 init_db.py 的「人工 DDL 冻结」是否被破坏。

背景（AMBRACE 3.8，2026-09-02 冻结）：
- init_db.py 作为幂等兼容层保留，但其手工 DDL（加列/改表/建索引）已冻结为冻结基线，只减不增；
- 新增 schema 变更只允许走 Alembic revision --autogenerate 入链。

本脚本作为 CI 防回潮 step，校验两条：
1. 整文件 `ADD COLUMN` 关键字计数不得超过冻结值（默认 86）。
2. FREEZE 哨兵注释之后不得再出现 DDL 关键字（ADD COLUMN / ALTER TABLE / CREATE INDEX）。

用法（可复现命令，仓库根目录）：
    python .github/scripts/check_initdb_ddl_freeze.py
    python .github/scripts/check_initdb_ddl_freeze.py --initdb backend/app/db/init_db.py --freeze-count 86

退出码：0 = 通过；1 = 失败。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_INITDB = "backend/app/db/init_db.py"
DEFAULT_FREEZE_COUNT = 86
FREEZE_MARKER = "3.8 FREEZE"
DDL_PATTERNS = ("ADD COLUMN", "ALTER TABLE", "CREATE INDEX")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--initdb", default=DEFAULT_INITDB)
    ap.add_argument("--freeze-count", type=int, default=DEFAULT_FREEZE_COUNT)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent.parent  # 仓库根=.github/scripts -> .. => .github -> .. => 仓库根
    path = Path(args.initdb)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        print(f"[FAIL] 找不到 init_db.py: {path}")
        return 1

    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    # 1) 整文件 ADD COLUMN 计数
    ac_count = text.count("ADD COLUMN")
    if ac_count > args.freeze_count:
        print(f"[FAIL] ADD COLUMN 计数 {ac_count} > 冻结值 {args.freeze_count}（只允许减少，不新增手工 DDL）")
        return 1
    print(f"[OK] ADD COLUMN 计数 {ac_count} <= 冻结值 {args.freeze_count}")

    # 2) FREEZE 哨兵存在 + 其后不得出现 DDL 关键字
    sentinel_idx = None
    for i, ln in enumerate(lines):
        if FREEZE_MARKER in ln and "===" in ln:
            sentinel_idx = i
            break
    if sentinel_idx is None:
        print(f"[FAIL] 未找到 FREEZE 哨兵注释（应包含 '{FREEZE_MARKER}'）")
        return 1
    print(f"[OK] FREEZE 哨兵位于行 {sentinel_idx + 1}")

    offenders = []
    for i in range(sentinel_idx + 1, len(lines)):
        ln = lines[i].strip()
        if not ln:  # 空行跳过
            continue
        for ddl in DDL_PATTERNS:
            if ddl in ln:
                offenders.append((i + 1, ddl, lines[i]))
                break

    if offenders:
        print(f"[FAIL] FREEZE 哨兵之后出现手工 DDL 关键字（共 {len(offenders)} 处）：")
        for lnno, ddl, raw in offenders[:20]:
            print(f"    L{lnno}: {ddl} -> {raw.strip()}")
        return 1
    print("[OK] FREEZE 哨兵之后无 ADD COLUMN / ALTER TABLE / CREATE INDEX")

    print("[PASS] init_db 人工 DDL 冻结校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
