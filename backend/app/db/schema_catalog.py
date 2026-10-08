"""PostgreSQL structure only: no table contents, credentials, owners or sequence values."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import text

MANIFEST_PATH = Path(__file__).with_name("v2_schema_manifest.json")


def signature(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


# PG14 pg_dump/restore distributes a varchar-array -> text[] cast over its
# constant members. Normalize only that exact built-in, unbounded cast of simple
# enum-like literals; retain every predicate, member, ordering and other cast.
_RESTORED_TEXT_ARRAY = re.compile(
    r"ARRAY\[(?P<members>\('[a-z_]+'::character varying\)::text"
    r"(?:, \('[a-z_]+'::character varying\)::text)*)\]"
)


def stable_definition(value: str) -> str:
    result = []
    index = 0
    while index < len(value):
        if value[index] in {"'", '"'}:
            # Quoted strings/identifiers are opaque: never normalize their contents.
            quote, start = value[index], index
            escaped = quote == "'" and index > 0 and value[index - 1] in "eE" and (
                index == 1 or not (value[index - 2].isalnum() or value[index - 2] in "_$")
            )
            index += 1
            while index < len(value):
                if escaped and value[index] == "\\":
                    index += 2
                    continue
                if value[index] == quote:
                    index += 1
                    if index < len(value) and value[index] == quote:
                        index += 1
                        continue
                    break
                index += 1
            result.append(value[start:index])
            continue
        match = _RESTORED_TEXT_ARRAY.match(value, index)
        if match and (index == 0 or not (value[index - 1].isalnum() or value[index - 1] in "_$")):
            members = re.findall(r"'([a-z_]+)'", match["members"])
            result.append("(ARRAY[" + ", ".join("'" + item + "'::character varying" for item in members) + "])::text[]")
            index = match.end()
        else:
            result.append(value[index])
            index += 1
    return "".join(result)


async def read_catalog(connection) -> dict:
    # Stabilize deparsing. This is transaction-local and does not change role/database settings.
    await connection.execute(text("SET LOCAL search_path TO pg_catalog, public"))
    tables = {}
    rows = (await connection.execute(text("""
        SELECT c.relname AS name, c.relkind::text AS kind, c.relpersistence::text AS persistence,
               c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls,
               c.relreplident::text AS replica_identity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
        ORDER BY c.relname
    """))).mappings()
    for row in rows:
        tables[row["name"]] = {"relation": signature(dict(row)), "columns": {}, "constraints": {},
                              "indexes": {}, "triggers": {}, "rules": {}, "policies": {},
                              "inheritance": {}}

    queries = {
        "columns": """
            SELECT c.relname AS relation, a.attname AS name,
                   format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS not_null,
                   pg_get_expr(d.adbin,d.adrelid,false) AS default_expression,
                   a.attidentity::text AS identity, a.attgenerated::text AS generated,
                   CASE WHEN a.attcollation=0 THEN NULL ELSE a.attcollation::regcollation::text END AS collation
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped
              AND c.relkind IN ('r','p','v','m','f')
        """,
        "constraints": """
            SELECT c.relname AS relation, x.conname AS name, x.contype::text AS type,
                   pg_get_constraintdef(x.oid,false) AS definition, x.convalidated AS validated,
                   x.condeferrable AS deferrable, x.condeferred AS deferred,
                   x.connoinherit AS no_inherit
            FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        """,
        "indexes": """
            SELECT c.relname AS relation, ic.relname AS name,
                   pg_get_indexdef(i.indexrelid,0,false) AS definition,
                   i.indisunique AS unique, i.indisvalid AS valid, i.indisready AS ready,
                   i.indislive AS live, i.indimmediate AS immediate
            FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
            JOIN pg_class ic ON ic.oid=i.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        """,
        "triggers": """
            SELECT c.relname AS relation,
                   CASE WHEN t.tgisinternal THEN cc.relname || '.' || x.conname || ':' || t.tgtype::text || ':' || t.tgfoid::regprocedure::text
                        ELSE t.tgname::text END AS name,
                   t.tgenabled::text AS enabled, t.tgisinternal AS internal,
                   t.tgtype AS type, t.tgfoid::regprocedure::text AS function,
                   CASE WHEN t.tgisinternal THEN NULL ELSE pg_get_triggerdef(t.oid,false) END AS definition,
                   t.tgdeferrable AS deferrable, t.tginitdeferred AS deferred
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_constraint x ON x.oid=t.tgconstraint
            LEFT JOIN pg_class cc ON cc.oid=x.conrelid
            WHERE n.nspname='public'
        """,
        "rules": """
            SELECT c.relname AS relation, r.rulename AS name,
                   pg_get_ruledef(r.oid,false) AS definition, r.ev_enabled::text AS enabled
            FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        """,
        "policies": """
            SELECT c.relname AS relation, p.polname AS name, p.polcmd::text AS command,
                   p.polpermissive AS permissive, p.polroles::text AS roles,
                   pg_get_expr(p.polqual,p.polrelid,false) AS using_expression,
                   pg_get_expr(p.polwithcheck,p.polrelid,false) AS check_expression
            FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        """,
        "inheritance": """
            SELECT c.relname AS relation, i.inhseqno::text AS name,
                   i.inhparent::regclass::text AS parent
            FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
        """,
    }
    for section, query in queries.items():
        for raw in (await connection.execute(text(query))).mappings():
            row = dict(raw)
            relation, name = row.pop("relation"), row.pop("name")
            if section in {"constraints", "indexes"}:
                row["definition"] = stable_definition(row["definition"])
            if relation not in tables or name is None or name in tables[relation][section]:
                raise RuntimeError("Ambiguous PostgreSQL schema catalog")
            tables[relation][section][name] = (
                {"signature": signature(row), "unique": row["unique"]} if section == "indexes"
                else signature(row)
            )

    functions = {}
    for row in (await connection.execute(text("""
        SELECT p.oid::regprocedure::text AS name, pg_get_functiondef(p.oid) AS definition,
               p.prosecdef AS security_definer, p.proconfig AS config
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname LIKE 'v2\\_%' ESCAPE '\\'
          AND p.prokind IN ('f','p')
    """))).mappings():
        functions[row["name"]] = signature(dict(row))
    enums = {}
    for row in (await connection.execute(text("""
        SELECT t.typname AS name, array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
        FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid
        JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
        GROUP BY t.typname
    """))).mappings():
        enums[row["name"]] = signature(list(row["labels"]))
    sequences = {}
    for row in (await connection.execute(text("""
        SELECT c.relname AS name, format_type(s.seqtypid,NULL) AS type,
               s.seqstart AS start, s.seqincrement AS increment, s.seqmax AS maximum,
               s.seqmin AS minimum, s.seqcache AS cache, s.seqcycle AS cycle
        FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
          AND c.relname LIKE 'v2\\_%' ESCAPE '\\'
    """))).mappings():
        sequences[row["name"]] = signature(dict(row))
    return {"tables": tables, "functions": functions, "enums": enums, "sequences": sequences}


def differences(actual: dict, expected: dict) -> list[str]:
    """Allow unrelated tables and extra non-unique performance indexes only."""
    issues = []
    for name, wanted in expected["tables"].items():
        current = actual["tables"].get(name)
        if current is None:
            issues.append(f"{name}:missing")
            continue
        for section, content in wanted.items():
            got = current[section]
            if section == "indexes":
                if any(got.get(key) != value for key, value in content.items()) or any(
                    value["unique"] for key, value in got.items() if key not in content
                ):
                    issues.append(f"{name}:indexes")
            elif got != content:
                issues.append(f"{name}:{section}")
    if any(name.startswith("v2_") and name not in expected["tables"] for name in actual["tables"]):
        issues.append("unexpected_v2_relation")
    for section in ("functions", "sequences"):
        if actual[section] != expected[section]:
            issues.append(section)
    if any(actual["enums"].get(name) != value for name, value in expected["enums"].items()):
        issues.append("enums")
    return issues


async def validate_catalog(connection, version: str) -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["format"] != 1 or manifest["schema_version"] != version:
        raise RuntimeError("V2 schema manifest is incompatible with this application")
    catalog = manifest["catalog"]
    if signature(catalog) != manifest["sha256"]:
        raise RuntimeError("V2 schema manifest is corrupt")
    major = int((await connection.execute(text("SHOW server_version_num"))).scalar_one()) // 10000
    if major not in manifest["verified_postgresql_majors"]:
        raise RuntimeError("PostgreSQL major version requires a reviewed V2 schema manifest")
    role = (await connection.execute(text("SHOW session_replication_role"))).scalar_one()
    shadowed = (await connection.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM unnest(CAST(:names AS text[])) AS t(name)
            WHERE to_regclass(quote_ident(name)) IS DISTINCT FROM
                  to_regclass('public.' || quote_ident(name))
        )
    """), {"names": list(catalog["tables"])})).scalar_one()
    if role not in {"origin", "local"} or shadowed:
        raise RuntimeError("V2 database session bypasses the required schema")
    issues = differences(await read_catalog(connection), catalog)
    if issues:
        # Labels come from the checked-in manifest; never print actual SQL/defaults/values.
        raise RuntimeError("V2 schema drift: " + ", ".join(issues[:12]))
    return {"sha256": manifest["sha256"], "tables": len(catalog["tables"]),
            "postgresql_major": major}
