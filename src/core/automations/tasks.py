from __future__ import annotations

from typing import Any

from celery import shared_task

from core.automations.sc04.services import execute_sc04
from core.automations.sc05.services import execute_sc05
from core.automations.sc20.services import execute_sc20


@shared_task(  # type: ignore[untyped-decorator]
    bind=True,
    name="automations.sc04.execute",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_sc04_task(task: Any, run_id: str) -> dict[str, int]:
    delivery_info = getattr(task.request, "delivery_info", None) or {}
    result = execute_sc04(
        run_id,
        resume_interrupted=bool(delivery_info.get("redelivered")),
    )
    return {
        "received": result.received,
        "routed": result.routed,
        "awaiting_review": result.awaiting_review,
        "duplicates": result.duplicates,
        "failed": result.failed,
    }


@shared_task(  # type: ignore[untyped-decorator]
    bind=True,
    name="automations.sc05.execute",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_sc05_task(task: Any, run_id: str) -> dict[str, int | bool]:
    delivery_info = getattr(task.request, "delivery_info", None) or {}
    result = execute_sc05(
        run_id,
        resume_interrupted=bool(delivery_info.get("redelivered")),
    )
    return {
        "applied": result.applied,
        "unchanged": result.unchanged,
        "compensated": result.compensated,
        "failed": result.failed,
        "partially_failed": result.partially_failed,
    }


@shared_task(name="automations.sc20.execute")  # type: ignore[untyped-decorator]
def run_sc20_task(run_id: str) -> dict[str, int]:
    result = execute_sc20(run_id)
    return {
        "selected": result.selected,
        "sent": result.sent,
        "failed": result.failed,
        "deduplicated": result.deduplicated,
    }
