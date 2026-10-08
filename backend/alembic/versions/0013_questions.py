"""Persist completed-turn clarification boundaries."""
from alembic import op
import sqlalchemy as sa
revision="v2_0013"
down_revision="v2_0012"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("v2_operations",sa.Column("question",sa.JSON(),nullable=True))

def downgrade():
    connection=op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM v2_operations WHERE question IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard persisted clarification questions")
    op.drop_column("v2_operations","question")
