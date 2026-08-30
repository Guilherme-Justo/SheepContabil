from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.automations.models import (
    AutomationComplexity,
    AutomationFrequency,
    AutomationModule,
    AutomationNature,
    AutomationRun,
    DigitalCertificate,
    RunStatus,
    RunTrigger,
)
from core.identity.models import Area, AreaMembership, User, UserRole

pytestmark = pytest.mark.django_db


def test_demo_seed_is_complete_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "safe-seed-admin-password")
    monkeypatch.setenv("DEMO_OPERATOR_PASSWORD", "safe-seed-operator-password")

    call_command("seed_demo", verbosity=0)
    call_command("seed_demo", verbosity=0)

    assert Area.objects.count() == 4
    assert set(AutomationModule.objects.values_list("code", flat=True)) == {
        "SC-04",
        "SC-05",
        "SC-06",
        "SC-20",
    }
    assert AutomationRun.objects.count() == 4
    assert DigitalCertificate.objects.count() == 7
    assert User.objects.count() == 2

    actual_modules = {
        module.code: (
            module.area.code,
            module.nature,
            module.complexity,
            module.frequency,
        )
        for module in AutomationModule.objects.select_related("area")
    }
    assert actual_modules == {
        "SC-04": (
            "fiscal",
            AutomationNature.AI_AGENT,
            AutomationComplexity.MEDIUM,
            AutomationFrequency.DAILY,
        ),
        "SC-05": (
            "tecnologia",
            AutomationNature.RPA,
            AutomationComplexity.MEDIUM,
            AutomationFrequency.ON_DEMAND,
        ),
        "SC-06": (
            "societario",
            AutomationNature.CONTROL,
            AutomationComplexity.MEDIUM,
            AutomationFrequency.ON_DEMAND,
        ),
        "SC-20": (
            "processos",
            AutomationNature.CONTROL,
            AutomationComplexity.LOW,
            AutomationFrequency.MONTHLY,
        ),
    }

    admin = User.objects.get(username="admin")
    operator = User.objects.get(username="operador.processos")
    assert admin.role == UserRole.ADMINISTRATOR
    assert admin.check_password("safe-seed-admin-password")
    assert operator.role == UserRole.OPERATOR
    assert operator.check_password("safe-seed-operator-password")
    assert AreaMembership.objects.filter(user=operator, area__code="processos").exists()


def test_run_exposes_duration_and_safe_status_tone(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    started = timezone.now()
    run = AutomationRun.objects.create(
        module=modules["SC-04"],
        trigger=RunTrigger.MANUAL,
        status=RunStatus.FAILED,
        triggered_by=administrator,
        started_at=started,
        finished_at=started + timedelta(minutes=2, seconds=7),
        error_message="Arquivo sintético fora do formato esperado.",
    )

    assert run.duration == timedelta(minutes=2, seconds=7)
    assert run.duration_label == "2 min 07 s"
    assert run.status_tone == "danger"
    assert str(run).startswith("SC-04 · Falhou")


def test_run_detail_obeys_the_same_area_policy(
    client: Client,
    modules: dict[str, AutomationModule],
    processes_operator: User,
) -> None:
    denied_run = AutomationRun.objects.create(
        module=modules["SC-04"],
        trigger=RunTrigger.SCHEDULED,
    )
    allowed_run = AutomationRun.objects.create(
        module=modules["SC-20"],
        trigger=RunTrigger.SCHEDULED,
    )
    client.force_login(processes_operator)

    denied = client.get(reverse("automations:run-detail", kwargs={"run_id": denied_run.id}))
    allowed = client.get(reverse("automations:run-detail", kwargs={"run_id": allowed_run.id}))

    assert denied.status_code == 404
    assert allowed.status_code == 200
    assert 'hx-trigger="every 2s"' in allowed.content.decode()
