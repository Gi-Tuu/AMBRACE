# -*- coding: utf-8 -*-
"""P0 第 4 步 · Actor 归属存量**只读**盘点（默认 dry-run：一个字节都不改，只出报告）。

为什么要盘点（S2 地图 §4.3 丢失点 1/2 ＋ 断点 #5 的反转）
────────────────────────────────────────────────────────
``memories.speaker_type`` 这一列历史上写了三种东西：``user`` / ``character`` / ``system``，
外加一批**没被归一化**的脏值（``ai`` 等），以及感知条被刻意留空的 ``NULL``。
本步起感知条真落 ``perception``，所以先量清「改写前后各有多少条、脏值分布长什么样」，
再决定要不要洗存量（**本轮不洗**：``--apply`` 只是把回填能力备好并验证幂等）。

输出三项（派单 §要求 3）
────────────────────────────────────────────────────────
1. ``speaker_type`` 为 NULL（含空串）的条数；
2. 其中 ``source='perception'`` 的条数（＝本步判定应写成 ``perception`` 的对象）；
3. ``ai`` 与 ``character`` 的混用分布（同一语义两个写法各多少条），并列出全部未登记值。

纪律
────────────────────────────────────────────────────────
- 默认只读：连接一律 ``file:...?mode=ro``（uri=True）+ ``PRAGMA query_only=ON`` 双保险，只跑 SELECT；
- ``--apply`` 才写，且只写一条判据（``source='perception'`` 且归属为空 ⇒ 补 ``perception``），
  **幂等**：跑第二次候选集必为 0（补完就不再是空）；
- **孤儿跳过**：候选行的 ``user_id`` / ``character_id`` 在父表里找不到（级联残留）时不改写，
  单列计数并给出样本 id——治理脚本不该把断链的行「洗干净」；
- 生产库双确认：``--apply`` 指向默认库时必须再加 ``--force-production``，否则退出码 2 不动库。

用法（一律用项目 venv 的 python）::

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\memory\\actor_audit.py
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\memory\\actor_audit.py --json
    #   换库：--app-db path\\to\\x.db      写库（本轮不用）：--apply [--force-production]

退出码：0=跑通（有无命中都算跑通）；1=库文件不存在或 memories 表缺失（无从盘点）；
2=``--apply`` 被生产库安全闸拦下（未加 ``--force-production``）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_APP_DB = BACKEND_DIR / "data" / "sqlite" / "ai_companion.db"
PERCEPTION_SOURCE = "perception"
PERCEPTION_ACTOR = "perception"
# 已知归属值空间（与 app/memory/format.py 的登记表同口径；本脚本自带常量以免 import 活代码）
KNOWN_ACTOR_VALUES = ("user", "character", "system", "tool", "perception")
SAMPLE_LIMIT = 5


def _connect(db_path: Path, *, read_only: bool = True) -> sqlite3.Connection:
    """打开 SQLite：默认只读（mode=ro + query_only 双保险），``read_only=False`` 才可写。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) if read_only \
        else sqlite3.connect(db_path)
    conn.execute("PRAGMA query_only=ON" if read_only else "PRAGMA query_only=OFF")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _norm(value) -> str:
    """列值归一用于计数：None/空白 ⇒ ``"(NULL)"``，其余去空白（保留原写法，脏值不洗白）。"""
    s = "" if value is None else str(value).strip()
    return s or "(NULL)"


# ─────────────────────────────── 盘点 ───────────────────────────────
def audit(conn: sqlite3.Connection) -> dict:
    """只读盘点：NULL 面 / 感知面 / ai↔character 混用面 / 未登记值分布。"""
    out: dict = {"total": 0, "null_speaker_type": 0, "null_perception": 0,
                 "perception_total": 0, "perception_filled": 0,
                 "distribution": {}, "unknown_values": {},
                 "ai_count": 0, "character_count": 0, "mix_note": ""}
    cols = _columns(conn, "memories")
    if "speaker_type" not in cols:
        out["mix_note"] = "memories 表缺 speaker_type 列"
        return out
    has_source = "source" in cols
    want = ["id", "speaker_type"] + (["source"] if has_source else ["'' AS source"])
    rows = conn.execute(f"SELECT {', '.join(want)} FROM memories").fetchall()
    dist: dict[str, int] = {}
    unknown: dict[str, int] = {}
    for _mid, spk, src in rows:
        key = _norm(spk)
        dist[key] = dist.get(key, 0) + 1
        if key != "(NULL)" and key.lower() not in KNOWN_ACTOR_VALUES:
            unknown[key] = unknown.get(key, 0) + 1
    out["total"] = len(rows)
    out["distribution"] = dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))
    out["unknown_values"] = dict(sorted(unknown.items(), key=lambda kv: (-kv[1], kv[0])))
    out["null_speaker_type"] = dist.get("(NULL)", 0)
    out["ai_count"] = sum(v for k, v in dist.items() if k.lower() == "ai")
    out["character_count"] = sum(v for k, v in dist.items() if k.lower() == "character")
    if has_source:
        null_ids_perception = [mid for mid, spk, src in rows
                               if _norm(spk) == "(NULL)" and (src or "").strip().lower() == PERCEPTION_SOURCE]
        out["null_perception"] = len(null_ids_perception)
        out["perception_total"] = sum(1 for _m, _s, src in rows
                                      if (src or "").strip().lower() == PERCEPTION_SOURCE)
        out["perception_filled"] = out["perception_total"] - out["null_perception"]
    else:
        out["null_perception"] = 0
        out["perception_total"] = 0
        out["mix_note"] = "memories 表缺 source 列（感知面按 0 计）"
    return out


# ─────────────────────────── 回填计划（幂等 + 孤儿跳过）───────────────────────────
def backfill_plan(conn: sqlite3.Connection) -> dict:
    """候选＝``source='perception'`` 且归属为空；孤儿（父行不存在）单列跳过。"""
    plan = {"candidate_ids": [], "orphan_ids": [], "orphan_sample": []}
    cols = _columns(conn, "memories")
    if not {"id", "speaker_type", "source"} <= set(cols):
        return plan
    rows = conn.execute("SELECT id, user_id, character_id FROM memories "
                        "WHERE lower(trim(coalesce(source,'')))='perception' "
                        "AND (speaker_type IS NULL OR trim(speaker_type)='')").fetchall()
    tables = _tables(conn)
    users = {r[0] for r in conn.execute("SELECT id FROM users")} if "users" in tables else None
    chars = {r[0] for r in conn.execute("SELECT id FROM ai_characters")} if "ai_characters" in tables else None
    for mid, uid, cid in rows:
        orphan = (users is not None and uid not in users) or (chars is not None and cid not in chars)
        if orphan:
            plan["orphan_ids"].append(mid)
            if len(plan["orphan_sample"]) < SAMPLE_LIMIT:
                plan["orphan_sample"].append({"id": mid, "user_id": uid, "character_id": cid})
        else:
            plan["candidate_ids"].append(mid)
    return plan


def apply_backfill(conn: sqlite3.Connection, ids: list[int]) -> int:
    """把给定 id 的归属补成 ``perception``（参数化 SQL，分批 IN，绝不拼接值）。"""
    written = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        cur = conn.execute(
            "UPDATE memories SET speaker_type=? "
            f"WHERE id IN ({ph}) AND (speaker_type IS NULL OR trim(speaker_type)='')",
            [PERCEPTION_ACTOR, *chunk],
        )
        written += int(cur.rowcount or 0)
    conn.commit()
    return written


def collect(db_path: Path, *, apply: bool = False) -> dict:
    """跑一遍盘点（``apply=True`` 时先只读出计划，再用可写连接落库并复核幂等）。"""
    result: dict = {"db": str(db_path), "ok": False, "applied": False, "written": 0, "note": ""}
    if not db_path.is_file():
        result["note"] = f"应用库不存在: {db_path}"
        return result
    try:
        conn = _connect(db_path, read_only=True)
    except sqlite3.Error as e:
        result["note"] = f"只读打开失败: {e.__class__.__name__}: {e}"
        return result
    try:
        if "memories" not in _tables(conn):
            result["note"] = "缺 memories 表（无从盘点）"
            return result
        result.update(audit(conn))
        plan = backfill_plan(conn)
        result["candidate_ids"] = plan["candidate_ids"]
        result["orphan_skipped"] = len(plan["orphan_ids"])
        result["orphan_sample"] = plan["orphan_sample"]
        result["ok"] = True
    except sqlite3.Error as e:
        result["note"] = f"盘点失败: {e.__class__.__name__}: {e}"
    finally:
        conn.close()
    if not apply or not result["ok"]:
        return result
    try:
        wconn = _connect(db_path, read_only=False)
    except sqlite3.Error as e:
        result["note"] = f"可写打开失败: {e.__class__.__name__}: {e}"
        return result
    try:
        result["written"] = apply_backfill(wconn, result["candidate_ids"])
        result["applied"] = True
        # 幂等复核：同一次连接再排一次计划，候选必须归零
        result["idempotent"] = len(backfill_plan(wconn)["candidate_ids"]) == 0
    except sqlite3.Error as e:
        result["note"] = f"回填失败: {e.__class__.__name__}: {e}"
    finally:
        wconn.close()
    return result


def print_report(data: dict) -> None:
    print("=== actor_audit（默认 dry-run：mode=ro + PRAGMA query_only，只 SELECT）===")
    print(f"库: {data.get('db')}")
    print("\n[1] speaker_type 空值面")
    print(f"    memories 总数={data.get('total')}  speaker_type NULL/空={data.get('null_speaker_type')}")
    print("\n[2] 感知面（本步判定应写成 perception 的对象）")
    print(f"    source=perception 总数={data.get('perception_total')}"
          f"  其中归属已填={data.get('perception_filled')}"
          f"  其中归属为空={data.get('null_perception')}")
    print(f"    可回填候选={len(data.get('candidate_ids') or [])}"
          f"  孤儿跳过={data.get('orphan_skipped')}")
    for i, o in enumerate(data.get("orphan_sample") or [], 1):
        print(f"      [孤儿{i}] id={o['id']} user_id={o['user_id']} character_id={o['character_id']}")
    print("\n[3] 归属值分布（含 ai 与 character 混用）")
    for k, v in (data.get("distribution") or {}).items():
        print(f"    {k} = {v}")
    print(f"    混用：ai={data.get('ai_count')}  character={data.get('character_count')}"
          "  （同一个语义两种写法，归一后应并入 character）")
    unk = data.get("unknown_values") or {}
    if unk:
        print(f"    未登记值={unk}")
    if data.get("note"):
        print(f"    备注: {data['note']}")
    if data.get("applied"):
        print(f"\n[写库] 本轮实际改写={data.get('written')} 条  幂等复核={data.get('idempotent')}")
    else:
        print("\n[写库] 未执行（dry-run；本轮约定只出报告，不洗存量）")
    print("\n结论: " + ("跑通" if data.get("ok") else "未取到数据")
          + f"；NULL={data.get('null_speaker_type')} 条，其中感知={data.get('null_perception')} 条"
          + f"；ai/character 混用={data.get('ai_count')}/{data.get('character_count')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Actor 归属存量只读盘点（默认 dry-run）")
    parser.add_argument("--app-db", type=Path, default=DEFAULT_APP_DB,
                        help=f"应用 SQLite 库（默认 {DEFAULT_APP_DB}）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出同样的键")
    parser.add_argument("--apply", action="store_true",
                        help="写库回填（NULL→perception，幂等、孤儿跳过）；缺省只读盘点")
    parser.add_argument("--force-production", action="store_true",
                        help="--apply 指向默认库时的显式确认（否则拒绝写生产库）")
    args = parser.parse_args(argv)
    resolved = args.app_db.resolve()
    if args.apply and resolved == DEFAULT_APP_DB.resolve() and not args.force_production:
        print("[拒绝] --apply 指向默认（生产）库 " + str(DEFAULT_APP_DB) +
              "；本轮约定不洗存量。确需执行请再加 --force-production", file=sys.stderr)
        return 2
    data = collect(resolved, apply=args.apply)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True))
    else:
        print_report(data)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
