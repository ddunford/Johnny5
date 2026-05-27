"""FastAPI application factory and process lifespan.

The cognitive loop runs headless; the HTTP/WebSocket surfaces are consumers that
emit regardless of whether anything is attached (FC-8). This module builds the
app, configures logging, and owns the startup/shutdown lifespan where shared
resources (DB engine, Redis, the cognitive cycle) are opened and closed as later
phases wire them in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from brain.runtime import build_runtime
from foundation.config import Settings, get_settings
from foundation.db import dispose_engine
from foundation.observability import (
    CORRELATION_ID_HEADER,
    bind_correlation_id,
    capture_exception,
    clear_log_context,
    configure_logging,
    configure_sentry,
    get_logger,
    new_correlation_id,
)
from foundation.redis_client import close_redis
from johnny.api.health import health_router
from johnny.api.v1.router import v1_router
from johnny.api.ws import ws_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared resources on startup, release them on shutdown.

    Connections are established lazily on first use, so a missing dependency
    leaves Johnny running and reported unhealthy rather than crashing the boot
    (graceful degradation). Shutdown releases the engine and Redis client.
    """
    settings: Settings = app.state.settings
    log = get_logger("johnny.lifespan")
    log.info("startup.begin", service=settings.service_name, environment=settings.app_env)

    # The Mind runs headless (FC-8): the cognitive runtime owns the heartbeat and
    # the workspace bus, started here and torn down on shutdown. The HTTP/WS layer
    # is a consumer of the workspace it exposes, not the source of truth.
    runtime = build_runtime(settings)
    app.state.runtime = runtime
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()
        await dispose_engine()
        await close_redis()
        log.info("shutdown.complete", service=settings.service_name)


async def _correlation_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach a correlation id to every request, log, and unhandled error.

    Reads ``X-Request-ID`` if the caller supplied one, else mints one; binds it
    into the log context and Sentry; echoes it back on the response. Truly
    unhandled errors are logged with the id, captured to Sentry, and returned as
    a generic JSON 500 that leaks no internals but lets the user quote the id.
    """
    correlation_id = request.headers.get(CORRELATION_ID_HEADER) or new_correlation_id()
    request.state.correlation_id = correlation_id
    clear_log_context()
    bind_correlation_id(correlation_id)
    log = get_logger("johnny.request")
    try:
        response = await call_next(request)
    except Exception as exc:  # only truly unhandled (500-class) errors reach here
        log.exception("request.unhandled_error", path=request.url.path, method=request.method)
        capture_exception(exc)
        response = JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "correlation_id": correlation_id},
        )
    finally:
        clear_log_context()
    response.headers[CORRELATION_ID_HEADER] = correlation_id
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings)
    configure_sentry(settings)

    app = FastAPI(
        title="Johnny 5",
        version="0.0.0",
        # No public OpenAPI docs in production: the surface sits behind a
        # single-token / basic-auth gate and ships no user system.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.middleware("http")(_correlation_id_middleware)

    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(ws_router)

    return app


app = create_app()
