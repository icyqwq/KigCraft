"""Image event anomaly receipts; null preserves unknown historical coverage."""
from alembic import op
import sqlalchemy as sa
revision="v2_0023"
down_revision="v2_0022"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("v2_runtime_observations",sa.Column("image_anomalies",sa.JSON(),nullable=True))

def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM v2_runtime_observations WHERE image_anomalies IS NOT NULL)")).scalar():
        raise RuntimeError("Refuse to discard image anomaly observations")
    op.drop_column("v2_runtime_observations","image_anomalies")
