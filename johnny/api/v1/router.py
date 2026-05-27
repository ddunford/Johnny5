"""Aggregate router for the versioned API surface (``/api/v1``).

Cognitive subsystems mount their routes here as later phases land (memory
browser, self panel, config). Phase 0 ships the substrate only, so the router is
intentionally empty — the seam exists, the routes don't yet.
"""

from __future__ import annotations

from fastapi import APIRouter

v1_router = APIRouter(prefix="/api/v1")
