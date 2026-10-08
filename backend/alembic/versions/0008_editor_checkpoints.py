"""Immutable editor document checkpoints, independent of visible image revisions."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0008"
down_revision = "v2_0007"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("v2_editor_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("design_id", sa.String(36), nullable=False),
        sa.Column("branch_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("root_asset_id", sa.String(36), nullable=False),
        sa.Column("clean_asset_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("document_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["design_id", "owner_id"], ["v2_designs.id", "v2_designs.owner_id"], name="fk_v2_editor_checkpoint_owner"),
        sa.ForeignKeyConstraint(["branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"], name="fk_v2_editor_checkpoint_branch"),
        sa.ForeignKeyConstraint(["revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"], name="fk_v2_editor_checkpoint_revision"),
        sa.ForeignKeyConstraint(["root_asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"], name="fk_v2_editor_checkpoint_root"),
        sa.ForeignKeyConstraint(["clean_asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"], name="fk_v2_editor_checkpoint_clean"),
        sa.UniqueConstraint("branch_id", "revision_id", "version", name="uq_v2_editor_checkpoint_version"),
        sa.UniqueConstraint("id", "design_id", name="uq_v2_editor_checkpoint_design"),
        sa.CheckConstraint("version >= 1", name="ck_v2_editor_checkpoint_version"))
    op.create_table("v2_editor_save_receipts",
        sa.Column("design_id", sa.String(36), sa.ForeignKey("v2_designs.id"), primary_key=True),
        sa.Column("request_id", sa.String(100), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("checkpoint_id", sa.String(36), nullable=False),
        sa.Column("disposition", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["checkpoint_id", "design_id"], ["v2_editor_checkpoints.id", "v2_editor_checkpoints.design_id"], name="fk_v2_editor_receipt_checkpoint"),
        sa.CheckConstraint("disposition IN ('revision', 'checkpoint', 'unchanged')", name="ck_v2_editor_receipt_disposition"))
    for table in ("v2_editor_checkpoints", "v2_editor_save_receipts"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v2_reject_mutation()")

def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_editor_checkpoints LIMIT 1")).first():
        raise RuntimeError("Editor checkpoints exist; retain schema during rollback")
    op.drop_table("v2_editor_save_receipts")
    op.drop_table("v2_editor_checkpoints")
