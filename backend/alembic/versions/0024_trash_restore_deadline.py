"""Snapshot an approved restore window when a design enters trash."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0024"
down_revision = "v2_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("v2_designs", sa.Column("trash_restore_until", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_v2_design_trash_deadline", "v2_designs",
        "trash_restore_until IS NULL OR (deleted_at IS NOT NULL AND trash_restore_until > deleted_at)")
    op.create_index("ix_v2_design_trash_restore_until", "v2_designs", ["trash_restore_until"])


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM v2_designs WHERE trash_restore_until IS NOT NULL)")).scalar():
        raise RuntimeError("Refuse to discard trash restoration deadlines")
    op.drop_index("ix_v2_design_trash_restore_until", table_name="v2_designs")
    op.drop_constraint("ck_v2_design_trash_deadline", "v2_designs", type_="check")
    op.drop_column("v2_designs", "trash_restore_until")
