from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    BriefingTemplateVersion,
    BriefingVersionStatus,
    RunStatus,
    RunTrigger,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.sc06.rules import format_answers, sanitize_answers
from core.identity.models import User

DEFAULT_TEMPLATE_CODE = "societary-briefing"


class PublishedTemplateUnavailable(RuntimeError):
    pass


def get_latest_published_version(
    template_code: str = DEFAULT_TEMPLATE_CODE,
) -> BriefingTemplateVersion:
    version = (
        BriefingTemplateVersion.objects.select_related("template")
        .filter(
            template_id=template_code,
            template__is_active=True,
            status=BriefingVersionStatus.PUBLISHED,
        )
        .order_by("-version")
        .first()
    )
    if version is None:
        raise PublishedTemplateUnavailable(
            "Nenhuma versão publicada do briefing societário está disponível."
        )
    return version


@transaction.atomic
def create_briefing(
    *,
    client_name: str,
    client_document: str,
    created_by: User,
    template_code: str = DEFAULT_TEMPLATE_CODE,
) -> SocietaryBriefing:
    template_version = get_latest_published_version(template_code)
    started_at = timezone.now()
    run = AutomationRun.objects.create(
        module=AutomationModule.objects.get(code="SC-06"),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.RUNNING,
        triggered_by=created_by,
        parameters={
            "template_code": template_version.template_id,
            "template_version": template_version.version,
        },
        summary="Briefing iniciado; respostas condicionais aguardando conclusão.",
        metadata={"answers_count": 0, "synthetic": True},
        started_at=started_at,
    )
    return SocietaryBriefing.objects.create(
        template_version=template_version,
        run=run,
        client_name=client_name.strip(),
        client_document=client_document.strip(),
        created_by=created_by,
    )


@transaction.atomic
def save_briefing_draft(
    briefing_id: UUID | str,
    answers: dict[str, Any],
) -> SocietaryBriefing:
    briefing = _locked_briefing(briefing_id)
    _require_draft(briefing)
    sanitized = sanitize_answers(briefing.template_version.schema, answers)
    briefing.answers = sanitized
    briefing.save(update_fields=("answers", "updated_at"))

    run = briefing.run
    run.summary = f"Rascunho salvo com {len(sanitized)} resposta(s) aplicável(is)."
    run.metadata = _run_metadata(briefing, answers_count=len(sanitized))
    run.save(update_fields=("summary", "metadata"))
    return briefing


@transaction.atomic
def complete_briefing(
    briefing_id: UUID | str,
    answers: dict[str, Any],
    *,
    completed_by: User,
) -> SocietaryBriefing:
    briefing = _locked_briefing(briefing_id)
    _require_draft(briefing)
    sanitized = sanitize_answers(
        briefing.template_version.schema,
        answers,
        require_complete=True,
    )
    completed_at = timezone.now()
    briefing.answers = sanitized
    briefing.status = SocietaryBriefingStatus.COMPLETED
    briefing.completed_by = completed_by
    briefing.completed_at = completed_at
    briefing.save(
        update_fields=(
            "answers",
            "status",
            "completed_by",
            "completed_at",
            "updated_at",
        )
    )

    visible_answers = format_answers(briefing.template_version.schema, sanitized)
    run = briefing.run
    run.status = RunStatus.SUCCEEDED
    run.summary = (
        f"Briefing concluído com {len(visible_answers)} resposta(s) aplicável(is) e validada(s)."
    )
    run.error_message = ""
    run.metadata = _run_metadata(briefing, answers_count=len(visible_answers))
    run.finished_at = completed_at
    run.save(
        update_fields=(
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
        )
    )
    return briefing


@transaction.atomic
def cancel_briefing(
    briefing_id: UUID | str,
    *,
    cancelled_by: User,
) -> SocietaryBriefing:
    briefing = _locked_briefing(briefing_id)
    _require_draft(briefing)
    now = timezone.now()
    briefing.status = SocietaryBriefingStatus.CANCELLED
    briefing.save(update_fields=("status", "updated_at"))

    run = briefing.run
    run.status = RunStatus.CANCELLED
    run.finished_at = now
    run.summary = f"Briefing cancelado por {cancelled_by.label} antes da conclusão."
    run.metadata = {
        **briefing.run.metadata,
        "cancelled_by_id": cancelled_by.id,
        "cancelled_by_label": cancelled_by.label,
        "cancelled_at": now.isoformat(),
    }
    run.save(update_fields=("status", "finished_at", "summary", "metadata"))
    return briefing


def _locked_briefing(briefing_id: UUID | str) -> SocietaryBriefing:
    return (
        SocietaryBriefing.objects.select_for_update()
        .select_related(
            "template_version",
            "template_version__template",
            "run",
        )
        .get(pk=briefing_id)
    )


def _require_draft(briefing: SocietaryBriefing) -> None:
    if briefing.status != SocietaryBriefingStatus.DRAFT:
        raise ValidationError("Somente um briefing em elaboração pode receber respostas.")


def _run_metadata(briefing: SocietaryBriefing, *, answers_count: int) -> dict[str, Any]:
    metadata = {
        **briefing.run.metadata,
        "answers_count": answers_count,
        "template_code": briefing.template_version.template_id,
        "template_version": briefing.template_version.version,
        "briefing_id": str(briefing.id),
        "synthetic": True,
    }
    if briefing.completed_by_id is None:
        metadata.pop("completed_by_id", None)
    else:
        metadata["completed_by_id"] = briefing.completed_by_id
    return metadata
