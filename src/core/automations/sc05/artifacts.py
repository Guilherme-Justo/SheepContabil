from __future__ import annotations

import hashlib
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from core.automations.sc05.contracts import (
    ArtifactStorageError,
    ScreenshotStorage,
    StoredScreenshot,
)

MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024


class S3ScreenshotStorage:
    def __init__(self, *, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, *, key: str, content: bytes) -> StoredScreenshot:
        if not content or len(content) > MAX_SCREENSHOT_BYTES:
            raise ArtifactStorageError("A captura de tela gerada é inválida ou excede 5 MiB.")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentLength=len(content),
                ContentType="image/png",
                Metadata={
                    "managed-by": "sheepcontabil-sc05",
                    "sha256": sha256,
                },
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if not _is_precondition_failed(exc) or not self._matches_existing(
                key=key,
                sha256=sha256,
                byte_size=len(content),
            ):
                raise ArtifactStorageError() from exc
        except BotoCoreError as exc:
            raise ArtifactStorageError() from exc
        return StoredScreenshot(key=key, sha256=sha256, byte_size=len(content))

    def get(self, *, key: str) -> bytes:
        body: Any = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            content = body.read(MAX_SCREENSHOT_BYTES + 1)
        except (BotoCoreError, ClientError, KeyError, OSError) as exc:
            raise ArtifactStorageError(
                "A captura de tela não pôde ser recuperada do storage privado."
            ) from exc
        finally:
            if body is not None:
                body.close()
        if len(content) > MAX_SCREENSHOT_BYTES:
            raise ArtifactStorageError("A captura armazenada excede o limite permitido.")
        return bytes(content)

    def _matches_existing(self, *, key: str, sha256: str, byte_size: int) -> bool:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError):
            return False
        metadata = response.get("Metadata", {})
        return (
            int(response.get("ContentLength", -1)) == byte_size
            and isinstance(metadata, dict)
            and str(metadata.get("sha256", "")) == sha256
        )


def _is_precondition_failed(exc: ClientError) -> bool:
    status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return status == 412 or code in {"412", "PreconditionFailed"}


def build_screenshot_storage() -> ScreenshotStorage:
    endpoint = str(settings.S3_ENDPOINT_URL).strip()
    access_key = str(settings.S3_ACCESS_KEY_ID).strip()
    secret_key = str(settings.S3_SECRET_ACCESS_KEY).strip()
    bucket = str(settings.S3_BUCKET_NAME).strip()
    if not all((endpoint, access_key, secret_key, bucket)):
        raise ArtifactStorageError("O storage privado de evidências não está configurado.")
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
    return S3ScreenshotStorage(client=client, bucket=bucket)
