"""Mark expired design content retirement while retaining financial tombstones."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0025"
down_revision = "v2_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("v2_designs", sa.Column("content_purged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("v2_designs", sa.Column("retained_resources", sa.JSON(), nullable=True))
    op.create_check_constraint("ck_v2_design_content_retirement", "v2_designs",
        "content_purged_at IS NULL OR (deleted_at IS NOT NULL AND trash_restore_until IS NOT NULL "
        "AND content_purged_at >= trash_restore_until AND retained_resources IS NOT NULL)")


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM v2_designs WHERE content_purged_at IS NOT NULL)")).scalar():
        raise RuntimeError("Refuse to discard content retirement markers")
    op.drop_constraint("ck_v2_design_content_retirement", "v2_designs", type_="check")
    op.drop_column("v2_designs", "retained_resources")
    op.drop_column("v2_designs", "content_purged_at")
