"""Let one controlled transaction delete a whole design.

Versions, snapshots, checkpoints and four-view groups are immutable, and ledger rows may never change.
The guards stay for every writer. They step aside only inside a transaction that sets the transaction-local
setting kig.v2_design_purge to 'on' (the design purge does, and nothing else): the design-owned tables may
then be deleted, branch history links may be cleared, and a ledger row may lose its operation link (and
nothing else about it). Amounts, owners and keys of money records never change.
"""
from alembic import op

revision = "v2_0029"
down_revision = "v2_0028"
branch_labels = None
depends_on = None

PURGING = "coalesce(current_setting('kig.v2_design_purge', true), '') = 'on'"
DELETABLE = ("'v2_input_snapshots','v2_revisions','v2_editor_checkpoints','v2_editor_save_receipts',"
             "'v2_viewset_attempts','v2_viewset_items','v2_viewsets'")

NEW_REJECT = f"""
CREATE OR REPLACE FUNCTION v2_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF {PURGING} THEN
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME IN ({DELETABLE}) THEN RETURN OLD; END IF;
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'v2_ledger' AND NEW.operation_id IS NULL
       AND (to_jsonb(NEW) - 'operation_id') = (to_jsonb(OLD) - 'operation_id') THEN RETURN NEW; END IF;
  END IF;
  RAISE EXCEPTION 'immutable V2 record cannot be changed';
END; $$
"""
OLD_REJECT = """
CREATE OR REPLACE FUNCTION v2_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN RAISE EXCEPTION 'immutable V2 record cannot be changed'; END; $$
"""
NEW_BRANCH = f"""
CREATE OR REPLACE FUNCTION v2_guard_branch_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF {PURGING} THEN RETURN NEW; END IF;
            IF NEW.parent_branch_id IS DISTINCT FROM OLD.parent_branch_id
                OR NEW.origin_revision_id IS DISTINCT FROM OLD.origin_revision_id
                OR NEW.history_parent_sequence IS DISTINCT FROM OLD.history_parent_sequence
                OR NEW.design_id IS DISTINCT FROM OLD.design_id OR NEW.fork_boundary_message_id IS DISTINCT FROM OLD.fork_boundary_message_id THEN
                RAISE EXCEPTION 'Branch history boundary is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
"""
OLD_BRANCH = """
CREATE OR REPLACE FUNCTION v2_guard_branch_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.parent_branch_id IS DISTINCT FROM OLD.parent_branch_id
                OR NEW.origin_revision_id IS DISTINCT FROM OLD.origin_revision_id
                OR NEW.history_parent_sequence IS DISTINCT FROM OLD.history_parent_sequence
                OR NEW.design_id IS DISTINCT FROM OLD.design_id OR NEW.fork_boundary_message_id IS DISTINCT FROM OLD.fork_boundary_message_id THEN
                RAISE EXCEPTION 'Branch history boundary is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
"""
NEW_LEGACY = f"""
CREATE OR REPLACE FUNCTION v2_legacy_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF {PURGING} AND TG_OP = 'DELETE' AND TG_TABLE_NAME = 'v2_legacy_imports' THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'Legacy import mapping is immutable';
END $$
"""
OLD_LEGACY = """
CREATE OR REPLACE FUNCTION v2_legacy_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Legacy import mapping is immutable'; END $$
"""
NEW_RECOVERY = f"""
CREATE OR REPLACE FUNCTION v2_recovery_check_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF {PURGING} THEN RETURN OLD; END IF;
   RAISE EXCEPTION 'Recovery audit cannot be deleted';
 END IF;
 IF ROW(NEW.id,NEW.operation_id,NEW.attempt,NEW.worker_id,NEW.started_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.operation_id,OLD.attempt,OLD.worker_id,OLD.started_at)
 THEN RAISE EXCEPTION 'Recovery identity is immutable'; END IF;
 RETURN NEW;
END $$
"""
OLD_RECOVERY = """
CREATE OR REPLACE FUNCTION v2_recovery_check_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Recovery audit cannot be deleted'; END IF;
 IF ROW(NEW.id,NEW.operation_id,NEW.attempt,NEW.worker_id,NEW.started_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.operation_id,OLD.attempt,OLD.worker_id,OLD.started_at)
 THEN RAISE EXCEPTION 'Recovery identity is immutable'; END IF;
 RETURN NEW;
END $$
"""


def upgrade():
    for statement in (NEW_REJECT, NEW_BRANCH, NEW_LEGACY, NEW_RECOVERY):
        op.execute(statement)


def downgrade():
    for statement in (OLD_REJECT, OLD_BRANCH, OLD_LEGACY, OLD_RECOVERY):
        op.execute(statement)
