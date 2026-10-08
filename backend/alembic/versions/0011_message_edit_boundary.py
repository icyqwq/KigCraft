"""Freeze the original message identity used by historical edits."""
from alembic import op
import sqlalchemy as sa
revision="v2_0011"
down_revision="v2_0010"
branch_labels=None
depends_on=None


def guard(message_identity):
    extra="OR NEW.fork_boundary_message_id IS DISTINCT FROM OLD.fork_boundary_message_id" if message_identity else ""
    op.execute(f"""
        CREATE OR REPLACE FUNCTION v2_guard_branch_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.parent_branch_id IS DISTINCT FROM OLD.parent_branch_id
                OR NEW.origin_revision_id IS DISTINCT FROM OLD.origin_revision_id
                OR NEW.history_parent_sequence IS DISTINCT FROM OLD.history_parent_sequence
                OR NEW.design_id IS DISTINCT FROM OLD.design_id {extra} THEN
                RAISE EXCEPTION 'Branch history boundary is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)


def upgrade():
    guard(True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_branches WHERE fork_boundary_message_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Historical message edits exist; retain schema during rollback")
    guard(False)
