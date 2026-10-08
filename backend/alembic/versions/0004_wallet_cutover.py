"""Quota policy window and immutable audit metadata; no automatic account cutover."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0004"
down_revision = "v2_0003"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("v2_wallets", sa.Column("last_topup_window", sa.DateTime(timezone=True)))
    op.add_column("v2_ledger", sa.Column("actor_id", sa.String(36)))
    op.create_foreign_key("fk_v2_ledger_actor", "v2_ledger", "users", ["actor_id"], ["id"])
    op.add_column("v2_ledger", sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_unique_constraint("uq_v2_manual_request", "v2_revisions", ["branch_id", "client_request_id"])

def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM v2_wallets WHERE last_topup_window IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Quota policy history exists; retain schema during application rollback")
    op.drop_constraint("uq_v2_manual_request", "v2_revisions", type_="unique")
    op.drop_column("v2_ledger", "details")
    op.drop_constraint("fk_v2_ledger_actor", "v2_ledger", type_="foreignkey")
    op.drop_column("v2_ledger", "actor_id")
    op.drop_column("v2_wallets", "last_topup_window")
