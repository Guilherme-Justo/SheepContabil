from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Max
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    ClassificationAttemptStatus,
    DocumentClassificationAttempt,
    DocumentDecision,
    DocumentDecisionOrigin,
    DocumentIntake,
    DocumentIntakeStatus,
    DocumentReview,
    DocumentReviewReason,
    DocumentReviewStatus,
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
)
from core.automations.run_tracking import (
    SupersededDelivery,
    bind_delivery,
    delivery_matches,
    require_current_delivery,
    touch_run,
    with_reconciliation_event,
)
from core.automations.sc04.classification import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_document_classifier,
)
from core.automations.sc04.contracts import (
    ClassificationPrediction,
    ClassificationRequest,
    ClassifierError,
    ClassifierInvalidResponse,
    ClientCandidate,
    DocumentClassifier,
    DocumentInbox,
    IngestionResult,
    ObjectStorage,
    SC04Error,
    SC04ExecutionResult,
    StorageOperationError,
    TextExtractor,
    ValidatedDocument,
)
from core.automations.sc04.extraction import DefaultTextExtractor
from core.automations.sc04.inbox import build_document_inbox
from core.automations.sc04.storage import build_object_storage
from core.automations.sc04.validation import (
    extension_for_media_type,
    original_storage_key,
    routed_storage_key,
    validate_document,
)

if TYPE_CHECKING:
    from core.identity.models import User


POLICY_VERSION = "sc04-routing-policy-v1"


def create_manual_sc04_run(
    *,
    triggered_by: User,
    filename: str,
    declared_content_type: str,
    content: bytes,
    storage: ObjectStorage | None = None,
) -> tuple[AutomationRun, IngestionResult, bool]:
    validated = validate_document(
        filename=filename,
        declared_content_type=declared_content_type,
        content=content,
    )
    token = uuid.uuid4().hex
    run = AutomationRun.objects.create(
        module=AutomationModule.objects.get(code="SC-04"),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.PENDING,
        triggered_by=triggered_by,
        parameters={"source": DocumentSource.MANUAL},
        idempotency_key=f"sc04:manual:{token}",
        summary="Upload recebido; preparando ingestão segura.",
    )
    try:
        ingestion = ingest_document(
            run=run,
            source=DocumentSource.MANUAL,
            source_reference=f"manual:{token}:0",
            validated=validated,
            storage=storage or build_object_storage(),
        )
    except Exception as exc:
        _fail_run_before_processing(run, exc)
        raise
    should_dispatch = ingestion.outcome == DocumentRunOutcome.NEW
    if should_dispatch:
        queued_at = timezone.now()
        AutomationRun.objects.filter(pk=run.pk).update(
            status=RunStatus.QUEUED,
            summary="Documento validado e adicionado à fila.",
            metadata={"received": 1, "policy_version": POLICY_VERSION},
            task_id=uuid.uuid4(),
            queued_at=queued_at,
        )
    else:
        recompute_sc04_run(run.id)
    run.refresh_from_db()
    return run, ingestion, should_dispatch


def create_manual_sc04_inbox_run(*, triggered_by: User) -> AutomationRun:
    token = uuid.uuid4().hex
    return AutomationRun.objects.create(
        module=AutomationModule.objects.get(code="SC-04"),
        trigger=RunTrigger.MANUAL,
        status=RunStatus.QUEUED,
        triggered_by=triggered_by,
        parameters={"source": DocumentSource.SIMULATED_INBOX},
        idempotency_key=f"sc04:manual-inbox:{token}",
        summary="Caixa sintética adicionada à fila de triagem.",
        task_id=uuid.uuid4(),
        queued_at=timezone.now(),
    )


@transaction.atomic
def prepare_scheduled_sc04_run(*, base_date: date) -> tuple[AutomationRun, bool]:
    module = AutomationModule.objects.get(code="SC-04")
    competence = base_date.isoformat()
    run, created = AutomationRun.objects.get_or_create(
        idempotency_key=f"sc04:scheduled:{competence}",
        defaults={
            "module": module,
            "trigger": RunTrigger.SCHEDULED,
            "status": RunStatus.QUEUED,
            "parameters": {
                "source": DocumentSource.SIMULATED_INBOX,
                "base_date": competence,
            },
            "summary": "Triagem diária adicionada à fila.",
            "task_id": uuid.uuid4(),
            "queued_at": timezone.now(),
        },
    )
    if created:
        return run, True
    run = AutomationRun.objects.select_for_update().get(pk=run.pk)
    dispatch_failed_before_start = (
        run.status == RunStatus.FAILED
        and run.started_at is None
        and bool(run.metadata.get("dispatch_error"))
    )
    if not dispatch_failed_before_start:
        return run, False
    run.status = RunStatus.QUEUED
    run.summary = "Triagem diária adicionada novamente à fila."
    run.error_message = ""
    run.metadata = {}
    run.finished_at = None
    run.task_id = uuid.uuid4()
    run.queued_at = timezone.now()
    run.dispatch_started_at = None
    run.broker_published_at = None
    run.heartbeat_at = None
    run.reconciliation_attempts = 0
    run.save(
        update_fields=(
            "status",
            "summary",
            "error_message",
            "metadata",
            "finished_at",
            "task_id",
            "queued_at",
            "dispatch_started_at",
            "broker_published_at",
            "heartbeat_at",
            "reconciliation_attempts",
        )
    )
    return run, True


def execute_sc04(
    run_id: uuid.UUID | str,
    *,
    inbox: DocumentInbox | None = None,
    storage: ObjectStorage | None = None,
    extractor: TextExtractor | None = None,
    classifier: DocumentClassifier | None = None,
    task_id: str | uuid.UUID | None = None,
    resume_interrupted: bool = False,
) -> SC04ExecutionResult:
    run, should_execute = _start_run(
        run_id,
        task_id=task_id,
        resume_interrupted=resume_interrupted,
    )
    if not should_execute:
        return _result_from_metadata(run.metadata)
    try:
        selected_storage = storage or build_object_storage()
        if run.parameters.get("source") == DocumentSource.SIMULATED_INBOX:
            require_current_delivery(run.id, task_id=run.task_id)
            ingestion_failures = _ingest_inbox(
                run=run,
                inbox=inbox or build_document_inbox(),
                storage=selected_storage,
            )
            if ingestion_failures:
                AutomationRun.objects.filter(pk=run.pk).update(
                    metadata={**run.metadata, "ingestion_failures": ingestion_failures}
                )
        for pending_route in DocumentRouting.objects.select_related(
            "decision__document",
            "run",
        ).filter(run=run, status=DocumentRoutingStatus.PENDING):
            require_current_delivery(run.id, task_id=run.task_id)
            touch_run(run.id, task_id=run.task_id)
            try:
                _execute_route(
                    route=pending_route,
                    storage=selected_storage,
                    expected_task_id=run.task_id,
                )
            except StorageOperationError:
                continue
        document_ids = list(
            DocumentRunItem.objects.filter(run=run, outcome=DocumentRunOutcome.NEW)
            .values_list("intake__document_id", flat=True)
            .distinct()
        )
        for document_id in document_ids:
            require_current_delivery(run.id, task_id=run.task_id)
            touch_run(run.id, task_id=run.task_id)
            _process_document(
                run=run,
                document_id=document_id,
                storage=selected_storage,
                extractor=extractor or DefaultTextExtractor(),
                classifier=classifier,
            )
        require_current_delivery(run.id, task_id=run.task_id)
        return recompute_sc04_run(run.id, expected_task_id=run.task_id)
    except SupersededDelivery:
        current = AutomationRun.objects.get(pk=run.pk)
        return _result_from_metadata(current.metadata)
    except Exception as exc:
        _finish_unhandled_failure(run, exc, expected_task_id=run.task_id)
        raise


def ingest_document(
    *,
    run: AutomationRun,
    source: str,
    source_reference: str,
    validated: ValidatedDocument,
    storage: ObjectStorage,
) -> IngestionResult:
    normalized_source_reference = source_reference.strip()
    if not normalized_source_reference:
        raise ValidationError("A origem do documento precisa de um identificador estável.")
    if source not in DocumentSource.values:
        raise ValidationError("A origem do documento não é reconhecida.")
    existing_intake = (
        DocumentIntake.objects.select_related("document")
        .filter(source=source, source_reference=normalized_source_reference)
        .first()
    )
    if existing_intake is not None:
        if existing_intake.document.sha256 != validated.sha256:
            raise ValidationError("A origem já foi associada a outro conteúdo.")
        DocumentRunItem.objects.get_or_create(
            run=run,
            intake=existing_intake,
            defaults={"outcome": DocumentRunOutcome.DUPLICATE_SOURCE},
        )
        return IngestionResult(
            intake_id=str(existing_intake.id),
            document_id=str(existing_intake.document_id),
            outcome=DocumentRunOutcome.DUPLICATE_SOURCE,
        )

    existing_document = FiscalDocument.objects.filter(sha256=validated.sha256).first()
    is_duplicate_hash = existing_document is not None
    if existing_document is None:
        storage_key = original_storage_key(
            sha256=validated.sha256,
            extension=validated.extension,
        )
        storage.put_bytes(
            key=storage_key,
            content=validated.content,
            content_type=validated.media_type,
        )
        existing_document, created = FiscalDocument.objects.get_or_create(
            sha256=validated.sha256,
            defaults={
                "storage_key": storage_key,
                "media_type": validated.media_type,
                "byte_size": len(validated.content),
                "page_count": validated.page_count,
                "status": DocumentStatus.QUEUED,
            },
        )
        is_duplicate_hash = not created
    elif existing_document.page_count is None and validated.page_count is not None:
        FiscalDocument.objects.filter(pk=existing_document.pk).update(
            page_count=validated.page_count
        )
        existing_document.page_count = validated.page_count

    intake_status = (
        DocumentIntakeStatus.DUPLICATE if is_duplicate_hash else DocumentIntakeStatus.QUEUED
    )
    outcome = DocumentRunOutcome.DUPLICATE_HASH if is_duplicate_hash else DocumentRunOutcome.NEW
    try:
        with transaction.atomic():
            intake = DocumentIntake.objects.create(
                document=existing_document,
                run=run,
                source=source,
                source_reference=normalized_source_reference,
                original_filename=validated.filename,
                status=intake_status,
                is_duplicate=is_duplicate_hash,
            )
            DocumentRunItem.objects.create(run=run, intake=intake, outcome=outcome)
    except IntegrityError as exc:
        intake = DocumentIntake.objects.select_related("document").get(
            source=source,
            source_reference=normalized_source_reference,
        )
        if intake.document.sha256 != validated.sha256:
            raise ValidationError("A origem já foi associada a outro conteúdo.") from exc
        DocumentRunItem.objects.get_or_create(
            run=run,
            intake=intake,
            defaults={"outcome": DocumentRunOutcome.DUPLICATE_SOURCE},
        )
        outcome = DocumentRunOutcome.DUPLICATE_SOURCE
        existing_document = intake.document
    return IngestionResult(
        intake_id=str(intake.id),
        document_id=str(existing_document.id),
        outcome=outcome,
    )


def resolve_document_review(
    review_id: uuid.UUID | str,
    *,
    document_type: str,
    client: FiscalClient,
    reviewed_by: User,
    notes: str,
    storage: ObjectStorage | None = None,
) -> DocumentDecision:
    if document_type not in DocumentType.values or document_type == DocumentType.UNKNOWN:
        raise ValidationError({"document_type": "Selecione um tipo documental válido."})
    with transaction.atomic():
        review = (
            DocumentReview.objects.select_for_update()
            .select_related("document", "suggested_attempt", "run")
            .get(pk=review_id)
        )
        if review.status != DocumentReviewStatus.PENDING:
            raise ValidationError("Esta revisão já foi concluída.")
        if not client.is_active:
            raise ValidationError({"client": "Selecione um cliente fiscal ativo."})
        review.status = DocumentReviewStatus.COMPLETED
        review.resolved_document_type = document_type
        review.resolved_client = client
        review.notes = notes.strip()
        review.reviewed_by = reviewed_by
        review.resolved_at = timezone.now()
        review.save(
            update_fields=(
                "status",
                "resolved_document_type",
                "resolved_client",
                "notes",
                "reviewed_by",
                "resolved_at",
            )
        )
        decision = DocumentDecision.objects.create(
            document=review.document,
            classification_attempt=review.suggested_attempt,
            review=review,
            document_type=document_type,
            client=client,
            origin=DocumentDecisionOrigin.HUMAN_REVIEW,
            decided_by=reviewed_by,
            policy_version=review.policy_version,
        )
        FiscalDocument.objects.filter(pk=review.document_id).update(
            classified_type=document_type,
            matched_client=client,
            last_error="",
        )
        route = _prepare_route(decision=decision, run=review.run)
    try:
        _execute_route(route=route, storage=storage or build_object_storage())
    finally:
        recompute_sc04_run(review.run_id)
    return decision


def retry_document_route(
    document_id: uuid.UUID | str,
    *,
    storage: ObjectStorage | None = None,
) -> DocumentRouting:
    document_pk = uuid.UUID(str(document_id))
    route = (
        DocumentRouting.objects.select_related("decision__document", "run")
        .filter(decision__document_id=document_pk)
        .get(status=DocumentRoutingStatus.FAILED)
    )
    try:
        _execute_route(route=route, storage=storage or build_object_storage())
    finally:
        recompute_sc04_run(route.run_id)
    route.refresh_from_db()
    return route


@transaction.atomic
def _start_run(
    run_id: uuid.UUID | str,
    *,
    task_id: str | uuid.UUID | None,
    resume_interrupted: bool,
) -> tuple[AutomationRun, bool]:
    run = AutomationRun.objects.select_for_update().get(pk=run_id, module_id="SC-04")
    if not bind_delivery(run, task_id):
        return run, False
    now = timezone.now()
    run.dispatch_started_at = run.dispatch_started_at or run.queued_at or now
    run.broker_published_at = run.broker_published_at or now
    if run.status == RunStatus.RUNNING and resume_interrupted:
        if run.reconciliation_attempts >= int(settings.AUTOMATION_RECONCILIATION_MAX_ATTEMPTS):
            _close_interrupted_sc04_items(run=run, now=now)
            previous_task_id = run.task_id
            run.task_id = None
            run.status = RunStatus.FAILED
            run.summary = (
                "A triagem foi interrompida repetidamente e não será retomada automaticamente."
            )
            run.error_message = "Revise os documentos com falha antes de iniciar uma nova execução."
            run.metadata = with_reconciliation_event(
                run.metadata,
                action="failed",
                reason="broker_redelivery_exhausted",
                at=now,
                previous_task_id=previous_task_id,
            )
            run.finished_at = now
            run.heartbeat_at = now
            run.save(
                update_fields=(
                    "task_id",
                    "dispatch_started_at",
                    "broker_published_at",
                    "status",
                    "summary",
                    "error_message",
                    "metadata",
                    "finished_at",
                    "heartbeat_at",
                )
            )
            return run, False
        interrupted_document_ids = list(
            FiscalDocument.objects.filter(
                intakes__run_items__run=run,
                intakes__run_items__outcome=DocumentRunOutcome.NEW,
                status=DocumentStatus.PROCESSING,
                decision__isnull=True,
                review__isnull=True,
            )
            .distinct()
            .values_list("id", flat=True)
        )
        DocumentClassificationAttempt.objects.filter(
            run=run,
            status=ClassificationAttemptStatus.PROCESSING,
        ).update(
            status=ClassificationAttemptStatus.FAILED,
            error_code="worker_interrupted",
            error_message="O processamento foi retomado após interrupção do worker.",
            finished_at=now,
        )
        FiscalDocument.objects.filter(id__in=interrupted_document_ids).update(
            status=DocumentStatus.QUEUED,
            last_error="",
        )
        DocumentIntake.objects.filter(
            run=run,
            document_id__in=interrupted_document_ids,
            status=DocumentIntakeStatus.PROCESSING,
        ).update(status=DocumentIntakeStatus.QUEUED)
        run.reconciliation_attempts += 1
        run.heartbeat_at = now
        run.metadata = with_reconciliation_event(
            run.metadata,
            action="resumed",
            reason="broker_redelivery",
            at=now,
            previous_task_id=run.task_id,
            details={"attempt": run.reconciliation_attempts},
        )
        run.save(
            update_fields=(
                "task_id",
                "dispatch_started_at",
                "broker_published_at",
                "reconciliation_attempts",
                "heartbeat_at",
                "metadata",
            )
        )
        return run, True
    if run.status not in {RunStatus.PENDING, RunStatus.QUEUED}:
        return run, False
    run.status = RunStatus.RUNNING
    run.started_at = run.started_at or now
    run.finished_at = None
    run.error_message = ""
    run.heartbeat_at = now
    run.save(
        update_fields=(
            "task_id",
            "dispatch_started_at",
            "broker_published_at",
            "status",
            "started_at",
            "finished_at",
            "error_message",
            "heartbeat_at",
        )
    )
    return run, True


def _close_interrupted_sc04_items(*, run: AutomationRun, now: datetime) -> None:
    interrupted_document_ids = list(
        FiscalDocument.objects.filter(
            intakes__run_items__run=run,
            intakes__run_items__outcome=DocumentRunOutcome.NEW,
            status__in=(DocumentStatus.QUEUED, DocumentStatus.PROCESSING),
        )
        .distinct()
        .values_list("id", flat=True)
    )
    DocumentClassificationAttempt.objects.filter(
        run=run,
        status=ClassificationAttemptStatus.PROCESSING,
    ).update(
        status=ClassificationAttemptStatus.FAILED,
        error_code="worker_interrupted",
        error_message="O worker foi interrompido repetidamente; a tentativa foi encerrada.",
        finished_at=now,
    )
    DocumentRouting.objects.filter(
        run=run,
        status=DocumentRoutingStatus.PENDING,
    ).update(
        status=DocumentRoutingStatus.FAILED,
        last_error="O processamento foi interrompido repetidamente.",
        updated_at=now,
    )
    FiscalDocument.objects.filter(id__in=interrupted_document_ids).update(
        status=DocumentStatus.FAILED,
        last_error="O processamento foi interrompido repetidamente.",
    )
    DocumentIntake.objects.filter(
        run=run,
        document_id__in=interrupted_document_ids,
        status__in=(DocumentIntakeStatus.QUEUED, DocumentIntakeStatus.PROCESSING),
    ).update(status=DocumentIntakeStatus.FAILED)


def _ingest_inbox(
    *,
    run: AutomationRun,
    inbox: DocumentInbox,
    storage: ObjectStorage,
) -> int:
    failures = 0
    for attachment in inbox.list_attachments():
        require_current_delivery(run.id, task_id=run.task_id)
        try:
            validated = validate_document(
                filename=attachment.filename,
                declared_content_type=attachment.declared_content_type,
                content=attachment.content,
            )
            require_current_delivery(run.id, task_id=run.task_id)
            ingest_document(
                run=run,
                source=DocumentSource.SIMULATED_INBOX,
                source_reference=attachment.source_reference,
                validated=validated,
                storage=storage,
            )
        except SupersededDelivery:
            raise
        except Exception:
            failures += 1
            continue
    return failures


def _process_document(
    *,
    run: AutomationRun,
    document_id: uuid.UUID | str,
    storage: ObjectStorage,
    extractor: TextExtractor,
    classifier: DocumentClassifier | None,
) -> None:
    document, should_process = _claim_document(document_id=document_id, run=run)
    if not should_process:
        return
    try:
        content = storage.get_bytes(document.storage_key)
        if hashlib.sha256(content).hexdigest() != document.sha256:
            raise StorageOperationError(
                "O original armazenado falhou na verificação de integridade."
            )
        extraction = extractor.extract(content=content, media_type=document.media_type)
        input_sha256 = hashlib.sha256(extraction.text.encode()).hexdigest()
        clients = tuple(FiscalClient.objects.filter(is_active=True).order_by("code"))
        exact_client, ambiguous_alias = _exact_client_match(extraction.text, clients)
        selected_classifier = classifier
        attempt = _create_attempt(
            document=document,
            run=run,
            input_sha256=input_sha256,
            input_char_count=len(extraction.text),
            model=(
                selected_classifier.model if selected_classifier else str(settings.OPENAI_MODEL)
            ),
            provider=(selected_classifier.provider if selected_classifier else "openai"),
            extraction_method=extraction.method,
            extracted_excerpt=extraction.text[:2000],
            page_count=extraction.page_count,
        )
        try:
            selected_classifier = selected_classifier or build_document_classifier()
            prediction = selected_classifier.classify(
                ClassificationRequest(
                    extracted_text=extraction.text,
                    content_sha256=document.sha256,
                    clients=tuple(
                        ClientCandidate(
                            code=client.code,
                            name=client.name,
                            aliases=tuple(_validated_aliases(client.aliases)),
                        )
                        for client in clients
                    ),
                    exact_client_code=exact_client.code if exact_client else None,
                )
            )
        except ClassifierError as exc:
            _record_classifier_failure(
                document=document,
                run=run,
                attempt=attempt,
                exc=exc,
            )
            return
        except Exception:
            _record_unexpected_classifier_failure(attempt=attempt, run=run)
            raise
        with transaction.atomic():
            _lock_current_delivery(run)
            predicted_client = (
                FiscalClient.objects.filter(code=prediction.client_code, is_active=True).first()
                if prediction.client_code
                else None
            )
            final_client = exact_client or predicted_client
            final_client_confidence = 1.0 if exact_client else prediction.client_confidence
            evidence = list(prediction.evidence)
            if exact_client:
                evidence.append("Cliente confirmado por identificador ou alias sintético exato.")
            _complete_attempt(
                attempt=attempt,
                prediction=prediction,
                predicted_client=predicted_client,
            )
            FiscalDocument.objects.filter(pk=document.pk).update(
                classified_type=prediction.document_type,
                type_confidence=_decimal_confidence(prediction.type_confidence),
                matched_client=final_client,
                client_confidence=_decimal_confidence(final_client_confidence),
                evidence=evidence[:4],
                last_error="",
            )
            reason = _review_reason(
                document_type=prediction.document_type,
                type_confidence=prediction.type_confidence,
                client=final_client,
                client_confidence=final_client_confidence,
                is_ambiguous=ambiguous_alias or prediction.is_ambiguous,
            )
            if reason is not None:
                _open_review(document=document, run=run, attempt=attempt, reason=reason)
                route = None
            else:
                if final_client is None:
                    raise RuntimeError("routing policy accepted a missing client")
                decision = DocumentDecision.objects.create(
                    document=document,
                    classification_attempt=attempt,
                    document_type=prediction.document_type,
                    client=final_client,
                    origin=DocumentDecisionOrigin.AUTOMATIC,
                    policy_version=POLICY_VERSION,
                )
                route = _prepare_route(decision=decision, run=run)
        if route is not None:
            _execute_route(
                route=route,
                storage=storage,
                expected_task_id=run.task_id,
            )
    except SupersededDelivery:
        return
    except Exception as exc:
        try:
            _mark_document_failed(
                document=document,
                run=run,
                exc=exc,
                expected_task_id=run.task_id,
            )
        except SupersededDelivery:
            return


@transaction.atomic
def _claim_document(
    *,
    document_id: uuid.UUID | str,
    run: AutomationRun,
) -> tuple[FiscalDocument, bool]:
    _lock_current_delivery(run)
    document = FiscalDocument.objects.select_for_update().get(pk=document_id)
    if (
        document.status != DocumentStatus.QUEUED
        or DocumentDecision.objects.filter(document=document).exists()
        or DocumentReview.objects.filter(document=document).exists()
    ):
        return document, False
    document.status = DocumentStatus.PROCESSING
    document.last_error = ""
    document.save(update_fields=("status", "last_error", "updated_at"))
    DocumentIntake.objects.filter(document=document, run=run, is_duplicate=False).update(
        status=DocumentIntakeStatus.PROCESSING
    )
    return document, True


def _lock_current_delivery(run: AutomationRun) -> AutomationRun:
    locked = AutomationRun.objects.select_for_update().get(pk=run.pk)
    if locked.status != RunStatus.RUNNING or not delivery_matches(locked, run.task_id):
        raise SupersededDelivery
    return locked


@transaction.atomic
def _create_attempt(
    *,
    document: FiscalDocument,
    run: AutomationRun,
    input_sha256: str,
    input_char_count: int,
    model: str,
    provider: str,
    extraction_method: str,
    extracted_excerpt: str,
    page_count: int | None,
) -> DocumentClassificationAttempt:
    _lock_current_delivery(run)
    sequence = (
        document.classification_attempts.aggregate(maximum=Max("sequence"))["maximum"] or 0
    ) + 1
    attempt = DocumentClassificationAttempt.objects.create(
        document=document,
        run=run,
        sequence=sequence,
        status=ClassificationAttemptStatus.PROCESSING,
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        input_sha256=input_sha256,
        input_char_count=input_char_count,
    )
    FiscalDocument.objects.filter(pk=document.pk).update(
        extraction_method=extraction_method,
        extracted_text_sha256=input_sha256,
        extracted_excerpt=extracted_excerpt,
        page_count=page_count,
    )
    return attempt


def _complete_attempt(
    *,
    attempt: DocumentClassificationAttempt,
    prediction: ClassificationPrediction,
    predicted_client: FiscalClient | None,
) -> None:
    DocumentClassificationAttempt.objects.filter(pk=attempt.pk).update(
        status=ClassificationAttemptStatus.SUCCEEDED,
        model=prediction.model,
        provider_response_id=prediction.provider_response_id,
        predicted_document_type=prediction.document_type,
        predicted_client=predicted_client,
        type_confidence=_decimal_confidence(prediction.type_confidence),
        client_confidence=_decimal_confidence(prediction.client_confidence),
        is_ambiguous=prediction.is_ambiguous,
        evidence=list(prediction.evidence),
        prediction=prediction.candidate_snapshot,
        finished_at=timezone.now(),
    )


@transaction.atomic
def _record_classifier_failure(
    *,
    document: FiscalDocument,
    run: AutomationRun,
    attempt: DocumentClassificationAttempt,
    exc: ClassifierError,
) -> None:
    _lock_current_delivery(run)
    status = (
        ClassificationAttemptStatus.INVALID_RESPONSE
        if isinstance(exc, ClassifierInvalidResponse)
        else ClassificationAttemptStatus.FAILED
    )
    DocumentClassificationAttempt.objects.filter(pk=attempt.pk).update(
        status=status,
        error_code=exc.code,
        error_message=str(exc),
        finished_at=timezone.now(),
    )
    reason = (
        DocumentReviewReason.INVALID_RESPONSE
        if isinstance(exc, ClassifierInvalidResponse)
        else DocumentReviewReason.CLASSIFIER_UNAVAILABLE
    )
    FiscalDocument.objects.filter(pk=document.pk).update(last_error=str(exc))
    _open_review(document=document, run=run, attempt=attempt, reason=reason)


@transaction.atomic
def _record_unexpected_classifier_failure(
    *,
    attempt: DocumentClassificationAttempt,
    run: AutomationRun,
) -> None:
    _lock_current_delivery(run)
    DocumentClassificationAttempt.objects.filter(
        pk=attempt.pk,
        status=ClassificationAttemptStatus.PROCESSING,
    ).update(
        status=ClassificationAttemptStatus.FAILED,
        error_code="unexpected_classifier_error",
        error_message="O classificador não pôde concluir a solicitação.",
        finished_at=timezone.now(),
    )


def _open_review(
    *,
    document: FiscalDocument,
    run: AutomationRun,
    attempt: DocumentClassificationAttempt,
    reason: str,
) -> None:
    DocumentReview.objects.get_or_create(
        document=document,
        defaults={
            "run": run,
            "suggested_attempt": attempt,
            "reason": reason,
            "policy_version": POLICY_VERSION,
        },
    )
    FiscalDocument.objects.filter(pk=document.pk).update(status=DocumentStatus.AWAITING_REVIEW)
    DocumentIntake.objects.filter(document=document, run=run, is_duplicate=False).update(
        status=DocumentIntakeStatus.AWAITING_REVIEW
    )


def _prepare_route(*, decision: DocumentDecision, run: AutomationRun) -> DocumentRouting:
    extension = extension_for_media_type(decision.document.media_type)
    key = routed_storage_key(
        client_prefix=decision.client.route_prefix,
        document_type=decision.document_type,
        document_id=str(decision.document_id),
        extension=extension,
    )
    return DocumentRouting.objects.create(
        decision=decision,
        run=run,
        storage_key=key,
        status=DocumentRoutingStatus.PENDING,
    )


def _execute_route(
    *,
    route: DocumentRouting,
    storage: ObjectStorage,
    expected_task_id: uuid.UUID | None = None,
) -> None:
    route.refresh_from_db()
    if route.status == DocumentRoutingStatus.ROUTED:
        return
    document = route.decision.document
    with transaction.atomic():
        if expected_task_id is not None:
            route.run.task_id = expected_task_id
            _lock_current_delivery(route.run)
        DocumentRouting.objects.filter(pk=route.pk).update(attempt_count=F("attempt_count") + 1)
    try:
        storage.copy_if_absent(
            source_key=document.storage_key,
            destination_key=route.storage_key,
            content_type=document.media_type,
        )
    except StorageOperationError as exc:
        with transaction.atomic():
            if expected_task_id is not None:
                route.run.task_id = expected_task_id
                _lock_current_delivery(route.run)
            DocumentRouting.objects.filter(pk=route.pk).update(
                status=DocumentRoutingStatus.FAILED,
                last_error=str(exc),
                updated_at=timezone.now(),
            )
            FiscalDocument.objects.filter(pk=document.pk).update(
                status=DocumentStatus.FAILED,
                last_error=str(exc),
            )
            DocumentIntake.objects.filter(
                document=document,
                run=route.run,
                is_duplicate=False,
            ).update(status=DocumentIntakeStatus.FAILED)
        raise
    finished_at = timezone.now()
    with transaction.atomic():
        if expected_task_id is not None:
            route.run.task_id = expected_task_id
            _lock_current_delivery(route.run)
        DocumentRouting.objects.filter(pk=route.pk).update(
            status=DocumentRoutingStatus.ROUTED,
            last_error="",
            routed_at=finished_at,
            updated_at=finished_at,
        )
        FiscalDocument.objects.filter(pk=document.pk).update(
            status=DocumentStatus.ROUTED,
            last_error="",
        )
        DocumentIntake.objects.filter(
            document=document,
            run=route.run,
            is_duplicate=False,
        ).update(status=DocumentIntakeStatus.ROUTED)


def _review_reason(
    *,
    document_type: str,
    type_confidence: float,
    client: FiscalClient | None,
    client_confidence: float,
    is_ambiguous: bool,
) -> str | None:
    if is_ambiguous:
        return DocumentReviewReason.AMBIGUOUS_CLIENT
    if document_type in {DocumentType.UNKNOWN, DocumentType.OTHER}:
        return DocumentReviewReason.UNKNOWN_TYPE
    if client is None:
        return DocumentReviewReason.UNKNOWN_CLIENT
    threshold = float(settings.SC04_AUTO_ROUTE_THRESHOLD)
    if type_confidence < threshold or client_confidence < threshold:
        return DocumentReviewReason.LOW_CONFIDENCE
    return None


def _exact_client_match(
    text: str,
    clients: tuple[FiscalClient, ...],
) -> tuple[FiscalClient | None, bool]:
    document_numbers = {
        digits
        for candidate in re.findall(r"(?<!\d)(?:\d[\s./-]*){10,13}\d(?!\d)", text)
        if len(digits := re.sub(r"\D", "", candidate)) in {11, 14}
    }
    by_document = [client for client in clients if client.document_number in document_numbers]
    if len(by_document) == 1:
        return by_document[0], False
    if len(by_document) > 1:
        return None, True

    normalized_text = f" {_normalize_match_text(text)} "
    matched: list[FiscalClient] = []
    for client in clients:
        candidates = [client.name, *_validated_aliases(client.aliases)]
        if any(
            len(normalized := _normalize_match_text(candidate)) >= 4
            and f" {normalized} " in normalized_text
            for candidate in candidates
        ):
            matched.append(client)
    if len(matched) == 1:
        return matched[0], False
    return None, len(matched) > 1


def _validated_aliases(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalize_match_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_marks).strip()


def _decimal_confidence(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


@transaction.atomic
def _mark_document_failed(
    *,
    document: FiscalDocument,
    run: AutomationRun,
    exc: Exception,
    expected_task_id: uuid.UUID | None = None,
) -> None:
    if expected_task_id is not None:
        run.task_id = expected_task_id
        _lock_current_delivery(run)
    safe_message = (
        str(exc)
        if isinstance(exc, (StorageOperationError, ValidationError))
        else "O documento não pôde ser concluído nesta execução."
    )
    FiscalDocument.objects.filter(pk=document.pk).update(
        status=DocumentStatus.FAILED,
        last_error=safe_message,
    )
    DocumentIntake.objects.filter(document=document, run=run, is_duplicate=False).update(
        status=DocumentIntakeStatus.FAILED
    )


def recompute_sc04_run(
    run_id: uuid.UUID | str,
    *,
    expected_task_id: uuid.UUID | None = None,
    preserve_terminal_status: bool = False,
) -> SC04ExecutionResult:
    run = AutomationRun.objects.get(pk=run_id, module_id="SC-04")
    ingestion_failures = _metadata_int(run.metadata.get("ingestion_failures"))
    items = DocumentRunItem.objects.filter(run=run)
    received = items.count() + ingestion_failures
    duplicates = items.exclude(outcome=DocumentRunOutcome.NEW).count()
    new_count = items.filter(outcome=DocumentRunOutcome.NEW).count()
    new_documents = FiscalDocument.objects.filter(
        intakes__run_items__run=run,
        intakes__run_items__outcome=DocumentRunOutcome.NEW,
    ).distinct()
    routed = new_documents.filter(status=DocumentStatus.ROUTED).count()
    awaiting_review = new_documents.filter(status=DocumentStatus.AWAITING_REVIEW).count()
    in_progress = new_documents.filter(
        status__in=(DocumentStatus.QUEUED, DocumentStatus.PROCESSING)
    ).count()
    duplicate_failed = (
        FiscalDocument.objects.filter(
            intakes__run_items__run=run,
            intakes__run_items__outcome=DocumentRunOutcome.DUPLICATE_HASH,
            status=DocumentStatus.FAILED,
        )
        .distinct()
        .count()
    )
    failed = (
        new_documents.filter(status=DocumentStatus.FAILED).count()
        + ingestion_failures
        + duplicate_failed
    )
    result = SC04ExecutionResult(
        received=received,
        routed=routed,
        awaiting_review=awaiting_review,
        duplicates=duplicates,
        failed=failed,
    )
    terminal_statuses = {
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED_WITH_WARNINGS,
        RunStatus.PARTIALLY_FAILED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
    if preserve_terminal_status and run.status in terminal_statuses:
        status = run.status
        finished_at = run.finished_at or timezone.now()
    elif awaiting_review:
        status = RunStatus.AWAITING_REVIEW
        finished_at = None
    elif in_progress:
        status = RunStatus.RUNNING
        finished_at = None
    elif failed and not routed and failed >= new_count + ingestion_failures:
        status = RunStatus.FAILED
        finished_at = timezone.now()
    elif failed:
        status = RunStatus.SUCCEEDED_WITH_WARNINGS
        finished_at = timezone.now()
    else:
        status = RunStatus.SUCCEEDED
        finished_at = timezone.now()
    summary = (
        f"{received} item(ns) recebido(s); {routed} encaminhado(s); "
        f"{awaiting_review} em revisão; {duplicates} duplicado(s); {failed} falha(s)."
    )
    error_message = ""
    if failed:
        error_message = "Há documentos que exigem correção operacional ou nova tentativa."
    run_query = AutomationRun.objects.filter(pk=run.pk)
    if expected_task_id is not None:
        run_query = run_query.filter(
            status=RunStatus.RUNNING,
            task_id=expected_task_id,
        )
    run_query.update(
        status=status,
        summary=summary,
        error_message=error_message,
        metadata={
            **run.metadata,
            "policy_version": POLICY_VERSION,
            "threshold": float(settings.SC04_AUTO_ROUTE_THRESHOLD),
            "ingestion_failures": ingestion_failures,
            "result": asdict(result),
        },
        finished_at=finished_at,
        heartbeat_at=timezone.now(),
    )
    return result


def _result_from_metadata(metadata: dict[str, object]) -> SC04ExecutionResult:
    raw = metadata.get("result")
    if not isinstance(raw, dict):
        return SC04ExecutionResult(0, 0, 0, 0, 0)
    return SC04ExecutionResult(
        received=_metadata_int(raw.get("received")),
        routed=_metadata_int(raw.get("routed")),
        awaiting_review=_metadata_int(raw.get("awaiting_review")),
        duplicates=_metadata_int(raw.get("duplicates")),
        failed=_metadata_int(raw.get("failed")),
    )


def _metadata_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _fail_run_before_processing(run: AutomationRun, exc: Exception) -> None:
    safe_message = (
        str(exc)
        if isinstance(exc, (SC04Error, ValidationError))
        else "O upload não pôde ser preparado por uma falha operacional."
    )
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.FAILED,
        summary="O upload não pôde ser preparado para processamento.",
        error_message=safe_message,
        metadata={"technical_error": type(exc).__name__},
        finished_at=timezone.now(),
    )


def _finish_unhandled_failure(
    run: AutomationRun,
    exc: Exception,
    *,
    expected_task_id: uuid.UUID | None,
) -> None:
    run_query = AutomationRun.objects.filter(pk=run.pk, status=RunStatus.RUNNING)
    if expected_task_id is not None:
        run_query = run_query.filter(task_id=expected_task_id)
    else:
        run_query = run_query.filter(task_id__isnull=True)
    run_query.update(
        status=RunStatus.FAILED,
        summary="A triagem não pôde ser concluída.",
        error_message="O pipeline documental encontrou uma falha operacional.",
        metadata={**run.metadata, "technical_error": type(exc).__name__},
        finished_at=timezone.now(),
        heartbeat_at=timezone.now(),
    )
