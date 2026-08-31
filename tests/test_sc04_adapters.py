from __future__ import annotations

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError
from django.test import override_settings
from openai import APIStatusError, APITimeoutError
from PIL import Image

from core.automations.models import DocumentType, ExtractionMethod
from core.automations.sc04 import classification, extraction, storage, validation
from core.automations.sc04.classification import OpenAIDocumentClassifier
from core.automations.sc04.contracts import (
    ClassificationRequest,
    ClassifierInvalidResponse,
    ClassifierPermanentError,
    ClassifierUnavailable,
    ClientCandidate,
    ExtractionError,
    InvalidDocument,
    StorageConfigurationError,
    StorageOperationError,
)
from core.automations.sc04.extraction import DefaultTextExtractor
from core.automations.sc04.storage import S3ObjectStorage
from core.automations.sc04.validation import validate_document


def _png_bytes(*, width: int = 80, height: int = 40) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_validation_sniffs_content_and_sanitizes_untrusted_filename() -> None:
    content = "Documento fiscal sintético".encode()

    validated = validate_document(
        filename="../../cliente\x00\r\nmalicioso.exe",
        declared_content_type="application/x-msdownload",
        content=content,
    )

    assert validated.filename == "malicioso.txt"
    assert validated.media_type == validation.TEXT_MEDIA_TYPE
    assert validated.extension == ".txt"
    assert validated.sha256 == hashlib.sha256(content).hexdigest()


@override_settings(SC04_MAX_UPLOAD_BYTES=4)
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "vazio"),
        (b"12345", "limite"),
        (b"\x00\x01\x02", "PDF, PNG, JPEG ou TXT"),
    ],
)
def test_validation_rejects_empty_oversized_and_binary_content(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(InvalidDocument, match=message):
        validate_document(
            filename="entrada.bin",
            declared_content_type="application/octet-stream",
            content=content,
        )


def test_validation_rejects_corrupt_image_instead_of_persisting_it() -> None:
    corrupt_png = bytes.fromhex("89504e470d0a1a0a") + b"not-a-real-png"

    with pytest.raises(
        InvalidDocument,
        match="imagem|PNG|corromp",
    ):
        validate_document(
            filename="corrompida.png",
            declared_content_type="image/png",
            content=corrupt_png,
        )


@override_settings(SC04_MAX_EXTRACTED_CHARS=18)
def test_text_extraction_normalizes_and_limits_content() -> None:
    result = DefaultTextExtractor().extract(
        content=b"  primeira linha  \r\n\r\n\r\nsegunda linha longa",
        media_type=validation.TEXT_MEDIA_TYPE,
    )

    assert result.method == ExtractionMethod.PLAIN_TEXT
    assert result.text == "primeira linha\n\nse"
    assert len(result.text) == 18
    assert result.page_count is None


def test_text_extraction_rejects_whitespace_only_input() -> None:
    with pytest.raises(ExtractionError, match="texto suficiente"):
        DefaultTextExtractor().extract(
            content=b" \t\r\n ",
            media_type=validation.TEXT_MEDIA_TYPE,
        )


def test_pdf_extraction_uses_pypdf_and_reports_page_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        is_encrypted = False
        pages = [Page("Página um"), Page("Página dois")]

        def __init__(self, source: BytesIO, *, strict: bool) -> None:
            assert source.read() == b"synthetic-pdf"
            assert strict is True

    monkeypatch.setattr(extraction, "PdfReader", Reader)

    result = DefaultTextExtractor().extract(
        content=b"synthetic-pdf",
        media_type=validation.PDF_MEDIA_TYPE,
    )

    assert result.text == "Página um\n\nPágina dois"
    assert result.method == ExtractionMethod.PDF_TEXT
    assert result.page_count == 2


@override_settings(
    SC04_MAX_IMAGE_PIXELS=10_000,
    SC04_TESSERACT_LANGUAGE="por",
    SC04_OCR_TIMEOUT_SECONDS=7,
)
def test_image_extraction_calls_tesseract_with_bounded_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_ocr(image: Image.Image, **kwargs: object) -> str:
        calls.append({"size": image.size, "mode": image.mode, **kwargs})
        return "  EXTRATO SINTÉTICO\nCliente Demo  "

    monkeypatch.setattr(extraction.pytesseract, "image_to_string", fake_ocr)

    result = DefaultTextExtractor().extract(
        content=_png_bytes(),
        media_type=validation.PNG_MEDIA_TYPE,
    )

    assert result.text == "EXTRATO SINTÉTICO\nCliente Demo"
    assert result.method == ExtractionMethod.OCR
    assert result.page_count == 1
    assert calls == [{"size": (80, 40), "mode": "RGB", "lang": "por", "timeout": 7}]


def test_image_extraction_translates_tesseract_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> str:
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(extraction.pytesseract, "image_to_string", timeout)

    with pytest.raises(ExtractionError, match="OCR"):
        DefaultTextExtractor().extract(
            content=_png_bytes(),
            media_type=validation.PNG_MEDIA_TYPE,
        )


class _FakeResponses:
    def __init__(self, result: object | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class _FakeOpenAI:
    def __init__(self, responses: _FakeResponses) -> None:
        self.responses = responses


def _classification_request() -> ClassificationRequest:
    return ClassificationRequest(
        extracted_text="NOTA FISCAL Cliente Aurora CNPJ 12.345.678/0001-90",
        content_sha256="a" * 64,
        clients=(
            ClientCandidate(
                code="aurora",
                name="Aurora Participações Demo",
                aliases=("Aurora",),
            ),
            ClientCandidate(
                code="horizonte",
                name="Horizonte Comércio Demo",
                aliases=("Horizonte",),
            ),
        ),
        exact_client_code="aurora",
    )


def _provider_response(payload: dict[str, object], *, status: str = "completed") -> object:
    return SimpleNamespace(
        id="resp_sc04_test",
        model="gpt-test",
        status=status,
        output_text=json.dumps(payload),
    )


def _valid_prediction_payload() -> dict[str, object]:
    return {
        "document_type": DocumentType.INVOICE,
        "type_confidence": 0.96,
        "client_code": "aurora",
        "client_confidence": 0.97,
        "is_ambiguous": False,
        "evidence": ["Texto contém NOTA FISCAL e o CNPJ sintético do cliente."],
    }


def test_classifier_uses_strict_non_persistent_structured_output() -> None:
    responses = _FakeResponses(_provider_response(_valid_prediction_payload()))
    classifier = OpenAIDocumentClassifier(client=Any, model="gpt-test")
    classifier._client = _FakeOpenAI(responses)  # type: ignore[assignment]

    prediction = classifier.classify(_classification_request())

    assert prediction.document_type == DocumentType.INVOICE
    assert prediction.client_code == "aurora"
    assert prediction.provider_response_id == "resp_sc04_test"
    call = responses.calls[0]
    assert call["store"] is False
    assert call["max_output_tokens"] == 500
    text_config = call["text"]
    assert isinstance(text_config, dict)
    response_format = text_config["format"]
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
    request_payload = json.loads(str(call["input"]))
    assert request_payload["exact_client_code"] == "aurora"
    assert request_payload["document_text"].startswith("NOTA FISCAL")


def test_classifier_rejects_unknown_client_even_after_structured_output() -> None:
    payload = _valid_prediction_payload()
    payload["client_code"] = "cliente-fora-do-catalogo"
    responses = _FakeResponses(_provider_response(payload))
    classifier = OpenAIDocumentClassifier(client=Any, model="gpt-test")
    classifier._client = _FakeOpenAI(responses)  # type: ignore[assignment]

    with pytest.raises(ClassifierInvalidResponse, match="cliente desconhecido"):
        classifier.classify(_classification_request())


def test_classifier_requires_at_least_one_verifiable_evidence() -> None:
    payload = _valid_prediction_payload()
    payload["evidence"] = []
    responses = _FakeResponses(_provider_response(payload))
    classifier = OpenAIDocumentClassifier(client=Any, model="gpt-test")
    classifier._client = _FakeOpenAI(responses)  # type: ignore[assignment]

    with pytest.raises(ClassifierInvalidResponse, match="evidências"):
        classifier.classify(_classification_request())

    schema = classification._response_schema(["aurora"])
    properties = schema["properties"]
    assert properties["evidence"]["minItems"] == 1


@pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503])
def test_classifier_translates_transient_http_statuses(status_code: int) -> None:
    request = httpx.Request("POST", "https://api.openai.test/v1/responses")
    response = httpx.Response(status_code, request=request)
    error = APIStatusError("provider error", response=response, body=None)
    responses = _FakeResponses(error=error)
    classifier = OpenAIDocumentClassifier(client=Any, model="gpt-test")
    classifier._client = _FakeOpenAI(responses)  # type: ignore[assignment]

    with pytest.raises(ClassifierUnavailable, match="temporariamente"):
        classifier.classify(_classification_request())


def test_classifier_translates_timeout_and_permanent_http_error() -> None:
    request = httpx.Request("POST", "https://api.openai.test/v1/responses")
    timeout_responses = _FakeResponses(error=APITimeoutError(request=request))
    timeout_classifier = OpenAIDocumentClassifier(client=Any, model="gpt-test")
    timeout_classifier._client = _FakeOpenAI(timeout_responses)  # type: ignore[assignment]

    with pytest.raises(ClassifierUnavailable, match="temporariamente"):
        timeout_classifier.classify(_classification_request())

    response = httpx.Response(400, request=request)
    bad_request_responses = _FakeResponses(
        error=APIStatusError("bad request", response=response, body=None)
    )
    bad_request_classifier = OpenAIDocumentClassifier(client=Any, model="gpt-test")
    bad_request_classifier._client = _FakeOpenAI(  # type: ignore[assignment]
        bad_request_responses
    )

    with pytest.raises(ClassifierPermanentError, match="recusou"):
        bad_request_classifier.classify(_classification_request())


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(id="resp", model="gpt-test", status="incomplete", output_text=""),
        SimpleNamespace(id="resp", model="gpt-test", status="completed", output_text="not-json"),
    ],
)
def test_classifier_rejects_incomplete_or_non_json_response(response: object) -> None:
    responses = _FakeResponses(response)
    classifier = OpenAIDocumentClassifier(client=Any, model="gpt-test")
    classifier._client = _FakeOpenAI(responses)  # type: ignore[assignment]

    with pytest.raises(ClassifierInvalidResponse):
        classifier.classify(_classification_request())


def _client_error(*, code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class _FakeBody:
    def __init__(self, content: bytes, *, error: OSError | None = None) -> None:
        self.content = content
        self.error = error
        self.closed = False

    def read(self, amount: int) -> bytes:
        if self.error is not None:
            raise self.error
        return self.content[:amount]

    def close(self) -> None:
        self.closed = True


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.put_calls: list[dict[str, object]] = []
        self.copy_calls: list[dict[str, object]] = []
        self.body_override: _FakeBody | None = None

    def head_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        try:
            stored = self.objects[key]
        except KeyError as exc:
            raise _client_error(code="NoSuchKey", status=404, operation="HeadObject") from exc
        return {
            "ContentLength": len(bytes(stored["content"])),
            "Metadata": dict(stored.get("metadata", {})),
        }

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.put_calls.append(kwargs)
        key = str(kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _client_error(code="PreconditionFailed", status=412, operation="PutObject")
        self.objects[key] = {
            "content": bytes(kwargs["Body"]),
            "metadata": dict(kwargs.get("Metadata", {})),
        }
        return {"ETag": "fake-etag"}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        if self.body_override is not None:
            return {"Body": self.body_override}
        try:
            content = bytes(self.objects[key]["content"])
        except KeyError as exc:
            raise _client_error(code="NoSuchKey", status=404, operation="GetObject") from exc
        return {"Body": _FakeBody(content)}

    def copy_object(self, **kwargs: object) -> dict[str, object]:
        self.copy_calls.append(kwargs)
        destination = str(kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and destination in self.objects:
            raise _client_error(code="PreconditionFailed", status=412, operation="CopyObject")
        source = kwargs["CopySource"]
        assert isinstance(source, dict)
        source_key = str(source["Key"])
        try:
            content = bytes(self.objects[source_key]["content"])
        except KeyError as exc:
            raise _client_error(code="NoSuchKey", status=404, operation="CopyObject") from exc
        self.objects[destination] = {
            "content": content,
            "metadata": dict(kwargs.get("Metadata", {})),
        }
        return {"CopyObjectResult": {"ETag": "fake-copy-etag"}}


def test_s3_put_get_and_copy_round_trip() -> None:
    client = _FakeS3Client()
    object_storage = S3ObjectStorage(client=client, bucket="private-bucket")
    content = b"synthetic-document"

    stored = object_storage.put_bytes(
        key="sc04/originals/document.txt",
        content=content,
        content_type="text/plain",
    )
    copied = object_storage.copy_if_absent(
        source_key=stored.key,
        destination_key="sc04/routed/client/invoice/document.txt",
        content_type="text/plain",
    )

    assert stored.byte_size == len(content)
    assert copied.byte_size == len(content)
    assert object_storage.get_bytes(copied.key) == content
    assert client.put_calls[0]["ContentType"] == "text/plain"
    assert client.copy_calls[0]["MetadataDirective"] == "REPLACE"


def test_s3_writes_are_conditional_and_keep_integrity_metadata() -> None:
    client = _FakeS3Client()
    object_storage = S3ObjectStorage(client=client, bucket="private-bucket")
    content = b"synthetic-document"

    object_storage.put_bytes(
        key="sc04/originals/document.txt",
        content=content,
        content_type="text/plain",
    )

    call = client.put_calls[0]
    assert call["IfNoneMatch"] == "*"
    metadata = call["Metadata"]
    assert isinstance(metadata, dict)
    assert metadata["sha256"] == hashlib.sha256(content).hexdigest()

    object_storage.copy_if_absent(
        source_key="sc04/originals/document.txt",
        destination_key="sc04/routed/client/invoice/document.txt",
        content_type="text/plain",
    )
    assert client.copy_calls[0]["IfNoneMatch"] == "*"


def test_s3_rejects_existing_object_with_mismatched_integrity_metadata() -> None:
    client = _FakeS3Client()
    content = b"expected-content"
    client.objects["sc04/originals/document.txt"] = {
        "content": b"different-content",
        "metadata": {"sha256": hashlib.sha256(b"different-content").hexdigest()},
    }
    object_storage = S3ObjectStorage(client=client, bucket="private-bucket")

    with pytest.raises(StorageOperationError, match="não corresponde"):
        object_storage.put_bytes(
            key="sc04/originals/document.txt",
            content=content,
            content_type="text/plain",
        )


def test_s3_closes_streaming_body_when_read_fails() -> None:
    client = _FakeS3Client()
    body = _FakeBody(b"", error=OSError("socket closed"))
    client.body_override = body
    object_storage = S3ObjectStorage(client=client, bucket="private-bucket")

    with pytest.raises(StorageOperationError, match="recuperado"):
        object_storage.get_bytes("sc04/originals/document.txt")

    assert body.closed is True


@override_settings(SC04_MAX_UPLOAD_BYTES=3)
def test_s3_rejects_oversized_download() -> None:
    client = _FakeS3Client()
    client.objects["sc04/originals/document.txt"] = {
        "content": b"1234",
        "metadata": {},
    }
    object_storage = S3ObjectStorage(client=client, bucket="private-bucket")

    with pytest.raises(StorageOperationError, match="ultrapassa"):
        object_storage.get_bytes("sc04/originals/document.txt")


@override_settings(
    S3_ENDPOINT_URL="",
    S3_ACCESS_KEY_ID="",
    S3_SECRET_ACCESS_KEY="",
    S3_BUCKET_NAME="",
)
def test_s3_builder_rejects_incomplete_configuration() -> None:
    with pytest.raises(StorageConfigurationError, match="não está configurado"):
        storage.build_object_storage()
