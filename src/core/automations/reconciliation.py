from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.automations.dispatching import (
    ASYNC_MODULE_CODES,
    confirm_claimed_run,
    publish_claimed_run,
)
from core.automations.models import (
    AutomationRun,
    ClassificationAttemptStatus,
    DocumentClassificationAttempt,
    DocumentIntake,
    DocumentIntakeStatus,
    DocumentRouting,
    DocumentRoutingStatus,
    DocumentRunOutcome,
    DocumentStatus,
    FiscalDocument,
    RunEventSource,
    RunEventType,
    RunStatus,
    SC05AttemptStatus,
    SC05ClientStatus,
    SC05Operation,
    SC05PortalStep,
    SC05StepAttempt,
    SC05StepStatus,
)
from core.automations.run_tracking import with_reconciliation_event
from core.automations.sc04.services import recompute_sc04_run
from core.automations.traceability import record_run_event


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    inspected: int = 0
    requeued: int = 0
    quarantined: int = 0
    failed: int = 0
    publish_failed: int = 0
    inspection_failed: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class _RecoveryClaim:
    module_code: str
    run_id: UUID
    task_id: UUID


@dataclass(frozen=True, slots=True)
class _RunOutcome:
    action: str
    claim: _RecoveryClaim | None = None
    recompute_sc04: bool = False


def reconcile_stale_runs(
    *,
    at: datetime | None = None,
    batch_size: int = 100,
    dry_run: bool = False,
) -> ReconciliationResult:
    if batch_size < 1:
        raise ValueError("batch_size precisa ser maior que zero.")
    now = at or timezone.now()
    queued_cutoff = now - timedelta(seconds=int(settings.AUTOMATION_QUEUED_STALE_AFTER_SECONDS))
    running_cutoff = now - timedelta(seconds=int(settings.AUTOMATION_RUNNING_STALE_AFTER_SECONDS))
    candidate_ids = list(
        AutomationRun.objects.filter(module_id__in=ASYNC_MODULE_CODES)
        .filter(
            Q(
                module_id="SC-04",
                status=RunStatus.PENDING,
                created_at__lte=queued_cutoff,
            )
            | (
                Q(status=RunStatus.QUEUED, broker_published_at__isnull=True)
                & (
                    Q(dispatch_started_at__lte=queued_cutoff)
                    | Q(
                        dispatch_started_at__isnull=True,
                        queued_at__lte=queued_cutoff,
                    )
                    | Q(
                        dispatch_started_at__isnull=True,
                        queued_at__isnull=True,
                        created_at__lte=queued_cutoff,
                    )
                )
            )
            | Q(status=RunStatus.RUNNING, heartbeat_at__lte=running_cutoff)
            | Q(
                status=RunStatus.RUNNING,
                heartbeat_at__isnull=True,
                started_at__lte=running_cutoff,
            )
            | Q(
                status=RunStatus.RUNNING,
                heartbeat_at__isnull=True,
                started_at__isnull=True,
                created_at__lte=running_cutoff,
            )
        )
        .order_by("created_at")
        .values_list("id", flat=True)[:batch_size]
    )

    requeued = quarantined = failed = publish_failed = inspection_failed = skipped = 0
    for run_id in candidate_ids:
        try:
            outcome = _reconcile_one(
                run_id=run_id,
                now=now,
                queued_cutoff=queued_cutoff,
                running_cutoff=running_cutoff,
                dry_run=dry_run,
            )
        except AutomationRun.DoesNotExist:
            skipped += 1
            continue
        except Exception as exc:
            inspection_failed += 1
            _record_inspection_failure(run_id=run_id, exc=exc, now=now)
            continue
        if outcome.action == "skipped":
            skipped += 1
            continue
        if outcome.action == "quarantined":
            quarantined += 1
        elif outcome.action == "failed":
            failed += 1
        elif outcome.action == "requeued":
            requeued += 1

        if outcome.recompute_sc04 and not dry_run:
            try:
                result = recompute_sc04_run(run_id, preserve_terminal_status=True)
                if result.received == 0:
                    _fail_empty_recovered_sc04(run_id=run_id, now=now)
            except Exception as exc:
                inspection_failed += 1
                _record_inspection_failure(run_id=run_id, exc=exc, now=now)
                continue

        if outcome.claim is None or dry_run:
            continue
        try:
            publish_claimed_run(
                module_code=outcome.claim.module_code,
                run_id=outcome.claim.run_id,
                task_id=outcome.claim.task_id,
            )
            confirm_claimed_run(
                run_id=outcome.claim.run_id,
                task_id=outcome.claim.task_id,
            )
        except Exception as exc:
            publish_failed += 1
            _record_recovery_publish_failure(claim=outcome.claim, exc=exc, now=now)

    return ReconciliationResult(
        inspected=len(candidate_ids),
        requeued=requeued,
        quarantined=quarantined,
        failed=failed,
        publish_failed=publish_failed,
        inspection_failed=inspection_failed,
        skipped=skipped,
    )


@transaction.atomic
def _fail_empty_recovered_sc04(*, run_id: UUID, now: datetime) -> None:
    run = (
        AutomationRun.objects.select_for_update()
        .filter(pk=run_id, status=RunStatus.SUCCEEDED)
        .first()
    )
    if run is None:
        return
    run.status = RunStatus.FAILED
    run.summary = "A triagem órfã foi encerrada sem itens processados."
    run.error_message = "Inicie uma nova triagem para processar a origem novamente."
    run.finished_at = now
    run.heartbeat_at = now
    run.save(update_fields=("status", "summary", "error_message", "finished_at", "heartbeat_at"))
    record_run_event(
        run=run,
        event_type=RunEventType.FAILED,
        source=RunEventSource.RECONCILER,
        previous_status=RunStatus.SUCCEEDED,
        current_status=RunStatus.FAILED,
        task_id=run.task_id,
        outcome="empty_recovery",
        error_code="empty_recovery",
        deduplication_key="reconciliation.failed:empty-recovery",
        message="Triagem órfã encerrada sem itens processados.",
    )


@transaction.atomic
def _reconcile_one(
    *,
    run_id: UUID,
    now: datetime,
    queued_cutoff: datetime,
    running_cutoff: datetime,
    dry_run: bool,
) -> _RunOutcome:
    run = AutomationRun.objects.select_for_update().select_related("module").get(pk=run_id)
    if not _is_still_stale(
        run=run,
        queued_cutoff=queued_cutoff,
        running_cutoff=running_cutoff,
    ):
        return _RunOutcome("skipped")
    action = _preview_action(run)
    if dry_run:
        return _RunOutcome(action)

    if run.status == RunStatus.PENDING:
        if _pending_sc04_has_new_items(run):
            return _claim_recovery(run=run, now=now, reason="stale_pending_after_ingestion")
        _fail_pending_sc04(run=run, now=now)
        return _RunOutcome("failed")
    if run.status == RunStatus.QUEUED:
        if run.reconciliation_attempts >= _max_attempts():
            return _exhaust_queued_run(run=run, now=now)
        return _claim_recovery(run=run, now=now, reason="stale_queue")
    if str(run.module_id) == "SC-20":
        _quarantine_sc20(run=run, now=now, reason="stale_running_ambiguous_delivery")
        return _RunOutcome("quarantined")
    if str(run.module_id) == "SC-05":
        _close_sc05_interrupted_attempts(run=run, now=now)
        _quarantine_sc05(run=run, now=now, reason="stale_running_ambiguous_external_state")
        return _RunOutcome("quarantined")
    if run.reconciliation_attempts >= _max_attempts():
        return _exhaust_running_run(run=run, now=now)
    _reset_sc04_interrupted_items(run=run, now=now)
    return _claim_recovery(run=run, now=now, reason="stale_running")


def _is_still_stale(
    *,
    run: AutomationRun,
    queued_cutoff: datetime,
    running_cutoff: datetime,
) -> bool:
    if str(run.module_id) not in ASYNC_MODULE_CODES:
        return False
    if run.status == RunStatus.PENDING:
        return str(run.module_id) == "SC-04" and run.created_at <= queued_cutoff
    if run.status == RunStatus.QUEUED:
        if run.broker_published_at is not None:
            return False
        activity_at = run.dispatch_started_at or run.queued_at or run.created_at
        return activity_at <= queued_cutoff
    if run.status == RunStatus.RUNNING:
        activity_at = run.heartbeat_at or run.started_at or run.created_at
        return activity_at <= running_cutoff
    return False


def _preview_action(run: AutomationRun) -> str:
    if run.status == RunStatus.PENDING:
        return "requeued" if _pending_sc04_has_new_items(run) else "failed"
    if run.status == RunStatus.QUEUED:
        if run.reconciliation_attempts < _max_attempts():
            return "requeued"
        if str(run.module_id) == "SC-05" and _sc05_queue_has_ambiguous_state(run):
            return "quarantined"
        return "failed"
    if str(run.module_id) == "SC-20":
        return "quarantined"
    if str(run.module_id) == "SC-05":
        return "quarantined"
    if run.reconciliation_attempts < _max_attempts():
        return "requeued"
    return "quarantined" if str(run.module_id) == "SC-05" else "failed"


def _claim_recovery(*, run: AutomationRun, now: datetime, reason: str) -> _RunOutcome:
    previous_status = run.status
    previous_task_id = run.task_id
    task_id = uuid4()
    run.status = RunStatus.QUEUED
    run.task_id = task_id
    run.queued_at = now
    run.dispatch_started_at = now
    run.broker_published_at = None
    run.heartbeat_at = None
    run.finished_at = None
    run.error_message = ""
    run.reconciliation_attempts += 1
    if str(run.module_id) == "SC-04":
        run.summary = "Triagem órfã recuperada e adicionada novamente à fila."
    elif str(run.module_id) == "SC-05":
        run.summary = "Operação RPA órfã recuperada e adicionada novamente à fila."
    else:
        run.summary = "Verificação órfã recuperada e adicionada novamente à fila."
    run.metadata = with_reconciliation_event(
        run.metadata,
        action="requeued",
        reason=reason,
        at=now,
        previous_task_id=previous_task_id,
        details={
            "attempt": run.reconciliation_attempts,
            "task_id": str(task_id),
        },
    )
    run.save(
        update_fields=(
            "status",
            "task_id",
            "queued_at",
            "dispatch_started_at",
            "broker_published_at",
            "heartbeat_at",
            "finished_at",
            "error_message",
            "reconciliation_attempts",
            "summary",
            "metadata",
        )
    )
    record_run_event(
        run=run,
        event_type=RunEventType.REQUEUED,
        source=RunEventSource.RECONCILER,
        previous_status=previous_status,
        current_status=RunStatus.QUEUED,
        task_id=task_id,
        attempt=run.reconciliation_attempts,
        outcome=reason,
        deduplication_key=f"reconciliation.requeued:{task_id}",
        message="Execução órfã recuperada e adicionada novamente à fila.",
    )
    return _RunOutcome(
        "requeued",
        claim=_RecoveryClaim(
            module_code=str(run.module_id),
            run_id=run.id,
            task_id=task_id,
        ),
    )


def _fail_pending_sc04(*, run: AutomationRun, now: datetime) -> None:
    previous_task_id = run.task_id
    run.status = RunStatus.FAILED
    run.task_id = None
    run.summary = "O upload ficou incompleto antes de chegar à fila."
    run.error_message = "Envie o documento novamente; nenhum processamento automático foi iniciado."
    run.metadata = with_reconciliation_event(
        run.metadata,
        action="failed",
        reason="stale_pending_upload",
        at=now,
        previous_task_id=previous_task_id,
    )
    run.finished_at = now
    run.heartbeat_at = now
    run.save(
        update_fields=(
            "status",
            "task_id",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "heartbeat_at",
        )
    )
    record_run_event(
        run=run,
        event_type=RunEventType.FAILED,
        source=RunEventSource.RECONCILER,
        previous_status=RunStatus.PENDING,
        current_status=RunStatus.FAILED,
        task_id=previous_task_id,
        outcome="stale_pending_upload",
        error_code="stale_pending_upload",
        deduplication_key="reconciliation.failed:stale-pending-upload",
        message="Upload órfão encerrado antes de entrar na fila.",
    )


def _pending_sc04_has_new_items(run: AutomationRun) -> bool:
    return DocumentRunOutcome.NEW in set(run.document_run_items.values_list("outcome", flat=True))


def _exhaust_queued_run(*, run: AutomationRun, now: datetime) -> _RunOutcome:
    module_code = str(run.module_id)
    if module_code == "SC-04":
        _close_sc04_interrupted_items(run=run, now=now, include_queued=True)
        _fence_run(
            run=run,
            now=now,
            action="failed",
            reason="stale_queue_recovery_exhausted",
        )
        return _RunOutcome("failed", recompute_sc04=True)
    if module_code == "SC-05" and _sc05_queue_has_ambiguous_state(run):
        _quarantine_sc05(run=run, now=now, reason="stale_queue_recovery_exhausted")
        return _RunOutcome("quarantined")
    _fence_run(
        run=run,
        now=now,
        action="failed",
        reason="stale_queue_recovery_exhausted",
    )
    run.status = RunStatus.FAILED
    run.summary = "A execução não foi consumida após a tentativa automática de recuperação."
    run.error_message = "Verifique o worker e inicie uma nova execução."
    run.save(update_fields=("status", "summary", "error_message"))
    return _RunOutcome("failed")


def _exhaust_running_run(*, run: AutomationRun, now: datetime) -> _RunOutcome:
    if str(run.module_id) == "SC-04":
        _close_sc04_interrupted_items(run=run, now=now, include_queued=True)
        _fence_run(
            run=run,
            now=now,
            action="failed",
            reason="stale_running_recovery_exhausted",
        )
        return _RunOutcome("failed", recompute_sc04=True)
    _close_sc05_interrupted_attempts(run=run, now=now)
    _quarantine_sc05(run=run, now=now, reason="stale_running_recovery_exhausted")
    return _RunOutcome("quarantined")


def _fence_run(*, run: AutomationRun, now: datetime, action: str, reason: str) -> None:
    previous_status = run.status
    previous_task_id = run.task_id
    run.task_id = None
    run.status = RunStatus.FAILED
    run.summary = "A execução órfã excedeu o limite de recuperação automática."
    run.error_message = "Revise os itens afetados antes de iniciar uma nova execução."
    run.metadata = with_reconciliation_event(
        run.metadata,
        action=action,
        reason=reason,
        at=now,
        previous_task_id=previous_task_id,
    )
    run.finished_at = now
    run.heartbeat_at = now
    run.save(
        update_fields=(
            "task_id",
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "heartbeat_at",
        )
    )
    record_run_event(
        run=run,
        event_type=RunEventType.FAILED,
        source=RunEventSource.RECONCILER,
        previous_status=previous_status,
        current_status=RunStatus.FAILED,
        task_id=previous_task_id,
        attempt=run.reconciliation_attempts,
        outcome=action,
        error_code=reason,
        deduplication_key=(f"reconciliation.failed:{previous_task_id or 'without-task'}:{reason}"),
        message="Execução órfã encerrada após esgotar a recuperação automática.",
    )


def _quarantine_sc20(*, run: AutomationRun, now: datetime, reason: str) -> None:
    previous_task_id = run.task_id
    run.reconciliation_attempts += 1
    run.task_id = None
    run.status = RunStatus.PARTIALLY_FAILED
    run.summary = (
        "A verificação ficou sem confirmação durante uma comunicação; "
        "nenhum reenvio automático foi realizado."
    )
    run.error_message = "Confirme o histórico do provedor antes de autorizar uma nova tentativa."
    run.metadata = {
        **with_reconciliation_event(
            run.metadata,
            action="quarantined",
            reason=reason,
            at=now,
            previous_task_id=previous_task_id,
            details={"attempt": run.reconciliation_attempts},
        ),
        "reconciliation_required": True,
    }
    run.finished_at = now
    run.heartbeat_at = now
    run.save(
        update_fields=(
            "reconciliation_attempts",
            "task_id",
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "heartbeat_at",
        )
    )
    record_run_event(
        run=run,
        event_type=RunEventType.QUARANTINED,
        source=RunEventSource.RECONCILER,
        previous_status=RunStatus.RUNNING,
        current_status=RunStatus.PARTIALLY_FAILED,
        task_id=previous_task_id,
        attempt=run.reconciliation_attempts,
        outcome="ambiguous_delivery",
        error_code=reason,
        deduplication_key=(
            f"reconciliation.quarantined:{previous_task_id or 'without-task'}:{reason}"
        ),
        message="Execução isolada para conferir uma entrega externa ambígua.",
    )


def _quarantine_sc05(*, run: AutomationRun, now: datetime, reason: str) -> None:
    previous_status = run.status
    operation = SC05Operation.objects.select_related("client").filter(run=run).first()
    if operation is not None:
        operation.client.status = SC05ClientStatus.PARTIAL
        operation.client.save(update_fields=("status", "updated_at"))
    previous_task_id = run.task_id
    run.reconciliation_attempts += 1
    run.task_id = None
    run.status = RunStatus.PARTIALLY_FAILED
    if operation is None:
        run.summary = "A operação RPA órfã não possui projeção íntegra e foi isolada."
        run.error_message = "Corrija a projeção da operação antes de qualquer nova tentativa."
    else:
        run.summary = (
            f"{operation.get_action_display()} interrompido para {operation.client.name}; "
            "o estado externo exige retomada manual."
        )
        run.error_message = "Reconcilie os três portais antes de retomar a mesma execução."
    run.metadata = {
        **with_reconciliation_event(
            run.metadata,
            action="quarantined",
            reason=reason,
            at=now,
            previous_task_id=previous_task_id,
            details={"attempt": run.reconciliation_attempts},
        ),
        "reconciliation_required": True,
    }
    run.finished_at = now
    run.heartbeat_at = now
    run.save(
        update_fields=(
            "reconciliation_attempts",
            "task_id",
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "heartbeat_at",
        )
    )
    record_run_event(
        run=run,
        event_type=RunEventType.QUARANTINED,
        source=RunEventSource.RECONCILER,
        previous_status=previous_status,
        current_status=RunStatus.PARTIALLY_FAILED,
        task_id=previous_task_id,
        entity_type=("sc05_operation" if operation else ""),
        entity_id=(str(operation.id) if operation else ""),
        attempt=run.reconciliation_attempts,
        outcome="ambiguous_external_state",
        error_code=reason,
        deduplication_key=(
            f"reconciliation.quarantined:{previous_task_id or 'without-task'}:{reason}"
        ),
        message="Operação RPA isolada para reconciliação manual.",
    )


def _sc05_queue_has_ambiguous_state(run: AutomationRun) -> bool:
    try:
        operation = SC05Operation.objects.select_related("client").get(run=run)
    except SC05Operation.DoesNotExist:
        return run.started_at is not None
    return (
        run.started_at is not None
        or operation.resume_count > 0
        or operation.client.status in {SC05ClientStatus.PARTIAL, SC05ClientStatus.UNKNOWN}
    )


def _reset_sc04_interrupted_items(*, run: AutomationRun, now: datetime) -> None:
    document_ids = _sc04_document_ids(run=run, statuses=(DocumentStatus.PROCESSING,))
    _fail_sc04_attempts(run=run, now=now)
    FiscalDocument.objects.filter(id__in=document_ids).update(
        status=DocumentStatus.QUEUED,
        last_error="",
    )
    DocumentIntake.objects.filter(
        run=run,
        document_id__in=document_ids,
        status=DocumentIntakeStatus.PROCESSING,
    ).update(status=DocumentIntakeStatus.QUEUED)


def _close_sc04_interrupted_items(
    *,
    run: AutomationRun,
    now: datetime,
    include_queued: bool,
) -> None:
    statuses = (
        (DocumentStatus.QUEUED, DocumentStatus.PROCESSING)
        if include_queued
        else (DocumentStatus.PROCESSING,)
    )
    document_ids = _sc04_document_ids(run=run, statuses=statuses, unresolved_only=False)
    _fail_sc04_attempts(run=run, now=now)
    DocumentRouting.objects.filter(
        run=run,
        status=DocumentRoutingStatus.PENDING,
    ).update(
        status=DocumentRoutingStatus.FAILED,
        last_error="A execução órfã excedeu o limite de recuperação automática.",
        updated_at=now,
    )
    FiscalDocument.objects.filter(id__in=document_ids).update(
        status=DocumentStatus.FAILED,
        last_error="A execução órfã excedeu o limite de recuperação automática.",
    )
    DocumentIntake.objects.filter(
        run=run,
        document_id__in=document_ids,
        status__in=(DocumentIntakeStatus.QUEUED, DocumentIntakeStatus.PROCESSING),
    ).update(status=DocumentIntakeStatus.FAILED)


def _sc04_document_ids(
    *,
    run: AutomationRun,
    statuses: tuple[str, ...],
    unresolved_only: bool = True,
) -> list[UUID]:
    query = FiscalDocument.objects.filter(
        intakes__run_items__run=run,
        intakes__run_items__outcome=DocumentRunOutcome.NEW,
        status__in=statuses,
    )
    if unresolved_only:
        query = query.filter(decision__isnull=True, review__isnull=True)
    return list(query.distinct().values_list("id", flat=True))


def _fail_sc04_attempts(*, run: AutomationRun, now: datetime) -> None:
    DocumentClassificationAttempt.objects.filter(
        run=run,
        status=ClassificationAttemptStatus.PROCESSING,
    ).update(
        status=ClassificationAttemptStatus.FAILED,
        error_code="worker_interrupted",
        error_message="A tentativa órfã foi encerrada pela reconciliação automática.",
        finished_at=now,
    )


def _close_sc05_interrupted_attempts(*, run: AutomationRun, now: datetime) -> None:
    operation = SC05Operation.objects.filter(run=run).first()
    if operation is None:
        return
    running_attempts = SC05StepAttempt.objects.filter(
        step__operation=operation,
        status=SC05AttemptStatus.RUNNING,
    )
    for attempt in running_attempts:
        attempt.status = SC05AttemptStatus.FAILED
        attempt.error_code = "worker_interrupted"
        attempt.error_message = "A tentativa órfã foi encerrada pela reconciliação automática."
        attempt.finished_at = now
        attempt.save(
            update_fields=(
                "status",
                "error_code",
                "error_message",
                "finished_at",
            )
        )
    SC05PortalStep.objects.filter(
        operation=operation,
        status=SC05StepStatus.RUNNING,
    ).update(
        status=SC05StepStatus.FAILED,
        error_message="A etapa órfã foi encerrada e será reconciliada.",
        finished_at=now,
    )


def _record_recovery_publish_failure(
    *,
    claim: _RecoveryClaim,
    exc: Exception,
    now: datetime,
) -> None:
    with transaction.atomic():
        run = (
            AutomationRun.objects.select_for_update()
            .filter(
                pk=claim.run_id,
                task_id=claim.task_id,
                status=RunStatus.QUEUED,
            )
            .first()
        )
        if run is None:
            return
        run.error_message = "A recuperação foi preparada, mas o broker não confirmou a publicação."
        run.metadata = with_reconciliation_event(
            run.metadata,
            action="publish_failed",
            reason="broker_error",
            at=now,
            previous_task_id=claim.task_id,
            details={"technical_error": type(exc).__name__},
        )
        run.save(update_fields=("error_message", "metadata"))
        record_run_event(
            run=run,
            event_type=RunEventType.RECONCILIATION_FAILED,
            source=RunEventSource.RECONCILER,
            current_status=RunStatus.QUEUED,
            task_id=claim.task_id,
            attempt=run.reconciliation_attempts,
            outcome="publish_failed",
            error_code=type(exc).__name__,
            deduplication_key=f"reconciliation.publish-failed:{claim.task_id}",
            message="Broker não confirmou a publicação de recuperação.",
        )


def _record_inspection_failure(*, run_id: UUID, exc: Exception, now: datetime) -> None:
    try:
        with transaction.atomic():
            run = AutomationRun.objects.select_for_update().filter(pk=run_id).first()
            if run is None:
                return
            run.metadata = with_reconciliation_event(
                run.metadata,
                action="inspection_failed",
                reason="inconsistent_run_state",
                at=now,
                previous_task_id=run.task_id,
                details={"technical_error": type(exc).__name__},
            )
            run.save(update_fields=("metadata",))
            record_run_event(
                run=run,
                event_type=RunEventType.RECONCILIATION_FAILED,
                source=RunEventSource.RECONCILER,
                current_status=run.status,
                task_id=run.task_id,
                attempt=run.reconciliation_attempts,
                outcome="inspection_failed",
                error_code=type(exc).__name__,
                deduplication_key=(
                    f"reconciliation.inspection-failed:{run.reconciliation_attempts}:"
                    f"{run.task_id or 'without-task'}"
                ),
                message="Reconciliação não conseguiu inspecionar a execução.",
            )
    except Exception:
        return


def _max_attempts() -> int:
    return int(settings.AUTOMATION_RECONCILIATION_MAX_ATTEMPTS)
