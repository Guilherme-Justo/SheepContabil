from __future__ import annotations

from uuid import uuid4

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory

from config.trace_context import trace_context
from core.automations.admin import AutomationRunAdmin, AutomationRunEventAdmin
from core.automations.models import (
    AutomationModule,
    AutomationRun,
    AutomationRunEvent,
    RunEventSource,
    RunEventType,
    RunStatus,
    RunTrigger,
)
from core.automations.traceability import (
    TraceabilityConflict,
    UnsafeTraceabilityData,
    record_run_event,
)
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _run(module: AutomationModule, actor: User) -> AutomationRun:
    return AutomationRun.objects.create(
        module=module,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.QUEUED,
        triggered_by=actor,
    )


def test_recorder_allocates_ordered_sequences_and_is_idempotent(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run = _run(modules["SC-20"], administrator)
    request_id = str(uuid4())
    pulse_id = uuid4()
    task_id = uuid4()

    with trace_context(request_id=request_id, pulse_id=str(pulse_id)):
        created, was_created = record_run_event(
            run=run,
            event_type=RunEventType.CREATED,
            source=RunEventSource.WEB,
            actor=administrator,
            current_status=RunStatus.QUEUED,
            task_id=task_id,
            deduplication_key="run.created",
            message="Execução criada.",
        )
        repeated, repeated_created = record_run_event(
            run=run,
            event_type=RunEventType.CREATED,
            source=RunEventSource.WEB,
            actor=administrator,
            current_status=RunStatus.QUEUED,
            task_id=task_id,
            deduplication_key="run.created",
            message="Execução criada.",
        )
        queued, queued_created = record_run_event(
            run=run,
            event_type=RunEventType.QUEUED,
            source=RunEventSource.WEB,
            actor=administrator,
            current_status=RunStatus.QUEUED,
            task_id=task_id,
            deduplication_key=f"run.queued:{task_id}",
            outcome="queued",
            message="Execução enfileirada.",
        )

    assert was_created is True
    assert repeated_created is False
    assert queued_created is True
    assert repeated.pk == created.pk
    assert [created.sequence, queued.sequence] == [1, 2]
    assert created.request_id == request_id
    assert created.pulse_id == pulse_id
    assert list(run.events.values_list("sequence", flat=True)) == [1, 2]


def test_reusing_a_key_for_different_evidence_is_rejected(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run = _run(modules["SC-04"], administrator)
    record_run_event(
        run=run,
        event_type=RunEventType.CREATED,
        source=RunEventSource.WEB,
        deduplication_key="run.created",
        message="Execução criada.",
    )

    with pytest.raises(TraceabilityConflict):
        record_run_event(
            run=run,
            event_type=RunEventType.CREATED,
            source=RunEventSource.WEB,
            deduplication_key="run.created",
            message="Conteúdo divergente.",
        )


def test_idempotent_retry_preserves_the_original_correlation_context(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run = _run(modules["SC-20"], administrator)
    first_request_id = str(uuid4())
    with trace_context(request_id=first_request_id):
        original, _ = record_run_event(
            run=run,
            event_type=RunEventType.DELIVERY_IGNORED,
            source=RunEventSource.WORKER,
            current_status=RunStatus.SUCCEEDED,
            outcome="terminal",
            deduplication_key="delivery.ignored:terminal",
            message="Entrega ignorada.",
        )
    with trace_context(request_id=str(uuid4())):
        repeated, created = record_run_event(
            run=run,
            event_type=RunEventType.DELIVERY_IGNORED,
            source=RunEventSource.WORKER,
            current_status=RunStatus.SUCCEEDED,
            outcome="terminal",
            deduplication_key="delivery.ignored:terminal",
            message="Entrega ignorada.",
        )

    assert created is False
    assert repeated.pk == original.pk
    assert repeated.request_id == first_request_id


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("details", {"password": "segredo"}),
        ("details", {"contact_email": "pessoa@example.test"}),
        ("details", {"safe": "123.456.789-01"}),
        ("message", "Token Bearer abcdefghijklmnop"),
        ("entity_id", "12345678901"),
    ),
)
def test_recorder_rejects_secrets_and_personal_data(
    modules: dict[str, AutomationModule],
    administrator: User,
    field: str,
    value: object,
) -> None:
    run = _run(modules["SC-04"], administrator)
    kwargs: dict[str, object] = {
        "run": run,
        "event_type": RunEventType.CREATED,
        "source": RunEventSource.WEB,
        "deduplication_key": "unsafe.example",
    }
    kwargs[field] = value

    with pytest.raises(UnsafeTraceabilityData):
        record_run_event(**kwargs)  # type: ignore[arg-type]


def test_event_evidence_cannot_be_rewritten_or_removed(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run = _run(modules["SC-06"], administrator)
    event, _ = record_run_event(
        run=run,
        event_type=RunEventType.CREATED,
        source=RunEventSource.WEB,
        deduplication_key="run.created",
        message="Execução criada.",
    )

    event.message = "Mutação indevida"
    with pytest.raises(ValidationError, match="append-only"):
        event.save()
    with pytest.raises(ValidationError, match="append-only"):
        event.delete()
    with pytest.raises(ValidationError, match="append-only"):
        AutomationRunEvent.objects.filter(pk=event.pk).update(message="Mutação")
    with pytest.raises(ValidationError, match="append-only"):
        AutomationRunEvent.objects.filter(pk=event.pk).delete()
    with pytest.raises(ValidationError, match="append-only"):
        AutomationRunEvent.objects.bulk_update([event], ["message"])
    with pytest.raises(ValidationError, match="gravador"):
        AutomationRunEvent.objects.bulk_create(
            [
                AutomationRunEvent(
                    run=run,
                    sequence=2,
                    event_type=RunEventType.QUEUED,
                    source=RunEventSource.SYSTEM,
                    deduplication_key="bypass",
                )
            ]
        )
    with pytest.raises(ProtectedError):
        run.delete()


def test_event_admin_is_read_only(
    modules: dict[str, AutomationModule],
    administrator: User,
) -> None:
    run = _run(modules["SC-06"], administrator)
    event, _ = record_run_event(
        run=run,
        event_type=RunEventType.CREATED,
        source=RunEventSource.WEB,
        deduplication_key="run.created",
    )
    administrator.is_staff = True
    administrator.is_superuser = True
    administrator.save(update_fields=("is_staff", "is_superuser"))
    request = RequestFactory().get("/admin/automations/automationrunevent/")
    request.user = administrator
    model_admin = AutomationRunEventAdmin(AutomationRunEvent, AdminSite())

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_delete_permission(request, event) is False
    assert set(model_admin.get_readonly_fields(request, event)) == {
        field.name for field in AutomationRunEvent._meta.fields
    }

    run_admin = AutomationRunAdmin(AutomationRun, AdminSite())
    assert run_admin.has_add_permission(request) is False
    assert run_admin.has_delete_permission(request, run) is False
    assert set(run_admin.get_readonly_fields(request, run)) == {
        field.name for field in AutomationRun._meta.fields
    }
