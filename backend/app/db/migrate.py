# -*- coding: utf-8 -*-
"""Alembic 迁移集成：启动时把当前数据库对齐到版本链 head。

设计决策（渐进式，避免把 90+ 条手工迁移重写为版本链的高风险）：
- ``init_db()``（app/db/database.py）仍是幂等兼容层：首次建库（create_all）+ 增量补丁
  （PRAGMA 幂等 ALTER）继续生效，保证存量与全新部署的 schema 都到当前模型状态，**不动**。
- 本模块负责「版本记账」：把当前库的 alembic_version 对齐到 head。3.8 收尾（渐进版，
  2026-09-03）把「无版本」分支细分为三类，避免远古库被直接 stamp 后缺列：
  - **全新空库（无任何表）** → stamp head（不重放，版本记账）；
  - **非空老库（有表无版本，schema 落后）** → alembic upgrade head 整链重放：版本链已具备
    has_table / has_index / has_column 全守卫 + bootstrap 补列迁移，可对已存在表安全重放，
    补齐远古库缺的列（修复「只 stamp 不重放」造成的缺列漂移）；
  - **当前 schema 库（有表无版本，但列已在 init_db/create_all 建到当前模型）** → stamp head
    （不重放；用「是否已含链上新增列」判别，避免每会话重放 16 条迁移）。
  - 已知旧版本且落后于 head → alembic upgrade head（不变）；
  - 孤儿版本（版本号不在本链）→ stamp head --purge（清旧历史再标，理由见函数内）。
- 使用同步引擎（源自 settings.database_url，去掉 +aiosqlite）执行，避免与异步引擎混用；
  Alembic 迁移脚本本身跑在同步引擎上（SQLite 用内置 sqlite3 驱动）。
- 未来新增 schema 变更：改模型 → ``alembic revision --autogenerate`` 生成修订 → 入版本链；
  init_db 不要再新增手工 ALTER。
"""
import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.character_id_guard import CLIENT_SAFE_MAX_ID, ensure_client_safe_id_guard

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"
_ALEMBIC_DIR = _BACKEND_ROOT / "alembic"

# ── B2b：角色引用高水位（启动序列修正 + 迁移预检共用单一真源）──
INT64_MAX = (1 << 63) - 1
# CLIENT_SAFE_MAX_ID 由 app.db.character_id_guard 提供（2^53-1）。
# 约束引入 revision；启动对齐必须以它为锚点，而非“最新 head 的父修订”。
CONSTRAINT_REVISION = "b2a0c1d2e3f4"

# 无物理 FK 但有明确角色语义的无条件引用。
_ROLE_REF_SPECS: dict[tuple[str, str], None] = {
    ("lorebook_entries", "character_id"): None,
    ("world_facts", "character_id"): None,
    ("memory_archive", "character_id"): None,
    ("shared_events", "character_id"): None,
    ("prospective_intents", "character_id"): None,
    ("moment_ai_likes", "character_id"): None,
    ("channel_bindings", "character_id"): None,
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


def repair_ai_character_sequence(conn) -> dict:
    """显式修正已有 AUTOINCREMENT 表的序列；只写 sqlite_sequence，不改 schema。

    启动路径只在 schema 检测确认表已 AUTOINCREMENT 后调用；不是只读检测的一部分。
    """
    ddl_row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
    ).fetchone()
    if ddl_row is None or "AUTOINCREMENT" not in (ddl_row[0] or "").upper():
        raise RuntimeError("B2b sequence repair refused: ai_characters is not AUTOINCREMENT")
    scan = scan_character_id_watermark(conn)
    high = scan["high"]
    has_seq = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone() is not None
    if not has_seq:
        raise RuntimeError("B2b sequence repair refused: sqlite_sequence missing")
    row = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'"
    ).fetchone()
    before = int(row[0]) if row is not None else None
    if before is None:
        conn.exec_driver_sql(
            "INSERT INTO sqlite_sequence(name, seq) VALUES ('ai_characters', ?)", (high,)
        )
    elif before < high:
        conn.exec_driver_sql(
            "UPDATE sqlite_sequence SET seq=? WHERE name='ai_characters'", (high,)
        )
    return {
        "before": before,
        "after": high if before is None else max(before, high),
        "high": high,
        "changed": before is None or before < high,
    }

# 「当前 schema」判别哨兵：『只由版本链引入、init_db 从不添加』的表/列/索引。
# 全新/当前库（current create_all）含全部这些表/列/索引；远古库（pre-alembic 旧库）缺其中若干。
# 命中全部 → 判定为当前 schema → stamp（不重放）；缺任一 → 判定为落后 → upgrade head。
# 注：create_all 路径会建出的表用「表级哨兵」（如 A5 的 user_runtime_flags），其余为列级哨兵；
#     索引迁移既不建表也不加列，用 idx: 前缀挂在同一张表里（同一条判别、同一个接缝，见
#     tests/test_admin_llm_quota.py 靠 patch 这张表来隔离语义的用法）。
_INDEX_SENTINEL_PREFIX = "idx:"
_CURRENT_SCHEMA_SENTINELS: list[tuple[str, str]] = [
    ("users", "parent_id"),
    ("memories", "group_id"),
    ("memories", "status"),
    ("memories", "superseded_by"),
    ("memories", "derived_from_ids"),
    ("memories", "valid_from"),
    ("memories", "valid_to"),
    ("ai_characters", "talkativeness"),
    ("ai_characters", "talkativeness_locked"),
    ("ai_characters", "user_llm_config_id"),
    ("chat_group_members", "muted"),
    ("chat_group_messages", "game_session_id"),
    ("chat_group_messages", "msg_type"),
    ("life_states", "current_room"),
    ("life_states", "location"),
    ("life_states", "location_updated_at"),
    ("llm_usage", "config_id"),
    ("llm_usage", "group_owner_id"),
    ("lorebook_entries", "cooldown_rounds"),
    ("lorebook_entries", "inclusion_group"),
    ("lorebook_entries", "is_regex"),
    ("lorebook_entries", "probability"),
    ("lorebook_entries", "sticky_rounds"),
    ("plugins", "source"),
    ("plugins", "source_url"),
    ("plugins", "sha256"),
    ("plugins", "consented_permissions"),
    ("plugins", "consented_at"),
    # ── A2 M1 插件归户（2026-09-20）：plugins.owner_user_id 只由迁移链 add_column 引入，
    # init_db 的 create_all 会建（当前模型含该列）但远古库不会 —— 老库（有表无版本号）缺此列时
    # 必须判「落后」走 upgrade head，否则会被 stamp 到 head 却永久缺列（select 直接报错）。
    ("plugins", "owner_user_id"),
    # ── A2 M6 插件同意按租户（2026-09-20）：plugin_consents 是【只由迁移链 create_table 引入】的表 ──
    # 老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表
    # （registry.get_tenant_consented_permissions / backfill 直接 select 报错，租户级同意静默失效）。
    ("plugin_consents", "plugin_name"),
    # ── P3-4（2026-09-19）：MCP 本地回环细粒度放行（mcp_servers.allow_loopback）──
    # 该列只由 Alembic 迁移链 add_column 引入、init_db 从不添加：老库（有表但无版本号）缺此列时
    # 必须判为「落后」走 upgrade head，否则会被 stamp 到 head 却永久缺列（select 直接报错）。
    ("mcp_servers", "allow_loopback"),
    # ── A5 用户级开关覆盖（2026-09-19）：user_runtime_flags 是【只由迁移链 create_table 引入】的表 ──
    # 老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表
    # （flag_service.resolve_flag/get_user_flags 直接 select 报错，用户级覆盖静默失效）。
    ("user_runtime_flags", "user_id"),
    # ── A8 LLM 额度按账号（2026-09-20）：user_llm_limits 同样是【只由迁移链 create_table 引入】的表 ──
    # 老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表
    # （llm_quota.resolve_limit / get_user_overrides 直接 select 报错，账号级额度静默失效）。
    ("user_llm_limits", "user_id"),
    # ── 一机多主 / 渠道绑定 per-账号化（2026-09-05，28→31；T5 2026-09-10 回退到 29）──
    # channel_bindings 是【主表】（app/models/channel/__init__.py），保留。
    ("channel_bindings", "tenant_id"),
    # T5（2026-09-10）：以下两条为【插件表】，已移出主 Base.metadata、不在主 schema 判别内 ——
    # 插件表的存在性由 registry.ensure_plugin_tables() 幂等保证（未装载渠道=本就不该有表），
    # 若继续列为主哨兵，未装某渠道的部署会被反复判「落后」而去 upgrade，
    # 但版本链并不建 wechat_ilink_*（baseline 只建 douyin_*），反而造成无意义重放。
    # ("wechat_ilink_bindings", "tenant_id"),   # 移除（插件表，registry 建）
    # ("douyin_accounts", "tenant_id"),         # 移除（插件表；版本链 baseline 仍建、_migration_chain_tables 自动判别仍覆盖）
    # ── X7-M1 结构化承载（2026-09-22，派单 P9）：phone_snapshots.payload_json ──
    # 该列由迁移 f4a5b6c7d8e9 add_column 引入；老库（有表无版本号）缺此列时必须判「落后」走
    # upgrade head 补列，否则会被 stamp 到 head 却永久缺列（select PhoneSnapshot 直接报错）。
    ("phone_snapshots", "payload_json"),
    # ── 断点 #4 快照归属三列（2026-09-29）：actor / character_id / confidence ──
    # 三列【只由迁移 d7e8f9a0b1c2 add_column 引入】（init_db 幂等层不补，同 payload_json 先例）。
    # 老库（有表无版本号）缺列时若不判「落后」，会被 stamp 到 head 却永久缺列——写入侧带 actor
    # 直接报错（快照整条写失败），且 select(PhoneSnapshot) 也会炸。
    ("phone_snapshots", "actor"),
    ("phone_snapshots", "character_id"),
    ("phone_snapshots", "confidence"),
    # ── X7-M4c-3 行动名单落库（2026-09-23，派单 P19）：两张【只由迁移链 create_table 引入】的表 ──
    # device_action_targets 是闸门④（目标白名单）的权威来源、device_action_plugins 是闸门③a
    # （逐插件灰度）的权威来源。老库缺表时若不判「落后」，会被 stamp 到 head 却永久缺表——
    # 读库异常被 fail-closed 吞成「名单为空＝全拒」，运维看到的是拒而不是缺表，问题被掩盖。
    ("device_action_targets", "target"),
    ("device_action_plugins", "plugin_name"),
    # ── 控制台删号·第一期地基（2026-09-24，派单「删号第一批」）：users 回收站两列 ──
    # 这两列【只由迁移 f6a7b8c9d0e1 add_column 引入】。老库（有表但无版本号）缺列时若不判
    # 「落后」，会被 stamp 到 head 却永久缺列——标记删除直接 UPDATE 不存在的列（500），
    # 而 GET /server/accounts 读 deleted_at 也会报错，删号功能整体不可用。
    ("users", "deleted_at"),
    ("users", "purge_after"),
    # ── 控制台删号·第二期第一批（2026-09-24，派单「物理清除器」）：account_purge_jobs 进度账本 ──
    # 该表【有 ORM 模型】（app/models/user/__init__.py::AccountPurgeJob，2026-09-24 Codex 复核补）：
    # 哨兵表必须在 metadata 里，否则「主 ORM 建齐、只缺插件表」的库会被判落后（T5 同口径，见
    # tests/test_migrate_schema_detect.py::test_manual_sentinel_ignores_plugin_tables）；
    # 老库补齐由迁移 f7b8c9d0e1f2 的 create_table 负责。
    # 老库（有表无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表：清除器无法
    # 记账，表现为「删一半进程被重启后无从续跑」——正是这张表要防的那件事。
    ("account_purge_jobs", "user_id"),
    # ── A4 批 5 / T6 M2 渠道归因（2026-09-27）：llm_usage.channel ──
    # 该列【只由迁移 b4c5d6e7f8a9 add_column 引入】（init_db 幂等层不补，同 config_id /
    # group_owner_id 两列的先例）。老库（有表但无版本号）缺此列时若不判「落后」，会被 stamp
    # 到 head 却永久缺列——_record_usage_async 落库带 channel 直接报错（记账整条链失败），
    # 且读端 select(LlmUsage) 也会炸。
    ("llm_usage", "channel"),
    # ── S2 M0 上下文预算档位（2026-09-27）：users.context_budget_tier ──
    # 该列【只由迁移 c6d7e8f9a0b1 add_column 引入】（init_db 幂等层不补，同 deleted_at /
    # purge_after / llm_usage.channel 的先例）。老库缺此列时若不判「落后」，会被 stamp 到 head
    # 却永久缺列——档位读写（PUT /system/context-budget/tier）与 GET /system/context-budget
    # 直接报错，且 select(User) 也会炸。
    ("users", "context_budget_tier"),
    # ── A4 批3 T1 M1a 关系驱力水位（2026-09-27）：relational_drives 表 ──
    # 该表【只由迁移 a1c4e7f9b2d5 create_table 引入】（表级哨兵，同 plugin_consents /
    # user_runtime_flags 的先例）。老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp
    # 到 head 却永久缺表——将来 M1b 的 settle/释放读写直接 select 报错，水位整条链静默失效。
    ("relational_drives", "level"),
    # ── A4 批4 T2 M1 念头池（2026-09-30）：thought_pool 表 ──
    # 该表【只由迁移 b2d4f6a8c0e1 create_table 引入】（表级哨兵，同 relational_drives /
    # device_action_targets 的先例）。老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp
    # 到 head 却永久缺表——影子供给（thought_pool_service）一旦拨开 flag 就直接 INSERT 报错，
    # 且「取一条可用的」热路径 SELECT 也炸，念头池整条链静默失效。
    ("thought_pool", "salt"),
    # ── A4 批 8 块 D 费用面板 M1（2026-09-30）：llm_usage 两条复合索引（索引用 idx: 前缀）──
    # 由迁移 c3d5e7f9a1b2 create_index 引入（模型侧同步声明在 models/agent/__init__.py
    # LlmUsage.__table_args__）。索引迁移不新增列也不建表，普通哨兵看不见它——老库若不判
    # 「落后」会被 stamp 到 head 却永久缺索引，面板按（账号 × 窗口）过滤仍走全表扫，而
    # schema 检查看起来是「当前」的。create_all 直建路径按 __table_args__ 会建出这两条，
    # 故全新库判「当前」不受影响。
    ("llm_usage", _INDEX_SENTINEL_PREFIX + "ix_llm_usage_user_created"),
    ("llm_usage", _INDEX_SENTINEL_PREFIX + "ix_llm_usage_task_created"),
]


def _migration_chain_tables(cfg: Config) -> set[str]:
    """遍历版本链全部迁移脚本，正则收集 ``create_table('x'`` 的表名（含插件/渠道等非 ORM 表）。

    §6.3（2026-09-09）：把「当前 schema」判别从人工列哨兵升级为自动比对——版本链建过的表
    必须全部存在；缺任一即判落后走 upgrade head（迁移自带 has_table 守卫，幂等补齐）。
    避免未来新增「只在某迁移 create_table、不在主 ORM」的表时，忘加哨兵列导致新库永久缺表。
    """
    import re

    script = ScriptDirectory.from_config(cfg)
    names: set[str] = set()
    for rev in script.walk_revisions():
        # rev.path 一般为相对 script_location；拼绝对路径读取，读不到则跳过（保守）。
        path = Path(rev.path) if Path(rev.path).is_absolute() else (_ALEMBIC_DIR / rev.path)
        if not path.exists():
            continue
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        names.update(re.findall(r"create_table\(\s*[\'\"]([a-zA-Z0-9_]+)[\'\"]", txt))
    return names


def _schema_is_current_auto(sync_url: str, cfg: Config) -> bool:
    """自动比对「版本链建过的全部表」vs 实际库表：need ⊆ have 才算当前 schema。

    与 _schema_is_current（人工 31 列哨兵）取「与」使用——两判都过才 stamp，任一存疑
    （表缺失/版本链不可读）都走 upgrade head，保守且幂等。
    """
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        insp = sa_inspect(engine)
        have = set(insp.get_table_names())
        need = _migration_chain_tables(cfg)
        if not need:
            return False  # 版本链为空/不可读 → 保守判非当前，走 upgrade 补齐
        return need.issubset(have)
    finally:
        engine.dispose()


def _alembic_config() -> Config:
    """构造 Alembic Config，脚本位置指向 backend/alembic。"""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("prepend_sys_path", str(_BACKEND_ROOT))
    return cfg


def _sync_url() -> str:
    """把异步 DB URL 转为 Alembic 同步引擎 URL。"""
    url = settings.database_url
    if url.startswith("sqlite+aiosqlite"):
        return url.replace("sqlite+aiosqlite", "sqlite", 1)
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql", 1)
    return url


def _current_rev(sync_url: str) -> str | None:
    """读取当前库的 alembic_version；无版本表则返回 None。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        if not sa_inspect(engine).has_table("alembic_version"):
            return None
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
            return row[0] if row else None
    finally:
        engine.dispose()


def _has_any_table(sync_url: str) -> bool:
    """判断库中是否存在任何用户表（排除 alembic_version 自身）。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        tables = sa_inspect(engine).get_table_names()
        return any(t != "alembic_version" for t in tables)
    finally:
        engine.dispose()


def _schema_is_current(sync_url: str) -> bool:
    """判别库是否已是当前 schema（链上新增的哨兵表/列/索引全部存在）。

    仅当「每一张相关表都存在且含对应列（idx: 前缀则含对应索引）」才返回 True；任一缺失 → False
    （判为落后库）。
    保守取向：宁可判为「落后」去 upgrade head（正确且幂等），不误判为「当前」去 stamp。
    """
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        insp = sa_inspect(engine)
        for table, probe in _CURRENT_SCHEMA_SENTINELS:
            try:
                if not insp.has_table(table):
                    return False
                if probe.startswith(_INDEX_SENTINEL_PREFIX):
                    names = {i["name"] for i in insp.get_indexes(table)}
                    if probe[len(_INDEX_SENTINEL_PREFIX):] not in names:
                        return False
                else:
                    names = {c["name"] for c in insp.get_columns(table)}
                    if probe not in names:
                        return False
            except Exception:
                # 表被锁 / 无法反射 → 安全取向：判为未确认当前 → 走 upgrade（保守）。
                return False
        return True
    finally:
        engine.dispose()


@contextmanager
def _alembic_logging_guard():
    """让 alembic 的 ``fileConfig`` 副作用不污染应用日志。

    env.py 会按 alembic.ini 调 ``fileConfig``：它会把 root logger 的 handlers 换成
    「console」、level 压到 WARN，并在默认 ``disable_existing_loggers=True`` 下把已存在
    的应用 logger 全部置 ``disabled``。结果是「本次启动若真的跑了迁移，之后 app.log
    一条不写」——服务进程活着、端口在听、却全无日志（2026-09-25 实测事故：日志停在
    ``Database initialized`` 之后的 Alembic 对齐那一步）。

    这里在进入前对日志设施做快照，退出时逐项还原；无论成败都在 ``finally`` 还原。
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    saved: dict[str, tuple[bool, int, bool]] = {}
    for name, obj in list(logging.root.manager.loggerDict.items()):
        if isinstance(obj, logging.Logger):
            saved[name] = (obj.disabled, obj.level, obj.propagate)
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_root_level)
        for name, (disabled, level, propagate) in saved.items():
            obj = logging.root.manager.loggerDict.get(name)
            if isinstance(obj, logging.Logger):
                obj.disabled = disabled
                obj.level = level
                obj.propagate = propagate
        # 快照里**没有**的 alembic* logger 是 fileConfig 期间新建的（env.py 加载时才产生）：
        # 还原逻辑管不到它们，会在进程里留下一份没人管理的 handler/logger，属未收尾的全局污染。
        for name in [n for n in logging.root.manager.loggerDict
                     if (n == "alembic" or n.startswith("alembic.")) and n not in saved]:
            obj = logging.root.manager.loggerDict.pop(name, None)
            if not isinstance(obj, logging.Logger):
                continue
            for handler in obj.handlers:
                try:
                    handler.close()
                except Exception:
                    pass
            obj.handlers.clear()


def _ai_characters_state(sync_url: str) -> dict:
    """只读读取 ai_characters 是否存在及 DDL 是否含 AUTOINCREMENT。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'"
            )).fetchone()
            if row is None:
                return {"exists": False, "autoincrement": False}
            return {"exists": True, "autoincrement": "AUTOINCREMENT" in (row[0] or "").upper()}
    finally:
        engine.dispose()


def _previous_head(script: ScriptDirectory, head: str) -> str | None:
    """返回当前 head 的直接父修订（单链）；多父/无父时返回 None。"""
    rev = script.get_revision(head)
    down = getattr(rev, "down_revision", None)
    if isinstance(down, (tuple, list)):
        return down[0] if len(down) == 1 else None
    return down


def _constraint_base(script: ScriptDirectory) -> str:
    """约束引入 revision 的父修订；启动对齐必须从这里起跑，而不是最新 head 的父修订。"""
    try:
        rev = script.get_revision(CONSTRAINT_REVISION)
    except Exception as exc:
        raise RuntimeError(
            f"B2b: constraint revision {CONSTRAINT_REVISION} missing from migration chain"
        ) from exc
    down = getattr(rev, "down_revision", None)
    if isinstance(down, (tuple, list)):
        base = down[0] if len(down) == 1 else None
    else:
        base = down
    if base is None:
        raise RuntimeError(f"B2b: constraint revision {CONSTRAINT_REVISION} has no parent")
    return base


def _is_ancestor(script: ScriptDirectory, ancestor: str, head: str) -> bool:
    cur = head
    seen: set[str] = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        if cur == ancestor:
            return True
        rev = script.get_revision(cur)
        down = getattr(rev, "down_revision", None)
        cur = down[0] if isinstance(down, (tuple, list)) and len(down) == 1 else (
            None if isinstance(down, (tuple, list)) else down
        )
    return False


def _repair_sequence_if_needed(sync_url: str) -> dict | None:
    """表已 AUTOINCREMENT 时显式修正序列；表不存在返回 None，非 AUTOINCREMENT 抛错。"""
    state = _ai_characters_state(sync_url)
    if not state["exists"]:
        return None
    if not state["autoincrement"]:
        raise RuntimeError(
            "B2b: ai_characters still lacks AUTOINCREMENT after startup alignment; refusing"
        )
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            return repair_ai_character_sequence(conn)
    finally:
        engine.dispose()


def _stamp_then_upgrade(cfg: Config, script: ScriptDirectory, head: str, *,
                        purge: bool = False) -> str:
    """stamp 到约束引入 revision 的父修订，再 upgrade 到实际 head。

    不能 stamp 到“最新 head 的父修订”：若未来新增后继，那会跳过约束重建。
    """
    if not _is_ancestor(script, CONSTRAINT_REVISION, head):
        raise RuntimeError(
            f"B2b: constraint revision {CONSTRAINT_REVISION} is not an ancestor of head {head}"
        )
    base = _constraint_base(script)
    command.stamp(cfg, base, purge=purge)
    command.upgrade(cfg, head)
    return base


def _seq_suffix(repaired: dict | None) -> str:
    if repaired and repaired.get("changed"):
        return f"+sequence_repaired:{repaired['before']}->{repaired['after']}"
    return ""


def _install_client_guard(sync_url: str) -> dict:
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            return ensure_client_safe_id_guard(conn)
    finally:
        engine.dispose()


def _guard_suffix(info: dict | None) -> str:
    mode = (info or {}).get("mode")
    return f"+guard:{mode}" if mode in ("check", "triggers", "check+triggers") else ""


def _finalize(sync_url: str, action: str, repaired: dict | None) -> str:
    """所有对齐路径统一：先修正序列，再安装/核验创建守卫，返回动作描述。"""
    return action + _seq_suffix(repaired) + _guard_suffix(_install_client_guard(sync_url))


def _ensure_alembic_revision_sync() -> str:
    """同步执行对齐，返回动作描述（stamped / re-stamped / upgraded / already_at_head）。

    外层套 ``_alembic_logging_guard``：alembic 的 env.py 会 fileConfig（按 alembic.ini
    重配 root 日志、并禁用已存在的 logger），跑完必须把应用日志原样还原，否则「启动时
    跑过迁移」会让后端日志整体静默（2026-09-25 实测事故）。
    """
    with _alembic_logging_guard():
        return _ensure_alembic_revision_sync_inner()


def _ensure_alembic_revision_sync_inner() -> str:
    cfg = _alembic_config()

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    sync_url = _sync_url()
    cur = _current_rev(sync_url)
    state = _ai_characters_state(sync_url)

    if cur == head:
        # B2b：版本号到 head 不等于约束已生效；已有 AUTOINCREMENT 才做显式序列修正，
        # 否则拒绝，不能靠版本号早退绕过重建。
        if not state["exists"]:
            raise RuntimeError("B2b: alembic_version at head but ai_characters table missing")
        if not state["autoincrement"]:
            raise RuntimeError(
                "B2b: alembic_version at head but ai_characters lacks AUTOINCREMENT; "
                "refusing to bypass rebuild (restore backup or re-stamp to previous head)"
            )
        repaired = _repair_sequence_if_needed(sync_url)
        return _finalize(sync_url, "already_at_head", repaired)

    # 判断当前版本号是否在本版本链中（已知 revision）。存量库可能带一个历史遗留/被移除的
    # alembic 试验版本号（例如本机真实库的 78d3405d8b58），该版本不在本链中 → 当作 orphaned。
    known = None
    if cur is not None:
        try:
            known = script.revision_map.get_revision(cur)
        except Exception:
            known = None

    if cur is None:
        # 无 alembic_version：细分三类。
        if not _has_any_table(sync_url):
            # 全新空库：无任何表 → 仅标记为已迁移到 head（不重放）。
            command.stamp(cfg, head)
            return _finalize(sync_url, f"stamped:{head}", None)
        if _schema_is_current(sync_url) and _schema_is_current_auto(sync_url, cfg):
            # 有表、无版本，且 schema 已是当前（init_db/create_all 已建到当前模型）。
            if state["exists"] and not state["autoincrement"]:
                # 旧表列齐全但没有 AUTOINCREMENT：stamp 到约束 revision 的父修订再 upgrade，
                # 即使未来有后继也不会跳过候选重建；不能直接 stamp 新 head。
                prev = _stamp_then_upgrade(cfg, script, head)
                action = f"upgraded:stamped-{prev}->{head}"
            else:
                command.stamp(cfg, head)
                action = f"stamped:{head}"
            repaired = _repair_sequence_if_needed(sync_url)
            return _finalize(sync_url, action, repaired)
        # 非空老库：schema 落后（缺链上新增列）→ 整链重放补齐（守卫幂等，安全）。
        command.upgrade(cfg, head)
        repaired = _repair_sequence_if_needed(sync_url)
        return _finalize(sync_url, f"upgraded:->{head}", repaired)

    if known is None:
        # 存量库带一个本版本链之外的版本号（历史遗留/被移除的 alembic 试验）。
        # B2b：只有在 schema 已确认是当前、且 AUTOINCREMENT 状态可判别时才允许清旧历史；
        # 否则拒绝强盖版本，交人工恢复。
        if not (_schema_is_current(sync_url) and _schema_is_current_auto(sync_url, cfg)):
            raise RuntimeError(
                f"B2b: orphan alembic revision {cur} with unconfirmed schema; refusing to stamp"
            )
        if state["exists"] and not state["autoincrement"]:
            prev = _stamp_then_upgrade(cfg, script, head, purge=True)
            action = f"upgraded:re-stamped-{prev}->{head}"
        else:
            command.stamp(cfg, head, purge=True)
            action = f"re-stamped:{cur}->{head}"
        repaired = _repair_sequence_if_needed(sync_url)
        return _finalize(sync_url, action, repaired)

    # 已知旧版本，且落后于 head → 执行升级（应用后续 schema 变更）。
    command.upgrade(cfg, head)
    repaired = _repair_sequence_if_needed(sync_url)
    return _finalize(sync_url, f"upgraded:{cur}->{head}", repaired)


async def ensure_alembic_revision() -> str:
    """启动时对齐数据库版本链（在线程池中执行，不阻塞事件循环）。

    幂等：库已在 head 时为 no-op；无版本时按「空库/当前库/落后老库」分流 stamp 或 upgrade。
    返回动作描述字符串，调用方（main.py lifespan）可记录日志。
    """
    return await asyncio.to_thread(_ensure_alembic_revision_sync)


def is_migration_available() -> bool:
    """判断 Alembic 是否就绪（alembic.ini 与脚本目录存在）。供 main.py 防御性判断。"""
    return _ALEMBIC_INI.exists() and (_ALEMBIC_DIR / "env.py").exists()
