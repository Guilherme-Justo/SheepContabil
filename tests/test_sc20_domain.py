from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.db.models.deletion import ProtectedError
from freezegun import freeze_time

from core.automations.management.commands import dispatch_due_schedules
from core.automations.models import (
    AutomationModule,
    AutomationRun,
    CertificateCommunication,
    CertificateStatus,
    CommunicationAttempt,
    CommunicationChannel,
    CommunicationStatus,
    DigitalCertificate,
    RunStatus,
    RunTrigger,
)
from core.automations.sc20.gateways import DeliveryResult, NotificationMessage
from core.automations.sc20.services import create_sc20_run, execute_sc20

pytestmark = pytest.mark.django_db


class RecordingGateway:
    def __init__(self) -> None:
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> DeliveryResult:
        self.messages.append(message)
        return DeliveryResult(delivered=True, provider_message_id=f"provider-{len(self.messages)}")


class ExplodingGateway:
    def send(self, message: NotificationMessage) -> DeliveryResult:
        raise TimeoutError(message.idempotency_key)


def _certificate(
    *,
    serial: str,
    valid_until: date,
    status: str = CertificateStatus.ACTIVE,
    email: str | None = None,
    channel: str = CommunicationChannel.EMAIL,
) -> DigitalCertificate:
    return DigitalCertificate.objects.create(
        serial_number=serial,
        client_name=f"Cliente sintético {serial}",
        client_document="12345678000190",
        responsible_name="Responsável de Teste",
        contact_email=email or f"{serial.lower()}@example.test",
        contact_phone="+55 11 99999-0000",
        preferred_channel=channel,
        valid_until=valid_until,
        status=status,
    )


def test_scan_uses_inclusive_window_and_ignores_ineligible_certificates(
    modules: dict[str, AutomationModule],
) -> None:
    base_date = date(2026, 8, 30)
    included_today = _certificate(serial="TODAY", valid_until=base_date)
    included_limit = _certificate(
        serial="LIMIT",
        valid_until=base_date + timedelta(days=60),
        channel=CommunicationChannel.WHATSAPP,
    )
    _certificate(serial="AFTER", valid_until=base_date + timedelta(days=61))
    _certificate(serial="EXPIRED", valid_until=base_date - timedelta(days=1))
    _certificate(
        serial="REVOKED",
        valid_until=base_date + timedelta(days=10),
        status=CertificateStatus.REVOKED,
    )
    _certificate(
        serial="REPLACED",
        valid_until=base_date + timedelta(days=20),
        status=CertificateStatus.REPLACED,
    )
    gateway = RecordingGateway()
    run = create_sc20_run(triggered_by=None, base_date=base_date)

    result = execute_sc20(run.id, gateway=gateway)

    assert result.selected == 2
    assert result.sent == 2
    assert result.failed == 0
    assert result.deduplicated == 0
    assert {message.recipient for message in gateway.messages} == {
        included_today.contact_email,
        included_limit.contact_phone,
    }
    assert CertificateCommunication.objects.count() == 2
    assert CommunicationAttempt.objects.count() == 2
    run.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED
    assert run.metadata["policy"] == {"key": "sc20-60-days-v1", "window_days": 60}


def test_reruns_are_idempotent_for_the_same_certificate_policy_and_channel(
    modules: dict[str, AutomationModule],
) -> None:
    base_date = date(2026, 8, 30)
    _certificate(serial="IDEMPOTENT", valid_until=base_date + timedelta(days=15))
    first_run = create_sc20_run(triggered_by=None, base_date=base_date)
    second_run = create_sc20_run(triggered_by=None, base_date=base_date)

    first_result = execute_sc20(first_run.id)
    second_result = execute_sc20(second_run.id)
    repeated_result = execute_sc20(first_run.id)

    assert first_result.sent == 1
    assert second_result.selected == 1
    assert second_result.sent == 0
    assert second_result.deduplicated == 1
    assert repeated_result == first_result
    assert CertificateCommunication.objects.count() == 1
    assert CommunicationAttempt.objects.count() == 1


def test_failed_delivery_can_be_retried_without_losing_the_first_attempt(
    modules: dict[str, AutomationModule],
) -> None:
    base_date = date(2026, 8, 30)
    _certificate(
        serial="RECOVERABLE",
        valid_until=base_date + timedelta(days=15),
        email="falha@avisos.invalid",
    )
    first_run = create_sc20_run(triggered_by=None, base_date=base_date)

    failed_result = execute_sc20(first_run.id)

    communication = CertificateCommunication.objects.get()
    first_attempt = CommunicationAttempt.objects.get()
    assert failed_result.failed == 1
    assert communication.status == CommunicationStatus.FAILED
    assert first_attempt.sequence == 1
    first_run.refresh_from_db()
    assert first_run.status == RunStatus.SUCCEEDED_WITH_WARNINGS

    retry_run = create_sc20_run(
        triggered_by=None,
        base_date=base_date,
        retry_communication=communication,
    )
    retry_result = execute_sc20(retry_run.id)

    assert retry_result.sent == 1
    assert retry_result.failed == 0
    communication.refresh_from_db()
    retry_run.refresh_from_db()
    assert communication.status == CommunicationStatus.SENT
    assert communication.recipient == "falha@avisos.invalid"
    assert communication.sent_at is not None
    assert retry_run.status == RunStatus.SUCCEEDED
    assert list(communication.attempts.order_by("sequence").values_list("sequence", flat=True)) == [
        1,
        2,
    ]
    with pytest.raises(ProtectedError):
        communication.delete()


@pytest.mark.parametrize("invalidating_change", ["status", "validity"])
def test_retry_is_discarded_when_certificate_state_has_become_stale(
    modules: dict[str, AutomationModule],
    invalidating_change: str,
) -> None:
    base_date = date(2026, 8, 30)
    certificate = _certificate(
        serial=f"STALE-{invalidating_change}",
        valid_until=base_date + timedelta(days=15),
        email="falha@avisos.invalid",
    )
    first_run = create_sc20_run(triggered_by=None, base_date=base_date)
    execute_sc20(first_run.id)
    communication = CertificateCommunication.objects.get()
    if invalidating_change == "status":
        certificate.status = CertificateStatus.REVOKED
        certificate.save(update_fields=("status", "updated_at"))
    else:
        certificate.valid_until += timedelta(days=1)
        certificate.save(update_fields=("valid_until", "updated_at"))
    retry_run = create_sc20_run(
        triggered_by=None,
        base_date=base_date,
        retry_communication=communication,
    )

    result = execute_sc20(retry_run.id)

    assert result.deduplicated == 1
    assert CommunicationAttempt.objects.count() == 1
    communication.refresh_from_db()
    assert communication.status == CommunicationStatus.FAILED


def test_gateway_exception_is_converted_to_an_auditable_failure(
    modules: dict[str, AutomationModule],
) -> None:
    base_date = date(2026, 8, 30)
    _certificate(serial="TIMEOUT", valid_until=base_date + timedelta(days=15))
    run = create_sc20_run(triggered_by=None, base_date=base_date)

    result = execute_sc20(run.id, gateway=ExplodingGateway())

    attempt = CommunicationAttempt.objects.get()
    run.refresh_from_db()
    assert result.failed == 1
    assert attempt.status == CommunicationStatus.FAILED
    assert "indisponível" in attempt.error_message
    assert run.status == RunStatus.SUCCEEDED_WITH_WARNINGS


@freeze_time("2026-08-30 15:00:00")
def test_monthly_dispatch_is_idempotent_and_anchored_to_competence(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(dispatch_due_schedules.run_sc20_task, "delay", dispatched.append)

    call_command("dispatch_due_schedules", verbosity=0)
    call_command("dispatch_due_schedules", verbosity=0)

    run = AutomationRun.objects.get()
    assert run.trigger == RunTrigger.SCHEDULED
    assert run.status == RunStatus.QUEUED
    assert run.idempotency_key == "sc20:scheduled:2026-08"
    assert run.parameters == {"base_date": "2026-08-01", "competence": "2026-08"}
    assert dispatched == [str(run.id)]


@freeze_time("2026-08-30 15:00:00")
def test_monthly_dispatch_recovers_a_broker_failure_before_execution(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []

    def dispatch(run_id: str) -> None:
        dispatched.append(run_id)
        if len(dispatched) == 1:
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(dispatch_due_schedules.run_sc20_task, "delay", dispatch)

    with pytest.raises(RuntimeError, match="broker unavailable"):
        call_command("dispatch_due_schedules", verbosity=0)
    run = AutomationRun.objects.get()
    run.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert run.started_at is None
    assert run.metadata == {"dispatch_error": "RuntimeError"}

    call_command("dispatch_due_schedules", verbosity=0)

    run.refresh_from_db()
    assert dispatched == [str(run.id), str(run.id)]
    assert run.status == RunStatus.QUEUED
    assert run.metadata == {}
    assert run.finished_at is None


@freeze_time("2026-08-01 10:59:00")
def test_monthly_dispatch_waits_until_eight_oclock_in_sao_paulo(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(dispatch_due_schedules.run_sc20_task, "delay", dispatched.append)

    call_command("dispatch_due_schedules", verbosity=0)

    assert not AutomationRun.objects.exists()
    assert dispatched == []
