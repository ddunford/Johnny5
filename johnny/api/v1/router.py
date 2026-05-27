"""Aggregate router for the versioned API surface (``/api/v1``).

Every route here sits behind the shared-token gate (``require_token``) — the same
``WS_TOKEN`` that protects the WebSocket streams (single-token model, no user
system). Cognitive subsystems mount their sub-routers here; each is a thin, typed
projection of an existing repository / the live runtime (no new domain logic, no
new tables — Phase 5a is the read/input doorway, FC-8).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from johnny.api.v1.auth import require_token

# The gate is applied at the router level, so it guards every mounted route with
# no per-route opt-out. Sub-routers (input, state, thoughts, audit, memory, goals,
# sleeps, self) are mounted in TASK-5a.7 once each route module + its runtime
# wiring lands.
v1_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
