from __future__ import annotations

import re
import warnings
from io import BytesIO

import pytesseract
from django.conf import settings
from PIL import Image, ImageOps
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from core.automations.models import ExtractionMethod
from core.automations.sc04.contracts import ExtractionError, ExtractionResult
from core.automations.sc04.validation import (
    JPEG_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    PNG_MEDIA_TYPE,
    TEXT_MEDIA_TYPE,
)


class DefaultTextExtractor:
    def extract(self, *, content: bytes, media_type: str) -> ExtractionResult:
        if media_type == TEXT_MEDIA_TYPE:
            return self._extract_text(content)
        if media_type == PDF_MEDIA_TYPE:
            return self._extract_pdf(content)
        if media_type in {PNG_MEDIA_TYPE, JPEG_MEDIA_TYPE}:
            return self._extract_image(content)
        raise ExtractionError("O tipo documental não possui extrator configurado.")

    def _extract_text(self, content: bytes) -> ExtractionResult:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExtractionError("O TXT precisa usar codificação UTF-8.") from exc
        normalized = _normalize_text(text)
        if not normalized:
            raise ExtractionError("O TXT não contém texto suficiente para classificação.")
        return ExtractionResult(
            text=normalized,
            method=ExtractionMethod.PLAIN_TEXT,
            page_count=None,
        )

    def _extract_pdf(self, content: bytes) -> ExtractionResult:
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise ExtractionError("PDF protegido por senha não pode ser processado.")
            if len(reader.pages) > int(settings.SC04_MAX_PDF_PAGES):
                raise ExtractionError(
                    f"O PDF ultrapassa o limite de {settings.SC04_MAX_PDF_PAGES} páginas."
                )
            parts = [page.extract_text() or "" for page in reader.pages]
        except ExtractionError:
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise ExtractionError("O PDF está corrompido ou não pôde ser lido.") from exc
        text = _normalize_text("\n\n".join(parts))
        if not text:
            raise ExtractionError(
                "O PDF não contém texto pesquisável; converta a página em PNG ou JPEG para OCR."
            )
        return ExtractionResult(
            text=text,
            method=ExtractionMethod.PDF_TEXT,
            page_count=len(reader.pages),
        )

    def _extract_image(self, content: bytes) -> ExtractionResult:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as opened:
                    if opened.width * opened.height > int(settings.SC04_MAX_IMAGE_PIXELS):
                        raise ExtractionError("A imagem excede o limite seguro de pixels.")
                    opened.load()
                    image = ImageOps.exif_transpose(opened).convert("RGB")
            text = pytesseract.image_to_string(
                image,
                lang=str(settings.SC04_TESSERACT_LANGUAGE),
                timeout=int(settings.SC04_OCR_TIMEOUT_SECONDS),
            )
        except ExtractionError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            RuntimeError,
            pytesseract.TesseractError,
            pytesseract.TesseractNotFoundError,
        ) as exc:
            raise ExtractionError("A imagem não pôde ser processada pelo OCR.") from exc
        normalized = _normalize_text(text)
        if not normalized:
            raise ExtractionError("O OCR não encontrou texto suficiente na imagem.")
        return ExtractionResult(
            text=normalized,
            method=ExtractionMethod.OCR,
            page_count=1,
        )


def _normalize_text(text: str) -> str:
    normalized = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized[: int(settings.SC04_MAX_EXTRACTED_CHARS)]
