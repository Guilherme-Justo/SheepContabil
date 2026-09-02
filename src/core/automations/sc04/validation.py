from __future__ import annotations

import hashlib
import re
import warnings
from io import BytesIO
from pathlib import PurePath

from django.conf import settings
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from core.automations.sc04.contracts import InvalidDocument, ValidatedDocument

PDF_MEDIA_TYPE = "application/pdf"
PNG_MEDIA_TYPE = "image/png"
JPEG_MEDIA_TYPE = "image/jpeg"
TEXT_MEDIA_TYPE = "text/plain"
ALLOWED_MEDIA_TYPES = (PDF_MEDIA_TYPE, PNG_MEDIA_TYPE, JPEG_MEDIA_TYPE, TEXT_MEDIA_TYPE)


def validate_document(
    *,
    filename: str,
    declared_content_type: str,
    content: bytes,
) -> ValidatedDocument:
    del declared_content_type
    if not content:
        raise InvalidDocument("O arquivo está vazio.")
    max_bytes = int(settings.SC04_MAX_UPLOAD_BYTES)
    if len(content) > max_bytes:
        raise InvalidDocument(f"O arquivo ultrapassa o limite de {max_bytes // (1024 * 1024)} MiB.")

    media_type, extension = _detect_media_type(content)
    page_count = _validate_structure(media_type=media_type, content=content)
    safe_filename = sanitize_filename(filename, extension=extension)
    return ValidatedDocument(
        filename=safe_filename,
        media_type=media_type,
        extension=extension,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        page_count=page_count,
    )


def sanitize_filename(filename: str, *, extension: str) -> str:
    normalized_path = re.sub(r"[\x00-\x1f\x7f]+", "/", filename.replace("\\", "/"))
    leaf = PurePath(normalized_path).name.strip()
    stem = PurePath(leaf).stem[:180].strip(" .") or "documento"
    stem = re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ._ -]+", "-", stem).strip(" .-")
    return f"{stem or 'documento'}{extension}"


def original_storage_key(*, sha256: str, extension: str) -> str:
    return f"sc04/originals/{sha256[:2]}/{sha256}{extension}"


def routed_storage_key(
    *,
    client_prefix: str,
    document_type: str,
    document_id: str,
    extension: str,
) -> str:
    return f"sc04/routed/{client_prefix}/{document_type}/{document_id}{extension}"


def extension_for_media_type(media_type: str) -> str:
    mapping = {
        PDF_MEDIA_TYPE: ".pdf",
        PNG_MEDIA_TYPE: ".png",
        JPEG_MEDIA_TYPE: ".jpg",
        TEXT_MEDIA_TYPE: ".txt",
    }
    try:
        return mapping[media_type]
    except KeyError as exc:
        raise InvalidDocument("Tipo de arquivo não permitido.") from exc


def _detect_media_type(content: bytes) -> tuple[str, str]:
    if content.startswith(b"%PDF-"):
        return PDF_MEDIA_TYPE, ".pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG_MEDIA_TYPE, ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return JPEG_MEDIA_TYPE, ".jpg"
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidDocument("Envie um PDF, PNG, JPEG ou TXT UTF-8 válido.") from exc
    if "\x00" in decoded:
        raise InvalidDocument("Envie um PDF, PNG, JPEG ou TXT UTF-8 válido.")
    printable = sum(character.isprintable() or character in "\r\n\t" for character in decoded)
    if printable / max(1, len(decoded)) < 0.9:
        raise InvalidDocument("Envie um PDF, PNG, JPEG ou TXT UTF-8 válido.")
    return TEXT_MEDIA_TYPE, ".txt"


def _validate_structure(*, media_type: str, content: bytes) -> int | None:
    if media_type == TEXT_MEDIA_TYPE:
        if not content.decode("utf-8").strip():
            raise InvalidDocument("O TXT não contém texto para classificação.")
        return None
    if media_type == PDF_MEDIA_TYPE:
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if not reader.pages:
                raise InvalidDocument("O PDF não contém páginas válidas.")
            if len(reader.pages) > int(settings.SC04_MAX_PDF_PAGES):
                raise InvalidDocument(
                    f"O PDF ultrapassa o limite de {settings.SC04_MAX_PDF_PAGES} páginas."
                )
            return len(reader.pages)
        except InvalidDocument:
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise InvalidDocument("O PDF está corrompido ou incompleto.") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.width * image.height > int(settings.SC04_MAX_IMAGE_PIXELS):
                    raise InvalidDocument("A imagem excede o limite seguro de pixels.")
                image.verify()
        return 1
    except InvalidDocument:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError) as exc:
        raise InvalidDocument("A imagem está corrompida ou incompleta.") from exc
