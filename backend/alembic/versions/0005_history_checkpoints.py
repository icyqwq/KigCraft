"""Persist exact context boundaries before dispatch and on immutable revisions."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0005"
down_revision = "v2_0004"
branch_labels = None
depends_on = None

def upgrade():
    # Existing rows remain unknown. Never infer an old checkpoint from today's active epoch.
    op.add_column("v2_operations", sa.Column("context_before", sa.JSON()))
    op.add_column("v2_revisions", sa.Column("context_checkpoint", sa.JSON()))

def downgrade():
    for table, column in (("v2_operations", "context_before"), ("v2_revisions", "context_checkpoint")):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL LIMIT 1")).first():
            raise RuntimeError("History checkpoints exist; retain schema during application rollback")
    op.drop_column("v2_revisions", "context_checkpoint")
    op.drop_column("v2_operations", "context_before")
