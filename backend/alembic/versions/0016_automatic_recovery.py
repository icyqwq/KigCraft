"""Bounded automatic reconciliation audit."""
from alembic import op
import sqlalchemy as sa
revision="v2_0016"
down_revision="v2_0015"
branch_labels=None
depends_on=None
STATEMENTS=('\nCREATE TABLE v2_recovery_checks (\n\tid VARCHAR(36) NOT NULL, \n\toperation_id VARCHAR(36) NOT NULL, \n\tattempt INTEGER NOT NULL, \n\tworker_id VARCHAR(100) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\toperation_state VARCHAR(24), \n\terror_code VARCHAR(100), \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tnext_check_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_v2_recovery_attempt UNIQUE (operation_id, attempt), \n\tCONSTRAINT ck_v2_recovery_attempt CHECK (attempt > 0), \n\tFOREIGN KEY(operation_id) REFERENCES v2_operations (id)\n)\n\n', 'CREATE INDEX ix_v2_recovery_operation ON v2_recovery_checks (operation_id, attempt)', "CREATE FUNCTION v2_recovery_check_guard() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Recovery audit cannot be deleted'; END IF;\n IF ROW(NEW.id,NEW.operation_id,NEW.attempt,NEW.worker_id,NEW.started_at)\n IS DISTINCT FROM ROW(OLD.id,OLD.operation_id,OLD.attempt,OLD.worker_id,OLD.started_at)\n THEN RAISE EXCEPTION 'Recovery identity is immutable'; END IF;\n RETURN NEW;\nEND $$", 'CREATE TRIGGER v2_recovery_check_guard BEFORE UPDATE OR DELETE ON v2_recovery_checks FOR EACH ROW EXECUTE FUNCTION v2_recovery_check_guard()')


def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.text(statement))

def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM v2_recovery_checks)")).scalar():
        raise RuntimeError("Refuse to discard automatic recovery audit")
    op.execute("DROP TABLE v2_recovery_checks")
    op.execute("DROP FUNCTION v2_recovery_check_guard()")
