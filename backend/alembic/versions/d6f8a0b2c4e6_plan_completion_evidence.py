"""C9 stable business items and persistent completion evidence (candidate only)."""
from alembic import op
import sqlalchemy as sa

revision = "d6f8a0b2c4e6"
down_revision = "c4e6f8a0b2d4"
branch_labels = None
depends_on = None


def _common():
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL")),
        sa.Column("source_event_at", sa.DateTime(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("target_text", sa.Text(), nullable=False),
    ]


def _ensure_table(name, extra):
    columns = _common() + extra
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(name):
        op.create_table(name, *columns, sqlite_autoincrement=True)
    else:
        # Historical bootstrap may create latest metadata before migration replay.
        actual = {c["name"]: c for c in inspector.get_columns(name)}
        expected = {c.name: c for c in columns}
        if actual.keys() != expected.keys():
            raise RuntimeError(f"Incomplete C9 ledger schema: {name}")
        for key, column in expected.items():
            if str(actual[key]["type"]) != str(column.type) or actual[key]["nullable"] != column.nullable:
                raise RuntimeError(f"Incompatible C9 ledger column: {name}.{key}")
            if column.server_default is not None:
                expected_default = str(column.server_default.arg).strip("'\"")
                actual_default = str(actual[key]["default"]).strip("'\"")
                if actual_default != expected_default:
                    raise RuntimeError(f"Invalid C9 default: {name}.{key}")
        if inspector.get_pk_constraint(name)["constrained_columns"] != ["id"]:
            raise RuntimeError(f"Invalid C9 identity: {name}")
        unique = {tuple(u["column_names"]) for u in inspector.get_unique_constraints(name)}
        for column in columns:
            if column.unique and (column.name,) not in unique:
                raise RuntimeError(f"Missing C9 uniqueness: {name}")
        fks = {(tuple(f["constrained_columns"]), f["referred_table"],
                tuple(f["referred_columns"]), f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys(name)}
        for column in columns:
            for fk in column.foreign_keys:
                table, col = fk.target_fullname.split(".")
                if ((column.name,), table, (col,), fk.ondelete) not in fks:
                    raise RuntimeError(f"Invalid C9 FK: {name}.{column.name}")
        if bind.dialect.name == "sqlite":
            ddl = bind.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).scalar_one()
            if "AUTOINCREMENT" not in ddl.upper():
                raise RuntimeError(f"Reusable C9 identity forbidden: {name}")
    index_name = "idx_business_plan_scope" if name == "business_plan_items" else "idx_plan_evidence_scope"
    indexes = {i["name"]: i for i in sa.inspect(bind).get_indexes(name)}
    scope = ["user_id", "character_id", "chat_session_id"]
    if index_name not in indexes:
        op.create_index(index_name, name, scope)
    elif indexes[index_name]["column_names"] != scope or indexes[index_name]["unique"]:
        raise RuntimeError(f"Invalid C9 index: {name}")


def upgrade():
    _ensure_table("business_plan_items", [
        sa.Column("item_key", sa.String(64), nullable=False, unique=True),
        sa.Column("cancelled", sa.Boolean(), server_default="0", nullable=False)])
    _ensure_table("plan_completion_evidence", [
        sa.Column("evidence_key", sa.String(64), nullable=False, unique=True),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("business_plan_items.id", ondelete="SET NULL")),
        sa.Column("binding_kind", sa.String(16), server_default="unresolved", nullable=False)])


def downgrade():
    # Dropping the ledger silently would revive obligations on queued replay.
    raise RuntimeError("Completion evidence is irreversible; restore the pre-migration backup")
