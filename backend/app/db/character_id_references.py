# -*- coding: utf-8 -*-
"""角色 ID 引用规则与水位扫描（**中性模块**，2026-10-22 B1）。

为什么独立：此前 `migrate -> character_id_guard -> migrate` 形成双向依赖
（migrate 顶部取 guard 的常量/安装函数，guard 在函数内延迟 import migrate 的扫描）。
本模块只放**纯规则与只读扫描**，不导入 migrate / guard，也不写库；
序列修复（写 `sqlite_sequence`）、迁移编排、守卫安装仍在原模块。

单一真源：`CLIENT_SAFE_MAX_ID` 定义在此，`character_id_guard` 与 `migrate` 均从此导入，
原公开路径（`app.db.character_id_guard.CLIENT_SAFE_MAX_ID`、`app.db.migrate.scan_character_id_watermark`）
通过 re-export 保持不变。
"""
from __future__ import annotations

from dataclasses import dataclass

CLIENT_SAFE_MAX_ID = (1 << 53) - 1
INT64_MAX = (1 << 63) - 1

_ROLE_REF_SPECS: dict[tuple[str, str], None] = {
    ("lorebook_entries", "character_id"): None,
    ("world_facts", "character_id"): None,
    ("memory_archive", "character_id"): None,
    ("shared_events", "character_id"): None,
    ("prospective_intents", "character_id"): None,
    ("moment_ai_likes", "character_id"): None,
    ("channel_bindings", "character_id"): None,
    # 插件独立metadata无物理FK；绑定停用留痕/消息清理仍保留角色ID语义。
    # 依据 plugins/examples/wechat_ilink/models.py 与既有角色删除策略，
    # 这里只读计高水位，不删行、不重绑、不安装插件。
    ("wechat_ilink_bindings", "character_id"): None,
    ("wechat_ilink_messages", "character_id"): None,
    ("agent_tasks", "character_id"): None,
    ("agent_task_logs", "character_id"): None,
    ("image_gen_tasks", "character_id"): None,
    ("memory_write_receipts", "character_id"): None,
    ("ai_chats", "speaker_id"): None,
}

# 有判别列的多义引用：character_values 才计入；non_character_values 是已证明非角色
# （含 None 表示 NULL 本身已证明非角色）。其余判别值（含未登记 NULL）一律未知：
# 行内 id 为正整数时写前拒绝，不猜、不清理、不改型、不重绑。
# 依据（实际模型/读写代码）：
# - world_facts.subject_type: user/character/system（models/memory/__init__.py:285；
#   application/characters.py:885 写 character）；
# - memories.speaker_type: character/user/system/tool/perception；NULL=无归属
#   （models/memory/__init__.py:32；memory/speaker.py:33-61；actors.py:40-45）；
# - group_memories.speaker_type: ai/character 角色，user/system 非角色
#   （models/chat/__init__.py:118-121；memory/group_memory.py:129/234 写 system）；
# - moment_comments.sender_type: ai 角色，user 非角色
#   （models/life/__init__.py:268-269；moment_service.py:808/920/977 写 ai）；
# - pets.owner_type: ai 角色；user 与 NULL（旧数据）按用户宠物
#   （models/pet/__init__.py:37-38；agent/context/section_pet.py:40-42；
#   db/init_db.py:296-300 回填 user）。
_CONDITIONAL_REF_SPECS: dict[
    tuple[str, str], tuple[str, tuple[str, ...], tuple[str | None, ...]]
] = {
    ("world_facts", "subject_id"): ("subject_type", ("character",), ("user", "system")),
    ("memories", "speaker_id"): (
        "speaker_type", ("character",), ("user", "system", "tool", "perception"),
    ),
    ("group_memories", "speaker_id"): ("speaker_type", ("ai", "character"), ("user", "system")),
    ("moment_comments", "sender_id"): ("sender_type", ("ai",), ("user",)),
    ("pets", "owner_id"): ("owner_type", ("ai",), ("user", None)),
}
_AMBIGUOUS_REF_COLUMNS = ("subject_id", "speaker_id", "sender_id")


@dataclass(frozen=True)
class _RoleRef:
    table: str
    column: str
    discriminator: str | None = None
    character_values: tuple[str, ...] = ()
    non_character_values: tuple[str | None, ...] = ()


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _in_sql(values) -> str:
    return "(" + ", ".join(_lit(v) for v in values) + ")"


def _table_exists(conn, table: str) -> bool:
    return conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({_q(table)})").fetchall()]


def _tables(conn) -> list[str]:
    return [
        r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    ]


def _role_ref_columns(conn):
    """返回 (known_refs, unknown_character_id, unknown_ambiguous)。

    known_refs: [_RoleRef]；无条件引用 discriminator=None，多义引用带判别条件。
    """
    tables = _tables(conn)
    known: list[_RoleRef] = []
    seen: set[tuple[str, str]] = set()
    for table in tables:
        cols = _columns(conn, table)
        for fk in conn.exec_driver_sql(f"PRAGMA foreign_key_list({_q(table)})").fetchall():
            if fk[2] != "ai_characters":
                continue
            col = fk[3]
            if col in cols and (table, col) not in seen:
                seen.add((table, col))
                known.append(_RoleRef(table, col))
    for (table, col) in _ROLE_REF_SPECS:
        if table in tables and col in _columns(conn, table) and (table, col) not in seen:
            seen.add((table, col))
            known.append(_RoleRef(table, col))
    for (table, col), (disc, chars, nonchars) in _CONDITIONAL_REF_SPECS.items():
        if table in tables and col in _columns(conn, table) and (table, col) not in seen:
            seen.add((table, col))
            known.append(_RoleRef(table, col, disc, chars, nonchars))

    unknown_character_id: list[tuple[str, str]] = []
    unknown_ambiguous: list[tuple[str, str]] = []
    for table in tables:
        for col in _columns(conn, table):
            if col == "character_id" and (table, col) not in seen:
                unknown_character_id.append((table, col))
            if col in _AMBIGUOUS_REF_COLUMNS and (table, col) not in seen:
                unknown_ambiguous.append((table, col))
    return known, unknown_character_id, unknown_ambiguous


def _validate_id_column(conn, table: str, column: str, cond: str, *,
                        allow_zero: bool) -> int:
    """校验 id 列类型/范围并返回正高水位；零仅在 allow_zero 时作为哨兵。"""
    zero_clause = "" if allow_zero else f" OR {_q(column)} = 0"
    bad = conn.exec_driver_sql(
        f"SELECT typeof({_q(column)}), {_q(column)} FROM {_q(table)} "
        f"WHERE ({cond}) AND ({_q(column)} IS NOT NULL) AND "
        f"(typeof({_q(column)}) <> 'integer' OR {_q(column)} < 0{zero_clause}) LIMIT 1"
    ).fetchone()
    if bad is not None:
        typ, val = bad
        if typ != "integer":
            raise RuntimeError(
                f"B2b high-water preflight: {table}.{column} has non-integer SQLite type "
                f"{typ!r} value={val!r}; refusing before any write"
            )
        if int(val) < 0:
            raise RuntimeError(
                f"B2b high-water preflight: negative {table}.{column}={val}; "
                "data governance required"
            )
        raise RuntimeError(
            f"B2b high-water preflight: non-positive {table}.{column}={val}; "
            "data governance required"
        )
    row = conn.exec_driver_sql(
        f"SELECT MAX({_q(column)}) FROM {_q(table)} WHERE ({cond}) AND {_q(column)} > 0"
    ).fetchone()
    high = int(row[0]) if row and row[0] is not None else 0
    if high > CLIENT_SAFE_MAX_ID:
        raise RuntimeError(
            f"B2b high-water preflight: {table}.{column}={high} has no allocatable id in "
            f"client-safe range (client-safe max={CLIENT_SAFE_MAX_ID}, int64 max={INT64_MAX}); "
            "sequence exhausted"
        )
    if high >= INT64_MAX:
        raise RuntimeError(
            f"B2b high-water preflight: {table}.{column}={high} has no allocatable next id "
            f"(int64 max={INT64_MAX}); sequence exhausted"
        )
    return high


def _unknown_discriminator_cond(ref: _RoleRef) -> str:
    disc = _q(ref.discriminator)
    known_nonnull = tuple(ref.character_values) + tuple(
        v for v in ref.non_character_values if v is not None
    )
    parts = []
    if None not in ref.non_character_values:
        parts.append(f"{disc} IS NULL")
    if known_nonnull:
        parts.append(f"{disc} IS NOT NULL AND {disc} NOT IN {_in_sql(known_nonnull)}")
    return "(" + " OR ".join(parts) + ")" if parts else "0"


def _validate_ref_column(conn, ref: _RoleRef) -> int:
    col = _q(ref.column)
    if ref.discriminator is None:
        return _validate_id_column(
            conn, ref.table, ref.column, f"{col} IS NOT NULL", allow_zero=True,
        )

    disc = _q(ref.discriminator)
    char_cond = f"{disc} IN {_in_sql(ref.character_values)}"
    high = _validate_id_column(conn, ref.table, ref.column, char_cond, allow_zero=True)

    # 未知/未登记判别值（含未登记 NULL）：有效正整数 id 写前拒绝；0/NULL 不构成引用。
    unknown_cond = _unknown_discriminator_cond(ref)
    bad = conn.exec_driver_sql(
        f"SELECT typeof({col}), {col} FROM {_q(ref.table)} "
        f"WHERE {unknown_cond} AND {col} IS NOT NULL AND "
        f"(typeof({col}) <> 'integer' OR {col} < 0) LIMIT 1"
    ).fetchone()
    if bad is not None:
        raise RuntimeError(
            f"B2b high-water preflight: {ref.table}.{ref.column} under unknown/null "
            f"{ref.discriminator} has invalid type/value {bad!r}; refusing before any write"
        )
    cnt = conn.exec_driver_sql(
        f"SELECT COUNT(*) FROM {_q(ref.table)} WHERE {unknown_cond} AND {col} > 0"
    ).fetchone()[0]
    if int(cnt or 0) > 0:
        raise RuntimeError(
            f"B2b high-water preflight: {ref.table}.{ref.column} has {int(cnt)} positive id(s) "
            f"under unknown/null {ref.discriminator}; classify before migration "
            "(no clean/rebind/guess)"
        )
    return high


def _reject_unknown_columns(conn, unknown_character_id, unknown_ambiguous) -> list[str]:
    recorded: list[str] = []
    for table, column in unknown_character_id:
        bad = conn.exec_driver_sql(
            f"SELECT typeof({_q(column)}), {_q(column)} FROM {_q(table)} "
            f"WHERE {_q(column)} IS NOT NULL AND "
            f"(typeof({_q(column)}) <> 'integer' OR {_q(column)} <> 0) LIMIT 1"
        ).fetchone()
        if bad is not None:
            raise RuntimeError(
                f"B2b high-water preflight: unknown character_id semantics "
                f"{table}.{column} type/value={bad!r}; refusing to guess/clean/rebind"
            )
        recorded.append(f"{table}.{column}")
    for table, column in unknown_ambiguous:
        bad = conn.exec_driver_sql(
            f"SELECT typeof({_q(column)}), {_q(column)} FROM {_q(table)} "
            f"WHERE {_q(column)} IS NOT NULL LIMIT 1"
        ).fetchone()
        if bad is not None:
            raise RuntimeError(
                f"B2b high-water preflight: unclassified multi-semantic reference "
                f"{table}.{column} type/value={bad!r}; classify it before migration"
            )
        recorded.append(f"{table}.{column}")
    return recorded


def _ref_label(ref: _RoleRef) -> str:
    if ref.discriminator is None:
        return f"{ref.table}.{ref.column}"
    chars = "/".join(ref.character_values)
    nonchars = "/".join("NULL" if v is None else str(v) for v in ref.non_character_values)
    return (
        f"{ref.table}.{ref.column} ({ref.discriminator} in {chars}; "
        f"non-character {nonchars})"
    )


def scan_character_id_watermark(conn) -> dict:
    """只读扫描角色 id 高水位；返回 {high, known_refs, unknown_columns, existing_sequence}。

    供 Alembic 重建迁移与启动序列修正共用。任何类型/范围/未知语义问题都在写前抛出；
    未知/未登记判别值只报告表列与数量，不输出内容、不删除/改型/重绑。
    """
    if not _table_exists(conn, "ai_characters"):
        raise RuntimeError("B2b high-water preflight: ai_characters table missing")
    cols = _columns(conn, "ai_characters")
    if "id" not in cols:
        raise RuntimeError("B2b high-water preflight: ai_characters.id missing")
    high = _validate_id_column(conn, "ai_characters", "id", "1=1", allow_zero=False)

    known, unknown_character_id, unknown_ambiguous = _role_ref_columns(conn)
    for ref in known:
        high = max(high, _validate_ref_column(conn, ref))
    recorded = _reject_unknown_columns(conn, unknown_character_id, unknown_ambiguous)

    existing_sequence = None
    has_seq = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone() is not None
    if has_seq:
        row = conn.exec_driver_sql(
            "SELECT typeof(seq), seq FROM sqlite_sequence WHERE name='ai_characters'"
        ).fetchone()
        if row is not None:
            typ, val = row
            if typ != "integer":
                raise RuntimeError(f"B2b high-water preflight: sqlite_sequence.seq type {typ!r}")
            seq = int(val)
            if seq < 0:
                raise RuntimeError(f"B2b high-water preflight: sqlite_sequence.seq={seq} invalid")
            if seq > CLIENT_SAFE_MAX_ID:
                raise RuntimeError(
                    f"B2b high-water preflight: sqlite_sequence.seq={seq} exceeds client-safe "
                    f"max={CLIENT_SAFE_MAX_ID}; sequence exhausted"
                )
            high = max(high, seq)
            existing_sequence = seq
    return {
        "high": high,
        "known_refs": [_ref_label(r) for r in known],
        "unknown_columns": recorded,
        "existing_sequence": existing_sequence,
    }
