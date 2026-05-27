"""WebSocket surfaces over the Global Workspace.

These are *consumers* of the headless Mind (FC-8): the cognitive loop runs and
broadcasts whether or not anyone is attached, and a socket simply tails the bus.
``/ws/consciousness`` streams Johnny's inner monologue — each ``thought`` event as
it is broadcast, with a stable JSON schema the Phase-5 web UI (and any other
consumer) can rely on. ``/ws/state`` (mood, drives, energy) arrives in Phase 3.

A fresh client first receives a short backfill of recent thoughts (so the stream
isn't blank until the next tick), then live events. Client disconnect breaks the
stream iterator, whose ``finally`` releases the underlying pub/sub subscription —
no leak. The socket sits behind the Traefik gate; app-level auth lands with the
web UI (Phase 5), tracked in plan/TODO.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from brain.workspace import Workspace, WorkspaceEvent
from foundation.observability import get_logger

_log = get_logger("johnny.api.ws")

ws_router = APIRouter()

THOUGHT_EVENT = "thought"
# How many recent thoughts to replay to a newly-connected client.
_BACKFILL = 10


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
    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None:
        # The Mind isn't running (shouldn't happen under the lifespan) — close.
        await websocket.close(code=1011)
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
