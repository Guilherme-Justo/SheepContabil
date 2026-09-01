from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    RunStatus,
    RunTrigger,
    SC05Action,
    SC05Artifact,
    SC05AttemptOperation,
    SC05AttemptStatus,
    SC05Client,
    SC05ClientStatus,
    SC05Operation,
    SC05Portal,
    SC05PortalStep,
    SC05Scenario,
    SC05StepAttempt,
    SC05StepStatus,
)
from core.automations.sc05.artifacts import build_screenshot_storage
from core.automations.sc05.contracts import (
    PortalEvidence,
    PortalGatewaySession,
    PortalOperationError,
    PortalState,
    PortalStateConflictError,
    SC05Error,
    SC05ExecutionResult,
    ScreenshotStorage,
    StoredScreenshot,
)
from core.identity.models import User

BLOCKED_TASK_OWNER = "BLOQUEADO_INADIMPLENCIA"
ACTIVE_RUN_STATUSES = (RunStatus.PENDING, RunStatus.QUEUED, RunStatus.RUNNING)
BLOCK_ORDER = (SC05Portal.FILES, SC05Portal.ACCOUNTING, SC05Portal.TASKS)
UNBLOCK_ORDER = tuple(reversed(BLOCK_ORDER))


@dataclass(frozen=True, slots=True)
class SC05RunCreation:
    run: AutomationRun
    created: bool


def create_sc05_run(
    *,
    module: AutomationModule,
    client: SC05Client,
    action: str,
    scenario: str,
    triggered_by: User,
    request_key: UUID,
) -> AutomationRun:
    return create_sc05_run_result(
        module=module,
        client=client,
        action=action,
        scenario=scenario,
        triggered_by=triggered_by,
        request_key=request_key,
    ).run


def create_sc05_run_result(
    *,
    module: AutomationModule,
    client: SC05Client,
    action: str,
    scenario: str,
    triggered_by: User,
    request_key: UUID,
) -> SC05RunCreation:
    selected_action = SC05Action(action)
    selected_scenario = SC05Scenario(scenario)
    idempotency_key = f"sc05:manual:{request_key}"

    with transaction.atomic():
        locked_client = SC05Client.objects.select_for_update().get(pk=client.pk)
        existing = (
            AutomationRun.objects.select_related("sc05_operation")
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None:
            return SC05RunCreation(run=existing, created=False)

        if locked_client.status in {SC05ClientStatus.PARTIAL, SC05ClientStatus.UNKNOWN}:
            raise ValidationError(
                "O cliente possui estado parcial; retome ou reconcilie a execução anterior."
            )
        if selected_action == SC05Action.BLOCK and locked_client.status == SC05ClientStatus.BLOCKED:
            raise ValidationError("O cliente já está bloqueado nos sistemas.")
        if selected_action == SC05Action.UNBLOCK:
            if locked_client.status != SC05ClientStatus.BLOCKED:
                raise ValidationError("O cliente não está bloqueado.")
            if not locked_client.task_restore_snapshot:
                raise ValidationError(
                    "Os responsáveis anteriores das tarefas não estão disponíveis para restauração."
                )

        if SC05Operation.objects.filter(
            client=locked_client,
            run__status__in=ACTIVE_RUN_STATUSES,
        ).exists():
            raise ValidationError("Já existe uma operação em andamento para este cliente.")

        run = AutomationRun.objects.create(
            module=module,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.QUEUED,
            triggered_by=triggered_by,
            parameters={
                "client_id": str(locked_client.id),
                "action": selected_action,
                "scenario": selected_scenario,
            },
            idempotency_key=idempotency_key,
            summary=(
                f"{selected_action.label} {locked_client.name}: operação aguardando o worker RPA."
            ),
        )
        operation = SC05Operation.objects.create(
            run=run,
            client=locked_client,
            action=selected_action,
            scenario=selected_scenario,
        )
        order = BLOCK_ORDER if selected_action == SC05Action.BLOCK else UNBLOCK_ORDER
        SC05PortalStep.objects.bulk_create(
            [
                SC05PortalStep(operation=operation, portal=portal, position=position)
                for position, portal in enumerate(order, start=1)
            ]
        )
    return SC05RunCreation(run=run, created=True)


def resume_sc05_run(run_id: str | UUID) -> AutomationRun:
    with transaction.atomic():
        operation = (
            SC05Operation.objects.select_for_update()
            .select_related("run", "client")
            .get(run_id=UUID(str(run_id)))
        )
        run = operation.run
        if run.status != RunStatus.PARTIALLY_FAILED:
            raise ValidationError("Somente uma execução com falha parcial pode ser retomada.")
        if (
            SC05Operation.objects.filter(
                client=operation.client,
                run__status__in=ACTIVE_RUN_STATUSES,
            )
            .exclude(pk=operation.pk)
            .exists()
        ):
            raise ValidationError("Já existe outra operação em andamento para este cliente.")
        previous_finished_at = run.finished_at
        previous_summary = run.summary
        previous_error = run.error_message
        operation.resume_count += 1
        resume_history = list(run.metadata.get("sc05_resume_history", []))
        resume_history.append(
            {
                "sequence": operation.resume_count,
                "requested_at": timezone.now().isoformat(),
                "previous_finished_at": (
                    previous_finished_at.isoformat() if previous_finished_at else None
                ),
                "previous_summary": previous_summary,
                "previous_error": previous_error,
            }
        )
        run.status = RunStatus.QUEUED
        run.summary = (
            f"Retomada de {operation.get_action_display().lower()} aguardando o worker RPA."
        )
        run.error_message = ""
        run.finished_at = None
        run.metadata = {**run.metadata, "sc05_resume_history": resume_history}
        run.save(update_fields=("status", "summary", "error_message", "finished_at", "metadata"))
        operation.save(update_fields=("resume_count", "updated_at"))
    return run


def execute_sc05(
    run_id: str | UUID,
    *,
    gateways_factory: Callable[[], PortalGatewaySession] | None = None,
    storage: ScreenshotStorage | None = None,
    resume_interrupted: bool = False,
) -> SC05ExecutionResult:
    operation = _prepare_operation(run_id, resume_interrupted=resume_interrupted)
    if operation.run.status not in ACTIVE_RUN_STATUSES:
        return _summarize(operation)

    if gateways_factory is None:
        from core.automations.sc05.playwright import build_playwright_session

        gateways_factory = build_playwright_session
    failed_step: SC05PortalStep | None = None
    failure: SC05Error | None = None

    try:
        storage = storage or build_screenshot_storage()
        with gateways_factory() as gateways:
            for step in operation.steps.order_by("position"):
                try:
                    _apply_step(
                        operation=operation,
                        step=step,
                        gateways=gateways,
                        storage=storage,
                    )
                except SC05Error as exc:
                    failed_step = step
                    failure = exc
                    _mark_step_failed(step, exc)
                    break
            if failure is not None:
                compensation_failed = _compensate(
                    operation=operation,
                    failed_step=failed_step,
                    gateways=gateways,
                    storage=storage,
                )
                _finish_failed(operation, failure, compensation_failed=compensation_failed)
            else:
                _finish_succeeded(operation)
    except SC05Error as exc:
        _finish_failed(operation, exc, compensation_failed=_has_residual_state(operation))
    except Exception as exc:
        safe_error = PortalOperationError(
            "O worker RPA foi interrompido antes de confirmar todos os sistemas.",
            code="unexpected_worker_error",
            transient=True,
        )
        _finish_failed(operation, safe_error, compensation_failed=_has_residual_state(operation))
        raise safe_error from exc

    operation.refresh_from_db()
    operation.run.refresh_from_db()
    return _summarize(operation)


def _prepare_operation(
    run_id: str | UUID,
    *,
    resume_interrupted: bool,
) -> SC05Operation:
    with transaction.atomic():
        operation = (
            SC05Operation.objects.select_for_update()
            .select_related("run", "client")
            .get(run_id=UUID(str(run_id)))
        )
        run = operation.run
        if run.status not in ACTIVE_RUN_STATUSES:
            return operation
        if run.status == RunStatus.RUNNING and not resume_interrupted:
            raise PortalOperationError(
                "A execução já está sendo processada por outro worker.",
                code="run_already_running",
            )
        if resume_interrupted:
            now = timezone.now()
            running_attempts = SC05StepAttempt.objects.filter(
                step__operation=operation,
                status=SC05AttemptStatus.RUNNING,
            )
            for attempt in running_attempts:
                attempt.status = SC05AttemptStatus.FAILED
                attempt.error_code = "worker_interrupted"
                attempt.error_message = "A tentativa foi interrompida antes da confirmação."
                attempt.finished_at = now
                attempt.save(
                    update_fields=(
                        "status",
                        "error_code",
                        "error_message",
                        "finished_at",
                    )
                )
            SC05PortalStep.objects.filter(
                operation=operation,
                status=SC05StepStatus.RUNNING,
            ).update(
                status=SC05StepStatus.FAILED,
                error_message="A etapa foi interrompida e será reconciliada.",
                finished_at=now,
            )
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.finished_at = None
        run.error_message = ""
        run.summary = f"{operation.get_action_display()} em andamento nos sistemas simulados."
        run.save(update_fields=("status", "started_at", "finished_at", "error_message", "summary"))
    return operation


def _apply_step(
    *,
    operation: SC05Operation,
    step: SC05PortalStep,
    gateways: PortalGatewaySession,
    storage: ScreenshotStorage,
) -> None:
    prior_status = step.status
    first_observation = not step.before_state
    step.status = SC05StepStatus.RUNNING
    step.started_at = step.started_at or timezone.now()
    step.finished_at = None
    step.error_message = ""
    step.save(update_fields=("status", "started_at", "finished_at", "error_message", "updated_at"))
    gateway = gateways.gateway(SC05Portal(step.portal))
    observed = _invoke(
        step=step,
        operation=SC05AttemptOperation.INSPECT,
        storage=storage,
        call=lambda: gateway.inspect(
            client_reference=operation.client.external_reference,
            scenario=_execution_scenario(operation),
            phase="apply",
        ),
    )
    if first_observation:
        _validate_initial_state(operation=operation, step=step, observed=observed.state)
        step.before_state = deepcopy(observed.state)
    desired = (
        deepcopy(step.desired_state)
        if not first_observation and step.desired_state
        else _desired_state(operation, step, observed.state)
    )
    step.desired_state = desired
    step.after_state = deepcopy(observed.state)
    step.save(update_fields=("before_state", "desired_state", "after_state", "updated_at"))

    if (
        not first_observation
        and not _states_equal(observed.state, step.before_state)
        and not _states_equal(observed.state, desired)
    ):
        raise PortalStateConflictError()

    if _states_equal(observed.state, desired):
        step.status = (
            SC05StepStatus.APPLIED
            if prior_status in {SC05StepStatus.APPLIED, SC05StepStatus.COMPENSATION_FAILED}
            else SC05StepStatus.UNCHANGED
        )
        step.finished_at = timezone.now()
        step.save(update_fields=("status", "finished_at", "updated_at"))
        return

    changed = _invoke(
        step=step,
        operation=SC05AttemptOperation.APPLY,
        storage=storage,
        call=lambda: gateway.apply(
            client_reference=operation.client.external_reference,
            action=SC05Action(operation.action),
            scenario=_execution_scenario(operation),
            phase="apply",
        ),
    )
    step.after_state = deepcopy(changed.state)
    if not _states_equal(changed.state, desired):
        step.save(update_fields=("after_state", "updated_at"))
        raise PortalStateConflictError(
            f"{step.get_portal_display()} não confirmou o estado solicitado."
        )
    step.status = SC05StepStatus.APPLIED
    step.error_message = ""
    step.finished_at = timezone.now()
    step.save(update_fields=("status", "after_state", "error_message", "finished_at", "updated_at"))


def _desired_state(
    operation: SC05Operation,
    step: SC05PortalStep,
    observed: PortalState,
) -> PortalState:
    action = SC05Action(operation.action)
    portal = SC05Portal(step.portal)
    if portal in {SC05Portal.FILES, SC05Portal.ACCOUNTING}:
        if action == SC05Action.BLOCK:
            return {"blocked": True}
        return _prior_block_state(operation=operation, portal=portal)
    if action == SC05Action.UNBLOCK:
        return _task_unblock_state(operation=operation, observed=observed)
    tasks = observed.get("tasks")
    if observed.get("client_active") is not True or not isinstance(tasks, list):
        raise PortalStateConflictError("O sistema de tarefas retornou um estado inválido.")
    desired_tasks: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("reference"), str):
            raise PortalStateConflictError("O sistema de tarefas retornou uma tarefa inválida.")
        desired_task = deepcopy(task)
        if task.get("is_open") is True:
            desired_task["assignee"] = BLOCKED_TASK_OWNER
        desired_tasks.append(desired_task)
    return {"client_active": True, "tasks": desired_tasks}


def _validate_initial_state(
    *,
    operation: SC05Operation,
    step: SC05PortalStep,
    observed: PortalState,
) -> None:
    if operation.action != SC05Action.BLOCK or step.portal != SC05Portal.TASKS:
        return
    tasks = observed.get("tasks")
    if observed.get("client_active") is not True or not isinstance(tasks, list):
        raise PortalStateConflictError("O sistema de tarefas retornou um estado inválido.")
    if any(isinstance(task, dict) and task.get("assignee") == BLOCKED_TASK_OWNER for task in tasks):
        raise PortalOperationError(
            "Há tarefa com o marcador de inadimplência sem snapshot confiável; "
            "o cliente precisa ser reconciliado antes de um novo bloqueio.",
            code="task_marker_without_snapshot",
        )


def _prior_block_state(*, operation: SC05Operation, portal: SC05Portal) -> PortalState:
    prior_step = (
        SC05PortalStep.objects.filter(
            operation__client=operation.client,
            operation__action=SC05Action.BLOCK,
            operation__run__status=RunStatus.SUCCEEDED,
            operation__created_at__lt=operation.created_at,
            portal=portal,
        )
        .order_by("-operation__created_at")
        .first()
    )
    if prior_step is None or set(prior_step.before_state) != {"blocked"}:
        raise PortalStateConflictError(
            f"O snapshot anterior de {portal.label.lower()} não está disponível para desfazer."
        )
    blocked = prior_step.before_state.get("blocked")
    if not isinstance(blocked, bool):
        raise PortalStateConflictError("O snapshot anterior da conta é inválido.")
    return {"blocked": blocked}


def _task_unblock_state(*, operation: SC05Operation, observed: PortalState) -> PortalState:
    snapshot = operation.client.task_restore_snapshot
    snapshot_tasks = snapshot.get("tasks") if isinstance(snapshot, dict) else None
    current_tasks = observed.get("tasks")
    if (
        snapshot.get("client_active") is not True
        or not isinstance(snapshot_tasks, list)
        or observed.get("client_active") is not True
        or not isinstance(current_tasks, list)
    ):
        raise PortalStateConflictError(
            "Não há responsáveis anteriores válidos para desbloquear as tarefas."
        )

    restore_assignees: dict[str, str] = {}
    for task in snapshot_tasks:
        if not isinstance(task, dict) or task.get("is_open") is not True:
            continue
        reference = task.get("reference")
        assignee = task.get("assignee")
        if not isinstance(reference, str) or not reference or not isinstance(assignee, str):
            raise PortalStateConflictError("O snapshot anterior das tarefas é inválido.")
        if reference in restore_assignees:
            raise PortalStateConflictError("O snapshot anterior possui tarefas duplicadas.")
        restore_assignees[reference] = assignee

    desired_tasks = deepcopy(current_tasks)
    current_by_reference: dict[str, dict[str, Any]] = {}
    for task in desired_tasks:
        if not isinstance(task, dict):
            raise PortalStateConflictError("O sistema de tarefas retornou uma tarefa inválida.")
        reference = task.get("reference")
        assignee = task.get("assignee")
        if not isinstance(reference, str) or not reference or not isinstance(assignee, str):
            raise PortalStateConflictError("O sistema de tarefas retornou uma tarefa inválida.")
        if reference in current_by_reference:
            raise PortalStateConflictError("O sistema de tarefas retornou referências duplicadas.")
        current_by_reference[reference] = task

    unexpected_markers = [
        reference
        for reference, task in current_by_reference.items()
        if task["assignee"] == BLOCKED_TASK_OWNER and reference not in restore_assignees
    ]
    if unexpected_markers:
        raise PortalStateConflictError(
            "Há tarefas bloqueadas que não pertencem ao snapshot desta operação."
        )

    for reference, previous_assignee in restore_assignees.items():
        current = current_by_reference.get(reference)
        if current is None:
            raise PortalStateConflictError(
                "Uma tarefa do snapshot não está mais disponível para restauração."
            )
        if current["assignee"] not in {BLOCKED_TASK_OWNER, previous_assignee}:
            raise PortalStateConflictError(
                "O responsável de uma tarefa mudou depois do bloqueio; nada foi sobrescrito."
            )
        current["assignee"] = previous_assignee
    return {"client_active": True, "tasks": desired_tasks}


def _execution_scenario(operation: SC05Operation) -> str:
    if operation.resume_count:
        return str(SC05Scenario.HAPPY_PATH)
    return str(operation.scenario)


def _invoke(
    *,
    step: SC05PortalStep,
    operation: SC05AttemptOperation,
    storage: ScreenshotStorage,
    call: Callable[[], PortalEvidence],
) -> PortalEvidence:
    attempt = _start_attempt(step, operation)
    evidence: PortalEvidence | None = None
    try:
        evidence = call()
        if operation in {SC05AttemptOperation.APPLY, SC05AttemptOperation.COMPENSATE}:
            step.after_state = deepcopy(evidence.state)
            step.save(update_fields=("after_state", "updated_at"))
        key = (
            f"sc05/{step.operation.run_id}/{step.position:02d}-{step.portal}/"
            f"{attempt.sequence:02d}-{operation}.png"
        )
        stored = storage.put(key=key, content=evidence.screenshot)
        _record_artifact(attempt=attempt, stored=stored)
    except SC05Error as exc:
        if isinstance(exc, PortalOperationError) and exc.screenshot:
            with suppress(Exception):
                key = (
                    f"sc05/{step.operation.run_id}/{step.position:02d}-{step.portal}/"
                    f"{attempt.sequence:02d}-{operation}-failure.png"
                )
                stored = storage.put(key=key, content=exc.screenshot)
                _record_artifact(attempt=attempt, stored=stored)
        _fail_attempt(attempt, exc, evidence.state if evidence else {})
        raise
    except Exception as exc:
        error = PortalOperationError(
            f"{step.get_portal_display()} ficou indisponível durante a operação.",
            code="portal_unavailable",
            transient=True,
        )
        _fail_attempt(attempt, error, evidence.state if evidence else {})
        raise error from exc
    _succeed_attempt(attempt, evidence.state)
    return evidence


def _record_artifact(*, attempt: SC05StepAttempt, stored: StoredScreenshot) -> None:
    SC05Artifact.objects.create(
        attempt=attempt,
        storage_key=stored.key,
        sha256=stored.sha256,
        content_type=stored.content_type,
        byte_size=stored.byte_size,
    )


def _start_attempt(
    step: SC05PortalStep,
    operation: SC05AttemptOperation,
) -> SC05StepAttempt:
    latest = step.attempts.aggregate(value=Max("sequence"))["value"] or 0
    return SC05StepAttempt.objects.create(
        step=step,
        sequence=int(latest) + 1,
        operation=operation,
        status=SC05AttemptStatus.RUNNING,
        state_before=deepcopy(step.after_state or step.before_state),
    )


def _succeed_attempt(attempt: SC05StepAttempt, state: PortalState) -> None:
    attempt.status = SC05AttemptStatus.SUCCEEDED
    attempt.state_after = deepcopy(state)
    attempt.finished_at = timezone.now()
    attempt.save(update_fields=("status", "state_after", "finished_at"))


def _fail_attempt(
    attempt: SC05StepAttempt,
    error: SC05Error,
    state: PortalState,
) -> None:
    attempt.status = SC05AttemptStatus.FAILED
    attempt.state_after = deepcopy(state)
    attempt.error_code = error.code
    attempt.error_message = error.safe_message
    attempt.finished_at = timezone.now()
    attempt.save(
        update_fields=(
            "status",
            "state_after",
            "error_code",
            "error_message",
            "finished_at",
        )
    )


def _mark_step_failed(step: SC05PortalStep, error: SC05Error) -> None:
    step.status = SC05StepStatus.FAILED
    step.error_message = error.safe_message
    step.finished_at = timezone.now()
    step.save(update_fields=("status", "error_message", "finished_at", "updated_at"))


def _compensate(
    *,
    operation: SC05Operation,
    failed_step: SC05PortalStep | None,
    gateways: PortalGatewaySession,
    storage: ScreenshotStorage,
) -> bool:
    compensation_failed = False
    candidates = list(operation.steps.exclude(before_state={}).order_by("-position"))
    for step in candidates:
        if failed_step is not None and step.position > failed_step.position:
            continue
        if not step.desired_state or not step.before_state:
            continue
        gateway = gateways.gateway(SC05Portal(step.portal))
        try:
            current = _invoke(
                step=step,
                operation=SC05AttemptOperation.INSPECT,
                storage=storage,
                call=partial(
                    gateway.inspect,
                    client_reference=operation.client.external_reference,
                    scenario=_execution_scenario(operation),
                    phase="compensation",
                ),
            )
            if _states_equal(current.state, step.before_state):
                if step.status == SC05StepStatus.FAILED:
                    continue
                step.status = SC05StepStatus.COMPENSATED
                step.after_state = deepcopy(current.state)
                step.error_message = ""
                step.finished_at = timezone.now()
                step.save(
                    update_fields=(
                        "status",
                        "after_state",
                        "error_message",
                        "finished_at",
                        "updated_at",
                    )
                )
                continue
            if not _states_equal(current.state, step.desired_state):
                raise PortalStateConflictError()
            restored = _invoke(
                step=step,
                operation=SC05AttemptOperation.COMPENSATE,
                storage=storage,
                call=partial(
                    gateway.restore,
                    client_reference=operation.client.external_reference,
                    expected_current_state=current.state,
                    target_state=deepcopy(step.before_state),
                    scenario=_execution_scenario(operation),
                    phase="compensation",
                ),
            )
            if not _states_equal(restored.state, step.before_state):
                raise PortalStateConflictError(
                    f"{step.get_portal_display()} não confirmou a restauração."
                )
            step.status = SC05StepStatus.COMPENSATED
            step.after_state = deepcopy(restored.state)
            step.error_message = ""
            step.finished_at = timezone.now()
            step.save(
                update_fields=(
                    "status",
                    "after_state",
                    "error_message",
                    "finished_at",
                    "updated_at",
                )
            )
        except SC05Error as exc:
            compensation_failed = True
            step.status = SC05StepStatus.COMPENSATION_FAILED
            step.error_message = exc.safe_message
            step.finished_at = timezone.now()
            step.save(update_fields=("status", "error_message", "finished_at", "updated_at"))
    return compensation_failed


def _finish_succeeded(operation: SC05Operation) -> None:
    with transaction.atomic():
        locked = (
            SC05Operation.objects.select_for_update()
            .select_related("run", "client")
            .get(pk=operation.pk)
        )
        run = locked.run
        client = locked.client
        if locked.action == SC05Action.BLOCK:
            task_step = locked.steps.get(portal=SC05Portal.TASKS)
            client.status = SC05ClientStatus.BLOCKED
            client.task_restore_snapshot = deepcopy(task_step.before_state)
        else:
            client.status = SC05ClientStatus.ACTIVE
            client.task_restore_snapshot = {}
        client.save(update_fields=("status", "task_restore_snapshot", "updated_at"))
        applied = locked.steps.filter(status=SC05StepStatus.APPLIED).count()
        unchanged = locked.steps.filter(status=SC05StepStatus.UNCHANGED).count()
        run.status = RunStatus.SUCCEEDED
        run.summary = (
            f"{locked.get_action_display()} concluído para {client.name}: "
            f"{applied} sistema(s) alterado(s) e {unchanged} já conforme."
        )
        run.error_message = ""
        run.finished_at = timezone.now()
        run.save(update_fields=("status", "summary", "error_message", "finished_at"))


def _finish_failed(
    operation: SC05Operation,
    error: SC05Error,
    *,
    compensation_failed: bool,
) -> None:
    with transaction.atomic():
        locked = (
            SC05Operation.objects.select_for_update()
            .select_related("run", "client")
            .get(pk=operation.pk)
        )
        run = locked.run
        if error.code == "task_marker_without_snapshot":
            locked.client.status = SC05ClientStatus.UNKNOWN
            locked.client.save(update_fields=("status", "updated_at"))
            run.status = RunStatus.FAILED
            run.summary = (
                f"{locked.get_action_display()} não iniciado com segurança para "
                f"{locked.client.name}; o estado anterior das tarefas exige reconciliação."
            )
        elif compensation_failed:
            locked.client.status = SC05ClientStatus.PARTIAL
            locked.client.save(update_fields=("status", "updated_at"))
            run.status = RunStatus.PARTIALLY_FAILED
            run.summary = (
                f"{locked.get_action_display()} interrompido para {locked.client.name}; "
                "há estado residual que exige retomada."
            )
        else:
            locked.client.status = (
                SC05ClientStatus.ACTIVE
                if locked.action == SC05Action.BLOCK
                else SC05ClientStatus.BLOCKED
            )
            locked.client.save(update_fields=("status", "updated_at"))
            run.status = RunStatus.FAILED
            run.summary = (
                f"{locked.get_action_display()} não concluído para {locked.client.name}; "
                "as alterações confirmadas foram restauradas."
            )
        run.error_message = error.safe_message
        run.finished_at = timezone.now()
        run.save(update_fields=("status", "summary", "error_message", "finished_at"))


def _has_residual_state(operation: SC05Operation) -> bool:
    return operation.steps.filter(
        status__in=(SC05StepStatus.APPLIED, SC05StepStatus.COMPENSATION_FAILED)
    ).exists()


def _summarize(operation: SC05Operation) -> SC05ExecutionResult:
    counts = {
        status: operation.steps.filter(status=status).count() for status in SC05StepStatus.values
    }
    return SC05ExecutionResult(
        applied=counts[SC05StepStatus.APPLIED],
        unchanged=counts[SC05StepStatus.UNCHANGED],
        compensated=counts[SC05StepStatus.COMPENSATED],
        failed=counts[SC05StepStatus.FAILED] + counts[SC05StepStatus.COMPENSATION_FAILED],
        partially_failed=operation.run.status == RunStatus.PARTIALLY_FAILED,
    )


def _states_equal(left: PortalState, right: PortalState) -> bool:
    return left == right
