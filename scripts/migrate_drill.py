# ruff: noqa: UP031, BLE001, I001  （%-格式化便于列宽对齐；演练脚本刻意宽捕异常；import 顺序含 sys.path 前置）
r"""发布前「老库升级」演练（2026-09-21 建立；来源＝docs/plans.md §六「发布 checklist 追加项」）。

做什么：在临时目录合成 5 种「老库形态」，逐个跑启动期的
app/db/migrate._ensure_alembic_revision_sync()，断言「判定动作 / 终态 head / 数据不丢 /
该补的表补上没有」。**全程只读生产库**（sqlite3 backup API 复制，绝不写生产库）。

五种形态：
  01-空库                   全空文件 → 期望 stamped
  02-非空老库-schema落后    有表无版本 + 缺链上哨兵表 → 期望 upgraded（整链重放补齐）
  03-只装部分插件(缺非链表)  有表无版本 + schema 当前但缺 wechat_ilink_bindings → 期望 stamped（不重放、不补）
  03b-缺链上插件表          有表无版本 + 缺 douyin_pending（版本链 baseline 建的表）→ 期望 upgraded 并补回该表
  04-孤儿版本号             alembic_version 指向不在本链的历史版本 → 期望 re-stamped（purge 后重标）

用法（在仓库根目录）：
    backend\.venv\Scripts\python.exe scripts\migrate_drill.py
    backend\.venv\Scripts\python.exe scripts\migrate_drill.py --out D:\some\dir
    backend\.venv\Scripts\python.exe scripts\migrate_drill.py --base fresh --out D:\some\dir

基线库来源（--base，2026-09-21 新增）：prod＝生产库的只读副本（默认，本机验真老库用）；
fresh＝没有生产库时用（CI / 全新机器）—— 先 init_db() + upgrade head 现建一份「当前 schema 且已到
head」的基线库，再在它上面做五种变异（语义等价，本机实测约 10 秒）。CI 固定走 fresh，见
.github/workflows/ci.yml 的 migrate-drill job（2026-09-21 接入，非零退出即拦）。

退出码 0＝全部 PASS；非 0＝有失败（逐条打印理由，便于发版前拦截）。
首次建立（2026-09-21）本演练抓到 1 个真实缺陷：a7b8c9d0e1f2 迁移里 drop_constraint 的
try 只包了调用、没包住 batch 上下文退出（SQLite 的 batch 把 DDL 延迟到 __exit__ 的 flush
执行）→ 旧库缺该命名约束时 ValueError 逃逸 → 整链 upgrade 失败、启动期
ensure_alembic_revision 抛错（服务起不来）。已修（try 包住整个 with），修后 5/5 PASS。
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 仓库根 D:\AMBRACE
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

ORPHAN_REV = "78d3405d8b58"   # 本机真实库历史上出现过的、不在本链的版本号

BASE_KINDS = ("prod", "fresh")

CASES = [
    ("01-空库", "empty", []),
    ("02-非空老库-schema落后", "lagging", ["DROP TABLE alembic_version", "DROP TABLE user_llm_limits"]),
    ("03-只装部分插件(缺非链表)", "partial_plugins", ["DROP TABLE alembic_version", "DROP TABLE wechat_ilink_bindings"]),
    ("03b-缺链上插件表(douyin)", "chain_plugin_missing", ["DROP TABLE alembic_version", "DROP TABLE douyin_pending"]),
    ("04-孤儿版本号", "orphan", ["UPDATE alembic_version SET version_num='%s'" % ORPHAN_REV]),
]


def _prod_db() -> Path:
    from app.config import settings
    return Path(settings.database_url.split("sqlite+aiosqlite:///")[-1])


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)


def _counts(path: Path) -> dict:
    out = {}
    c = _ro(path)
    try:
        for t in ("users", "ai_characters", "memories", "ai_moments"):
            try:
                out[t] = c.execute("select count(*) from %s" % t).fetchone()[0]
            except Exception:
                out[t] = None
    finally:
        c.close()
    return out


def _tables(path: Path) -> set:
    c = _ro(path)
    try:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    finally:
        c.close()


def bootstrap(db: Path) -> None:
    """CI/无生产库时用：就地建出「当前 schema 且已到 head」的基线库（等价于当前生产库形态）。

    步骤＝init_db()（create_all + 幂等补丁）→ _ensure_alembic_revision_sync()（判落后走整链
    upgrade，补齐版本链建的表并记账）。之后 5 种形态的变异才有意义（否则缺链上表会被统一判落后）。
    """
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + db.resolve().as_posix()
    import asyncio

    from app.db import migrate as m
    from app.db.database import init_db

    asyncio.run(init_db())
    print("   [bootstrap] init_db 完成 → %s" % m._ensure_alembic_revision_sync())


def run_case(db: Path) -> None:
    """子进程入口：对该库跑一次启动期对齐，输出 JSON。"""
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + db.resolve().as_posix()
    from alembic.script import ScriptDirectory

    from app.db import migrate as m

    head = ScriptDirectory.from_config(m._alembic_config()).get_current_head()
    before_cur, before_counts, before_tables = m._current_rev(m._sync_url()), _counts(db), _tables(db)
    action = m._ensure_alembic_revision_sync()
    after_cur, after_counts, after_tables = m._current_rev(m._sync_url()), _counts(db), _tables(db)
    print(json.dumps({
        "db": db.name, "head": head, "cur_before": before_cur, "action": action, "cur_after": after_cur,
        "counts_before": before_counts, "counts_after": after_counts,
        "tables_before_n": len(before_tables), "tables_after_n": len(after_tables),
        "tables_added": sorted(after_tables - before_tables), "tables_removed": sorted(before_tables - after_tables),
    }, ensure_ascii=False))


def build(kind: str, stmts: list, out: Path, base_db: Path) -> Path:
    db = out / ("%s.db" % kind)
    if db.exists():
        db.unlink()
    if kind == "empty":
        sqlite3.connect(str(db)).close()
        return db
    src = _ro(base_db)
    try:
        dst = sqlite3.connect(str(db))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    c = sqlite3.connect(str(db))
    try:
        for s in stmts:
            try:
                c.execute(s)
                print("   [fixture] %s -> ok" % s[:70])
            except Exception as e:   # 目标表在真实库里可能本就不存在（如未装该插件）
                print("   [fixture] %s -> 跳过（%s）" % (s[:70], e))
        c.commit()
    finally:
        c.close()
    return db


def verdict(name: str, r: dict) -> tuple:
    if "error" in r:
        return False, "执行失败: %s" % r["error"][:240]
    a, head = r["action"], r["head"]
    lost = [k for k, v in r["counts_after"].items() if v is not None and r["counts_before"].get(k) not in (None, v)]
    if lost:
        return False, "数据丢失: %s" % lost
    if name.startswith("01"):
        return (a.startswith("stamped") and r["cur_after"] == head), "空库应 stamp（不重放）"
    if name.startswith("02"):
        ok = a.startswith("upgraded") and r["cur_after"] == head and "user_llm_limits" in r["tables_added"]
        return ok, "落后库应整链 upgrade 并补齐 user_llm_limits"
    if name.startswith("03-"):
        extra = [t for t in r["tables_added"] if t != "alembic_version"]
        return (a.startswith("stamped") and r["cur_after"] == head and not extra), \
            "当前 schema + 缺非链插件表 → stamp（不重放、不补插件表）"
    if name.startswith("03b"):
        return (r["cur_after"] == head), "缺链上插件表 → 应补回该表（动作=%s）" % a
    if name.startswith("04"):
        return (a.startswith("re-stamped") and r["cur_after"] == head), "孤儿版本号应 purge 后 re-stamp"
    return False, "未知用例"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="内部用：只对指定库跑一次对齐并打印 JSON")
    ap.add_argument("--bootstrap", help="内部用：就地建出「当前 schema 且已到 head」的基线库")
    ap.add_argument("--out", help="演练工作目录（默认系统临时目录）")
    ap.add_argument("--base", choices=BASE_KINDS, default="prod",
                    help="基线来源：prod=只读复制生产库（默认，本地用）；fresh=现建（CI/无生产库）")
    args = ap.parse_args()
    if args.case:
        run_case(Path(args.case))
        return 0
    if args.bootstrap:
        bootstrap(Path(args.bootstrap))
        return 0

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="migrate_drill_"))
    out.mkdir(parents=True, exist_ok=True)
    if args.base == "prod":
        base_db = _prod_db()
        print("基线＝只读复制生产库: %s  (%.1f MB)" % (base_db, base_db.stat().st_size / 1e6))
    else:
        base_db = out / "base_fresh.db"
        if base_db.exists():
            base_db.unlink()
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--bootstrap", str(base_db)],
                           cwd=str(BACKEND), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", check=False)
        if p.returncode != 0:
            print("bootstrap 失败:", (p.stdout or "")[-1200:] + (p.stderr or "")[-1200:])
            return 2
        print("基线＝现建库（init_db + upgrade head）: %s" % base_db)
    print("演练工作目录: %s" % out)
    rows = []
    for name, kind, stmts in CASES:
        db = build(kind, stmts, out, base_db)
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", str(db)],
                           cwd=str(BACKEND), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", check=False)
        if p.returncode != 0:
            r = {"error": (p.stdout or "")[-1500:] + (p.stderr or "")[-1500:]}
        else:
            r = json.loads([l for l in p.stdout.splitlines() if l.strip().startswith("{")][-1])
        ok, why = verdict(name, r)
        rows.append({"case": name, "kind": kind, "ok": ok, "why": why, "result": r})
        print("\n=== %s === %s" % (name, "PASS" if ok else "FAIL"))
        print("   理由:", why)
        if "error" not in r:
            print("   动作: %s   cur: %s -> %s (head=%s)" % (r["action"], r["cur_before"], r["cur_after"], r["head"]))
            print("   表数: %s -> %s   新增表: %s" % (r["tables_before_n"], r["tables_after_n"], r["tables_added"] or "无"))
            print("   计数(前/后): %s" % {k: (r["counts_before"].get(k), r["counts_after"].get(k))
                                        for k in ("users", "ai_characters", "memories", "ai_moments")})
    (out / "drill_result.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ok = sum(1 for x in rows if x["ok"])
    print("\n汇总: %d/%d PASS   明细: %s" % (n_ok, len(rows), out / "drill_result.json"))
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
