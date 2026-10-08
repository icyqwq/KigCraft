"""Owner-scoped stable image safety assessments, separate from request authorization."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0020"
down_revision = "v2_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_asset_review_cache",
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("policy_key", sa.String(64), primary_key=True),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("source_operation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_operation_id", "owner_id"],
            ["v2_operations.id", "v2_operations.owner_id"], name="fk_v2_review_cache_source_owner"),
        sa.CheckConstraint("verdict IN ('allow','reject')", name="ck_v2_review_cache_verdict"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$' AND policy_key ~ '^[0-9a-f]{64}$'", name="ck_v2_review_cache_hashes"))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_asset_review_cache)")).scalar():
        raise RuntimeError("Refuse to discard asset review evidence")
    op.drop_table("v2_asset_review_cache")
