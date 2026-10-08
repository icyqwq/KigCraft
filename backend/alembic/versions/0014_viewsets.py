"""Frozen view-set authorizations, delivery units and immutable attempts."""
from alembic import op
import sqlalchemy as sa
revision="v2_0014"
down_revision="v2_0013"
branch_labels=None
depends_on=None

STATEMENTS = ('\nCREATE TABLE v2_viewsets (\n\tid VARCHAR(36) NOT NULL, \n\towner_id VARCHAR(36) NOT NULL, \n\tdesign_id VARCHAR(36) NOT NULL, \n\tbranch_id VARCHAR(36) NOT NULL, \n\tsource_revision_id VARCHAR(36) NOT NULL, \n\tclient_request_id VARCHAR(100) NOT NULL, \n\trequest_digest VARCHAR(64) NOT NULL, \n\tsnapshot JSON NOT NULL, \n\tsnapshot_digest VARCHAR(64) NOT NULL, \n\tquote_id VARCHAR(36) NOT NULL, \n\tmax_units INTEGER NOT NULL, \n\tsettled_units INTEGER NOT NULL, \n\theld_units INTEGER NOT NULL, \n\trun_number INTEGER NOT NULL, \n\tauthorization_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstate VARCHAR(24) NOT NULL, \n\tcancel_requested BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_v2_viewset_request UNIQUE (owner_id, client_request_id), \n\tCONSTRAINT uq_v2_viewset_quote UNIQUE (quote_id), \n\tCONSTRAINT uq_v2_viewset_design UNIQUE (id, design_id), \n\tCONSTRAINT fk_v2_viewset_owner FOREIGN KEY(design_id, owner_id) REFERENCES v2_designs (id, owner_id), \n\tCONSTRAINT fk_v2_viewset_branch FOREIGN KEY(branch_id, design_id) REFERENCES v2_branches (id, design_id), \n\tCONSTRAINT fk_v2_viewset_source FOREIGN KEY(source_revision_id, design_id) REFERENCES v2_revisions (id, design_id), \n\tCONSTRAINT fk_v2_viewset_quote FOREIGN KEY(quote_id, owner_id) REFERENCES v2_quotes (id, owner_id), \n\tCONSTRAINT ck_v2_viewset_units CHECK (max_units >= settled_units + held_units AND settled_units >= 0 AND held_units >= 0 AND run_number >= 1)\n)\n\n', '\nCREATE TABLE v2_viewset_items (\n\tid VARCHAR(36) NOT NULL, \n\tviewset_id VARCHAR(36) NOT NULL, \n\tdesign_id VARCHAR(36) NOT NULL, \n\tview VARCHAR(24) NOT NULL, \n\tordinal INTEGER NOT NULL, \n\tunit_cost INTEGER NOT NULL, \n\tinferred BOOLEAN NOT NULL, \n\tstate VARCHAR(24) NOT NULL, \n\tattempt_count INTEGER NOT NULL, \n\trevision_id VARCHAR(36), \n\terror_code VARCHAR(100), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_v2_viewset_angle UNIQUE (viewset_id, view), \n\tCONSTRAINT uq_v2_viewset_ordinal UNIQUE (viewset_id, ordinal), \n\tCONSTRAINT uq_v2_viewset_item_design UNIQUE (id, design_id), \n\tCONSTRAINT fk_v2_viewset_item_group FOREIGN KEY(viewset_id, design_id) REFERENCES v2_viewsets (id, design_id), \n\tCONSTRAINT fk_v2_viewset_item_revision FOREIGN KEY(revision_id, design_id) REFERENCES v2_revisions (id, design_id), \n\tCONSTRAINT ck_v2_viewset_item_units CHECK (unit_cost >= 0 AND ordinal >= 0 AND attempt_count >= 0)\n)\n\n', '\nCREATE TABLE v2_viewset_attempts (\n\toperation_id VARCHAR(36) NOT NULL, \n\titem_id VARCHAR(36) NOT NULL, \n\tdesign_id VARCHAR(36) NOT NULL, \n\tnumber INTEGER NOT NULL, \n\tPRIMARY KEY (operation_id), \n\tCONSTRAINT uq_v2_viewset_attempt UNIQUE (item_id, number), \n\tCONSTRAINT fk_v2_viewset_attempt_item FOREIGN KEY(item_id, design_id) REFERENCES v2_viewset_items (id, design_id), \n\tCONSTRAINT fk_v2_viewset_attempt_operation FOREIGN KEY(operation_id, design_id) REFERENCES v2_operations (id, design_id), \n\tCONSTRAINT ck_v2_viewset_attempt_number CHECK (number >= 1)\n)\n\n', "CREATE UNIQUE INDEX uq_v2_active_viewset ON v2_viewsets (design_id) WHERE state IN ('running','reconciling','publishing','cancelling')", "CREATE FUNCTION v2_viewset_fixed_input() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n IF ROW(NEW.owner_id,NEW.design_id,NEW.branch_id,NEW.source_revision_id,NEW.client_request_id,NEW.request_digest,NEW.snapshot::jsonb,NEW.snapshot_digest,NEW.quote_id,NEW.max_units,NEW.authorization_expires_at)\n IS DISTINCT FROM ROW(OLD.owner_id,OLD.design_id,OLD.branch_id,OLD.source_revision_id,OLD.client_request_id,OLD.request_digest,OLD.snapshot::jsonb,OLD.snapshot_digest,OLD.quote_id,OLD.max_units,OLD.authorization_expires_at)\n THEN RAISE EXCEPTION 'immutable viewset authorization cannot be changed'; END IF;\n RETURN NEW;\nEND; $$", 'CREATE TRIGGER v2_viewset_fixed_input BEFORE UPDATE ON v2_viewsets FOR EACH ROW EXECUTE FUNCTION v2_viewset_fixed_input()', "CREATE FUNCTION v2_viewset_fixed_item() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n IF ROW(NEW.viewset_id,NEW.design_id,NEW.view,NEW.ordinal,NEW.unit_cost,NEW.inferred)\n IS DISTINCT FROM ROW(OLD.viewset_id,OLD.design_id,OLD.view,OLD.ordinal,OLD.unit_cost,OLD.inferred)\n OR (OLD.revision_id IS NOT NULL AND NEW.revision_id IS DISTINCT FROM OLD.revision_id)\n THEN RAISE EXCEPTION 'immutable viewset delivery cannot be changed'; END IF;\n RETURN NEW;\nEND; $$", 'CREATE TRIGGER v2_viewset_fixed_item BEFORE UPDATE ON v2_viewset_items FOR EACH ROW EXECUTE FUNCTION v2_viewset_fixed_item()', 'CREATE TRIGGER v2_viewset_attempts_immutable BEFORE UPDATE OR DELETE ON v2_viewset_attempts FOR EACH ROW EXECUTE FUNCTION v2_reject_mutation()')


STATEMENTS += (
    "CREATE TRIGGER v2_viewsets_no_delete BEFORE DELETE ON v2_viewsets FOR EACH ROW EXECUTE FUNCTION v2_reject_mutation()",
    "CREATE TRIGGER v2_viewset_items_no_delete BEFORE DELETE ON v2_viewset_items FOR EACH ROW EXECUTE FUNCTION v2_reject_mutation()",
)


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)

def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM v2_viewsets LIMIT 1")).first():
        raise RuntimeError("Cannot discard view-set authorizations and deliveries")
    op.drop_table("v2_viewset_attempts")
    op.drop_table("v2_viewset_items")
    op.drop_table("v2_viewsets")
    op.execute("DROP FUNCTION v2_viewset_fixed_item()")
    op.execute("DROP FUNCTION v2_viewset_fixed_input()")
