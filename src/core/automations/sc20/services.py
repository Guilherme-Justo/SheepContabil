from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

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
    RunStatus,
    RunTrigger,
)
from core.automations.sc20.gateways import (
    DeliveryResult,
    NotificationGateway,
    NotificationMessage,
    SimulatedNotificationGateway,
)

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
    return AutomationRun.objects.create(
        module=AutomationModule.objects.get(code="SC-20"),
        trigger=trigger,
        status=RunStatus.QUEUED,
        triggered_by=triggered_by,
        parameters=parameters,
        idempotency_key=idempotency_key,
        summary="Execução adicionada à fila.",
    )


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
        },
    )
    if created:
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
    run.save(
        update_fields=(
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
        )
    )
    return run, True


def execute_sc20(
    run_id: uuid.UUID | str,
    *,
    gateway: NotificationGateway | None = None,
    policy: SC20Policy | None = None,
) -> SC20ExecutionResult:
    selected_gateway = gateway or SimulatedNotificationGateway()
    selected_policy = policy or SC20Policy()
    run, should_execute = _start_run(run_id)
    if not should_execute:
        return _result_from_metadata(run.metadata)

    try:
        retry_id = run.parameters.get("retry_communication_id")
        if retry_id:
            result = _execute_retry(
                run=run,
                communication_id=str(retry_id),
                gateway=selected_gateway,
            )
        else:
            result = _execute_scan(run=run, gateway=selected_gateway, policy=selected_policy)
        _finish_run(run=run, result=result, policy=selected_policy)
        return result
    except Exception as exc:
        AutomationRun.objects.filter(pk=run.pk).update(
            status=RunStatus.FAILED,
            error_message="Não foi possível concluir a verificação de certificados.",
            metadata={**run.metadata, "technical_error": type(exc).__name__},
            finished_at=timezone.now(),
        )
        raise


@transaction.atomic
def _start_run(run_id: uuid.UUID | str) -> tuple[AutomationRun, bool]:
    run = AutomationRun.objects.select_for_update().get(pk=run_id, module_id="SC-20")
    if run.status in _terminal_statuses() or run.status == RunStatus.RUNNING:
        return run, False
    run.status = RunStatus.RUNNING
    run.started_at = timezone.now()
    run.error_message = ""
    run.save(update_fields=("status", "started_at", "error_message"))
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


@transaction.atomic
def _notify_certificate(
    *,
    certificate: DigitalCertificate,
    run: AutomationRun,
    policy: SC20Policy,
    gateway: NotificationGateway,
) -> SC20ExecutionResult:
    channel = certificate.preferred_channel
    recipient = certificate.recipient_for(channel)
    communication, created = CertificateCommunication.objects.get_or_create(
        certificate=certificate,
        certificate_valid_until=certificate.valid_until,
        channel=channel,
        policy_key=policy.key,
        defaults={
            "recipient": recipient,
            "first_run": run,
            "latest_run": run,
        },
    )
    if not created:
        return SC20ExecutionResult(deduplicated=1)

    return _deliver(
        communication=communication,
        run=run,
        gateway=gateway,
    )


@transaction.atomic
def _execute_retry(
    *,
    run: AutomationRun,
    communication_id: str,
    gateway: NotificationGateway,
) -> SC20ExecutionResult:
    communication = (
        CertificateCommunication.objects.select_for_update()
        .select_related("certificate")
        .get(pk=communication_id)
    )
    certificate = communication.certificate
    if (
        certificate.status != CertificateStatus.ACTIVE
        or certificate.valid_until != communication.certificate_valid_until
    ):
        return SC20ExecutionResult(deduplicated=1)
    if communication.status == CommunicationStatus.SENT:
        return SC20ExecutionResult(deduplicated=1)
    communication.latest_run = run
    communication.recipient = communication.certificate.recipient_for(communication.channel)
    communication.save(update_fields=("latest_run", "recipient", "updated_at"))
    return _deliver(communication=communication, run=run, gateway=gateway)


def _deliver(
    *,
    communication: CertificateCommunication,
    run: AutomationRun,
    gateway: NotificationGateway,
) -> SC20ExecutionResult:
    next_sequence = (communication.attempts.aggregate(maximum=Max("sequence"))["maximum"] or 0) + 1
    key = (
        f"{communication.certificate_id}:{communication.certificate_valid_until}:"
        f"{communication.channel}:{communication.policy_key}:{next_sequence}"
    )
    message = NotificationMessage(
        recipient=communication.recipient,
        channel=communication.channel,
        subject="Certificado digital próximo do vencimento",
        body=(
            f"O certificado de {communication.certificate.client_name} vence em "
            f"{communication.certificate_valid_until:%d/%m/%Y}."
        ),
        idempotency_key=key,
    )
    try:
        delivery = gateway.send(message)
    except Exception:
        delivery = DeliveryResult(
            delivered=False,
            error_message=(
                "O canal simulado ficou indisponível durante o envio; "
                "uma nova tentativa pode ser feita."
            ),
        )
    status = CommunicationStatus.SENT if delivery.delivered else CommunicationStatus.FAILED
    finished_at = timezone.now()
    CommunicationAttempt.objects.create(
        communication=communication,
        run=run,
        sequence=next_sequence,
        status=status,
        recipient=message.recipient,
        provider_message_id=delivery.provider_message_id,
        error_message=delivery.error_message,
        payload={"subject": message.subject, "body": message.body, "synthetic": True},
        finished_at=finished_at,
    )
    communication.status = status
    communication.sent_at = finished_at if delivery.delivered else None
    communication.last_error = delivery.error_message
    communication.latest_run = run
    communication.save(
        update_fields=("status", "sent_at", "last_error", "latest_run", "updated_at")
    )
    if delivery.delivered:
        return SC20ExecutionResult(sent=1)
    return SC20ExecutionResult(failed=1)


def _finish_run(
    *,
    run: AutomationRun,
    result: SC20ExecutionResult,
    policy: SC20Policy,
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
    AutomationRun.objects.filter(pk=run.pk).update(
        status=status,
        summary=summary,
        error_message=(
            "Há avisos com falha disponíveis para uma nova tentativa." if result.failed else ""
        ),
        metadata={"policy": asdict(policy), "result": asdict(result)},
        finished_at=timezone.now(),
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
