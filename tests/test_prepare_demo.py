from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    DigitalCertificate,
    RunStatus,
    RunTrigger,
    SC05Action,
    SC05Client,
    SC05ClientStatus,
    SC05Operation,
    SC05Scenario,
)
from core.sc05_simulator.models import (
    SimulatorClient,
    SimulatorServiceAccount,
    SimulatorTask,
)

pytestmark = pytest.mark.django_db


def _seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "demo-readiness-admin-password")
    monkeypatch.setenv("DEMO_OPERATOR_PASSWORD", "demo-readiness-operator-password")
    call_command("seed_demo", verbosity=0)


def _diverge_aurora_projection() -> tuple[SC05Client, SimulatorClient]:
    client = SC05Client.objects.get(external_reference="aurora-demo")
    client.status = SC05ClientStatus.BLOCKED
    client.task_restore_snapshot = {"AURORA-FISCAL-01": "maria.fiscal"}
    client.save(update_fields=("status", "task_restore_snapshot"))

    simulator_client = SimulatorClient.objects.get(external_id="aurora-demo")
    simulator_client.is_active = False
    simulator_client.save(update_fields=("is_active",))
    SimulatorServiceAccount.objects.filter(client=simulator_client).update(is_blocked=True)
    SimulatorTask.objects.filter(client=simulator_client, is_open=True).update(
        assignee="BLOQUEADO_INADIMPLENCIA",
        previous_assignee="responsavel.anterior",
    )
    return client, simulator_client


def test_prepare_demo_defaults_to_read_only_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(monkeypatch)
    client, simulator_client = _diverge_aurora_projection()
    output = StringIO()

    call_command("prepare_demo", stdout=output)

    client.refresh_from_db()
    simulator_client.refresh_from_db()
    assert client.status == SC05ClientStatus.BLOCKED
    assert client.task_restore_snapshot
    assert simulator_client.is_active is False
    assert (
        SimulatorServiceAccount.objects.filter(client=simulator_client, is_blocked=True).count()
        == 2
    )
    assert "SIMULAÇÃO" in output.getvalue()
    assert "Execute novamente com --apply" in output.getvalue()
    assert "Resultado: NOT READY" in output.getvalue()


def test_prepare_demo_apply_restores_only_allowlisted_projection_and_preserves_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(monkeypatch)
    client, simulator_client = _diverge_aurora_projection()
    completed_run = AutomationRun.objects.create(
        module=AutomationModule.objects.get(code="SC-05"),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.SUCCEEDED,
    )
    operation = SC05Operation.objects.create(
        run=completed_run,
        client=client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.HAPPY_PATH,
    )
    output = StringIO()

    call_command("prepare_demo", apply=True, stdout=output)

    client.refresh_from_db()
    simulator_client.refresh_from_db()
    assert client.status == SC05ClientStatus.ACTIVE
    assert client.task_restore_snapshot == {}
    assert simulator_client.is_active is True
    assert not SimulatorServiceAccount.objects.filter(
        client=simulator_client, is_blocked=True
    ).exists()
    expected_assignees = {
        "AURORA-FISCAL-01": ("maria.fiscal", True),
        "AURORA-CONTABIL-02": ("joao.contabil", True),
        "AURORA-ARQUIVO-03": ("ana.arquivos", False),
    }
    actual_assignees = {
        task.reference: (task.assignee, task.is_open)
        for task in SimulatorTask.objects.filter(client=simulator_client)
    }
    assert actual_assignees == expected_assignees
    assert not SimulatorTask.objects.filter(
        client=simulator_client, previous_assignee__gt=""
    ).exists()
    assert SC05Operation.objects.filter(pk=operation.pk).exists()
    assert AutomationRun.objects.filter(pk=completed_run.pk).exists()
    assert "APLICAÇÃO" in output.getvalue()
    assert "SC-06: PDF válido" in output.getvalue()


def test_prepare_demo_refuses_active_or_uncertain_sc05_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(monkeypatch)
    client = SC05Client.objects.get(external_reference="aurora-demo")
    active_run = AutomationRun.objects.create(
        module=AutomationModule.objects.get(code="SC-05"),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.QUEUED,
    )
    SC05Operation.objects.create(
        run=active_run,
        client=client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.HAPPY_PATH,
    )

    with pytest.raises(CommandError, match="operação SC-05 ativa"):
        call_command("prepare_demo", apply=True)

    active_run.status = RunStatus.CANCELLED
    active_run.save(update_fields=("status",))
    client.status = SC05ClientStatus.PARTIAL
    client.save(update_fields=("status",))

    with pytest.raises(CommandError, match="parcial ou a reconciliar"):
        call_command("prepare_demo", apply=True)


@override_settings(SC20_NOTIFICATION_BACKEND="smtp")
def test_prepare_demo_refuses_non_simulated_sc20_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(monkeypatch)

    with pytest.raises(CommandError, match="não está em modo simulated"):
        call_command("prepare_demo", apply=True)


def test_prepare_demo_refuses_stale_controlled_sc20_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(monkeypatch)
    certificate = DigitalCertificate.objects.get(serial_number="DEMO-CERT-008")
    certificate.valid_until = timezone.localdate() - timedelta(days=1)
    certificate.save(update_fields=("valid_until", "updated_at"))

    with pytest.raises(CommandError, match="seed_demo --refresh-certificate-dates"):
        call_command("prepare_demo")
