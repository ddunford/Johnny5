"""Structured logging and error tracking.

This module owns log configuration so every package emits the same structured
JSON. Correlation-id propagation and Sentry wiring are layered on here too (see
the request middleware in `johnny.main`). Logs must never carry secrets — only
variable *names* and non-sensitive values.
"""

from __future__ import annotations

import logging
import sys

import structlog

from foundation.config import Settings


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
