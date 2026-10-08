"""Preserved legacy references and opaque drafts, independently of model history."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0021"
down_revision = "v2_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_legacy_materials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("source_summary", sa.JSON(), nullable=False),
        sa.Column("asset_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_kind", "source_id", name="uq_v2_legacy_material_source"),
        sa.ForeignKeyConstraint(["asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"],
                                name="fk_v2_legacy_material_asset_owner"),
        sa.CheckConstraint("source_kind IN ('reference','project_reference','draft')", name="ck_v2_legacy_material_kind"),
        sa.CheckConstraint("(source_kind = 'draft' AND asset_id IS NULL) OR (source_kind <> 'draft' AND asset_id IS NOT NULL)",
                           name="ck_v2_legacy_material_payload"))
    op.create_index("ix_v2_legacy_material_owner", "v2_legacy_materials", ["owner_id", "id"])
    op.execute("CREATE TRIGGER v2_legacy_material_guard BEFORE UPDATE OR DELETE ON v2_legacy_materials "
               "FOR EACH ROW EXECUTE FUNCTION v2_legacy_import_guard()")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_legacy_materials)")).scalar():
        raise RuntimeError("Refuse to discard legacy material provenance")
    op.drop_table("v2_legacy_materials")
