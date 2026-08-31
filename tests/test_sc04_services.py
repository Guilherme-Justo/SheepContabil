from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest
from django.core.management import call_command
from django.utils import timezone
from freezegun import freeze_time

from core.automations.management.commands import dispatch_due_schedules
from core.automations.models import (
    AutomationFrequency,
    AutomationModule,
    AutomationRun,
    ClassificationAttemptStatus,
    DocumentClassificationAttempt,
    DocumentDecision,
    DocumentDecisionOrigin,
    DocumentIntake,
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
    ExtractionMethod,
    FiscalClient,
    FiscalDocument,
    RunStatus,
    RunTrigger,
)
from core.automations.sc04.contracts import (
    ClassificationPrediction,
    ClassificationRequest,
    ClassifierUnavailable,
    ExtractionResult,
    IncomingDocument,
    StorageOperationError,
    StoredObject,
)
from core.automations.sc04.services import (
    create_manual_sc04_inbox_run,
    create_manual_sc04_run,
    execute_sc04,
    ingest_document,
    prepare_scheduled_sc04_run,
    resolve_document_review,
    retry_document_route,
)
from core.automations.sc04.validation import validate_document
from core.identity.models import User

pytestmark = pytest.mark.django_db


@dataclass
class MemoryStorage:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    put_calls: list[str] = field(default_factory=list)
    copy_calls: list[tuple[str, str]] = field(default_factory=list)
    remaining_copy_failures: int = 0

    def put_bytes(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        self.put_calls.append(key)
        self.objects.setdefault(key, (content, content_type))
        stored_content, stored_type = self.objects[key]
        return StoredObject(
            key=key,
            byte_size=len(stored_content),
            content_type=stored_type,
        )

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    def copy_if_absent(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
    ) -> StoredObject:
        self.copy_calls.append((source_key, destination_key))
        if self.remaining_copy_failures:
            self.remaining_copy_failures -= 1
            raise StorageOperationError("O roteamento sintético ficou indisponível.")
        content, _ = self.objects[source_key]
        self.objects.setdefault(destination_key, (content, content_type))
        return StoredObject(
            key=destination_key,
            byte_size=len(content),
            content_type=content_type,
        )


@dataclass
class FixedExtractor:
    text: str

    def extract(self, *, content: bytes, media_type: str) -> ExtractionResult:
        del content, media_type
        return ExtractionResult(
            text=self.text,
            method=ExtractionMethod.PLAIN_TEXT,
            page_count=1,
        )


@dataclass
class RecordingClassifier:
    prediction: ClassificationPrediction | None = None
    failure: Exception | None = None
    provider: str = "fake"
    model: str = "fake-classifier-v1"
    requests: list[ClassificationRequest] = field(default_factory=list)

    def classify(self, request: ClassificationRequest) -> ClassificationPrediction:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        assert self.prediction is not None
        return self.prediction


@dataclass(frozen=True)
class FixedInbox:
    attachments: tuple[IncomingDocument, ...]

    def list_attachments(self) -> tuple[IncomingDocument, ...]:
        return self.attachments


@pytest.fixture
def fiscal_client(db: object) -> FiscalClient:
    del db
    return FiscalClient.objects.create(
        code="aurora-demo",
        name="Aurora Participações Demo",
        document_number="12345678000190",
        aliases=["Aurora Demo"],
        route_prefix="aurora-demo",
    )


def _run(
    module: AutomationModule,
    *,
    suffix: str,
    trigger: str = RunTrigger.MANUAL,
) -> AutomationRun:
    return AutomationRun.objects.create(
        module=module,
        trigger=trigger,
        status=RunStatus.PENDING,
        idempotency_key=f"test-sc04:{suffix}",
    )


def _prediction(
    *,
    client: FiscalClient,
    document_type: str = DocumentType.INVOICE,
    type_confidence: float = 0.96,
    client_confidence: float = 0.94,
    is_ambiguous: bool = False,
) -> ClassificationPrediction:
    return ClassificationPrediction(
        document_type=document_type,
        type_confidence=type_confidence,
        client_code=client.code,
        client_confidence=client_confidence,
        evidence=("Evidência sintética compatível.",),
        provider_response_id="fake-response-1",
        model="fake-classifier-v1",
        candidate_snapshot={
            "document_type": document_type,
            "client_code": client.code,
        },
        is_ambiguous=is_ambiguous,
    )


def _manual_upload(
    *,
    modules: dict[str, AutomationModule],
    administrator: User,
    storage: MemoryStorage,
    content: bytes,
) -> AutomationRun:
    run, ingestion, should_dispatch = create_manual_sc04_run(
        triggered_by=administrator,
        filename="documento-sintetico.txt",
        declared_content_type="text/plain",
        content=content,
        storage=storage,
    )
    assert ingestion.outcome == DocumentRunOutcome.NEW
    assert should_dispatch is True
    return run


def test_ingestion_is_idempotent_by_source_and_content_hash(
    modules: dict[str, AutomationModule],
) -> None:
    storage = MemoryStorage()
    content = b"DOCUMENTO FISCAL SINTETICO 001"
    validated = validate_document(
        filename="entrada.txt",
        declared_content_type="text/plain",
        content=content,
    )
    first_run = _run(modules["SC-04"], suffix="source-first")
    repeated_source_run = _run(modules["SC-04"], suffix="source-repeat")
    repeated_hash_run = _run(modules["SC-04"], suffix="hash-repeat")

    first = ingest_document(
        run=first_run,
        source=DocumentSource.SIMULATED_INBOX,
        source_reference="inbox:test:message-1:attachment-1",
        validated=validated,
        storage=storage,
    )
    repeated_source = ingest_document(
        run=repeated_source_run,
        source=DocumentSource.SIMULATED_INBOX,
        source_reference="inbox:test:message-1:attachment-1",
        validated=validated,
        storage=storage,
    )
    repeated_hash = ingest_document(
        run=repeated_hash_run,
        source=DocumentSource.SIMULATED_INBOX,
        source_reference="inbox:test:message-2:attachment-1",
        validated=validated,
        storage=storage,
    )

    assert first.outcome == DocumentRunOutcome.NEW
    assert repeated_source.outcome == DocumentRunOutcome.DUPLICATE_SOURCE
    assert repeated_hash.outcome == DocumentRunOutcome.DUPLICATE_HASH
    assert repeated_source.intake_id == first.intake_id
    assert repeated_hash.document_id == first.document_id
    assert FiscalDocument.objects.count() == 1
    assert DocumentIntake.objects.count() == 2
    assert DocumentRunItem.objects.count() == 3
    assert DocumentRunItem.objects.get(run=repeated_source_run).outcome == (
        DocumentRunOutcome.DUPLICATE_SOURCE
    )
    assert DocumentRunItem.objects.get(run=repeated_hash_run).outcome == (
        DocumentRunOutcome.DUPLICATE_HASH
    )
    assert len(storage.put_calls) == 1


def test_high_confidence_prediction_routes_document_automatically(
    modules: dict[str, AutomationModule],
    administrator: User,
    fiscal_client: FiscalClient,
) -> None:
    storage = MemoryStorage()
    run = _manual_upload(
        modules=modules,
        administrator=administrator,
        storage=storage,
        content=b"NOTA FISCAL SINTETICA DE ALTA CONFIANCA",
    )
    classifier = RecordingClassifier(prediction=_prediction(client=fiscal_client))

    result = execute_sc04(
        run.id,
        storage=storage,
        extractor=FixedExtractor(
            "NOTA FISCAL. CNPJ 12.345.678/0001-90. Aurora Participações Demo."
        ),
        classifier=classifier,
    )

    document = FiscalDocument.objects.get()
    decision = DocumentDecision.objects.select_related("client").get(document=document)
    routing = DocumentRouting.objects.get(decision=decision)
    run.refresh_from_db()
    assert result.routed == 1
    assert result.awaiting_review == 0
    assert result.failed == 0
    assert run.status == RunStatus.SUCCEEDED
    assert document.status == DocumentStatus.ROUTED
    assert decision.origin == DocumentDecisionOrigin.AUTOMATIC
    assert decision.client == fiscal_client
    assert routing.status == DocumentRoutingStatus.ROUTED
    assert routing.attempt_count == 1
    assert classifier.requests[0].exact_client_code == fiscal_client.code
    assert routing.storage_key in storage.objects


def test_low_confidence_opens_review_and_human_resolution_completes_run(
    modules: dict[str, AutomationModule],
    administrator: User,
    fiscal_client: FiscalClient,
) -> None:
    storage = MemoryStorage()
    run = _manual_upload(
        modules=modules,
        administrator=administrator,
        storage=storage,
        content=b"RELATORIO CONTABIL SINTETICO DE BAIXA CONFIANCA",
    )
    classifier = RecordingClassifier(
        prediction=_prediction(
            client=fiscal_client,
            document_type=DocumentType.BANK_STATEMENT,
            type_confidence=0.91,
            client_confidence=0.52,
        )
    )

    result = execute_sc04(
        run.id,
        storage=storage,
        extractor=FixedExtractor("Extrato sem identificador fiscal ou nome confiável."),
        classifier=classifier,
    )

    review = DocumentReview.objects.select_related("document").get()
    run.refresh_from_db()
    assert result.awaiting_review == 1
    assert result.routed == 0
    assert run.status == RunStatus.AWAITING_REVIEW
    assert review.status == DocumentReviewStatus.PENDING
    assert review.reason == DocumentReviewReason.LOW_CONFIDENCE
    assert not DocumentDecision.objects.exists()

    decision = resolve_document_review(
        review.id,
        document_type=DocumentType.BANK_STATEMENT,
        client=fiscal_client,
        reviewed_by=administrator,
        notes="Cliente e tipo confirmados na revisão sintética.",
        storage=storage,
    )

    review.refresh_from_db()
    review.document.refresh_from_db()
    run.refresh_from_db()
    assert review.status == DocumentReviewStatus.COMPLETED
    assert review.reviewed_by == administrator
    assert review.resolved_at is not None
    assert decision.origin == DocumentDecisionOrigin.HUMAN_REVIEW
    assert decision.review == review
    assert decision.decided_by == administrator
    assert review.document.status == DocumentStatus.ROUTED
    assert run.status == RunStatus.SUCCEEDED


def test_failed_route_preserves_original_and_can_be_retried(
    modules: dict[str, AutomationModule],
    administrator: User,
    fiscal_client: FiscalClient,
) -> None:
    storage = MemoryStorage(remaining_copy_failures=1)
    run = _manual_upload(
        modules=modules,
        administrator=administrator,
        storage=storage,
        content=b"NOTA FISCAL SINTETICA COM FALHA DE ROTEAMENTO",
    )
    classifier = RecordingClassifier(prediction=_prediction(client=fiscal_client))

    result = execute_sc04(
        run.id,
        storage=storage,
        extractor=FixedExtractor(
            "NOTA FISCAL. CNPJ 12.345.678/0001-90. Aurora Participações Demo."
        ),
        classifier=classifier,
    )

    document = FiscalDocument.objects.get()
    routing = DocumentRouting.objects.get()
    run.refresh_from_db()
    assert result.failed == 1
    assert run.status == RunStatus.FAILED
    assert document.status == DocumentStatus.FAILED
    assert routing.status == DocumentRoutingStatus.FAILED
    assert routing.attempt_count == 1
    assert document.storage_key in storage.objects
    assert routing.storage_key not in storage.objects

    retried = retry_document_route(document.id, storage=storage)

    document.refresh_from_db()
    run.refresh_from_db()
    assert retried.status == DocumentRoutingStatus.ROUTED
    assert retried.attempt_count == 2
    assert document.status == DocumentStatus.ROUTED
    assert run.status == RunStatus.SUCCEEDED
    assert document.storage_key in storage.objects
    assert retried.storage_key in storage.objects


def test_classifier_failure_is_audited_and_sent_to_review(
    modules: dict[str, AutomationModule],
    administrator: User,
    fiscal_client: FiscalClient,
) -> None:
    storage = MemoryStorage()
    run = _manual_upload(
        modules=modules,
        administrator=administrator,
        storage=storage,
        content=b"DOCUMENTO SINTETICO PARA FALHA DO CLASSIFICADOR",
    )
    classifier = RecordingClassifier(
        failure=ClassifierUnavailable("O classificador sintético está indisponível.")
    )

    result = execute_sc04(
        run.id,
        storage=storage,
        extractor=FixedExtractor("Documento sintético legível, mas sem classificação."),
        classifier=classifier,
    )

    attempt = DocumentClassificationAttempt.objects.get()
    review = DocumentReview.objects.get()
    run.refresh_from_db()
    assert result.awaiting_review == 1
    assert result.failed == 0
    assert run.status == RunStatus.AWAITING_REVIEW
    assert attempt.status == ClassificationAttemptStatus.FAILED
    assert attempt.error_code == "classifier_unavailable"
    assert attempt.finished_at is not None
    assert review.reason == DocumentReviewReason.CLASSIFIER_UNAVAILABLE
    assert review.suggested_attempt == attempt
    assert not DocumentRouting.objects.exists()


def test_ambiguous_prediction_never_routes_automatically(
    modules: dict[str, AutomationModule],
    administrator: User,
    fiscal_client: FiscalClient,
) -> None:
    storage = MemoryStorage()
    run = _manual_upload(
        modules=modules,
        administrator=administrator,
        storage=storage,
        content=b"DOCUMENTO SINTETICO AMBIGUO",
    )
    classifier = RecordingClassifier(
        prediction=_prediction(client=fiscal_client, is_ambiguous=True)
    )

    result = execute_sc04(
        run.id,
        storage=storage,
        extractor=FixedExtractor("Nota fiscal sintética potencialmente ambígua."),
        classifier=classifier,
    )

    review = DocumentReview.objects.get()
    assert result.awaiting_review == 1
    assert review.reason == DocumentReviewReason.AMBIGUOUS_CLIENT
    assert not DocumentDecision.objects.exists()


def test_redelivery_recovers_interrupted_attempt_without_duplicate_decision(
    modules: dict[str, AutomationModule],
    administrator: User,
    fiscal_client: FiscalClient,
) -> None:
    storage = MemoryStorage()
    run = _manual_upload(
        modules=modules,
        administrator=administrator,
        storage=storage,
        content=b"DOCUMENTO SINTETICO INTERROMPIDO",
    )
    run.status = RunStatus.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=("status", "started_at"))
    document = FiscalDocument.objects.get()
    document.status = DocumentStatus.PROCESSING
    document.save(update_fields=("status", "updated_at"))
    interrupted = DocumentClassificationAttempt.objects.create(
        document=document,
        run=run,
        sequence=1,
        status=ClassificationAttemptStatus.PROCESSING,
        provider="fake",
        model="fake-v1",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        input_sha256="a" * 64,
        input_char_count=40,
    )

    result = execute_sc04(
        run.id,
        storage=storage,
        extractor=FixedExtractor(
            "NOTA FISCAL. CNPJ 12.345.678/0001-90. Aurora Participações Demo."
        ),
        classifier=RecordingClassifier(prediction=_prediction(client=fiscal_client)),
        resume_interrupted=True,
    )

    interrupted.refresh_from_db()
    assert interrupted.status == ClassificationAttemptStatus.FAILED
    assert interrupted.error_code == "worker_interrupted"
    assert DocumentClassificationAttempt.objects.filter(sequence=2).exists()
    assert DocumentDecision.objects.count() == 1
    assert result.routed == 1


def test_scheduled_run_is_unique_per_daily_competence(
    modules: dict[str, AutomationModule],
) -> None:
    del modules
    first, first_should_dispatch = prepare_scheduled_sc04_run(base_date=date(2026, 8, 31))
    repeated, repeated_should_dispatch = prepare_scheduled_sc04_run(base_date=date(2026, 8, 31))

    assert first_should_dispatch is True
    assert repeated_should_dispatch is False
    assert repeated.id == first.id
    assert first.idempotency_key == "sc04:scheduled:2026-08-31"
    assert first.trigger == RunTrigger.SCHEDULED
    assert first.status == RunStatus.QUEUED


def test_manual_inbox_and_scheduler_use_the_same_idempotent_pipeline(
    modules: dict[str, AutomationModule],
    administrator: User,
    fiscal_client: FiscalClient,
) -> None:
    del modules
    storage = MemoryStorage()
    inbox = FixedInbox(
        attachments=(
            IncomingDocument(
                source_reference="inbox:test:shared-message:attachment-1",
                filename="nota-compartilhada.txt",
                declared_content_type="text/plain",
                content=b"NOTA FISCAL SINTETICA COMPARTILHADA",
            ),
        )
    )
    classifier = RecordingClassifier(prediction=_prediction(client=fiscal_client))
    extractor = FixedExtractor("NOTA FISCAL. CNPJ 12.345.678/0001-90. Aurora Participações Demo.")
    manual_run = create_manual_sc04_inbox_run(triggered_by=administrator)

    manual_result = execute_sc04(
        manual_run.id,
        inbox=inbox,
        storage=storage,
        extractor=extractor,
        classifier=classifier,
    )
    scheduled_run, should_dispatch = prepare_scheduled_sc04_run(base_date=date(2026, 9, 1))
    scheduled_result = execute_sc04(
        scheduled_run.id,
        inbox=inbox,
        storage=storage,
        extractor=extractor,
        classifier=classifier,
    )

    manual_run.refresh_from_db()
    scheduled_run.refresh_from_db()
    assert should_dispatch is True
    assert manual_result.routed == 1
    assert manual_run.trigger == RunTrigger.MANUAL
    assert manual_run.status == RunStatus.SUCCEEDED
    assert scheduled_result.routed == 0
    assert scheduled_result.duplicates == 1
    assert scheduled_run.status == RunStatus.SUCCEEDED
    assert len(classifier.requests) == 1
    assert FiscalDocument.objects.count() == 1
    assert DocumentIntake.objects.count() == 1
    assert DocumentRunItem.objects.count() == 2


@freeze_time("2026-08-31 15:00:00")
def test_dispatch_command_publishes_sc04_daily_once(
    modules: dict[str, AutomationModule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc04 = modules["SC-04"]
    sc04.frequency = AutomationFrequency.DAILY
    sc04.save(update_fields=("frequency",))
    sc04_dispatched: list[str] = []
    sc20_dispatched: list[str] = []
    monkeypatch.setattr(dispatch_due_schedules.run_sc04_task, "delay", sc04_dispatched.append)
    monkeypatch.setattr(dispatch_due_schedules.run_sc20_task, "delay", sc20_dispatched.append)

    call_command("dispatch_due_schedules", verbosity=0)
    call_command("dispatch_due_schedules", verbosity=0)

    run = AutomationRun.objects.get(module_id="SC-04")
    assert run.idempotency_key == "sc04:scheduled:2026-08-31"
    assert sc04_dispatched == [str(run.id)]
    assert len(sc20_dispatched) == 1
