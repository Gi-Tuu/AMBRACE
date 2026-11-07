# -*- coding: utf-8 -*-
"""世界认知相关表 user_id 缺失/为 0 一次性回填脚本（审计第三批第 11 项，P2-05 收尾）

背景：P2-05 要求 ai_moments 等世界认知相关表的 user_id（或等价归属列）不缺失，
历史数据按 character.user_id（从属关系）回填；写入路径兜底在代码侧完成（本批已改）。

用法（供 Codex 核实后执行）：
  python scripts/backfill_world_user_id.py [--db PATH] [--apply] [--tables a,b,c]

  --db     数据库路径（默认自动解析 <项目根>/data/sqlite/ai_companion.db）
  --apply  实际写库（默认 dry-run 只统计与打印将执行的 UPDATE 数，不写库）
  --tables 只处理指定表（逗号分隔），默认全表清单

安全：
- 默认 dry-run；--apply 时在单个事务内执行（失败回滚）；
- 只 UPDATE user_id 为空/为 0 的行，值一律取自 ai_characters.user_id（不编造）；
- 角色不存在/角色 user_id 也为空 → 保持原值并计入 unresolvable，报告中列出。
仅用标准库 + sqlite3，无新依赖。
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "backend" / "data" / "sqlite" / "ai_companion.db"

# 表清单：(表名, 角色列, user 列, 额外条件, 说明)
# extra_where 为空时默认 user_id IS NULL OR user_id=0
TABLE_SPECS = [
    ("ai_moments", "character_id", "user_id", "", "P2-05 主表：AI 动态归属"),
    ("moment_comments", "sender_id", "user_id", "sender_type='ai'", "AI 角色评论（sender_id=角色id）"),
    ("agent_task_logs", "character_id", "user_id", "", "任务 trace"),
    ("agent_tasks", "character_id", "user_id", "", "任务态记录"),
    ("proactive_trigger_logs", "character_id", "user_id", "", "主动触发候选日志"),
    ("relationship_events", "character_id", "user_id", "", "关系事件"),
    ("weave_cards", "character_id", "user_id", "", "织库卡片"),
    ("reflection_logs", "character_id", "user_id", "", "复盘日志"),
    ("proactive_storyline_items", "character_id", "user_id", "", "主动剧情项"),
    ("life_artifacts", "character_id", "user_id", "", "生活产物"),
    ("life_schedules", "character_id", "user_id", "", "生活日程"),
    ("memories", "character_id", "user_id", "", "常规记忆（兜底 0 值）"),
    ("stage_memories", "character_id", "user_id", "", "舞台记忆（兜底 0 值）"),
    ("world_facts", "character_id", "user_id", "", "世界事实（兜底 0 值）"),
]


def load_character_user_map(cur) -> dict[int, int | None]:
    """ai_characters.id -> user_id（缺失/为 0 时映射为 None 标记不可解析）"""
    try:
        rows = cur.execute("SELECT id, user_id FROM ai_characters").fetchall()
    except sqlite3.Error:
        return {}
    m = {}
    for cid, uid in rows:
        m[int(cid)] = int(uid) if uid else None
    return m


def table_exists(cur, table: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def column_names(cur, table: str) -> set[str]:
    # PRAGMA table_info 不支持占位符绑定，改用语义等价的 table-valued pragma 函数（SQLite 3.16+）
    return {r[0] for r in cur.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()}


def _needs_cols(cur, table: str, cols: tuple) -> bool:
    have = column_names(cur, table)
    return not all(c in have for c in cols)


def _skipped_result(table: str) -> dict:
    return {"table": table, "skipped": "缺少列", "missing": 0, "resolvable": 0,
            "unresolvable": 0, "total": 0, "updates": []}


def _collect_result(table: str, total, rows, char_map: dict) -> dict:
    updates = []
    unresolvable = 0
    for rid, cid in rows:
        uid = char_map.get(int(cid)) if cid else None
        if uid:
            updates.append((int(rid), int(uid)))
        else:
            unresolvable += 1
    return {
        "table": table, "skipped": None, "total": total,
        "missing": len(rows), "resolvable": len(updates),
        "unresolvable": unresolvable, "updates": updates,
    }


def compute_updates(cur, spec: tuple, char_map: dict) -> dict:
    """计算某表待回填行的 (rowid, 解析到的 user_id)；纯计算，不写库。

    表名/列名/条件全部来自模块内固定的 TABLE_SPECS 常量：SQL 按表展开成独立字面量块
    （不做任何字符串拼接），数据值一律 ? 绑定。各分支的 WHERE 子句与原 f-string 拼接
    结果逐字一致（含 moment_comments 的原运算符优先级形态）。
    """
    table = spec[0]
    if table == "ai_moments":
        if _needs_cols(cur, "ai_moments", ("character_id", "user_id")):
            return _skipped_result("ai_moments")
        total = cur.execute("SELECT COUNT(*) FROM ai_moments").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM ai_moments WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("ai_moments", total, rows, char_map)
    if table == "moment_comments":
        if _needs_cols(cur, "moment_comments", ("sender_id", "user_id")):
            return _skipped_result("moment_comments")
        total = cur.execute("SELECT COUNT(*) FROM moment_comments").fetchone()[0]
        rows = cur.execute("SELECT id, sender_id FROM moment_comments WHERE user_id IS NULL OR user_id=0 AND sender_type='ai'").fetchall()
        return _collect_result("moment_comments", total, rows, char_map)
    if table == "agent_task_logs":
        if _needs_cols(cur, "agent_task_logs", ("character_id", "user_id")):
            return _skipped_result("agent_task_logs")
        total = cur.execute("SELECT COUNT(*) FROM agent_task_logs").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM agent_task_logs WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("agent_task_logs", total, rows, char_map)
    if table == "agent_tasks":
        if _needs_cols(cur, "agent_tasks", ("character_id", "user_id")):
            return _skipped_result("agent_tasks")
        total = cur.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM agent_tasks WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("agent_tasks", total, rows, char_map)
    if table == "proactive_trigger_logs":
        if _needs_cols(cur, "proactive_trigger_logs", ("character_id", "user_id")):
            return _skipped_result("proactive_trigger_logs")
        total = cur.execute("SELECT COUNT(*) FROM proactive_trigger_logs").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM proactive_trigger_logs WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("proactive_trigger_logs", total, rows, char_map)
    if table == "relationship_events":
        if _needs_cols(cur, "relationship_events", ("character_id", "user_id")):
            return _skipped_result("relationship_events")
        total = cur.execute("SELECT COUNT(*) FROM relationship_events").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM relationship_events WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("relationship_events", total, rows, char_map)
    if table == "weave_cards":
        if _needs_cols(cur, "weave_cards", ("character_id", "user_id")):
            return _skipped_result("weave_cards")
        total = cur.execute("SELECT COUNT(*) FROM weave_cards").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM weave_cards WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("weave_cards", total, rows, char_map)
    if table == "reflection_logs":
        if _needs_cols(cur, "reflection_logs", ("character_id", "user_id")):
            return _skipped_result("reflection_logs")
        total = cur.execute("SELECT COUNT(*) FROM reflection_logs").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM reflection_logs WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("reflection_logs", total, rows, char_map)
    if table == "proactive_storyline_items":
        if _needs_cols(cur, "proactive_storyline_items", ("character_id", "user_id")):
            return _skipped_result("proactive_storyline_items")
        total = cur.execute("SELECT COUNT(*) FROM proactive_storyline_items").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM proactive_storyline_items WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("proactive_storyline_items", total, rows, char_map)
    if table == "life_artifacts":
        if _needs_cols(cur, "life_artifacts", ("character_id", "user_id")):
            return _skipped_result("life_artifacts")
        total = cur.execute("SELECT COUNT(*) FROM life_artifacts").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM life_artifacts WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("life_artifacts", total, rows, char_map)
    if table == "life_schedules":
        if _needs_cols(cur, "life_schedules", ("character_id", "user_id")):
            return _skipped_result("life_schedules")
        total = cur.execute("SELECT COUNT(*) FROM life_schedules").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM life_schedules WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("life_schedules", total, rows, char_map)
    if table == "memories":
        if _needs_cols(cur, "memories", ("character_id", "user_id")):
            return _skipped_result("memories")
        total = cur.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM memories WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("memories", total, rows, char_map)
    if table == "stage_memories":
        if _needs_cols(cur, "stage_memories", ("character_id", "user_id")):
            return _skipped_result("stage_memories")
        total = cur.execute("SELECT COUNT(*) FROM stage_memories").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM stage_memories WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("stage_memories", total, rows, char_map)
    if table == "world_facts":
        if _needs_cols(cur, "world_facts", ("character_id", "user_id")):
            return _skipped_result("world_facts")
        total = cur.execute("SELECT COUNT(*) FROM world_facts").fetchone()[0]
        rows = cur.execute("SELECT id, character_id FROM world_facts WHERE user_id IS NULL OR user_id=0").fetchall()
        return _collect_result("world_facts", total, rows, char_map)
    raise ValueError("未知表: %r" % (table,))


def apply_updates(con, spec: tuple, updates: list) -> int:
    """执行 UPDATE（单事务由调用方控制）；返回更新行数。

    SQL 按表展开成独立字面量块（表名来自固定 TABLE_SPECS，不做字符串拼接），数据值 ? 绑定。
    """
    table = spec[0]
    n = 0
    for rid, uid in updates:
        if table == "ai_moments":
            con.execute("UPDATE ai_moments SET user_id=? WHERE id=?", (uid, rid))
        elif table == "moment_comments":
            con.execute("UPDATE moment_comments SET user_id=? WHERE id=?", (uid, rid))
        elif table == "agent_task_logs":
            con.execute("UPDATE agent_task_logs SET user_id=? WHERE id=?", (uid, rid))
        elif table == "agent_tasks":
            con.execute("UPDATE agent_tasks SET user_id=? WHERE id=?", (uid, rid))
        elif table == "proactive_trigger_logs":
            con.execute("UPDATE proactive_trigger_logs SET user_id=? WHERE id=?", (uid, rid))
        elif table == "relationship_events":
            con.execute("UPDATE relationship_events SET user_id=? WHERE id=?", (uid, rid))
        elif table == "weave_cards":
            con.execute("UPDATE weave_cards SET user_id=? WHERE id=?", (uid, rid))
        elif table == "reflection_logs":
            con.execute("UPDATE reflection_logs SET user_id=? WHERE id=?", (uid, rid))
        elif table == "proactive_storyline_items":
            con.execute("UPDATE proactive_storyline_items SET user_id=? WHERE id=?", (uid, rid))
        elif table == "life_artifacts":
            con.execute("UPDATE life_artifacts SET user_id=? WHERE id=?", (uid, rid))
        elif table == "life_schedules":
            con.execute("UPDATE life_schedules SET user_id=? WHERE id=?", (uid, rid))
        elif table == "memories":
            con.execute("UPDATE memories SET user_id=? WHERE id=?", (uid, rid))
        elif table == "stage_memories":
            con.execute("UPDATE stage_memories SET user_id=? WHERE id=?", (uid, rid))
        elif table == "world_facts":
            con.execute("UPDATE world_facts SET user_id=? WHERE id=?", (uid, rid))
        else:
            raise ValueError("未知表: %r" % (table,))
        n += 1
    return n


def run(con, tables: list[str], apply: bool) -> int:
    cur = con.cursor()
    char_map = load_character_user_map(cur)
    if not char_map:
        print("[warn] ai_characters 表为空或不可读——所有行将视为不可解析", file=sys.stderr)
    print(f"# 世界认知相关表 user_id 回填{'（--apply 执行）' if apply else '（dry-run，未写库）'}")
    print("")
    print("| 表 | 总行数 | 缺失(NULL/0) | 可回填 | 不可解析 | 说明 |")
    print("|---|---|---|---|---|---|")
    total_missing = total_resolvable = total_unresolvable = 0
    plan = []
    for spec in TABLE_SPECS:
        table = spec[0]
        if tables and table not in tables:
            continue
        if not table_exists(cur, table):
            print(f"| {table} | 表不存在 | - | - | - | 跳过 |")
            continue
        res = compute_updates(cur, spec, char_map)
        plan.append((spec, res))
        if res["skipped"]:
            print(f"| {table} | - | - | - | - | {res['skipped']}，跳过 |")
            continue
        total_missing += res["missing"]
        total_resolvable += res["resolvable"]
        total_unresolvable += res["unresolvable"]
        print(
            f"| {table} | {res['total']} | {res['missing']} | {res['resolvable']} | "
            f"{res['unresolvable']} | {spec[4]} |"
        )
    print("")
    print(f"合计：缺失 {total_missing}，可回填 {total_resolvable}，不可解析 {total_unresolvable}")
    print("")
    if not apply:
        print("[dry-run] 加 --apply 才执行 UPDATE（单事务，失败回滚）")
        return 0 if total_resolvable == 0 else 2  # 2=有待执行回填（dry-run 完成）
    con.execute("BEGIN")
    try:
        applied = 0
        for spec, res in plan:
            if res.get("skipped") or not res["updates"]:
                continue
            n = apply_updates(con, spec, res["updates"])
            applied += n
            print(f"[applied] {spec[0]}: {n} 行")
        con.commit()
    except Exception as e:
        con.rollback()
        print(f"[error] 回滚（未写库）：{e}", file=sys.stderr)
        return 1
    print(f"[done] 共回填 {applied} 行（事务已提交）")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="世界认知相关表 user_id 一次性回填（默认 dry-run）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 数据库路径")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只统计不写）")
    parser.add_argument("--tables", default="", help="只处理指定表（逗号分隔）")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[error] 数据库不存在：{db_path}", file=sys.stderr)
        return 1
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    con = sqlite3.connect(str(db_path))
    con.text_factory = str
    try:
        return run(con, tables, args.apply)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
