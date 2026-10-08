"""Immutable, design-wide revision numbers, allocated under the design row lock."""
from alembic import op
import sqlalchemy as sa

revision = "v2_0009"
down_revision = "v2_0008"
branch_labels = None
depends_on = None


def upgrade():
    # Hold both tables before backfilling so old writers cannot insert an unnumbered row.
    op.execute("LOCK TABLE v2_designs, v2_revisions IN ACCESS EXCLUSIVE MODE")
    op.add_column("v2_designs", sa.Column("revision_counter", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("v2_revisions", sa.Column("number", sa.BigInteger(), nullable=True))
    op.execute("ALTER TABLE v2_revisions DISABLE TRIGGER v2_revisions_immutable")
    op.execute("""
        WITH numbered AS (
            SELECT id, row_number() OVER (PARTITION BY design_id ORDER BY created_at, id) AS n
            FROM v2_revisions
        )
        UPDATE v2_revisions r SET number = numbered.n FROM numbered WHERE r.id = numbered.id
    """)
    op.execute("ALTER TABLE v2_revisions ENABLE TRIGGER v2_revisions_immutable")
    op.execute("""
        UPDATE v2_designs d SET revision_counter = numbered.n
        FROM (SELECT design_id, max(number) AS n FROM v2_revisions GROUP BY design_id) numbered
        WHERE d.id = numbered.design_id
    """)
    op.alter_column("v2_revisions", "number", nullable=False)
    op.create_check_constraint("ck_v2_design_revision_counter", "v2_designs",
                               "revision_counter >= 0 AND revision_counter <= 9007199254740991")
    op.create_check_constraint("ck_v2_revision_number", "v2_revisions",
                               "number >= 1 AND number <= 9007199254740991")
    op.create_unique_constraint("uq_v2_revision_number", "v2_revisions", ["design_id", "number"])
    op.execute("""
        CREATE FUNCTION v2_assign_revision_number() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.number IS NOT NULL THEN
                RAISE EXCEPTION 'Revision numbers are assigned by the server';
            END IF;
            UPDATE v2_designs SET revision_counter = revision_counter + 1
                WHERE id = NEW.design_id RETURNING revision_counter INTO NEW.number;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Revision design does not exist' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER v2_revision_number_insert BEFORE INSERT ON v2_revisions
        FOR EACH ROW EXECUTE FUNCTION v2_assign_revision_number()
    """)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_revisions LIMIT 1")).first():
        raise RuntimeError("Numbered revisions exist; retain schema during rollback")
    op.execute("DROP TRIGGER v2_revision_number_insert ON v2_revisions")
    op.execute("DROP FUNCTION v2_assign_revision_number()")
    op.drop_constraint("uq_v2_revision_number", "v2_revisions", type_="unique")
    op.drop_constraint("ck_v2_revision_number", "v2_revisions", type_="check")
    op.drop_constraint("ck_v2_design_revision_counter", "v2_designs", type_="check")
    op.drop_column("v2_revisions", "number")
    op.drop_column("v2_designs", "revision_counter")
