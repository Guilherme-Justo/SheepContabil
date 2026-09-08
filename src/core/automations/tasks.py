from __future__ import annotations

import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any
from uuid import UUID

from celery import shared_task

from config.trace_context import trace_context
from core.automations.models import RunEventSource, RunEventType
from core.automations.run_tracking import normalize_task_id
from core.automations.sc04.services import execute_sc04
from core.automations.sc05.services import execute_sc05
from core.automations.sc20.services import execute_sc20
from core.automations.traceability import record_run_event

logger = logging.getLogger("sheepcontabil.automations.tasks")


@shared_task(  # type: ignore[untyped-decorator]
    bind=True,
    name="automations.sc04.execute",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_sc04_task(task: Any, run_id: str) -> dict[str, int]:
    result = _execute_with_trace(
        task=task,
        run_id=run_id,
        module_code="SC-04",
        execute=lambda task_id, redelivered: execute_sc04(
            run_id,
            task_id=task_id,
            resume_interrupted=redelivered,
        ),
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
    result = _execute_with_trace(
        task=task,
        run_id=run_id,
        module_code="SC-05",
        execute=lambda task_id, redelivered: execute_sc05(
            run_id,
            task_id=task_id,
            resume_interrupted=redelivered,
        ),
    )
    return {
        "applied": result.applied,
        "unchanged": result.unchanged,
        "compensated": result.compensated,
        "failed": result.failed,
        "partially_failed": result.partially_failed,
    }


@shared_task(  # type: ignore[untyped-decorator]
    bind=True,
    name="automations.sc20.execute",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_sc20_task(task: Any, run_id: str) -> dict[str, int]:
    result = _execute_with_trace(
        task=task,
        run_id=run_id,
        module_code="SC-20",
        execute=lambda task_id, redelivered: execute_sc20(
            run_id,
            task_id=task_id,
            resume_interrupted=redelivered,
        ),
    )
    return {
        "selected": result.selected,
        "sent": result.sent,
        "failed": result.failed,
        "deduplicated": result.deduplicated,
    }


def _execute_with_trace[TaskResult](
    *,
    task: Any,
    run_id: str,
    module_code: str,
    execute: Callable[[str | None, bool], TaskResult],
) -> TaskResult:
    task_id, redelivered = _delivery_context(task)
    started_at = perf_counter()
    persisted_run_id = _normalize_run_id(run_id)
    with trace_context(
        run_id=run_id,
        module_code=module_code,
        task_id=task_id,
        source="worker",
    ):
        normalized_task_id = normalize_task_id(task_id)
        delivery_key = str(normalized_task_id or "without-task")
        if persisted_run_id is not None:
            record_run_event(
                run=persisted_run_id,
                event_type=RunEventType.DELIVERY_RECEIVED,
                source=RunEventSource.WORKER,
                task_id=normalized_task_id,
                outcome=("redelivered" if redelivered else "received"),
                deduplication_key=(
                    f"delivery.received:{delivery_key}:"
                    f"{'redelivered' if redelivered else 'initial'}"
                ),
                message="Entrega Celery recebida pelo worker.",
            )
        logger.info(
            "Entrega Celery recebida pelo worker.",
            extra={
                "event_type": "automation.delivery.received",
                "outcome": "redelivered" if redelivered else "received",
            },
        )
        try:
            result = execute(task_id, redelivered)
        except Exception as exc:
            if persisted_run_id is not None:
                record_run_event(
                    run=persisted_run_id,
                    event_type=RunEventType.DELIVERY_FINISHED,
                    source=RunEventSource.WORKER,
                    task_id=normalized_task_id,
                    outcome="failed",
                    error_code=type(exc).__name__,
                    deduplication_key=(
                        f"delivery.finished:{delivery_key}:failed:{type(exc).__name__}"
                    ),
                    message="Entrega Celery encerrada com falha.",
                )
            logger.exception(
                "Entrega Celery encerrada com falha.",
                extra={
                    "event_type": "automation.delivery.failed",
                    "outcome": "failed",
                    "duration_ms": _elapsed_ms(started_at),
                    "error_code": type(exc).__name__,
                },
            )
            raise
        if persisted_run_id is not None:
            record_run_event(
                run=persisted_run_id,
                event_type=RunEventType.DELIVERY_FINISHED,
                source=RunEventSource.WORKER,
                task_id=normalized_task_id,
                outcome="handled",
                deduplication_key=f"delivery.finished:{delivery_key}:handled",
                message="Entrega Celery processada pelo worker.",
            )
        logger.info(
            "Entrega Celery concluída pelo worker.",
            extra={
                "event_type": "automation.delivery.succeeded",
                "outcome": "succeeded",
                "duration_ms": _elapsed_ms(started_at),
            },
        )
        return result


def _delivery_context(task: Any) -> tuple[str | None, bool]:
    request = task.request
    raw_task_id = getattr(request, "id", None)
    task_id = str(raw_task_id) if raw_task_id else None
    delivery_info = getattr(request, "delivery_info", None) or {}
    return task_id, bool(delivery_info.get("redelivered"))


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _normalize_run_id(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None
