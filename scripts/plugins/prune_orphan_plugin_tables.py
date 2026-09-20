# -*- coding: utf-8 -*-
"""一次性清理脚本（A2 M5，2026-09-20）：列出/清理「孤儿插件自有表」。

孤儿定义：物理仍存在、但归属插件**已不在 ``plugins`` 表**（已卸载）的插件自有业务表
（``<name>_*``，如 douyin_* / wechat_ilink_*）。

背景（A2 M5）：``plugins.py::uninstall_plugin`` 卸载时**只删插件目录 + 清 KV + 删 plugins 行**，
**不 DROP 插件自有表**（历史数据保留，重装后仍可见）。若确需回收这些表，用本脚本显式执行。

纪律（派单 §三 M5-4 / §五 硬约束）：
- **默认 dry-run**：只读连接（``mode=ro`` + ``PRAGMA query_only=ON``），只打印表名与行数，
  绝不 DDL/DML；
- ``--apply`` 必须同时带 ``--yes`` 才执行 ``DROP TABLE``；本批**不接任何定时/自动执行**；
- 幂等：清理后再跑 dry-run 不再列出这些表；
- 绝不误删内核表：候选 = 物理表 − 内核 ``Base.metadata`` 表 − SQLite 内部表；
  归属无法判定的表只报告、**永不在 --apply 时 DROP**（保守）。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\plugins\\prune_orphan_plugin_tables.py            # dry-run
    backend\\.venv\\Scripts\\python.exe scripts\\plugins\\prune_orphan_plugin_tables.py --json
    # 显式执行（本批不允许对生产库执行）：
    backend\\.venv\\Scripts\\python.exe scripts\\plugins\\prune_orphan_plugin_tables.py --apply --yes
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # scripts/plugins/ -> 仓库根
_DEFAULT_DB = ROOT / "backend" / "data" / "sqlite" / "ai_companion.db"
_BACKEND = ROOT / "backend"
# 插件目录（与 registry.EXAMPLE_DIR / registry.USER_DIR 同口径；此处不 import registry，
# 避免 load_plugin_dir 触发建表等写副作用）
_PLUGIN_DIRS = [ROOT / "plugins" / "examples", ROOT / "backend" / "data" / "plugins"]
_INTERNAL_TABLES = {"alembic_version", "sqlite_sequence"}
_TABLENAME_RE = re.compile(r"__tablename__\s*=\s*[\"']([A-Za-z0-9_]+)[\"']")


def _open_ro(db_path: str) -> sqlite3.Connection:
    """只读打开 SQLite（URI mode=ro + PRAGMA query_only），运行期任何写都被拒。"""
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _kernel_tables() -> set[str]:
    """内核表名集合（``app.models`` 主 metadata）。

    导入失败 → 返回空集（调用方退化为「只按插件归属前缀判定」的保守模式，绝不 DROP 内核表）。
    """
    try:
        if str(_BACKEND) not in sys.path:
            sys.path.insert(0, str(_BACKEND))
        import app.models  # noqa: F401  # 注册全部内核模型到 Base.metadata
        from app.models.base import Base

        return set(Base.metadata.tables)
    except Exception as e:  # pragma: no cover - 环境不完整时的保守退化
        print("[warn] 读取内核 metadata 失败，退化为保守模式（仅按插件归属判定）: %s" % e,
              file=sys.stderr)
        return set()


def _plugin_table_owners(extra_dirs=()) -> tuple[dict[str, str], set[str]]:
    """静态扫描插件目录（只读，不 import 插件 main.py）：返回 (表名→插件名, 插件名集合)。

    插件名取 manifest.json 的 ``name``；表名取插件目录内 ``.py`` 的 ``__tablename__`` 字面量。
    """
    owners: dict[str, str] = {}
    names: set[str] = set()
    for base in list(_PLUGIN_DIRS) + [Path(d) for d in extra_dirs]:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or not (d / "manifest.json").is_file():
                continue
            try:
                name = str(json.loads((d / "manifest.json").read_text(encoding="utf-8-sig"))["name"])
            except Exception:
                continue
            names.add(name)
            for py in d.rglob("*.py"):
                try:
                    txt = py.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for m in _TABLENAME_RE.finditer(txt):
                    owners.setdefault(m.group(1), name)
    return owners, names


def _guess_owner_by_prefix(table: str, candidate_names: set[str]) -> str | None:
    """前缀兜底：``<name>_*`` 命中插件名（最长匹配优先）。"""
    best = None
    for name in candidate_names:
        if table.startswith(name + "_") and (best is None or len(name) > len(best)):
            best = name
    return best


def collect(db_path: str, extra_dirs=()) -> list[dict]:
    """收集插件自有表清单：每项 {table, owner, status, rows}。

    status: ``orphan``（归属插件已不在 plugins 表）/ ``kept``（归属插件仍在）/ ``unknown``（无法归属）。
    """
    conn = _open_ro(db_path)
    try:
        phys = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        have = set(phys)
        installed: set[str] = set()
        if "plugins" in have:
            installed = {r[0] for r in conn.execute("SELECT name FROM plugins")}
        owners, dir_names = _plugin_table_owners(extra_dirs)
        kernel = _kernel_tables()
        cand_names = set(installed) | set(dir_names) | set(owners.values())
        out: list[dict] = []
        for t in phys:
            if t in kernel or t in _INTERNAL_TABLES:
                continue  # 内核表 / SQLite 内部表：绝不动
            owner = owners.get(t)
            if owner is None:
                # 仅当内核集合可知时，才把「内核之外的物理表」纳入前缀兜底
                owner = _guess_owner_by_prefix(t, cand_names)
            if owner is None:
                # 内核集合不可知时，不把无归属表当候选（保守：绝不误判为孤儿）
                status = "unknown" if kernel else "skip"
            elif owner in installed:
                status = "kept"
            else:
                status = "orphan"
            if status == "skip":
                continue
            try:
                rows = int(conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0])
            except Exception:
                rows = -1
            out.append({"table": t, "owner": owner, "status": status, "rows": rows})
        return out
    finally:
        conn.close()


def _apply_drops(db_path: str, tables: list[str]) -> int:
    """显式 DROP（仅 orphan 且归属可判定的表）；返回成功执行条数。"""
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    done = 0
    try:
        for t in tables:
            conn.execute('DROP TABLE IF EXISTS "%s"' % t)
            done += 1
            print("  [DROP] %s" % t)
        conn.commit()
    finally:
        conn.close()
    return done


def main() -> int:
    ap = argparse.ArgumentParser(
        description="列出/清理「孤儿插件自有表」（默认 dry-run，只读）")
    ap.add_argument("--db", default=str(_DEFAULT_DB),
                    help="SQLite 库路径（默认 backend/data/sqlite/ai_companion.db）")
    ap.add_argument("--plugin-dir", action="append", default=[],
                    help="额外的插件目录（可重复；默认已含 plugins/examples 与 backend/data/plugins）")
    ap.add_argument("--dry-run", action="store_true", help="只打印（默认行为，无需显式指定）")
    ap.add_argument("--apply", action="store_true", help="执行 DROP（必须同时带 --yes）")
    ap.add_argument("--yes", action="store_true", help="确认执行破坏性动作")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        print("[ERROR] 数据库不存在：%s" % db_path, file=sys.stderr)
        return 2

    do_apply = bool(args.apply)
    if do_apply and not args.yes:
        print("[ERROR] --apply 必须同时带 --yes（默认 dry-run；本批不允许对生产库执行 --apply）",
              file=sys.stderr)
        return 2

    try:
        items = collect(db_path, args.plugin_dir)
    except Exception as e:
        print("[ERROR] 扫描失败：%s" % e, file=sys.stderr)
        return 1

    orphans = [it for it in items if it["status"] == "orphan"]
    if args.json:
        print(json.dumps({"db": db_path, "apply": do_apply, "items": items},
                         ensure_ascii=False, indent=2))
    else:
        print("=== 插件自有表清单（db=%s） ===" % db_path)
        print("%-32s %-24s %-8s %8s" % ("table", "owner", "status", "rows"))
        for it in items:
            print("%-32s %-24s %-8s %8s" % (
                it["table"], it["owner"] or "-", it["status"], it["rows"]))
        print()
        print("插件自有表 %d 张：orphan=%d / kept=%d / unknown=%d"
              % (len(items),
                 sum(1 for it in items if it["status"] == "orphan"),
                 sum(1 for it in items if it["status"] == "kept"),
                 sum(1 for it in items if it["status"] == "unknown")))
        if not orphans:
            print("[OK] 未发现孤儿插件自有表（归属插件均在 plugins 表中，或无法归属→只报告不删除）。")
        else:
            print("[发现 %d 张孤儿插件自有表]（归属插件已不在 plugins 表）：" % len(orphans))
            for it in orphans:
                print("  - %s（原属插件 %s，%s 行）" % (it["table"], it["owner"], it["rows"]))

    if not do_apply:
        print("[dry-run] 未执行任何 DROP。如需执行（本批不允许对生产库执行）：--apply --yes")
        return 0

    if not orphans:
        print("[apply] 无孤儿表，0 变更（幂等）。")
        return 0
    print("[apply] 即将 DROP %d 张孤儿插件自有表：" % len(orphans))
    done = _apply_drops(db_path, [it["table"] for it in orphans])
    print("[apply] 完成：DROP %d 张（重跑 dry-run 应为 0 孤儿 = 幂等）。" % done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
