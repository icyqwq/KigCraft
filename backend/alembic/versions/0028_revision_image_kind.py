"""Classify generated versions as finished head-shell photos or character references.

A revision is immutable (trigger v2_revisions_immutable), so the kind is written when the row is
inserted. Adding a column with a constant default is a catalog change and does not fire the
row-level UPDATE trigger; every existing version becomes head_shell.
"""
from alembic import op
import sqlalchemy as sa

revision = "v2_0028"
down_revision = "v2_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("v2_revisions", sa.Column("image_kind", sa.String(16), nullable=False, server_default="head_shell"))
    op.create_check_constraint("ck_v2_revisions_image_kind", "v2_revisions", "image_kind IN ('head_shell','reference')")


def downgrade():
    op.drop_constraint("ck_v2_revisions_image_kind", "v2_revisions", type_="check")
    op.drop_column("v2_revisions", "image_kind")
