from __future__ import annotations

import hashlib
from collections.abc import Callable
from copy import deepcopy
from types import TracebackType
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ValidationError

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
    SC05Operation,
    SC05Portal,
    SC05Scenario,
    SC05StepAttempt,
    SC05StepStatus,
)
from core.automations.sc05.contracts import (
    PortalEvidence,
    PortalGateway,
    PortalGatewaySession,
    PortalOperationError,
    PortalState,
    PortalStateConflictError,
    StoredScreenshot,
)
from core.automations.sc05.services import (
    BLOCKED_TASK_OWNER,
    create_sc05_run,
    execute_sc05,
    resume_sc05_run,
)
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _initial_portal_states() -> dict[SC05Portal, PortalState]:
    return {
        SC05Portal.FILES: {"blocked": False},
        SC05Portal.ACCOUNTING: {"blocked": False},
        SC05Portal.TASKS: {
            "client_active": True,
            "tasks": [
                {
                    "reference": "TASK-OPEN-01",
                    "is_open": True,
                    "assignee": "maria.operadora",
                },
                {
                    "reference": "TASK-CLOSED-01",
                    "is_open": False,
                    "assignee": "joao.operador",
                },
            ],
        },
    }


class MemoryScreenshotStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, *, key: str, content: bytes) -> StoredScreenshot:
        self.objects.setdefault(key, content)
        stored = self.objects[key]
        return StoredScreenshot(
            key=key,
            sha256=hashlib.sha256(stored).hexdigest(),
            byte_size=len(stored),
        )

    def get(self, *, key: str) -> bytes:
        return self.objects[key]


class MemoryPortalGateway:
    def __init__(self, session: MemoryPortalGatewaySession, portal: SC05Portal) -> None:
        self.session = session
        self.selected_portal = portal
        self.portal = str(portal)

    def inspect(
        self,
        *,
        client_reference: str,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        self.session.record(self.selected_portal, "inspect", scenario, phase, client_reference)
        if phase == "compensation" and self.selected_portal in self.session.divergent_states:
            self.session.states[self.selected_portal] = deepcopy(
                self.session.divergent_states[self.selected_portal]
            )
        return self.session.evidence(self.selected_portal, "inspect", phase)

    def apply(
        self,
        *,
        client_reference: str,
        action: SC05Action,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        self.session.record(
            self.selected_portal,
            f"apply:{action}",
            scenario,
            phase,
            client_reference,
        )
        if self.selected_portal in self.session.forced_apply_failures:
            raise PortalOperationError(
                "O portal recusou a alteração forçada pelo teste.",
                code="forced_apply_failure",
                transient=True,
            )
        if self.selected_portal == SC05Portal.TASKS and scenario in {
            SC05Scenario.FAIL_TASKS_APPLY,
            SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION,
        }:
            raise PortalOperationError(
                "O sistema de tarefas recusou a alteração sintética.",
                code="tasks_apply_failed",
                transient=True,
            )

        if self.selected_portal in {SC05Portal.FILES, SC05Portal.ACCOUNTING}:
            self.session.states[self.selected_portal] = {"blocked": action == SC05Action.BLOCK}
        elif action == SC05Action.BLOCK:
            current = deepcopy(self.session.states[SC05Portal.TASKS])
            tasks = current["tasks"]
            assert isinstance(tasks, list)
            for task in tasks:
                assert isinstance(task, dict)
                if task["is_open"] is True:
                    task["assignee"] = BLOCKED_TASK_OWNER
            self.session.states[SC05Portal.TASKS] = current
        else:
            if self.session.task_unblock_snapshot is None:
                raise PortalStateConflictError(
                    "O fake não recebeu o snapshot das tarefas para desbloqueio."
                )
            snapshot_tasks = self.session.task_unblock_snapshot["tasks"]
            assert isinstance(snapshot_tasks, list)
            restore_assignees = {
                str(task["reference"]): str(task["assignee"])
                for task in snapshot_tasks
                if isinstance(task, dict) and task.get("is_open") is True
            }
            current = deepcopy(self.session.states[SC05Portal.TASKS])
            tasks = current["tasks"]
            assert isinstance(tasks, list)
            for task in tasks:
                assert isinstance(task, dict)
                reference = str(task["reference"])
                if reference in restore_assignees and task["assignee"] == BLOCKED_TASK_OWNER:
                    task["assignee"] = restore_assignees[reference]
            self.session.states[SC05Portal.TASKS] = current
        return self.session.evidence(self.selected_portal, "apply", phase)

    def restore(
        self,
        *,
        client_reference: str,
        expected_current_state: PortalState,
        target_state: PortalState,
        scenario: str,
        phase: str,
    ) -> PortalEvidence:
        self.session.record(
            self.selected_portal,
            "restore",
            scenario,
            phase,
            client_reference,
        )
        if self.session.states[self.selected_portal] != expected_current_state:
            raise PortalStateConflictError()
        if (
            self.selected_portal == SC05Portal.FILES
            and scenario == SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION
        ):
            raise PortalOperationError(
                "O portal de arquivos recusou a compensação sintética.",
                code="files_compensation_failed",
                transient=True,
            )
        self.session.states[self.selected_portal] = deepcopy(target_state)
        return self.session.evidence(self.selected_portal, "restore", phase)


class MemoryPortalGatewaySession:
    def __init__(
        self,
        states: dict[SC05Portal, PortalState] | None = None,
        *,
        task_unblock_snapshot: PortalState | None = None,
        divergent_states: dict[SC05Portal, PortalState] | None = None,
        forced_apply_failures: set[SC05Portal] | None = None,
    ) -> None:
        self.states = deepcopy(states or _initial_portal_states())
        self.task_unblock_snapshot = deepcopy(task_unblock_snapshot)
        self.divergent_states = deepcopy(divergent_states or {})
        self.forced_apply_failures = forced_apply_failures or set()
        self.calls: list[tuple[SC05Portal, str, str, str, str]] = []
        self.enter_count = 0
        self.exit_count = 0
        self._gateways = {portal: MemoryPortalGateway(self, portal) for portal in SC05Portal}

    def __enter__(self) -> MemoryPortalGatewaySession:
        self.enter_count += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.exit_count += 1

    def gateway(self, portal: SC05Portal) -> PortalGateway:
        return self._gateways[portal]

    def record(
        self,
        portal: SC05Portal,
        operation: str,
        scenario: str,
        phase: str,
        client_reference: str,
    ) -> None:
        self.calls.append((portal, operation, scenario, phase, client_reference))

    def evidence(self, portal: SC05Portal, operation: str, phase: str) -> PortalEvidence:
        sequence = len(self.calls)
        screenshot = f"{portal}:{operation}:{phase}:{sequence}".encode()
        return PortalEvidence(state=deepcopy(self.states[portal]), screenshot=screenshot)


def _factory(session: MemoryPortalGatewaySession) -> Callable[[], PortalGatewaySession]:
    return lambda: session


def _client() -> SC05Client:
    return SC05Client.objects.create(
        external_reference="aurora-demo",
        name="Aurora Demonstração Ltda.",
        document="12345678000190",
    )


def _create_run(
    *,
    modules: dict[str, AutomationModule],
    administrator: User,
    client: SC05Client,
    action: SC05Action = SC05Action.BLOCK,
    scenario: SC05Scenario = SC05Scenario.HAPPY_PATH,
    request_key: UUID | None = None,
) -> AutomationRun:
    return create_sc05_run(
        module=modules["SC-05"],
        client=client,
        action=action,
        scenario=scenario,
        triggered_by=administrator,
        request_key=request_key or uuid4(),
    )


def test_command_creation_orders_steps_and_is_idempotent_by_request_key(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    client = _client()
    request_key = uuid4()

    first = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        request_key=request_key,
    )
    repeated = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        request_key=request_key,
    )

    assert repeated.pk == first.pk
    assert AutomationRun.objects.filter(idempotency_key=f"sc05:manual:{request_key}").count() == 1
    assert SC05Operation.objects.count() == 1
    assert list(
        first.sc05_operation.steps.order_by("position").values_list("portal", flat=True)
    ) == [SC05Portal.FILES, SC05Portal.ACCOUNTING, SC05Portal.TASKS]

    with pytest.raises(ValidationError, match="operação em andamento"):
        _create_run(
            modules=modules,
            administrator=administrator,
            client=client,
        )


def test_happy_block_applies_three_portals_and_only_marks_open_tasks(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    session = MemoryPortalGatewaySession(initial)
    storage = MemoryScreenshotStorage()
    client = _client()
    run = _create_run(modules=modules, administrator=administrator, client=client)

    result = execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=storage,
    )

    run.refresh_from_db()
    client.refresh_from_db()
    tasks_state = session.states[SC05Portal.TASKS]
    tasks = tasks_state["tasks"]
    assert isinstance(tasks, list)
    assert result.applied == 3
    assert result.failed == 0
    assert run.status == RunStatus.SUCCEEDED
    assert client.status == SC05ClientStatus.BLOCKED
    assert client.task_restore_snapshot == initial[SC05Portal.TASKS]
    assert session.states[SC05Portal.FILES] == {"blocked": True}
    assert session.states[SC05Portal.ACCOUNTING] == {"blocked": True}
    assert tasks_state["client_active"] is True
    assert tasks[0]["assignee"] == BLOCKED_TASK_OWNER
    assert tasks[1]["assignee"] == "joao.operador"
    assert list(run.sc05_operation.steps.values_list("status", flat=True)) == [
        SC05StepStatus.APPLIED,
        SC05StepStatus.APPLIED,
        SC05StepStatus.APPLIED,
    ]
    assert (
        SC05StepAttempt.objects.filter(
            step__operation=run.sc05_operation,
            status=SC05AttemptStatus.SUCCEEDED,
        ).count()
        == 6
    )
    assert SC05Artifact.objects.filter(attempt__step__operation=run.sc05_operation).count() == 6
    assert len(storage.objects) == 6
    assert session.enter_count == session.exit_count == 1

    calls_before_redelivery = list(session.calls)
    repeated = execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=storage,
    )
    assert repeated == result
    assert session.calls == calls_before_redelivery
    assert session.enter_count == 1


def test_unblock_restores_the_exact_snapshot_from_the_prior_block(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    shared_states = deepcopy(initial)
    client = _client()
    block_run = _create_run(modules=modules, administrator=administrator, client=client)
    block_session = MemoryPortalGatewaySession(shared_states)
    execute_sc05(
        block_run.id,
        gateways_factory=_factory(block_session),
        storage=MemoryScreenshotStorage(),
    )
    client.refresh_from_db()
    shared_states = block_session.states

    unblock_run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        action=SC05Action.UNBLOCK,
    )
    unblock_session = MemoryPortalGatewaySession(
        shared_states,
        task_unblock_snapshot=client.task_restore_snapshot,
    )
    result = execute_sc05(
        unblock_run.id,
        gateways_factory=_factory(unblock_session),
        storage=MemoryScreenshotStorage(),
    )

    unblock_run.refresh_from_db()
    client.refresh_from_db()
    assert result.applied == 3
    assert unblock_run.status == RunStatus.SUCCEEDED
    assert client.status == SC05ClientStatus.ACTIVE
    assert client.task_restore_snapshot == {}
    assert unblock_session.states == initial
    assert list(
        unblock_run.sc05_operation.steps.order_by("position").values_list("portal", flat=True)
    ) == [SC05Portal.TASKS, SC05Portal.ACCOUNTING, SC05Portal.FILES]


def test_tasks_failure_compensates_files_and_accounting_and_finishes_failed(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    session = MemoryPortalGatewaySession(initial)
    client = _client()
    run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        scenario=SC05Scenario.FAIL_TASKS_APPLY,
    )

    result = execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=MemoryScreenshotStorage(),
    )

    run.refresh_from_db()
    client.refresh_from_db()
    steps = {step.portal: step for step in run.sc05_operation.steps.all()}
    assert run.status == RunStatus.FAILED
    assert client.status == SC05ClientStatus.ACTIVE
    assert result.compensated == 2
    assert result.failed == 1
    assert result.partially_failed is False
    assert session.states == initial
    assert steps[SC05Portal.FILES].status == SC05StepStatus.COMPENSATED
    assert steps[SC05Portal.ACCOUNTING].status == SC05StepStatus.COMPENSATED
    assert steps[SC05Portal.TASKS].status == SC05StepStatus.FAILED
    assert (
        steps[SC05Portal.TASKS]
        .attempts.filter(
            operation=SC05AttemptOperation.APPLY,
            status=SC05AttemptStatus.FAILED,
            error_code="tasks_apply_failed",
        )
        .exists()
    )


def test_tasks_failure_with_files_compensation_failure_is_partially_failed(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    session = MemoryPortalGatewaySession(initial)
    client = _client()
    run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        scenario=SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION,
    )

    result = execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=MemoryScreenshotStorage(),
    )

    run.refresh_from_db()
    client.refresh_from_db()
    steps = {step.portal: step for step in run.sc05_operation.steps.all()}
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert client.status == SC05ClientStatus.PARTIAL
    assert result.partially_failed is True
    assert session.states[SC05Portal.FILES] == {"blocked": True}
    assert session.states[SC05Portal.ACCOUNTING] == initial[SC05Portal.ACCOUNTING]
    assert session.states[SC05Portal.TASKS] == initial[SC05Portal.TASKS]
    assert steps[SC05Portal.FILES].status == SC05StepStatus.COMPENSATION_FAILED
    assert steps[SC05Portal.ACCOUNTING].status == SC05StepStatus.COMPENSATED
    assert steps[SC05Portal.TASKS].status == SC05StepStatus.FAILED


def test_resume_reconciles_partial_failure_without_reapplying_a_conforming_portal(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    session = MemoryPortalGatewaySession(_initial_portal_states())
    storage = MemoryScreenshotStorage()
    client = _client()
    run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        scenario=SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION,
    )
    execute_sc05(run.id, gateways_factory=_factory(session), storage=storage)
    calls_before_resume = len(session.calls)

    resumed = resume_sc05_run(run.id)
    result = execute_sc05(
        resumed.id,
        gateways_factory=_factory(session),
        storage=storage,
    )

    resumed.refresh_from_db()
    client.refresh_from_db()
    resumed.sc05_operation.refresh_from_db()
    resume_calls = session.calls[calls_before_resume:]
    assert resumed.status == RunStatus.SUCCEEDED
    assert client.status == SC05ClientStatus.BLOCKED
    assert resumed.sc05_operation.resume_count == 1
    assert result.applied == 3
    assert not any(
        portal == SC05Portal.FILES and operation.startswith("apply:")
        for portal, operation, _scenario, phase, _client in resume_calls
        if phase == "apply"
    )
    assert any(
        portal == SC05Portal.FILES and operation == "inspect"
        for portal, operation, _scenario, phase, _client in resume_calls
        if phase == "apply"
    )
    assert session.states[SC05Portal.FILES] == {"blocked": True}
    assert session.states[SC05Portal.ACCOUNTING] == {"blocked": True}


def test_divergent_external_state_prevents_destructive_compensation(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    divergent_files_state = {"blocked": False, "external_revision": "manual-change"}
    session = MemoryPortalGatewaySession(
        _initial_portal_states(),
        divergent_states={SC05Portal.FILES: divergent_files_state},
    )
    client = _client()
    run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        scenario=SC05Scenario.FAIL_TASKS_APPLY,
    )

    result = execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=MemoryScreenshotStorage(),
    )

    run.refresh_from_db()
    client.refresh_from_db()
    files_step = run.sc05_operation.steps.get(portal=SC05Portal.FILES)
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert client.status == SC05ClientStatus.PARTIAL
    assert result.partially_failed is True
    assert files_step.status == SC05StepStatus.COMPENSATION_FAILED
    assert "mudou depois da captura" in files_step.error_message
    assert session.states[SC05Portal.FILES] == divergent_files_state
    assert not any(
        portal == SC05Portal.FILES and operation == "restore"
        for portal, operation, _scenario, phase, _client in session.calls
        if phase == "compensation"
    )


def test_partial_failure_is_terminal_until_explicit_resume(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    session = MemoryPortalGatewaySession(_initial_portal_states())
    client = _client()
    run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        scenario=SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION,
    )
    first = execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=MemoryScreenshotStorage(),
    )
    calls_before_redelivery = list(session.calls)

    repeated = execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=MemoryScreenshotStorage(),
        resume_interrupted=True,
    )

    run.refresh_from_db()
    assert first.partially_failed is True
    assert repeated.partially_failed is True
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert run.sc05_operation.resume_count == 0
    assert session.calls == calls_before_redelivery


def test_failed_resume_with_complete_compensation_restores_client_projection(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    first_session = MemoryPortalGatewaySession(initial)
    client = _client()
    run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        scenario=SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION,
    )
    execute_sc05(
        run.id,
        gateways_factory=_factory(first_session),
        storage=MemoryScreenshotStorage(),
    )
    run.refresh_from_db()
    original_started_at = run.started_at
    original_finished_at = run.finished_at

    resume_sc05_run(run.id)
    resume_session = MemoryPortalGatewaySession(
        first_session.states,
        forced_apply_failures={SC05Portal.TASKS},
    )
    result = execute_sc05(
        run.id,
        gateways_factory=_factory(resume_session),
        storage=MemoryScreenshotStorage(),
    )

    run.refresh_from_db()
    client.refresh_from_db()
    run.sc05_operation.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert client.status == SC05ClientStatus.ACTIVE
    assert resume_session.states == initial
    assert result.partially_failed is False
    assert run.started_at == original_started_at
    assert run.sc05_operation.scenario == SC05Scenario.FAIL_TASKS_AND_FILES_COMPENSATION
    assert run.sc05_operation.resume_count == 1
    assert run.metadata["sc05_resume_history"][0]["previous_finished_at"] == (
        original_finished_at.isoformat() if original_finished_at else None
    )


def test_block_refuses_preexisting_task_marker_without_overwriting_unknown_state(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    tasks = initial[SC05Portal.TASKS]["tasks"]
    assert isinstance(tasks, list)
    tasks[0]["assignee"] = BLOCKED_TASK_OWNER
    session = MemoryPortalGatewaySession(initial)
    client = _client()
    run = _create_run(modules=modules, administrator=administrator, client=client)

    execute_sc05(
        run.id,
        gateways_factory=_factory(session),
        storage=MemoryScreenshotStorage(),
    )

    run.refresh_from_db()
    client.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert client.status == SC05ClientStatus.UNKNOWN
    assert session.states == initial
    assert "snapshot confiável" in run.error_message
    assert not any(
        portal == SC05Portal.TASKS and operation.startswith("apply:")
        for portal, operation, _scenario, _phase, _client in session.calls
    )


def test_unblock_preserves_account_that_was_blocked_before_sc05(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    initial[SC05Portal.FILES] = {"blocked": True}
    client = _client()
    block_session = MemoryPortalGatewaySession(initial)
    block_run = _create_run(modules=modules, administrator=administrator, client=client)
    execute_sc05(
        block_run.id,
        gateways_factory=_factory(block_session),
        storage=MemoryScreenshotStorage(),
    )
    client.refresh_from_db()

    unblock_run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        action=SC05Action.UNBLOCK,
    )
    unblock_session = MemoryPortalGatewaySession(
        block_session.states,
        task_unblock_snapshot=client.task_restore_snapshot,
    )
    execute_sc05(
        unblock_run.id,
        gateways_factory=_factory(unblock_session),
        storage=MemoryScreenshotStorage(),
    )

    unblock_run.refresh_from_db()
    client.refresh_from_db()
    assert unblock_run.status == RunStatus.SUCCEEDED
    assert client.status == SC05ClientStatus.ACTIVE
    assert unblock_session.states[SC05Portal.FILES] == {"blocked": True}
    assert unblock_session.states[SC05Portal.ACCOUNTING] == {"blocked": False}


def test_unblock_restores_assignees_without_freezing_task_lifecycle(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    initial = _initial_portal_states()
    client = _client()
    block_session = MemoryPortalGatewaySession(initial)
    block_run = _create_run(modules=modules, administrator=administrator, client=client)
    execute_sc05(
        block_run.id,
        gateways_factory=_factory(block_session),
        storage=MemoryScreenshotStorage(),
    )
    client.refresh_from_db()
    tasks = block_session.states[SC05Portal.TASKS]["tasks"]
    assert isinstance(tasks, list)
    tasks[0]["is_open"] = False
    tasks.append(
        {
            "reference": "TASK-NEW-02",
            "is_open": True,
            "assignee": "nova.operadora",
        }
    )

    unblock_run = _create_run(
        modules=modules,
        administrator=administrator,
        client=client,
        action=SC05Action.UNBLOCK,
    )
    unblock_session = MemoryPortalGatewaySession(
        block_session.states,
        task_unblock_snapshot=client.task_restore_snapshot,
    )
    execute_sc05(
        unblock_run.id,
        gateways_factory=_factory(unblock_session),
        storage=MemoryScreenshotStorage(),
    )

    unblock_run.refresh_from_db()
    restored_tasks = unblock_session.states[SC05Portal.TASKS]["tasks"]
    assert isinstance(restored_tasks, list)
    restored = {str(task["reference"]): task for task in restored_tasks}
    assert unblock_run.status == RunStatus.SUCCEEDED
    assert restored["TASK-OPEN-01"]["assignee"] == "maria.operadora"
    assert restored["TASK-OPEN-01"]["is_open"] is False
    assert restored["TASK-NEW-02"]["assignee"] == "nova.operadora"
    assert restored["TASK-NEW-02"]["is_open"] is True
