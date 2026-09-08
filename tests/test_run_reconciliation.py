from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from io import StringIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.automations import dispatching, reconciliation
from core.automations import tasks as automation_tasks
from core.automations.checks import automation_settings_check
from core.automations.dispatching import dispatch_run
from core.automations.models import (
    AutomationModule,
    AutomationRun,
    ClassificationAttemptStatus,
    DocumentClassificationAttempt,
    DocumentDecision,
    DocumentDecisionOrigin,
    DocumentIntake,
    DocumentIntakeStatus,
    DocumentRouting,
    DocumentRoutingStatus,
    DocumentRunItem,
    DocumentRunOutcome,
    DocumentSource,
    DocumentStatus,
    DocumentType,
    FiscalClient,
    FiscalDocument,
    RunStatus,
    RunTrigger,
    SC05Action,
    SC05AttemptOperation,
    SC05AttemptStatus,
    SC05Client,
    SC05ClientStatus,
    SC05Scenario,
    SC05StepAttempt,
    SC05StepStatus,
)
from core.automations.reconciliation import reconcile_stale_runs
from core.automations.sc04.services import execute_sc04
from core.automations.sc05 import services as sc05_services
from core.automations.sc05.services import create_sc05_run, resume_sc05_run
from core.automations.sc20.services import create_sc20_run, execute_sc20
from core.automations.tasks import run_sc04_task, run_sc05_task, run_sc20_task
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _queued_run(
    module: AutomationModule,
    *,
    queued_at: datetime,
    task_id: UUID | None = None,
) -> AutomationRun:
    return AutomationRun.objects.create(
        module=module,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.QUEUED,
        queued_at=queued_at,
        task_id=task_id,
    )


def _running_run(
    module: AutomationModule,
    *,
    heartbeat_at: datetime,
    task_id: UUID,
    reconciliation_attempts: int = 0,
) -> AutomationRun:
    return AutomationRun.objects.create(
        module=module,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.RUNNING,
        task_id=task_id,
        started_at=heartbeat_at,
        heartbeat_at=heartbeat_at,
        reconciliation_attempts=reconciliation_attempts,
    )


def _add_processing_sc04_document(run: AutomationRun) -> tuple[FiscalDocument, DocumentIntake]:
    content = f"document-{run.id}".encode()
    digest = hashlib.sha256(content).hexdigest()
    document = FiscalDocument.objects.create(
        sha256=digest,
        storage_key=f"originals/{digest}.txt",
        media_type="text/plain",
        byte_size=len(content),
        status=DocumentStatus.PROCESSING,
    )
    intake = DocumentIntake.objects.create(
        document=document,
        run=run,
        source=DocumentSource.MANUAL,
        source_reference=f"reconciliation:{run.id}",
        original_filename="documento.txt",
        status=DocumentIntakeStatus.PROCESSING,
    )
    DocumentRunItem.objects.create(
        run=run,
        intake=intake,
        outcome=DocumentRunOutcome.NEW,
    )
    DocumentClassificationAttempt.objects.create(
        document=document,
        run=run,
        sequence=1,
        status=ClassificationAttemptStatus.PROCESSING,
        provider="test",
        model="test-v1",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        input_sha256="a" * 64,
        input_char_count=20,
    )
    return document, intake


def _add_queued_sc04_document(run: AutomationRun) -> tuple[FiscalDocument, DocumentIntake]:
    content = f"queued-document-{run.id}".encode()
    digest = hashlib.sha256(content).hexdigest()
    document = FiscalDocument.objects.create(
        sha256=digest,
        storage_key=f"originals/{digest}.txt",
        media_type="text/plain",
        byte_size=len(content),
        status=DocumentStatus.QUEUED,
    )
    intake = DocumentIntake.objects.create(
        document=document,
        run=run,
        source=DocumentSource.MANUAL,
        source_reference=f"queued-reconciliation:{run.id}",
        original_filename="documento-pendente.txt",
        status=DocumentIntakeStatus.QUEUED,
    )
    DocumentRunItem.objects.create(
        run=run,
        intake=intake,
        outcome=DocumentRunOutcome.NEW,
    )
    return document, intake


def _sc05_run(
    *,
    modules: dict[str, AutomationModule],
    administrator: User,
) -> tuple[AutomationRun, SC05Client]:
    client = SC05Client.objects.create(
        external_reference=f"reconciliation-{uuid4()}",
        name="Cliente da reconciliação",
        document=str(uuid4().int)[:14],
    )
    run = create_sc05_run(
        module=modules["SC-05"],
        client=client,
        action=SC05Action.BLOCK,
        scenario=SC05Scenario.HAPPY_PATH,
        triggered_by=administrator,
        request_key=uuid4(),
    )
    return run, client


def test_dispatch_persists_same_fencing_token_before_broker_publish(
    modules: dict[str, AutomationModule],
    administrator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del modules
    run = create_sc20_run(triggered_by=administrator)
    preclaimed_task_id = run.task_id
    assert preclaimed_task_id is not None
    observed: list[tuple[str, str]] = []

    def publish(*, args: tuple[str], task_id: str) -> None:
        persisted = AutomationRun.objects.get(pk=args[0])
        assert str(persisted.task_id) == task_id
        assert persisted.queued_at is not None
        observed.append((args[0], task_id))

    monkeypatch.setattr(dispatching.run_sc20_task, "apply_async", publish)

    task_id = dispatch_run(run, failure_summary="Falha segura.")

    run.refresh_from_db()
    assert observed == [(str(run.id), str(task_id))]
    assert run.task_id == task_id == preclaimed_task_id
    assert run.status == RunStatus.QUEUED
    assert run.dispatch_started_at is not None
    assert run.broker_published_at is not None
    assert run.heartbeat_at is None


def test_dispatch_is_idempotent_after_broker_confirmation(
    modules: dict[str, AutomationModule],
    administrator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del modules
    run = create_sc20_run(triggered_by=administrator)
    published: list[str] = []
    monkeypatch.setattr(
        dispatching.run_sc20_task,
        "apply_async",
        lambda *, args, task_id: published.append(task_id),
    )

    first = dispatch_run(run, failure_summary="Falha segura.")
    second = dispatch_run(run, failure_summary="Falha segura.")

    assert second == first
    assert published == [str(first)]


def test_dispatch_failure_is_safely_persisted_without_broker_detail(
    modules: dict[str, AutomationModule],
    administrator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del modules
    run = create_sc20_run(triggered_by=administrator)

    def unavailable(*, args: tuple[str], task_id: str) -> None:
        del args, task_id
        raise ConnectionError("private broker endpoint")

    monkeypatch.setattr(dispatching.run_sc20_task, "apply_async", unavailable)

    with pytest.raises(ConnectionError, match="private broker endpoint"):
        dispatch_run(run, failure_summary="A publicação falhou.")

    run.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert run.summary == "A publicação falhou."
    assert run.metadata["dispatch_error"] == "ConnectionError"
    assert "endpoint" not in run.error_message


def test_all_async_tasks_use_late_ack_and_worker_loss_redelivery() -> None:
    for task in (run_sc04_task, run_sc05_task, run_sc20_task):
        assert task.acks_late is True
        assert task.reject_on_worker_lost is True
    assert settings.CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS is True
    assert settings.CELERY_BROKER_TRANSPORT_OPTIONS == {
        "visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS
    }


def test_celery_adapters_forward_delivery_identity_and_redelivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    observed: list[tuple[str, str, bool]] = []

    def record_sc04(run_id: str, *, task_id: str, resume_interrupted: bool):
        observed.append((run_id, task_id, resume_interrupted))
        return SimpleNamespace(received=0, routed=0, awaiting_review=0, duplicates=0, failed=0)

    def record_sc05(run_id: str, *, task_id: str, resume_interrupted: bool):
        observed.append((run_id, task_id, resume_interrupted))
        return SimpleNamespace(
            applied=0,
            unchanged=0,
            compensated=0,
            failed=0,
            partially_failed=False,
        )

    def record_sc20(run_id: str, *, task_id: str, resume_interrupted: bool):
        observed.append((run_id, task_id, resume_interrupted))
        return SimpleNamespace(selected=0, sent=0, failed=0, deduplicated=0)

    monkeypatch.setattr(automation_tasks, "execute_sc04", record_sc04)
    monkeypatch.setattr(automation_tasks, "execute_sc05", record_sc05)
    monkeypatch.setattr(automation_tasks, "execute_sc20", record_sc20)
    for task, run_id in (
        (run_sc04_task, "sc04-run"),
        (run_sc05_task, "sc05-run"),
        (run_sc20_task, "sc20-run"),
    ):
        task.push_request(id=task_id, delivery_info={"redelivered": True})
        try:
            task.run(run_id)
        finally:
            task.pop_request()

    assert observed == [
        ("sc04-run", task_id, True),
        ("sc05-run", task_id, True),
        ("sc20-run", task_id, True),
    ]


def test_superseded_sc20_delivery_is_a_noop(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    del modules
    expected_task_id = uuid4()
    run = create_sc20_run(triggered_by=administrator)
    AutomationRun.objects.filter(pk=run.pk).update(task_id=expected_task_id)

    result = execute_sc20(run.id, task_id=uuid4())

    run.refresh_from_db()
    assert result.selected == result.sent == result.failed == 0
    assert run.status == RunStatus.QUEUED
    assert run.task_id == expected_task_id


def test_sc20_redelivery_quarantines_without_invoking_gateway(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    del modules
    task_id = uuid4()
    run = create_sc20_run(triggered_by=administrator)
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.RUNNING,
        task_id=task_id,
        started_at=timezone.now(),
        heartbeat_at=timezone.now(),
    )

    class ForbiddenGateway:
        def send(self, message):  # type: ignore[no-untyped-def]
            del message
            raise AssertionError("a redelivery não pode repetir comunicação")

    result = execute_sc20(
        run.id,
        task_id=task_id,
        resume_interrupted=True,
        gateway=ForbiddenGateway(),
    )

    run.refresh_from_db()
    assert result.sent == 0
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert run.task_id is None
    assert run.metadata["reconciliation_required"] is True
    assert run.metadata["reconciliation_history"][-1]["action"] == "quarantined"


def test_sc20_success_preserves_reconciliation_history(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    del modules
    task_id = uuid4()
    run = create_sc20_run(triggered_by=administrator)
    history = [{"action": "requeued", "reason": "stale_queue"}]
    AutomationRun.objects.filter(pk=run.pk).update(
        task_id=task_id,
        metadata={"reconciliation_history": history},
    )

    result = execute_sc20(run.id, task_id=task_id)

    run.refresh_from_db()
    assert result.selected == 0
    assert run.status == RunStatus.SUCCEEDED
    assert run.metadata["reconciliation_history"] == history
    assert run.metadata["result"] == {
        "deduplicated": 0,
        "failed": 0,
        "selected": 0,
        "sent": 0,
    }


def test_stale_pending_sc04_with_ingested_item_is_recovered_instead_of_orphaned(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run = AutomationRun.objects.create(
        module=modules["SC-04"],
        trigger=RunTrigger.MANUAL,
        status=RunStatus.PENDING,
    )
    AutomationRun.objects.filter(pk=run.pk).update(created_at=now - timedelta(hours=1))
    document, intake = _add_queued_sc04_document(run)
    published: list[UUID] = []
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda *, module_code, run_id, task_id: published.append(task_id),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    document.refresh_from_db()
    intake.refresh_from_db()
    assert result.requeued == 1
    assert run.status == RunStatus.QUEUED
    assert run.task_id is not None
    assert run.broker_published_at is not None
    assert published == [run.task_id]
    assert document.status == DocumentStatus.QUEUED
    assert intake.status == DocumentIntakeStatus.QUEUED
    assert run.metadata["reconciliation_history"][-1]["reason"] == ("stale_pending_after_ingestion")


def test_stale_queued_run_rotates_token_and_is_republished_at_exact_cutoff(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    previous_task_id = uuid4()
    run = _queued_run(
        modules["SC-20"],
        queued_at=now - timedelta(seconds=settings.AUTOMATION_QUEUED_STALE_AFTER_SECONDS),
        task_id=previous_task_id,
    )
    published: list[tuple[str, UUID, UUID]] = []
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda *, module_code, run_id, task_id: published.append((module_code, run_id, task_id)),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    assert result.requeued == 1
    assert result.publish_failed == 0
    assert run.task_id is not None and run.task_id != previous_task_id
    assert run.reconciliation_attempts == 1
    assert run.queued_at == now
    assert run.broker_published_at is not None
    assert published == [("SC-20", run.id, run.task_id)]
    event = run.metadata["reconciliation_history"][-1]
    assert event["previous_task_id"] == str(previous_task_id)
    assert event["reason"] == "stale_queue"


def test_fresh_queue_and_sc06_are_excluded(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    fresh = _queued_run(
        modules["SC-20"],
        queued_at=now - timedelta(seconds=settings.AUTOMATION_QUEUED_STALE_AFTER_SECONDS - 1),
        task_id=uuid4(),
    )
    sc06 = _running_run(
        modules["SC-06"],
        heartbeat_at=now - timedelta(days=2),
        task_id=uuid4(),
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now)

    fresh.refresh_from_db()
    sc06.refresh_from_db()
    assert result.inspected == 0
    assert fresh.status == RunStatus.QUEUED
    assert sc06.status == RunStatus.RUNNING


def test_confirmed_backlog_is_not_misclassified_as_an_orphan(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run = _queued_run(
        modules["SC-04"],
        queued_at=now - timedelta(days=2),
        task_id=uuid4(),
    )
    AutomationRun.objects.filter(pk=run.pk).update(
        dispatch_started_at=now - timedelta(days=2),
        broker_published_at=now - timedelta(days=2),
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    assert result.inspected == 0
    assert run.status == RunStatus.QUEUED
    assert run.reconciliation_attempts == 0


def test_dry_run_reports_action_without_mutation_or_publish(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    task_id = uuid4()
    run = _queued_run(
        modules["SC-04"],
        queued_at=now - timedelta(hours=1),
        task_id=task_id,
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now, dry_run=True)

    run.refresh_from_db()
    assert result.inspected == result.requeued == 1
    assert run.task_id == task_id
    assert run.reconciliation_attempts == 0
    assert run.metadata == {}


def test_recovery_publish_failure_remains_auditable_and_bounded(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run = _queued_run(
        modules["SC-20"],
        queued_at=now - timedelta(hours=1),
        task_id=uuid4(),
    )

    def unavailable(**kwargs) -> None:  # type: ignore[no-untyped-def]
        del kwargs
        raise ConnectionError("redis private address")

    monkeypatch.setattr(reconciliation, "publish_claimed_run", unavailable)

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    assert result.requeued == result.publish_failed == 1
    assert run.status == RunStatus.QUEUED
    assert run.reconciliation_attempts == 1
    assert "broker" in run.error_message
    assert run.metadata["reconciliation_history"][-1]["technical_error"] == "ConnectionError"

    second = reconcile_stale_runs(
        at=now + timedelta(seconds=settings.AUTOMATION_QUEUED_STALE_AFTER_SECONDS)
    )

    run.refresh_from_db()
    assert second.failed == 1
    assert second.publish_failed == 0
    assert run.status == RunStatus.FAILED
    assert run.task_id is None


def test_stale_running_sc04_closes_attempt_and_requeues_document(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    previous_task_id = uuid4()
    run = _running_run(
        modules["SC-04"],
        heartbeat_at=now - timedelta(hours=1),
        task_id=previous_task_id,
    )
    document, intake = _add_processing_sc04_document(run)
    published: list[UUID] = []
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda *, module_code, run_id, task_id: published.append(task_id),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    document.refresh_from_db()
    intake.refresh_from_db()
    attempt = DocumentClassificationAttempt.objects.get(run=run)
    assert result.requeued == 1
    assert run.status == RunStatus.QUEUED
    assert run.task_id is not None and run.task_id != previous_task_id
    assert published == [run.task_id]
    assert document.status == DocumentStatus.QUEUED
    assert intake.status == DocumentIntakeStatus.QUEUED
    assert attempt.status == ClassificationAttemptStatus.FAILED
    assert attempt.error_code == "worker_interrupted"


def test_sc04_recovery_exhaustion_closes_document_truthfully(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run = _running_run(
        modules["SC-04"],
        heartbeat_at=now - timedelta(hours=1),
        task_id=uuid4(),
        reconciliation_attempts=1,
    )
    document, intake = _add_processing_sc04_document(run)
    queued_document, queued_intake = _add_queued_sc04_document(run)
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    document.refresh_from_db()
    intake.refresh_from_db()
    queued_document.refresh_from_db()
    queued_intake.refresh_from_db()
    assert result.failed == 1
    assert run.status == RunStatus.FAILED
    assert run.task_id is None
    assert document.status == DocumentStatus.FAILED
    assert intake.status == DocumentIntakeStatus.FAILED
    assert queued_document.status == DocumentStatus.FAILED
    assert queued_intake.status == DocumentIntakeStatus.FAILED
    assert run.metadata["reconciliation_history"][-1]["reason"] == (
        "stale_running_recovery_exhausted"
    )


def test_sc04_exhaustion_closes_a_pending_route_without_reviving_the_run(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run = _running_run(
        modules["SC-04"],
        heartbeat_at=now - timedelta(hours=1),
        task_id=uuid4(),
        reconciliation_attempts=settings.AUTOMATION_RECONCILIATION_MAX_ATTEMPTS,
    )
    document, intake = _add_processing_sc04_document(run)
    fiscal_client = FiscalClient.objects.create(
        code="reconciliation-client",
        name="Cliente fiscal da reconciliação",
        document_number="12345678000190",
        route_prefix="reconciliation-client",
    )
    attempt = DocumentClassificationAttempt.objects.get(run=run)
    decision = DocumentDecision.objects.create(
        document=document,
        classification_attempt=attempt,
        document_type=DocumentType.INVOICE,
        client=fiscal_client,
        origin=DocumentDecisionOrigin.AUTOMATIC,
        policy_version="test-policy-v1",
    )
    route = DocumentRouting.objects.create(
        decision=decision,
        run=run,
        storage_key=f"routed/{document.id}.txt",
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    document.refresh_from_db()
    intake.refresh_from_db()
    route.refresh_from_db()
    assert result.failed == 1
    assert run.status == RunStatus.FAILED
    assert run.task_id is None
    assert document.status == DocumentStatus.FAILED
    assert intake.status == DocumentIntakeStatus.FAILED
    assert route.status == DocumentRoutingStatus.FAILED
    assert route.last_error


def test_sc04_broker_redelivery_exhaustion_closes_processing_and_queued_items(
    modules: dict[str, AutomationModule],
) -> None:
    now = timezone.now()
    task_id = uuid4()
    run = _running_run(
        modules["SC-04"],
        heartbeat_at=now,
        task_id=task_id,
        reconciliation_attempts=settings.AUTOMATION_RECONCILIATION_MAX_ATTEMPTS,
    )
    processing_document, processing_intake = _add_processing_sc04_document(run)
    queued_document, queued_intake = _add_queued_sc04_document(run)

    execute_sc04(run.id, task_id=task_id, resume_interrupted=True)

    run.refresh_from_db()
    processing_document.refresh_from_db()
    processing_intake.refresh_from_db()
    queued_document.refresh_from_db()
    queued_intake.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert run.task_id is None
    assert processing_document.status == DocumentStatus.FAILED
    assert processing_intake.status == DocumentIntakeStatus.FAILED
    assert queued_document.status == DocumentStatus.FAILED
    assert queued_intake.status == DocumentIntakeStatus.FAILED


def test_stale_running_sc05_closes_attempt_and_quarantines_without_replay(
    modules: dict[str, AutomationModule],
    administrator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run, sc05_client = _sc05_run(modules=modules, administrator=administrator)
    previous_task_id = uuid4()
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.RUNNING,
        task_id=previous_task_id,
        started_at=now - timedelta(hours=1),
        heartbeat_at=now - timedelta(hours=1),
    )
    step = run.sc05_operation.steps.order_by("position").first()
    assert step is not None
    step.status = SC05StepStatus.RUNNING
    step.started_at = now - timedelta(hours=1)
    step.save(update_fields=("status", "started_at", "updated_at"))
    attempt = SC05StepAttempt.objects.create(
        step=step,
        sequence=1,
        operation=SC05AttemptOperation.APPLY,
        status=SC05AttemptStatus.RUNNING,
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    sc05_client.refresh_from_db()
    step.refresh_from_db()
    attempt.refresh_from_db()
    assert result.quarantined == 1
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert run.task_id is None
    assert sc05_client.status == SC05ClientStatus.PARTIAL
    assert step.status == SC05StepStatus.FAILED
    assert attempt.status == SC05AttemptStatus.FAILED
    assert attempt.error_code == "worker_interrupted"
    assert run.metadata["reconciliation_required"] is True


def test_sc05_redelivery_while_still_queued_starts_without_consuming_recovery(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run, sc05_client = _sc05_run(modules=modules, administrator=administrator)
    assert run.task_id is not None

    operation, should_execute = sc05_services._prepare_operation(
        run.id,
        task_id=run.task_id,
        resume_interrupted=True,
    )

    run.refresh_from_db()
    sc05_client.refresh_from_db()
    assert should_execute is True
    assert operation.run_id == run.id
    assert run.status == RunStatus.RUNNING
    assert run.reconciliation_attempts == 0
    assert sc05_client.status == SC05ClientStatus.ACTIVE
    assert "reconciliation_history" not in run.metadata


def test_sc05_running_redelivery_quarantines_without_opening_a_gateway(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run, sc05_client = _sc05_run(modules=modules, administrator=administrator)
    task_id = run.task_id
    assert task_id is not None
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.RUNNING,
        started_at=timezone.now(),
        heartbeat_at=timezone.now(),
    )

    def forbidden_factory():  # type: ignore[no-untyped-def]
        raise AssertionError("a redelivery não pode abrir os portais")

    result = sc05_services.execute_sc05(
        run.id,
        task_id=task_id,
        resume_interrupted=True,
        gateways_factory=forbidden_factory,
    )

    run.refresh_from_db()
    sc05_client.refresh_from_db()
    assert result.partially_failed is True
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert run.task_id is None
    assert sc05_client.status == SC05ClientStatus.PARTIAL
    assert run.metadata["reconciliation_required"] is True


def test_sc05_resume_preclaims_a_new_token_and_rejects_the_old_delivery(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run, sc05_client = _sc05_run(modules=modules, administrator=administrator)
    old_task_id = run.task_id
    assert old_task_id is not None
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.PARTIALLY_FAILED,
        finished_at=timezone.now(),
    )
    sc05_client.status = SC05ClientStatus.PARTIAL
    sc05_client.save(update_fields=("status", "updated_at"))

    resumed = resume_sc05_run(run.id)
    operation, should_execute = sc05_services._prepare_operation(
        run.id,
        task_id=old_task_id,
        resume_interrupted=True,
    )

    resumed.refresh_from_db()
    assert resumed.task_id is not None and resumed.task_id != old_task_id
    assert resumed.status == RunStatus.QUEUED
    assert should_execute is False
    assert operation.run_id == resumed.id


def test_sc05_recovery_exhaustion_quarantines_client_as_partial(
    modules: dict[str, AutomationModule],
    administrator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run, sc05_client = _sc05_run(modules=modules, administrator=administrator)
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.RUNNING,
        task_id=uuid4(),
        started_at=now - timedelta(hours=1),
        heartbeat_at=now - timedelta(hours=1),
        reconciliation_attempts=1,
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    sc05_client.refresh_from_db()
    assert result.quarantined == 1
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert run.task_id is None
    assert sc05_client.status == SC05ClientStatus.PARTIAL
    assert run.metadata["reconciliation_required"] is True


def test_inconsistent_sc05_run_is_isolated_without_blocking_other_recovery(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    inconsistent = _running_run(
        modules["SC-05"],
        heartbeat_at=now - timedelta(hours=1),
        task_id=uuid4(),
    )
    recoverable = _queued_run(
        modules["SC-20"],
        queued_at=now - timedelta(hours=1),
        task_id=uuid4(),
    )
    published: list[UUID] = []
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda *, module_code, run_id, task_id: published.append(task_id),
    )

    result = reconcile_stale_runs(at=now)

    inconsistent.refresh_from_db()
    recoverable.refresh_from_db()
    assert result.inspected == 2
    assert result.quarantined == 1
    assert result.requeued == 1
    assert result.inspection_failed == 0
    assert inconsistent.status == RunStatus.PARTIALLY_FAILED
    assert inconsistent.metadata["reconciliation_required"] is True
    assert recoverable.status == RunStatus.QUEUED
    assert published == [recoverable.task_id]


def test_stale_running_sc20_is_quarantined_without_publication(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    run = _running_run(
        modules["SC-20"],
        heartbeat_at=now - timedelta(hours=1),
        task_id=uuid4(),
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_claimed_run",
        lambda **kwargs: pytest.fail(f"publicação inesperada: {kwargs}"),
    )

    result = reconcile_stale_runs(at=now)

    run.refresh_from_db()
    assert result.quarantined == 1
    assert run.status == RunStatus.PARTIALLY_FAILED
    assert run.task_id is None
    assert run.metadata["reconciliation_required"] is True


def test_standalone_reconciliation_command_supports_dry_run(
    modules: dict[str, AutomationModule],
) -> None:
    now = timezone.now()
    run = _queued_run(
        modules["SC-20"],
        queued_at=now - timedelta(hours=1),
        task_id=uuid4(),
    )
    output = StringIO()

    call_command("reconcile_automation_runs", dry_run=True, stdout=output)

    run.refresh_from_db()
    assert "Simulação: 1 inspecionada(s), 1 republicada(s)" in output.getvalue()
    assert run.reconciliation_attempts == 0


@pytest.mark.parametrize(
    ("overrides", "error_id"),
    (
        (
            {
                "CELERY_TASK_TIME_LIMIT": 900,
                "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS": 900,
            },
            "automations.E045",
        ),
        ({"AUTOMATION_QUEUED_STALE_AFTER_SECONDS": 0}, "automations.E046"),
        (
            {
                "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS": 1200,
                "AUTOMATION_RUNNING_STALE_AFTER_SECONDS": 1200,
            },
            "automations.E047",
        ),
        ({"AUTOMATION_RECONCILIATION_MAX_ATTEMPTS": 0}, "automations.E048"),
    ),
)
def test_reconciliation_system_checks_reject_unsafe_settings(
    overrides: dict[str, int],
    error_id: str,
) -> None:
    with override_settings(**overrides):
        errors = automation_settings_check(None)

    assert error_id in {error.id for error in errors}
