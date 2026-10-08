"""Durable upstream context allocation and historical source provenance."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0006"
down_revision = "v2_0005"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("v2_operations", sa.Column("context_allocation", sa.JSON()))
    op.add_column("v2_operations", sa.Column("source_operation_id", sa.String(36)))
    op.create_foreign_key("fk_v2_operation_source", "v2_operations", "v2_operations", ["source_operation_id"], ["id"])
    op.add_column("v2_branches", sa.Column("origin_revision_id", sa.String(36)))
    op.add_column("v2_branches", sa.Column("context_seed", sa.JSON()))
    op.add_column("v2_revisions", sa.Column("request_digest", sa.String(64)))
    op.create_unique_constraint("uq_v2_manual_design_request", "v2_revisions", ["design_id", "client_request_id"])
    op.create_foreign_key("fk_v2_branch_origin", "v2_branches", "v2_revisions",
                          ["origin_revision_id", "design_id"], ["id", "design_id"])

def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_operations WHERE context_allocation IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Context allocation history exists; retain schema during rollback")
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_branches WHERE context_seed IS NOT NULL OR origin_revision_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Historical branches exist; retain schema during rollback")
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_revisions WHERE request_digest IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Manual request identities exist; retain schema during rollback")
    op.drop_constraint("uq_v2_manual_design_request", "v2_revisions", type_="unique")
    op.drop_column("v2_revisions", "request_digest")
    op.drop_column("v2_branches", "context_seed")
    op.drop_constraint("fk_v2_branch_origin", "v2_branches", type_="foreignkey")
    op.drop_column("v2_branches", "origin_revision_id")
    op.drop_constraint("fk_v2_operation_source", "v2_operations", type_="foreignkey")
    op.drop_column("v2_operations", "source_operation_id")
    op.drop_column("v2_operations", "context_allocation")
