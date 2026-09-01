from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    RunStatus,
    SC05Action,
    SC05Artifact,
    SC05AttemptOperation,
    SC05AttemptStatus,
    SC05Client,
    SC05ClientStatus,
    SC05Scenario,
    SC05StepAttempt,
)
from core.automations.sc05.contracts import StoredScreenshot
from core.automations.sc05.services import create_sc05_run
from core.automations.tasks import run_sc05_task
from core.identity.models import User

pytestmark = pytest.mark.django_db


class DownloadStorage:
    content = b"\x89PNG\r\n\x1a\nsynthetic-screenshot"

    def get(self, *, key: str) -> bytes:
        assert key.startswith("sc05/")
        return self.content

    def put(self, *, key: str, content: bytes) -> StoredScreenshot:
        return StoredScreenshot(
            key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
        )


def _client() -> SC05Client:
    return SC05Client.objects.create(
        external_reference="aurora-view",
        name="Aurora Portal Ltda.",
        document="12345678000190",
    )


def _module_url(modules: dict[str, AutomationModule]) -> str:
    return reverse(
        "automations:module-detail",
        kwargs={"slug": modules["SC-05"].slug},
    )


def test_technology_operator_sees_sc05_and_other_area_receives_404(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    processes_operator: User,
) -> None:
    _client()
    client.force_login(technology_operator)
    allowed = client.get(_module_url(modules))
    client.force_login(processes_operator)
    denied = client.get(_module_url(modules))

    assert allowed.status_code == 200
    assert "Operar os três sistemas" in allowed.content.decode()
    assert "BLOQUEADO_INADIMPLENCIA" in allowed.content.decode()
    assert denied.status_code == 404


def test_operator_posts_happy_path_and_dispatches_only_the_run_uuid(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()
    dispatched: list[str] = []
    monkeypatch.setattr(run_sc05_task, "delay", lambda run_id: dispatched.append(run_id))
    client.force_login(technology_operator)

    response = client.post(
        _module_url(modules),
        {
            "client": str(sc05_client.id),
            "action": SC05Action.BLOCK,
            "scenario": SC05Scenario.HAPPY_PATH,
            "request_key": str(uuid4()),
        },
    )

    run = AutomationRun.objects.get(module=modules["SC-05"])
    assert response.status_code == 302
    assert response.url == reverse("automations:run-detail", kwargs={"run_id": run.id})
    assert dispatched == [str(run.id)]
    assert run.status == RunStatus.QUEUED
    assert list(run.sc05_operation.steps.values_list("position", flat=True)) == [1, 2, 3]


def test_failure_scenario_is_rejected_for_operator_and_available_to_admin(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    administrator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()
    monkeypatch.setattr(run_sc05_task, "delay", lambda run_id: None)
    payload = {
        "client": str(sc05_client.id),
        "action": SC05Action.BLOCK,
        "scenario": SC05Scenario.FAIL_TASKS_APPLY,
        "request_key": str(uuid4()),
    }
    client.force_login(technology_operator)
    denied = client.post(_module_url(modules), payload)
    assert denied.status_code == 200
    assert AutomationRun.objects.filter(module=modules["SC-05"]).count() == 0

    client.force_login(administrator)
    accepted = client.post(_module_url(modules), {**payload, "request_key": str(uuid4())})
    assert accepted.status_code == 302
    assert AutomationRun.objects.get(module=modules["SC-05"]).parameters["scenario"] == str(
        SC05Scenario.FAIL_TASKS_APPLY
    )


def test_partial_run_can_be_resumed_explicitly_by_authorized_operator(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()
    run = create_sc05_run(
        module=modules["SC-05"],
        client=sc05_client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION,
        triggered_by=technology_operator,
        request_key=uuid4(),
    )
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.PARTIALLY_FAILED,
        finished_at=timezone.now(),
    )
    SC05Client.objects.filter(pk=sc05_client.pk).update(status=SC05ClientStatus.PARTIAL)
    dispatched: list[str] = []
    monkeypatch.setattr(run_sc05_task, "delay", lambda run_id: dispatched.append(run_id))
    client.force_login(technology_operator)

    response = client.post(
        reverse("automations:sc05-resume", kwargs={"run_id": run.id}),
    )

    run.refresh_from_db()
    run.sc05_operation.refresh_from_db()
    assert response.status_code == 302
    assert run.status == RunStatus.QUEUED
    assert run.sc05_operation.scenario == SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION
    assert run.sc05_operation.resume_count == 1
    assert dispatched == [str(run.id)]


def test_run_detail_exposes_steps_and_private_artifact_obeys_rbac(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    processes_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()
    run = create_sc05_run(
        module=modules["SC-05"],
        client=sc05_client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.HAPPY_PATH,
        triggered_by=technology_operator,
        request_key=uuid4(),
    )
    step = run.sc05_operation.steps.first()
    assert step is not None
    attempt = SC05StepAttempt.objects.create(
        step=step,
        sequence=1,
        operation=SC05AttemptOperation.INSPECT,
        status=SC05AttemptStatus.SUCCEEDED,
        state_after={"blocked": False},
        finished_at=timezone.now(),
    )
    artifact = SC05Artifact.objects.create(
        attempt=attempt,
        storage_key=f"sc05/{run.id}/01-files/01-inspect.png",
        sha256=hashlib.sha256(DownloadStorage.content).hexdigest(),
        byte_size=len(DownloadStorage.content),
    )
    monkeypatch.setattr(
        "core.automations.views.build_screenshot_storage",
        lambda: DownloadStorage(),
    )
    client.force_login(technology_operator)
    detail = client.get(reverse("automations:run-detail", kwargs={"run_id": run.id}))
    allowed = client.get(reverse("automations:sc05-artifact", kwargs={"artifact_id": artifact.id}))
    client.force_login(processes_operator)
    denied = client.get(reverse("automations:sc05-artifact", kwargs={"artifact_id": artifact.id}))

    assert detail.status_code == 200
    assert "Portal de arquivos" in detail.content.decode()
    assert "Abrir captura" in detail.content.decode()
    assert allowed.status_code == 200
    assert allowed["Cache-Control"] == "private, no-store"
    assert allowed["X-Content-Type-Options"] == "nosniff"
    assert denied.status_code == 404


def test_artifact_download_rejects_corrupted_content(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()
    run = create_sc05_run(
        module=modules["SC-05"],
        client=sc05_client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.HAPPY_PATH,
        triggered_by=technology_operator,
        request_key=uuid4(),
    )
    step = run.sc05_operation.steps.first()
    assert step is not None
    attempt = SC05StepAttempt.objects.create(
        step=step,
        sequence=1,
        operation=SC05AttemptOperation.INSPECT,
        status=SC05AttemptStatus.SUCCEEDED,
        state_after={"blocked": False},
        finished_at=timezone.now(),
    )
    artifact = SC05Artifact.objects.create(
        attempt=attempt,
        storage_key=f"sc05/{run.id}/01-files/01-inspect.png",
        sha256=hashlib.sha256(b"expected").hexdigest(),
        byte_size=len(b"expected"),
    )
    monkeypatch.setattr(
        "core.automations.views.build_screenshot_storage",
        lambda: DownloadStorage(),
    )
    client.force_login(technology_operator)

    response = client.get(reverse("automations:sc05-artifact", kwargs={"artifact_id": artifact.id}))

    assert response.status_code == 503
    assert "temporariamente indisponível" in response.content.decode()


def test_broker_failure_is_recorded_as_safe_terminal_failure(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()

    def unavailable(run_id: str) -> None:
        del run_id
        raise ConnectionError("broker detail must not leak")

    monkeypatch.setattr(run_sc05_task, "delay", unavailable)
    client.force_login(technology_operator)
    response = client.post(
        _module_url(modules),
        {
            "client": str(sc05_client.id),
            "action": SC05Action.BLOCK,
            "scenario": SC05Scenario.HAPPY_PATH,
            "request_key": str(uuid4()),
        },
    )

    run = AutomationRun.objects.get(module=modules["SC-05"])
    assert response.status_code == 302
    assert run.status == RunStatus.FAILED
    assert run.error_message == "O serviço de execução está temporariamente indisponível."
    assert "broker detail" not in run.error_message
    assert run.metadata == {"dispatch_error": "ConnectionError"}


def test_repeated_idempotency_key_does_not_dispatch_the_same_run_twice(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()
    request_key = uuid4()
    dispatched: list[str] = []
    monkeypatch.setattr(run_sc05_task, "delay", lambda run_id: dispatched.append(run_id))
    client.force_login(technology_operator)
    payload = {
        "client": str(sc05_client.id),
        "action": SC05Action.BLOCK,
        "scenario": SC05Scenario.HAPPY_PATH,
        "request_key": str(request_key),
    }

    first = client.post(_module_url(modules), payload)
    repeated = client.post(_module_url(modules), payload)

    run = AutomationRun.objects.get(idempotency_key=f"sc05:manual:{request_key}")
    assert first.status_code == repeated.status_code == 302
    assert dispatched == [str(run.id)]
    assert AutomationRun.objects.filter(idempotency_key=f"sc05:manual:{request_key}").count() == 1


def test_broker_failure_during_resume_keeps_partial_run_retryable(
    client: Client,
    modules: dict[str, AutomationModule],
    technology_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc05_client = _client()
    run = create_sc05_run(
        module=modules["SC-05"],
        client=sc05_client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION,
        triggered_by=technology_operator,
        request_key=uuid4(),
    )
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.PARTIALLY_FAILED,
        error_message="Falha parcial anterior.",
        finished_at=timezone.now(),
    )
    SC05Client.objects.filter(pk=sc05_client.pk).update(status=SC05ClientStatus.PARTIAL)

    def unavailable(run_id: str) -> None:
        del run_id
        raise ConnectionError("private broker detail")

    monkeypatch.setattr(run_sc05_task, "delay", unavailable)
    client.force_login(technology_operator)
    response = client.post(reverse("automations:sc05-resume", kwargs={"run_id": run.id}))

    run.refresh_from_db()
    sc05_client.refresh_from_db()
    assert response.status_code == 302
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert sc05_client.status == SC05ClientStatus.PARTIAL
    assert run.sc05_operation.resume_count == 1
    assert run.metadata["dispatch_error"] == "ConnectionError"
    assert run.metadata["sc05_resume_history"][0]["previous_error"] == "Falha parcial anterior."
