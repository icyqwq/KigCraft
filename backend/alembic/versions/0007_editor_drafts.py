"""Conditional, account-bound editor drafts and retry receipts."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0007"
down_revision = "v2_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_editor_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("design_id", sa.String(36), nullable=False),
        sa.Column("branch_id", sa.String(36), nullable=False),
        sa.Column("base_revision_id", sa.String(36), nullable=False),
        sa.Column("root_asset_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("device_id", sa.String(100), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["design_id", "owner_id"], ["v2_designs.id", "v2_designs.owner_id"], name="fk_v2_draft_design_owner"),
        sa.ForeignKeyConstraint(["branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"], name="fk_v2_draft_branch"),
        sa.ForeignKeyConstraint(["base_revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"], name="fk_v2_draft_base"),
        sa.ForeignKeyConstraint(["root_asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"], name="fk_v2_draft_root_owner"),
        sa.CheckConstraint("version >= 1", name="ck_v2_draft_version"))
    op.create_index("ix_v2_draft_scope", "v2_editor_drafts", ["owner_id", "branch_id", "base_revision_id"])
    op.create_table("v2_draft_write_receipts",
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("v2_editor_drafts.id"), primary_key=True),
        sa.Column("request_id", sa.String(100), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_editor_drafts LIMIT 1")).first():
        raise RuntimeError("Editor drafts exist; retain schema during rollback")
    op.drop_table("v2_draft_write_receipts")
    op.drop_table("v2_editor_drafts")
