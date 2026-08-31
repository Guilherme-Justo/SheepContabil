from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import asdict
from datetime import date
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
        AutomationRun.objects.filter(pk=run.pk).update(
            status=RunStatus.QUEUED,
            summary="Documento validado e adicionado à fila.",
            metadata={"received": 1, "policy_version": POLICY_VERSION},
        )
    else:
        _recompute_run(run.id)
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
    run.save(update_fields=("status", "summary", "error_message", "metadata", "finished_at"))
    return run, True


def execute_sc04(
    run_id: uuid.UUID | str,
    *,
    inbox: DocumentInbox | None = None,
    storage: ObjectStorage | None = None,
    extractor: TextExtractor | None = None,
    classifier: DocumentClassifier | None = None,
    resume_interrupted: bool = False,
) -> SC04ExecutionResult:
    run, should_execute = _start_run(run_id, resume_interrupted=resume_interrupted)
    if not should_execute:
        return _result_from_metadata(run.metadata)
    try:
        selected_storage = storage or build_object_storage()
        if run.parameters.get("source") == DocumentSource.SIMULATED_INBOX:
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
            try:
                _execute_route(route=pending_route, storage=selected_storage)
            except StorageOperationError:
                continue
        document_ids = list(
            DocumentRunItem.objects.filter(run=run, outcome=DocumentRunOutcome.NEW)
            .values_list("intake__document_id", flat=True)
            .distinct()
        )
        for document_id in document_ids:
            _process_document(
                run=run,
                document_id=document_id,
                storage=selected_storage,
                extractor=extractor or DefaultTextExtractor(),
                classifier=classifier,
            )
        return _recompute_run(run.id)
    except Exception as exc:
        _finish_unhandled_failure(run, exc)
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
                "status": DocumentStatus.QUEUED,
            },
        )
        is_duplicate_hash = not created

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
        _recompute_run(review.run_id)
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
        _recompute_run(route.run_id)
    route.refresh_from_db()
    return route


@transaction.atomic
def _start_run(
    run_id: uuid.UUID | str,
    *,
    resume_interrupted: bool,
) -> tuple[AutomationRun, bool]:
    run = AutomationRun.objects.select_for_update().get(pk=run_id, module_id="SC-04")
    if run.status == RunStatus.RUNNING and resume_interrupted:
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
            finished_at=timezone.now(),
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
        return run, True
    if run.status not in {RunStatus.PENDING, RunStatus.QUEUED}:
        return run, False
    run.status = RunStatus.RUNNING
    run.started_at = run.started_at or timezone.now()
    run.finished_at = None
    run.error_message = ""
    run.save(update_fields=("status", "started_at", "finished_at", "error_message"))
    return run, True


def _ingest_inbox(
    *,
    run: AutomationRun,
    inbox: DocumentInbox,
    storage: ObjectStorage,
) -> int:
    failures = 0
    for attachment in inbox.list_attachments():
        try:
            validated = validate_document(
                filename=attachment.filename,
                declared_content_type=attachment.declared_content_type,
                content=attachment.content,
            )
            ingest_document(
                run=run,
                source=DocumentSource.SIMULATED_INBOX,
                source_reference=attachment.source_reference,
                validated=validated,
                storage=storage,
            )
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
        )
        FiscalDocument.objects.filter(pk=document.pk).update(
            extraction_method=extraction.method,
            extracted_text_sha256=input_sha256,
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
            DocumentClassificationAttempt.objects.filter(
                pk=attempt.pk,
                status=ClassificationAttemptStatus.PROCESSING,
            ).update(
                status=ClassificationAttemptStatus.FAILED,
                error_code="unexpected_classifier_error",
                error_message="O classificador não pôde concluir a solicitação.",
                finished_at=timezone.now(),
            )
            raise
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
            return
        if final_client is None:
            raise RuntimeError("routing policy accepted a missing client")
        with transaction.atomic():
            decision = DocumentDecision.objects.create(
                document=document,
                classification_attempt=attempt,
                document_type=prediction.document_type,
                client=final_client,
                origin=DocumentDecisionOrigin.AUTOMATIC,
                policy_version=POLICY_VERSION,
            )
            route = _prepare_route(decision=decision, run=run)
        _execute_route(route=route, storage=storage)
    except Exception as exc:
        _mark_document_failed(document=document, run=run, exc=exc)


@transaction.atomic
def _claim_document(
    *,
    document_id: uuid.UUID | str,
    run: AutomationRun,
) -> tuple[FiscalDocument, bool]:
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


def _create_attempt(
    *,
    document: FiscalDocument,
    run: AutomationRun,
    input_sha256: str,
    input_char_count: int,
    model: str,
    provider: str,
) -> DocumentClassificationAttempt:
    sequence = (
        document.classification_attempts.aggregate(maximum=Max("sequence"))["maximum"] or 0
    ) + 1
    return DocumentClassificationAttempt.objects.create(
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


def _record_classifier_failure(
    *,
    document: FiscalDocument,
    run: AutomationRun,
    attempt: DocumentClassificationAttempt,
    exc: ClassifierError,
) -> None:
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


def _execute_route(*, route: DocumentRouting, storage: ObjectStorage) -> None:
    route.refresh_from_db()
    if route.status == DocumentRoutingStatus.ROUTED:
        return
    document = route.decision.document
    DocumentRouting.objects.filter(pk=route.pk).update(attempt_count=F("attempt_count") + 1)
    try:
        storage.copy_if_absent(
            source_key=document.storage_key,
            destination_key=route.storage_key,
            content_type=document.media_type,
        )
    except StorageOperationError as exc:
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


def _mark_document_failed(
    *,
    document: FiscalDocument,
    run: AutomationRun,
    exc: Exception,
) -> None:
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


def _recompute_run(run_id: uuid.UUID | str) -> SC04ExecutionResult:
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
    if awaiting_review:
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
    AutomationRun.objects.filter(pk=run.pk).update(
        status=status,
        summary=summary,
        error_message=error_message,
        metadata={
            "policy_version": POLICY_VERSION,
            "threshold": float(settings.SC04_AUTO_ROUTE_THRESHOLD),
            "ingestion_failures": ingestion_failures,
            "result": asdict(result),
        },
        finished_at=finished_at,
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


def _finish_unhandled_failure(run: AutomationRun, exc: Exception) -> None:
    AutomationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.FAILED,
        summary="A triagem não pôde ser concluída.",
        error_message="O pipeline documental encontrou uma falha operacional.",
        metadata={**run.metadata, "technical_error": type(exc).__name__},
        finished_at=timezone.now(),
    )
