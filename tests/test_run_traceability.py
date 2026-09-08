from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.automations import dispatching
from core.automations.models import (
    AutomationModule,
    AutomationRun,
    RunEventSource,
    RunEventType,
    RunStatus,
    RunTrigger,
    SC05Action,
    SC05Client,
    SC05ClientStatus,
    SC05Scenario,
)
from core.automations.reconciliation import reconcile_stale_runs
from core.automations.sc04.services import create_manual_sc04_inbox_run
from core.automations.sc05.services import create_sc05_run, resume_sc05_run
from core.automations.sc20.services import create_sc20_run
from core.automations.tasks import run_sc20_task
from core.automations.traceability import record_run_event
from core.identity.models import User

pytestmark = pytest.mark.django_db


def test_sc20_lifecycle_is_correlated_from_portal_to_terminal_state(
    modules: dict[str, AutomationModule],
    administrator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    monkeypatch.setattr(
        dispatching.run_sc20_task,
        "apply_async",
        lambda *, args, task_id: published.append(task_id),
    )
    run = create_sc20_run(triggered_by=administrator)

    task_id = dispatching.dispatch_run(run, failure_summary="Falha segura.")
    run_sc20_task.push_request(id=str(task_id), delivery_info={"redelivered": False})
    try:
        result = run_sc20_task.run(str(run.id))
    finally:
        run_sc20_task.pop_request()

    run.refresh_from_db()
    events = list(run.events.order_by("sequence"))
    assert result["selected"] == 0
    assert run.status == RunStatus.SUCCEEDED
    assert published == [str(task_id)]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        RunEventType.CREATED,
        RunEventType.QUEUED,
        RunEventType.DISPATCH_STARTED,
        RunEventType.BROKER_PUBLISHED,
        RunEventType.DELIVERY_RECEIVED,
        RunEventType.STARTED,
        RunEventType.SUCCEEDED,
        RunEventType.DELIVERY_FINISHED,
    ]
    assert {event.task_id for event in events if event.task_id} == {task_id}


def test_sc04_and_sc05_human_commands_record_actor_and_resume(
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
    technology_operator: User,
) -> None:
    sc04_run = create_manual_sc04_inbox_run(triggered_by=fiscal_operator)
    assert list(sc04_run.events.values_list("event_type", "actor_id")) == [
        (RunEventType.CREATED, fiscal_operator.id),
        (RunEventType.QUEUED, fiscal_operator.id),
    ]

    sc05_client = SC05Client.objects.create(
        external_reference="trace-client",
        name="Cliente Sintético",
        document="12345678000190",
    )
    sc05_run = create_sc05_run(
        module=modules["SC-05"],
        client=sc05_client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.HAPPY_PATH,
        triggered_by=technology_operator,
        request_key=uuid4(),
    )
    sc05_run.status = RunStatus.PARTIALLY_FAILED
    sc05_run.finished_at = timezone.now()
    sc05_run.save(update_fields=("status", "finished_at"))
    sc05_client.status = SC05ClientStatus.PARTIAL
    sc05_client.save(update_fields=("status", "updated_at"))

    resumed = resume_sc05_run(sc05_run.id, requested_by=technology_operator)

    event = resumed.events.get(event_type=RunEventType.RESUMED)
    assert resumed.status == RunStatus.QUEUED
    assert event.actor == technology_operator
    assert event.previous_status == RunStatus.PARTIALLY_FAILED
    assert event.current_status == RunStatus.QUEUED
    assert event.task_id == resumed.task_id


@override_settings(
    AUTOMATION_QUEUED_STALE_AFTER_SECONDS=60,
    AUTOMATION_RUNNING_STALE_AFTER_SECONDS=60,
    AUTOMATION_RECONCILIATION_MAX_ATTEMPTS=1,
)
def test_reconciler_quarantines_ambiguous_sc20_with_durable_evidence(
    modules: dict[str, AutomationModule],
) -> None:
    now = timezone.now()
    run = AutomationRun.objects.create(
        module=modules["SC-20"],
        trigger=RunTrigger.SCHEDULED,
        status=RunStatus.RUNNING,
        task_id=uuid4(),
        started_at=now - timedelta(minutes=5),
        heartbeat_at=now - timedelta(minutes=5),
    )
    old_task_id = run.task_id

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    event = run.events.get(event_type=RunEventType.QUARANTINED)
    assert result.quarantined == 1
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert run.task_id is None
    assert event.source == RunEventSource.RECONCILER
    assert event.task_id == old_task_id
    assert event.outcome == "ambiguous_delivery"


def test_run_timeline_obeys_rbac_and_hides_technical_ids_from_operator(
    client: Client,
    modules: dict[str, AutomationModule],
    administrator: User,
    processes_operator: User,
    fiscal_operator: User,
) -> None:
    run = AutomationRun.objects.create(
        module=modules["SC-20"],
        trigger=RunTrigger.MANUAL,
        status=RunStatus.QUEUED,
        triggered_by=processes_operator,
    )
    request_id = str(uuid4())
    task_id = uuid4()
    record_run_event(
        run=run,
        event_type=RunEventType.QUEUED,
        source=RunEventSource.WEB,
        actor=processes_operator,
        current_status=RunStatus.QUEUED,
        request_id=request_id,
        task_id=task_id,
        deduplication_key="timeline.example",
        message="Execução disponível para processamento.",
    )
    url = reverse("automations:run-detail", kwargs={"run_id": run.id})

    client.force_login(administrator)
    admin_content = client.get(url).content.decode()
    assert "Linha do tempo operacional" in admin_content
    assert "Execução disponível para processamento." in admin_content
    assert request_id in admin_content
    assert str(task_id) in admin_content

    client.force_login(processes_operator)
    operator_content = client.get(url).content.decode()
    assert "Linha do tempo operacional" in operator_content
    assert "Execução disponível para processamento." in operator_content
    assert request_id not in operator_content
    assert str(task_id) not in operator_content

    client.force_login(fiscal_operator)
    assert client.get(url).status_code == 404


def test_legacy_run_is_not_given_fabricated_history(
    client: Client,
    modules: dict[str, AutomationModule],
    processes_operator: User,
) -> None:
    run = AutomationRun.objects.create(
        module=modules["SC-20"],
        trigger=RunTrigger.SCHEDULED,
        status=RunStatus.SUCCEEDED,
    )
    client.force_login(processes_operator)

    content = client.get(
        reverse("automations:run-detail", kwargs={"run_id": run.id})
    ).content.decode()

    assert run.events.count() == 0
    assert "anterior à trilha unificada" in content
