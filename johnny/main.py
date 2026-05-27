"""FastAPI application factory and process lifespan.

The cognitive loop runs headless; the HTTP/WebSocket surfaces are consumers that
emit regardless of whether anything is attached (FC-8). This module builds the
app, configures logging, and owns the startup/shutdown lifespan where shared
resources (DB engine, Redis, the cognitive cycle) are opened and closed as later
phases wire them in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from foundation.config import Settings, get_settings
from foundation.observability import configure_logging, get_logger
from johnny.api.health import health_router
from johnny.api.v1.router import v1_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared resources on startup, release them on shutdown."""
    settings: Settings = app.state.settings
    log = get_logger("johnny.lifespan")
    log.info("startup.begin", service=settings.service_name, environment=settings.app_env)

    # Resource wiring (DB engine, Redis client, cognitive cycle) is attached to
    # app.state by later tasks. The lifespan seam is fixed here so they slot in.
    yield

    log.info("shutdown.complete", service=settings.service_name)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings)

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

    app.include_router(health_router)
    app.include_router(v1_router)

    return app


app = create_app()
