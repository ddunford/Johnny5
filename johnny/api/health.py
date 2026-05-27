"""Health endpoint — readiness across every upstream dependency.

`GET /api/health` reports Postgres, Redis, Groq, the local Ollama LLM,
embeddings, and YOLO. Postgres and Redis are *critical* (Johnny can't run without
them) → the endpoint returns 503 if either is down. The inference upstreams are
*degradable*: their failure is surfaced as ``degraded`` (still HTTP 200), because
Johnny is designed to keep running "tired" on whatever remains.

Detail strings are deliberately terse (status codes, exception class names) so
the endpoint never leaks internal hosts, URLs, or credentials.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter

import httpx
from fastapi import APIRouter, Response
from pydantic import BaseModel

from foundation import db, redis_client
from foundation.config import Settings, get_settings

health_router = APIRouter(prefix="/api", tags=["health"])

# Without these Johnny cannot operate; their failure makes the service unready.
_CRITICAL = frozenset({"postgres", "redis"})
_CHECK_TIMEOUT = 5.0


class ComponentHealth(BaseModel):
    status: str  # "ok" | "error"
    latency_ms: int | None = None
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded" | "unhealthy"
    service: str
    environment: str
    timestamp: str
    dependencies: dict[str, ComponentHealth]


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)


async def _guard(coro: object) -> ComponentHealth:
    """Run a check with a timeout, turning any failure into an 'error' status."""
    start = perf_counter()
    try:
        return await asyncio.wait_for(coro, timeout=_CHECK_TIMEOUT)  # type: ignore[arg-type]
    except TimeoutError:
        return ComponentHealth(status="error", latency_ms=_elapsed_ms(start), detail="timeout")
    except Exception as exc:
        return ComponentHealth(
            status="error", latency_ms=_elapsed_ms(start), detail=type(exc).__name__
        )


async def _check_postgres() -> ComponentHealth:
    start = perf_counter()
    await db.ping()
    return ComponentHealth(status="ok", latency_ms=_elapsed_ms(start))


async def _check_redis() -> ComponentHealth:
    start = perf_counter()
    await redis_client.ping()
    return ComponentHealth(status="ok", latency_ms=_elapsed_ms(start))


async def _check_http(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None
) -> ComponentHealth:
    start = perf_counter()
    resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        return ComponentHealth(
            status="error", latency_ms=_elapsed_ms(start), detail=f"HTTP {resp.status_code}"
        )
    return ComponentHealth(status="ok", latency_ms=_elapsed_ms(start))


async def _check_groq(client: httpx.AsyncClient, settings: Settings) -> ComponentHealth:
    if not settings.groq_api_key:
        return ComponentHealth(status="error", detail="unconfigured")
    return await _check_http(
        client,
        f"{settings.groq_base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
    )


async def check_health(settings: Settings) -> HealthResponse:
    """Probe every dependency concurrently and assemble the report."""
    async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
        names = ["postgres", "redis", "groq", "ollama", "embeddings", "yolo"]
        results = await asyncio.gather(
            _guard(_check_postgres()),
            _guard(_check_redis()),
            _guard(_check_groq(client, settings)),
            _guard(_check_http(client, f"{settings.local_llm_base_url.rstrip('/')}/v1/models")),
            _guard(_check_http(client, f"{settings.embed_base_url.rstrip('/')}/health")),
            _guard(_check_http(client, f"{settings.yolo_base_url.rstrip('/')}/health")),
        )

    dependencies = dict(zip(names, results, strict=True))
    critical_down = any(
        comp.status != "ok" for name, comp in dependencies.items() if name in _CRITICAL
    )
    any_down = any(comp.status != "ok" for comp in dependencies.values())
    overall = "unhealthy" if critical_down else ("degraded" if any_down else "ok")

    return HealthResponse(
        status=overall,
        service=settings.service_name,
        environment=settings.app_env,
        timestamp=datetime.now(UTC).isoformat(),
        dependencies=dependencies,
    )


@health_router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    """Readiness across all dependencies. 503 only when a critical store is down."""
    report = await check_health(get_settings())
    response.status_code = 503 if report.status == "unhealthy" else 200
    return report
