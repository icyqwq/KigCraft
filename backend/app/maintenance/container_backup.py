"""Quiescent multi-account backups; container inspection is read-only, never starts a model."""
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import UUID
from sqlalchemy import JSON, select
from app.codex_runtime.container_runtime import ContainerRuntime
from app.codex_runtime.selection import runtime_allowed
from app.db.base import Base
from app.design.models import AdminAction, Branch, CodexEpoch, Design, Operation
from app.maintenance import backup_bundle as backup, backup_assets
from app.maintenance.backup_bundle import BackupError, canonical_root
from app.maintenance.codex_snapshot import create_snapshot, verify_snapshot

async def rows(db, query):
    result = (await db.execute(query.limit(backup.MAX_ROWS + 1))).all()
    if len(result) > backup.MAX_ROWS:
        raise BackupError("backup_inventory_too_large")
    return result


async def owned_threads(db, *, include_retired=False):
    """Resolve only relational ownership; never infer it from native filenames."""
    result = {}
    def bind(owner, identities):
        for identity in identities:
            try:
                if str(UUID(identity)) != identity:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise BackupError("invalid_native_dependency") from None
            if identity in result and result[identity] != owner:
                raise BackupError("cross_account_native_dependency")
            result[identity] = owner
    for owner, identity in await rows(db, select(Design.owner_id, CodexEpoch.thread_id)
        .select_from(CodexEpoch).join(Branch, Branch.id == CodexEpoch.branch_id)
        .join(Design, Design.id == Branch.design_id)):
        bind(owner, [identity])
    for owner, identity in await rows(db, select(Operation.owner_id, Operation.upstream_thread_id)
                                     .where(Operation.upstream_thread_id.is_not(None))):
        bind(owner, [identity])
    # Structured checkpoints can reference a fork before its epoch is installed.
    for table in Base.metadata.tables.values():
        if not table.name.startswith("v2_") or table.name == AdminAction.__tablename__:
            continue
        query = select().select_from(table)
        if "owner_id" in table.c:
            owner = table.c.owner_id
        elif "design_id" in table.c:
            owner = Design.owner_id
            query = query.join(Design, Design.id == table.c.design_id)
        elif "branch_id" in table.c:
            owner = Design.owner_id
            query = query.join(Branch, Branch.id == table.c.branch_id).join(Design, Design.id == Branch.design_id)
        elif "operation_id" in table.c:
            owner = Operation.owner_id
            query = query.join(Operation, Operation.id == table.c.operation_id)
        else:
            continue
        for column in table.columns:
            if isinstance(column.type, JSON):
                for account, value in await rows(db, query.with_only_columns(owner, column)):
                    identities = backup.thread_references(value)
                    if identities and account is None:
                        raise BackupError("unmapped_native_dependency")
                    bind(account, identities)
    if not include_retired:
        from app.maintenance.native_history import retired_threads
        for identity in await retired_threads(db): result.pop(identity, None)
    return result


def account_inventory(base, owners):
    """No omitted old account directories or unregistered operator workspaces."""
    actual = set()
    for path in base.iterdir():
        if path.name == ".docker-client":
            if path.is_symlink() or path.is_junction() or not path.is_dir() or any(path.iterdir()):
                raise BackupError("unexpected_docker_configuration")
            continue
        if path.is_symlink() or path.is_junction() or not path.is_dir() or path.name not in owners:
            raise BackupError("unregistered_runtime_account")
        actual.add(path.name)
        names = {entry.name for entry in path.iterdir()}
        if names - {"profile", "work", "inputs", ".kigcraft-runtime-locks"}:
            raise BackupError("unregistered_account_directory")
    if actual != set(owners):
        raise BackupError("runtime_account_missing")


async def create_container_bundle(db, settings, destination, roots, *, writers_stopped=False,
                                  pg_dump="pg_dump", dump=backup.dump_database,
                                  runtime_factory=ContainerRuntime):
    if not writers_stopped:
        raise BackupError("writers_stopped_acknowledgement_required")
    if (not settings.v2_enabled or settings.app_env != "production"
        or settings.v2_runtime_mode != "container" or not runtime_allowed(settings)):
        raise BackupError("production_container_configuration_required")
    if "assets" not in roots or set(roots) - {"assets", "legacy_generated", "legacy_references"}:
        raise BackupError("invalid_storage_roots")
    asset_root = backup_assets.cache_root if settings.v2_asset_backend == "minio" else canonical_root
    roots = {key: (asset_root(value) if key == "assets" else canonical_root(value)) for key, value in roots.items()}
    if roots["assets"] != asset_root(settings.v2_storage_dir):
        raise BackupError("storage_configuration_mismatch")
    base = canonical_root(settings.v2_container_root)
    sources = [base, *roots.values()]
    for index, left in enumerate(sources):
        for right in sources[index + 1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise BackupError("overlapping_storage_roots")
    destination = backup.new_destination(destination, sources)
    owners = sorted(settings.v2_container_user_ids)
    account_inventory(base, owners)
    accounts, runtimes, metadata = {}, {}, {}
    extra = {"accounts": metadata, "runtime_configuration": {
        "root": settings.v2_container_root, "image": settings.v2_container_image,
        "egress_image": settings.v2_egress_image, "hosts": sorted(settings.v2_egress_hosts),
        "uid": settings.v2_container_uid, "gid": settings.v2_container_gid,
        "credentials_included": False}}
    asset_repository = backup_assets.repository(settings)
    async with AsyncExitStack() as stack:
        # Deterministic order; a failed acquisition releases previously acquired locks.
        for owner in owners:
            runtime = runtime_factory(settings.model_copy(update={"v2_worker_user_id": owner}))
            await stack.enter_async_context(runtime.operation())
            runtimes[owner] = runtime
            accounts[owner] = {"work": canonical_root(runtime.work_root),
                               "inputs": canonical_root(runtime.input_root)}
            metadata[owner] = {"native_threads": [], "daemon_id": runtime.daemon_id}
        async def read_dependencies(db, roots):
            account_inventory(base, owners)
            files, threads, absent = await backup.dependencies(db, roots, accounts=accounts, asset_repository=asset_repository)
            assignments = await owned_threads(db)
            if set(assignments) != set(threads):
                raise BackupError("unmapped_native_dependency")
            for owner in owners:
                selected = sorted(identity for identity, account in assignments.items() if account == owner)
                if metadata[owner]["native_threads"] and metadata[owner]["native_threads"] != selected:
                    raise BackupError("native_dependency_changed")
                metadata[owner]["native_threads"] = selected
            if set(assignments.values()) - set(owners):
                raise BackupError("runtime_account_missing")
            for runtime in runtimes.values():
                await runtime.verify_stopped()
            return files, threads, absent
        async def write_native(destination, threads):
            for owner, entry in metadata.items():
                if entry["native_threads"]:
                    create_snapshot(runtimes[owner].profile, destination/"accounts"/owner/"native",
                                    entry["native_threads"], version=settings.v2_codex_version)
        return await backup.assemble_bundle(db, settings, destination, roots,
            pg_dump=pg_dump, dump=dump, read_dependencies=read_dependencies, write_native=write_native,
            scope=backup.CONTAINER_SCOPE, extra_manifest=extra)


def verify_accounts(root, manifest, files):
    accounts = manifest.get("accounts")
    if not isinstance(accounts, dict) or not 1 <= len(accounts) <= 1000:
        raise BackupError("invalid_backup_accounts")
    threads = set()
    for owner, entry in accounts.items():
        try:
            if str(UUID(owner)) != owner or not isinstance(entry, dict):
                raise ValueError()
            selected = entry["native_threads"]
            if not isinstance(selected, list) or selected != sorted(set(selected)):
                raise ValueError()
            if any(str(UUID(identity)) != identity for identity in selected):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, KeyError):
            raise BackupError("invalid_backup_accounts") from None
        if threads & set(selected):
            raise BackupError("cross_account_native_dependency")
        threads.update(selected)
        prefix = f"accounts/{owner}/native/"
        if selected:
            native = verify_snapshot(root/"accounts"/owner/"native", version=manifest["codex_version"])
            if sorted(native["threads"]) != selected:
                raise BackupError("native_dependency_mismatch")
        elif any(key.startswith(prefix) for key in files):
            raise BackupError("unexpected_native_data")
    if sorted(threads) != manifest["native_threads"]:
        raise BackupError("native_dependency_mismatch")
    for key in files:
        if key.startswith("accounts/"):
            parts = key.split("/")
            if len(parts) < 4 or parts[1] not in accounts or parts[2] not in {"work", "inputs", "native"}:
                raise BackupError("unexpected_account_data")
        elif key != "database.dump" and not key.startswith(("assets/", "legacy_generated/", "legacy_references/")):
            raise BackupError("unexpected_backup_data")
    configuration = manifest.get("runtime_configuration", {})
    if configuration.get("credentials_included") is not False:
        raise BackupError("unexpected_account_credentials")
    # Validate the retained non-secret execution manifest; it never authorizes restore.
    from app.codex_runtime.container_policy import ContainerSpec
    try:
        for owner in accounts:
            ContainerSpec(owner, configuration["root"], configuration["image"], configuration["egress_image"],
                          tuple(configuration["hosts"]), configuration["uid"], configuration["gid"])
    except Exception:
        raise BackupError("invalid_backup_runtime_configuration") from None
