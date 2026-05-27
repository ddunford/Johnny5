"""Live verification against the real inference.lan + Groq endpoints.

This is the global rule-6 gate (TASK-0.15): prove the substrate actually works
end-to-end before Phase 1 builds on it — not against mocks. These tests hit REAL
endpoints, so they are marked ``live`` and deselected unless ``--run-live`` is
passed (CI has no LAN access or Groq key).

    Run:      uv run pytest -m live --run-live
    Prereqs:  .env populated (GROQ_API_KEY); inference.lan reachable on the LAN.

Covers, against live services:
  * real Groq completion (llama-3.3-70b-versatile),
  * real local completion (gemma4:e4b on inference.lan),
  * real 1024-d embedding from the :8002 /embed server,
  * forced-failover smoke: Groq down → the router transparently serves locally
    and the Groq circuit trips on the real failure.

The local legs use gemma4 (not qwen) on purpose — loading qwen3.5-9b can evict
the GPU-resident gemma4.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.llm.base import Message
from brain.llm.call_logger import CallLogRecord
from brain.llm.circuit_breaker import CircuitState
from brain.llm.detection import Detector
from brain.llm.embeddings import Embedder
from brain.llm.providers.groq import GROQ_PROVIDER_NAME, GroqProvider
from brain.llm.providers.ollama import OLLAMA_PROVIDER_NAME, OllamaProvider
from brain.llm.providers.openai_compatible import OpenAICompatibleProvider
from brain.llm.router import LLMRouter
from brain.llm.routing import ModelStep, RoutingConfig
from brain.llm.vision import Vision
from foundation.config import Settings

pytestmark = pytest.mark.live

_PROMPT = "Reply with exactly the word: ALIVE"
_BUS_IMAGE = Path(__file__).parent.parent / "fixtures" / "images" / "bus.jpg"


class _MemoryLogger:
    """In-memory CallLogger so the live router needs no database."""

    def __init__(self) -> None:
        self.records: list[CallLogRecord] = []

    async def record(self, entry: CallLogRecord) -> None:
        self.records.append(entry)


@pytest.fixture
def settings() -> Settings:
    return Settings()


async def test_live_groq_completion(settings: Settings) -> None:
    if not settings.groq_api_key:
        pytest.skip("GROQ_API_KEY not set in .env")
    provider = GroqProvider(settings)
    try:
        completion = await provider.complete(
            [Message(role="user", content=_PROMPT)],
            model=settings.groq_model,
            temperature=0,
            max_tokens=20,
        )
        assert completion.provider == GROQ_PROVIDER_NAME
        assert completion.content.strip(), "Groq returned empty content"
        assert completion.prompt_tokens > 0
        assert completion.completion_tokens > 0
    finally:
        await provider.aclose()


async def test_live_local_gemma4_completion(settings: Settings) -> None:
    provider = OllamaProvider(settings)
    try:
        completion = await provider.complete(
            [Message(role="user", content=_PROMPT)],
            model=settings.local_fast_model,
            temperature=0,
        )
        assert completion.provider == OLLAMA_PROVIDER_NAME
        assert completion.content.strip(), "gemma4 returned empty content"
        # gemma4 returns clean content with no separate reasoning channel.
        assert completion.reasoning is None
    finally:
        await provider.aclose()


async def test_live_embedding_is_1024d(settings: Settings) -> None:
    embedder = Embedder(settings)
    try:
        vector = await embedder.embed_one("Johnny 5 is alive.")
        assert len(vector) == settings.embed_dimensions == 1024
        assert all(isinstance(x, float) for x in vector)
        assert any(x != 0.0 for x in vector), "degenerate (all-zero) embedding"
    finally:
        await embedder.aclose()


async def test_live_forced_failover_groq_down_to_local(settings: Settings) -> None:
    """Point Groq at a dead host → the router fails over to local gemma4, the
    caller gets a usable completion, and the Groq circuit trips on the real
    transport failure (threshold 1)."""
    broken_groq = OpenAICompatibleProvider(
        name=GROQ_PROVIDER_NAME, base_url="http://127.0.0.1:1/v1", timeout=5.0
    )
    local = OllamaProvider(settings)
    logger = _MemoryLogger()
    routing = RoutingConfig(
        roles={
            "smoke": [
                ModelStep(provider=GROQ_PROVIDER_NAME, model=settings.groq_model),
                ModelStep(provider=OLLAMA_PROVIDER_NAME, model=settings.local_fast_model),
            ]
        }
    )
    router = LLMRouter(
        providers={GROQ_PROVIDER_NAME: broken_groq, OLLAMA_PROVIDER_NAME: local},
        routing=routing,
        call_logger=logger,
        failure_threshold=1,
        reset_timeout=60.0,
    )
    try:
        completion = await router.complete(
            "smoke", [Message(role="user", content=_PROMPT)], temperature=0
        )
        assert completion.provider == OLLAMA_PROVIDER_NAME  # transparently served locally
        assert completion.content.strip()
        # The Groq circuit tripped on the real connection failure.
        assert router.circuit_states()[GROQ_PROVIDER_NAME] is CircuitState.OPEN
        statuses = [(r.provider, r.status) for r in logger.records]
        assert (GROQ_PROVIDER_NAME, "error") in statuses
        assert (OLLAMA_PROVIDER_NAME, "ok") in statuses
    finally:
        await router.aclose()


async def test_live_vision_describes_image(settings: Settings) -> None:
    """gemma4:e4b multimodal via the router (vision role) describes a real image.

    Built with an in-memory call logger (not the DB logger) so this live leg
    doesn't open the process-global DB engine on its event loop — which would
    otherwise leave a stale engine for the health leg below.
    """
    router = LLMRouter(
        providers={OLLAMA_PROVIDER_NAME: OllamaProvider(settings)},
        routing=RoutingConfig(
            roles={
                "vision": [
                    ModelStep(provider=OLLAMA_PROVIDER_NAME, model=settings.local_fast_model)
                ]
            }
        ),
        call_logger=_MemoryLogger(),
    )
    vision = Vision(router)
    try:
        description = await vision.describe(
            _BUS_IMAGE.read_bytes(),
            "What is the main subject of this image? Answer in a short phrase.",
            media_type="image/jpeg",
        )
        assert description.strip(), "vision returned an empty description"
    finally:
        await router.aclose()


async def test_live_yolo_detects_objects(settings: Settings) -> None:
    """YOLO11 on :8003 detects the bus + people in the test image."""
    detector = Detector(settings)
    try:
        detections = await detector.detect(_BUS_IMAGE.read_bytes())
        assert len(detections) >= 1
        labels = {d.label for d in detections}
        assert "bus" in labels  # the dominant, high-confidence object
        assert all(0.0 < d.confidence <= 1.0 for d in detections)
    finally:
        await detector.aclose()


def test_live_health_reports_all_six_dependencies_reachable() -> None:
    """Full-stack 'lights on': boot the app and assert GET /api/health reports
    all six dependencies reachable against the real stack.

    Postgres/redis are network-only (compose `internal` network), so this asserts
    for real when run inside the stack: ``./ctl.sh test -m live --run-live``. From
    a host ``uv run`` the stores aren't reachable, so the leg skips with guidance
    rather than reporting a false failure — the off-box inference legs above still
    run on the host.
    """
    from fastapi.testclient import TestClient

    from johnny.main import create_app

    with TestClient(create_app()) as client:
        resp = client.get("/api/health")

    body = resp.json()
    deps = body["dependencies"]
    assert set(deps) == {"postgres", "redis", "groq", "ollama", "embeddings", "yolo"}

    if deps["postgres"]["status"] != "ok" or deps["redis"]["status"] != "ok":
        pytest.skip(
            "compose stack (postgres/redis) not reachable from this runner — "
            "run via `./ctl.sh test -m live --run-live` to verify the full stack"
        )

    assert resp.status_code == 200
    assert body["status"] == "ok"
    for name, comp in deps.items():
        assert comp["status"] == "ok", f"{name} not reachable: {comp.get('detail')}"
