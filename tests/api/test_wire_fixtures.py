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
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from helpers.web_api import WIRE_DIR, build_api_app, seed_full

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
