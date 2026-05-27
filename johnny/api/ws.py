"""WebSocket surfaces over the Global Workspace.

These are *consumers* of the headless Mind (FC-8): the cognitive loop runs and
broadcasts whether or not anyone is attached, and a socket simply tails the bus.
``/ws/consciousness`` streams Johnny's inner monologue — each ``thought`` event as
it is broadcast. ``/ws/state`` streams the consolidated per-tick state snapshot —
drive levels, current mood, and the active goal — so the dashboard can watch the
drives climb and a goal appear in real time (``SPEC §11.1``).

Both have a stable JSON schema the Phase-5 web UI (and any other consumer) can rely
on, and both gate on the same ``WS_TOKEN`` (his inner life + state aren't public).
A fresh client first receives a short backfill (so the stream isn't blank until the
next tick), then live events. Client disconnect breaks the stream iterator, whose
``finally`` releases the underlying pub/sub subscription — no leak.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from brain.cycle import STATE_EVENT
from brain.workspace import Workspace, WorkspaceEvent
from foundation.config import Settings
from foundation.observability import get_logger

_log = get_logger("johnny.api.ws")

ws_router = APIRouter()

THOUGHT_EVENT = "thought"
# How many recent thoughts to replay to a newly-connected client.
_BACKFILL = 10
# WS close code 1008 = policy violation (used for an unauthorised handshake).
_POLICY_VIOLATION = 1008
# Internal-error close code for "the Mind isn't attached".
_INTERNAL_ERROR = 1011


def _ws_authorised(websocket: WebSocket, settings: Settings) -> bool:
    """Interim shared-token gate (Phase-5 session-auth replaces this).

    A blank ``ws_token`` disables the gate (local dev). Otherwise the client must
    present the token as ``?token=`` or the ``X-WS-Token`` header. Constant-time
    compared; the token is never logged.
    """
    expected = settings.ws_token
    if not expected:
        return True
    provided = websocket.query_params.get("token") or websocket.headers.get("x-ws-token") or ""
    return secrets.compare_digest(provided, expected)


def _thought_message(event: WorkspaceEvent) -> dict[str, Any]:
    """The stable wire schema for a streamed thought (UI/consumer contract)."""
    return {
        "type": THOUGHT_EVENT,
        "id": event.id,
        "ts": event.ts.isoformat() if event.ts else None,
        "text": event.payload.get("text", ""),
    }


@ws_router.websocket("/ws/consciousness")
async def consciousness(websocket: WebSocket) -> None:
    """Stream Johnny's stream of consciousness (recent backfill, then live)."""
    await websocket.accept()
    if not _ws_authorised(websocket, websocket.app.state.settings):
        # Reject before streaming a single thought — his inner life isn't public.
        _log.warning("ws.consciousness.unauthorised")
        await websocket.close(code=_POLICY_VIOLATION)
        return
    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None:
        # The Mind isn't running (shouldn't happen under the lifespan) — close.
        await websocket.close(code=_INTERNAL_ERROR)
        return

    workspace: Workspace = runtime.workspace
    try:
        for event in reversed(await workspace.recent_events(_BACKFILL, type_filter=THOUGHT_EVENT)):
            await websocket.send_json(_thought_message(event))

        async for event in workspace.stream(types=[THOUGHT_EVENT]):
            await websocket.send_json(_thought_message(event))
    except WebSocketDisconnect:
        _log.debug("ws.consciousness.disconnect")
    except Exception:
        _log.warning("ws.consciousness.error")
        raise


def _state_message(event: WorkspaceEvent) -> dict[str, Any]:
    """The stable wire schema for a streamed state snapshot (UI/consumer contract)."""
    payload = event.payload
    return {
        "type": STATE_EVENT,
        "id": event.id,
        "ts": event.ts.isoformat() if event.ts else None,
        "tick": payload.get("tick"),
        "drives": payload.get("drives", []),
        "mood": payload.get("mood"),
        "goals": payload.get("goals", []),
        "interval": payload.get("interval"),
        # Sleep status (awake/asleep + last-sleep summary). Absent on pre-Phase-4
        # snapshots, so default to "awake, never slept" for a stable schema.
        "sleep": payload.get("sleep", {"asleep": False, "last": None}),
    }


@ws_router.websocket("/ws/state")
async def state(websocket: WebSocket) -> None:
    """Stream the live state snapshot — drives, mood, goals (latest, then live).

    The dashboard's "he's alive" view: drive bars climbing, mood shifting, a goal
    appearing — all with no input. Same ``WS_TOKEN`` gate as ``/ws/consciousness``.
    """
    await websocket.accept()
    if not _ws_authorised(websocket, websocket.app.state.settings):
        _log.warning("ws.state.unauthorised")
        await websocket.close(code=_POLICY_VIOLATION)
        return
    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None:
        await websocket.close(code=_INTERNAL_ERROR)
        return

    workspace: Workspace = runtime.workspace
    try:
        # Backfill the most recent snapshot so a fresh client renders immediately.
        for event in reversed(await workspace.recent_events(1, type_filter=STATE_EVENT)):
            await websocket.send_json(_state_message(event))

        async for event in workspace.stream(types=[STATE_EVENT]):
            await websocket.send_json(_state_message(event))
    except WebSocketDisconnect:
        _log.debug("ws.state.disconnect")
    except Exception:
        _log.warning("ws.state.error")
        raise
