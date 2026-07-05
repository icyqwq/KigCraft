from collections import defaultdict
from typing import Any

_EVENTS: dict[str, list[dict[str, Any]]] = defaultdict(list)


def publish_event(job_id: str, event: dict[str, Any]) -> None:
    _EVENTS[job_id].append(event)


def list_events(job_id: str) -> list[dict[str, Any]]:
    return list(_EVENTS.get(job_id, []))


def clear_events() -> None:
    _EVENTS.clear()
