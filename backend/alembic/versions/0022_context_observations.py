"""Main-session context-open receipts, separate from turn usage and billing."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0022"
down_revision = "v2_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_context_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation_id", sa.String(36), sa.ForeignKey("v2_operations.id"), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("operation_id", "fencing_token", name="uq_v2_context_attempt"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_v2_context_fence"),
        sa.CheckConstraint("method IN ('start','resume','fork')", name="ck_v2_context_method"),
        sa.CheckConstraint("state IN ('sent','confirmed','unconfirmed')", name="ck_v2_context_state"),
        sa.CheckConstraint("(state = 'sent' AND completed_at IS NULL) OR (state <> 'sent' AND completed_at IS NOT NULL)",
                           name="ck_v2_context_completed"))
    op.create_index("ix_v2_context_operation", "v2_context_observations", ["operation_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_context_observations)")).scalar():
        raise RuntimeError("Refuse to discard context observations")
    op.drop_table("v2_context_observations")
