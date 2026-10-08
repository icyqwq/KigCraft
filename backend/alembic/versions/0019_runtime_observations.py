"""Private execution observations; never a user quota ledger."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0019"
down_revision = "v2_0018"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("v2_runtime_observations",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("operation_id",sa.String(36),sa.ForeignKey("v2_operations.id"),nullable=False),
        sa.Column("phase",sa.String(16),nullable=False),
        sa.Column("fencing_token",sa.Integer(),nullable=False),
        sa.Column("model",sa.String(100),nullable=False),
        sa.Column("codex_version",sa.String(100),nullable=False),
        sa.Column("state",sa.String(24),nullable=False),
        sa.Column("thread_id",sa.String(100),nullable=False),
        sa.Column("turn_id",sa.String(100),nullable=True),
        sa.Column("token_status",sa.String(16),nullable=False),
        sa.Column("token_snapshot",sa.JSON(),nullable=True),
        sa.Column("image_items",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("operation_id","phase","fencing_token",name="uq_v2_runtime_observation_attempt"),
        sa.CheckConstraint("phase IN ('review','generation')",name="ck_v2_observation_phase"),
        sa.CheckConstraint("state IN ('dispatch_intent','started','completed','interrupted','failed')",name="ck_v2_observation_state"),
        sa.CheckConstraint("token_status IN ('unknown','observed','invalid')",name="ck_v2_observation_tokens"),
        sa.CheckConstraint("fencing_token >= 0",name="ck_v2_observation_fence"))
    op.create_index("ix_v2_observation_operation","v2_runtime_observations",["operation_id"])

def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_runtime_observations)")).scalar():
        raise RuntimeError("Refuse to discard runtime observations")
    op.drop_table("v2_runtime_observations")
