"""Structured logging and error tracking.

This module owns log configuration so every package emits the same structured
JSON. Correlation-id propagation and Sentry wiring are layered on here too (see
the request middleware in `johnny.main`). Logs must never carry secrets — only
variable *names* and non-sensitive values.
"""

from __future__ import annotations

import logging
import sys
import uuid

import sentry_sdk
import structlog
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from foundation.config import Settings

# Header carrying the correlation id in and out of the service.
CORRELATION_ID_HEADER = "X-Request-ID"
CORRELATION_ID_KEY = "correlation_id"

_sentry_enabled = False


def configure_logging(settings: Settings) -> None:
    """Configure stdlib + structlog for structured JSON output.

    In development we render human-friendly console logs; in production we emit
    one JSON object per line so the host's log shipper can parse them.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, etc.) through the same handler
    # so the whole process speaks one log format.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)


def configure_sentry(settings: Settings) -> bool:
    """Initialise Sentry error tracking if a DSN is configured.

    Returns True when Sentry is active. Errors only (no perf tracing) and no PII
    by default — Johnny's inner content must not leak to a third party.
    """
    global _sentry_enabled
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    _sentry_enabled = True
    return True


def sentry_enabled() -> bool:
    return _sentry_enabled


def capture_exception(exc: BaseException) -> None:
    """Send an exception to Sentry if active; a no-op otherwise."""
    if _sentry_enabled:
        sentry_sdk.capture_exception(exc)


def new_correlation_id() -> str:
    """Generate a fresh correlation id."""
    return uuid.uuid4().hex


def bind_correlation_id(correlation_id: str) -> None:
    """Bind the correlation id into the log context and (if active) Sentry."""
    structlog.contextvars.bind_contextvars(**{CORRELATION_ID_KEY: correlation_id})
    if _sentry_enabled:
        sentry_sdk.set_tag(CORRELATION_ID_KEY, correlation_id)


def clear_log_context() -> None:
    """Clear per-request log context (call at the end of each request)."""
    structlog.contextvars.clear_contextvars()
