from __future__ import annotations

import json
import logging
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory

from config.logging import JsonFormatter
from config.middleware import TraceContextMiddleware
from config.trace_context import get_trace_context, trace_context
from core.automations.management.commands.dispatch_due_schedules import Command
from core.identity.models import User


def test_trace_context_is_nested_and_restored() -> None:
    assert get_trace_context() == {}
    with trace_context(request_id="outer", source="web"):
        assert get_trace_context() == {"request_id": "outer", "source": "web"}
        with trace_context(task_id="inner", request_id=None):
            assert get_trace_context() == {"source": "web", "task_id": "inner"}
        assert get_trace_context() == {"request_id": "outer", "source": "web"}
    assert get_trace_context() == {}


@pytest.mark.django_db
def test_http_middleware_validates_echoes_and_resets_request_id(administrator: User) -> None:
    seen: list[dict[str, object]] = []

    def endpoint(request: object) -> HttpResponse:
        del request
        seen.append(get_trace_context())
        return HttpResponse("ok")

    middleware = TraceContextMiddleware(endpoint)  # type: ignore[arg-type]
    valid_id = str(uuid4())
    request = RequestFactory().get("/", HTTP_X_REQUEST_ID=valid_id)
    request.user = administrator
    response = middleware(request)

    assert response["X-Request-ID"] == valid_id
    assert seen == [{"request_id": valid_id, "user_id": str(administrator.pk), "source": "web"}]
    assert get_trace_context() == {}

    invalid = RequestFactory().get("/", HTTP_X_REQUEST_ID="não-é-uuid")
    invalid.user = administrator
    generated = middleware(invalid)["X-Request-ID"]
    assert str(UUID(generated)) == generated
    assert generated != valid_id
    assert get_trace_context() == {}


def test_json_formatter_combines_context_record_and_deployment_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_SERVICE_NAME", "worker")
    record = logging.LogRecord(
        name="test.trace",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="evento seguro",
        args=(),
        exc_info=None,
    )
    record.outcome = "succeeded"
    record.event_id = str(uuid4())
    record.sequence = 7
    record.entity_type = "communication_attempt"
    record.entity_id = str(uuid4())

    with trace_context(request_id=str(uuid4()), run_id=str(uuid4()), source="worker"):
        payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "evento seguro"
    assert payload["source"] == "worker"
    assert payload["outcome"] == "succeeded"
    assert payload["service"] == "worker"
    assert payload["event_id"] == record.event_id
    assert payload["sequence"] == 7
    assert payload["entity_type"] == "communication_attempt"
    assert payload["entity_id"] == record.entity_id
    assert "run_id" in payload
    assert "request_id" in payload


def test_scheduler_creates_one_scoped_pulse(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[dict[str, object]] = []
    command = Command()
    monkeypatch.setattr(
        command,
        "_handle_pulse",
        lambda *args, **kwargs: observed.append(get_trace_context()),
    )

    command.handle(force=False)

    assert len(observed) == 1
    assert observed[0]["source"] == "scheduler"
    assert str(UUID(str(observed[0]["pulse_id"]))) == observed[0]["pulse_id"]
    assert get_trace_context() == {}


def test_runtime_configuration_keeps_trace_middleware_and_celery_logging_contract() -> None:
    middleware = list(settings.MIDDLEWARE)
    assert middleware.index("config.middleware.TraceContextMiddleware") > middleware.index(
        "django.contrib.auth.middleware.AuthenticationMiddleware"
    )
    assert settings.CELERY_WORKER_HIJACK_ROOT_LOGGER is False
    assert "trace_context" in settings.LOGGING["handlers"]["console"]["filters"]
