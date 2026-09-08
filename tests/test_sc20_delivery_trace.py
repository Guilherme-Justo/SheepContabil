from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import connection

from core.automations.models import (
    AutomationModule,
    CertificateCommunication,
    CommunicationAttempt,
    CommunicationChannel,
    CommunicationStatus,
    DigitalCertificate,
    RunEventType,
    RunStatus,
)
from core.automations.sc20 import services
from core.automations.sc20.gateways import DeliveryResult, NotificationMessage

pytestmark = pytest.mark.django_db(transaction=True)


class InspectingGateway:
    def __init__(self, *, delivered: bool = True) -> None:
        self.delivered = delivered
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> DeliveryResult:
        assert connection.in_atomic_block is False
        attempt = CommunicationAttempt.objects.get(status=CommunicationStatus.PENDING)
        communication = CertificateCommunication.objects.get()
        assert attempt.status == CommunicationStatus.PENDING
        assert attempt.finished_at is None
        assert attempt.payload["idempotency_key"] == message.idempotency_key
        assert communication.status == CommunicationStatus.PENDING
        self.messages.append(message)
        return DeliveryResult(
            delivered=self.delivered,
            provider_message_id="provider-confirmed" if self.delivered else "",
            error_message="Falha explícita do provedor." if not self.delivered else "",
        )


class SimulatedWorkerCrash(BaseException):
    pass


def _certificate() -> DigitalCertificate:
    return DigitalCertificate.objects.create(
        serial_number="TRACE-SC20",
        client_name="Cliente sintético de rastreabilidade",
        client_document="12345678000190",
        responsible_name="Responsável Sintético",
        contact_email="trace@example.test",
        contact_phone="+55 11 99999-0000",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=date(2026, 9, 15),
    )


def test_attempt_is_durable_and_pending_before_the_gateway_is_called(
    modules: dict[str, AutomationModule],
) -> None:
    _certificate()
    gateway = InspectingGateway()
    run = services.create_sc20_run(
        triggered_by=None,
        base_date=date(2026, 9, 1),
    )

    result = services.execute_sc20(run.id, gateway=gateway)

    attempt = CommunicationAttempt.objects.get()
    communication = CertificateCommunication.objects.get()
    assert len(gateway.messages) == 1
    assert result.sent == 1
    assert attempt.status == CommunicationStatus.SENT
    assert attempt.finished_at is not None
    assert attempt.provider_message_id == "provider-confirmed"
    assert communication.status == CommunicationStatus.SENT
    assert communication.sent_at == attempt.finished_at
    assert list(run.events.values_list("event_type", flat=True)) == [
        RunEventType.CREATED,
        RunEventType.QUEUED,
        RunEventType.STARTED,
        RunEventType.INTEGRATION_STARTED,
        RunEventType.INTEGRATION_FINISHED,
        RunEventType.SUCCEEDED,
    ]


def test_explicit_gateway_failure_finalizes_the_pending_attempt(
    modules: dict[str, AutomationModule],
) -> None:
    _certificate()
    gateway = InspectingGateway(delivered=False)
    run = services.create_sc20_run(
        triggered_by=None,
        base_date=date(2026, 9, 1),
    )

    result = services.execute_sc20(run.id, gateway=gateway)

    attempt = CommunicationAttempt.objects.get()
    communication = CertificateCommunication.objects.get()
    run.refresh_from_db()
    assert result.failed == 1
    assert attempt.status == CommunicationStatus.FAILED
    assert attempt.finished_at is not None
    assert attempt.error_message == "Falha explícita do provedor."
    assert communication.status == CommunicationStatus.FAILED
    assert run.status == RunStatus.SUCCEEDED_WITH_WARNINGS
    assert run.events.filter(
        event_type=RunEventType.INTEGRATION_FINISHED,
        outcome="failed",
    ).exists()


def test_interruption_after_gateway_acceptance_keeps_pending_and_is_not_resent(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _certificate()
    gateway = InspectingGateway()
    run = services.create_sc20_run(
        triggered_by=None,
        base_date=date(2026, 9, 1),
    )

    def interrupt_after_gateway(**kwargs: object) -> services.SC20ExecutionResult:
        del kwargs
        raise SimulatedWorkerCrash

    monkeypatch.setattr(services, "_finalize_delivery", interrupt_after_gateway)

    with pytest.raises(SimulatedWorkerCrash):
        services.execute_sc20(run.id, gateway=gateway)

    attempt = CommunicationAttempt.objects.get()
    communication = CertificateCommunication.objects.get()
    run.refresh_from_db()
    assert len(gateway.messages) == 1
    assert attempt.status == CommunicationStatus.PENDING
    assert attempt.finished_at is None
    assert attempt.provider_message_id == ""
    assert communication.status == CommunicationStatus.PENDING
    assert run.status == RunStatus.RUNNING
    assert run.events.filter(
        event_type=RunEventType.INTEGRATION_UNKNOWN,
        entity_id=str(attempt.id),
    ).exists()

    repeated = services.execute_sc20(run.id, gateway=gateway)
    redelivered = services.execute_sc20(
        run.id,
        gateway=gateway,
        resume_interrupted=True,
    )

    run.refresh_from_db()
    attempt.refresh_from_db()
    assert repeated == services.SC20ExecutionResult()
    assert redelivered == services.SC20ExecutionResult()
    assert len(gateway.messages) == 1
    assert CommunicationAttempt.objects.count() == 1
    assert attempt.status == CommunicationStatus.PENDING
    assert run.status == RunStatus.PARTIALLY_FAILED


def test_pending_retry_blocks_a_second_retry_delivery(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = _certificate()
    first_run = services.create_sc20_run(
        triggered_by=None,
        base_date=certificate.valid_until - timedelta(days=14),
    )
    services.execute_sc20(first_run.id, gateway=InspectingGateway(delivered=False))
    communication = CertificateCommunication.objects.get()
    first_retry = services.create_sc20_run(
        triggered_by=None,
        base_date=date(2026, 9, 1),
        retry_communication=communication,
    )
    second_retry = services.create_sc20_run(
        triggered_by=None,
        base_date=date(2026, 9, 1),
        retry_communication=communication,
    )
    gateway = InspectingGateway()

    def interrupt_after_gateway(**kwargs: object) -> services.SC20ExecutionResult:
        del kwargs
        raise SimulatedWorkerCrash

    monkeypatch.setattr(services, "_finalize_delivery", interrupt_after_gateway)
    with pytest.raises(SimulatedWorkerCrash):
        services.execute_sc20(first_retry.id, gateway=gateway)

    result = services.execute_sc20(second_retry.id, gateway=gateway)

    communication.refresh_from_db()
    assert result.deduplicated == 1
    assert len(gateway.messages) == 1
    assert communication.status == CommunicationStatus.PENDING
    assert list(communication.attempts.order_by("sequence").values_list("status", flat=True)) == [
        CommunicationStatus.FAILED,
        CommunicationStatus.PENDING,
    ]
