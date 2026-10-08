"""Audited recovery requests and persistent runtime admission controls."""
from alembic import op
import sqlalchemy as sa
revision="v2_0015"
down_revision="v2_0014"
branch_labels=None
depends_on=None

STATEMENTS=('\nCREATE TABLE v2_runtime_controls (\n\tscope VARCHAR(50) NOT NULL, \n\tblocked BOOLEAN NOT NULL, \n\tPRIMARY KEY (scope)\n)\n\n', '\nCREATE TABLE v2_admin_actions (\n\tid VARCHAR(36) NOT NULL, \n\tactor_id VARCHAR(36) NOT NULL, \n\tclient_request_id VARCHAR(100) NOT NULL, \n\trequest_digest VARCHAR(64) NOT NULL, \n\taction VARCHAR(24) NOT NULL, \n\ttarget_id VARCHAR(36) NOT NULL, \n\towner_id VARCHAR(36), \n\treason VARCHAR(500) NOT NULL, \n\tunits INTEGER, \n\tstatus VARCHAR(24) NOT NULL, \n\tresult JSON NOT NULL, \n\terror_code VARCHAR(100), \n\treplay_count INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_v2_admin_request UNIQUE (actor_id, client_request_id), \n\tCONSTRAINT ck_v2_admin_replays CHECK (replay_count >= 0), \n\tFOREIGN KEY(actor_id) REFERENCES users (id), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n', 'CREATE INDEX ix_v2_admin_pending ON v2_admin_actions (owner_id, status, created_at)', "CREATE FUNCTION v2_admin_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Admin audit cannot be deleted'; END IF;\n IF ROW(NEW.id,NEW.actor_id,NEW.client_request_id,NEW.request_digest,NEW.action,NEW.target_id,NEW.owner_id,NEW.reason,NEW.units,NEW.created_at)\n IS DISTINCT FROM ROW(OLD.id,OLD.actor_id,OLD.client_request_id,OLD.request_digest,OLD.action,OLD.target_id,OLD.owner_id,OLD.reason,OLD.units,OLD.created_at)\n THEN RAISE EXCEPTION 'Admin request is immutable'; END IF;\n RETURN NEW;\nEND $$", 'CREATE TRIGGER v2_admin_audit_guard BEFORE UPDATE OR DELETE ON v2_admin_actions FOR EACH ROW EXECUTE FUNCTION v2_admin_audit_guard()')


def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.text(statement))

def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_admin_actions) OR EXISTS(SELECT 1 FROM v2_runtime_controls)")).scalar():
        raise RuntimeError("Refuse to discard audit or runtime controls")
    op.execute("DROP TABLE v2_admin_actions")
    op.execute("DROP FUNCTION v2_admin_audit_guard()")
    op.execute("DROP TABLE v2_runtime_controls")
