"""Stable paginated history and frozen parent visibility for branches."""
from alembic import op
import sqlalchemy as sa
revision = "v2_0010"
down_revision = "v2_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("LOCK TABLE v2_designs, v2_branches, v2_messages, v2_revisions IN ACCESS EXCLUSIVE MODE")
    op.add_column("v2_designs", sa.Column("history_counter", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("v2_branches", sa.Column("history_parent_sequence", sa.BigInteger(), nullable=True))
    for table in ("v2_messages", "v2_revisions"):
        op.add_column(table, sa.Column("history_sequence", sa.BigInteger(), nullable=True))
    op.execute("""
        CREATE TEMP TABLE v2_history_backfill ON COMMIT DROP AS
        SELECT kind, id, design_id, row_number() OVER
          (PARTITION BY design_id ORDER BY created_at, kind, id) AS sequence
        FROM (
          SELECT 'message'::text AS kind, m.id, b.design_id, m.created_at
          FROM v2_messages m JOIN v2_branches b ON b.id=m.branch_id
          UNION ALL
          SELECT 'revision', id, design_id, created_at FROM v2_revisions
        ) entries
    """)
    op.execute("ALTER TABLE v2_revisions DISABLE TRIGGER v2_revisions_immutable")
    for table, kind in (("v2_messages", "message"), ("v2_revisions", "revision")):
        op.execute(f"""UPDATE {table} t SET history_sequence=h.sequence
            FROM v2_history_backfill h WHERE h.kind='{kind}' AND h.id=t.id""")
    op.execute("ALTER TABLE v2_revisions ENABLE TRIGGER v2_revisions_immutable")
    op.execute("""UPDATE v2_designs d SET history_counter=h.sequence FROM
        (SELECT design_id,max(sequence) AS sequence FROM v2_history_backfill GROUP BY design_id) h
        WHERE d.id=h.design_id""")
    # Known historical branches were created for regeneration or continuation.
    # A missing original user message remains NULL (explicit incomplete ancestry).
    op.execute("""
        UPDATE v2_branches b SET history_parent_sequence =
          CASE WHEN (SELECT kind FROM v2_operations o WHERE o.branch_id=b.id
                     ORDER BY o.created_at,o.id LIMIT 1)='regenerate'
            THEN (SELECT min(m.history_sequence)-1 FROM v2_messages m
                  WHERE m.operation_id=r.operation_id AND m.role='user' AND m.branch_id=b.parent_branch_id)
            ELSE greatest(r.history_sequence,coalesce(
                 (SELECT max(m.history_sequence) FROM v2_messages m WHERE m.operation_id=r.operation_id
                  AND m.branch_id=b.parent_branch_id),0))
          END
        FROM v2_revisions r
        WHERE b.parent_branch_id IS NOT NULL AND r.id=b.origin_revision_id
          AND r.branch_id=b.parent_branch_id AND r.design_id=b.design_id
    """)
    op.create_check_constraint("ck_v2_design_history_counter", "v2_designs",
                               "history_counter >= 0 AND history_counter <= 9007199254740991")
    op.create_check_constraint("ck_v2_branch_history_parent", "v2_branches",
                               "history_parent_sequence >= 0 AND history_parent_sequence <= 9007199254740991")
    for table in ("v2_messages", "v2_revisions"):
        op.alter_column(table, "history_sequence", nullable=False)
        op.create_check_constraint(f"ck_{table}_history_sequence", table,
                                   "history_sequence >= 1 AND history_sequence <= 9007199254740991")
        op.create_index(f"ix_{table}_branch_history", table, ["branch_id", "history_sequence"], unique=True)
    op.execute("""
        CREATE FUNCTION v2_assign_history_sequence() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner_design varchar(36);
        BEGIN
            IF NEW.history_sequence IS NOT NULL THEN
                RAISE EXCEPTION 'History sequences are assigned by the server';
            END IF;
            SELECT design_id INTO owner_design FROM v2_branches WHERE id=NEW.branch_id;
            UPDATE v2_designs SET history_counter=history_counter+1
              WHERE id=owner_design RETURNING history_counter INTO NEW.history_sequence;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'History branch does not exist' USING ERRCODE='23503';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    for table in ("v2_messages", "v2_revisions"):
        op.execute(f"""CREATE TRIGGER {table}_history_insert BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION v2_assign_history_sequence()""")
    op.execute("""
        CREATE FUNCTION v2_guard_message_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.history_sequence IS DISTINCT FROM OLD.history_sequence
                OR NEW.branch_id IS DISTINCT FROM OLD.branch_id OR NEW.id IS DISTINCT FROM OLD.id THEN
                RAISE EXCEPTION 'Message history identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""CREATE TRIGGER v2_messages_history_update BEFORE UPDATE ON v2_messages
        FOR EACH ROW EXECUTE FUNCTION v2_guard_message_history()""")

    op.execute("""
        CREATE FUNCTION v2_guard_branch_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.parent_branch_id IS DISTINCT FROM OLD.parent_branch_id
                OR NEW.origin_revision_id IS DISTINCT FROM OLD.origin_revision_id
                OR NEW.history_parent_sequence IS DISTINCT FROM OLD.history_parent_sequence
                OR NEW.design_id IS DISTINCT FROM OLD.design_id THEN
                RAISE EXCEPTION 'Branch history boundary is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""CREATE TRIGGER v2_branches_history_update BEFORE UPDATE ON v2_branches
        FOR EACH ROW EXECUTE FUNCTION v2_guard_branch_history()""")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_messages UNION ALL SELECT 1 FROM v2_revisions LIMIT 1")).first():
        raise RuntimeError("Sequenced history exists; retain schema during rollback")
    op.execute("DROP TRIGGER v2_branches_history_update ON v2_branches")
    op.execute("DROP FUNCTION v2_guard_branch_history()")
    op.execute("DROP TRIGGER v2_messages_history_update ON v2_messages")
    op.execute("DROP FUNCTION v2_guard_message_history()")
    for table in ("v2_messages", "v2_revisions"):
        op.execute(f"DROP TRIGGER {table}_history_insert ON {table}")
        op.drop_index(f"ix_{table}_branch_history", table_name=table)
        op.drop_constraint(f"ck_{table}_history_sequence", table, type_="check")
        op.drop_column(table, "history_sequence")
    op.execute("DROP FUNCTION v2_assign_history_sequence()")
    op.drop_constraint("ck_v2_branch_history_parent", "v2_branches", type_="check")
    op.drop_column("v2_branches", "history_parent_sequence")
    op.drop_constraint("ck_v2_design_history_counter", "v2_designs", type_="check")
    op.drop_column("v2_designs", "history_counter")
