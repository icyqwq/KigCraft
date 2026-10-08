"""Legacy execution/selection evidence is not a V2 Operation state."""
from enum import Enum


def legacy_status(status, runtime_status, accepted_output_index, output_indexes):
    stored = status.value if isinstance(status, Enum) else str(status)
    runtime = str(runtime_status or "")
    execution = {
        "queued": "queued", "blocked_by_quota": "blocked", "running": "running",
        "uploading_outputs": "publishing", "succeeded": "completed",
        "failed": "failed", "cancelled": "cancelled", "expired": "expired",
    }.get(stored, "unknown")
    terminal = None if execution == "unknown" else execution in {"completed", "failed", "cancelled", "expired"}
    issues = []
    selection = "none"
    if runtime == "accepted" or accepted_output_index is not None:
        matches = sum(value == accepted_output_index for value in output_indexes)
        if (stored != "succeeded" or runtime != "accepted" or
                type(accepted_output_index) is not int or matches != 1):
            selection = "unverified"
            issues.append("selection_conflict")
        else:
            selection = "confirmed"
    if stored == "succeeded" and not output_indexes:
        issues.append("completed_without_output_record")
    if runtime in {"failed", "cancelled", "expired"} and runtime != stored:
        issues.append("execution_status_conflict")
    return {"execution": execution, "terminal": terminal, "selection": selection,
            "stored_status": stored, "runtime_status": runtime, "issues": issues}
