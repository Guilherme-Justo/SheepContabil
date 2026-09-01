from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from core.automations.models import (
    AutomationModule,
    RunStatus,
    SC05Action,
    SC05Artifact,
    SC05Client,
    SC05Portal,
)
from core.automations.sc05.contracts import StoredScreenshot
from core.automations.sc05.playwright import PlaywrightPortalSession
from core.automations.sc05.services import (
    BLOCKED_TASK_OWNER,
    create_sc05_run,
    execute_sc05,
    resume_sc05_run,
)
from core.identity.models import User
from core.sc05_simulator.models import (
    SimulatorClient,
    SimulatorServiceAccount,
    SimulatorSystem,
    SimulatorTask,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.urls("config.simulator_urls"),
]


class ContractScreenshotStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, *, key: str, content: bytes) -> StoredScreenshot:
        self.objects[key] = content
        return StoredScreenshot(
            key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
        )

    def get(self, *, key: str) -> bytes:
        return self.objects[key]


def test_chromium_operates_all_three_portals_only_through_visible_html(
    live_server,
    settings,
) -> None:
    settings.SC05_SIMULATOR_USERNAME = "robot.contract"
    settings.SC05_SIMULATOR_PASSWORD = "synthetic-contract-password"
    client = SimulatorClient.objects.create(
        external_id="aurora-contract",
        name="Aurora Contrato RPA Ltda.",
        document="12345678000190",
    )
    for system in SimulatorSystem.values:
        SimulatorServiceAccount.objects.create(client=client, system=system)
    SimulatorTask.objects.create(
        reference="CONTRACT-OPEN",
        client=client,
        title="Tarefa aberta do contrato",
        assignee="maria.operadora",
    )
    SimulatorTask.objects.create(
        reference="CONTRACT-CLOSED",
        client=client,
        title="Tarefa encerrada do contrato",
        assignee="joao.operador",
        is_open=False,
    )

    session = PlaywrightPortalSession(
        base_url=live_server.url,
        username=settings.SC05_SIMULATOR_USERNAME,
        password=settings.SC05_SIMULATOR_PASSWORD,
        timeout_ms=10_000,
    )
    with session as browser:
        files = browser.gateway(SC05Portal.FILES)
        accounting = browser.gateway(SC05Portal.ACCOUNTING)
        tasks = browser.gateway(SC05Portal.TASKS)

        assert files.inspect(
            client_reference=client.external_id,
            scenario="",
            phase="apply",
        ).state == {"blocked": False}
        assert accounting.apply(
            client_reference=client.external_id,
            action=SC05Action.BLOCK,
            scenario="",
            phase="apply",
        ).state == {"blocked": True}

        tasks_before = tasks.inspect(
            client_reference=client.external_id,
            scenario="",
            phase="apply",
        ).state
        tasks_blocked = tasks.apply(
            client_reference=client.external_id,
            action=SC05Action.BLOCK,
            scenario="",
            phase="apply",
        ).state
        assert tasks_blocked["client_active"] is True
        assert tasks_blocked["tasks"] == [
            {
                "reference": "CONTRACT-CLOSED",
                "assignee": "joao.operador",
                "is_open": False,
            },
            {
                "reference": "CONTRACT-OPEN",
                "assignee": BLOCKED_TASK_OWNER,
                "is_open": True,
            },
        ]

        restored = tasks.restore(
            client_reference=client.external_id,
            expected_current_state=tasks_blocked,
            target_state=tasks_before,
            scenario="",
            phase="compensation",
        )
        assert restored.state == tasks_before
        assert restored.screenshot.startswith(b"\x89PNG\r\n\x1a\n")


def test_complete_saga_uses_real_chromium_and_persists_each_visual_evidence(
    live_server,
    settings,
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    settings.SC05_SIMULATOR_USERNAME = "robot.saga"
    settings.SC05_SIMULATOR_PASSWORD = "synthetic-saga-password"
    client = SC05Client.objects.create(
        external_reference="lume-saga",
        name="Lume Saga RPA Ltda.",
        document="45678901000122",
    )
    simulator_client = SimulatorClient.objects.create(
        external_id=client.external_reference,
        name=client.name,
        document=client.document,
    )
    for system in SimulatorSystem.values:
        SimulatorServiceAccount.objects.create(client=simulator_client, system=system)
    task = SimulatorTask.objects.create(
        reference="SAGA-OPEN",
        client=simulator_client,
        title="Tarefa aberta da saga",
        assignee="giovana.operadora",
    )
    run = create_sc05_run(
        module=modules["SC-05"],
        client=client,
        action=SC05Action.BLOCK,
        scenario="happy_path",
        triggered_by=administrator,
        request_key=uuid4(),
    )
    storage = ContractScreenshotStorage()

    result = execute_sc05(
        run.id,
        gateways_factory=lambda: PlaywrightPortalSession(
            base_url=live_server.url,
            username=settings.SC05_SIMULATOR_USERNAME,
            password=settings.SC05_SIMULATOR_PASSWORD,
            timeout_ms=10_000,
        ),
        storage=storage,
    )

    run.refresh_from_db()
    client.refresh_from_db()
    task.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED
    assert result.applied == 3
    assert task.assignee == BLOCKED_TASK_OWNER
    assert (
        SC05Artifact.objects.filter(
            attempt__step__operation__run=run,
        ).count()
        == 6
    )
    assert len(storage.objects) == 6
    assert all(content.startswith(b"\x89PNG\r\n\x1a\n") for content in storage.objects.values())

    unblock_run = create_sc05_run(
        module=modules["SC-05"],
        client=client,
        action=SC05Action.UNBLOCK,
        scenario="happy_path",
        triggered_by=administrator,
        request_key=uuid4(),
    )
    unblock_result = execute_sc05(
        unblock_run.id,
        gateways_factory=lambda: PlaywrightPortalSession(
            base_url=live_server.url,
            username=settings.SC05_SIMULATOR_USERNAME,
            password=settings.SC05_SIMULATOR_PASSWORD,
            timeout_ms=10_000,
        ),
        storage=storage,
    )

    unblock_run.refresh_from_db()
    client.refresh_from_db()
    task.refresh_from_db()
    assert unblock_run.status == RunStatus.SUCCEEDED
    assert unblock_result.applied == 3
    assert client.status == "active"
    assert client.task_restore_snapshot == {}
    assert task.assignee == "giovana.operadora"
    assert (
        SC05Artifact.objects.filter(
            attempt__step__operation__run=unblock_run,
        ).count()
        == 6
    )
    assert len(storage.objects) == 12


def test_failed_browser_action_preserves_the_error_page_as_visual_evidence(
    live_server,
    settings,
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    settings.SC05_SIMULATOR_USERNAME = "robot.failure"
    settings.SC05_SIMULATOR_PASSWORD = "synthetic-failure-password"
    client = SC05Client.objects.create(
        external_reference="lume-failure",
        name="Lume Falha RPA Ltda.",
        document="45678901000133",
    )
    simulator_client = SimulatorClient.objects.create(
        external_id=client.external_reference,
        name=client.name,
        document=client.document,
    )
    for system in SimulatorSystem.values:
        SimulatorServiceAccount.objects.create(client=simulator_client, system=system)
    SimulatorTask.objects.create(
        reference="FAILURE-OPEN",
        client=simulator_client,
        title="Tarefa que provoca falha sintética",
        assignee="giovana.operadora",
    )
    run = create_sc05_run(
        module=modules["SC-05"],
        client=client,
        action=SC05Action.BLOCK,
        scenario="fail_tasks_apply",
        triggered_by=administrator,
        request_key=uuid4(),
    )
    storage = ContractScreenshotStorage()

    execute_sc05(
        run.id,
        gateways_factory=lambda: PlaywrightPortalSession(
            base_url=live_server.url,
            username=settings.SC05_SIMULATOR_USERNAME,
            password=settings.SC05_SIMULATOR_PASSWORD,
            timeout_ms=10_000,
        ),
        storage=storage,
    )

    run.refresh_from_db()
    failed_attempt = run.sc05_operation.steps.get(portal=SC05Portal.TASKS).attempts.get(
        operation="apply",
        status="failed",
    )
    artifact = failed_attempt.artifacts.get()
    assert run.status == RunStatus.FAILED
    assert artifact.storage_key.endswith("-apply-failure.png")
    assert storage.objects[artifact.storage_key].startswith(b"\x89PNG\r\n\x1a\n")


def test_real_browser_resumes_partial_saga_without_reapplying_conforming_portal(
    live_server,
    settings,
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    settings.SC05_SIMULATOR_USERNAME = "robot.resume"
    settings.SC05_SIMULATOR_PASSWORD = "synthetic-resume-password"
    client = SC05Client.objects.create(
        external_reference="lume-resume",
        name="Lume Retomada RPA Ltda.",
        document="45678901000144",
    )
    simulator_client = SimulatorClient.objects.create(
        external_id=client.external_reference,
        name=client.name,
        document=client.document,
    )
    for system in SimulatorSystem.values:
        SimulatorServiceAccount.objects.create(client=simulator_client, system=system)
    task = SimulatorTask.objects.create(
        reference="RESUME-OPEN",
        client=simulator_client,
        title="Tarefa da retomada sintética",
        assignee="giovana.operadora",
    )
    run = create_sc05_run(
        module=modules["SC-05"],
        client=client,
        action=SC05Action.BLOCK,
        scenario="fail_tasks_apply_and_files_compensation",
        triggered_by=administrator,
        request_key=uuid4(),
    )
    storage = ContractScreenshotStorage()

    def session_factory() -> PlaywrightPortalSession:
        return PlaywrightPortalSession(
            base_url=live_server.url,
            username=settings.SC05_SIMULATOR_USERNAME,
            password=settings.SC05_SIMULATOR_PASSWORD,
            timeout_ms=10_000,
        )

    first = execute_sc05(run.id, gateways_factory=session_factory, storage=storage)
    run.refresh_from_db()
    client.refresh_from_db()
    assert first.partially_failed is True
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert client.status == "partial"

    files_step = run.sc05_operation.steps.get(portal=SC05Portal.FILES)
    apply_attempts_before = files_step.attempts.filter(operation="apply").count()
    resume_sc05_run(run.id)
    resumed = execute_sc05(run.id, gateways_factory=session_factory, storage=storage)

    run.refresh_from_db()
    client.refresh_from_db()
    task.refresh_from_db()
    files_step.refresh_from_db()
    assert resumed.partially_failed is False
    assert run.status == RunStatus.SUCCEEDED
    assert client.status == "blocked"
    assert task.assignee == BLOCKED_TASK_OWNER
    assert files_step.attempts.filter(operation="apply").count() == apply_attempts_before
