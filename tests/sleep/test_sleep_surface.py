"""Sleep/wake + self-model version surface on ``/ws/state`` (TC-4.11, SPEC §11.1).

Phase 4 wires the sleep state onto the existing state surface (FC-8): every snapshot
carries a ``sleep`` block — the live ``asleep`` flag plus a compact summary of the
last completed sleep (trigger, counts, **self-model version**, self-check result) —
so the dashboard can show awake↔asleep transitions and "what the last sleep did"
without input. Same headless-bus consumer model + ``WS_TOKEN`` gate as
``/ws/consciousness``.

This drives the real ``johnny.api.ws`` ``/ws/state`` handler through
``TestClient.websocket_connect`` and pins:

* the ``sleep`` block + last-sleep summary (incl. ``self_model_version``) is surfaced;
* the awake→asleep→awake transition is reflected live;
* the ``WS_TOKEN`` gate rejects an unauthorised client before any state streams.

The ``last`` summary is built with the production ``_sleep_summary`` projection so the
test pins the real wire shape, not a hand-rolled echo. Cross-loop discipline mirrors
``test_consciousness_ws.py`` (lifespan builds the engine/Redis in the portal loop; a
sync Redis client publishes from the test thread). DB+Redis backed → ``./ctl.sh test``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import redis as sync_redis
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from redis.asyncio import from_url as aio_from_url

from brain.cycle import STATE_EVENT, _sleep_summary
from brain.sleep import SleepReport
from brain.workspace import Workspace, WorkspaceEvent
from foundation.config import get_settings
from johnny.api.ws import ws_router

_CHANNEL = "johnny:test:state:bus"
_CONTENTS_KEY = "johnny:test:state:contents"
_PUBLISH_DEADLINE_S = 5.0
_T = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _report(*, version: int) -> SleepReport:
    return SleepReport(
        trigger="energy",
        started_at=_T,
        ended_at=_T,
        facts_written=5,
        episodes_decayed=3,
        facts_merged=1,
        self_model_version=version,
        self_check_ok=True,
    )


def _snapshot(*, asleep: bool, last_version: int | None) -> dict[str, Any]:
    """A state-snapshot payload with a sleep block, mirroring the cycle's emit."""
    last = _sleep_summary(_report(version=last_version)) if last_version is not None else None
    return {
        "tick": 1,
        "drives": [],
        "mood": None,
        "goals": [],
        "interval": 4.0,
        "sleep": {"asleep": asleep, "last": last},
    }


def _build_state_app(preseed: Sequence[dict[str, Any]] = (), *, ws_token: str = "") -> FastAPI:
    """A minimal app exposing the real ``/ws/state`` over a clean workspace."""
    settings = get_settings().model_copy(update={"ws_token": ws_token})

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        install_fresh_global_engine()
        await truncate_tables(("workspace_event",))
        redis = aio_from_url(settings.redis_url, decode_responses=True)
        workspace = Workspace(redis=redis, channel=_CHANNEL, contents_key=_CONTENTS_KEY)
        for payload in preseed:
            await workspace.broadcast(
                WorkspaceEvent(module="cycle", type=STATE_EVENT, payload=payload)
            )
        app.state.runtime = SimpleNamespace(workspace=workspace)
        try:
            yield
        finally:
            await redis.aclose()
            await dispose_global_engine()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.include_router(ws_router)
    return app


class _StatePublisher:
    """Publishes a state snapshot repeatedly from a background thread (closing the
    publish/subscribe race without a flaky fixed sleep)."""

    def __init__(self, payload: dict[str, Any], *, event_id: int) -> None:
        self._event = WorkspaceEvent(
            id=event_id, ts=datetime.now(UTC), module="cycle", type=STATE_EVENT, payload=payload
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _StatePublisher:
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


def _read_state(ws: object, predicate: Any, *, max_messages: int = 50) -> dict[str, Any]:
    """Read frames until a STATE_EVENT matching ``predicate`` arrives (tolerating
    backfill/duplicate frames), or fail rather than block forever."""
    for _ in range(max_messages):
        message = ws.receive_json()  # type: ignore[attr-defined]
        if message.get("type") == STATE_EVENT and predicate(message):
            return message
    raise AssertionError(f"no matching state frame within {max_messages} frames")


def test_ws_state_surfaces_the_sleep_block_and_last_sleep_summary(_migrated_test_db: None) -> None:
    """A fresh client's backfill carries the sleep block — the awake/asleep flag and
    the last-sleep summary, including the current self-model version."""
    app = _build_state_app(preseed=[_snapshot(asleep=False, last_version=4)])
    with TestClient(app) as client, client.websocket_connect("/ws/state") as ws:
        message = _read_state(ws, lambda m: m["sleep"]["last"] is not None)

    assert message["type"] == STATE_EVENT
    assert message["sleep"]["asleep"] is False
    last = message["sleep"]["last"]
    assert last["self_model_version"] == 4  # the grown self-model version is surfaced
    assert last["facts_written"] == 5
    assert last["self_check_ok"] is True
    assert last["trigger"] == "energy"


def test_ws_state_reflects_the_awake_asleep_awake_transition(_migrated_test_db: None) -> None:
    """The live stream flips asleep→True when Johnny sleeps, then back to awake with
    a fresh last-sleep summary (a bumped self-model version) on wake."""
    app = _build_state_app()  # empty backfill — first match is the live frame
    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws/state") as ws,
            _StatePublisher(_snapshot(asleep=True, last_version=None), event_id=201),
        ):
            asleep = _read_state(ws, lambda m: m["sleep"]["asleep"] is True)
            assert asleep["sleep"]["asleep"] is True
            assert asleep["sleep"]["last"] is None  # mid-sleep: no completed summary yet

        # Wake: a new client sees the awake snapshot carrying the last-sleep summary.
        with (
            client.websocket_connect("/ws/state") as ws2,
            _StatePublisher(_snapshot(asleep=False, last_version=5), event_id=202),
        ):
            awake = _read_state(
                ws2, lambda m: m["sleep"]["asleep"] is False and m["sleep"]["last"] is not None
            )
            assert awake["sleep"]["last"]["self_model_version"] == 5


def test_ws_state_rejects_an_unauthorised_client_when_a_token_is_configured(
    _migrated_test_db: None,
) -> None:
    """With a ``WS_TOKEN`` set, a client without it is closed (1008) before any state
    streams — Johnny's state isn't public. The correct token is admitted."""
    app = _build_state_app(preseed=[_snapshot(asleep=False, last_version=2)], ws_token="s3cret")
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117
            with client.websocket_connect("/ws/state") as ws:
                ws.receive_json()
        assert exc_info.value.code == 1008

        with client.websocket_connect("/ws/state?token=s3cret") as ws_ok:
            message = _read_state(ws_ok, lambda m: m["sleep"]["last"] is not None)
        assert message["sleep"]["last"]["self_model_version"] == 2
