"""Health endpoint.

Phase 0 scaffold ships a liveness check; the readiness probe that reports every
upstream dependency (Postgres, Redis, Groq, local Ollama, embeddings, YOLO) is
built out in the health task once the resource clients exist.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from foundation.config import get_settings

health_router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    timestamp: str


@health_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: the process is up and serving requests."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        environment=settings.app_env,
        timestamp=datetime.now(UTC).isoformat(),
    )
