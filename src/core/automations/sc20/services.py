from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    CertificateCommunication,
    CertificateStatus,
    CommunicationAttempt,
    CommunicationStatus,
    DigitalCertificate,
    RunEventSource,
    RunEventType,
    RunStatus,
    RunTrigger,
)
from core.automations.run_tracking import (
    SupersededDelivery,
    bind_delivery,
    delivery_matches,
    normalize_task_id,
    require_current_delivery,
    touch_run,
    with_reconciliation_event,
)
from core.automations.sc20.gateways import (
    DeliveryResult,
    NotificationGateway,
    NotificationMessage,
    SimulatedNotificationGateway,
    get_sc20_gateway,
)
from core.automations.traceability import record_run_event

if TYPE_CHECKING:
    from core.identity.models import User


@dataclass(frozen=True, slots=True)
class SC20Policy:
    key: str = "sc20-60-days-v1"
    window_days: int = 60


@dataclass(frozen=True, slots=True)
class SC20ExecutionResult:
    selected: int = 0
    sent: int = 0
    failed: int = 0
    deduplicated: int = 0


@dataclass(frozen=True, slots=True)
class _PreparedDelivery:
    attempt_id: uuid.UUID
    communication_id: uuid.UUID
    run_id: uuid.UUID
    task_id: uuid.UUID | None
    message: NotificationMessage


@transaction.atomic
def create_sc20_run(
    *,
    triggered_by: User | None,
    trigger: str = RunTrigger.MANUAL,
    base_date: date | None = None,
    retry_communication: CertificateCommunication | None = None,
    idempotency_key: str | None = None,
) -> AutomationRun:
    parameters: dict[str, str] = {
        "base_date": (base_date or timezone.localdate()).isoformat(),
    }
    if retry_communication is not None:
        parameters["retry_communication_id"] = str(retry_communication.id)
    run = AutomationRun.objects.create(
        module=AutomationModule.objects.get(code="SC-20"),
        trigger=trigger,
        status=RunStatus.QUEUED,
        triggered_by=triggered_by,
        parameters=parameters,
        idempotency_key=idempotency_key,
        summary="Execução adicionada à fila.",
        task_id=uuid.uuid4(),
        queued_at=timezone.now(),
    )
    source = RunEventSource.SCHEDULER if trigger == RunTrigger.SCHEDULED else RunEventSource.WEB
    record_run_event(
        run=run,
        event_type=RunEventType.CREATED,
        source=source,
        actor=triggered_by,
        current_status=RunStatus.QUEUED,
        task_id=run.task_id,
        deduplication_key="run.created",
        message="Execução de certificados criada.",
    )
    record_run_event(
        run=run,
        event_type=RunEventType.QUEUED,
        source=source,
        actor=triggered_by,
        current_status=RunStatus.QUEUED,
        task_id=run.task_id,
        outcome="queued",
        deduplication_key=f"run.queued:{run.task_id}",
        message="Execução adicionada à fila.",
    )
    return run


@transaction.atomic
def prepare_scheduled_sc20_run(*, base_date: date) -> tuple[AutomationRun, bool]:
    module = AutomationModule.objects.get(code="SC-20")
    competence = base_date.strftime("%Y-%m")
    run, created = AutomationRun.objects.get_or_create(
        idempotency_key=f"sc20:scheduled:{competence}",
        defaults={
            "module": module,
            "trigger": RunTrigger.SCHEDULED,
            "status": RunStatus.QUEUED,
            "parameters": {"base_date": base_date.isoformat(), "competence": competence},
            "summary": "Execução mensal adicionada à fila.",
            "task_id": uuid.uuid4(),
            "queued_at": timezone.now(),
        },
    )
    if created:
        record_run_event(
            run=run,
            event_type=RunEventType.CREATED,
            source=RunEventSource.SCHEDULER,
            current_status=RunStatus.QUEUED,
            task_id=run.task_id,
            deduplication_key="run.created",
            message="Execução mensal criada pelo agendador.",
        )
        record_run_event(
            run=run,
            event_type=RunEventType.QUEUED,
            source=RunEventSource.SCHEDULER,
            current_status=RunStatus.QUEUED,
            task_id=run.task_id,
            pulse_id=None,
            outcome="queued",
            deduplication_key=f"run.queued:{run.task_id}",
            message="Execução mensal adicionada à fila.",
        )
        return run, True

    run = AutomationRun.objects.select_for_update().get(pk=run.pk)
    dispatch_failed_before_start = (
        run.status == RunStatus.FAILED
        and run.started_at is None
        and bool(run.metadata.get("dispatch_error"))
    )
    if not dispatch_failed_before_start:
        return run, False

    run.status = RunStatus.QUEUED
    run.summary = "Execução mensal adicionada novamente à fila."
    run.error_message = ""
    run.metadata = {}
    run.finished_at = None
    run.task_id = uuid.uuid4()
    run.queued_at = timezone.now()
    run.dispatch_started_at = None
    run.broker_published_at = None
    run.heartbeat_at = None
    run.reconciliation_attempts = 0
    run.save(
        update_fields=(
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "task_id",
            "queued_at",
            "dispatch_started_at",
            "broker_published_at",
            "heartbeat_at",
            "reconciliation_attempts",
        )
    )
    record_run_event(
        run=run,
        event_type=RunEventType.REQUEUED,
        source=RunEventSource.SCHEDULER,
        previous_status=RunStatus.FAILED,
        current_status=RunStatus.QUEUED,
        task_id=run.task_id,
        outcome="requeued",
        deduplication_key=f"run.requeued:{run.task_id}",
        message="Execução mensal recolocada na fila após falha de publicação.",
    )
    return run, True


def execute_sc20(
    run_id: uuid.UUID | str,
    *,
    gateway: NotificationGateway | None = None,
    policy: SC20Policy | None = None,
    task_id: str | uuid.UUID | None = None,
    resume_interrupted: bool = False,
) -> SC20ExecutionResult:
    selected_policy = policy or SC20Policy()
    run, should_execute = _start_run(
        run_id,
        task_id=task_id,
        resume_interrupted=resume_interrupted,
    )
    if not should_execute:
        return _result_from_metadata(run.metadata)

    try:
        selected_gateway = gateway or get_sc20_gateway()
        retry_id = run.parameters.get("retry_communication_id")
        if retry_id:
            result = _execute_retry(
                run=run,
                communication_id=str(retry_id),
                gateway=selected_gateway,
            )
        else:
            result = _execute_scan(run=run, gateway=selected_gateway, policy=selected_policy)
        _finish_run(
            run=run,
            result=result,
            policy=selected_policy,
            expected_task_id=run.task_id,
        )
        return result
    except SupersededDelivery:
        current = AutomationRun.objects.get(pk=run.pk)
        return _result_from_metadata(current.metadata)
    except Exception as exc:
        _record_unhandled_failure(run=run, exc=exc, expected_task_id=run.task_id)
        raise


@transaction.atomic
def _start_run(
    run_id: uuid.UUID | str,
    *,
    task_id: str | uuid.UUID | None,
    resume_interrupted: bool,
) -> tuple[AutomationRun, bool]:
    run = AutomationRun.objects.select_for_update().get(pk=run_id, module_id="SC-20")
    if not bind_delivery(run, task_id):
        supplied_task_id = normalize_task_id(task_id)
        record_run_event(
            run=run,
            event_type=RunEventType.DELIVERY_IGNORED,
            source=RunEventSource.WORKER,
            current_status=run.status,
            task_id=supplied_task_id,
            outcome="superseded",
            deduplication_key=f"delivery.ignored:{supplied_task_id or 'invalid'}",
            message="Entrega substituída ignorada sem alterar a execução.",
        )
        return run, False
    now = timezone.now()
    run.dispatch_started_at = run.dispatch_started_at or run.queued_at or now
    run.broker_published_at = run.broker_published_at or now
    if run.status == RunStatus.RUNNING:
        if resume_interrupted:
            previous_task_id = run.task_id
            run.reconciliation_attempts += 1
            run.task_id = None
            run.status = RunStatus.PARTIALLY_FAILED
            run.summary = (
                "A verificação foi interrompida durante uma comunicação; "
                "nenhum reenvio automático foi realizado."
            )
            run.error_message = (
                "Confirme o histórico do provedor antes de autorizar uma nova tentativa."
            )
            run.metadata = {
                **with_reconciliation_event(
                    run.metadata,
                    action="quarantined",
                    reason="broker_redelivery_with_ambiguous_delivery",
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
                    "task_id",
                    "dispatch_started_at",
                    "broker_published_at",
                    "reconciliation_attempts",
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
                source=RunEventSource.WORKER,
                previous_status=RunStatus.RUNNING,
                current_status=RunStatus.PARTIALLY_FAILED,
                task_id=previous_task_id,
                attempt=run.reconciliation_attempts,
                outcome="ambiguous_delivery",
                deduplication_key=(
                    f"run.quarantined:{previous_task_id or 'without-task'}:redelivery"
                ),
                message="Execução isolada para conferência; não houve reenvio automático.",
            )
        else:
            record_run_event(
                run=run,
                event_type=RunEventType.DELIVERY_IGNORED,
                source=RunEventSource.WORKER,
                current_status=RunStatus.RUNNING,
                task_id=run.task_id,
                outcome="already_running",
                deduplication_key=(
                    f"delivery.ignored:{run.task_id or 'without-task'}:already-running"
                ),
                message="Entrega repetida ignorada enquanto a execução estava ativa.",
            )
        return run, False
    if run.status in _terminal_statuses():
        supplied_task_id = normalize_task_id(task_id)
        record_run_event(
            run=run,
            event_type=RunEventType.DELIVERY_IGNORED,
            source=RunEventSource.WORKER,
            current_status=run.status,
            task_id=supplied_task_id,
            outcome="terminal",
            deduplication_key=f"delivery.ignored:{supplied_task_id or 'without-task'}:terminal",
            message="Entrega ignorada porque a execução já estava encerrada.",
        )
        return run, False
    previous_status = run.status
    run.status = RunStatus.RUNNING
    run.started_at = now
    run.error_message = ""
    run.heartbeat_at = now
    run.save(
        update_fields=(
            "task_id",
            "dispatch_started_at",
            "broker_published_at",
            "status",
            "started_at",
            "error_message",
            "heartbeat_at",
        )
    )
    record_run_event(
        run=run,
        event_type=RunEventType.STARTED,
        source=RunEventSource.WORKER,
        previous_status=previous_status,
        current_status=RunStatus.RUNNING,
        task_id=run.task_id,
        outcome="started",
        deduplication_key=f"run.started:{run.task_id or 'without-task'}",
        message="Worker iniciou a execução.",
    )
    return run, True


def _execute_scan(
    *,
    run: AutomationRun,
    gateway: NotificationGateway,
    policy: SC20Policy,
) -> SC20ExecutionResult:
    base_date = date.fromisoformat(str(run.parameters["base_date"]))
    end_date = base_date + timedelta(days=policy.window_days)
    certificates = DigitalCertificate.objects.expiring_between(
        start_date=base_date,
        end_date=end_date,
    )
    selected = sent = failed = deduplicated = 0
    for certificate in certificates.iterator():
        require_current_delivery(run.id, task_id=run.task_id)
        touch_run(run.id, task_id=run.task_id)
        selected += 1
        result = _notify_certificate(
            certificate=certificate,
            run=run,
            policy=policy,
            gateway=gateway,
        )
        sent += result.sent
        failed += result.failed
        deduplicated += result.deduplicated
    return SC20ExecutionResult(
        selected=selected,
        sent=sent,
        failed=failed,
        deduplicated=deduplicated,
    )


def _notify_certificate(
    *,
    certificate: DigitalCertificate,
    run: AutomationRun,
    policy: SC20Policy,
    gateway: NotificationGateway,
) -> SC20ExecutionResult:
    prepared = _prepare_new_delivery(
        certificate=certificate,
        run=run,
        policy=policy,
        gateway=gateway,
    )
    if prepared is None:
        return SC20ExecutionResult(deduplicated=1)

    return _send_prepared_delivery(prepared=prepared, gateway=gateway)


def _execute_retry(
    *,
    run: AutomationRun,
    communication_id: str,
    gateway: NotificationGateway,
) -> SC20ExecutionResult:
    prepared = _prepare_retry_delivery(
        run=run,
        communication_id=communication_id,
        gateway=gateway,
    )
    if prepared is None:
        return SC20ExecutionResult(deduplicated=1)

    return _send_prepared_delivery(prepared=prepared, gateway=gateway)


@transaction.atomic
def _prepare_new_delivery(
    *,
    certificate: DigitalCertificate,
    run: AutomationRun,
    policy: SC20Policy,
    gateway: NotificationGateway,
) -> _PreparedDelivery | None:
    locked_run = AutomationRun.objects.select_for_update().get(pk=run.pk)
    _require_locked_delivery(locked_run, expected_task_id=run.task_id)
    locked_certificate = DigitalCertificate.objects.select_for_update().get(pk=certificate.pk)
    channel = locked_certificate.preferred_channel
    recipient = locked_certificate.recipient_for(channel)
    communication, created = CertificateCommunication.objects.get_or_create(
        certificate=locked_certificate,
        certificate_valid_until=locked_certificate.valid_until,
        channel=channel,
        policy_key=policy.key,
        defaults={
            "recipient": recipient,
            "first_run": locked_run,
            "latest_run": locked_run,
        },
    )
    if not created:
        return None
    return _prepare_delivery(
        communication=communication,
        run=locked_run,
        gateway=gateway,
    )


@transaction.atomic
def _prepare_retry_delivery(
    *,
    run: AutomationRun,
    communication_id: str,
    gateway: NotificationGateway,
) -> _PreparedDelivery | None:
    locked_run = AutomationRun.objects.select_for_update().get(pk=run.pk)
    _require_locked_delivery(locked_run, expected_task_id=run.task_id)
    communication = (
        CertificateCommunication.objects.select_for_update()
        .select_related("certificate")
        .get(pk=communication_id)
    )
    certificate = communication.certificate
    if (
        certificate.status != CertificateStatus.ACTIVE
        or certificate.valid_until != communication.certificate_valid_until
        or communication.status != CommunicationStatus.FAILED
    ):
        return None
    communication.latest_run = locked_run
    communication.recipient = certificate.recipient_for(communication.channel)
    communication.status = CommunicationStatus.PENDING
    communication.sent_at = None
    communication.last_error = ""
    communication.save(
        update_fields=(
            "latest_run",
            "recipient",
            "status",
            "sent_at",
            "last_error",
            "updated_at",
        )
    )
    return _prepare_delivery(
        communication=communication,
        run=locked_run,
        gateway=gateway,
    )


def _prepare_delivery(
    *,
    communication: CertificateCommunication,
    run: AutomationRun,
    gateway: NotificationGateway,
) -> _PreparedDelivery:
    next_sequence = (communication.attempts.aggregate(maximum=Max("sequence"))["maximum"] or 0) + 1
    key = (
        f"{communication.certificate_id}:{communication.certificate_valid_until}:"
        f"{communication.channel}:{communication.policy_key}:{next_sequence}"
    )
    certificate = communication.certificate
    today = timezone.localdate()
    days_remaining = (communication.certificate_valid_until - today).days
    extra_context = {
        "client_name": certificate.client_name,
        "client_document": certificate.formatted_document,
        "responsible_name": certificate.responsible_name,
        "valid_until": communication.certificate_valid_until,
        "days_remaining": days_remaining,
        "serial_number": certificate.serial_number,
        "channel": communication.channel,
        "run_id": str(run.id),
    }
    message = NotificationMessage(
        recipient=communication.recipient,
        channel=communication.channel,
        subject="Certificado digital próximo do vencimento",
        body=(
            f"O certificado de {certificate.client_name} vence em "
            f"{communication.certificate_valid_until:%d/%m/%Y}."
        ),
        idempotency_key=key,
        extra_context=extra_context,
    )
    is_synthetic = isinstance(gateway, SimulatedNotificationGateway)
    if is_synthetic:
        backend = "simulated"
    elif communication.channel == "email":
        backend = "email"
    else:
        backend = "whatsapp"

    attempt_payload: dict[str, Any] = {
        "subject": message.subject,
        "body": message.body,
        "synthetic": is_synthetic,
        "backend": backend,
    }
    if communication.channel == "whatsapp":
        attempt_payload["whatsapp_url"] = certificate.whatsapp_url()
    elif communication.channel == "email":
        attempt_payload["mailto_url"] = certificate.mailto_url()

    attempt_payload["idempotency_key"] = key
    attempt = CommunicationAttempt.objects.create(
        communication=communication,
        run=run,
        sequence=next_sequence,
        status=CommunicationStatus.PENDING,
        recipient=message.recipient,
        payload=attempt_payload,
    )
    record_run_event(
        run=run,
        event_type=RunEventType.INTEGRATION_STARTED,
        source=RunEventSource.WORKER,
        current_status=RunStatus.RUNNING,
        task_id=run.task_id,
        entity_type="communication_attempt",
        entity_id=str(attempt.id),
        step=communication.channel,
        attempt=next_sequence,
        outcome="pending",
        deduplication_key=f"integration.started:{attempt.id}",
        message="Tentativa de comunicação preparada antes do envio externo.",
    )
    return _PreparedDelivery(
        attempt_id=attempt.id,
        communication_id=communication.id,
        run_id=run.id,
        task_id=run.task_id,
        message=message,
    )


def _send_prepared_delivery(
    *,
    prepared: _PreparedDelivery,
    gateway: NotificationGateway,
) -> SC20ExecutionResult:
    try:
        delivery = gateway.send(prepared.message)
        return _finalize_delivery(prepared=prepared, delivery=delivery)
    except BaseException:
        record_run_event(
            run=prepared.run_id,
            event_type=RunEventType.INTEGRATION_UNKNOWN,
            source=RunEventSource.WORKER,
            task_id=prepared.task_id,
            entity_type="communication_attempt",
            entity_id=str(prepared.attempt_id),
            outcome="unknown",
            deduplication_key=f"integration.unknown:{prepared.attempt_id}",
            message="A integração foi interrompida sem confirmação final; exige conferência.",
        )
        raise


@transaction.atomic
def _finalize_delivery(
    *,
    prepared: _PreparedDelivery,
    delivery: DeliveryResult,
) -> SC20ExecutionResult:
    run = AutomationRun.objects.select_for_update().get(pk=prepared.run_id)
    _require_locked_delivery(run, expected_task_id=prepared.task_id)
    attempt = CommunicationAttempt.objects.select_for_update().get(pk=prepared.attempt_id)
    if attempt.run_id != prepared.run_id or attempt.communication_id != prepared.communication_id:
        raise RuntimeError("A tentativa preparada não pertence à entrega informada.")
    if attempt.status == CommunicationStatus.SENT:
        return SC20ExecutionResult(sent=1)
    if attempt.status == CommunicationStatus.FAILED:
        return SC20ExecutionResult(failed=1)
    if attempt.status != CommunicationStatus.PENDING or attempt.finished_at is not None:
        raise RuntimeError("A tentativa preparada não está pendente.")

    communication = CertificateCommunication.objects.select_for_update().get(
        pk=prepared.communication_id
    )
    status = CommunicationStatus.SENT if delivery.delivered else CommunicationStatus.FAILED
    finished_at = timezone.now()
    attempt.status = status
    attempt.provider_message_id = delivery.provider_message_id
    attempt.error_message = delivery.error_message
    attempt.finished_at = finished_at
    attempt.save(
        update_fields=(
            "status",
            "provider_message_id",
            "error_message",
            "finished_at",
        )
    )
    communication.status = status
    communication.sent_at = finished_at if delivery.delivered else None
    communication.last_error = delivery.error_message
    communication.latest_run = run
    communication.save(
        update_fields=("status", "sent_at", "last_error", "latest_run", "updated_at")
    )
    record_run_event(
        run=run,
        event_type=RunEventType.INTEGRATION_FINISHED,
        source=RunEventSource.WORKER,
        current_status=RunStatus.RUNNING,
        task_id=prepared.task_id,
        entity_type="communication_attempt",
        entity_id=str(attempt.id),
        step=communication.channel,
        attempt=attempt.sequence,
        outcome=("sent" if delivery.delivered else "failed"),
        error_code=("provider_rejected" if not delivery.delivered else ""),
        deduplication_key=f"integration.finished:{attempt.id}",
        message=(
            "Comunicação confirmada pelo provedor."
            if delivery.delivered
            else "O provedor confirmou a falha da comunicação."
        ),
    )
    if delivery.delivered:
        return SC20ExecutionResult(sent=1)
    return SC20ExecutionResult(failed=1)


def _require_locked_delivery(
    run: AutomationRun,
    *,
    expected_task_id: uuid.UUID | None,
) -> None:
    if run.status != RunStatus.RUNNING or not delivery_matches(run, expected_task_id):
        raise SupersededDelivery


@transaction.atomic
def _finish_run(
    *,
    run: AutomationRun,
    result: SC20ExecutionResult,
    policy: SC20Policy,
    expected_task_id: uuid.UUID | None,
) -> None:
    status = RunStatus.SUCCEEDED_WITH_WARNINGS if result.failed else RunStatus.SUCCEEDED
    if run.parameters.get("retry_communication_id"):
        summary = (
            f"Nova tentativa concluída: {result.sent} aviso(s) enviado(s), "
            f"{result.failed} falha(s) e {result.deduplicated} envio(s) dispensado(s)."
        )
    else:
        summary = (
            f"{result.selected} certificado(s) na janela; {result.sent} aviso(s) enviado(s); "
            f"{result.failed} falha(s); {result.deduplicated} aviso(s) já registrado(s)."
        )
    locked = AutomationRun.objects.select_for_update().get(pk=run.pk)
    _require_locked_delivery(locked, expected_task_id=expected_task_id)
    finished_at = timezone.now()
    locked.status = status
    locked.summary = summary
    locked.error_message = (
        "Há avisos com falha disponíveis para uma nova tentativa." if result.failed else ""
    )
    locked.metadata = {**locked.metadata, "policy": asdict(policy), "result": asdict(result)}
    locked.finished_at = finished_at
    locked.heartbeat_at = finished_at
    locked.save(
        update_fields=(
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "heartbeat_at",
        )
    )
    record_run_event(
        run=locked,
        event_type=(
            RunEventType.SUCCEEDED_WITH_WARNINGS if result.failed else RunEventType.SUCCEEDED
        ),
        source=RunEventSource.WORKER,
        previous_status=RunStatus.RUNNING,
        current_status=status,
        task_id=expected_task_id,
        outcome=("succeeded_with_warnings" if result.failed else "succeeded"),
        deduplication_key=(f"run.terminal:{expected_task_id or 'without-task'}:{status}"),
        message=(
            "Execução concluída com avisos de entrega."
            if result.failed
            else "Execução concluída com sucesso."
        ),
        details={
            "selected_count": result.selected,
            "sent_count": result.sent,
            "failed_count": result.failed,
            "deduplicated_count": result.deduplicated,
        },
    )


@transaction.atomic
def _record_unhandled_failure(
    *,
    run: AutomationRun,
    exc: Exception,
    expected_task_id: uuid.UUID | None,
) -> None:
    locked = AutomationRun.objects.select_for_update().get(pk=run.pk)
    if locked.status != RunStatus.RUNNING or not delivery_matches(locked, expected_task_id):
        return
    ambiguous_delivery = CommunicationAttempt.objects.filter(
        run=locked,
        status=CommunicationStatus.PENDING,
        finished_at__isnull=True,
    ).exists()
    terminal_status = RunStatus.PARTIALLY_FAILED if ambiguous_delivery else RunStatus.FAILED
    finished_at = timezone.now()
    locked.status = terminal_status
    locked.summary = (
        "A comunicação foi interrompida sem confirmação final; nenhum reenvio foi realizado."
        if ambiguous_delivery
        else "A verificação de certificados foi interrompida antes da conclusão."
    )
    locked.error_message = (
        "Confirme o histórico do provedor antes de autorizar uma nova tentativa."
        if ambiguous_delivery
        else "Não foi possível concluir a verificação de certificados."
    )
    locked.metadata = {
        **locked.metadata,
        "technical_error": type(exc).__name__,
        "reconciliation_required": ambiguous_delivery,
    }
    locked.finished_at = finished_at
    locked.heartbeat_at = finished_at
    locked.save(
        update_fields=(
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "heartbeat_at",
        )
    )
    record_run_event(
        run=locked,
        event_type=(RunEventType.PARTIALLY_FAILED if ambiguous_delivery else RunEventType.FAILED),
        source=RunEventSource.WORKER,
        previous_status=RunStatus.RUNNING,
        current_status=terminal_status,
        task_id=expected_task_id,
        outcome=("ambiguous_delivery" if ambiguous_delivery else "failed"),
        error_code=type(exc).__name__,
        deduplication_key=(f"run.terminal:{expected_task_id or 'without-task'}:{terminal_status}"),
        message=(
            "Execução isolada por comunicação sem confirmação final."
            if ambiguous_delivery
            else "Execução encerrada por falha técnica controlada."
        ),
    )


def _terminal_statuses() -> set[str]:
    return {
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED_WITH_WARNINGS,
        RunStatus.PARTIALLY_FAILED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }


def _result_from_metadata(metadata: dict[str, object]) -> SC20ExecutionResult:
    raw_result = metadata.get("result")
    if not isinstance(raw_result, dict):
        return SC20ExecutionResult()
    return SC20ExecutionResult(
        selected=_metadata_int(raw_result.get("selected")),
        sent=_metadata_int(raw_result.get("sent")),
        failed=_metadata_int(raw_result.get("failed")),
        deduplicated=_metadata_int(raw_result.get("deduplicated")),
    )


def _metadata_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0
