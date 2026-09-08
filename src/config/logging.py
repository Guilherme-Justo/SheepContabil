import json
import logging
import os
from datetime import UTC, datetime

from config.trace_context import get_trace_context

TRACE_FIELDS = (
    "request_id",
    "run_id",
    "event_id",
    "sequence",
    "module_code",
    "task_id",
    "pulse_id",
    "user_id",
    "client_id",
    "entity_type",
    "entity_id",
    "step",
    "attempt",
    "event_type",
    "source",
    "status",
    "from_status",
    "to_status",
    "outcome",
    "duration_ms",
    "error_code",
    "deduplication_key",
)

DEPLOYMENT_FIELDS = {
    "service": ("APP_SERVICE_NAME", "RAILWAY_SERVICE_NAME", "SERVICE_NAME"),
    "environment": ("APP_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "ENVIRONMENT"),
    "release": ("APP_RELEASE", "RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT_SHA"),
}


class TraceContextFilter(logging.Filter):
    """Copy ContextVar values onto records before they leave the current context."""

    def filter(self, record: logging.LogRecord) -> bool:
        for field, value in get_trace_context().items():
            if value is not None and not hasattr(record, field):
                setattr(record, field, value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = get_trace_context()
        for field in TRACE_FIELDS:
            value = getattr(record, field, context.get(field))
            if value is not None:
                payload[field] = value
        for field, environment_keys in DEPLOYMENT_FIELDS.items():
            value = getattr(record, field, context.get(field))
            if value is None:
                value = next((os.getenv(key) for key in environment_keys if os.getenv(key)), None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)
