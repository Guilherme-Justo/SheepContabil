from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from core.automations.sc04.contracts import (
    ObjectStorage,
    StorageConfigurationError,
    StorageOperationError,
    StoredObject,
)


class S3ObjectStorage:
    def __init__(self, *, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put_bytes(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        expected_sha256 = hashlib.sha256(content).hexdigest()
        existing = self._head(key)
        if existing is not None:
            if existing.byte_size != len(content) or existing.sha256 != expected_sha256:
                raise StorageOperationError("O objeto existente não corresponde ao documento.")
            return StoredObject(key=key, byte_size=existing.byte_size, content_type=content_type)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentLength=len(content),
                ContentType=content_type,
                Metadata={
                    "managed-by": "sheepcontabil-sc04",
                    "sha256": expected_sha256,
                },
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _is_precondition_failed(exc):
                concurrent = self._head(key)
                if (
                    concurrent is not None
                    and concurrent.byte_size == len(content)
                    and concurrent.sha256 == expected_sha256
                ):
                    return StoredObject(
                        key=key,
                        byte_size=concurrent.byte_size,
                        content_type=content_type,
                    )
            raise StorageOperationError(
                "O storage privado está temporariamente indisponível."
            ) from exc
        except BotoCoreError as exc:
            raise StorageOperationError(
                "O storage privado está temporariamente indisponível."
            ) from exc
        return StoredObject(key=key, byte_size=len(content), content_type=content_type)

    def get_bytes(self, key: str) -> bytes:
        body: Any = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            content = body.read(int(settings.SC04_MAX_UPLOAD_BYTES) + 1)
        except (BotoCoreError, ClientError, KeyError, OSError) as exc:
            raise StorageOperationError("O original não pôde ser recuperado do storage.") from exc
        finally:
            if body is not None:
                body.close()
        if len(content) > int(settings.SC04_MAX_UPLOAD_BYTES):
            raise StorageOperationError("O objeto armazenado ultrapassa o limite permitido.")
        return bytes(content)

    def copy_if_absent(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
    ) -> StoredObject:
        source = self._head(source_key)
        if source is None or not source.sha256:
            raise StorageOperationError("O storage não confirmou a integridade do original.")
        existing = self._head(destination_key)
        if existing is not None:
            if existing.byte_size != source.byte_size or existing.sha256 != source.sha256:
                raise StorageOperationError("O destino existente não corresponde ao original.")
            return StoredObject(
                key=destination_key,
                byte_size=existing.byte_size,
                content_type=content_type,
            )
        try:
            self._client.copy_object(
                Bucket=self._bucket,
                Key=destination_key,
                CopySource={"Bucket": self._bucket, "Key": source_key},
                ContentType=content_type,
                MetadataDirective="REPLACE",
                Metadata={
                    "managed-by": "sheepcontabil-sc04",
                    "sha256": source.sha256,
                },
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _is_precondition_failed(exc):
                concurrent = self._head(destination_key)
                if (
                    concurrent is not None
                    and concurrent.byte_size == source.byte_size
                    and concurrent.sha256 == source.sha256
                ):
                    return StoredObject(
                        key=destination_key,
                        byte_size=concurrent.byte_size,
                        content_type=content_type,
                    )
            raise StorageOperationError("O documento não pôde ser encaminhado no storage.") from exc
        except BotoCoreError as exc:
            raise StorageOperationError("O documento não pôde ser encaminhado no storage.") from exc
        size = self._head(destination_key)
        if size is None:
            raise StorageOperationError("O storage não confirmou o documento encaminhado.")
        if size.byte_size != source.byte_size or size.sha256 != source.sha256:
            raise StorageOperationError(
                "O documento encaminhado falhou na verificação de integridade."
            )
        return StoredObject(
            key=destination_key, byte_size=size.byte_size, content_type=content_type
        )

    def _head(self, key: str) -> _StoredMetadata | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageOperationError(
                "O storage privado está temporariamente indisponível."
            ) from exc
        except BotoCoreError as exc:
            raise StorageOperationError(
                "O storage privado está temporariamente indisponível."
            ) from exc
        metadata = response.get("Metadata", {})
        sha256 = str(metadata.get("sha256", "")) if isinstance(metadata, dict) else ""
        return _StoredMetadata(
            byte_size=int(response.get("ContentLength", 0)),
            sha256=sha256,
        )


@dataclass(frozen=True, slots=True)
class _StoredMetadata:
    byte_size: int
    sha256: str


def _is_precondition_failed(exc: ClientError) -> bool:
    status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return status == 412 or code in {"412", "PreconditionFailed"}


class FileSystemObjectStorage:
    def __init__(self, *, base_path: Path) -> None:
        self._base_path = base_path

    def put_bytes(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        expected_sha256 = hashlib.sha256(content).hexdigest()
        file_path = self._base_path / key
        meta_path = self._base_path / f"{key}.meta.json"

        if file_path.exists():
            try:
                existing_bytes = file_path.read_bytes()
            except OSError as exc:
                raise StorageOperationError(
                    "O storage privado está temporariamente indisponível."
                ) from exc
            if (
                len(existing_bytes) != len(content)
                or hashlib.sha256(existing_bytes).hexdigest() != expected_sha256
            ):
                raise StorageOperationError("O objeto existente não corresponde ao documento.")
            return StoredObject(key=key, byte_size=len(existing_bytes), content_type=content_type)

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                json.dumps(
                    {
                        "sha256": expected_sha256,
                        "byte_size": len(content),
                        "content_type": content_type,
                    }
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            raise StorageOperationError(
                "O storage privado está temporariamente indisponível."
            ) from exc

        return StoredObject(key=key, byte_size=len(content), content_type=content_type)

    def get_bytes(self, key: str) -> bytes:
        file_path = self._base_path / key
        if not file_path.exists():
            raise StorageOperationError("O original não pôde ser recuperado do storage.")
        try:
            content = file_path.read_bytes()
        except OSError as exc:
            raise StorageOperationError("O original não pôde ser recuperado do storage.") from exc

        if len(content) > int(settings.SC04_MAX_UPLOAD_BYTES):
            raise StorageOperationError("O objeto armazenado ultrapassa o limite permitido.")
        return content

    def copy_if_absent(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
    ) -> StoredObject:
        src_path = self._base_path / source_key
        if not src_path.exists():
            raise StorageOperationError("O storage não confirmou a integridade do original.")
        try:
            source_bytes = src_path.read_bytes()
        except OSError as exc:
            raise StorageOperationError(
                "O storage não confirmou a integridade do original."
            ) from exc
        source_sha = hashlib.sha256(source_bytes).hexdigest()

        dst_path = self._base_path / destination_key
        dst_meta = self._base_path / f"{destination_key}.meta.json"

        if dst_path.exists():
            try:
                dst_bytes = dst_path.read_bytes()
            except OSError as exc:
                raise StorageOperationError(
                    "O documento não pôde ser encaminhado no storage."
                ) from exc
            if (
                len(dst_bytes) != len(source_bytes)
                or hashlib.sha256(dst_bytes).hexdigest() != source_sha
            ):
                raise StorageOperationError("O destino existente não corresponde ao original.")
            return StoredObject(
                key=destination_key, byte_size=len(dst_bytes), content_type=content_type
            )

        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_path, dst_path)
            dst_meta.parent.mkdir(parents=True, exist_ok=True)
            dst_meta.write_text(
                json.dumps(
                    {
                        "sha256": source_sha,
                        "byte_size": len(source_bytes),
                        "content_type": content_type,
                    }
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            raise StorageOperationError("O documento não pôde ser encaminhado no storage.") from exc

        return StoredObject(
            key=destination_key, byte_size=dst_path.stat().st_size, content_type=content_type
        )


def build_object_storage() -> ObjectStorage:
    endpoint = str(settings.S3_ENDPOINT_URL).strip()
    access_key = str(settings.S3_ACCESS_KEY_ID).strip()
    secret_key = str(settings.S3_SECRET_ACCESS_KEY).strip()
    bucket = str(settings.S3_BUCKET_NAME).strip()

    if getattr(settings, "DEBUG", False):
        if not all((endpoint, access_key, secret_key, bucket)):
            storage_dir = Path(settings.PROJECT_DIR) / "var" / "storage" / "sc04"
            return FileSystemObjectStorage(base_path=storage_dir)
        try:
            config = Config(
                signature_version="s3v4",
                connect_timeout=1,
                read_timeout=2,
                retries={"mode": "standard", "max_attempts": 1},
                s3={"addressing_style": str(settings.S3_ADDRESSING_STYLE)},
            )
            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=str(settings.S3_REGION),
                config=config,
            )
            client.head_bucket(Bucket=bucket)
            return S3ObjectStorage(client=client, bucket=bucket)
        except Exception:
            storage_dir = Path(settings.PROJECT_DIR) / "var" / "storage" / "sc04"
            return FileSystemObjectStorage(base_path=storage_dir)

    if not all((endpoint, access_key, secret_key, bucket)):
        raise StorageConfigurationError("O storage privado do SC-04 não está configurado.")
    config = Config(
        signature_version="s3v4",
        connect_timeout=3,
        read_timeout=20,
        retries={"mode": "standard", "max_attempts": 3},
        s3={"addressing_style": str(settings.S3_ADDRESSING_STYLE)},
    )
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=str(settings.S3_REGION),
        config=config,
    )
    return S3ObjectStorage(client=client, bucket=bucket)
