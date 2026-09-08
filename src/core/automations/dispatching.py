from __future__ import annotations

from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from core.automations.models import AutomationRun, RunStatus
from core.automations.tasks import (
    run_sc04_task,
    run_sc05_task,
    run_sc20_task,
)

ASYNC_MODULE_CODES = ("SC-04", "SC-05", "SC-20")


def dispatch_run(
    run: AutomationRun,
    *,
    failure_status: str = RunStatus.FAILED,
    failure_summary: str,
) -> UUID:
    task = _task_for_module(str(run.module_id))
    now = timezone.now()
    with transaction.atomic():
        locked = AutomationRun.objects.select_for_update().get(pk=run.pk)
        if locked.status != RunStatus.QUEUED:
            raise ValueError("Somente uma execução enfileirada pode ser publicada.")
        if locked.broker_published_at is not None or locked.dispatch_started_at is not None:
            if locked.task_id is None:
                raise ValueError("A publicação existente não possui identificador de entrega.")
            return locked.task_id
        task_id = locked.task_id or uuid4()
        locked.task_id = task_id
        locked.queued_at = now
        locked.dispatch_started_at = now
        locked.heartbeat_at = None
        locked.finished_at = None
        locked.save(
            update_fields=(
                "task_id",
                "queued_at",
                "dispatch_started_at",
                "heartbeat_at",
                "finished_at",
            )
        )

    try:
        task.apply_async(args=(str(run.id),), task_id=str(task_id))
    except Exception as exc:
        _record_dispatch_failure(
            run_id=run.id,
            task_id=task_id,
            status=failure_status,
            summary=failure_summary,
            exc=exc,
        )
        raise
    _record_dispatch_success(run_id=run.id, task_id=task_id)
    return task_id


def publish_claimed_run(*, module_code: str, run_id: UUID, task_id: UUID) -> None:
    _task_for_module(module_code).apply_async(args=(str(run_id),), task_id=str(task_id))


def confirm_claimed_run(*, run_id: UUID, task_id: UUID) -> None:
    _record_dispatch_success(run_id=run_id, task_id=task_id)


def _task_for_module(module_code: str):  # type: ignore[no-untyped-def]
    tasks = {
        "SC-04": run_sc04_task,
        "SC-05": run_sc05_task,
        "SC-20": run_sc20_task,
    }
    try:
        return tasks[module_code]
    except KeyError as exc:
        raise ValueError(f"O módulo {module_code} não possui execução assíncrona.") from exc


def _record_dispatch_failure(
    *,
    run_id: UUID,
    task_id: UUID,
    status: str,
    summary: str,
    exc: Exception,
) -> None:
    now = timezone.now()
    with transaction.atomic():
        run = (
            AutomationRun.objects.select_for_update()
            .filter(pk=run_id, task_id=task_id, status=RunStatus.QUEUED)
            .first()
        )
        if run is None:
            return
        run.status = status
        run.summary = summary
        run.error_message = "O serviço de execução está temporariamente indisponível."
        run.metadata = {**run.metadata, "dispatch_error": type(exc).__name__}
        run.finished_at = now
        run.save(
            update_fields=(
                "status",
                "summary",
                "error_message",
                "metadata",
                "finished_at",
            )
        )


def _record_dispatch_success(*, run_id: UUID, task_id: UUID) -> None:
    AutomationRun.objects.filter(
        pk=run_id,
        task_id=task_id,
        broker_published_at__isnull=True,
    ).update(broker_published_at=timezone.now())
