# -*- coding: utf-8 -*-
"""world_facts 存量清理脚本（批次一 任务6，2026-09-16；独立脚本、默认 dry-run）。

交接：批次一（时效与现状止血）；交接文档见项目 output/ 目录（不写本机绝对路径）。
清理项（全部只改 status，**不物理删除**，与「留痕可追溯」口径一致）：

  1) 三条已核实错误的身份事实 → status='superseded'（**走 supersede 链，不用裸 expired**）：
     #15「用户是宣传部艺术负责人」、#42「用户是学校工作人员」、#64「用户是设计师，从事设计工作」
     （用户实际是在读大二学生）。若同角色已存在批次一的「当前现状锚点」事实，则把 superseded_by
     指向它（形成真正的取代链）；否则 superseded_by 留空（仅置位，与 assert_fact 的 12 条淘汰路径同形）。
  2) kind='relationship_baseline' 的 active 重复合并：**按 character_id 分组**各保留 1 条权威记录
     （asserted_at 最新，并列取 id 最大），组内其余置 superseded 且 superseded_by=保留行 id。
     说明：交接写「合并为 1 条」，但该表按角色隔离（char13=sam / char6=DeepSeek 各一套语义：
     「我是用户的老公」vs「用户的对象是sam（男）」），跨角色合并会让别的角色错认自己的身份关系，
     故按角色各留 1 条权威记录（实测 14 → 2），此为对交接的唯一有意偏离，已在交付回报中标注。
  3) 裸 expired（status='expired'）只**打印清单与来源**，不做任何修改。来源已核实为
     2026-09-16 10:53（北京）用户在前端世界设定页手工删重复所致：唯一写入路径是
     ``app/application/characters.py::delete_world_fact``（P1-3 2026-09-15 放宽的「本角色任意活跃事实可删」
     软删接口）；该路径与「重复即取代（supersede）」的治理方向不一致，属批次二/后续收敛项，本脚本不擅动。

安全设计（与 scripts/dedupe_world_facts.py、scripts/cleanup_prospective_intents.py 同约定）：
  - 默认 dry-run：只读连接，只打印计划变更与计数，不写任何东西；
  - ``--apply`` 才写库，且写前自动整库备份到 ``backend/data/sqlite/backups/``
    （``ai_companion.db.pre_worldfact_cleanup_<UTC时间戳>``）；备份失败即中止；
  - 幂等可重复执行（已 superseded 的不再入待处理集）；
  - 单事务（BEGIN IMMEDIATE）内完成全部 UPDATE，异常整体回滚；
  - 建议先停服务器再 --apply（避免与在线进程并发写 SQLite）。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_world_facts.py            # dry-run（默认）
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_world_facts.py --apply    # 先备份再写库
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get(
    "AMB_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "data", "sqlite", "ai_companion.db"),
)
DEFAULT_BACKUPS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "backend", "data", "sqlite", "backups",
)
DEFAULT_WRONG_IDS = (15, 42, 64)
RELATION_KIND = "relationship_baseline"
ANCHOR_PREFIX = "用户当前常驻"  # 批次一「当前现状锚点」事实的 object_value 前缀


def connect_ro(db_path: str) -> sqlite3.Connection:
    """只读连接（dry-run 用）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def connect_rw(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    return con


def backup_path(backups_dir: str, now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    return os.path.join(backups_dir, f"ai_companion.db.pre_worldfact_cleanup_{ts}")


def do_backup(db_path: str, backups_dir: str) -> str | None:
    """写库前整库备份；返回备份路径（失败返回 None）。"""
    if not db_path or not os.path.exists(db_path):
        print(f"[backup] 跳过：找不到库文件 {db_path!r}")
        return None
    os.makedirs(backups_dir, exist_ok=True)
    dst = backup_path(backups_dir)
    shutil.copy2(db_path, dst)
    print(f"[backup] 已备份 {db_path} → {dst}（{os.path.getsize(dst)} bytes）")
    return dst


# ────────────────────────── 计划（纯函数，便于单测）──────────────────────────

def find_wrong_identity_facts(con: sqlite3.Connection, ids: tuple[int, ...]) -> list[dict]:
    """待置 superseded 的错误身份事实（只取 active）。"""
    if not ids:
        return []
    ph = ",".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT id, character_id, user_id, object_value, status, kind FROM world_facts "
        f"WHERE id IN ({ph}) AND status='active' ORDER BY id",
        list(ids),
    ).fetchall()
    return [dict(r) for r in rows]


def find_anchor_fact_ids(con: sqlite3.Connection) -> dict[int, int]:
    """批次一现状锚点事实：{character_id: fact_id}（active，object_value 以锚点前缀开头）。"""
    rows = con.execute(
        "SELECT id, character_id FROM world_facts "
        "WHERE status='active' AND object_value LIKE ? ORDER BY id",
        (ANCHOR_PREFIX + "%",),
    ).fetchall()
    out: dict[int, int] = {}
    for r in rows:
        out.setdefault(int(r["character_id"]), int(r["id"]))
    return out


def find_relationship_baseline_groups(con: sqlite3.Connection) -> list[dict]:
    """按 character_id 分组的 relationship_baseline active 重复。

    返回 [{character_id, keep: dict, drop: [dict, ...]}, ...]；
    keep = asserted_at 最新（并列取 id 最大），drop = 组内其余（id 升序）；组内仅 1 条则不入结果。
    """
    rows = [dict(r) for r in con.execute(
        "SELECT id, character_id, object_value, asserted_at, status FROM world_facts "
        "WHERE status='active' AND kind=? ORDER BY character_id, asserted_at, id",
        (RELATION_KIND,),
    ).fetchall()]
    groups: dict[int, list[dict]] = {}
    for r in rows:
        groups.setdefault(int(r["character_id"]), []).append(r)
    out: list[dict] = []
    for cid, items in sorted(groups.items()):
        if len(items) <= 1:
            continue
        ordered = sorted(items, key=lambda r: (str(r["asserted_at"] or ""), int(r["id"])))
        keep = ordered[-1]
        drop = [r for r in ordered[:-1]]
        out.append({"character_id": cid, "keep": keep, "drop": drop})
    return out


def find_bare_expired(con: sqlite3.Connection) -> list[dict]:
    """裸 expired 清单（只报告，不修改）。"""
    return [dict(r) for r in con.execute(
        "SELECT id, character_id, subject_type, predicate, kind, object_value, updated_at "
        "FROM world_facts WHERE status='expired' ORDER BY id"
    ).fetchall()]


# ────────────────────────── 执行 ──────────────────────────

def _snippet(text: str, n: int = 44) -> str:
    t = " ".join((text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def apply_changes(con: sqlite3.Connection, wrong: list[dict], groups: list[dict],
                  anchors: dict[int, int]) -> tuple[int, int]:
    """单事务写入：错误身份事实 + 关系基线重复。返回 (wrong_n, merged_n)。"""
    con.execute("BEGIN IMMEDIATE")
    try:
        w = 0
        for r in wrong:
            con.execute(
                "UPDATE world_facts SET status='superseded', superseded_by=?,"
                " superseded_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
                (anchors.get(int(r["character_id"])), int(r["id"])),
            )
            w += 1
        m = 0
        for g in groups:
            keep_id = int(g["keep"]["id"])
            for r in g["drop"]:
                con.execute(
                    "UPDATE world_facts SET status='superseded', superseded_by=?,"
                    " superseded_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
                    (keep_id, int(r["id"])),
                )
                m += 1
        con.commit()
        return w, m
    except Exception:
        con.rollback()
        raise


def main() -> int:
    ap = argparse.ArgumentParser(
        description="world_facts 存量清理（任务6；默认 dry-run，--apply 才写；写前自动整库备份）")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"数据库路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--apply", action="store_true", help="实际写库（缺省=只读 dry-run）")
    ap.add_argument("--yes", action="store_true", help="跳过交互确认（仍需 --apply）")
    ap.add_argument("--backups-dir", default=DEFAULT_BACKUPS_DIR,
                    help=f"整库备份目录（默认 {DEFAULT_BACKUPS_DIR}）")
    ap.add_argument("--wrong-ids", type=int, nargs="*", default=list(DEFAULT_WRONG_IDS),
                    help=f"错误身份事实 id（默认 {' '.join(map(str, DEFAULT_WRONG_IDS))}）")
    ap.add_argument("--skip-relationship-merge", action="store_true",
                    help="不做关系基线重复合并（只处理错误身份事实）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB 不存在: {args.db}")
        return 2

    con = connect_ro(args.db)
    try:
        wrong = find_wrong_identity_facts(con, tuple(args.wrong_ids))
        anchors = find_anchor_fact_ids(con)
        groups = [] if args.skip_relationship_merge else find_relationship_baseline_groups(con)
        expired = find_bare_expired(con)
        total_rel_active = con.execute(
            "SELECT COUNT(*) FROM world_facts WHERE status='active' AND kind=?", (RELATION_KIND,)
        ).fetchone()[0]
    finally:
        con.close()

    print("=== world_facts 存量清理（dry-run 默认）===")
    print(f"DB: {args.db}")
    print(f"阈值: wrong_ids={list(args.wrong_ids)} relationship_merge="
          f"{'off' if args.skip_relationship_merge else 'on'}")

    print(f"\n[1] 错误身份事实 → superseded：{len(wrong)} 条")
    for r in wrong:
        print(f"    id={r['id']:<5} char={r['character_id']:<4} kind={r['kind']:<6} "
              f"superseded_by={anchors.get(int(r['character_id']))}  {_snippet(r['object_value'])}")
    if wrong and not anchors:
        print("    ⚠ 未发现批次一「当前现状锚点」事实 → superseded_by 将留空。")
        print("      建议先跑 scripts/memory/batch1_plan_and_anchor_governance.py --apply --only anchor，"
              "再跑本脚本，以形成真正的取代链。")

    merged = [r for g in groups for r in g["drop"]]
    print(f"\n[2] relationship_baseline active：{total_rel_active} 条 → 按角色各留 1 条权威记录，"
          f"拟 superseded {len(merged)} 条（{len(groups)} 个角色分组）")
    for g in groups:
        print(f"  -- char={g['character_id']} keep=id{g['keep']['id']} "
              f"（{_snippet(g['keep']['object_value'])}）")
        for r in g["drop"]:
            print(f"       drop id={r['id']:<5}  {_snippet(r['object_value'])}")

    print(f"\n[3] 裸 expired（只报告，本脚本不动）：{len(expired)} 条")
    print("    来源：2026-09-16 10:53（北京）用户在前端世界设定页手工删重复；唯一写入路径 "
          "app/application/characters.py::delete_world_fact（P1-3 软删接口，与本批 supersede 方向不同）。")
    for r in expired[:5]:
        print(f"    id={r['id']:<5} char={r['character_id']:<4} kind={r['kind']:<22} "
              f"updated={str(r['updated_at'])[:19]}  {_snippet(r['object_value'], 30)}")
    if len(expired) > 5:
        print(f"    …… 其余 {len(expired) - 5} 条（完整清单见 --apply 前 dry-run 输出/数据库查询）")

    print(f"\n合计拟变更：错误身份 {len(wrong)} + 关系基线合并 {len(merged)} = {len(wrong) + len(merged)} 条"
          f"（不删行，只改 status + superseded_by/superseded_at）")

    if not args.apply:
        print("\n[dry-run] 未写库；确认影响面后加 --apply 执行（脚本会先自动整库备份）。")
        return 0

    if not args.yes:
        ans = input(f"即将把 {len(wrong)} 条身份事实与 {len(merged)} 条关系基线置 superseded。"
                    f"确认请输入大写 YES：").strip()
        if ans != "YES":
            print("[abort] 未确认，已退出（未写任何数据）。")
            return 1

    if not do_backup(args.db, args.backups_dir):
        print("[abort] 备份失败，未做任何写入。")
        return 2

    con = connect_rw(args.db)
    try:
        w, m = apply_changes(con, wrong, groups, anchors)
    except Exception as e:
        print(f"[abort] 事务失败已整体回滚，未产生任何变更: {e}")
        return 3
    finally:
        con.close()

    con = connect_ro(args.db)
    try:
        after_wrong = find_wrong_identity_facts(con, tuple(args.wrong_ids))
        after_groups = find_relationship_baseline_groups(con)
        after_rel_active = con.execute(
            "SELECT COUNT(*) FROM world_facts WHERE status='active' AND kind=?", (RELATION_KIND,)
        ).fetchone()[0]
    finally:
        con.close()

    print(f"\n[done] 已提交：身份事实 superseded {w} 条；关系基线合并 superseded {m} 条。")
    print(f"[done] after：待处理身份事实 {len(after_wrong)} 条；relationship_baseline active "
          f"{total_rel_active} → {after_rel_active} 条（剩余重复分组 {len(after_groups)} 个，应为 0，每角色 1 条）")
    print("[note] 回滚：从 backup 文件还原，或按 id 手工 status='active'/superseded_by=NULL。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
