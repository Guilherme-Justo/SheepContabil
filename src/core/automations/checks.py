from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, register


@register()
def sc04_settings_check(
    app_configs: object | None,
    **kwargs: Any,
) -> list[CheckMessage]:
    del app_configs, kwargs
    errors: list[CheckMessage] = []
    positive_settings = (
        "SC04_MAX_UPLOAD_BYTES",
        "SC04_MAX_EXTRACTED_CHARS",
        "SC04_MAX_PDF_PAGES",
        "SC04_MAX_IMAGE_PIXELS",
        "SC04_OCR_TIMEOUT_SECONDS",
        "SC04_OPENAI_TIMEOUT_SECONDS",
    )
    for name in positive_settings:
        if int(getattr(settings, name)) <= 0:
            errors.append(
                Error(
                    f"{name} precisa ser maior que zero.",
                    id="automations.E040",
                )
            )
    threshold = float(settings.SC04_AUTO_ROUTE_THRESHOLD)
    if not 0 <= threshold <= 1:
        errors.append(
            Error(
                "SC04_AUTO_ROUTE_THRESHOLD precisa ficar entre 0 e 1.",
                id="automations.E041",
            )
        )
    if not 0 <= int(settings.SC04_DAILY_HOUR) <= 23:
        errors.append(
            Error(
                "SC04_DAILY_HOUR precisa ficar entre 0 e 23.",
                id="automations.E042",
            )
        )
    if str(settings.S3_ADDRESSING_STYLE) not in {"auto", "path", "virtual"}:
        errors.append(
            Error(
                "S3_ADDRESSING_STYLE precisa ser auto, path ou virtual.",
                id="automations.E043",
            )
        )
    return errors
