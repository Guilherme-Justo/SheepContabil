from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from config.trace_context import get_trace_context
from core.automations.models import (
    AutomationRun,
    AutomationRunEvent,
    RunEventSource,
    RunEventType,
    RunStatus,
)
from core.identity.models import User

logger = logging.getLogger(__name__)

_MAX_DETAILS_BYTES = 4_096
_MAX_DETAILS_ITEMS = 40
_MAX_DETAILS_DEPTH = 4
_MAX_DETAIL_KEY_LENGTH = 80
_MAX_DETAIL_STRING_LENGTH = 500

_TOKEN_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
_UUID_PATTERN = re.compile(
    r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"
)
_SENSITIVE_KEY_PATTERNS = (
    re.compile(
        r"(?:^|_)(?:password|passwd|passphrase|secret|token|authorization|cookie|session|"
        r"credential|credentials)(?:_|$)"
    ),
    re.compile(r"(?:^|_)(?:api|access|private|secret|signing|encryption)_key(?:_|$)"),
    re.compile(r"(?:^|_)(?:database|redis|broker|celery|smtp|s3)_url(?:_|$)"),
    re.compile(r"(?:^|_)(?:url|uri|dsn|endpoint)(?:_|$)"),
    re.compile(r"(?:^|_)(?:email|phone|recipient|cpf|cnpj|client_document|document_number)(?:_|$)"),
    re.compile(r"(?:^|_)(?:body|payload|content|raw|answers|extracted_text|storage_key)(?:_|$)"),
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:bearer|basic)\s+[a-zA-Z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[a-zA-Z0-9_-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"),
    re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    re.compile(r"\b[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
    re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)"),
    re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)"),
    re.compile(r"(?<!\d)(?:\d{11}|\d{14})(?!\d)"),
)


class TraceabilityConflict(ValidationError):
    """A deduplication key was reused for different immutable evidence."""


class UnsafeTraceabilityData(ValidationError):
    """An event attempted to persist secrets, PII, or unbounded details."""


def record_run_event(
    *,
    run: AutomationRun | UUID | str,
    event_type: str,
    source: str,
    deduplication_key: str,
    actor: User | None = None,
    previous_status: str = "",
    current_status: str = "",
    request_id: str = "",
    task_id: UUID | str | None = None,
    pulse_id: UUID | str | None = None,
    entity_type: str = "",
    entity_id: str = "",
    step: str = "",
    attempt: int | None = None,
    outcome: str = "",
    error_code: str = "",
    duration_ms: int | None = None,
    message: str = "",
    details: Mapping[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> tuple[AutomationRunEvent, bool]:
    """Record one immutable event, returning ``(event, created)``.

    The run row serializes sequence allocation. A repeated deduplication key is
    idempotent only when every supplied semantic field has the same content.
    """

    context = get_trace_context()
    if not request_id:
        request_id = str(context.get("request_id") or "")
    if task_id is None:
        task_id = _context_identifier(context, "task_id")
    if pulse_id is None:
        pulse_id = _context_identifier(context, "pulse_id")

    run_id = _run_id(run)
    clean_event_type = _choice(event_type, RunEventType.values, field="event_type")
    clean_source = _choice(source, RunEventSource.values, field="source")
    clean_previous_status = _optional_choice(
        previous_status,
        RunStatus.values,
        field="previous_status",
    )
    clean_current_status = _optional_choice(
        current_status,
        RunStatus.values,
        field="current_status",
    )
    clean_deduplication_key = _safe_token(
        deduplication_key,
        field="deduplication_key",
        max_length=180,
        required=True,
    )
    clean_request_id = _safe_token(
        request_id,
        field="request_id",
        max_length=100,
    )
    clean_task_id = _optional_uuid(task_id, field="task_id")
    clean_pulse_id = _optional_uuid(pulse_id, field="pulse_id")
    clean_entity_type = _safe_token(
        entity_type,
        field="entity_type",
        max_length=80,
    )
    clean_entity_id = _safe_token(
        entity_id,
        field="entity_id",
        max_length=180,
    )
    clean_step = _safe_token(step, field="step", max_length=80)
    clean_attempt = _optional_positive_int(attempt, field="attempt", maximum=65_535)
    clean_outcome = _safe_token(outcome, field="outcome", max_length=64)
    clean_error_code = _safe_token(error_code, field="error_code", max_length=80)
    clean_duration_ms = _optional_positive_int(duration_ms, field="duration_ms")
    clean_message = _safe_message(message)
    clean_details = _minimize_details(details)
    clean_occurred_at = _occurred_at(occurred_at)

    if actor is not None and actor.pk is None:
        raise UnsafeTraceabilityData("O ator do evento precisa estar persistido.")

    with transaction.atomic():
        try:
            locked_run = AutomationRun.objects.select_for_update().get(pk=run_id)
        except (AutomationRun.DoesNotExist, ValueError) as exc:
            raise UnsafeTraceabilityData("A execução informada não existe.") from exc

        payload: dict[str, Any] = {
            "event_type": clean_event_type,
            "source": clean_source,
            "actor_id": actor.pk if actor is not None else None,
            "previous_status": clean_previous_status,
            "current_status": clean_current_status,
            "request_id": clean_request_id,
            "task_id": clean_task_id,
            "pulse_id": clean_pulse_id,
            "entity_type": clean_entity_type,
            "entity_id": clean_entity_id,
            "step": clean_step,
            "attempt": clean_attempt,
            "outcome": clean_outcome,
            "error_code": clean_error_code,
            "duration_ms": clean_duration_ms,
            "deduplication_key": clean_deduplication_key,
            "message": clean_message,
            "details": clean_details,
        }
        existing = AutomationRunEvent.objects.filter(
            run=locked_run,
            deduplication_key=clean_deduplication_key,
        ).first()
        if existing is not None:
            _require_same_content(existing, payload, occurred_at=clean_occurred_at)
            return existing, False

        latest_sequence = locked_run.events.aggregate(maximum=models.Max("sequence"))["maximum"]
        event = AutomationRunEvent.objects.create(
            run=locked_run,
            sequence=(latest_sequence or 0) + 1,
            occurred_at=clean_occurred_at or timezone.now(),
            **payload,
        )
        transaction.on_commit(
            partial(
                _log_recorded_event,
                event_id=str(event.id),
                run_id=str(locked_run.id),
                module_code=locked_run.module_id,
                sequence=event.sequence,
                event_type=event.event_type,
                source=event.source,
                request_id=event.request_id,
                task_id=str(event.task_id) if event.task_id else "",
                pulse_id=str(event.pulse_id) if event.pulse_id else "",
                user_id=event.actor_id,
                previous_status=event.previous_status,
                current_status=event.current_status,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                step=event.step,
                attempt=event.attempt,
                outcome=event.outcome,
                error_code=event.error_code,
                duration_ms=event.duration_ms,
                deduplication_key=event.deduplication_key,
            )
        )
        return event, True


def _run_id(run: AutomationRun | UUID | str) -> UUID:
    raw_id = run.pk if isinstance(run, AutomationRun) else run
    if raw_id is None:
        raise UnsafeTraceabilityData("A execução precisa estar persistida.")
    try:
        return UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise UnsafeTraceabilityData("O identificador da execução é inválido.") from exc


def _context_identifier(context: Mapping[str, object], field: str) -> UUID | str | None:
    value = context.get(field)
    if value is None:
        return None
    return str(value)


def _choice(value: str, allowed: list[str], *, field: str) -> str:
    normalized = str(value).strip()
    if normalized not in allowed:
        raise UnsafeTraceabilityData(f"{field} não possui um valor permitido.")
    return normalized


def _optional_choice(value: str, allowed: list[str], *, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        return ""
    return _choice(normalized, allowed, field=field)


def _safe_token(
    value: object,
    *,
    field: str,
    max_length: int,
    required: bool = False,
) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise UnsafeTraceabilityData(f"{field} é obrigatório.")
    if not normalized:
        return ""
    if len(normalized) > max_length or not _TOKEN_PATTERN.fullmatch(normalized):
        raise UnsafeTraceabilityData(f"{field} deve ser um identificador opaco e limitado.")
    _reject_sensitive_value(_UUID_PATTERN.sub("", normalized), field=field)
    return normalized


def _optional_uuid(value: UUID | str | None, *, field: str) -> UUID | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise UnsafeTraceabilityData(f"{field} deve ser um UUID válido.") from exc


def _optional_positive_int(
    value: int | None,
    *,
    field: str,
    maximum: int | None = None,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UnsafeTraceabilityData(f"{field} deve ser um inteiro não negativo.")
    if maximum is not None and value > maximum:
        raise UnsafeTraceabilityData(f"{field} excede o limite permitido.")
    return value


def _safe_message(value: object) -> str:
    message = str(value or "").strip()
    if len(message) > 500:
        raise UnsafeTraceabilityData("A mensagem segura excede o limite permitido.")
    _reject_sensitive_value(message, field="message")
    return message


def _occurred_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise UnsafeTraceabilityData("occurred_at deve conter data e fuso horário.")
    return value


def _minimize_details(details: Mapping[str, object] | None) -> dict[str, object]:
    if details is None:
        return {}
    if not isinstance(details, Mapping):
        raise UnsafeTraceabilityData("details deve ser um objeto JSON.")

    item_count = [0]
    minimized = _minimize_value(details, path="details", depth=0, item_count=item_count)
    if not isinstance(minimized, dict):
        raise UnsafeTraceabilityData("details deve ser um objeto JSON.")
    encoded = json.dumps(minimized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_DETAILS_BYTES:
        raise UnsafeTraceabilityData("details excede o limite de 4096 bytes.")
    return minimized


def _minimize_value(
    value: object,
    *,
    path: str,
    depth: int,
    item_count: list[int],
) -> object:
    if depth > _MAX_DETAILS_DEPTH:
        raise UnsafeTraceabilityData("details excede a profundidade permitida.")
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise UnsafeTraceabilityData("Todas as chaves de details devem ser texto.")
            key = raw_key.strip()
            if not key or len(key) > _MAX_DETAIL_KEY_LENGTH:
                raise UnsafeTraceabilityData("Uma chave de details é vazia ou longa demais.")
            _reject_sensitive_key(key)
            item_count[0] += 1
            _check_item_count(item_count[0])
            result[key] = _minimize_value(
                raw_value,
                path=f"{path}.{key}",
                depth=depth + 1,
                item_count=item_count,
            )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_DETAILS_ITEMS:
            raise UnsafeTraceabilityData("Uma lista de details excede o limite permitido.")
        result_list: list[object] = []
        for index, item in enumerate(value):
            item_count[0] += 1
            _check_item_count(item_count[0])
            result_list.append(
                _minimize_value(
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    item_count=item_count,
                )
            )
        return result_list
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsafeTraceabilityData(f"{path} contém um número inválido.")
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if len(normalized) > _MAX_DETAIL_STRING_LENGTH:
            raise UnsafeTraceabilityData(f"{path} excede o limite de texto permitido.")
        _reject_sensitive_value(normalized, field=path)
        return normalized
    raise UnsafeTraceabilityData(f"{path} contém um tipo que não pertence ao JSON minimizado.")


def _check_item_count(value: int) -> None:
    if value > _MAX_DETAILS_ITEMS:
        raise UnsafeTraceabilityData("details possui itens demais.")


def _reject_sensitive_key(key: str) -> None:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if any(pattern.search(normalized) for pattern in _SENSITIVE_KEY_PATTERNS):
        raise UnsafeTraceabilityData(f"details contém a chave sensível '{key}'.")


def _reject_sensitive_value(value: str, *, field: str) -> None:
    if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS):
        raise UnsafeTraceabilityData(f"{field} contém conteúdo sensível.")


def _require_same_content(
    event: AutomationRunEvent,
    payload: Mapping[str, object],
    *,
    occurred_at: datetime | None,
) -> None:
    retry_context_fields = {"request_id", "pulse_id", "duration_ms"}
    mismatches = [
        field
        for field, expected in payload.items()
        if field not in retry_context_fields and getattr(event, field) != expected
    ]
    if occurred_at is not None and event.occurred_at != occurred_at:
        mismatches.append("occurred_at")
    if mismatches:
        raise TraceabilityConflict(
            "A chave de deduplicação já identifica um evento com conteúdo diferente."
        )


def _log_recorded_event(
    *,
    event_id: str,
    run_id: str,
    module_code: str,
    sequence: int,
    event_type: str,
    source: str,
    request_id: str,
    task_id: str,
    pulse_id: str,
    user_id: int | None,
    previous_status: str,
    current_status: str,
    entity_type: str,
    entity_id: str,
    step: str,
    attempt: int | None,
    outcome: str,
    error_code: str,
    duration_ms: int | None,
    deduplication_key: str,
) -> None:
    fields: dict[str, object | None] = {
        "event_id": event_id,
        "run_id": run_id,
        "module_code": module_code,
        "sequence": sequence,
        "event_type": event_type,
        "source": source,
        "request_id": request_id,
        "task_id": task_id,
        "pulse_id": pulse_id,
        "user_id": user_id,
        "from_status": previous_status,
        "to_status": current_status,
        "status": current_status,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "step": step,
        "attempt": attempt,
        "outcome": outcome,
        "error_code": error_code,
        "duration_ms": duration_ms,
        "deduplication_key": deduplication_key,
    }
    logger.info(
        "automation_run_event_recorded",
        extra={key: value for key, value in fields.items() if value not in {None, ""}},
    )
