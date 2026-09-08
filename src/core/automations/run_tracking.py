from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils import timezone

from core.automations.models import AutomationRun, RunStatus


class SupersededDelivery(Exception):
    """Internal signal that a newer delivery owns the persisted run."""


def normalize_task_id(task_id: str | UUID | None) -> UUID | None:
    if task_id is None:
        return None
    try:
        return UUID(str(task_id))
    except (TypeError, ValueError):
        return None


def delivery_matches(run: AutomationRun, task_id: str | UUID | None) -> bool:
    if task_id is None:
        return True
    supplied = normalize_task_id(task_id)
    if run.task_id is None:
        return supplied is not None
    return supplied == run.task_id


def bind_delivery(run: AutomationRun, task_id: str | UUID | None) -> bool:
    if not delivery_matches(run, task_id):
        return False
    supplied = normalize_task_id(task_id)
    if run.task_id is None and supplied is not None:
        run.task_id = supplied
    return True


def touch_run(
    run_id: UUID | str,
    *,
    task_id: str | UUID | None,
    at: datetime | None = None,
) -> bool:
    query = AutomationRun.objects.filter(pk=run_id, status=RunStatus.RUNNING)
    supplied = normalize_task_id(task_id)
    if supplied is not None:
        query = query.filter(task_id=supplied)
    elif task_id is not None:
        return False
    else:
        query = query.filter(task_id__isnull=True)
    return bool(query.update(heartbeat_at=at or timezone.now()))


def delivery_is_current(
    run_id: UUID | str,
    *,
    task_id: str | UUID | None,
) -> bool:
    query = AutomationRun.objects.filter(pk=run_id, status=RunStatus.RUNNING)
    supplied = normalize_task_id(task_id)
    if supplied is not None:
        query = query.filter(task_id=supplied)
    elif task_id is not None:
        return False
    else:
        query = query.filter(task_id__isnull=True)
    return query.exists()


def require_current_delivery(
    run_id: UUID | str,
    *,
    task_id: str | UUID | None,
) -> None:
    if not delivery_is_current(run_id, task_id=task_id):
        raise SupersededDelivery


def with_reconciliation_event(
    metadata: dict[str, Any],
    *,
    action: str,
    reason: str,
    at: datetime,
    previous_task_id: UUID | None,
    details: dict[str, object] | None = None,
) -> dict[str, Any]:
    raw_history = metadata.get("reconciliation_history")
    history = list(raw_history) if isinstance(raw_history, list) else []
    event: dict[str, object] = {
        "action": action,
        "reason": reason,
        "at": at.isoformat(),
        "previous_task_id": str(previous_task_id) if previous_task_id else None,
    }
    if details:
        event.update(details)
    history.append(event)
    return {**metadata, "reconciliation_history": history[-20:]}
