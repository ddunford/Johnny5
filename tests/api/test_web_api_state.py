"""TC-5a.3 — ``GET /api/v1/state`` matches a ``/ws/state`` frame's payload (no drift).

Both are built by the one ``brain.cycle.serialize_state`` (FC-8: the REST snapshot
is for initial load, then the SPA switches to the live socket — one projection so
they can't diverge). The no-drift test seeds a coherent state, persists a ``state``
frame the way the cycle would, then asserts the REST snapshot equals the frame the
WS backfill replays — field for field. The empty test pins the fresh-Johnny shape
(null mood, no goals, never slept) so the SPA's first paint can't hit a null bug.
DB+Redis backed → in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers.web_api import broadcast_state_frame, build_api_app, seed_full

TOKEN = "s3cret"
_PAYLOAD_KEYS = ("tick", "drives", "mood", "goals", "interval", "sleep")


def test_state_snapshot_equals_ws_state_frame(_migrated_test_db: None) -> None:
    """The REST snapshot is byte-equal (per payload key) to the WS frame — no drift."""

    async def seed(runtime: object) -> None:
        await seed_full(runtime)  # type: ignore[arg-type]
        # Persist a state frame built from the SAME current state the REST route reads,
        # so the /ws/state backfill replays exactly what GET /state returns.
        await broadcast_state_frame(runtime)  # type: ignore[arg-type]

    app = build_api_app(ws_token=TOKEN, seed=seed, tick=128)
    with TestClient(app) as client:
        rest = client.get("/api/v1/state", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        with client.websocket_connect(f"/ws/state?token={TOKEN}") as ws:
            frame = ws.receive_json()

    assert frame["type"] == "state"
    # The WS frame carries the same payload under the snapshot keys; compare each.
    for key in _PAYLOAD_KEYS:
        assert rest[key] == frame[key], f"REST/WS drift on {key!r}: {rest[key]!r} != {frame[key]!r}"

    # Sanity: the seeded state actually exercised the non-trivial branches.
    assert rest["mood"] is not None
    assert rest["goals"] and rest["sleep"]["last"] is not None
    assert len(rest["drives"]) > 0


def test_state_fresh_mind_shape(_migrated_test_db: None) -> None:
    """A fresh Johnny serialises cleanly: null mood, no goals, never slept — no null crash."""
    app = build_api_app(ws_token="")  # no seed → only the baseline bootstrap (drives + identity)
    with TestClient(app) as client:
        resp = client.get("/api/v1/state")
    assert resp.status_code == 200
    body = resp.json()

    assert body["tick"] == 0
    assert body["mood"] is None
    assert body["goals"] == []
    assert body["sleep"] == {"asleep": False, "full_agency": True, "last": None}
    assert isinstance(body["interval"], (int, float))
    # Drives ARE present on a fresh Mind (bootstrapped at setpoint), each fully shaped.
    assert len(body["drives"]) > 0
    for drive in body["drives"]:
        assert set(drive) == {"drive", "value", "setpoint", "threshold", "over_threshold"}
