# -*- coding: utf-8 -*-
"""AMBRACE 记忆时态缺陷族·第一批 · 只读量化脚本（2026-09-17 交接任务5-3）。

产出两项前后对比数字：
  ① 已知样本记忆（默认 id=10724 char6 stale 位置；id=10405/10409/10413 群聊发言）在
     **改造前**（git HEAD 版 backend/app/memory/tense.py，只读 `git show`）与**改造后**
     （工作区当前 tense.py）的 `classify_tense` 结果；
  ② 「现状面」口径下 stale 族（stale/superseded/expired）可见行数与占比的前后对比：
     - 旧口径：`_active_status_clause()` 在 memory_supersede 默认关时返回永真 → stale 族
       会进入无条件注入 / 关系锚点 / 向量写前查重等现状面；
     - 新口径：`current_facts_active_only`（默认开）→ 现状面恒 `status='active'`，stale 族不可见；
       怀旧面（`_retrievable_status_clause` / 向量 / BM25 默认路）仍保留 stale，且 rerank 恒降权 0.5。

安全：**全程只读**（sqlite3 `file:...?mode=ro`）；只 SELECT，不写库、不改库、不删记忆；
      `git show` 亦为只读操作。
用法：
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\tense_current_facts_report.py
  ... --db D:\\...\\x.db --ids 10724,10405,10409,10413
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(SERVER_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)

from app.memory.tense import classify_tense  # noqa: E402

DEFAULT_DB = os.path.join(BACKEND_DIR, "data", "sqlite", "ai_companion.db")
DEFAULT_IDS = "10724,10405,10409,10413"
STALE_FAMILY = ("stale", "superseded", "expired")


def connect_ro(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise SystemExit(f"[abort] 数据库不存在: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row_obj(r: dict) -> SimpleNamespace:
    """sqlite 行 → tense 规则所需的最小对象面（classify_tense 只读 memory_type/sub_type/文本字段）。"""
    return SimpleNamespace(
        memory_type=r.get("memory_type"), sub_type=r.get("sub_type"),
        is_core=False, core_category=None,
        title="", content=r.get("content") or "", why_it_matters="",
        created_at=None, valid_to=None,
    )


def load_legacy_classify():
    """改造前口径：只读 `git show HEAD:backend/app/memory/tense.py` 并 exec 出 classify_tense。

    只读 git 是本批允许的操作；失败则返回 (None, 原因)，脚本仍输出改造后结果。
    """
    try:
        out = subprocess.run(
            ["git", "-C", SERVER_DIR, "show", "HEAD:backend/app/memory/tense.py"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        ns: dict = {"__name__": "ambrace_tense_legacy"}
        exec(compile(out.stdout, "tense.py@HEAD", "exec"), ns)
        return ns["classify_tense"], "git HEAD:backend/app/memory/tense.py"
    except Exception as e:  # noqa: BLE001 - 只读诊断，取不到就退化
        return None, f"（改造前口径不可得：{e}）"


def report_samples(conn: sqlite3.Connection, ids: list[int], legacy) -> list[str]:
    lines = ["", "① 已知样本 classify_tense：改造前(HEAD) → 改造后(工作区)"]
    q = ("SELECT id,character_id,memory_type,sub_type,status,is_archived,content "
         "FROM memories WHERE id=?")
    for mid in ids:
        r = conn.execute(q, (mid,)).fetchone()
        if r is None:
            lines.append(f"  id={mid}: 不存在（跳过）")
            continue
        d = dict(r)
        obj = _row_obj(d)
        after = classify_tense(obj)
        before = legacy(obj) if legacy else "n/a"
        flag = "  ← 改变" if before != after else ""
        content = (d["content"] or "").replace("\n", " ")[:42]
        lines.append(
            f"  id={mid} char={d['character_id']} type={d['memory_type']}/{d['sub_type']} "
            f"status={d['status']} archived={d['is_archived']}\n"
            f"      content={content!r}\n"
            f"      before={before}  after={after}{flag}"
        )
    return lines


def _count(conn: sqlite3.Connection, where: str, params: tuple = ()) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where}", params).fetchone()[0])


def report_current_facts(conn: sqlite3.Connection) -> list[str]:
    lines = ["", "② 现状面口径 stale 族可见行数（未归档 memories，只读全库）"]
    total = _count(conn, "is_archived=0")
    dist = conn.execute(
        "SELECT status, COUNT(*) c FROM memories WHERE is_archived=0 GROUP BY status ORDER BY c DESC"
    ).fetchall()
    lines.append(f"  未归档 memories 总数 N = {total}")
    lines.append("  status 分布：" + "；".join(f"{r['status']}={r['c']}" for r in dist))

    stale_expr = " OR ".join(["status=?"] * len(STALE_FAMILY))
    stale_n = _count(conn, f"is_archived=0 AND ({stale_expr})", STALE_FAMILY)
    pct = (stale_n / total * 100.0) if total else 0.0
    lines.append("  【旧口径】memory_supersede 默认关 → _active_status_clause() 永真：")
    lines.append(f"      现状面可见 stale 族 = {stale_n} / {total} = {pct:.2f}%")
    lines.append("  【新口径】current_facts_active_only 默认开 → 现状面恒 status='active'：")
    lines.append(f"      现状面可见 stale 族 = 0 / {total} = 0.00%（净减 {stale_n} 行，{pct:.2f}%）")
    lines.append("  【怀旧面】_retrievable_status_clause 仍保留 stale：不缩减（rerank 恒降权 0.5）")

    # 用户位置/现状子集：本批用户体感问题的直接来源
    for label, cond, params in (
        ("user 位置（memory_type=user_info 且 sub_type=location）",
         "is_archived=0 AND memory_type='user_info' AND sub_type='location'", ()),
        ("全部易变现状子类（location/trip/mood/current_state）",
         "is_archived=0 AND sub_type IN ('location','current_location','current_state','trip','mood')", ()),
    ):
        t = _count(conn, cond, params)
        s = _count(conn, f"({cond}) AND ({stale_expr})", params + STALE_FAMILY)
        sp = (s / t * 100.0) if t else 0.0
        lines.append(f"  · 子集「{label}」：{t} 行，其中 stale 族 {s} 行（旧口径现状面可见 {sp:.2f}%，新口径 0）")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆时态缺陷族第一批 · 只读量化（不改库）")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite 库路径（默认 backend/data/sqlite/ai_companion.db）")
    ap.add_argument("--ids", default=DEFAULT_IDS, help="样本记忆 id，逗号分隔")
    args = ap.parse_args()

    ids = [int(x) for x in str(args.ids).replace("，", ",").split(",") if x.strip().isdigit()]
    conn = connect_ro(args.db)
    legacy, legacy_src = load_legacy_classify()

    out = [
        "AMBRACE 记忆时态缺陷族·第一批 · 只读量化报告",
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"库：{args.db}（mode=ro 只读）",
        f"改造前口径：{legacy_src}",
        "改造后口径：工作区 backend/app/memory/tense.py",
    ]
    out += report_samples(conn, ids, legacy)
    out += report_current_facts(conn)
    out += ["", "说明：本脚本只 SELECT（sqlite mode=ro）+ 只读 git show，不写库、不改数据。"]
    print("\n".join(out))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
