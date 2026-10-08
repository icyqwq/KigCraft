"""Persist runtime binding and recoverable image staging descriptors."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0003"
down_revision = "v2_0002"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("v2_codex_epochs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("v2_branches.id"), nullable=False),
        sa.Column("thread_id", sa.String(100), nullable=False),
        sa.Column("runtime_identity", sa.String(64), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("codex_version", sa.String(100), nullable=False),
        sa.Column("checkpoint_turn_id", sa.String(100)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("runtime_identity", "thread_id", name="uq_v2_epoch_thread"),
    )
    op.create_index("ix_v2_codex_epochs_branch_id", "v2_codex_epochs", ["branch_id"])
    op.add_column("v2_branches", sa.Column("active_epoch_id", sa.String(36)))
    op.create_foreign_key("fk_v2_branch_epoch", "v2_branches", "v2_codex_epochs", ["active_epoch_id"], ["id"])
    op.add_column("v2_operations", sa.Column("upstream_status", sa.String(24)))
    op.add_column("v2_operations", sa.Column("staged_outputs", sa.JSON()))
    op.add_column("v2_operations", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_codex_epochs LIMIT 1")).first():
        raise RuntimeError("Runtime bindings exist; retain them during application rollback")
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_operations WHERE staged_outputs IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Staged images exist; publish or reconcile before schema rollback")
    op.drop_column("v2_operations", "cancel_requested")
    op.drop_column("v2_operations", "staged_outputs")
    op.drop_column("v2_operations", "upstream_status")
    op.drop_constraint("fk_v2_branch_epoch", "v2_branches", type_="foreignkey")
    op.drop_column("v2_branches", "active_epoch_id")
    op.drop_table("v2_codex_epochs")
