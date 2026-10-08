"""Durable per-account public submission frequency."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0018"
down_revision = "v2_0017"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("v2_submission_admissions",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("owner_id",sa.String(36),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("action",sa.String(20),nullable=False),
        sa.Column("client_request_id",sa.String(100),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("owner_id","action","client_request_id",name="uq_v2_submission_admission"),
        sa.CheckConstraint("action IN ('turn','viewset','viewset_retry')",name="ck_v2_submission_action"))
    op.create_index("ix_v2_submission_window","v2_submission_admissions",["owner_id","created_at"])

def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_submission_admissions)")).scalar():
        raise RuntimeError("Refuse to discard accepted submission history")
    op.drop_table("v2_submission_admissions")
