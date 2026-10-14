# -*- coding: utf-8 -*-
"""生活模板记忆的可撤销归档工具（默认干跑；只改 ``is_archived`` 一位，绝不物理删）。

背景（2026-10-14 派单）：生活循环 ``study`` 活动沉淀过一批「固定模板句」记忆（组框
``{角色名}学了「{主题}」，{收尾语}`` 与整句泛模板），写侧闸门已上线但存量仍在召回面。
本工具把「挑哪几行、为什么不能动、动了怎么撤」做成可复核的工程流程，**生产 apply 由主审执行**。

口径（唯一真源＝生产写入方 ``app/life/life_loop.py``，此处只复刻字面形状、不抄第二份判据）：
- 先决条件：``source='life' AND memory_type='event' AND sub_type='life_event'``，来源不是 life 的一律不进批；
- 形状判据：**整串**命中「组框＋四句收尾语之一」或「整句泛模板」，只用锚定的完整形状，
  不拿「有点收获」这类泛词单独判（实测会误杀日记正文）；
- 保护面：已归档 / 非 active / 待删（``delete_at``）/ 置顶 / 核心 / 上锁 / 有复习痕迹 /
  已排复习 / 自身是链上子节点 / 被任何行当父节点引用 / 同链有批外兄弟 / 被任何已知指针列引用 /
  命中 schema 里**未知**的 memories 引用列（无法排除即保留）。
- 指针列**逐值**核引用（含 ``*_memory_ids`` 这类 JSON 数组列，绝不做 ``IN`` 标量比对）：
  坏 JSON、非整数 id、未知对象/嵌套结构、以及**表列存在却读不动**的列 ⇒ 全体候选保留并给原因；
  表/列确实缺席（插件裸 schema）才允许忽略。

安全护栏：
- 只读面 ``sqlite3.connect("file:...?mode=ro", uri=True)`` + ``PRAGMA query_only=ON``，无 app 业务 import；
- ``--apply`` 必须配 ``--confirm`` 逐字确认串，且必须先出**在线备份**（``sqlite3.Connection.backup``）
  与**审计清单**（写盘失败即零写入）；
- 写入在 ``BEGIN IMMEDIATE`` 下重跑一遍选择/保护并逐行比对**原字段指纹**，漂移或新增引用即整批拒绝；
- 事务越界自检：批外行的归档位、其它表的行数必须逐字节不变，否则回滚；
- ``--restore`` 只按审计清单里记录的**原值**回填 ``is_archived``，并校验该行在本批之后没被别人改过
  （指纹一致）且仍是我们归档的那个状态——不满足就整批拒绝，不做盲反向 UPDATE。

用法（``--out`` 必须是**仓库外**私有目录，报告/清单不进 docs、不进公开快照）：
    backend\\.venv\\Scripts\\python.exe backend/scripts/archive_life_templates.py --out <仓库外目录>
    backend\\.venv\\Scripts\\python.exe backend/scripts/archive_life_templates.py --out <仓库外目录> --apply --confirm ARCHIVE-LIFE-TEMPLATES
    backend\\.venv\\Scripts\\python.exe backend/scripts/archive_life_templates.py --out <仓库外目录> --restore <apply 审计清单.json> --confirm RESTORE-LIFE-TEMPLATES
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "backend" / "data" / "sqlite" / "ai_companion.db"

APPLY_CONFIRM = "ARCHIVE-LIFE-TEMPLATES"
RESTORE_CONFIRM = "RESTORE-LIFE-TEMPLATES"

# 生产写入方字面量（app/life/life_loop.py 的 study 模板族）——形状判据，非用户内容
TAKEAWAYS = (
    "弄明白了一个之前卡住的点，顺手记了几笔。",
    "把之前模糊的地方理清了，有点收获。",
    "对其中一处细节想通了，记下来备查。",
    "整理了一遍要点，比昨天清楚一些。",
)
GENERIC_TAIL = "学了一会儿新东西，感觉有收获。"

# 组框：^{名字}学了「{主题}」，{四句收尾语}$（名字/主题用通配，绝不在源码里落真实角色名）
_FRAME_RE = re.compile(
    r"^[^「」\n]{1,40}?学了「[^「」\n]{1,80}」，(" + "|".join(re.escape(t) for t in TAKEAWAYS) + r")$"
)
# 整句泛模板：^{名字}{GENERIC_TAIL}$（必须整串，尾部不得再挂正文）
_GENERIC_RE = re.compile(r"^[^「」\n]{1,40}?" + re.escape(GENERIC_TAIL) + r"$")

MEMORIES_TABLE = "memories"

# 指纹覆盖的列（is_archived 之外的一切——本工具只允许归档位变化）
FINGERPRINT_COLUMNS = (
    "user_id", "character_id", "memory_type", "sub_type", "source", "source_id", "group_id",
    "speaker_id", "speaker_type", "epistemic_status", "related_memory_id", "title", "content",
    "scope", "importance", "is_pinned", "decay_base_at", "delete_at", "strength_days",
    "last_reinforce_at", "review_count", "next_review_at", "is_locked", "ai_rated",
    "departed_names", "why_it_matters", "is_core", "core_category", "confirmation_count",
    "contradiction_count", "reliability_score", "chain_id", "parent_id", "node_type",
    "version", "status", "superseded_by", "valid_from", "valid_to", "derived_from_ids",
    "created_at", "updated_at",
)

# 已核实的 memories 指针列（2026-10-14 schema 扫描口径）；不在表内者＝未知引用 → 保留
KNOWN_POINTERS = {
    ("memories", "parent_id"),
    ("memories", "related_memory_id"),
    ("memories", "superseded_by"),
    ("memories", "derived_from_ids"),          # JSON 数组，单独解析
    ("weave_card_memories", "memory_id"),
    ("memory_archive", "memory_id"),
    ("memory_write_receipts", "memory_id"),
    ("life_activity_logs", "memory_id"),
    ("life_followups", "memory_id"),
    ("relationship_events", "memory_id"),
    ("shared_events", "related_memory_id"),
}
# 指针列的语义分类：引用它即保留（audit 类同样保留，判据从宽不由本工具裁定业务含义）
POINTER_REASONS = {
    ("memories", "parent_id"): "referenced_as_parent",
    ("memories", "related_memory_id"): "referenced_related",
    ("memories", "superseded_by"): "referenced_superseded",
    ("weave_card_memories", "memory_id"): "referenced_weave_card",
    ("memory_archive", "memory_id"): "referenced_archive_row",
    ("memory_write_receipts", "memory_id"): "referenced_receipt",
    ("life_activity_logs", "memory_id"): "referenced_life_activity",
    ("life_followups", "memory_id"): "referenced_life_followup",
    ("relationship_events", "memory_id"): "referenced_relationship_event",
    ("shared_events", "related_memory_id"): "referenced_shared_event",
    ("memories", "derived_from_ids"): "referenced_derived_from",
}
POINTER_ID_COLUMNS = {
    ("memories", "parent_id"),
    ("memories", "related_memory_id"),
    ("memories", "superseded_by"),
    ("weave_card_memories", "memory_id"),
    ("memory_archive", "memory_id"),
    ("memory_write_receipts", "memory_id"),
    ("life_activity_logs", "memory_id"),
    ("life_followups", "memory_id"),
    ("relationship_events", "memory_id"),
    ("shared_events", "related_memory_id"),
}
# derived_from_ids 是 JSON 数组列，值形态由 _classify_pointer 严核（不当标量）
DERIVED_POINTER = ("memories", "derived_from_ids")


def _q(ident: object) -> str:
    """动态 SQL 标识符：双引号包裹 + 内嵌双引号成对转义（特殊表/列名不得被误读成两段）。"""
    return '"' + str(ident).replace('"', '""') + '"'


_INT_LIKE = re.compile(r"^[+-]?\d+$")
# 指针值里「对象包一层数组」的既有写法；其余对象结构＝解释不了 → 保留
_POINTER_DICT_LIST_KEYS = ("ids", "terms")
_POINTER_DICT_ID_KEYS = ("id", "memory_id")


def _classify_pointer(value, id_by_str: dict[str, int], depth: int = 0) -> tuple[set[int], list[str]]:
    """指针列单个值 → (命中的候选 id, 解释不了的形态描述)。

    标量数字/纯数字串按 id 核；JSON 数组逐项严核；坏 JSON、非整数、未知对象、
    嵌套过深、含数字的非数组形态一律计入「排除不掉」，绝不当标量强转 int。
    描述只报形态，绝不回显值内容（可能是用户数据）。
    """
    hit: set[int] = set()
    bad: list[str] = []
    if value is None:
        return hit, bad
    if depth > 4:
        return hit, ["嵌套过深"]
    if isinstance(value, bool):
        return hit, ["布尔值当指针"]
    if isinstance(value, int):
        mid = id_by_str.get(str(value))
        if mid is not None:
            hit.add(mid)
        return hit, bad
    if isinstance(value, float):
        if value.is_integer():
            return _classify_pointer(int(value), id_by_str, depth + 1)
        return hit, ["非整数指针"]
    if isinstance(value, (bytes, bytearray)):
        return _classify_pointer(value.decode("utf-8", "replace"), id_by_str, depth + 1)
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in ("null", "none"):
            return hit, bad
        if _INT_LIKE.match(s):
            mid = id_by_str.get(str(int(s)))
            if mid is not None:
                hit.add(mid)
            return hit, bad
        if s[0] in '[{"':
            try:
                parsed = json.loads(s)
            except Exception:
                return hit, [f"坏JSON(len={len(s)})"]
            return _classify_pointer(parsed, id_by_str, depth + 1)
        if any(ch.isdigit() for ch in s):
            return hit, [f"不支持的指针形态(len={len(s)},含数字)"]
        return hit, bad
    if isinstance(value, (list, tuple)):
        for item in value:
            h, b = _classify_pointer(item, id_by_str, depth + 1)
            hit |= h
            bad += b
        return hit, bad
    if isinstance(value, dict):
        for key in _POINTER_DICT_LIST_KEYS:
            if key in value:
                return _classify_pointer(value[key], id_by_str, depth + 1)
        for key in _POINTER_DICT_ID_KEYS:
            if key in value:
                return _classify_pointer(value[key], id_by_str, depth + 1)
        return hit, [f"未知对象结构(keys={len(value)})"]
    return hit, [f"不支持的指针类型({type(value).__name__})"]


def _schema_of(
    con: sqlite3.Connection,
) -> tuple[set[str], dict[str, set[str]], dict[str, list], dict[str, str]]:
    """(表名集合, 表→列集合, 表→外键行, 读不出 schema 的表→原因)。

    schema 本身读不动不许当成「没有列」：缺席与读失败是两件事，后者一律排除不掉。
    """
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    cols: dict[str, set[str]] = {}
    fks: dict[str, list] = {}
    bad: dict[str, str] = {}
    for t in tables:
        try:
            cols[t] = table_columns(con, t)
            fks[t] = con.execute(f"PRAGMA foreign_key_list({_q(t)})").fetchall()
        except sqlite3.Error as e:
            bad[t] = f"schema读取失败({e.__class__.__name__})"
    return set(tables), cols, fks, bad


def _scan_pointer_column(con, table: str, column: str, id_by_str: dict[str, int],
                         schema) -> tuple[set[int], str | None]:
    """整列去重读取后逐值严核。表/列确实缺席 ⇒ (空, None)（可选表，可忽略）；
    存在却读不动 ⇒ (空, 原因)（不可吞成「无引用」）。"""
    tables, colmap, _fks, schema_bad = schema
    if table in schema_bad:
        return set(), schema_bad[table]
    if table not in tables or column not in colmap.get(table, set()):
        return set(), None
    try:
        rows = con.execute(f"SELECT DISTINCT {_q(column)} FROM {_q(table)}").fetchall()
    except sqlite3.Error as e:
        return set(), f"读取失败({e.__class__.__name__})"
    hit: set[int] = set()
    bad: list[str] = []
    for (val,) in rows:
        try:
            h, b = _classify_pointer(val, id_by_str)
        except Exception:
            h, b = set(), ["无法解释的指针值"]
        hit |= h
        bad += b
    return hit, (";".join(dict.fromkeys(bad)) if bad else None)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def open_ro(db: Path) -> sqlite3.Connection:
    """只读连接：驱动层就写不进去（mode=ro），再叠一道 PRAGMA query_only。"""
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({_q(table)})").fetchall()}


def fingerprint_columns(con: sqlite3.Connection) -> tuple[str, ...]:
    have = table_columns(con, MEMORIES_TABLE)
    return tuple(c for c in FINGERPRINT_COLUMNS if c in have)


def fingerprint(cols: tuple[str, ...], row: dict) -> str:
    """原字段指纹：列名排序后 json（key 稳定），只输出哈希、绝不输出正文。"""
    payload = {c: row.get(c) for c in sorted(cols)}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _row_select(con: sqlite3.Connection) -> tuple[str, str]:
    """候选查询的列清单：id + 归档位 + 指纹列（归档位单独留原值，不进指纹）。"""
    cols = fingerprint_columns(con)
    head = "SELECT id, is_archived, " + ",".join(_q(c) for c in cols) + f" FROM {_q(MEMORIES_TABLE)}"
    return cols, head


def _unpack(cols: tuple[str, ...], raw: tuple) -> dict:
    row = {"id": raw[0], "is_archived": int(bool(raw[1]))}
    row.update(dict(zip(cols, raw[2:])))
    return row


def candidate_rows(con: sqlite3.Connection) -> list[dict]:
    """形状 + 来源三重命中；SQL 只按来源/类型过滤，形状判定在 Python 整串锚定完成。"""
    cols, head = _row_select(con)
    sql = (
        f"{head} WHERE source='life' AND memory_type='event' AND sub_type='life_event'"
    )
    out = []
    for raw in con.execute(sql).fetchall():
        row = _unpack(cols, raw)
        content = row.get("content") or ""
        if _FRAME_RE.match(content):
            row["shape"] = "frame"
        elif _GENERIC_RE.match(content):
            row["shape"] = "generic"
        else:
            continue
        row["fingerprint"] = fingerprint(cols, row)
        out.append(row)
    return out


def discover_pointers(
    con: sqlite3.Connection, schema=None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """扫 schema 找出所有可能指向 memories.id 的列：外键 + 列名形态 + 已核实白名单。

    返回 (已知指针, 未知指针)。未知指针一律按「无法排除」处理 → 引用即保留。
    schema 读不动的表在此**不产生指针**，由 inbound_references 计为「排除不掉」。
    """
    tables, colmap, fks, schema_bad = schema or _schema_of(con)
    found: set[tuple[str, str]] = set()
    for t in sorted(tables):
        if t in schema_bad:
            continue
        for f in fks.get(t, []):
            if f[2] == MEMORIES_TABLE:
                found.add((t, f[3]))
        for c in colmap.get(t, set()):
            low = c.lower()
            if low.endswith("memory_id") or low.endswith("_memory_ids") or low == "memory_ids":
                found.add((t, c))
    found |= set(KNOWN_POINTERS)
    known = sorted(p for p in found if p in KNOWN_POINTERS)
    unknown = sorted(p for p in found if p not in KNOWN_POINTERS)
    return known, unknown


def inbound_references(
    con: sqlite3.Connection, ids: set[int]
) -> tuple[dict[int, list[str]], dict[int, list[str]], dict[str, str]]:
    """反向引用：指针列 → (已知命中, 未知命中, 解释不了的列)。

    已知标量列与 JSON 列（derived_from_ids）以及一切未知指针列**统一逐值严核**，
    不再用 ``WHERE col IN (...)`` 的标量比对——JSON 数组、TEXT 存 id、嵌套形态在
    SQL 比对下都会静默漏掉。第三项非空时所有候选一律保留（排除不掉）。
    """
    if not ids:
        return {}, {}, {}
    schema = _schema_of(con)
    schema_bad = schema[3]
    known, unknown = discover_pointers(con, schema)
    id_by_str = {str(int(i)): int(i) for i in ids}
    scan: list[tuple[str, str, bool]] = [(t, c, True) for (t, c) in unknown]
    scan += [(t, c, False) for (t, c) in known
             if (t, c) in POINTER_ID_COLUMNS or (t, c) == DERIVED_POINTER]
    refs: dict[int, list[str]] = {}
    unk_hits: dict[int, list[str]] = {}
    unresolved: dict[str, str] = {}
    # schema 读不动的表：列清单都拿不到，无法排除它藏着指针列 → 全体候选保留
    for table in sorted(schema_bad):
        unresolved[f"{table}(schema)"] = schema_bad[table]
    for table, column, is_unknown in scan:
        hit, detail = _scan_pointer_column(con, table, column, id_by_str, schema)
        desc = f"{table}.{column}"
        if detail:
            prev = unresolved.get(desc)
            unresolved[desc] = f"{prev};{detail}" if prev else detail
        if is_unknown:
            reason = f"unknown_reference:{desc}"
            bucket = unk_hits
        else:
            reason = POINTER_REASONS.get((table, column), f"referenced_{table}_{column}")
            bucket = refs
        for mid in sorted(hit):
            bucket.setdefault(mid, []).append(reason)
    return refs, unk_hits, unresolved


def protect_reasons(con: sqlite3.Connection, rows: list[dict]) -> dict[int, list[str]]:
    """逐行保护判定（保守：命中任一即保留，不进入可归档面）。"""
    ids = {r["id"] for r in rows}
    refs, unk_refs, unresolved = inbound_references(con, ids) if ids else ({}, {}, {})
    unresolved_tags = [f"unresolved_reference:{desc}:{why}" for desc, why in sorted(unresolved.items())]
    sibling_chains: set[str] = set()
    if ids:
        chains = {r["chain_id"] for r in rows if r.get("chain_id")}
        if chains:
            cph = ",".join("?" * len(chains))
            for chain, mid in con.execute(
                    f"SELECT chain_id, id FROM {MEMORIES_TABLE} WHERE chain_id IN ({cph})", tuple(chains)).fetchall():
                if mid not in ids:
                    sibling_chains.add(chain)
    out: dict[int, list[str]] = {}
    for r in rows:
        reasons: list[str] = []
        if r.get("is_archived"):
            reasons.append("already_archived")
        if (r.get("status") or "") != "active":
            reasons.append("status_not_active")
        if r.get("delete_at"):
            reasons.append("delete_pending")
        if r.get("is_pinned"):
            reasons.append("pinned")
        if r.get("is_core"):
            reasons.append("core")
        if r.get("is_locked"):
            reasons.append("locked")
        if (r.get("review_count") or 0) > 0:
            reasons.append("reviewed")
        if r.get("next_review_at"):
            reasons.append("review_scheduled")
        if r.get("parent_id"):
            reasons.append("child_node")
        if r.get("title"):
            reasons.append("has_title")
        if r.get("why_it_matters"):
            reasons.append("has_meaning_layer")
        for tag in refs.get(r["id"], []):
            reasons.append(tag)
        for tag in unk_refs.get(r["id"], []):
            reasons.append(tag)
        reasons.extend(unresolved_tags)
        if r.get("chain_id") and r["chain_id"] in sibling_chains:
            reasons.append("chain_sibling_outside_batch")
        out[r["id"]] = reasons
    return out


def build_plan(con: sqlite3.Connection) -> dict:
    """干跑计划（纯聚合 + 候选 id + 指纹，绝不含正文）。"""
    rows = candidate_rows(con)
    archived_in_shape = sum(1 for r in rows if r.get("is_archived"))
    reasons = protect_reasons(con, rows)
    blocked = {rid: rs for rid, rs in reasons.items() if rs}
    allowed = [r["id"] for r in rows if not reasons.get(r["id"])]
    reason_counts: dict[str, int] = {}
    for rs in blocked.values():
        for tag in set(rs):
            reason_counts[tag] = reason_counts.get(tag, 0) + 1
    unresolved_cols = sorted({t for rs in reasons.values() for t in rs
                              if t.startswith("unresolved_reference:")})
    known, unknown = discover_pointers(con)
    return {
        "tool": "archive_life_templates",
        "version": 1,
        "generated_at_utc": _now_utc(),
        "db_basename": None,  # 由调用处填入文件名（不落绝对路径）
        "totals": {
            "memories_all": con.execute(f"SELECT COUNT(*) FROM {MEMORIES_TABLE}").fetchone()[0],
            "life_event_rows": con.execute(
                f"SELECT COUNT(*) FROM {MEMORIES_TABLE} "
                "WHERE source='life' AND memory_type='event' AND sub_type='life_event'").fetchone()[0],
            "shape_candidates": len(rows),
            "shape_frame": sum(1 for r in rows if r["shape"] == "frame"),
            "shape_generic": sum(1 for r in rows if r["shape"] == "generic"),
            "shape_already_archived": archived_in_shape,
            "protected": len(blocked),
            "archivable": len(allowed),
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "pointer_columns_known": [f"{t}.{c}" for t, c in known],
        "pointer_columns_unknown": [f"{t}.{c}" for t, c in unknown],
        "pointer_unresolved": unresolved_cols,
        "candidates": [
            {"id": r["id"], "shape": r["shape"], "is_archived": int(bool(r.get("is_archived"))),
             "fingerprint": r["fingerprint"], "protected": reasons.get(r["id"], [])}
            for r in rows
        ],
        "allowed_ids": allowed,
    }


def _assert_outside_repo(out: Path) -> Path:
    out = out.resolve()
    try:
        out.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return out
    raise SystemExit(f"[停止] --out 必须在仓库外（避免用户数据进 git）：{out} 位于 {REPO_ROOT}")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _table_counts(con: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def _online_backup(con: sqlite3.Connection, dst: Path) -> Path:
    """在线备份（sqlite3.Connection.backup，非 SQL BACKUP TO）；副本必须过 quick_check。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tgt = sqlite3.connect(str(dst))
    try:
        con.backup(tgt)
        ok = tgt.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        tgt.close()
    if ok != "ok":
        raise RuntimeError(f"备份自检未通过：quick_check={ok}")
    return dst


def _current_rows(con: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    cols, head = _row_select(con)
    ph = ",".join("?" * len(ids))
    out: dict[int, dict] = {}
    for raw in con.execute(f"{head} WHERE id IN ({ph})", tuple(ids)).fetchall():
        row = _unpack(cols, raw)
        row["fingerprint"] = fingerprint(cols, row)
        out[row["id"]] = row
    return out


def _guard_no_write(con: sqlite3.Connection) -> None:
    """只读闸门自检：query_only 必须为开，否则立即停手。"""
    if con.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise RuntimeError("只读闸门失效（query_only 未开）")


def run_dry(db: Path, out: Path) -> dict:
    out = _assert_outside_repo(out)
    con = open_ro(db)
    try:
        _guard_no_write(con)
        plan = build_plan(con)
    finally:
        con.close()
    plan["db_basename"] = Path(db).name
    _write_json(out / "plan.json", plan)
    return plan


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def apply_plan(db: Path, out: Path, plan: dict, confirm: str) -> dict:
    """按干跑白名单归档；备份或审计清单落盘失败 ⇒ 零写入。"""
    if confirm != APPLY_CONFIRM:
        raise SystemExit(f"[拒绝] 缺少明确确认（--confirm {APPLY_CONFIRM}）")
    out = _assert_outside_repo(out)
    ids = [int(x) for x in plan.get("allowed_ids") or []]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = out / f"apply_manifest_{stamp}.json"
    audit: dict = {
        "mode": "apply", "started_at_utc": _now_utc(), "db_basename": Path(db).name,
        "plan_generated_at_utc": plan.get("generated_at_utc"), "requested_ids": ids,
        "backup": None, "written": 0, "result": "aborted", "rejects": {},
    }
    if not ids:
        audit["result"] = "noop-empty-plan"
        _write_json(audit_path, audit)
        return audit

    con = sqlite3.connect(str(db))
    try:
        # 1) 写前在线备份（备份失败 → 直接返回，一行都不写）
        backup_path = out / f"db_backup_{stamp}.sqlite"
        try:
            _online_backup(con, backup_path)
        except Exception as e:
            audit["rejects"]["backup_failed"] = f"{e.__class__.__name__}: {e}"
            _write_json(audit_path, audit)
            return audit
        audit["backup"] = {"name": backup_path.name, "size": backup_path.stat().st_size}
        expected = {c["id"]: c["fingerprint"] for c in plan["candidates"] if c["id"] in ids}

        con.execute("BEGIN IMMEDIATE")
        try:
            # 2) 事务内重校：重跑选择/保护 + 逐行原字段指纹漂移校验（不满足即整批拒绝）
            archived_before_all = con.execute(
                f"SELECT COUNT(*) FROM {MEMORIES_TABLE} WHERE is_archived=1").fetchone()[0]
            counts_before = _table_counts(con)
            plan_now = build_plan(con)
            allowed_now = set(plan_now["allowed_ids"])
            rows_now = _current_rows(con, ids)
            rejects: dict[str, list[int]] = {"drift": [], "newly_protected": [], "row_missing": [],
                                             "not_archived_expected": []}
            targets: list[int] = []
            for i in ids:
                row = rows_now.get(i)
                if row is None:
                    rejects["row_missing"].append(i)
                    continue
                if row["fingerprint"] != expected.get(i):
                    rejects["drift"].append(i)
                    continue
                if row["is_archived"] != int(plan_row_archived(plan, i)):
                    rejects["not_archived_expected"].append(i)
                    continue
                if i not in allowed_now:
                    rejects["newly_protected"].append(i)
                    continue
                targets.append(i)
            audit["rejects"] = {k: v for k, v in rejects.items() if v}
            audit["recheck"] = {
                "shape_candidates": plan_now["totals"]["shape_candidates"],
                "protected": plan_now["totals"]["protected"],
                "archivable": plan_now["totals"]["archivable"],
            }
            if not targets:
                con.execute("ROLLBACK")
                audit["result"] = "rejected"
                _write_json(audit_path, audit)
                return audit

            # 3) 审计清单先落盘（含每行原归档位与原字段指纹）——写不进盘就不写库
            audit["rows"] = [{"id": i, "is_archived_before": int(plan_row_archived(plan, i)),
                              "fingerprint_at_plan": expected[i]} for i in targets]
            audit["pending_writes"] = len(targets)
            _write_json(audit_path, audit)

            for n, i in enumerate(targets, 1):
                cur = con.execute(
                    f"UPDATE {MEMORIES_TABLE} SET is_archived=1 WHERE id=? AND is_archived=0", (i,))
                if cur.rowcount != 1:
                    raise RuntimeError(f"归档第 {n} 条写入异常（rowcount={cur.rowcount}）")
            # 4) 事务越界自检：其它表行数不变、全表行数不变、归档位增量恰等于本批
            counts_after = _table_counts(con)
            if counts_after != counts_before:
                stray = {k: (counts_before.get(k), v) for k, v in counts_after.items()
                         if counts_before.get(k) != v}
                raise RuntimeError(f"越界：其它表行数变化 {stray}")
            archived_after = con.execute(
                f"SELECT COUNT(*) FROM {MEMORIES_TABLE} WHERE is_archived=1").fetchone()[0]
            if archived_after - archived_before_all != len(targets):
                raise RuntimeError(
                    f"越界：归档位增量 {archived_after - archived_before_all} != 本批 {len(targets)}")
            left = con.execute(
                f"SELECT COUNT(*) FROM {MEMORIES_TABLE} WHERE is_archived=0 "
                f"AND source='life' AND memory_type='event' AND sub_type='life_event'").fetchone()[0]
            audit["after"] = {"archived_all": archived_after, "life_event_unarchived": left}
            con.execute("COMMIT")
        except Exception as e:
            con.execute("ROLLBACK")
            audit["result"] = "rolled-back"
            audit["error"] = f"{e.__class__.__name__}: {e}"
            _write_json(audit_path, audit)
            return audit
        audit["written"] = len(targets)
        audit["result"] = "applied"
        audit["finished_at_utc"] = _now_utc()
        _write_json(audit_path, audit)
        return audit
    finally:
        con.close()


def plan_row_archived(plan: dict, rid: int) -> int:
    for c in plan["candidates"]:
        if c["id"] == rid:
            return int(c["is_archived"])
    raise KeyError(rid)


def restore_apply(db: Path, out: Path, audit: dict, confirm: str) -> dict:
    """按 apply 审计清单撤销：只回填本批原归档位，指纹不符/状态被人改过 ⇒ 整批拒绝。"""
    if confirm != RESTORE_CONFIRM:
        raise SystemExit(f"[拒绝] 缺少明确确认（--confirm {RESTORE_CONFIRM}）")
    out = _assert_outside_repo(out)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = out / f"restore_manifest_{stamp}.json"
    rows = audit.get("rows") or []
    res: dict = {"mode": "restore", "started_at_utc": _now_utc(), "db_basename": Path(db).name,
                 "source_apply": audit.get("finished_at_utc"), "restored": 0,
                 "result": "aborted", "conflicts": {}}
    if audit.get("result") != "applied" or not rows:
        res["conflicts"]["bad_source"] = [f"result={audit.get('result')} rows={len(rows)}"]
        _write_json(result_path, res)
        return res

    con = sqlite3.connect(str(db))
    try:
        backup_path = out / f"db_backup_restore_{stamp}.sqlite"
        try:
            _online_backup(con, backup_path)
        except Exception as e:
            res["conflicts"]["backup_failed"] = [f"{e.__class__.__name__}: {e}"]
            _write_json(result_path, res)
            return res
        res["backup"] = {"name": backup_path.name, "size": backup_path.stat().st_size}
        ids = [int(r["id"]) for r in rows]

        con.execute("BEGIN IMMEDIATE")
        try:
            counts_before = _table_counts(con)
            archived_before_all = con.execute(
                f"SELECT COUNT(*) FROM {MEMORIES_TABLE} WHERE is_archived=1").fetchone()[0]
            cur_rows = _current_rows(con, ids)
            conflicts: dict[str, list[int]] = {"row_missing": [], "fingerprint_drift": [],
                                               "state_not_ours": []}
            targets: list[tuple[int, int]] = []
            for r in rows:
                rid = int(r["id"])
                row = cur_rows.get(rid)
                if row is None:
                    conflicts["row_missing"].append(rid)
                    continue
                if row["fingerprint"] != r["fingerprint_at_plan"]:
                    # 本批之后该行被人改过（内容/权重/链位）——不盲反向覆盖别人的写入
                    conflicts["fingerprint_drift"].append(rid)
                    continue
                want_before = int(r["is_archived_before"])
                if want_before != 0 or row["is_archived"] != 1:
                    # 本工具只做过 0→1；当前不是 1 说明状态已被其它路径改动
                    conflicts["state_not_ours"].append(rid)
                    continue
                targets.append((rid, want_before))
            res["conflicts"] = {k: v for k, v in conflicts.items() if v}
            if not targets:
                con.execute("ROLLBACK")
                res["result"] = "rejected"
                _write_json(result_path, res)
                return res
            res["rows"] = [{"id": rid, "is_archived_restore_to": want} for rid, want in targets]
            _write_json(result_path, res)

            for n, (rid, want) in enumerate(targets, 1):
                cur = con.execute(
                    f"UPDATE {MEMORIES_TABLE} SET is_archived=? WHERE id=? AND is_archived<>?",
                    (want, rid, want))
                if cur.rowcount != 1:
                    raise RuntimeError(f"撤销第 {n} 条写入异常（rowcount={cur.rowcount}）")
            counts_after = _table_counts(con)
            if counts_after != counts_before:
                stray = {k: (counts_before.get(k), v) for k, v in counts_after.items()
                         if counts_before.get(k) != v}
                raise RuntimeError(f"越界：其它表行数变化 {stray}")
            archived_after = con.execute(
                f"SELECT COUNT(*) FROM {MEMORIES_TABLE} WHERE is_archived=1").fetchone()[0]
            if archived_after - archived_before_all != -len(targets):
                raise RuntimeError(
                    f"越界：归档位增量 {archived_after - archived_before_all} != 预期 -{len(targets)}")
            con.execute("COMMIT")
        except Exception as e:
            con.execute("ROLLBACK")
            res["result"] = "rolled-back"
            res["error"] = f"{e.__class__.__name__}: {e}"
            _write_json(result_path, res)
            return res
        res["restored"] = len(targets)
        res["result"] = "restored"
        res["finished_at_utc"] = _now_utc()
        _write_json(result_path, res)
        return res
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生活模板记忆可撤销归档（默认干跑，绝不物理删）")
    ap.add_argument("--db", default=os.environ.get("AMBRACE_DB", str(DEFAULT_DB)))
    ap.add_argument("--out", required=True, help="仓库外私有目录（计划/清单/备份）")
    ap.add_argument("--apply", action="store_true", help="真正写库（须配 --confirm）")
    ap.add_argument("--confirm", default="", help=f"逐字确认串：{APPLY_CONFIRM} / {RESTORE_CONFIRM}")
    ap.add_argument("--restore", default="", help="apply 审计清单 json 路径（撤销本批）")
    ap.add_argument("--plan", default="", help="复用既有 plan.json（默认现跑一次干跑）")
    args = ap.parse_args(argv)

    db = Path(args.db)
    out = Path(args.out)
    if not db.exists():
        print(f"[停止] 库不存在：{db.name}")
        return 2
    try:
        out = _assert_outside_repo(out)
    except SystemExit as e:
        print(str(e))
        return 2

    if args.restore:
        audit = _load_json(Path(args.restore))
        res = restore_apply(db, out, audit, args.confirm)
        print("[restore] %s 撤销 %d 行；冲突面（条数）=%s"
              % (res["result"], res["restored"],
                 {k: (len(v) if isinstance(v, list) else 1) for k, v in res["conflicts"].items()}))
        return 0 if res["result"] == "restored" else 1

    plan = _load_json(Path(args.plan)) if args.plan else run_dry(db, out)
    t = plan["totals"]
    print("[干跑] 全表=%s life_event=%s 形状命中=%s（组框%s/泛句%s，其中已归档%s）保护=%s 可归档=%s"
          % (t["memories_all"], t["life_event_rows"], t["shape_candidates"], t["shape_frame"],
             t["shape_generic"], t["shape_already_archived"], t["protected"], t["archivable"]))
    if plan["pointer_columns_unknown"]:
        print("[警告] schema 发现未知 memories 指针列：%s" % ", ".join(plan["pointer_columns_unknown"]))
    print("[干跑] 保护原因分布：%s" % json.dumps(plan["reason_counts"], ensure_ascii=False))
    if not args.apply:
        print("[干跑] 未写库（加 --apply --confirm %s 才执行；执行前自动在线备份）" % APPLY_CONFIRM)
        return 0
    audit = apply_plan(db, out, plan, args.confirm)
    print("[apply] %s 写入 %s 行；拒绝面（条数）=%s；备份=%s"
          % (audit["result"], audit.get("written", 0),
             {k: (len(v) if isinstance(v, list) else 1) for k, v in audit["rejects"].items()},
             (audit.get("backup") or {}).get("name")))
    return 0 if audit["result"] in ("applied", "noop-empty-plan") else 1


if __name__ == "__main__":
    sys.exit(main())
