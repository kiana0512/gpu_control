import contextvars
import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)
FIELDS = (
    "request_id",
    "trace_id",
    "job_id",
    "tenant_id",
    "workflow_key",
    "workflow_version",
    "node_id",
    "prompt_id",
    "attempt",
    "event",
    "duration_ms",
    "error_code",
)
SECRET_PATTERN = re.compile(r"(?i)(authorization|api[_-]?key|password|secret|cookie)")


def bind_context(**values: Any) -> contextvars.Token[dict[str, Any] | None]:
    merged = {**(_context.get() or {}), **values}
    return _context.set(merged)


def reset_context(token: contextvars.Token[dict[str, Any] | None]) -> None:
    _context.reset(token)


def _inject(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    context = _context.get() or {}
    for field in FIELDS:
        event_dict.setdefault(field, context.get(field))
    return event_dict


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if SECRET_PATTERN.search(key):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(service: str, environment: str, level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject,
            _redact,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.CallsiteParameterAdder(
                {structlog.processors.CallsiteParameter.MODULE}
            ),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service, environment=environment)


def logger() -> Any:
    return structlog.get_logger()
