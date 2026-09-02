from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from core.automations.sc04.contracts import StorageOperationError
from core.automations.sc04.storage import (
    FileSystemObjectStorage,
    build_object_storage,
)
from core.automations.sc05.artifacts import (
    FileSystemScreenshotStorage,
    build_screenshot_storage,
)
from core.automations.sc05.contracts import ArtifactStorageError


def test_sc04_filesystem_object_storage_put_get_and_copy(tmp_path: Path) -> None:
    storage = FileSystemObjectStorage(base_path=tmp_path)
    content = b"Documento fiscal de teste local"
    sha256 = hashlib.sha256(content).hexdigest()

    # 1. Put bytes
    stored = storage.put_bytes(
        key="intake/doc1.txt",
        content=content,
        content_type="text/plain",
    )
    assert stored.key == "intake/doc1.txt"
    assert stored.byte_size == len(content)
    assert (tmp_path / "intake" / "doc1.txt").exists()

    # 2. Duplicate put of identical bytes is idempotent
    stored_dup = storage.put_bytes(
        key="intake/doc1.txt",
        content=content,
        content_type="text/plain",
    )
    assert stored_dup.key == "intake/doc1.txt"

    # 3. Put conflicting content raises StorageOperationError
    with pytest.raises(StorageOperationError, match="não corresponde ao documento"):
        storage.put_bytes(
            key="intake/doc1.txt",
            content=b"Outro conteudo diferente",
            content_type="text/plain",
        )

    # 4. Get bytes
    retrieved = storage.get_bytes("intake/doc1.txt")
    assert retrieved == content
    assert hashlib.sha256(retrieved).hexdigest() == sha256

    # 5. Get missing raises StorageOperationError
    with pytest.raises(StorageOperationError, match="não pôde ser recuperado"):
        storage.get_bytes("intake/inexistente.txt")

    # 6. Copy if absent
    copied = storage.copy_if_absent(
        source_key="intake/doc1.txt",
        destination_key="clients/123/doc1.txt",
        content_type="text/plain",
    )
    assert copied.key == "clients/123/doc1.txt"
    assert copied.byte_size == len(content)
    assert (tmp_path / "clients" / "123" / "doc1.txt").exists()

    # 7. Copy duplicate is idempotent
    copied_dup = storage.copy_if_absent(
        source_key="intake/doc1.txt",
        destination_key="clients/123/doc1.txt",
        content_type="text/plain",
    )
    assert copied_dup.key == "clients/123/doc1.txt"


def test_sc05_filesystem_screenshot_storage_put_and_get(tmp_path: Path) -> None:
    storage = FileSystemScreenshotStorage(base_path=tmp_path)
    screenshot_bytes = b"fake-png-screenshot-content-data"
    sha256 = hashlib.sha256(screenshot_bytes).hexdigest()

    # 1. Put screenshot
    stored = storage.put(key="runs/run-1/step-1.png", content=screenshot_bytes)
    assert stored.key == "runs/run-1/step-1.png"
    assert stored.sha256 == sha256
    assert (tmp_path / "runs" / "run-1" / "step-1.png").exists()

    # 2. Get screenshot
    retrieved = storage.get(key="runs/run-1/step-1.png")
    assert retrieved == screenshot_bytes

    # 3. Get missing raises ArtifactStorageError
    with pytest.raises(ArtifactStorageError, match="não pôde ser recuperada"):
        storage.get(key="runs/run-1/missing.png")

    # 4. Put empty raises ArtifactStorageError
    with pytest.raises(ArtifactStorageError, match="inválida ou excede"):
        storage.put(key="runs/run-1/empty.png", content=b"")


def test_build_storage_factories_fallback_in_debug(settings: Any) -> None:
    settings.DEBUG = True
    settings.S3_ENDPOINT_URL = "http://localhost:9999"
    settings.S3_ACCESS_KEY_ID = "test"
    settings.S3_SECRET_ACCESS_KEY = "test"
    settings.S3_BUCKET_NAME = "test-bucket"

    sc04_storage = build_object_storage()
    assert isinstance(sc04_storage, FileSystemObjectStorage)

    sc05_storage = build_screenshot_storage()
    assert isinstance(sc05_storage, FileSystemScreenshotStorage)
