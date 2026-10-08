"""Persist account closure intent and prevent accidental reactivation."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0026"
down_revision = "v2_0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_account_closures",
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("previously_banned", sa.Boolean(), nullable=False),
        sa.Column("policy_days", sa.Integer(), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_v2_account_closure_request"),
        sa.CheckConstraint("state IN ('pending','cancelled','closed')", name="ck_v2_account_closure_state"),
        sa.CheckConstraint("policy_days >= 1 AND policy_days <= 3650 AND delete_after > requested_at", name="ck_v2_account_closure_policy"),
        sa.CheckConstraint("(state = 'pending' AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(state = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL) OR "
            "(state = 'closed' AND completed_at IS NOT NULL AND completed_at >= delete_after AND cancelled_at IS NULL)",
            name="ck_v2_account_closure_terminal"))
    op.execute("""
CREATE FUNCTION v2_account_closure_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE closure_state text;
BEGIN
    IF TG_TABLE_NAME = 'users' THEN
        SELECT state INTO closure_state FROM v2_account_closures WHERE owner_id = OLD.id;
        IF closure_state IN ('pending','closed') AND NEW.is_banned IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'account closure prevents reactivation';
        END IF;
        IF closure_state = 'closed' AND
            (NEW.email IS DISTINCT FROM ('closed+' || OLD.id || '@kigcraft.invalid') OR NEW.password_hash IS NOT NULL) THEN
            RAISE EXCEPTION 'closed account identity is retired';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'account closure history cannot be deleted';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.state = 'closed' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'closed account marker is immutable';
    END IF;
    IF NEW.state IN ('pending','closed') AND NOT EXISTS
        (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_banned=true) THEN
        RAISE EXCEPTION 'account closure requires blocked identity';
    END IF;
    RETURN NEW;
END $$;
""")
    op.execute("""CREATE TRIGGER v2_account_closure_user_guard BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION v2_account_closure_guard();
""")
    op.execute("""CREATE TRIGGER v2_account_closure_state_guard BEFORE INSERT OR UPDATE OR DELETE ON v2_account_closures
FOR EACH ROW EXECUTE FUNCTION v2_account_closure_guard();
""")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_account_closures)")).scalar():
        raise RuntimeError("Refuse to discard account closure history")
    op.execute("DROP TRIGGER v2_account_closure_user_guard ON users")
    op.drop_table("v2_account_closures")
    op.execute("DROP FUNCTION v2_account_closure_guard()")
