"""Shared runtime admission for the API and explicitly account-bound workers."""
from uuid import UUID
from app.codex_runtime.rpc import RpcError
from app.codex_runtime.container_policy import ContainerSpec
from app.design.service import ApplicationError


def container_spec(settings, owner):
    if owner not in settings.v2_container_user_ids:
        raise ApplicationError("runtime_not_configured")
    return ContainerSpec(owner, settings.v2_container_root, settings.v2_container_image,
        settings.v2_egress_image, tuple(sorted(settings.v2_egress_hosts)),
        settings.v2_container_uid, settings.v2_container_gid)


# One Codex login and one worker serve every account, as V1 does. The worker is not bound to an owner.
SHARED_OWNER = "*"


def owner_clause(column, owner):
    """SQL filter for the accounts a worker serves: one account, or all of them in shared mode."""
    from sqlalchemy import true
    return true() if owner == SHARED_OWNER else column == owner


def serves(owner, operation_owner) -> bool:
    return owner == SHARED_OWNER or owner == operation_owner


def runtime_allowed(settings, owner=None):
    if settings.v2_runtime_mode == "shared":
        return bool(settings.v2_enabled)
    if settings.v2_runtime_mode == "local":
        return (settings.app_env in {"development", "test"} and bool(settings.v2_local_user_id)
                and (owner is None or owner == settings.v2_local_user_id))
    if settings.v2_runtime_mode != "container":
        return False
    try:
        owners = settings.v2_container_user_ids
        if not owners or len(owners) > 1000 or len(set(owners)) != len(owners):
            return False
        for identity in owners:
            if str(UUID(identity)) != identity:
                return False
        container_spec(settings, owner or owners[0])
        return True
    except (ValueError, TypeError, AttributeError, RpcError):
        return False


def worker_owner(settings):
    if settings.v2_runtime_mode == "shared":
        return SHARED_OWNER
    return settings.v2_worker_user_id if settings.v2_runtime_mode == "container" else settings.v2_local_user_id


def make_runtime(settings):
    if not worker_owner(settings) or not runtime_allowed(settings, worker_owner(settings)):
        raise ApplicationError("runtime_not_configured")
    if settings.v2_runtime_mode == "container":
        from app.codex_runtime.container_runtime import ContainerRuntime
        return ContainerRuntime(settings)
    from app.codex_runtime.local_runtime import LocalRuntime
    return LocalRuntime(settings)  # also serves shared mode


def execution_owner(worker):
    configured = worker_owner(worker.settings)
    bound = getattr(worker, "owner_id", configured)
    if not bound or bound != configured:
        raise ApplicationError("runtime_not_configured")
    return bound


def validate_account_service(settings, expected_owner):
    """Bind a production service instance independently of environment overrides."""
    if (settings.app_env != "production" or not settings.v2_enabled
        or settings.v2_runtime_mode != "container"
        or worker_owner(settings) != expected_owner
        or not runtime_allowed(settings, expected_owner)
        or settings.codex_update_enabled or settings.codex_session_cleanup_enabled):
        raise ApplicationError("container_service_configuration_mismatch")
