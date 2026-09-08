from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_trace_context: ContextVar[dict[str, object] | None] = ContextVar(
    "sheepcontabil_trace_context",
    default=None,
)


def get_trace_context() -> dict[str, object]:
    """Return an isolated snapshot of the current structured-log context."""

    return dict(_trace_context.get() or {})


def bind_trace_context(**values: object) -> Token[dict[str, object] | None]:
    """Bind values for the current execution context and return its reset token."""

    context = get_trace_context()
    for field, value in values.items():
        if value is None:
            context.pop(field, None)
        else:
            context[field] = value
    return _trace_context.set(context)


def reset_trace_context(token: Token[dict[str, object] | None]) -> None:
    """Restore the exact context that existed before a bind operation."""

    _trace_context.reset(token)


@contextmanager
def trace_context(**values: object) -> Iterator[dict[str, object]]:
    """Temporarily bind structured-log fields without leaking them to later work."""

    token = bind_trace_context(**values)
    try:
        yield get_trace_context()
    finally:
        reset_trace_context(token)
