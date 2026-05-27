"""TC-2.7 — the live consciousness stream over ``/ws/consciousness``.

The WebSocket is a *consumer* of the headless workspace bus (FC-8): a fresh client
gets a short backfill of recent thoughts, then live ``thought`` events as they are
broadcast, on a stable JSON schema. This drives the real ``johnny.api.ws`` handler
through Starlette's ``TestClient.websocket_connect`` and asserts:

* backfill — a recent thought is replayed on connect;
* live — a thought broadcast after connect arrives in real time;
* reconnect — a new client resumes cleanly and picks up subsequent thoughts.

Cross-loop note (the Phase-0/1 gotcha): ``TestClient`` runs the handler in its own
event loop. So the app builds its workspace + a NullPool engine **inside the
lifespan** (the portal loop), and the test publishes from its own thread via a
**sync** Redis client — Redis pub/sub bridges the two loops through the server, no
shared connection is reused across loops. DB+Redis backed → run in-network via
``./ctl.sh test``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import redis as sync_redis
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from redis.asyncio import from_url as aio_from_url

from brain.workspace import Workspace, WorkspaceEvent
from foundation.config import get_settings
from johnny.api.ws import ws_router

_CHANNEL = "johnny:test:consciousness:bus"
_CONTENTS_KEY = "johnny:test:consciousness:contents"
_PUBLISH_DEADLINE_S = 5.0


def _build_app(preseed: Sequence[str] = (), *, ws_token: str = "") -> FastAPI:
    """A minimal app exposing the real ``/ws/consciousness`` over a clean workspace.

    The lifespan runs in the TestClient's portal loop, so the NullPool engine and
    the async Redis client are created (and disposed) there — never reused across
    loops. ``preseed`` thoughts are broadcast (persisted) before serving, to drive
    the backfill path deterministically. ``ws_token`` sets the interim shared-token
    gate (``""`` disables it — the default for the stream tests).
    """
    settings = get_settings().model_copy(update={"ws_token": ws_token})

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        install_fresh_global_engine()
        await truncate_tables(("workspace_event",))
        redis = aio_from_url(settings.redis_url, decode_responses=True)
        workspace = Workspace(redis=redis, channel=_CHANNEL, contents_key=_CONTENTS_KEY)
        for text in preseed:
            await workspace.broadcast(
                WorkspaceEvent(module="narrator", type="thought", payload={"text": text})
            )
        app.state.runtime = SimpleNamespace(workspace=workspace)
        try:
            yield
        finally:
            await redis.aclose()
            await dispose_global_engine()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings  # the WS auth gate reads app.state.settings
    app.include_router(ws_router)
    return app


class _ThoughtPublisher:
    """Publishes a thought event to the bus repeatedly from a background thread,
    so the subscriber (whose subscribe completes a beat after connect) can't miss
    it — closing the publish/subscribe race without a flaky fixed sleep."""

    def __init__(self, text: str, *, event_id: int) -> None:
        self._event = WorkspaceEvent(
            id=event_id,
            ts=datetime.now(UTC),
            module="narrator",
            type="thought",
            payload={"text": text},
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _ThoughtPublisher:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        client = sync_redis.from_url(get_settings().redis_url)
        message = self._event.model_dump_json()
        deadline = time.monotonic() + _PUBLISH_DEADLINE_S
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                client.publish(_CHANNEL, message)
                time.sleep(0.05)
        finally:
            client.close()


def _read_thought(ws: object, expected_text: str, *, max_messages: int = 50) -> dict[str, object]:
    """Read frames until the expected thought arrives (tolerating any backfill /
    duplicate frames), or fail rather than block forever."""
    for _ in range(max_messages):
        message = ws.receive_json()  # type: ignore[attr-defined]
        if message.get("type") == "thought" and message.get("text") == expected_text:
            return message
    raise AssertionError(f"did not receive thought {expected_text!r} within {max_messages} frames")


def test_backfill_replays_a_recent_thought_on_connect(_migrated_test_db: None) -> None:
    """A fresh client immediately receives the recent-thought backfill (so the
    stream isn't blank until the next tick), on the stable wire schema."""
    app = _build_app(preseed=["I was just mulling over the pgvector recall."])
    with TestClient(app) as client, client.websocket_connect("/ws/consciousness") as ws:
        message = _read_thought(ws, "I was just mulling over the pgvector recall.")

    assert message["type"] == "thought"
    assert message["text"] == "I was just mulling over the pgvector recall."
    assert message["id"] is not None  # a persisted bus-log row id
    assert message["ts"] is not None


def test_live_thought_streams_then_reconnect_resumes(_migrated_test_db: None) -> None:
    """A thought broadcast after connect arrives live; a reconnecting client
    resumes cleanly and picks up subsequent thoughts."""
    app = _build_app()  # empty backfill — the first match is the live thought
    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws/consciousness") as ws,
            _ThoughtPublisher("I am awake now.", event_id=101),
        ):
            live = _read_thought(ws, "I am awake now.")
            assert live["type"] == "thought"

        # Reconnect: a brand-new client resumes the stream and sees NEW thoughts.
        with (
            client.websocket_connect("/ws/consciousness") as ws2,
            _ThoughtPublisher("Still thinking after the reconnect.", event_id=102),
        ):
            resumed = _read_thought(ws2, "Still thinking after the reconnect.")
            assert resumed["text"] == "Still thinking after the reconnect."


def test_unauthorised_client_is_rejected_when_a_token_is_configured(
    _migrated_test_db: None,
) -> None:
    """With a ``ws_token`` set, a client without the token is closed (1008) before
    any thought streams — Johnny's inner life isn't public. The correct token (as
    a query param) is admitted and receives the backfill."""
    app = _build_app(
        preseed=["A private musing only an authorised client may see."], ws_token="s3cret"
    )
    with TestClient(app) as client:
        # No token → handshake accepted, then closed with a policy-violation code.
        with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117
            with client.websocket_connect("/ws/consciousness") as ws:
                ws.receive_json()
        assert exc_info.value.code == 1008

        # Correct token → admitted, receives the backfilled thought.
        with client.websocket_connect("/ws/consciousness?token=s3cret") as ws_ok:
            message = _read_thought(ws_ok, "A private musing only an authorised client may see.")
        assert message["type"] == "thought"
