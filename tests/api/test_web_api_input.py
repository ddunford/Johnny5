"""TC-5a.2 — ``POST /api/v1/input`` is the "talk to him" path (and only write).

It pushes the message onto the same Redis ``InputQueue`` Sensorium drains (it does
NOT bypass the cycle, FC-1/FC-9): a tick later the message is a high-salience
``input`` percept. Guards: blank → 422, oversized → 413, full queue → 429 — so a
runaway client can't grow the Redis list unboundedly.

The round-trip test runs the app **in the test's own event loop** (ASGITransport +
the app's lifespan context) so the HTTP push and the Sensorium drain share one loop
and one loop-local engine — the cleanest way to prove the message a human POSTs
becomes a percept Johnny perceives. DB+Redis backed → in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers.web_api import build_api_app

from brain.agents.sensorium import MODALITY_TEXT, Sensorium


@pytest.mark.asyncio
async def test_input_enqueues_then_sensorium_drains_it_to_a_percept(
    _migrated_test_db: None,
) -> None:
    """POST grows the queue depth; a Sensorium tick drains the same queue into input percepts."""
    app = build_api_app(ws_token="")  # dev-open: this test is about the round-trip, not the gate
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            first = await client.post("/api/v1/input", json={"text": "Hello Johnny"})
            second = await client.post("/api/v1/input", json={"text": "Are you there?"})

        # 202 Accepted (not a synchronous reply) + the queue depth grows per push.
        assert first.status_code == 202
        assert first.json() == {"accepted": True, "queue_depth": 1}
        assert second.json() == {"accepted": True, "queue_depth": 2}

        # The SAME queue Sensorium drains: a tick turns the messages into ``input``
        # percepts (FIFO), draining the queue — proof the push reached the cycle.
        sensorium = Sensorium(input_queue=runtime.input_queue)
        items = await sensorium.perceive()
        inputs = [item for item in items if item.kind == "input"]
        assert [item.content for item in inputs] == ["Hello Johnny", "Are you there?"]
        assert all(item.source == "web" for item in inputs)
        assert all(item.metadata.get("modality") == MODALITY_TEXT for item in inputs)
        # Each normalised input persisted a percept row (provenance id present).
        assert all(item.metadata.get("percept_id") is not None for item in inputs)
        # The queue is now empty — Sensorium consumed exactly what the API enqueued.
        assert await runtime.input_queue.depth() == 0


def test_blank_input_is_rejected_422(_migrated_test_db: None) -> None:
    """Whitespace-only and missing text → 422 (Pydantic), nothing enqueued."""
    app = build_api_app(ws_token="")
    with TestClient(app) as client:
        whitespace = client.post("/api/v1/input", json={"text": "   "})
        missing = client.post("/api/v1/input", json={})
    assert whitespace.status_code == 422
    assert missing.status_code == 422


def test_oversized_input_is_rejected_413(_migrated_test_db: None) -> None:
    """Text longer than ``web_input_max_chars`` → 413, not enqueued."""
    app = build_api_app(ws_token="", settings_overrides={"web_input_max_chars": 10})
    with TestClient(app) as client:
        resp = client.post("/api/v1/input", json={"text": "x" * 11})
        ok = client.post("/api/v1/input", json={"text": "x" * 10})
    assert resp.status_code == 413
    assert ok.status_code == 202  # exactly at the bound is allowed


def test_full_queue_is_rejected_429(_migrated_test_db: None) -> None:
    """When the queue already holds the cap, POST → 429 and does not enqueue."""

    async def fill(runtime: object) -> None:
        # Pre-fill to the (lowered) cap so the next push would exceed it.
        for i in range(2):
            await runtime.input_queue.push(f"backlog {i}", source="web")  # type: ignore[attr-defined]

    app = build_api_app(ws_token="", seed=fill, settings_overrides={"web_input_max_queue_depth": 2})
    with TestClient(app) as client:
        resp = client.post("/api/v1/input", json={"text": "one too many"})
    assert resp.status_code == 429
