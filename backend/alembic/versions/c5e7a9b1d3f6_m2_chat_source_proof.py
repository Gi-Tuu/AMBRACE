from alembic import op
import sqlalchemy as sa

revision = "c5e7a9b1d3f6"
down_revision = "b3d9e1f5a7c2"
branch_labels = None
depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("chat_messages")}
    if "source_event_uid" in cols:
        col = next(c for c in insp.get_columns("chat_messages")
                   if c["name"] == "source_event_uid")
        if str(col["type"]).split("(")[0].upper() != "VARCHAR" or not col["nullable"]:
            raise RuntimeError("corrupt chat_messages.source_event_uid; refusing to stamp")
    else:
        op.add_column("chat_messages", sa.Column("source_event_uid", sa.String(64), nullable=True))
    indexes = {i["name"]: i for i in insp.get_indexes("chat_messages")}
    if "idx_chat_source_event_uid" in indexes:
        if indexes["idx_chat_source_event_uid"]["column_names"] != ["source_event_uid"]:
            raise RuntimeError("corrupt idx_chat_source_event_uid; refusing to stamp")
    else:
        op.create_index("idx_chat_source_event_uid", "chat_messages", ["source_event_uid"])


def downgrade():
    insp = sa.inspect(op.get_bind())
    if "idx_chat_source_event_uid" in {i["name"] for i in insp.get_indexes("chat_messages")}:
        op.drop_index("idx_chat_source_event_uid", table_name="chat_messages")
    if "source_event_uid" in {c["name"] for c in insp.get_columns("chat_messages")}:
        op.drop_column("chat_messages", "source_event_uid")
