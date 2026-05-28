"""TC-5a.8 / TASK-5a.9 — capture each endpoint's REAL JSON response to committed fixtures.

These are the literal wire shapes Phase-5b's frontend service adapters are
contract-pinned against (the canonical frontend↔backend failure mode, ``/plan-review``
Step 7c): a hand-written TS interface is a *claim*; these captures are the *proof*.
They MUST be real responses, never hand-authored — so this test drives the real
routes and writes their output verbatim to ``tests/fixtures/wire/``.

Two variants per endpoint:

* **populated** (``<name>.json``) — a coherent lived-in Johnny (``seed_full``);
* **empty / fresh-Johnny** (``<name>.empty.json``) — no episodes, never slept, null
  mood, seed identity v1, no goals/notes. This is the SPA's first-paint state and
  the one that catches ``undefined``/null projection bugs before they ship.

The seed is a FIXED timeline, so re-running reproduces byte-identical fixtures (no
churn). Search (``?q=``) responses are intentionally NOT captured: their shape is the
same ``EpisodeOut``/``FactOut`` as browse (only ``score`` flips from null to a float —
asserted in ``test_web_api_reads``), and the recall ``score`` carries a wall-clock
recency term that would churn a committed fixture. DB+Redis backed → ``./ctl.sh test``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from helpers.web_api import WIRE_DIR, broadcast_state_frame, build_api_app, seed_full

from core.audit import AuditWriter

# A secret-shaped canary (Groq-key value pattern) for the action-trail capture: the
# Core writer redacts it to [REDACTED] on write, so the captured fixture proves
# redaction-on-the-wire and the frontend adapter + E2E bind to identical redacted bytes.
# Assembled from parts so the .githooks credential guard doesn't flag this *fake*
# canary as a real key — the runtime value is the full gsk_ shape the redactor matches.
_AUDIT_CANARY = "gsk_" + "CANARYtokenmustneverrender0123456789"

# Each guarded GET endpoint, by fixture name → path. Captured in both variants.
_GET_ENDPOINTS: dict[str, str] = {
    "state": "/api/v1/state",
    "thoughts": "/api/v1/thoughts",
    "audit": "/api/v1/audit",
    "memory_episodes": "/api/v1/memory/episodes",
    "memory_facts": "/api/v1/memory/facts",
    "goals": "/api/v1/goals",
    "sleeps": "/api/v1/sleeps",
    "self": "/api/v1/self",
}


def _write_fixture(name: str, data: Any) -> Path:
    """Write a captured response to ``tests/fixtures/wire/<name>.json`` (committed)."""
    WIRE_DIR.mkdir(parents=True, exist_ok=True)
    path = WIRE_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def test_capture_populated_wire_fixtures(_migrated_test_db: None) -> None:
    """Capture the populated response of every endpoint (incl. the POST /input ack)."""
    app = build_api_app(ws_token="", seed=seed_full, tick=128)
    with TestClient(app) as client:
        responses = {name: client.get(path) for name, path in _GET_ENDPOINTS.items()}
        # The one write: capture the InputAccepted ack shape too (queue starts empty → depth 1).
        responses["input"] = client.post("/api/v1/input", json={"text": "Hello from the web UI"})

    for name, resp in responses.items():
        assert resp.status_code in (200, 202), f"{name}: {resp.status_code} {resp.text}"
        data = resp.json()
        path = _write_fixture(name, data)
        # Round-trip: the committed file is valid JSON equal to the live response.
        assert json.loads(path.read_text()) == data

    # Spot-check the captures are genuinely populated (not silently empty).
    assert json.loads((WIRE_DIR / "self.json").read_text())["identity"]["version"] == 2
    assert json.loads((WIRE_DIR / "thoughts.json").read_text())["thoughts"]
    assert json.loads((WIRE_DIR / "input.json").read_text()) == {"accepted": True, "queue_depth": 1}


def test_capture_empty_state_wire_fixtures(_migrated_test_db: None) -> None:
    """Capture the fresh-Johnny response of every endpoint — the first-paint contract."""
    app = build_api_app(ws_token="")  # no seed → baseline bootstrap only (drives + identity v1)
    with TestClient(app) as client:
        responses = {name: client.get(path) for name, path in _GET_ENDPOINTS.items()}

    for name, resp in responses.items():
        assert resp.status_code == 200, f"{name}: {resp.status_code} {resp.text}"
        data = resp.json()
        path = _write_fixture(f"{name}.empty", data)
        assert json.loads(path.read_text()) == data

    # The first-paint null traps the empty-state capture exists to surface:
    state = json.loads((WIRE_DIR / "state.empty.json").read_text())
    assert state["mood"] is None
    assert state["goals"] == []
    assert state["sleep"] == {"asleep": False, "full_agency": True, "last": None}
    assert len(state["drives"]) > 0  # drives ARE present on a fresh Mind (bootstrapped)

    assert json.loads((WIRE_DIR / "thoughts.empty.json").read_text()) == {"thoughts": []}
    assert json.loads((WIRE_DIR / "audit.empty.json").read_text()) == {"events": []}
    assert json.loads((WIRE_DIR / "memory_episodes.empty.json").read_text()) == {"episodes": []}
    assert json.loads((WIRE_DIR / "memory_facts.empty.json").read_text()) == {"facts": []}
    assert json.loads((WIRE_DIR / "goals.empty.json").read_text()) == {"active": [], "recent": []}
    assert json.loads((WIRE_DIR / "sleeps.empty.json").read_text()) == {"sleeps": []}

    # Fresh self = the anchor-grounded v1 seed (NOT null) + no notes yet.
    self_empty = json.loads((WIRE_DIR / "self.empty.json").read_text())
    assert self_empty["identity"]["version"] == 1
    assert self_empty["identity"]["name"] == "Johnny"
    assert self_empty["notes"] == []


# ── GET /api/v1/audit/actions — the durable Core action trail (Phase 6b) ──────────
#
# seed_full predates the tool belt and writes no action_log rows, so the populated
# audit_actions fixture needs its own seed: two real actions through the production
# Core AuditWriter — an allowed `note` whose body carries a secret-shaped canary
# (redacted on write) + a vetoed `web_fetch`. The frontend auditActionsApi adapter +
# QA's AuditPanel E2E pin against THESE bytes (incl. the empty {actions:[]} first-paint).


async def _seed_action_log(_runtime: SimpleNamespace) -> None:
    """Two durable actions via the production Core writer (allow note + veto fetch)."""
    writer = AuditWriter()
    await writer.record(
        tool="note",
        args={
            "title": "diagnostic note",
            "body": f"checking redaction: {_AUDIT_CANARY}",
            "tags": [],
        },
        result={
            "success": True,
            "output": {"note_id": 1},
            "summary": "wrote note 'diagnostic note'",
        },
        conscience_verdict="allow",
        veto_reason=None,
        goal_id=1,
        success=True,
    )
    await writer.record(
        tool="web_fetch",
        args={"url": "http://example.com/article"},
        result=None,
        conscience_verdict="veto",
        veto_reason="that doesn't sit right with what I value",
        goal_id=2,
        success=False,
    )


def test_capture_action_audit_wire_fixtures(_migrated_test_db: None) -> None:
    """Capture the durable action-trail endpoint — populated (redaction proven) + empty."""
    app = build_api_app(ws_token="", seed=_seed_action_log)
    with TestClient(app) as client:
        populated = client.get("/api/v1/audit/actions")
    empty_app = build_api_app(ws_token="")  # no seed → fresh trail
    with TestClient(empty_app) as client:
        empty = client.get("/api/v1/audit/actions")

    assert populated.status_code == 200 and empty.status_code == 200
    pop_data = populated.json()
    _write_fixture("audit_actions", pop_data)
    _write_fixture("audit_actions.empty", empty.json())

    # Real durable shape: two rows newest-first; the canary is REDACTED on the wire
    # (never the raw secret); the empty first-paint is {actions: []}.
    assert _AUDIT_CANARY not in json.dumps(pop_data)
    assert "[REDACTED]" in json.dumps(pop_data)
    assert len(pop_data["actions"]) == 2
    assert json.loads((WIRE_DIR / "audit_actions.empty.json").read_text()) == {"actions": []}


# ── /ws frame captures (the socket adapters pin against THESE, not the REST shapes) ──
#
# 5a captured the REST envelopes only. The two live streams wrap their payload in a
# ``{type, id, ts, …}`` frame (``johnny/api/ws.py`` ``_thought_message`` /
# ``_state_message``) — so ``thoughts.json`` / ``state.json`` are NOT the literal
# frames ``consciousnessSocket`` / ``stateSocket`` parse. Without these, the Phase-5b
# socket adapters would be contract-pinned against a hand-authored WS envelope (a
# *claim*), defeating Step 7c. Two shapes, mirroring each handler's on-connect replay:
#   * ``ws_consciousness`` — the backfill *burst*: every recent ``thought`` frame the
#     socket replays, oldest-first (a JSON array; ``[]`` on a fresh Mind).
#   * ``ws_state`` — the single most-recent state *snapshot* frame the backfill replays.
# Envelope ``ts`` is frozen (``broadcast_state_frame(ts=…)`` / seed_full's fixed thought
# timestamps) so re-capture is byte-identical, like every REST fixture.

# Continues seed_full's fixed timeline (last thought at +8m) so the state frame's
# envelope ts is deterministic; the empty frame uses the timeline origin.
_WS_STATE_TS = datetime(2026, 5, 20, 9, 9, 0, tzinfo=UTC)
_WS_STATE_EMPTY_TS = datetime(2026, 5, 20, 9, 0, 0, tzinfo=UTC)
_STATE_PAYLOAD_KEYS = ("tick", "drives", "mood", "goals", "interval", "sleep")


def test_capture_populated_ws_fixtures(_migrated_test_db: None) -> None:
    """Capture the REAL populated ``/ws/consciousness`` + ``/ws/state`` frames."""

    async def seed(runtime: SimpleNamespace) -> None:
        await seed_full(runtime)
        # A fresh-connect /ws/state replays the most-recent persisted state frame; emit
        # one (built from the same current state GET /state reads) with a frozen ts.
        await broadcast_state_frame(runtime, ts=_WS_STATE_TS)

    app = build_api_app(ws_token="", seed=seed, tick=128)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/consciousness") as ws:
            # seed_full broadcasts exactly two thought events → two backfill frames,
            # replayed oldest-first (the handler reverses recent_events).
            consciousness_frames = [ws.receive_json(), ws.receive_json()]
        with client.websocket_connect("/ws/state") as ws:
            state_frame = ws.receive_json()

    _write_fixture("ws_consciousness", consciousness_frames)
    _write_fixture("ws_state", state_frame)

    # The {type,id,ts,text} wrapper the REST thoughts shape lacks — really present.
    assert [f["type"] for f in consciousness_frames] == ["thought", "thought"]
    assert all(set(f) == {"type", "id", "ts", "text"} for f in consciousness_frames)
    assert consciousness_frames[0]["text"] == "I wonder what Dan is working on right now."
    assert consciousness_frames[1]["text"].startswith("Reflecting on my recall")
    assert [f["id"] for f in consciousness_frames] == [1, 4]  # oldest-first, stable ids

    # The state frame = the {type,id,ts} wrapper + the same payload GET /state builds.
    assert state_frame["type"] == "state"
    assert set(state_frame) == {"type", "id", "ts", *_STATE_PAYLOAD_KEYS}
    assert state_frame["mood"] is not None and state_frame["goals"]
    assert state_frame["sleep"]["last"] is not None
    rest_state = json.loads((WIRE_DIR / "state.json").read_text())
    for key in _STATE_PAYLOAD_KEYS:
        assert state_frame[key] == rest_state[key], f"WS/REST drift on {key!r}"


def test_capture_empty_state_ws_fixtures(_migrated_test_db: None) -> None:
    """Capture the fresh-Johnny ``/ws`` frames — the socket adapters' first-paint traps."""

    async def seed(runtime: SimpleNamespace) -> None:
        # A fresh Mind hasn't ticked, so persist the single state frame its backfill
        # would replay — the canonical first-paint frame (null mood, no goals, never slept).
        await broadcast_state_frame(runtime, ts=_WS_STATE_EMPTY_TS)

    app = build_api_app(ws_token="", seed=seed)  # tick defaults to 0
    with TestClient(app) as client:
        with client.websocket_connect("/ws/consciousness") as ws:
            # A fresh Mind has zero thoughts → the backfill sends NOTHING (a later
            # receive would block on the live stream). The empty burst IS the capture.
            assert ws  # the socket accepts the (token-open) handshake
            consciousness_frames: list[dict[str, Any]] = []
        with client.websocket_connect("/ws/state") as ws:
            state_frame = ws.receive_json()

    _write_fixture("ws_consciousness.empty", consciousness_frames)
    _write_fixture("ws_state.empty", state_frame)

    # Fresh client → empty stream-of-consciousness backfill.
    assert consciousness_frames == []
    # The fresh-Johnny socket frame: the null traps a hand-authored envelope would miss.
    assert state_frame["type"] == "state"
    assert state_frame["tick"] == 0
    assert state_frame["mood"] is None
    assert state_frame["goals"] == []
    assert state_frame["sleep"] == {"asleep": False, "full_agency": True, "last": None}
    assert len(state_frame["drives"]) > 0
    rest_empty = json.loads((WIRE_DIR / "state.empty.json").read_text())
    for key in _STATE_PAYLOAD_KEYS:
        assert state_frame[key] == rest_empty[key], f"WS/REST drift on {key!r}"
