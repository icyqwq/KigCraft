"""Public message search and meaningful design activity."""
from alembic import op
import sqlalchemy as sa
revision="v2_0012"
down_revision="v2_0011"
branch_labels=None
depends_on=None


# Frozen v1 public-text projection; future changes need a new migration.
import re

_LOCAL_START = re.compile(r"^(?:[A-Za-z]:[\\/]|file:|sandbox:|/(?:tmp|mnt|home|Users|var|workspace)/)", re.I)
_MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\(([^\n]*?)\)")
_CODE_PATH = re.compile(r"`((?:[A-Za-z]:[\\/]|file:|sandbox:|/(?:tmp|mnt|home|Users|var|workspace)/)[^`\n]*)`", re.I)
_BARE_PATH = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|file://|sandbox:/|/(?:tmp|mnt|home|Users|var|workspace)/)[^\s<>\"'`]+", re.I)


def public_assistant_text(value: str) -> str:
    # Native image.ready cards replace these transport links. Never apply to user text.
    value = _MARKDOWN_LINK.sub(lambda match: "" if _LOCAL_START.match(
        match.group(1).strip().lstrip("<")) else match.group(0), value)
    value = _CODE_PATH.sub("", value)
    value = _BARE_PATH.sub("", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def message_search_text(role, parts):
    if role not in {"user", "assistant"}:
        return ""
    values = []
    for part in parts:
        kind = part.get("type")
        if kind == "text":
            value = part.get("text", "")
            if isinstance(value, str):
                values.append(public_assistant_text(value) if role == "assistant" else value)
        elif kind == "references":
            values.extend(item["usage"] for item in part.get("items", [])
                          if isinstance(item.get("usage"), str))
        elif kind == "annotations":
            values.extend(mark[key] for mark in part.get("annotations", []) for key in ("text", "note")
                          if isinstance(mark.get(key), str))
    return "\n".join(values)




def upgrade():
    op.execute("LOCK TABLE v2_designs, v2_messages IN SHARE ROW EXCLUSIVE MODE")
    op.add_column("v2_designs", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE v2_designs d SET last_activity_at=GREATEST(d.created_at, COALESCE((
          SELECT MAX(e.created_at) FROM v2_events e WHERE e.design_id=d.id AND e.event_type IN
          ('session.updated','message.created','message.completed','image.ready','question.opened','question.resolved')
        ), d.created_at))
    """)
    op.alter_column("v2_designs", "last_activity_at", nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))
    op.execute("CREATE SEQUENCE v2_design_catalog_sequence")
    op.add_column("v2_designs",sa.Column("list_sequence",sa.BigInteger(),server_default=sa.text("nextval('v2_design_catalog_sequence')"),nullable=False))
    op.execute("ALTER SEQUENCE v2_design_catalog_sequence OWNED BY v2_designs.list_sequence")
    op.execute("""
        CREATE FUNCTION v2_update_catalog_sequence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.title,NEW.pinned,NEW.archived_at,NEW.deleted_at,NEW.last_activity_at)
             IS DISTINCT FROM ROW(OLD.title,OLD.pinned,OLD.archived_at,OLD.deleted_at,OLD.last_activity_at) THEN
            NEW.list_sequence := nextval('v2_design_catalog_sequence');
          ELSE
            NEW.list_sequence := OLD.list_sequence;
          END IF;
          RETURN NEW;
        END;
        $$
    """)
    op.execute("CREATE TRIGGER v2_design_catalog_changed BEFORE UPDATE ON v2_designs FOR EACH ROW EXECUTE FUNCTION v2_update_catalog_sequence()")
    op.execute("CREATE INDEX ix_v2_design_catalog ON v2_designs(owner_id, archived_at, pinned DESC, last_activity_at DESC, id) WHERE deleted_at IS NULL")
    op.add_column("v2_messages",sa.Column("search_text",sa.Text(),nullable=False,server_default=""))
    connection=op.get_bind()
    # Read/update in bounded batches; original message JSON and identities remain unchanged.
    after=""
    while True:
        rows=connection.execute(sa.text("SELECT id,role,content_parts FROM v2_messages WHERE id>:after ORDER BY id LIMIT 500"),{"after":after}).mappings().all()
        if not rows:
            break
        for row in rows:
            connection.execute(sa.text("UPDATE v2_messages SET search_text=:value WHERE id=:id"),
                               {"id":row["id"],"value":message_search_text(row["role"],row["content_parts"])})
        after=rows[-1]["id"]


def downgrade():
    op.drop_column("v2_messages","search_text")
    op.execute("DROP TRIGGER v2_design_catalog_changed ON v2_designs")
    op.execute("DROP FUNCTION v2_update_catalog_sequence()")
    op.drop_index("ix_v2_design_catalog",table_name="v2_designs")
    op.drop_column("v2_designs","list_sequence")
    op.drop_column("v2_designs","last_activity_at")
