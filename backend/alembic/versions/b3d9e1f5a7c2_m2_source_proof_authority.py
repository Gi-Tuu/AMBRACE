"""M2 2a: source proof authority (candidate; not applied to real DB)."""
from alembic import op
import os
import re
import sqlalchemy as sa

revision = "b3d9e1f5a7c2"
down_revision = "d6f8a0b2c4e6"
branch_labels = None
depends_on = None




def _fault(point):
    if os.environ.get("M2_2A_FAULT") == point:
        raise RuntimeError("injected migration fault: " + point)


def _insp():
    return sa.inspect(op.get_bind())


def _ensure_table(name, create):
    if not _insp().has_table(name):
        create()


def _add_col(table, col):
    if col.name not in {c["name"] for c in _insp().get_columns(table)}:
        op.add_column(table, col)


def _has_source_fk(table):
    return any(fk["constrained_columns"] == ["source_event_uid"]
               and fk["referred_table"] == "memory_source_events"
               for fk in _insp().get_foreign_keys(table))


def _ensure_pinned_index():
    if _insp().has_table("memories"):
        op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_pinned_active
        ON memories (
            character_id,
            memory_type,
            CASE WHEN sub_type IS NULL OR sub_type = 'summary' THEN '' ELSE sub_type END
        )
        WHERE is_pinned = 1 AND is_archived = 0
        """
        )


def _ensure_index(name, table, cols, **kw):
    if name not in {i["name"] for i in _insp().get_indexes(table)}:
        op.create_index(name, table, cols, **kw)



_REQUIRED_COLUMNS = {
    "source_seq": {"id", "seq"},
    "memory_source_events": {"event_uid", "kind", "source_row_id", "generation",
                             "seq", "owner_user_id", "character_id", "event_at",
                             "content_sha256"},
    "memory_tombstones": {"memory_id", "owner_user_id", "character_id", "epoch",
                          "revoked_seq", "confirm_seq", "state"},
    "memory_reallow_grants": {"token_hash", "memory_id", "owner_user_id",
                              "character_id", "epoch", "confirm_seq", "used",
                              "invalidated"},
    "memory_restore_quarantine": {"event_uid", "reason"},
}
_REQUIRED_TYPES = {
    "source_seq": {"id": "INTEGER", "seq": "INTEGER"},
    "memory_source_events": {"event_uid": "VARCHAR", "kind": "VARCHAR",
                             "source_row_id": "INTEGER", "generation": "INTEGER",
                             "seq": "INTEGER", "owner_user_id": "INTEGER",
                             "character_id": "INTEGER", "event_at": "DATETIME",
                             "content_sha256": "VARCHAR"},
    "memory_tombstones": {"memory_id": "INTEGER", "owner_user_id": "INTEGER",
                          "character_id": "INTEGER", "epoch": "INTEGER",
                          "revoked_seq": "INTEGER", "confirm_seq": "INTEGER",
                          "state": "VARCHAR"},
    "memory_reallow_grants": {"token_hash": "VARCHAR", "memory_id": "INTEGER",
                              "owner_user_id": "INTEGER", "character_id": "INTEGER",
                              "epoch": "INTEGER", "confirm_seq": "INTEGER",
                              "used": "BOOLEAN", "invalidated": "BOOLEAN"},
    "memory_restore_quarantine": {"event_uid": "VARCHAR", "reason": "VARCHAR"},
}
_REQUIRED_CHECKS = {
    "source_seq": {"ck_source_seq_single_row"},
    "memory_source_events": {"ck_mse_row_positive", "ck_mse_generation_positive",
                             "ck_mse_seq_positive", "ck_mse_owner_positive",
                             "ck_mse_char_positive"},
}


_CHECK_SQL = {
    "source_seq": {"ck_source_seq_single_row": "id = 1"},
    "memory_source_events": {
        "ck_mse_row_positive": "source_row_id > 0",
        "ck_mse_generation_positive": "generation > 0",
        "ck_mse_seq_positive": "seq > 0",
        "ck_mse_owner_positive": "owner_user_id > 0",
        "ck_mse_char_positive": "character_id > 0"},
}
_FULL_COLUMNS = {
    "source_seq": {"id", "seq"},
    "memory_source_events": set(_REQUIRED_COLUMNS["memory_source_events"]) |
        {"session_id", "deleted", "created_at"},
    "memory_tombstones": set(_REQUIRED_COLUMNS["memory_tombstones"]) | {"created_at"},
    "memory_reallow_grants": set(_REQUIRED_COLUMNS["memory_reallow_grants"]) | {"created_at"},
    "memory_restore_quarantine": {"event_uid", "owner_user_id", "character_id", "reason", "restored_at"},
}
_NULLABLE = {"memory_source_events": {"session_id"},
             "memory_restore_quarantine": {"owner_user_id", "character_id"}}
_PRIMARY_KEYS = {"source_seq": ["id"], "memory_source_events": ["event_uid"],
                 "memory_tombstones": ["memory_id"], "memory_reallow_grants": ["token_hash"],
                 "memory_restore_quarantine": ["event_uid"]}
_INDEX_COLUMNS = {
    "memory_source_events": {"idx_mse_owner_char_time": ["owner_user_id", "character_id", "event_at"],
                             "idx_mse_session": ["session_id"]},
    "memory_tombstones": {"idx_mt_owner_char": ["owner_user_id", "character_id"]},
    "memory_reallow_grants": {"idx_mrg_memory": ["memory_id"]},
}


def _sql_shape(value):
    return re.sub(r'[\s()"`\[\]]', '', str('' if value is None else value)).lower()


def _validate_existing_schema():
    """已存在新表必须列/命名 CHECK 齐全；损坏形态拒绝，不静默 stamp。"""
    insp = _insp()
    for table, required in _FULL_COLUMNS.items():
        if not insp.has_table(table):
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        missing = required - cols
        if missing:
            raise RuntimeError(f"incomplete M2 table {table}: missing {sorted(missing)}")
        details = {c["name"]: c for c in insp.get_columns(table)}
        for name in required:
            if bool(details[name]["nullable"]) != (name in _NULLABLE.get(table, set())):
                raise RuntimeError(f"invalid M2 nullable: {table}.{name}")
        if insp.get_pk_constraint(table).get("constrained_columns") != _PRIMARY_KEYS[table]:
            raise RuntimeError(f"invalid M2 primary key: {table}")
        actual_checks = {c.get("name"): c.get("sqltext") for c in insp.get_check_constraints(table)}
        for name, sql in _CHECK_SQL.get(table, {}).items():
            if _sql_shape(actual_checks.get(name)) != _sql_shape(sql):
                raise RuntimeError(f"invalid M2 CHECK definition: {table}.{name}")
        actual_indexes = {i["name"]: i for i in insp.get_indexes(table)}
        for name, columns in _INDEX_COLUMNS.get(table, {}).items():
            item = actual_indexes.get(name)
            if item and (item["column_names"] != columns or item["unique"]):
                raise RuntimeError(f"invalid M2 index definition: {table}.{name}")
        types = {c["name"]: str(c["type"]).split("(")[0].upper()
                 for c in insp.get_columns(table)}
        for col, want in _REQUIRED_TYPES.get(table, {}).items():
            if types[col] != want:
                raise RuntimeError(f"incomplete M2 table {table}: {col} type {types[col]} != {want}")
        checks = {c.get("name") for c in insp.get_check_constraints(table)}
        missing_checks = _REQUIRED_CHECKS.get(table, set()) - checks
        if missing_checks:
            raise RuntimeError(f"incomplete M2 table {table}: missing checks {sorted(missing_checks)}")
        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints(table)}
        uniques |= {tuple(i["column_names"]) for i in insp.get_indexes(table) if i["unique"]}
        required_unique = {
            "memory_source_events": {("kind", "source_row_id", "generation"),
                                     ("seq",)},
            "memory_reallow_grants": {("memory_id", "epoch")},
        }.get(table, set())
        missing_unique = required_unique - uniques
        if missing_unique:
            raise RuntimeError(f"incomplete M2 table {table}: missing unique {sorted(missing_unique)}")
        required_indexes = {
            "memory_source_events": {"idx_mse_owner_char_time", "idx_mse_session"},
            "memory_tombstones": {"idx_mt_owner_char"},
            "memory_reallow_grants": {"idx_mrg_memory"},
        }.get(table, set())
        indexes = {i["name"] for i in insp.get_indexes(table)}
        missing_idx = required_indexes - indexes
        if missing_idx:
            raise RuntimeError(f"incomplete M2 table {table}: missing indexes {sorted(missing_idx)}")
    if insp.has_table("memory_write_receipts"):
        for item in insp.get_indexes("memory_write_receipts"):
            if item["name"] == "uq_mwr_idem_authoritative":
                predicate = item.get("dialect_options", {}).get("sqlite_where")
                if not item["unique"] or item["column_names"] != ["idempotency_key"] or \
                        _sql_shape(predicate) != _sql_shape("authoritative = 1 AND idempotency_key IS NOT NULL"):
                    raise RuntimeError("invalid authoritative receipt index definition")
    for table in ("memories", "memory_write_receipts"):
        if not insp.has_table(table):
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "source_event_uid" in cols and not _has_source_fk(table):
            raise RuntimeError(f"partial {table}.source_event_uid without source FK")

def upgrade():
    _validate_existing_schema()
    if not _insp().has_table("source_seq"):
        op.create_table(
            "source_seq",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_source_seq_single_row"),
    )
    op.execute("INSERT OR IGNORE INTO source_seq (id, seq) VALUES (1, 0)")

    if not _insp().has_table("memory_source_events"):
        op.create_table(
            "memory_source_events",
        sa.Column("event_uid", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_row_id", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, unique=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("event_at", sa.DateTime(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("kind", "source_row_id", "generation",
                            name="uq_mse_source_generation"),
        sa.CheckConstraint("source_row_id > 0", name="ck_mse_row_positive"),
        sa.CheckConstraint("generation > 0", name="ck_mse_generation_positive"),
        sa.CheckConstraint("seq > 0", name="ck_mse_seq_positive"),
        sa.CheckConstraint("owner_user_id > 0", name="ck_mse_owner_positive"),
        sa.CheckConstraint("character_id > 0", name="ck_mse_char_positive"),
    )
    _ensure_index("idx_mse_owner_char_time", "memory_source_events",
                   ["owner_user_id", "character_id", "event_at"])
    _ensure_index("idx_mse_session", "memory_source_events", ["session_id"])

    if not _insp().has_table("memory_tombstones"):
        op.create_table(
            "memory_tombstones",
        sa.Column("memory_id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("revoked_seq", sa.Integer(), nullable=False),
        sa.Column("confirm_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(24), nullable=False, server_default="closed"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    _ensure_index("idx_mt_owner_char", "memory_tombstones",
                   ["owner_user_id", "character_id"])

    if not _insp().has_table("memory_reallow_grants"):
        op.create_table(
            "memory_reallow_grants",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("confirm_seq", sa.Integer(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("invalidated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("memory_id", "epoch", name="uq_mrg_memory_epoch"),
    )
    _ensure_index("idx_mrg_memory", "memory_reallow_grants", ["memory_id"])

    if not _insp().has_table("memory_restore_quarantine"):
        op.create_table(
            "memory_restore_quarantine",
        sa.Column("event_uid", sa.String(64), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("character_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("restored_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    _fault("new_tables")
    mem_cols = {c["name"] for c in _insp().get_columns("memories")}
    if "source_event_uid" in mem_cols and not _has_source_fk("memories"):
        raise RuntimeError("partial memories.source_event_uid without FK; refusing to stamp head")
    if "source_event_uid" not in mem_cols:
        with op.batch_alter_table("memories") as batch:
            batch.add_column(sa.Column("source_event_uid", sa.String(64),
                                       sa.ForeignKey("memory_source_events.event_uid",
                                                     name="fk_mem_source_event", ondelete="RESTRICT"), nullable=True))
    _ensure_pinned_index()
    _ensure_index("idx_mem_source_event", "memories", ["source_event_uid"])

    for name, col in (
        ("source_event_uid", sa.Column("source_event_uid", sa.String(64), nullable=True)),
        ("owner_user_id", sa.Column("owner_user_id", sa.Integer(), nullable=True)),
        ("actor_kind", sa.Column("actor_kind", sa.String(32), nullable=True)),
        ("actor_user_id", sa.Column("actor_user_id", sa.Integer(), nullable=True)),
        ("envelope_version", sa.Column("envelope_version", sa.Integer(), nullable=False, server_default="0")),
        ("authoritative", sa.Column("authoritative", sa.Boolean(), nullable=False, server_default="0")),
        ("idempotency_key", sa.Column("idempotency_key", sa.String(128), nullable=True)),
    ):
        if col.name == "source_event_uid" and col.name in {c["name"] for c in _insp().get_columns("memory_write_receipts")} and not _has_source_fk("memory_write_receipts"):
            raise RuntimeError("partial memory_write_receipts.source_event_uid without FK; refusing to stamp head")
        if col.name == "source_event_uid" and col.name not in {c["name"] for c in _insp().get_columns("memory_write_receipts")}:
            with op.batch_alter_table("memory_write_receipts") as batch:
                batch.add_column(sa.Column("source_event_uid", sa.String(64),
                                           sa.ForeignKey("memory_source_events.event_uid",
                                                          name="fk_mwr_source_event"),
                                           nullable=True))
        else:
            _add_col("memory_write_receipts", col)
    _ensure_index("idx_mwr_source_event", "memory_write_receipts", ["source_event_uid"])
    _fault("columns")
    _ensure_index(
        "uq_mwr_idem_authoritative", "memory_write_receipts", ["idempotency_key"],
        unique=True,
        sqlite_where=sa.text("authoritative = 1 AND idempotency_key IS NOT NULL"),
    )


    _fault("indexes")


def downgrade():
    op.drop_index("uq_mwr_idem_authoritative", table_name="memory_write_receipts")
    op.drop_index("idx_mwr_source_event", table_name="memory_write_receipts")
    with op.batch_alter_table("memory_write_receipts") as batch:
        for col in ("idempotency_key", "authoritative", "envelope_version",
                    "actor_user_id", "actor_kind", "owner_user_id", "source_event_uid"):
            batch.drop_column(col)
    op.drop_index("idx_mem_source_event", table_name="memories")
    with op.batch_alter_table("memories") as batch:
        batch.drop_column("source_event_uid")
    _ensure_pinned_index()
    op.drop_table("memory_restore_quarantine")
    op.drop_index("idx_mrg_memory", table_name="memory_reallow_grants")
    op.drop_table("memory_reallow_grants")
    op.drop_index("idx_mt_owner_char", table_name="memory_tombstones")
    op.drop_table("memory_tombstones")
    op.drop_index("idx_mse_session", table_name="memory_source_events")
    op.drop_index("idx_mse_owner_char_time", table_name="memory_source_events")
    op.drop_table("memory_source_events")
    op.drop_table("source_seq")
