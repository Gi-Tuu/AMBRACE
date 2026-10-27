"""Add nullable evidence write keys without merging or modifying legacy memories."""
from alembic import op
import sqlalchemy as sa

revision = "c4e6f8a0b2d4"
down_revision = "b2a0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "write_key" not in {col["name"] for col in inspector.get_columns("memories")}:
        op.add_column("memories", sa.Column("write_key", sa.String(64), nullable=True))
    if "ix_memories_write_key" not in set(bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memories'")).scalars()):
        op.create_index("ix_memories_write_key", "memories", ["write_key"], unique=True)


def downgrade():
    # Leave the additive column/index intact: discarding replay keys would make a
    # queued retry create a second row after a rollback/re-upgrade.
    pass
