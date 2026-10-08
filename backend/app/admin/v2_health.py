"""Read-only operational signals. Unknown telemetry is never reported healthy."""
import asyncio
import shutil
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select, func, or_
from app.core.time import utc_now
from app.design.models import Operation, Wallet, LedgerEntry

CODES = ("expired_leases", "publishing_backlog", "unresolved_operations", "wallet_mismatch",
         "review_failures", "storage_capacity", "runtime_identity", "duplicate_outputs",
         "access_denials")


def signal(code, count, *, threshold=1, samples=(), severity="warning"):
    return {"code": code, "status": "unknown" if count is None else "active" if count >= threshold else "clear",
            "severity": severity, "count": count, "threshold": threshold, "operation_ids": list(samples)}


def storage_signal(settings):
    if settings.v2_asset_backend == "minio":
        return {**signal("storage_capacity", None), "free_bytes": None, "scope": "minio"}
    try:
        root = Path(settings.v2_storage_dir)
        if not root.is_dir():
            return {**signal("storage_capacity", None), "free_bytes": None}
        free = shutil.disk_usage(root).free
        minimum = settings.v2_alert_storage_free_bytes
        return {**signal("storage_capacity", int(free < minimum), severity="critical"),
                "free_bytes": free, "minimum_free_bytes": minimum}
    except OSError:
        return {**signal("storage_capacity", None), "free_bytes": None}


async def overview(db, settings, *, now=None):
    now = now or utc_now()
    cutoff = now - timedelta(seconds=settings.v2_alert_stalled_seconds)
    recent = now - timedelta(seconds=settings.v2_alert_recent_seconds)
    signals = []

    async def operations(code, condition, *, threshold=1, severity="warning"):
        # Count and samples share one statement snapshot, including concurrent completions.
        counted = select(Operation.id, Operation.updated_at, func.count().over().label("total")).where(condition).subquery()
        rows = (await db.execute(select(counted.c.id, counted.c.total)
                .order_by(counted.c.updated_at, counted.c.id).limit(5))).all()
        signals.append(signal(code, rows[0].total if rows else 0, threshold=threshold,
                              samples=[row.id for row in rows], severity=severity))

    await operations("expired_leases",
        Operation.state.in_(["reviewing","running","publishing","cancelling","reconciling"]) &
        Operation.lease_expires_at.is_not(None) & (Operation.lease_expires_at < now),
        threshold=settings.v2_alert_expired_leases)
    await operations("publishing_backlog", (Operation.state=="publishing") & (Operation.updated_at < cutoff))
    await operations("unresolved_operations", Operation.state.in_(["reconciling","cancelling"]) &
                     (Operation.updated_at < cutoff))
    # Compare ledger sums and current wallet in one SQL snapshot, with no locks or top-up writes.
    ledger = select(LedgerEntry.owner_id, func.sum(LedgerEntry.balance_delta).label("balance"),
                    func.sum(LedgerEntry.reserved_delta).label("reserved")).group_by(LedgerEntry.owner_id).subquery()
    inconsistent = await db.scalar(select(func.count()).select_from(Wallet)
        .outerjoin(ledger, ledger.c.owner_id==Wallet.owner_id).where(Wallet.write_owner=="v2",
            or_(Wallet.balance != func.coalesce(ledger.c.balance,0),
                Wallet.reserved != func.coalesce(ledger.c.reserved,0))))
    signals.append(signal("wallet_mismatch", inconsistent, severity="critical"))
    # Count the most recent consecutive terminal failures, bounded by the configured threshold.
    latest = list(await db.scalars(select(Operation.error_code).where(
        Operation.state.in_(["failed","succeeded","partial_success","awaiting_input"]),
        Operation.updated_at >= recent).order_by(Operation.updated_at.desc(),Operation.id.desc())
        .limit(settings.v2_alert_review_failures)))
    streak = 0
    for error in latest:
        if error != "moderation_unavailable":
            break
        streak += 1
    signals.append(signal("review_failures", streak, threshold=settings.v2_alert_review_failures))
    if settings.v2_asset_backend == "minio":
        from app.admin.minio_capacity import capacity_signal
        signals.append(await capacity_signal(settings, now=now))
    else:
        signals.append(await asyncio.to_thread(storage_signal, settings))
    await operations("runtime_identity", (Operation.updated_at >= recent) & Operation.error_code.in_(
        ["runtime_identity_changed","runtime_binding_unavailable","runtime_binding_changed"]), severity="critical")
    from app.admin.image_alerts import duplicate_outputs
    duplicate = await duplicate_outputs(db, recent)
    signals.append(signal("duplicate_outputs",duplicate["count"],samples=duplicate["operation_ids"]))
    from app.admin.access_observations import recent_denials
    denials = await recent_denials(db, settings, now=now)
    # Known recorded incidents remain actionable even if later observation failed.
    count = denials["recorded_count"] if denials["recorded_count"] >= settings.v2_alert_access_denials else denials["count"]
    signals.append({**signal("access_denials",count,threshold=settings.v2_alert_access_denials,severity="critical"),
                    "coverage":denials["coverage"], "categories":denials["categories"]})
    return {"checked_at": now, "stalled_seconds": settings.v2_alert_stalled_seconds,
            "recent_seconds": settings.v2_alert_recent_seconds, "signals": signals}
