"""Immutable legacy source mapping."""
from alembic import op
import sqlalchemy as sa
revision="v2_0017"
down_revision="v2_0016"
branch_labels=None
depends_on=None
STATEMENTS=("\nCREATE TABLE v2_legacy_imports (\n\tid VARCHAR(36) NOT NULL, \n\towner_id VARCHAR(36) NOT NULL, \n\tsource_kind VARCHAR(16) NOT NULL, \n\tsource_id VARCHAR(36) NOT NULL, \n\tsource_digest VARCHAR(64) NOT NULL, \n\tsource_summary JSON NOT NULL, \n\timage_sha256 VARCHAR(64), \n\tgroup_key VARCHAR(64) NOT NULL, \n\tdesign_id VARCHAR(36) NOT NULL, \n\trevision_id VARCHAR(36), \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_v2_legacy_source UNIQUE (source_kind, source_id), \n\tCONSTRAINT fk_v2_legacy_design_owner FOREIGN KEY(design_id, owner_id) REFERENCES v2_designs (id, owner_id), \n\tCONSTRAINT fk_v2_legacy_revision_design FOREIGN KEY(revision_id, design_id) REFERENCES v2_revisions (id, design_id), \n\tCONSTRAINT ck_v2_legacy_kind CHECK (source_kind IN ('job','output','album')), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n", 'CREATE INDEX ix_v2_legacy_design ON v2_legacy_imports (design_id, id)', 'CREATE INDEX ix_v2_legacy_imports_group_key ON v2_legacy_imports (group_key)', 'CREATE INDEX ix_v2_legacy_imports_owner_id ON v2_legacy_imports (owner_id)', 'CREATE INDEX ix_v2_legacy_revision ON v2_legacy_imports (revision_id, id)', "CREATE FUNCTION v2_legacy_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Legacy import mapping is immutable'; END $$", 'CREATE TRIGGER v2_legacy_import_guard BEFORE UPDATE OR DELETE ON v2_legacy_imports FOR EACH ROW EXECUTE FUNCTION v2_legacy_import_guard()')
def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.text(statement))

def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_legacy_imports)")).scalar():
        raise RuntimeError("Refuse to discard legacy import provenance")
    op.execute("DROP TABLE v2_legacy_imports")
    op.execute("DROP FUNCTION v2_legacy_import_guard()")
