from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, register


@register()
def automation_settings_check(
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
    if not 0 <= int(settings.SC20_MONTHLY_HOUR) <= 23:
        errors.append(
            Error(
                "SC20_MONTHLY_HOUR precisa ficar entre 0 e 23.",
                id="automations.E044",
            )
        )
    visibility_timeout = int(settings.CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS)
    hard_time_limit = int(settings.CELERY_TASK_TIME_LIMIT)
    if visibility_timeout <= hard_time_limit:
        errors.append(
            Error(
                "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS precisa superar o limite rígido da task.",
                id="automations.E045",
            )
        )
    queued_stale_after = int(settings.AUTOMATION_QUEUED_STALE_AFTER_SECONDS)
    if queued_stale_after <= 0:
        errors.append(
            Error(
                "AUTOMATION_QUEUED_STALE_AFTER_SECONDS precisa ser maior que zero.",
                id="automations.E046",
            )
        )
    running_stale_after = int(settings.AUTOMATION_RUNNING_STALE_AFTER_SECONDS)
    if running_stale_after <= visibility_timeout:
        errors.append(
            Error(
                "AUTOMATION_RUNNING_STALE_AFTER_SECONDS precisa superar o visibility timeout.",
                id="automations.E047",
            )
        )
    if int(settings.AUTOMATION_RECONCILIATION_MAX_ATTEMPTS) < 1:
        errors.append(
            Error(
                "AUTOMATION_RECONCILIATION_MAX_ATTEMPTS precisa ser ao menos 1.",
                id="automations.E048",
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
