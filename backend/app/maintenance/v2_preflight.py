"""Read-only local release preflight. Never starts an App Server or model turn."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.codex_runtime.launch import DESIGN_MODEL, TESTED_CODEX_VERSION
from app.codex_runtime.local_runtime import LocalRuntime
from app.core.config import Settings
from app.db.v2_schema import V2_SCHEMA_VERSION, require_v2_schema


LOCAL_TRIAL_CANDIDATE_VERSION = "0.159.0-alpha.12.1"


class PreflightError(ValueError):
    pass


def validate_settings(settings: Settings) -> None:
    if not settings.v2_enabled:
        raise PreflightError("v2_disabled")
    if settings.app_env not in {"development", "test"} or settings.v2_runtime_mode != "local":
        raise PreflightError("local_trial_only")
    if not settings.v2_local_user_id:
        raise PreflightError("test_owner_required")
    if not settings.v2_review_model.strip() or settings.v2_review_model == DESIGN_MODEL:
        raise PreflightError("independent_review_model_required")
    candidate = (settings.v2_local_trial_candidate_enabled
                 and settings.v2_codex_version == LOCAL_TRIAL_CANDIDATE_VERSION)
    if settings.v2_codex_version != TESTED_CODEX_VERSION and not candidate:
        raise PreflightError("untested_codex_version")
    if not Path(settings.v2_codex_path).is_absolute():
        raise PreflightError("absolute_codex_path_required")
    if settings.codex_update_enabled or settings.codex_session_cleanup_enabled:
        raise PreflightError("legacy_maintenance_must_be_disabled")
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise PreflightError("postgresql_required")
    roots = [Path(value) for value in (settings.v2_storage_dir, settings.v2_runtime_dir)]
    if any(not root.is_absolute() or not root.is_dir() or root.is_symlink()
           or root.is_junction() for root in roots):
        raise PreflightError("existing_absolute_data_roots_required")
    left, right = (root.resolve() for root in roots)
    if left.is_relative_to(right) or right.is_relative_to(left):
        raise PreflightError("separate_data_roots_required")


async def inspect_release(settings: Settings) -> dict:
    validate_settings(settings)
    try:
        runtime = LocalRuntime(settings)
        await runtime.verify()  # --version only; no initialize/account/read/turn/start.
    except Exception:
        raise PreflightError("pinned_runtime_unavailable") from None
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        try:
            schema = await require_v2_schema(settings=settings, engine=engine)
        except Exception:
            raise PreflightError("schema_unavailable_or_incompatible") from None
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            owner = (await connection.execute(text(
                "SELECT id FROM users WHERE id = :owner AND is_banned = false"
            ), {"owner": settings.v2_local_user_id})).scalar_one_or_none()
            if owner is None:
                raise PreflightError("active_test_owner_required")
    finally:
        await engine.dispose()
    return {
        "status": "verified_local_configuration",
        "schema_version": V2_SCHEMA_VERSION,
        "schema_structure": schema,
        "codex_version": settings.v2_codex_version,
        "local_trial_candidate": settings.v2_codex_version != TESTED_CODEX_VERSION,
        "design_model": DESIGN_MODEL,
        "review_model": settings.v2_review_model,
        "new_operations_enabled": settings.v2_new_operations,
        "automatic_reconciliation_enabled": settings.v2_auto_reconcile_enabled,
        "model_turns_started": 0,
        "not_verified": ["login_and_model_availability", "database_privileges",
                         "native_image_tool_budget", "production_isolation",
                         "backup_completeness", "container_deployment"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True,
                        help="Explicit private configuration; process environment takes precedence")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise PreflightError("configuration_file_missing")
        settings = Settings(_env_file=args.env_file)
        result = asyncio.run(asyncio.wait_for(inspect_release(settings), timeout=45))
    except Exception as exc:
        # Do not expose validation values, connection URLs, paths or credentials.
        code = str(exc) if isinstance(exc, PreflightError) else "preflight_unavailable"
        print(json.dumps({"status": "refused", "code": code}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
