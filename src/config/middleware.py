from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

from django.http import HttpRequest, HttpResponse

from config.trace_context import trace_context


class TraceContextMiddleware:
    """Give every HTTP request a validated correlation ID and scoped actor context."""

    header_name = "X-Request-ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = self._request_id(request.headers.get(self.header_name))
        request.request_id = request_id  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        user_id = str(user.pk) if user is not None and user.is_authenticated else None

        with trace_context(request_id=request_id, user_id=user_id, source="web"):
            response = self.get_response(request)
            response[self.header_name] = request_id
            return response

    @staticmethod
    def _request_id(raw_value: str | None) -> str:
        if raw_value and len(raw_value) <= 64:
            try:
                return str(UUID(raw_value.strip()))
            except (AttributeError, ValueError):
                pass
        return str(uuid4())
