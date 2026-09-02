from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IncomingDocument:
    source_reference: str
    filename: str
    declared_content_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ValidatedDocument:
    filename: str
    media_type: str
    extension: str
    content: bytes
    sha256: str
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    byte_size: int
    content_type: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    text: str
    method: str
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class ClassificationRequest:
    extracted_text: str
    content_sha256: str
    clients: tuple[ClientCandidate, ...]
    exact_client_code: str | None = None


@dataclass(frozen=True, slots=True)
class ClientCandidate:
    code: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClassificationPrediction:
    document_type: str
    type_confidence: float
    client_code: str | None
    client_confidence: float
    evidence: tuple[str, ...]
    provider_response_id: str
    model: str
    candidate_snapshot: dict[str, object]
    is_ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class IngestionResult:
    intake_id: str
    document_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class SC04ExecutionResult:
    received: int
    routed: int
    awaiting_review: int
    duplicates: int
    failed: int


class DocumentInbox(Protocol):
    def list_attachments(self) -> tuple[IncomingDocument, ...]: ...


class ObjectStorage(Protocol):
    def put_bytes(self, *, key: str, content: bytes, content_type: str) -> StoredObject: ...

    def get_bytes(self, key: str) -> bytes: ...

    def copy_if_absent(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
    ) -> StoredObject: ...


class TextExtractor(Protocol):
    def extract(self, *, content: bytes, media_type: str) -> ExtractionResult: ...


class DocumentClassifier(Protocol):
    provider: str
    model: str

    def classify(self, request: ClassificationRequest) -> ClassificationPrediction: ...


class SC04Error(Exception):
    """Base exception with a safe operational message."""


class InvalidDocument(SC04Error):
    pass


class StorageConfigurationError(SC04Error):
    pass


class StorageOperationError(SC04Error):
    pass


class ExtractionError(SC04Error):
    pass


class ClassifierError(SC04Error):
    code = "classifier_error"


class ClassifierUnavailable(ClassifierError):
    code = "classifier_unavailable"


class ClassifierInvalidResponse(ClassifierError):
    code = "classifier_invalid_response"


class ClassifierPermanentError(ClassifierError):
    code = "classifier_permanent_error"
