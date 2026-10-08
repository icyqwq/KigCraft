"""Preserve closed-account resource references while removing legacy content."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0027"
down_revision = "v2_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_account_content_retirements",
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("v2_account_closures.owner_id"), primary_key=True),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.execute("CREATE TRIGGER v2_account_content_immutable BEFORE UPDATE OR DELETE ON v2_account_content_retirements "
               "FOR EACH ROW EXECUTE FUNCTION v2_reject_mutation()")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_account_content_retirements)")).scalar():
        raise RuntimeError("Refuse to discard account content retirement references")
    op.drop_table("v2_account_content_retirements")
